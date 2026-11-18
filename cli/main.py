"""
cli/main.py

Argument parsing and command dispatch for `sober-shader`.

The CLI is a thin wrapper around the core modules. Every command maps to
one or two core function calls plus formatting. The only command with real
logic is `apply`, which orchestrates the test-and-rollback flow.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core import config as config_mod
from core import launch as launch_mod
from core import runtime as runtime_mod

from cli import format as fmt


DEFAULT_ACTIVE_CONFIG = Path.home() / (
    ".var/app/org.vinegarhq.Sober/config/vkBasalt/vkBasalt.conf"
)
SANDBOX_CONFIG_DIR = DEFAULT_ACTIVE_CONFIG.parent
SANDBOX_SHADER_DIR = SANDBOX_CONFIG_DIR / "Shaders"
SANDBOX_TEXTURE_DIR = SANDBOX_CONFIG_DIR / "Textures"


def _backup_path(active: Path) -> Path:
    return active.with_name("last-known-good.conf")

def cmd_list_shaders(args: argparse.Namespace) -> int:
    from core import shader as shader_mod

    shader_dir = (
        Path(args.shader_dir).expanduser()
        if args.shader_dir
        else Path.home() / "GShade-Shaders" / "Shaders"
    )
    infos = shader_mod.discover(shader_dir)

    if args.json:
        import dataclasses, json
        print(json.dumps(
            {
                "shader_dir": str(shader_dir),
                "shaders": [
                    {
                        "name": i.name,
                        "path": str(i.path),
                        "uniforms": [dataclasses.asdict(u) for u in i.uniforms],
                        "includes": i.includes,
                        "textures": i.textures,
                        "warnings": i.parse_warnings,
                    }
                    for i in infos
                ],
            },
            indent=2,
        ))
        return 0

    if not infos:
        print(f"No .fx files found in {shader_dir}", file=sys.stderr)
        return 0

    print(f"Shader directory: {shader_dir}")
    print()
    for info in infos:
        missing = shader_mod.check_dependencies(
            info,
            include_dir=shader_dir,
            texture_dir=shader_dir.parent / "Textures",
        )
        marker = ""
        if missing:
            marker = f"  [{len(missing)} missing dep(s)]"
        print(f"{info.name}.fx{marker}")
        for u in info.uniforms:
            if u.ui_type == "slider" and u.ui_min is not None and u.ui_max is not None:
                print(f"  {u.name} = {u.default}  [{u.ui_min}–{u.ui_max}]")
            elif u.ui_type == "combo" and u.ui_items:
                print(f"  {u.name} = {u.default}  [{', '.join(u.ui_items)}]")
            else:
                print(f"  {u.name} = {u.default}")
        print()
    return 0

def cmd_validate_config(args: argparse.Namespace) -> int:
    config_path = Path(args.config_file)
    try:
        cfg = config_mod.parse(config_path)
    except config_mod.ConfigError as e:
        print(f"Invalid config: {e}", file=sys.stderr)
        return 5

    errors = config_mod.validate(cfg)

    shader_dir = (
        Path(args.shader_dir).expanduser()
        if args.shader_dir
        else Path.home() / "GShade-Shaders" / "Shaders"
    )
    errors += config_mod.validate_against_shaders(cfg, shader_dir)

    if args.json:
        import json
        print(json.dumps({"valid": not errors, "errors": errors}, indent=2))
    else:
        if not errors:
            print(f"{config_path}: OK")
            print(f"Effects: {':'.join(cfg.effects)}")
        else:
            print(f"{config_path}: {len(errors)} error(s)", file=sys.stderr)
            for e in errors:
                print(f"  {e}", file=sys.stderr)

    return 0 if not errors else 13

def cmd_fetch_shaders(args: argparse.Namespace) -> int:
    from core import fetch as fetch_mod

    dest = (
        Path(args.dest).expanduser()
        if args.dest
        else Path.home() / "GShade-Shaders"
    )
    try:
        result = fetch_mod.fetch(dest, update=args.update)
    except fetch_mod.FetchError as e:
        print(f"fetch failed: {e}", file=sys.stderr)
        return 11

    if args.json:
        import json
        print(json.dumps(
            {
                "action": result.action,
                "dest": str(result.dest),
                "success": result.success,
                "message": result.message,
            },
            indent=2,
        ))
    else:
        if result.action == "cloned":
            print(f"Cloned GShade-Shaders to {result.dest}")
        elif result.action == "updated":
            print(f"Updated {result.dest}")
        elif result.action == "already-present":
            print(result.message)
        else:
            print(f"Failed: {result.message}", file=sys.stderr)

    if result.action == "failed":
        return 12
    return 0

# --- commands ------------------------------------------------------------

def cmd_import_shaders(args: argparse.Namespace) -> int:
    from core import importing

    source = Path(args.source).expanduser()
    if not source.is_dir():
        message = f"not a directory: {source}"
        if args.json:
            print(json.dumps({"source": str(source), "error": message}))
        else:
            print(message, file=sys.stderr)
        return 1

    verify_requested = args.verify or args.verify_each
    if verify_requested and launch_mod._sober_running():
        message = "Sober is running; close it before verifying"
        if args.json:
            print(json.dumps({"source": str(source), "error": message}))
        else:
            print(message, file=sys.stderr)
        return 1

    result = importing.import_root(
        source,
        SANDBOX_SHADER_DIR,
        SANDBOX_TEXTURE_DIR,
        force=args.force,
        dry=args.dry_run,
    )

    if not result.shaders:
        message = f"no .fx files found under {source}"
        if args.json:
            print(json.dumps({"source": str(source), "error": message}))
        else:
            print(f"No .fx files found under {source}", file=sys.stderr)
        return 1

    imported = [d for d in result.decisions if d.status == "imported"]
    rejected = [d for d in result.decisions if d.status == "rejected"]

    verify_broken: list[str] = []
    verify_inconclusive: list[str] = []
    if verify_requested and imported and not args.dry_run:
        from core import verify as verify_mod

        vr = verify_mod.verify_shaders(
            [d.name for d in imported],
            shader_dir=SANDBOX_SHADER_DIR,
            texture_dir=SANDBOX_TEXTURE_DIR,
            config_path=Path(args.config),
            each=args.verify_each,
        )
        verify_broken = vr.broken
        verify_inconclusive = vr.inconclusive
        removed = set(importing.remove_verified_broken(
            verify_broken,
            SANDBOX_SHADER_DIR,
            {fx.stem: fx for fx in result.shaders},
        ))
        if removed:
            for d in result.decisions:
                if d.name in removed:
                    d.status = "rejected"
                    d.reason = "failed vkBasalt compile (skipped)"
            imported = [d for d in result.decisions if d.status == "imported"]
            rejected = [d for d in result.decisions if d.status == "rejected"]
            result.copied = max(0, result.copied - len(removed))

    payload = {
        "source": str(source),
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
        "collisions": {
            "includes": result.inc_collisions,
            "textures": result.tex_collisions,
        },
        "verify_broken": verify_broken,
        "verify_inconclusive": verify_inconclusive,
        "copied": result.copied,
        "dry_run": args.dry_run,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print(f"Import report: {source}")
    print(f"  imported: {len(imported)} shader(s)")
    for d in imported:
        print(f"    {d.name}")
    if rejected:
        print(f"  rejected: {len(rejected)} shader(s)")
        for d in rejected:
            print(f"    {d.name}: {d.reason}")
    if verify_broken:
        print(f"  failed compile (skipped): {', '.join(verify_broken)}")
    if verify_inconclusive:
        print(f"  verification inconclusive: {', '.join(verify_inconclusive)}")
    collisions = result.inc_collisions + result.tex_collisions
    if collisions:
        print(f"  name collisions: {', '.join(sorted(set(collisions)))}")
    print(f"  {'would copy' if args.dry_run else 'copied'} {result.copied} file(s)")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    result = runtime_mod.check_compatibility()
    if args.json:
        print(fmt.check_json(result))
    else:
        print(fmt.check_human(result))

    return {
        "ok": 0,
        "mismatch": 1,
        "missing": 2,
        "no_sober": 3,
        "error": 4,
    }.get(result.status, 4)


def cmd_status(args: argparse.Namespace) -> int:
    active = Path(args.config)
    compat = runtime_mod.check_compatibility()

    cfg = None
    if active.exists():
        try:
            cfg = config_mod.parse(active)
        except config_mod.ConfigError:
            cfg = None

    backup = _backup_path(active)
    if args.json:
        print(fmt.status_json(compat, cfg, str(active), backup.exists(), str(backup)))
    else:
        print(fmt.status_human(compat, cfg, str(active), backup.exists(), str(backup)))
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    active = Path(args.config)
    backup = _backup_path(active)
    new_cfg_path = Path(args.config_file)

    # 1. Parse and validate the new config.
    try:
        new_cfg = config_mod.parse(new_cfg_path)
    except config_mod.ConfigError as e:
        print(f"Invalid config: {e}", file=sys.stderr)
        return 5

    errors = config_mod.validate(new_cfg)
    if errors:
        print("Config validation failed:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 5

    # 2. Back up the current config (if it exists).
    had_active = active.exists()
    if had_active:
        config_mod.backup(active, backup)

    # 3. Write the new config.
    config_mod.write(new_cfg, active)

    # 4. Test it by launching Sober in verify mode. This blocks until
    #    Sober exits (Option B — see README for the detach limitation).
    if not args.quiet:
        print("Launching Sober (verify mode)...", file=sys.stderr)

    try:
        if getattr(args, "detach", False):
            result = launch_mod.launch(verify_then_detach=True)
        else:
            result = launch_mod.launch(verify=True)
    except launch_mod.LaunchError as e:
        ...
        # Could not even spawn flatpak — restore and fail.
        if had_active:
            config_mod.restore(backup, active)
        print(f"Launch error: {e}", file=sys.stderr)
        return 7

    # 5. Success? Keep the new config. Failure? Restore.
    if result.success:
        if args.json:
            print(fmt.apply_json(True, [], False, new_cfg.effects))
        else:
            if getattr(args, "detach", False):
                print("\n".join([
                    "Result: SUCCESS",
                    f"Effects: {':'.join(new_cfg.effects)}",
                    "Sober is running in the background with the new config.",
                    "Closing this terminal will not stop Sober.",
                ]))
            else:
                print(fmt.apply_success_human(new_cfg.effects))
        return 0

    restored = False
    if had_active:
        try:
            config_mod.restore(backup, active)
            restored = True
        except config_mod.ConfigError:
            restored = False

    if args.json:
        print(fmt.apply_json(False, result.vkbasalt_errors, restored, new_cfg.effects))
    else:
        print(fmt.apply_failure_human(result.vkbasalt_errors, restored))
    return 6 if result.vkbasalt_errors else 7


def cmd_launch(args: argparse.Namespace) -> int:
    # Validate the active config exists and parses before launching.
    active = Path(args.config)
    if not active.exists():
        print(f"Config not found: {active}", file=sys.stderr)
        return 5
    try:
        config_mod.parse(active)
    except config_mod.ConfigError as e:
        print(f"Config invalid: {e}", file=sys.stderr)
        return 5

    detach = args.detach if args.detach is not None else (not args.verify)
    try:
        result = launch_mod.launch(detach=detach, verify=args.verify)
    except launch_mod.LaunchError as e:
        print(f"Launch error: {e}", file=sys.stderr)
        return 7

    if args.json:
        print(fmt.launch_json(result))
    else:
        print(fmt.launch_human(result))

    if result.mode == "verify" and not result.success:
        return 9
    if result.mode == "normal" and not result.success:
        return result.exit_code or 7
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    active = Path(args.config)
    backup = _backup_path(active)
    if not backup.exists():
        print(f"No backup found at {backup}", file=sys.stderr)
        if args.json:
            print(fmt.rollback_json(False, None))
        return 10

    try:
        config_mod.restore(backup, active)
    except config_mod.ConfigError as e:
        print(f"Rollback failed: {e}", file=sys.stderr)
        return 10

    effects = None
    try:
        effects = config_mod.parse(active).effects
    except config_mod.ConfigError:
        pass

    if args.json:
        print(fmt.rollback_json(True, effects))
    else:
        print(fmt.rollback_human(effects))
    return 0


# --- argparse ------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sober-shader",
        description=(
            "Manage and launch vkBasalt shader chains with Sober (Roblox Flatpak)."
        ),
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_ACTIVE_CONFIG),
        help="Active vkBasalt.conf path (default: %(default)s)",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="Check runtime/vkBasalt compatibility")

    sub.add_parser("status", help="Show active config, compatibility, and backup state")

    p_apply = sub.add_parser(
        "apply",
        help="Validate, write, and test a config; roll back on failure",
    )
    p_apply.add_argument("config_file", help="Path to the vkBasalt.conf to apply")
    p_apply.add_argument(
        "--detach", action="store_true",
        help=(
            "Verify the config and leave Sober running. Without this, apply "
            "blocks until Sober exits."
        ),
    )

    p_launch = sub.add_parser("launch", help="Launch Sober with the active config")
    p_launch.add_argument(
        "--verify", action="store_true",
        help="Capture output and check that vkBasalt loaded (attached)",
    )
    p_launch.add_argument(
        "--detach", dest="detach", action="store_true", default=None,
        help="Launch detached (default unless --verify)",
    )
    p_launch.add_argument(
        "--no-detach", dest="detach", action="store_false",
        help="Launch attached",
    )

    sub.add_parser("rollback", help="Restore last-known-good.conf")

    p_list = sub.add_parser(
        "list-shaders",
        help="Parse .fx files and show their uniforms",
    )
    p_list.add_argument(
        "--shader-dir",
        help="Directory containing .fx files (default: ~/GShade-Shaders/Shaders)",
    )

    p_fetch = sub.add_parser(
        "fetch-shaders",
        help="Clone or update the GShade-Shaders repository",
    )
    p_fetch.add_argument(
        "--dest",
        help="Destination directory (default: ~/GShade-Shaders)",
    )
   
    p_fetch.add_argument(
        "--update", action="store_true",
        help="Update an existing clone instead of reporting already-present",
    )

    p_import = sub.add_parser(
        "import-shaders",
        help="Import a folder of shaders, validating dependencies first",
    )
    p_import.add_argument(
        "source", help="Directory containing .fx/.fxh files and Textures/"
    )
    p_import.add_argument(
        "--dry-run", action="store_true",
        help="Validate and report without touching the sandbox",
    )
    p_import.add_argument(
        "--force", action="store_true",
        help="Overwrite sandbox files even when they look up to date",
    )
    p_import.add_argument(
        "--verify", action="store_true",
        help="Launch Sober once with all imported shaders and drop any that "
             "vkBasalt refuses to compile",
    )
    p_import.add_argument(
        "--verify-each", action="store_true",
        help="Like --verify, but launch one shader at a time for exact "
             "attribution (slower)",
    )

    p_validate = sub.add_parser(
        "validate-config",
        help="Check a vkBasalt.conf for structural and parameter-name errors",
    )
    p_validate.add_argument("config_file", help="Path to the vkBasalt.conf to check")
    p_validate.add_argument(
        "--shader-dir",
        help="Directory containing .fx files (default: ~/GShade-Shaders/Shaders)",
    )

    return parser

COMMANDS = {
    "check": cmd_check,
    "status": cmd_status,
    "apply": cmd_apply,
    "launch": cmd_launch,
    "rollback": cmd_rollback,
    "list-shaders": cmd_list_shaders,
    "fetch-shaders": cmd_fetch_shaders,
    "import-shaders": cmd_import_shaders,
    "validate-config": cmd_validate_config,
}

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = COMMANDS[args.command]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
