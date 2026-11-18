"""core/verify.py

Compile-verify imported shaders by launching Sober and reading vkBasalt's
log.

Dependency validation cannot see an unsupported ReShade intrinsic, so a
shader can have every file it needs and still fail to compile. This is the
only part of the import flow that runs Sober:

  batch — every candidate in one config. Fast, but an error that vkBasalt
          reports at a header path cannot be attributed to a shader.
  each  — one Sober launch per shader. Slower, but any error belongs to
          that shader.

The active config is backed up and restored; Sober is killed afterwards.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from core import config as config_mod
from core import launch as launch_mod


BATCH_TIMEOUT = 20.0
EACH_TIMEOUT = 8.0
SETTLE_AFTER_KILL = 2.0


@dataclass
class VerifyResult:
    """Shaders vkBasalt refused, and launches that gave no verdict."""

    broken: list[str] = field(default_factory=list)
    inconclusive: list[str] = field(default_factory=list)


def _config_for(names: list[str], shader_dir: Path, texture_dir: Path):
    cfg = config_mod.VkBasaltConfig()
    cfg.depth_capture = "on"
    cfg.reshade_texture_path = str(texture_dir)
    cfg.reshade_include_path = str(shader_dir)
    for name in names:
        alias = "".join(c for c in name.lower() if c.isalnum()) or "shader"
        cfg.effects.append(alias)
        cfg.shader_paths[alias] = str(shader_dir / f"{name}.fx")
    return cfg


def _kill_sober() -> None:
    launch_mod.kill_sober()
    time.sleep(SETTLE_AFTER_KILL)


def _capture(
    names: list[str],
    shader_dir: Path,
    texture_dir: Path,
    config_path: Path,
    timeout: float,
) -> tuple[bool, list[str]]:
    config_mod.write(_config_for(names, shader_dir, texture_dir), config_path)
    proc = subprocess.Popen(
        launch_mod._build_argv(verify=True),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Kill the app before draining: the sandboxed child inherits our
        # stdout pipe, so communicate() can block until it is gone.
        _kill_sober()
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out = b""
    text = out.decode("utf-8", errors="replace")
    return launch_mod._parse_vkbasalt_log(text)


def verify_shaders(
    names: list[str],
    *,
    shader_dir: Path,
    texture_dir: Path,
    config_path: Path,
    each: bool = False,
    batch_timeout: float = BATCH_TIMEOUT,
    each_timeout: float = EACH_TIMEOUT,
) -> VerifyResult:
    """Launch Sober and return the shaders vkBasalt refused to compile."""
    backup = config_path.with_name(config_path.name + ".import-verify-bak")
    had_active = config_path.exists()
    if had_active:
        shutil.copy2(config_path, backup)

    result = VerifyResult()
    groups = [[name] for name in names] if each else [list(names)]
    try:
        for group in groups:
            loaded, errors = _capture(
                group,
                shader_dir,
                texture_dir,
                config_path,
                each_timeout if each else batch_timeout,
            )
            if each:
                # One shader in the config, so any error is its.
                if errors:
                    result.broken.append(group[0])
                elif not loaded:
                    result.inconclusive.append(group[0])
                _kill_sober()
            else:
                for err in errors:
                    for name in names:
                        if name.lower() in err.lower():
                            result.broken.append(name)
    finally:
        if had_active:
            shutil.copy2(backup, config_path)
            backup.unlink(missing_ok=True)
        else:
            config_path.unlink(missing_ok=True)
        _kill_sober()

    seen: set[str] = set()
    result.broken = [
        b for b in result.broken if not (b in seen or seen.add(b))
    ]
    return result
