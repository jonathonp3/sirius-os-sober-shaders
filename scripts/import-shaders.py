#!/usr/bin/env python3
"""
scripts/import-shaders.py

Import shaders, includes, and textures from a user-supplied directory into
the Sober sandbox where vkBasalt reads them. For shaders downloaded from
sources other than the GShade/ReShade repos that the app's Fetch command
clones (e.g. individual ReShade presets, forum posts, personal collections).

The source directory may contain any of:
  *.fx files              — shaders
  *.fxh files             — include headers used by shaders
  Textures/ subdirectory  — textures referenced by shaders
  (all three, or a subset)

Files are copied to:
  ~/.var/app/org.vinegarhq.Sober/config/vkBasalt/Shaders/
  ~/.var/app/org.vinegarhq.Sober/config/vkBasalt/Textures/

After copying, each imported .fx is parsed and checked against the sandbox
contents. The report says which shaders will load (dependencies found) and
which will gray out in the GUI (dependencies missing), with the missing
filenames for the latter.

Usage:
  import-shaders.py SOURCE_DIR [--dry-run] [--force]

  --dry-run   Show what would be copied and what would resolve, without
              touching the sandbox.
  --force     Overwrite existing files (default: only copy files that are
              new or newer in SOURCE_DIR than in the sandbox).
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# Add the app's lib dir to sys.path so `from core import shader` works.
# When run from the repo, gui/ and core/ are siblings of scripts/.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if (_REPO_ROOT / "core").is_dir():
    sys.path.insert(0, str(_REPO_ROOT))

from core import shader  # noqa: E402


SANDBOX_CFG = Path.home() / ".var/app/org.vinegarhq.Sober/config/vkBasalt"
SANDBOX_SHADERS = SANDBOX_CFG / "Shaders"
SANDBOX_TEXTURES = SANDBOX_CFG / "Textures"


def copy_file(src: Path, dst_dir: Path, force: bool, dry: bool) -> bool:
    """Copy src to dst_dir. Return True if it was (or would be) copied."""
    dst = dst_dir / src.name
    if not force and dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return False
    if dry:
        print(f"  would copy {src.name}")
        return True
    dst_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def collect_sources(source_dir: Path) -> tuple[list[Path], list[Path]]:
    """
    Return (shader_files, texture_files).
    shader_files includes .fx and .fxh from source_dir (top level only).
    texture_files is everything in source_dir/Textures (if it exists).
    """
    fx = sorted(p for p in source_dir.glob("*.fx") if p.is_file())
    fxh = sorted(p for p in source_dir.glob("*.fxh") if p.is_file())
    textures: list[Path] = []
    tex_dir = source_dir / "Textures"
    if tex_dir.is_dir():
        textures = sorted(p for p in tex_dir.iterdir() if p.is_file())
    return fx + fxh, textures


def report_dependencies(
    shader_paths: list[Path],
    include_dir: Path,
    texture_dir: Path,
) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """
    For each .fx in shader_paths, check its dependencies.
    Return (ok_names, grayed) where grayed is [(name, missing_list)].
    """
    ok: list[str] = []
    grayed: list[tuple[str, list[str]]] = []
    for fx in shader_paths:
        if fx.suffix != ".fx":
            continue
        try:
            info = shader.parse_fx(fx)
        except Exception as e:  # noqa: BLE001 — parser can fail on malformed .fx
            grayed.append((fx.stem, [f"parse error: {e}"]))
            continue
        missing = shader.check_dependencies(
            info, include_dir=include_dir, texture_dir=texture_dir,
        )
        if missing:
            grayed.append((fx.stem, missing))
        else:
            ok.append(fx.stem)
    return ok, grayed


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description="Import shaders and textures into the Sober sandbox.",
    )
    p.add_argument("source", type=Path, help="Directory containing .fx/.fxh/Textures")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be copied; don't touch the sandbox")
    p.add_argument("--force", action="store_true",
                   help="Overwrite existing files (default: only newer)")
    args = p.parse_args(argv)

    src = args.source.expanduser()
    if not src.is_dir():
        print(f"error: not a directory: {src}", file=sys.stderr)
        return 1

    shader_files, texture_files = collect_sources(src)
    if not shader_files and not texture_files:
        print(
            f"error: no .fx/.fxh files or Textures/ in {src}",
            file=sys.stderr,
        )
        return 1

    print(f"Source: {src}")
    print(f"  {len(shader_files)} shader/include file(s)")
    print(f"  {len(texture_files)} texture file(s)")
    print()

    print("Copying to sandbox:")
    n_sh = sum(copy_file(p, SANDBOX_SHADERS, args.force, args.dry_run) for p in shader_files)
    n_tex = sum(copy_file(p, SANDBOX_TEXTURES, args.force, args.dry_run) for p in texture_files)
    if args.dry_run:
        print(f"  (dry run: {n_sh + n_tex} file(s) would be copied)")
    else:
        print(f"  copied {n_sh} shader/include file(s), {n_tex} texture file(s)")
    print()

    # Validate against the sandbox: includes and textures are resolved
    # relative to the sandbox, matching what vkBasalt will see.
    print("Validating dependencies against sandbox:")
    ok, grayed = report_dependencies(
        shader_files, SANDBOX_SHADERS, SANDBOX_TEXTURES,
    )
    print(f"  {len(ok)} shader(s) fully resolved")
    if grayed:
        print(f"  {len(grayed)} shader(s) missing dependencies:")
        for name, missing in grayed:
            print(f"    {name}: {', '.join(missing)}")
    print()

    if grayed:
        print(
            "Shaders with missing dependencies will appear grayed out in the\n"
            "GUI. Add the missing .fxh/.png to the source directory and re-run,\n"
            "or source them from another package."
        )
        return 0  # import succeeded, some shaders just won't load
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
