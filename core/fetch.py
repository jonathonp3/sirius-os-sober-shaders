"""
core/fetch.py

Clone or update the GShade-Shaders and reshade-shaders repositories into
user directories.

The app never redistributes shaders or framework headers. This module only
runs `git clone` and `git pull` against the upstream repos, so the user's
machine fetches from the official source. That's the legal model for files
whose licenses don't permit redistribution (see docs/SHADERS.md).

Two upstreams are fetched:

  - Mortalitas/GShade-Shaders — the GShade shader collection (per-file
    licenses; fetched at runtime, not bundled).

  - crosire/reshade-shaders — the ReShade framework headers
    (ReShade.fxh, ReShadeUI.fxh, Blending.fxh, Macros.fxh, etc.) under
    BSD 3-Clause. GShade shaders include these, but the GShade repo
    doesn't ship them; fetching them from the official source keeps the
    app from bundling ReShade code.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

GSHADE_REPO_URL = "https://github.com/Mortalitas/GShade-Shaders"
GSHADE_DEST = Path.home() / "GShade-Shaders"

RESHADE_REPO_URL = "https://github.com/crosire/reshade-shaders"
RESHADE_DEST = Path.home() / "reshade-shaders"


@dataclass
class FetchResult:
    action: str          # "cloned" | "updated" | "already-present" | "failed"
    dest: Path
    success: bool
    message: str = ""


class FetchError(Exception):
    """Raised when git is missing or the operation fails irrecoverably."""


Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _default_runner(args: list[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(args, capture_output=True, text=True, check=False)


def _run(args: list[str], runner: Runner) -> "subprocess.CompletedProcess[str]":
    try:
        return runner(args)
    except FileNotFoundError as e:
        raise FetchError(f"`{args[0]}` not found in PATH") from e


def _git_cmd() -> list[str]:
    """
    Return the base command for invoking git.

    Inside the Flatpak sandbox (SOBER_SHADER_HOST_FLATPAK=1), git isn't on
    PATH; it has to be reached via `flatpak-spawn --host`. On the host
    (dev, CLI, tests), the direct `git` command is used.
    """
    if os.environ.get("SOBER_SHADER_HOST_FLATPAK"):
        return ["flatpak-spawn", "--host", "git"]
    return ["git"]


def _fetch_one(
    dest: Path,
    repo_url: str,
    *,
    update: bool,
    runner: Runner,
) -> FetchResult:
    """
    Clone `repo_url` into `dest`, or pull if it already exists.

    This is the per-repo implementation. The public `fetch()` runs it
    once per upstream.
    """
    dest = dest.expanduser()

    if dest.exists():
        if not update:
            return FetchResult(
                action="already-present",
                dest=dest,
                success=True,
                message=f"{dest} already exists. Use --update to refresh.",
            )
        result = _run([*_git_cmd(), "-C", str(dest), "pull", "--ff-only"], runner)
        if result.returncode != 0:
            return FetchResult(
                action="failed",
                dest=dest,
                success=False,
                message=(result.stderr or result.stdout or "").strip(),
            )
        return FetchResult(
            action="updated",
            dest=dest,
            success=True,
            message=(result.stdout or "").strip(),
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    result = _run(
        [*_git_cmd(), "clone", "--depth=1", repo_url, str(dest)],
        runner,
    )
    if result.returncode != 0:
        return FetchResult(
            action="failed",
            dest=dest,
            success=False,
            message=(result.stderr or result.stdout or "").strip(),
        )
    return FetchResult(
        action="cloned",
        dest=dest,
        success=True,
        message=(result.stdout or "").strip(),
    )


def fetch(
    dest: Path | None = None,
    *,
    update: bool = False,
    repo_url: str | None = None,
    runner: Runner = _default_runner,
) -> FetchResult:
    """
    Fetch both upstream repos (GShade and ReShade framework).

    The public signature is preserved for backward compatibility with the
    CLI: passing `dest` and/or `repo_url` overrides the GShade destination
    and URL (the tests use this to redirect into a tmpdir). The ReShade
    clone always goes to RESHADE_DEST, because callers don't need to
    parameterize it.

    Returns the *GShade* result, but `success` is True only if both
    fetches succeeded; a failed ReShade clone fails the combined result
    because the framework headers are required for most GShade shaders to
    compile. The ReShade outcome is appended to `message`.
    """
    gshade_dest = (dest or GSHADE_DEST).expanduser()
    gshade_url = repo_url or GSHADE_REPO_URL

    gshade = _fetch_one(gshade_dest, gshade_url, update=update, runner=runner)
    reshade = _fetch_one(RESHADE_DEST, RESHADE_REPO_URL, update=update, runner=runner)

    combined_success = gshade.success and reshade.success
    combined_message = (
        f"GShade: {gshade.action} ({gshade.dest})"
        f"{' — ' + gshade.message if gshade.message else ''}\n"
        f"ReShade: {reshade.action} ({reshade.dest})"
        f"{' — ' + reshade.message if reshade.message else ''}"
    )

    # `action` reports the GShade action (what the GUI banner keys off),
    # unless GShade succeeded and ReShade failed — in which case the
    # overall operation failed and "failed" is the honest label.
    if not combined_success:
        action = "failed"
    else:
        action = gshade.action

    return FetchResult(
        action=action,
        dest=gshade_dest,
        success=combined_success,
        message=combined_message,
    )
