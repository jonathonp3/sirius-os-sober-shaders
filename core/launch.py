"""
core/launch.py

Launch Sober with vkBasalt enabled, in one of three modes:

  normal   — flatpak run, inherit stdio, return Sober's exit code.
  detached — flatpak run, start_new_session=True, return immediately.
  verify   — flatpak run with VKBASALT_LOG_LEVEL=info, capture output,
             parse for the vkBasalt config block and any `vkBasalt err:`
             lines.

The Wayland limitation (no mid-session toggle) is a vkBasalt property, not
something this module works around. `enableOnLaunch = True` in the config
is what keeps the effect on for the whole session.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable

SOBER_APP_ID = "org.vinegarhq.Sober"


@dataclass
class LaunchResult:
    mode: str                      # "normal" | "detached" | "verify"
    success: bool
    exit_code: int | None = None
    pid: int | None = None
    vkbasalt_loaded: bool = False  # verify only
    vkbasalt_errors: list[str] = field(default_factory=list)  # verify only
    log_excerpt: str = ""


class LaunchError(Exception):
    """Raised when launching Sober fails in a way the caller must handle."""


# A runner for normal and verify modes: takes argv, returns CompletedProcess.
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]

# A runner for detached mode: takes argv, returns something with .pid and
# .poll(). Tests use a small fake; production uses subprocess.Popen.
DetachedRunner = Callable[[list[str]], "object"]


def _default_runner(args: list[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(args, capture_output=True, text=True, check=False)


def _default_detached_runner(args: list[str]) -> "object":
    return subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def _flatpak_cmd() -> list[str]:
    """
    Return the base command for invoking flatpak.

    Inside the Flatpak sandbox (SOBER_SHADER_HOST_FLATPAK=1), flatpak isn't
    on PATH; it has to be reached via `flatpak-spawn --host`. On the host
    (dev, CLI, tests), the direct `flatpak` command is used.
    """
    if os.environ.get("SOBER_SHADER_HOST_FLATPAK"):
        return ["flatpak-spawn", "--host", "flatpak"]
    return ["flatpak"]


def _build_argv(verify: bool) -> list[str]:
    """Build the flatpak argv for launching Sober with vkBasalt."""
    env_flags = ["--env=ENABLE_VKBASALT=1"]
    if verify:
        env_flags.append("--env=VKBASALT_LOG_LEVEL=info")
    return [*_flatpak_cmd(), "run", *env_flags, SOBER_APP_ID]


def _parse_vkbasalt_log(text: str) -> tuple[bool, list[str]]:
    """
    Return (loaded, errors).

    loaded is True if any `vkBasalt info: config file:` line is present,
    which is the signal that the layer initialized.

    errors is every line starting with `vkBasalt err:`.
    """
    loaded = bool(re.search(r"^vkBasalt info:\s+config file:", text, re.MULTILINE))
    errors = re.findall(r"^vkBasalt err:\s+(.*)$", text, re.MULTILINE)
    return loaded, errors


def _excerpt(text: str, lines: int = 30) -> str:
    """Last N non-empty lines, for error reports."""
    kept = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(kept[-lines:])


def launch(
    *,
    detach: bool = False,
    verify: bool = False,
    runner: Runner = _default_runner,
    detached_runner: DetachedRunner = _default_detached_runner,
    settle_seconds: float = 1.5,
) -> LaunchResult:
    """
    Launch Sober with vkBasalt enabled.

    Exactly one of `detach` or `verify` should be True; if both are False,
    it's a normal attached launch. If both are True, `verify` wins and
    `detach` is ignored (verify mode needs the output).

    `settle_seconds` is only used in detached mode: after Popen returns,
    wait this long and poll. If the process already exited, treat it as
    a launch failure.
    """
    if verify:
        return _launch_verify(runner)
    if detach:
        return _launch_detached(detached_runner, settle_seconds)
    return _launch_normal(runner)


def _launch_normal(runner: Runner) -> LaunchResult:
    args = _build_argv(verify=False)
    try:
        result = runner(args)
    except FileNotFoundError as e:
        raise LaunchError(f"`{args[0]}` not found in PATH") from e

    return LaunchResult(
        mode="normal",
        success=(result.returncode == 0),
        exit_code=result.returncode,
        log_excerpt=_excerpt(result.stderr or result.stdout or ""),
    )


def _launch_detached(detached_runner: DetachedRunner, settle_seconds: float) -> LaunchResult:
    import time

    args = _build_argv(verify=False)
    try:
        proc = detached_runner(args)
    except FileNotFoundError as e:
        raise LaunchError(f"`{args[0]}` not found in PATH") from e

    # Give the process a moment to fail if it's going to fail immediately
    # (e.g., Sober not installed, flatpak misconfigured).
    time.sleep(settle_seconds)
    exit_code = proc.poll()
    if exit_code is not None and exit_code != 0:
        return LaunchResult(
            mode="detached",
            success=False,
            exit_code=exit_code,
            pid=proc.pid,
        )

    return LaunchResult(
        mode="detached",
        success=True,
        exit_code=None,  # still running
        pid=proc.pid,
    )


def _launch_verify(runner: Runner) -> LaunchResult:
    args = _build_argv(verify=True)
    try:
        result = runner(args)
    except FileNotFoundError as e:
        raise LaunchError(f"`{args[0]}` not found in PATH") from e

    combined = (result.stdout or "") + (result.stderr or "")
    loaded, errors = _parse_vkbasalt_log(combined)

    success = (result.returncode == 0) and loaded and not errors
    return LaunchResult(
        mode="verify",
        success=success,
        exit_code=result.returncode,
        vkbasalt_loaded=loaded,
        vkbasalt_errors=errors,
        log_excerpt=_excerpt(combined),
    )
