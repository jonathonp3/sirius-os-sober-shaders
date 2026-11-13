"""
core/runtime.py

Detect Sober's Flatpak runtime and the installed vkBasalt extension branch,
and determine whether they are compatible.

Compatibility rule: the vkBasalt extension branch must match the
`org.freedesktop.Platform.VulkanLayer` extension point version declared by
Sober's runtime. For example, `org.gnome.Platform//51` declares
`VulkanLayer version = 26.08`, so the compatible vkBasalt extension is
`org.freedesktop.Platform.VulkanLayer.vkBasalt//26.08`.

This module has no GUI dependencies and is tested without Sober installed
by mocking the `run` function below.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable

SOBER_APP_ID = "org.vinegarhq.Sober"
VKBASALT_EXTENSION_ID = "org.freedesktop.Platform.VulkanLayer.vkBasalt"
VULKANLAYER_EXTENSION_ID = "org.freedesktop.Platform.VulkanLayer"


class DetectionError(Exception):
    """Raised when flatpak output cannot be parsed."""


@dataclass
class CompatibilityResult:
    status: str  # "ok" | "mismatch" | "missing" | "no_sober" | "error"
    sober_runtime: str | None = None          # e.g. "org.gnome.Platform//51"
    installed_vkbasalt_branch: str | None = None  # e.g. "26.08"
    expected_vkbasalt_branch: str | None = None   # e.g. "26.08"
    fix_commands: list[str] = field(default_factory=list)
    error: str | None = None


# A tiny wrapper so tests can inject a fake `flatpak` output.
Runner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _default_runner(args: list[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
    )


def _run(args: list[str], runner: Runner) -> "subprocess.CompletedProcess[str]":
    try:
        return runner(args)
    except FileNotFoundError as e:
        raise DetectionError(f"`{args[0]}` not found in PATH") from e


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


def detect_sober_runtime(runner: Runner = _default_runner) -> str | None:
    """
    Return Sober's runtime reference, e.g. "org.gnome.Platform//51".
    Return None if Sober is not installed.

    Uses `flatpak info --show-metadata`, which prints the [Application]
    section with `runtime=`.
    """
    result = _run(
        [*_flatpak_cmd(), "info", "--show-metadata", SOBER_APP_ID],
        runner,
    )
    if result.returncode != 0:
        # Non-zero means Sober isn't installed (or flatpak failed).
        return None

    # The [Application] section looks like:
    #   [Application]
    #   name=org.vinegarhq.Sober
    #   runtime=org.gnome.Platform/x86_64/51
    #   sdk=org.gnome.Sdk/x86_64/51
    #
    # Note: the [Application] section uses `key=value` (no spaces), unlike
    # extension blocks which use `key = value` (spaces). This is a real
    # quirk of flatpak's metadata format.
    m = re.search(r"^runtime=(\S+)/(?:x86_64|aarch64)/(\S+)$",
                  result.stdout, re.MULTILINE)
    if not m:
        raise DetectionError(
            "Could not parse `runtime=` from flatpak info output"
        )
    runtime_name, runtime_branch = m.group(1), m.group(2)
    return f"{runtime_name}//{runtime_branch}"


def detect_vulkanlayer_version(
    runtime_ref: str,
    runner: Runner = _default_runner,
) -> str | None:
    """
    Given a runtime ref like "org.gnome.Platform//51", return the version
    of the org.freedesktop.Platform.VulkanLayer extension point it declares,
    e.g. "26.08". Return None if the extension point is not declared.
    """
    if "//" not in runtime_ref:
        raise DetectionError(f"Invalid runtime ref: {runtime_ref!r}")
    runtime_name, runtime_branch = runtime_ref.split("//", 1)
    flatpak_ref = f"{runtime_name}//{runtime_branch}"

    result = _run(
        [*_flatpak_cmd(), "info", "--show-metadata", flatpak_ref],
        runner,
    )
    if result.returncode != 0:
        raise DetectionError(
            f"Could not read metadata for runtime {flatpak_ref}"
        )

    # Extension blocks look like:
    #   [Extension org.freedesktop.Platform.VulkanLayer]
    #   version = 26.08
    #   directory = lib/extensions/vulkan
    #   ...
    #
    # The blank line after the block is what terminates it. The capture
    # group below matches any line that does not start with `[`, including
    # blank lines, so it stops at the next `[Extension ...]` header.
    block = re.search(
        r"^\[Extension " + re.escape(VULKANLAYER_EXTENSION_ID) + r"\]\n"
        r"((?:^(?!\[).*\n)*)",
        result.stdout,
        re.MULTILINE,
    )
    if not block:
        return None

    # Extension blocks use `version = 26.08` with spaces around `=`.
    version_match = re.search(
        r"^version\s*=\s*(\S+)$", block.group(1), re.MULTILINE
    )
    if not version_match:
        return None
    return version_match.group(1)


def detect_installed_vkbasalt_branches(
    runner: Runner = _default_runner,
) -> list[str]:
    """
    Return all installed vkBasalt extension branches (e.g. ["25.08", "26.08"]).
    Empty list if none are installed.

    Uses `flatpak list --columns=application,branch`, then filters for the
    vkBasalt extension ID. Multiple branches can coexist when a previous
    version hasn't been cleaned up yet.
    """
    result = _run(
        [*_flatpak_cmd(), "list", "--columns=application,branch"],
        runner,
    )
    if result.returncode != 0:
        raise DetectionError("`flatpak list` failed")

    branches: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == VKBASALT_EXTENSION_ID:
            branches.append(parts[1])
    return branches


def check_compatibility(
    runner: Runner = _default_runner,
) -> CompatibilityResult:
    """
    Run all three detections and return a CompatibilityResult.
    """
    try:
        sober_runtime = detect_sober_runtime(runner)
    except DetectionError as e:
        return CompatibilityResult(status="error", error=str(e))

    if sober_runtime is None:
        return CompatibilityResult(status="no_sober")

    try:
        expected_branch = detect_vulkanlayer_version(sober_runtime, runner)
    except DetectionError as e:
        return CompatibilityResult(
            status="error",
            sober_runtime=sober_runtime,
            error=str(e),
        )

    try:
        installed_branches = detect_installed_vkbasalt_branches(runner)
    except DetectionError as e:
        return CompatibilityResult(
            status="error",
            sober_runtime=sober_runtime,
            expected_vkbasalt_branch=expected_branch,
            error=str(e),
        )

    if expected_branch is None:
        # Runtime doesn't declare a VulkanLayer extension point.
        return CompatibilityResult(
            status="error",
            sober_runtime=sober_runtime,
            installed_vkbasalt_branch=(
                installed_branches[0] if installed_branches else None
            ),
            error=(
                f"Runtime {sober_runtime} does not declare a "
                f"{VULKANLAYER_EXTENSION_ID} extension point. "
                f"This usually means the runtime is incomplete or out of "
                f"date. Try: flatpak repair, or reinstall {sober_runtime}."
            ),
        )

    # If the expected branch is among the installed ones, we're good.
    # Extra branches may exist (stale versions); they don't hurt.
    if expected_branch in installed_branches:
        return CompatibilityResult(
            status="ok",
            sober_runtime=sober_runtime,
            installed_vkbasalt_branch=expected_branch,
            expected_vkbasalt_branch=expected_branch,
        )

    if not installed_branches:
        return CompatibilityResult(
            status="missing",
            sober_runtime=sober_runtime,
            expected_vkbasalt_branch=expected_branch,
            fix_commands=[
                f"flatpak install flathub {VKBASALT_EXTENSION_ID}//{expected_branch}"
            ],
        )

    # Some vkBasalt branch is installed, but not the expected one.
    first_installed = installed_branches[0]
    fix = [
        f"flatpak install flathub {VKBASALT_EXTENSION_ID}//{expected_branch}",
    ]
    for b in installed_branches:
        if b != expected_branch:
            fix.append(f"flatpak uninstall {VKBASALT_EXTENSION_ID}//{b}")

    return CompatibilityResult(
        status="mismatch",
        sober_runtime=sober_runtime,
        installed_vkbasalt_branch=first_installed,
        expected_vkbasalt_branch=expected_branch,
        fix_commands=fix,
    )
