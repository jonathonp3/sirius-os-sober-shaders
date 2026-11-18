"""core/importing.py

Pure import/validation logic for bringing shaders from an arbitrary source
directory (or repo checkout) into vkBasalt's flat sandbox layout.

No subprocess calls. Discovery and validation only read; the copy helpers
write exclusively into the shader_dir/texture_dir they are given. Everything
is parameterized, so the host import script, the GUI, and the test suite all
run the same code.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from core import shader as shader_mod


@dataclass
class ShaderDecision:
    name: str
    status: str = "rejected"                 # "imported" | "rejected"
    reason: str = ""                         # human-readable, when rejected
    missing: list[str] = field(default_factory=list)
    resolved_includes: list[str] = field(default_factory=list)
    resolved_textures: list[str] = field(default_factory=list)


@dataclass
class ImportResult:
    """Everything one import pass discovered, decided, and copied."""

    shaders: list[Path] = field(default_factory=list)
    decisions: list[ShaderDecision] = field(default_factory=list)
    inc_collisions: list[str] = field(default_factory=list)
    tex_collisions: list[str] = field(default_factory=list)
    copied: int = 0


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".dds", ".bmp", ".tga", ".hdr", ".exr"}


def _same_contents(a: Path, b: Path) -> bool:
    """Compare files as they will be imported (shader text is rewritten)."""
    try:
        if a.suffix.lower() in (".fx", ".fxh") and b.suffix.lower() in (".fx", ".fxh"):
            return (
                rewrite_references(a.read_text(encoding="utf-8", errors="replace"))
                == rewrite_references(b.read_text(encoding="utf-8", errors="replace"))
            )
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def _common_parent(paths: list[Path]) -> Path | None:
    if not paths:
        return None
    common: list[str] = []
    for parts in zip(*(p.parent.parts for p in paths)):
        if all(part == parts[0] for part in parts):
            common.append(parts[0])
        else:
            break
    return Path(*common) if common else None


def discover_assets(
    root: Path,
) -> tuple[list[Path], list[tuple[Path, str]], list[tuple[Path, str]]]:
    """
    Locate shaders, headers, and textures anywhere under `root`.

    Returns (shaders, includes, textures):
      shaders  — list of .fx source paths
      includes — (source path, posix path relative to the shader dir)
      textures — (source path, posix path relative to the texture dir)

    A directory literally named Shaders/ (or Textures/) is the anchor for
    that asset type if present; otherwise the whole tree is scanned.
    """
    shader_dirs = sorted(
        (d for d in root.rglob("*") if d.is_dir() and d.name.lower() == "shaders"),
        key=lambda d: len(d.relative_to(root).parts),
    )
    texture_dirs = sorted(
        (d for d in root.rglob("*") if d.is_dir() and d.name.lower() in ("textures", "texture")),
        key=lambda d: len(d.relative_to(root).parts),
    )

    shaders: list[Path] = []
    includes: list[tuple[Path, str]] = []
    textures: list[tuple[Path, str]] = []

    if shader_dirs:
        sdir = shader_dirs[0]
        shaders = sorted(p for p in sdir.rglob("*.fx") if p.is_file())
        includes = sorted(
            ((p, p.relative_to(sdir).as_posix()) for p in sdir.rglob("*.fxh") if p.is_file()),
            key=lambda t: t[1].lower(),
        )
    else:
        shaders = sorted(p for p in root.rglob("*.fx") if p.is_file())
        anchor = _common_parent(shaders)
        includes = sorted(
            ((p, (p.relative_to(anchor).as_posix() if anchor else p.name))
             for p in root.rglob("*.fxh") if p.is_file()),
            key=lambda t: t[1].lower(),
        )

    if texture_dirs:
        tdir = texture_dirs[0]
        textures = sorted(
            ((p, p.relative_to(tdir).as_posix()) for p in tdir.rglob("*") if p.is_file()),
            key=lambda t: t[1].lower(),
        )
    else:
        textures = sorted(
            ((p, p.relative_to(root).as_posix()) for p in root.rglob("*")
             if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES),
            key=lambda t: t[1].lower(),
        )

    return shaders, includes, textures


def build_available(
    includes: list[tuple[Path, str]],
    textures: list[tuple[Path, str]],
    shader_dir: Path,
    texture_dir: Path,
) -> tuple[dict[str, Path], dict[str, Path], list[str], list[str]]:
    """
    Compute the flattened namespaces vkBasalt will see after import.

    Seeded from files already in `shader_dir`/`texture_dir` (the sandbox),
    then the source repo's files (source wins). Returns
    (include_src, texture_src, inc_collisions, tex_collisions); each src map
    is keyed by a lowercased normalized path, covering both the flat basename
    and any preserved subpath.
    """
    include_src: dict[str, Path] = {}
    texture_src: dict[str, Path] = {}
    inc_collisions: list[str] = []
    tex_collisions: list[str] = []

    if shader_dir.is_dir():
        for p in shader_dir.rglob("*"):
            if p.is_file():
                include_src[p.name.lower()] = p
                include_src[p.relative_to(shader_dir).as_posix().lower()] = p
    if texture_dir.is_dir():
        for p in texture_dir.rglob("*"):
            if p.is_file():
                texture_src[p.name.lower()] = p
                texture_src[p.relative_to(texture_dir).as_posix().lower()] = p

    def add(mapping: dict[str, Path], collisions: list[str], src: Path, rel: str) -> None:
        keys = {rel}
        base = Path(rel).name
        if base != rel:
            keys.add(base)
        for k in keys:
            norm = shader_mod.normalize_reference(k).lower()
            existing = mapping.get(norm)
            if existing is not None and existing != src:
                # Same-name, byte-identical files (e.g. re-importing a header
                # already in the sandbox) are not a collision.
                if not _same_contents(existing, src) and norm not in collisions:
                    collisions.append(norm)
            # The source repo wins over whatever the sandbox already had.
            mapping[norm] = src

    for src, rel in includes:
        add(include_src, inc_collisions, src, rel)
    for src, rel in textures:
        add(texture_src, tex_collisions, src, rel)

    return include_src, texture_src, inc_collisions, tex_collisions


def validate_shader(
    fx: Path,
    include_src: dict[str, Path],
    texture_src: dict[str, Path],
) -> ShaderDecision:
    """Decide whether `fx` can load, resolving deps against the flat namespace."""
    decision = ShaderDecision(name=fx.stem)
    try:
        info = shader_mod.parse_fx(fx)
    except Exception as e:  # noqa: BLE001 — parser must not crash the import
        decision.reason = f"parse error: {e}"
        return decision

    found_includes: list[str] = []
    found_textures: list[str] = []
    resolved: set[str] = set()
    queue = [shader_mod.normalize_reference(inc) for inc in info.includes]

    # Textures may be declared in the shader or in an included header.
    texture_refs = [shader_mod.normalize_reference(t) for t in info.textures]

    while queue:
        norm = queue.pop(0)
        if not norm or norm.lower() in resolved:
            continue
        resolved.add(norm.lower())
        src = include_src.get(norm.lower())
        if src is None:
            decision.missing.append(f"missing include: {norm}")
            continue
        found_includes.append(norm)
        for inc in shader_mod.read_includes(src):
            if inc and inc.lower() not in resolved:
                queue.append(inc)
        texture_refs.extend(shader_mod.read_textures(src))

    seen_textures: set[str] = set()
    for norm in texture_refs:
        if not norm or norm.lower() in seen_textures:
            continue
        seen_textures.add(norm.lower())
        if norm.lower() in texture_src:
            found_textures.append(norm)
        else:
            decision.missing.append(f"missing texture: {norm}")

    if decision.missing:
        decision.reason = "; ".join(decision.missing)
        return decision

    decision.status = "imported"
    decision.resolved_includes = found_includes
    decision.resolved_textures = found_textures
    return decision


def decide(
    root: Path,
    shader_dir: Path,
    texture_dir: Path,
) -> list[ShaderDecision]:
    """Discover, build the namespace, and validate every .fx under `root`."""
    shaders, includes, textures = discover_assets(root)
    include_src, texture_src, _i, _t = build_available(
        includes, textures, shader_dir, texture_dir,
    )
    return [validate_shader(fx, include_src, texture_src) for fx in shaders]


# --- path rewriting -------------------------------------------------------

_INCLUDE_DIRECTIVE_RE = re.compile(
    r'^([ \t]*#[ \t]*include[ \t]+)([<"])([^>"]*)([>"])',
    re.MULTILINE,
)
_SOURCE_ATTRIBUTE_RE = re.compile(r'(\bsource[ \t]*=[ \t]*")([^"]*)(")')


def rewrite_references(text: str) -> str:
    """Return shader source with dependency paths normalized for vkBasalt.

    vkBasalt's preprocessor resolves an include by replacing the including
    file's name with the raw include string (via ``std::filesystem``); on
    Linux a backslash is an ordinary character, so
    ``#include ".\\Includes\\x.fxh"`` looks for a literal file name and fails.
    Rewriting includes and texture ``source`` paths to POSIX relative paths
    makes the imported copies resolvable via the flat + preserved layout.
    """

    def _norm(m: re.Match) -> str:
        return (
            f"{m.group(1)}{m.group(2)}"
            f"{shader_mod.normalize_reference(m.group(3))}{m.group(4)}"
        )

    text = _INCLUDE_DIRECTIVE_RE.sub(_norm, text)
    return _SOURCE_ATTRIBUTE_RE.sub(
        lambda m: f"{m.group(1)}{shader_mod.normalize_reference(m.group(2))}{m.group(3)}",
        text,
    )


# --- copying --------------------------------------------------------------

def _copy(
    src: Path,
    dst: Path,
    force: bool,
    dry: bool,
    normalize_text: bool = False,
) -> int:
    """Copy one file, rewriting shader dependency paths to POSIX form.

    vkBasalt opens an include by substituting the raw include string into the
    including file's path, so a verbatim ``#include ".\\Includes\\x.fxh"`` fails
    on Linux. Text copies are compared by content (so re-imports stay
    idempotent) instead of by mtime.
    """
    text: str | None = None
    if normalize_text and src.suffix.lower() in (".fx", ".fxh"):
        text = rewrite_references(
            src.read_text(encoding="utf-8", errors="replace")
        )

    # A dependency can resolve to a file already in the sandbox (e.g. a
    # framework header from a prior Fetch), in which case src == dst. Never
    # copy a file onto itself; rewrite text in place if it still needs it.
    try:
        same_file = src.resolve() == dst.resolve()
    except OSError:
        same_file = False
    if same_file:
        if text is None or text == src.read_text(encoding="utf-8", errors="replace"):
            return 0
        if dry:
            return 1
        src.write_text(text, encoding="utf-8")
        return 1

    if not force and dst.exists():
        if text is None:
            if dst.stat().st_mtime >= src.stat().st_mtime:
                return 0
        else:
            try:
                if dst.read_text(encoding="utf-8", errors="replace") == text:
                    return 0
            except OSError:
                pass

    if dry:
        return 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    if text is None:
        shutil.copy2(src, dst)
    else:
        dst.write_text(text, encoding="utf-8")
    return 1


def copy_decided(
    fx: Path,
    decision: ShaderDecision,
    include_src: dict[str, Path],
    texture_src: dict[str, Path],
    shader_dir: Path,
    texture_dir: Path,
    force: bool = False,
    dry: bool = False,
) -> int:
    """Copy an imported shader, its transitive includes, and its textures.

    Each include/texture is copied to its flat path and its preserved subpath,
    so both ``#include "foo.fxh"`` and ``#include "Includes/foo.fxh"`` resolve
    after the rewrite. Returns the number of files copied (or, in dry-run, the
    number that would be copied).
    """
    n = _copy(fx, shader_dir / fx.name, force, dry, normalize_text=True)

    copied_incs: set[str] = set()
    for norm in decision.resolved_includes:
        src = include_src.get(norm.lower())
        if src is None:
            continue
        targets = {norm}
        base = Path(norm).name
        if base != norm:
            targets.add(base)
        for t in targets:
            key = ("i", t.lower())
            if key in copied_incs:
                continue
            copied_incs.add(key)
            n += _copy(src, shader_dir / t, force, dry, normalize_text=True)

    copied_texs: set[str] = set()
    for norm in decision.resolved_textures:
        src = texture_src.get(norm.lower())
        if src is None:
            continue
        targets = {norm}
        base = Path(norm).name
        if base != norm:
            targets.add(base)
        for t in targets:
            key = ("t", t.lower())
            if key in copied_texs:
                continue
            copied_texs.add(key)
            n += _copy(src, texture_dir / t, force, dry)

    return n


def import_root(
    root: Path,
    shader_dir: Path,
    texture_dir: Path,
    *,
    force: bool = False,
    dry: bool = False,
) -> ImportResult:
    """Discover, validate, and copy every importable shader under `root`.

    The single entry point the CLI script and the GUI share: validate against
    the same flattened namespace vkBasalt sees, copy only shaders whose
    dependencies resolve, and rewrite Windows separators on the way in.
    Rejected shaders come back with their reasons and are never written.
    """
    shaders, includes, textures = discover_assets(root)
    include_src, texture_src, inc_collisions, tex_collisions = build_available(
        includes, textures, shader_dir, texture_dir,
    )
    decisions = [validate_shader(fx, include_src, texture_src) for fx in shaders]

    copied = 0
    for fx, decision in zip(shaders, decisions):
        if decision.status == "imported":
            copied += copy_decided(
                fx, decision, include_src, texture_src,
                shader_dir, texture_dir, force, dry,
            )

    return ImportResult(
        shaders=shaders,
        decisions=decisions,
        inc_collisions=inc_collisions,
        tex_collisions=tex_collisions,
        copied=copied,
    )


def remove_verified_broken(
    names: list[str],
    shader_dir: Path,
    source_by_name: dict[str, Path],
) -> list[str]:
    """Delete our rewritten .fx copies for shaders vkBasalt refused.

    Shared headers and textures are left alone. The content check avoids
    deleting a different, newer file the user may have placed under the
    same name.
    """
    removed: list[str] = []
    for name in names:
        src = source_by_name.get(name)
        dst = shader_dir / f"{name}.fx"
        if src is None or not dst.is_file():
            continue
        try:
            expected = rewrite_references(
                src.read_text(encoding="utf-8", errors="replace")
            )
            actual = dst.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if actual == expected:
            dst.unlink()
            removed.append(name)
    return removed
