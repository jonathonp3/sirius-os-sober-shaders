"""
core/shader.py

Parse ReShade FX (.fx) files to discover their uniforms (for auto-generating
a parameter UI) and their resource dependencies (#include files and texture
references, for checking that a shader can actually load).

This is not an HLSL parser. It matches the specific syntactic patterns that
ReShade FX shaders use for uniforms and resource declarations:

    uniform float name < annotations > = default;
    #include "file.fxh"
    texture2D texName < source = "path.png"; > { ... };

If a shader uses a format these patterns don't cover, the parser returns
what it found and `check_dependencies` reports what's missing. It never
crashes on an unrecognized shader — that would make the app fragile against
shader repo changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Uniform:
    name: str
    ui_type: str | None = None       # slider, combo, radio, color, button, input, drag
    ui_label: str | None = None
    ui_tooltip: str | None = None
    default: str | None = None
    ui_min: float | None = None
    ui_max: float | None = None
    ui_step: float | None = None
    ui_items: list[str] | None = None


@dataclass
class ShaderInfo:
    path: Path
    name: str
    uniforms: list[Uniform] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)
    textures: list[str] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)


# --- regexes --------------------------------------------------------------

# #include "file.fxh" or #include <file.fxh>
_INCLUDE_RE = re.compile(r'^\s*#include\s+[<"]([^>"]+)[>"]', re.MULTILINE)

# texture2D texName < source = "path.png"; > or texture texName < source = ... >
_TEXTURE_SOURCE_RE = re.compile(
    r'\btexture(?:2D|3D|1D)?\s+\w+\s*<\s*source\s*=\s*"([^"]+)"',
    re.MULTILINE,
)

# A uniform declaration spans from `uniform` to the terminating `;`
# (which comes after the optional `= default`). This is intentionally
# greedy across the annotation block but stops at the first `;` after
# the default value.
_UNIFORM_START_RE = re.compile(r'^\s*uniform\s+([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*',
                               re.MULTILINE)

# Within an annotation block, `key = "value";` or `key = 1.0;`
_ANNOTATION_RE = re.compile(
    r'([A-Za-z_]\w*)\s*=\s*(?:"([^"]*)"|([^;]+))\s*;',
    re.MULTILINE,
)


# --- parsing --------------------------------------------------------------

def _find_annotation_block(text: str, start: int) -> tuple[str, int] | None:
    """
    Given the position right after a uniform's name, find the `< ... >`
    annotation block if present. Return (block_text, end_index) or None.
    """
    i = start
    n = len(text)
    while i < n and text[i].isspace():
        i += 1
    if i >= n or text[i] != "<":
        return None
    depth = 1
    j = i + 1
    while j < n and depth > 0:
        if text[j] == "<":
            depth += 1
        elif text[j] == ">":
            depth -= 1
        j += 1
    if depth != 0:
        return None
    return text[i + 1 : j - 1], j


def _parse_annotations(block: str) -> dict[str, str]:
    """Parse `key = "value";` or `key = value;` pairs from an annotation block."""
    result: dict[str, str] = {}
    for m in _ANNOTATION_RE.finditer(block):
        key = m.group(1)
        quoted = m.group(2)
        bare = m.group(3)
        result[key] = quoted if quoted is not None else (bare or "").strip()
    return result


def _parse_items(raw: str) -> list[str]:
    """Split a ui_items value on NUL (`\\0` in the source), preserving empty items."""
    # The source has literal backslash-zero sequences, which the annotation
    # regex captures as-is. Split on the two-char sequence.
    parts = raw.split("\\0")
    # Trailing empty item is an artifact of the trailing \0
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def _parse_uniform(text: str, m: re.Match) -> Uniform:
    """Build a Uniform from a _UNIFORM_START_RE match and its surroundings."""
    _kind, name = m.group(1), m.group(2)
    uniform = Uniform(name=name)

    tail_start = m.end()
    block = _find_annotation_block(text, tail_start)
    if block is not None:
        block_text, after_block = block
        ann = _parse_annotations(block_text)
        uniform.ui_type = ann.get("ui_type")
        uniform.ui_label = ann.get("ui_label")
        uniform.ui_tooltip = ann.get("ui_tooltip")
        for key in ("ui_min", "ui_max", "ui_step"):
            if key in ann:
                try:
                    setattr(uniform, key, float(ann[key]))
                except ValueError:
                    pass
        if "ui_items" in ann:
            uniform.ui_items = _parse_items(ann["ui_items"])
        tail_start = after_block

    # Default value: `= value;` after the annotation block.
    default_match = re.match(r'\s*=\s*([^;]+);', text[tail_start:])
    if default_match:
        uniform.default = default_match.group(1).strip()

    return uniform


def parse_fx(path: Path) -> ShaderInfo:
    """Parse a .fx file into a ShaderInfo. Never raises on unrecognized syntax."""
    text = path.read_text(errors="replace")
    info = ShaderInfo(path=path, name=path.stem)

    # Strip line and block comments before parsing. Comments can contain
    # `uniform`, `#include`, etc. and would otherwise produce false positives.
    # This is a simple strip, not a full lexer — it handles the common cases.
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)

    info.includes = _INCLUDE_RE.findall(text)
    info.textures = _TEXTURE_SOURCE_RE.findall(text)

    for m in _UNIFORM_START_RE.finditer(text):
        try:
            info.uniforms.append(_parse_uniform(text, m))
        except Exception as e:  # never let a bad shader crash discovery
            info.parse_warnings.append(
                f"{path.name}: failed to parse uniform {m.group(2)!r}: {e}"
            )

    return info


def discover(shader_dir: Path) -> list[ShaderInfo]:
    """
    Parse every .fx file directly under shader_dir (non-recursive).
    Returns an empty list if shader_dir doesn't exist.
    """
    if not shader_dir.exists() or not shader_dir.is_dir():
        return []
    return [
        parse_fx(p) for p in sorted(shader_dir.glob("*.fx"))
    ]


def check_dependencies(
    shader: ShaderInfo,
    include_dir: Path,
    texture_dir: Path | None = None,
) -> list[str]:
    """
    Return a list of human-readable strings describing missing dependencies.
    Empty list means the shader's includes and textures are all present.

    `include_dir` is where #include files are expected to live (usually the
    same directory as the .fx file, but ReShade convention is a flat
    shader directory, so include_dir is usually shader.path.parent).
    """
    missing: list[str] = []
    for inc in shader.includes:
        if not (include_dir / inc).exists():
            missing.append(f"missing include: {inc}")
    if texture_dir is not None:
        for tex in shader.textures:
            if not (texture_dir / tex).exists():
                missing.append(f"missing texture: {tex}")
    return missing


def normalize_reference(raw: str) -> str:
    """Normalize an #include or texture source path to a POSIX relative path.

    Handles Windows separators and ReShade's common forms:
        .\\Includes\\foo.fxh -> Includes/foo.fxh
        ./Includes/foo.fxh   -> Includes/foo.fxh
        Includes\\foo.fxh    -> Includes/foo.fxh
        foo.fxh              -> foo.fxh
    """
    p = (raw or "").strip().strip('"').strip("'")
    p = p.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if p == ".":
        p = ""
    p = p.lstrip("/")
    return p


def read_includes(path: Path) -> list[str]:
    """Return the normalized #include references from a shader or header file.

    Unlike parse_fx, this extracts only includes (no uniform parsing), so it
    is cheap to call on .fxh headers during transitive resolution.
    """
    text = path.read_text(errors="replace")
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return [normalize_reference(m) for m in _INCLUDE_RE.findall(text)]


def read_textures(path: Path) -> list[str]:
    """Return the normalized texture sources declared in a shader or header.

    A header can declare `texture X < source = "file.png" >`, so texture
    dependencies have to be collected transitively alongside includes.
    """
    text = path.read_text(errors="replace")
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return [normalize_reference(m) for m in _TEXTURE_SOURCE_RE.findall(text)]


def resolve_dependencies(
    shader: ShaderInfo,
    shader_dir: Path,
    texture_dir: Path | None = None,
) -> tuple[list[str], set[str], set[str]]:
    """Compiler-accurate dependency resolution for a shader.

    Resolves includes the way vkBasalt does: an #include is looked up as
    `shader_dir / <path>` (flat, no recursive search), and a texture as
    `texture_dir / <path>`. Includes are followed transitively.

    This is deliberately stricter than the old basename/subdir fallback,
    which reported a shader as resolved when its include existed anywhere
    under shader_dir — even at a subpath vkBasalt would not search — so the
    shader then failed to compile at runtime.

    Returns (missing, resolved_includes, resolved_textures).
    """
    missing: list[str] = []
    resolved_includes: set[str] = set()
    resolved_textures: set[str] = set()

    # Textures can be declared in the shader or in any included header, so
    # collect them while walking the include graph.
    texture_refs: list[str] = [normalize_reference(t) for t in shader.textures]

    seen: set[str] = set()
    queue: list[str] = [normalize_reference(inc) for inc in shader.includes]
    while queue:
        norm = queue.pop(0)
        if not norm or norm.lower() in seen:
            continue
        seen.add(norm.lower())
        candidate = shader_dir / norm
        if candidate.is_file():
            resolved_includes.add(norm)
            for inc in read_includes(candidate):
                if inc and inc.lower() not in seen:
                    queue.append(inc)
            texture_refs.extend(read_textures(candidate))
        else:
            missing.append(f"missing include: {norm}")

    if texture_dir is not None:
        seen_textures: set[str] = set()
        for norm in texture_refs:
            if not norm or norm.lower() in seen_textures:
                continue
            seen_textures.add(norm.lower())
            if (texture_dir / norm).is_file():
                resolved_textures.add(norm)
            else:
                missing.append(f"missing texture: {norm}")

    return missing, resolved_includes, resolved_textures
    
