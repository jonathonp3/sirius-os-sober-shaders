from pathlib import Path

import pytest

from core import config


FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_valid():
    cfg = config.parse(FIXTURES / "vkbasalt-valid.conf")
    assert cfg.effects == ["dbd", "blm", "cas", "clr", "crv", "vib"]
    assert cfg.shader_paths["dbd"].endswith("Deband.fx")
    assert cfg.parameters["casSharpness"] == "0.30"
    assert cfg.parameters["Range"] == "16.0"
    assert cfg.reshade_texture_path is not None
    assert cfg.reshade_texture_path.startswith("/var/home/")
    assert cfg.depth_capture == "off"
    assert cfg.enable_on_launch is True


def test_parse_valid_has_no_errors():
    cfg = config.parse(FIXTURES / "vkbasalt-valid.conf")
    assert config.validate(cfg) == []


def test_parse_missing_effects_is_invalid():
    cfg = config.parse(FIXTURES / "vkbasalt-missing-effects.conf")
    errors = config.validate(cfg)
    assert any("effects" in e for e in errors)


def test_parse_host_paths_is_invalid():
    cfg = config.parse(FIXTURES / "vkbasalt-host-paths.conf")
    errors = config.validate(cfg)
    assert len(errors) >= 1
    # Every error should mention host paths and the fix.
    assert all("/var/home/" in e for e in errors)


def test_parse_orphan_effect_is_invalid():
    cfg = config.parse(FIXTURES / "vkbasalt-orphan-effect.conf")
    errors = config.validate(cfg)
    assert any("blm" in e for e in errors)


def test_roundtrip(tmp_path):
    original = config.parse(FIXTURES / "vkbasalt-valid.conf")
    out = tmp_path / "out.conf"
    config.write(original, out)
    reparsed = config.parse(out)
    assert reparsed.effects == original.effects
    assert reparsed.shader_paths == original.shader_paths
    assert reparsed.parameters == original.parameters
    assert reparsed.reshade_texture_path == original.reshade_texture_path
    assert reparsed.enable_on_launch == original.enable_on_launch


def test_backup_and_restore(tmp_path):
    active = tmp_path / "vkBasalt.conf"
    active.write_text("effects = cas\n")
    backup = tmp_path / "last-known-good.conf"

    config.backup(active, backup)
    assert backup.read_text() == "effects = cas\n"

    active.write_text("effects = dbd\n")
    config.restore(backup, active)
    assert active.read_text() == "effects = cas\n"


def test_backup_missing_active_raises(tmp_path):
    with pytest.raises(config.ConfigError):
        config.backup(tmp_path / "nope.conf", tmp_path / "backup.conf")


def test_restore_missing_backup_raises(tmp_path):
    with pytest.raises(config.ConfigError):
        config.restore(tmp_path / "nope.conf", tmp_path / "active.conf")
