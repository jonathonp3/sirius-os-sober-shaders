"""
cli/format.py

Output formatting for the CLI: human-readable text and machine-readable JSON.
The core modules return dataclasses; this module knows how to render them.
"""

from __future__ import annotations

import json
import sys

from core import config as config_mod
from core import runtime as runtime_mod


def _emit(text: str, stream=None) -> None:
    (stream or sys.stdout).write(text if text.endswith("\n") else text + "\n")


# --- check ---------------------------------------------------------------

def check_human(result: runtime_mod.CompatibilityResult) -> str:
    lines: list[str] = []
    if result.status == "no_sober":
        lines.append("Status:       SOBER NOT INSTALLED")
        lines.append("Sober is not installed as a Flatpak. Install it first:")
        lines.append("  flatpak install flathub org.vinegarhq.Sober")
        return "\n".join(lines)

    if result.status == "error":
        lines.append("Status:       ERROR")
        if result.error:
            lines.append(f"Error:        {result.error}")
        return "\n".join(lines)

    lines.append(f"Sober runtime:      {result.sober_runtime or 'unknown'}")
    lines.append(f"vkBasalt extension: {result.installed_vkbasalt_branch or '(not installed)'}")
    lines.append(f"Expected extension: {result.expected_vkbasalt_branch or 'unknown'}")

    if result.status == "ok":
        lines.append("Status:             OK")
    elif result.status == "mismatch":
        lines.append("Status:             MISMATCH")
        lines.append("")
        lines.append("Fix:")
        for cmd in result.fix_commands:
            lines.append(f"  {cmd}")
    elif result.status == "missing":
        lines.append("Status:             MISSING")
        lines.append("")
        lines.append("Fix:")
        for cmd in result.fix_commands:
            lines.append(f"  {cmd}")
    return "\n".join(lines)


def check_json(result: runtime_mod.CompatibilityResult) -> str:
    return json.dumps(
        {
            "status": result.status,
            "sober_runtime": result.sober_runtime,
            "installed_vkbasalt_branch": result.installed_vkbasalt_branch,
            "expected_vkbasalt_branch": result.expected_vkbasalt_branch,
            "fix_commands": result.fix_commands,
            "error": result.error,
        },
        indent=2,
    )


# --- status --------------------------------------------------------------

def status_human(
    result: runtime_mod.CompatibilityResult,
    cfg: config_mod.VkBasaltConfig | None,
    config_path: str,
    backup_exists: bool,
    backup_path: str,
) -> str:
    lines: list[str] = []
    lines.append(f"Config:           {config_path}")
    if cfg is None:
        lines.append("Effects:          (config missing or unreadable)")
    else:
        lines.append(f"Effects:          {':'.join(cfg.effects) or '(none)'}")
    lines.append(f"Compatibility:    {result.status}")

    if result.status == "mismatch":
        lines.append("")
        lines.append("Fix:")
        for cmd in result.fix_commands:
            lines.append(f"  {cmd}")
    elif result.status == "missing":
        lines.append("")
        lines.append("Fix:")
        for cmd in result.fix_commands:
            lines.append(f"  {cmd}")

    if backup_exists:
        lines.append(f"Last-known-good:  yes ({backup_path})")
    else:
        lines.append("Last-known-good:  no")

    return "\n".join(lines)


def status_json(
    result: runtime_mod.CompatibilityResult,
    cfg: config_mod.VkBasaltConfig | None,
    config_path: str,
    backup_exists: bool,
    backup_path: str,
) -> str:
    return json.dumps(
        {
            "config_path": config_path,
            "effects": cfg.effects if cfg else None,
            "compatibility": {
                "status": result.status,
                "sober_runtime": result.sober_runtime,
                "installed_vkbasalt_branch": result.installed_vkbasalt_branch,
                "expected_vkbasalt_branch": result.expected_vkbasalt_branch,
                "fix_commands": result.fix_commands,
            },
            "backup": {"exists": backup_exists, "path": backup_path},
        },
        indent=2,
    )


# --- apply ---------------------------------------------------------------

def apply_success_human(effects: list[str]) -> str:
    return "\n".join([
        "Result: SUCCESS",
        f"Effects: {':'.join(effects)}",
        "Sober is running with the new config.",
    ])


def apply_failure_human(errors: list[str], restored: bool) -> str:
    lines = ["Result: FAILED"]
    if errors:
        lines.append("vkBasalt reported:")
        for e in errors:
            lines.append(f"  {e}")
    else:
        lines.append("vkBasalt did not report a config block (layer may not have loaded).")
    if restored:
        lines.append("Restored last-known-good.conf.")
    else:
        lines.append("WARNING: could not restore last-known-good.conf.")
    return "\n".join(lines)


def apply_json(success: bool, errors: list[str], restored: bool, effects: list[str]) -> str:
    return json.dumps(
        {
            "result": "success" if success else "failed",
            "effects": effects,
            "errors": errors,
            "restored": restored,
        },
        indent=2,
    )


# --- launch --------------------------------------------------------------

def launch_human(result) -> str:
    if result.mode == "detached":
        if result.success:
            return f"Launched (pid {result.pid})."
        return f"Launch failed (exit {result.exit_code})."
    if result.mode == "verify":
        if result.success:
            return "Verify: OK (vkBasalt loaded, no errors)."
        if result.vkbasalt_errors:
            return "Verify: FAILED — " + "; ".join(result.vkbasalt_errors)
        return "Verify: FAILED — vkBasalt did not load."
    # normal
    if result.success:
        return "Sober exited cleanly."
    return f"Sober exited with code {result.exit_code}."


def launch_json(result) -> str:
    return json.dumps(
        {
            "mode": result.mode,
            "success": result.success,
            "exit_code": result.exit_code,
            "pid": result.pid,
            "vkbasalt_loaded": result.vkbasalt_loaded,
            "vkbasalt_errors": result.vkbasalt_errors,
        },
        indent=2,
    )


# --- rollback ------------------------------------------------------------

def rollback_human(effects: list[str] | None) -> str:
    if effects is None:
        return "Rolled back to last-known-good.conf (unable to parse effects)."
    return f"Rolled back. Effects: {':'.join(effects)}"


def rollback_json(restored: bool, effects: list[str] | None) -> str:
    return json.dumps(
        {"restored": restored, "effects": effects},
        indent=2,
    )
