#!/usr/bin/env python3
"""
scripts/import-shaders.py

Import shaders from a GitHub repository or a local directory into the Sober
sandbox, validating each shader *before* it is copied so only shaders whose
dependencies actually resolve are added. Broken shaders are skipped and
reported, keeping the GUI's shader list clean.

The source may be:
  - a local directory (e.g. a clone of qUINT or reshade-shaders), or
  - a git URL (https://github.com/... or git@github.com:...), cloned into a
    temporary directory.

The importer locates the assets itself, so it does not matter whether the
repo keeps shaders in Shaders/ at the top level, headers in an Includes/
subdirectory, or textures under Textures/<author>/. Windows-style backslashes
in #include and texture source paths are normalized. Because vkBasalt never
converts backslashes when it opens an include, the copied .fx/.fxh files are
rewritten with POSIX relative paths rather than copied verbatim.

Validation is compiler-accurate: an #include resolves only against the flat
shader directory (the way vkBasalt's include path works), and a texture
against the flat texture directory. Every include (transitively) and every
texture a shader references must be present — either in the source repo or
already in the sandbox (e.g. ReShade.fxh from a prior Fetch).

Discovery, validation, and copying live in core/importing.py so the GUI can
reuse the same decisions; this script adds source acquisition and reporting.

Usage:
  import-shaders.py SOURCE [--dry-run] [--force] [--json] [--verify]
                     [--verify-each]

  --dry-run   Validate and report without touching the sandbox.
  --force     Overwrite existing files (default: copy only newer).
  --json      Machine-readable report (for the GUI).
  --verify    Additionally launch Sober in verify mode to catch shaders whose
              dependencies resolve but still fail to compile in vkBasalt.
  --verify-each
              Like --verify, but launch one shader at a time so errors that
              vkBasalt reports at a header path are attributed exactly.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if (_REPO_ROOT / "core").is_dir():
    sys.path.insert(0, str(_REPO_ROOT))

from core import importing  # noqa: E402


SANDBOX_CFG = Path.home() / ".var/app/org.vinegarhq.Sober/config/vkBasalt"
SANDBOX_SHADERS = SANDBOX_CFG / "Shaders"
SANDBOX_TEXTURES = SANDBOX_CFG / "Textures"
ACTIVE_CONFIG = SANDBOX_CFG / "vkBasalt.conf"

# How long to let Sober run during --verify before killing it and reading
# whatever vkBasalt logged. Compile errors are emitted right after the config
# block, so a few seconds is enough.
COMPILE_TIMEOUT = 20.0

# Per-shader verification launches one Sober per shader so a header-located
# error can be attributed exactly. Good shaders never exit, so this is the
# wall-clock cost per shader; bad ones usually crash well before it.
COMPILE_EACH_TIMEOUT = 8.0


# --- source acquisition ---------------------------------------------------

def is_git_url(source: str) -> bool:
    s = source.strip()
    return (
        s.startswith(("https://", "http://", "git://", "ssh://", "git@"))
        or s.endswith(".git")
    )


def clone_repo(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "clone", "--depth=1", url, str(dest)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            "git clone failed: " + (result.stderr or result.stdout or "").strip()
        )
    return dest


# --- optional compile verification ----------------------------------------

def _verify_config(names: list[str]):
    from core import config as config_mod

    cfg = config_mod.VkBasaltConfig()
    cfg.depth_capture = "on"
    cfg.reshade_texture_path = str(SANDBOX_TEXTURES)
    cfg.reshade_include_path = str(SANDBOX_SHADERS)
    for name in names:
        alias = "".join(c for c in name.lower() if c.isalnum()) or "shader"
        cfg.effects.append(alias)
        cfg.shader_paths[alias] = str(SANDBOX_SHADERS / f"{name}.fx")
    return cfg


def _kill_sober() -> None:
    subprocess.run(["flatpak", "kill", "org.vinegarhq.Sober"],
                   capture_output=True, check=False)
    time.sleep(2.0)


def _capture_verify(names: list[str], timeout: float) -> tuple[bool, list[str]]:
    from core import config as config_mod
    from core import launch as launch_mod

    config_mod.write(_verify_config(names), ACTIVE_CONFIG)
    args = launch_mod._build_argv(verify=True)
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Kill the app before draining: the sandboxed child inherits our
        # stdout pipe, so communicate() can block until it is gone.
        _kill_sober()
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out = b""
    text = out.decode("utf-8", errors="replace")
    return launch_mod._parse_vkbasalt_log(text)


def compile_verify(names: list[str], each: bool = False) -> list[str]:
    """
    Launch Sober in verify mode and return the shader names that failed.

    With `each=False` all candidates go in one config; errors are attributed
    by looking for the shader name in the error line. That misses errors that
    vkBasalt reports at a header location (e.g. `Includes/bb_normal.fxh:132`).
    With `each=True` one shader is launched per config, so any error belongs
    to that shader — slower, but exact.

    The active config is saved and restored; Sober is killed afterwards.
    Best-effort: the raw vkBasalt errors are still printed for inspection.
    """
    backup = ACTIVE_CONFIG.with_name("vkBasalt.conf.import-verify-bak")
    had_active = ACTIVE_CONFIG.exists()
    if had_active:
        shutil.copy2(ACTIVE_CONFIG, backup)

    broken: list[str] = []
    groups = [[name] for name in names] if each else [list(names)]
    try:
        for group in groups:
            loaded, errors = _capture_verify(
                group,
                COMPILE_EACH_TIMEOUT if each else COMPILE_TIMEOUT,
            )
            if each:
                # The config held exactly this shader, so any error is its.
                # If Sober exited before vkBasalt loaded there is no verdict;
                # report it rather than deleting a possibly-good shader.
                if errors:
                    broken.append(group[0])
                elif not loaded:
                    print(
                        f"warning: verification inconclusive for {group[0]} "
                        "(Sober exited before vkBasalt loaded)",
                        file=sys.stderr,
                    )
                _kill_sober()
                time.sleep(1.0)
            else:
                for err in errors:
                    for name in names:
                        if name.lower() in err.lower():
                            broken.append(name)
    finally:
        if had_active:
            shutil.copy2(backup, ACTIVE_CONFIG)
            backup.unlink(missing_ok=True)
        else:
            ACTIVE_CONFIG.unlink(missing_ok=True)
        _kill_sober()

    seen: set[str] = set()
    return [b for b in broken if not (b in seen or seen.add(b))]

# --- verified-broken cleanup ----------------------------------------------

def _remove_verified_broken(
    names: list[str],
    source_by_name: dict[str, Path],
) -> list[str]:
    """Delete .fx files that vkBasalt refused, if they are our rewritten copy.

    Shared headers/textures are left alone: only the top-level shader is
    removed, so a broken shader stops appearing in the GUI's shader list.
    The content check avoids deleting a different, newer file the user may
    have placed in the sandbox under the same name.
    """
    removed: list[str] = []
    for name in names:
        src = source_by_name.get(name)
        dst = SANDBOX_SHADERS / f"{name}.fx"
        if src is None or not dst.is_file():
            continue
        try:
            expected = importing.rewrite_references(
                src.read_text(encoding="utf-8", errors="replace")
            )
            actual = dst.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if actual == expected:
            dst.unlink()
            removed.append(name)
    return removed


# --- reporting ------------------------------------------------------------

def report(
    source: str,
    imported: list[importing.ShaderDecision],
    rejected: list[importing.ShaderDecision],
    verify_broken: list[str],
    inc_collisions: list[str],
    tex_collisions: list[str],
    copied: int,
    dry: bool,
    as_json: bool,
) -> None:
    payload = {
        "source": source,
        "imported": [
            {
                "name": d.name,
                "includes": d.resolved_includes,
                "textures": d.resolved_textures,
            }
            for d in imported
        ],
        "rejected": [
            {"name": d.name, "reason": d.reason, "missing": d.missing}
            for d in rejected
        ],
        "verify_broken": verify_broken,
        "collisions": {"includes": inc_collisions, "textures": tex_collisions},
        "copied": copied,
        "dry_run": dry,
    }
    if as_json:
        print(json.dumps(payload, indent=2))
        return

    print(f"Import report: {source}")
    print(f"  imported: {len(imported)} shader(s)")
    for d in imported:
        print(f"    {d.name}")
    if rejected:
        print(f"  rejected: {len(rejected)} shader(s)")
        for d in rejected:
            print(f"    {d.name}: {d.reason}")
    if verify_broken:
        print(f"  failed vkBasalt compile ({len(verify_broken)}):")
        for n in verify_broken:
            print(f"    {n}")
    if inc_collisions:
        print(f"  header name collisions: {', '.join(inc_collisions)}")
    if tex_collisions:
        print(f"  texture name collisions: {', '.join(tex_collisions)}")
    print(f"  {'would copy' if dry else 'copied'} {copied} file(s)")


# --- main -----------------------------------------------------------------

def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description="Import shaders from a repo/directory into the Sober sandbox.",
    )
    p.add_argument("source", help="A directory or a git URL")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate and report without touching the sandbox")
    p.add_argument("--force", action="store_true",
                   help="Overwrite existing files (default: only newer)")
    p.add_argument("--json", action="store_true",
                   help="Machine-readable report")
    p.add_argument("--verify", action="store_true",
                   help="Launch Sober to catch shaders that still fail to compile")
    p.add_argument("--verify-each", action="store_true",
                   help="Verify one shader per launch (slower, exact attribution)")
    args = p.parse_args(argv)

    tmp: str | None = None
    try:
        if is_git_url(args.source):
            tmp = tempfile.mkdtemp(prefix="shader-import-")
            root = clone_repo(args.source, Path(tmp))
        else:
            root = Path(args.source).expanduser()
            if not root.is_dir():
                print(f"error: not a directory or URL: {args.source}", file=sys.stderr)
                return 1

        result = importing.import_root(
            root, SANDBOX_SHADERS, SANDBOX_TEXTURES,
            force=args.force, dry=args.dry_run,
        )
        if not result.shaders:
            print(f"error: no .fx files found under {root}", file=sys.stderr)
            return 1

        imported = [d for d in result.decisions if d.status == "imported"]
        rejected = [d for d in result.decisions if d.status == "rejected"]
        copied = result.copied

        verify_broken: list[str] = []
        do_verify = args.verify or args.verify_each
        if do_verify and imported and not args.dry_run:
            mode = "one shader at a time" if args.verify_each else "in one batch"
            print(f"Launching Sober to verify compilation ({mode})...",
                  file=sys.stderr)
            verify_broken = compile_verify(
                [d.name for d in imported], each=args.verify_each,
            )
            source_by_name = {fx.stem: fx for fx in result.shaders}
            removed = set(_remove_verified_broken(verify_broken, source_by_name))
            if removed:
                for d in result.decisions:
                    if d.name in removed:
                        d.status = "rejected"
                        d.reason = "failed vkBasalt compile (skipped)"
                imported = [d for d in result.decisions if d.status == "imported"]
                rejected = [d for d in result.decisions if d.status == "rejected"]
                copied = max(0, copied - len(removed))

        report(
            str(args.source), imported, rejected, verify_broken,
            result.inc_collisions, result.tex_collisions,
            copied, args.dry_run, args.json,
        )
        return 0
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
