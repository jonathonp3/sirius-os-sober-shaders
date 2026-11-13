"""
gui/cli_bridge.py

The only module that runs `sober-shader` as a subprocess. Everything else
in the GUI works with the dataclasses returned here.

The CLI is invoked with --json (in the global-flags position, before the
subcommand) so the output is machine-readable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field


@dataclass
class CheckResult:
    ok: bool
    status: str
    sober_runtime: str | None = None
    installed_branch: str | None = None
    expected_branch: str | None = None
    fix_commands: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class ApplyResult:
    ok: bool
    effects: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    restored: bool = False


@dataclass
class LaunchResult:
    ok: bool
    pid: int | None = None
    exit_code: int | None = None


@dataclass
class FetchResult:
    ok: bool
    action: str          # "cloned" | "updated" | "already-present" | "failed"
    dest: str
    message: str = ""


def _run_cli(args: list[str], timeout: int = 600) -> tuple[int, str, str]:
    """Run the CLI as a subprocess. `args` is everything after the global flags.

    --json must come before the subcommand; argparse only accepts
    parent-parser flags in that position.
    """
    result = subprocess.run(
        [sys.executable, "-m", "cli", "--json", *args],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    return result.returncode, result.stdout, result.stderr


def check() -> CheckResult:
    code, out, err = _run_cli(["check"], timeout=30)
    if not out.strip():
        return CheckResult(
            ok=False, status="error",
            error=err.strip() or "check returned no output",
        )
    data = json.loads(out)
    return CheckResult(
        ok=(data["status"] == "ok"),
        status=data["status"],
        sober_runtime=data.get("sober_runtime"),
        installed_branch=data.get("installed_vkbasalt_branch"),
        expected_branch=data.get("expected_vkbasalt_branch"),
        fix_commands=data.get("fix_commands", []),
        error=data.get("error"),
    )


def apply_config(config_path: str) -> ApplyResult:
    code, out, err = _run_cli(["apply", config_path], timeout=86400)
    if not out.strip():
        return ApplyResult(ok=False, errors=[err.strip() or "apply returned no output"])
    data = json.loads(out)
    return ApplyResult(
        ok=(data["result"] == "success"),
        effects=data.get("effects", []),
        errors=data.get("errors", []),
        restored=data.get("restored", False),
    )


def launch(detach: bool = True) -> LaunchResult:
    args = ["launch"]
    if not detach:
        args.append("--no-detach")
    code, out, err = _run_cli(args, timeout=600)
    if not out.strip():
        return LaunchResult(ok=False)
    data = json.loads(out)
    return LaunchResult(
        ok=data["success"],
        pid=data.get("pid"),
        exit_code=data.get("exit_code"),
    )


def rollback() -> bool:
    code, out, err = _run_cli(["rollback"], timeout=30)
    return code == 0


def fetch_shaders(dest: str | None = None, update: bool = False) -> FetchResult:
    """
    Clone or update the GShade-Shaders repository into the user's home.

    This is a guided fetch, not an install: the user's machine downloads
    from the upstream repo, so the app never redistributes shaders whose
    licenses don't permit it.
    """
    args = ["fetch-shaders"]
    if dest:
        args += ["--dest", dest]
    if update:
        args.append("--update")
    code, out, err = _run_cli(args, timeout=600)
    if not out.strip():
        return FetchResult(
            ok=False,
            action="failed",
            dest=dest or "",
            message=err.strip() or "fetch-shaders returned no output",
        )
    data = json.loads(out)
    return FetchResult(
        ok=data["success"],
        action=data["action"],
        dest=data["dest"],
        message=data.get("message", ""),
    )
