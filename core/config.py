"""
core/config.py

Parse, validate, write, backup, and restore vkBasalt.conf files.

The parser is deliberately strict about paths: vkBasalt runs inside Sober's
Flatpak sandbox, where the user's home is /var/home/<user>, not
/home/<user>. A config that works from a host shell will fail inside the
sandbox if it uses host paths. validate() rejects host paths with a clear
message rather than letting the user discover the failure at launch time.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

# Sandbox home prefix on Fedora ostree. Anything under /home/ is a host path
# and will not resolve inside Sober's Flatpak sandbox.
SANDBOX_HOME_PREFIX = "/var/home/"
HOST_HOME_PREFIX = "/home/"

# Keys that are not shader references or parameters but belong to the config.
KNOWN_KEYS = {
    "effects",
    "reshadeTexturePath",
    "reshadeIncludePath",
    "depthCapture",
    "enableOnLaunch",
    "toggleKey",  # present in default configs, but ineffective on Wayland
}


@dataclass
class VkBasaltConfig:
    effects: list[str] = field(default_factory=list)
    shader_paths: dict[str, str] = field(default_factory=dict)
    parameters: dict[str, str] = field(default_factory=dict)
    reshade_texture_path: str | None = None
    reshade_include_path: str | None = None
    depth_capture: str = "off"
    enable_on_launch: bool = True
    extras: dict[str, str] = field(default_factory=dict)


class ConfigError(Exception):
    """Raised on unrecoverable parse errors."""


def _is_sandbox_path(p: str) -> bool:
    """True if p is a path vkBasalt can see inside Sober's sandbox."""
    return p.startswith(SANDBOX_HOME_PREFIX)


def _is_host_path(p: str) -> bool:
    """True if p is a host path that will not resolve inside the sandbox."""
    return p.startswith(HOST_HOME_PREFIX)


def parse(path: Path) -> VkBasaltConfig:
    """
    Parse a vkBasalt.conf file into a VkBasaltConfig.

    The format is line-oriented `key = value`, with `effects = a:b:c` being
    the one key whose value is a colon-separated list. Comments start with
    `#` or `;`. Blank lines are ignored.
    """
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    config = VkBasaltConfig()
    seen_keys: set[str] = set()

    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if "=" not in line:
            raise ConfigError(
                f"{path}:{lineno}: expected `key = value`, got: {raw!r}"
            )
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        if key in seen_keys:
            raise ConfigError(
                f"{path}:{lineno}: duplicate key {key!r}"
            )
        seen_keys.add(key)

        if key == "effects":
            config.effects = [e for e in value.split(":") if e]
        elif key == "reshadeTexturePath":
            config.reshade_texture_path = value
        elif key == "reshadeIncludePath":
            config.reshade_include_path = value
        elif key == "depthCapture":
            config.depth_capture = value
        elif key == "enableOnLaunch":
            config.enable_on_launch = value.lower() in ("true", "1", "yes")
        elif key == "toggleKey":
            # Accepted for compatibility with default configs; ineffective
            # on Wayland. Not stored, not validated.
            pass
        elif value.endswith(".fx"):
            # A shader path: key is the short name used in `effects`.
            config.shader_paths[key] = value
        else:
            # Everything else is a shader parameter or an extra key.
            config.parameters[key] = value

    return config


def validate(config: VkBasaltConfig) -> list[str]:
    """
    Return a list of human-readable validation errors. An empty list means
    the config is valid.
    """
    errors: list[str] = []

    if not config.effects:
        errors.append(
            "`effects` is empty or missing. At least one effect is required."
        )

    # Every name in `effects` must be either a built-in or have a shader path.
    builtin_effects = {"cas", "dls", "fxaa", "smaa", "lut"}
    for name in config.effects:
        if name in builtin_effects:
            continue
        if name not in config.shader_paths:
            errors.append(
                f"Effect {name!r} is in `effects` but has no shader path "
                f"(expected `{name} = /path/to/something.fx`)."
            )
        else:
            path = config.shader_paths[name]
            if _is_host_path(path):
                errors.append(
                    f"Shader {name!r} uses a host path ({path}). "
                    f"Inside Sober's Flatpak sandbox, use "
                    f"{SANDBOX_HOME_PREFIX}<user>/... instead of "
                    f"{HOST_HOME_PREFIX}<user>/...."
                )
            elif not _is_sandbox_path(path):
                errors.append(
                    f"Shader {name!r} path is not under "
                    f"{SANDBOX_HOME_PREFIX} ({path}). vkBasalt may not be "
                    f"able to read it from inside Sober's sandbox."
                )

    # Sandbox-path enforcement for the two global paths.
    if config.reshade_texture_path and _is_host_path(config.reshade_texture_path):
        errors.append(
            f"`reshadeTexturePath` uses a host path "
            f"({config.reshade_texture_path}). Use "
            f"{SANDBOX_HOME_PREFIX}<user>/... instead."
        )
    if config.reshade_include_path and _is_host_path(config.reshade_include_path):
        errors.append(
            f"`reshadeIncludePath` uses a host path "
            f"({config.reshade_include_path}). Use "
            f"{SANDBOX_HOME_PREFIX}<user>/... instead."
        )

    return errors


def write(config: VkBasaltConfig, path: Path) -> None:
    """Serialize config to path, atomically (write to temp, then rename)."""
    lines: list[str] = []
    lines.append(f"effects = {':'.join(config.effects)}")
    lines.append("")

    for name in config.effects:
        if name in config.shader_paths:
            lines.append(f"{name} = {config.shader_paths[name]}")

    for key, value in config.parameters.items():
        lines.append(f"{key} = {value}")

    if config.reshade_texture_path:
        lines.append(f"reshadeTexturePath = {config.reshade_texture_path}")
    if config.reshade_include_path:
        lines.append(f"reshadeIncludePath = {config.reshade_include_path}")
    lines.append(f"depthCapture = {config.depth_capture}")
    lines.append(f"enableOnLaunch = {'True' if config.enable_on_launch else 'False'}")

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(path)


def backup(active: Path, backup: Path) -> None:
    """Copy active config to backup path. Overwrites any existing backup."""
    if not active.exists():
        raise ConfigError(f"Cannot back up: active config not found at {active}")
    shutil.copy2(active, backup)


def restore(backup: Path, active: Path) -> None:
    """Copy backup config back to active path. Overwrites active."""
    if not backup.exists():
        raise ConfigError(f"Cannot restore: backup not found at {backup}")
    shutil.copy2(backup, active)
