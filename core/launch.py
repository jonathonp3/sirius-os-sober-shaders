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

# A checker that returns True if Sober is currently running. Tests pass a
# lambda so the result is deterministic; production uses _sober_running.
RunningChecker = Callable[[], bool]


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
    
def _default_popen_runner(args: list[str]) -> "subprocess.Popen[bytes]":
    return subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
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


def _sober_running() -> bool:
    """
    Return True if Sober is currently running (best-effort).

    Sober doesn't support multiple instances; launching a second one while
    the first is running crashes. If `flatpak ps` fails (timeout, flatpak
    missing), returns False and the launch proceeds rather than blocking.
    """
    cmd = _flatpak_cmd() + ["ps", "--columns=application"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (subprocess.SubprocessError, FileNotFoundError):
        return False
    return SOBER_APP_ID in result.stdout


def _default_running_checker() -> bool:
    return _sober_running()


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
    verify_then_detach: bool = False,
    runner: Runner = _default_runner,
    detached_runner: DetachedRunner = _default_detached_runner,
    running_checker: RunningChecker = _default_running_checker,
    settle_seconds: float = 1.5,
) -> LaunchResult:
    """
    ...
    Three modes are mutually exclusive:
      normal            — block until Sober exits, return its exit code.
      detached          — spawn and return immediately.
      verify            — block until Sober exits, parse vkBasalt's output.
      verify_then_detach — spawn, capture until vkBasalt reports, then
                          leave the process running and return.

    If verify_then_detach is True, it wins over verify and detach.
    """
    if running_checker():
        raise LaunchError(
            "Sober is already running. Close it before launching a new instance."
        )
    if verify_then_detach:
        return _launch_verify_then_detach(_default_popen_runner)
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

def _launch_verify_then_detach(
    runner: "subprocess.Popen[bytes]",
    timeout: float = 20.0,
) -> LaunchResult:
    """
    Launch Sober, capture output until vkBasalt reports its config (or an
    error), then release the process so it keeps running.

    The process is left in a running state; this function returns as soon
    as the vkBasalt config block is seen (success) or an error/crash is
    detected. Unlike _launch_verify, it does not wait for Sober to exit.

    If the timeout elapses with no signal, the process is killed and the
    result is a timeout failure — better than leaving a half-launched
    Sober for the user to find.

    The caller is responsible for the returned LaunchResult; the process
    is not managed after this function returns.
    """
    import time

    args = _build_argv(verify=True)
    try:
        proc = runner(args)
    except FileNotFoundError as e:
        raise LaunchError(f"`{args[0]}` not found in PATH") from e

    # Read stdout line by line until we see the vkBasalt config block or an
    # error, or the process exits, or the timeout hits.
    captured: list[str] = []
    loaded = False
    errors: list[str] = []
    deadline = time.monotonic() + timeout
    exit_code: int | None = None

    # Popen with stdout=PIPE gives us a readable stream. Read line by line
    # so we can react as soon as the config block appears.
    assert proc.stdout is not None
    while True:
        if time.monotonic() > deadline:
            break
        line = proc.stdout.readline()
        if not line:
            # Process closed stdout or exited.
            exit_code = proc.poll()
            break
        text = line.decode("utf-8", errors="replace").rstrip()
        captured.append(text)
        if text.startswith("vkBasalt info:  config file:"):
            loaded = True
        if text.startswith("vkBasalt err:"):
            errors.append(text[len("vkBasalt err:"):].strip())

        # Once we've seen the config block and any early errors, we can
        # stop capturing. vkBasalt reports the config synchronously during
        # init, so a few hundred ms is enough.
        if loaded:
            break

    if not loaded and not errors:
        # Either timeout or process exited without a signal.
        if exit_code is None:
            # Timeout. Kill the process so we don't leave an unmanaged
            # Sober running with no window.
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return LaunchResult(
                mode="verify-then-detach",
                success=False,
                exit_code=None,
                pid=proc.pid,
                log_excerpt=_excerpt("\n".join(captured)),
            )
        # Process exited before vkBasalt reported — probably a crash.
        return LaunchResult(
            mode="verify-then-detach",
            success=False,
            exit_code=exit_code,
            pid=proc.pid,
            log_excerpt=_excerpt("\n".join(captured)),
        )

    if errors:
        # vkBasalt reported an error. The process is probably crashing;
        # kill it so the caller's rollback runs cleanly and the user
        # doesn't see a half-broken Sober.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return LaunchResult(
            mode="verify-then-detach",
            success=False,
            exit_code=None,
            pid=proc.pid,
            vkbasalt_loaded=loaded,
            vkbasalt_errors=errors,
            log_excerpt=_excerpt("\n".join(captured)),
        )

    # Success. The process keeps running; we return without managing it.
    # Close our read end of the pipe so the process doesn't block on a
    # full stdout buffer when the GUI exits.
    try:
        proc.stdout.close()
    except Exception:
        pass

    return LaunchResult(
        mode="verify-then-detach",
        success=True,
        exit_code=None,
        pid=proc.pid,
        vkbasalt_loaded=True,
        vkbasalt_errors=[],
        log_excerpt=_excerpt("\n".join(captured)),
    )
    
