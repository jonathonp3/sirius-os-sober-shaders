"""
core/fetch.py

Clone or update the GShade-Shaders repository into a user directory.

The app never redistributes shaders. This module only runs `git clone` and
`git pull` against the upstream repo, so the user's machine fetches from the
official source. That's the legal model for shaders whose per-file licenses
don't permit redistribution (see docs/SHADERS.md).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

GSHADE_REPO_URL = "https://github.com/Mortalitas/GShade-Shaders"


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

def fetch(
    dest: Path,
    *,
    update: bool = False,
    repo_url: str = GSHADE_REPO_URL,
    runner: Runner = _default_runner,
) -> FetchResult:
    """
    Clone GShade-Shaders into `dest`, or pull if it already exists.

    Behavior:
      - dest does not exist: git clone
      - dest exists and update=True: git pull
      - dest exists and update=False: report already-present
    """
    dest = dest.expanduser()

    if dest.exists():
        if not update:
            return FetchResult(
                action="already-present",
                dest=dest,
                success=True,
                message=(
                    f"{dest} already exists. Use --update to refresh."
                ),
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

    # Clone
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = _run([*_git_cmd(), "clone", "--depth=1", repo_url, str(dest)], runner)
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

    # Clone
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = _run([*_git_cmd(), "clone", "--depth=1", repo_url, str(dest)], runner)
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
