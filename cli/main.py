"""
cli/main.py

Argument parsing and command dispatch for `sober-shader`.

The CLI is a thin wrapper around the core modules. Every command maps to
one or two core function calls plus formatting. The only command with real
logic is `apply`, which orchestrates the test-and-rollback flow.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core import config as config_mod
from core import launch as launch_mod
from core import runtime as runtime_mod

from cli import format as fmt


DEFAULT_ACTIVE_CONFIG = Path.home() / (
    ".var/app/org.vinegarhq.Sober/config/vkBasalt/vkBasalt.conf"
)


def _backup_path(active: Path) -> Path:
    return active.with_name("last-known-good.conf")


# --- commands ------------------------------------------------------------

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
        result = launch_mod.launch(verify=True)
    except launch_mod.LaunchError as e:
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

    return parser


COMMANDS = {
    "check": cmd_check,
    "status": cmd_status,
    "apply": cmd_apply,
    "launch": cmd_launch,
    "rollback": cmd_rollback,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = COMMANDS[args.command]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
