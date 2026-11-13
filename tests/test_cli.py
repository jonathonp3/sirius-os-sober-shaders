from pathlib import Path

import pytest

from cli import main as cli_main
from core import config as config_mod
from core import launch as launch_mod
from core import runtime as runtime_mod


FIXTURES = Path(__file__).parent / "fixtures"


def _ok_compat():
    return runtime_mod.CompatibilityResult(
        status="ok",
        sober_runtime="org.gnome.Platform//51",
        installed_vkbasalt_branch="26.08",
        expected_vkbasalt_branch="26.08",
    )


def _mismatch_compat():
    return runtime_mod.CompatibilityResult(
        status="mismatch",
        sober_runtime="org.gnome.Platform//51",
        installed_vkbasalt_branch="25.08",
        expected_vkbasalt_branch="26.08",
        fix_commands=[
            "flatpak install flathub org.freedesktop.Platform.VulkanLayer.vkBasalt//26.08",
            "flatpak uninstall org.freedesktop.Platform.VulkanLayer.vkBasalt//25.08",
        ],
    )


# --- check ---------------------------------------------------------------

def test_check_ok(monkeypatch, capsys):
    monkeypatch.setattr(runtime_mod, "check_compatibility", _ok_compat)
    code = cli_main.main(["check"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Status:             OK" in out
    assert "org.gnome.Platform//51" in out


def test_check_mismatch(monkeypatch, capsys):
    monkeypatch.setattr(runtime_mod, "check_compatibility", _mismatch_compat)
    code = cli_main.main(["check"])
    assert code == 1
    out = capsys.readouterr().out
    assert "MISMATCH" in out
    assert "flatpak install" in out


def test_check_json(monkeypatch, capsys):
    monkeypatch.setattr(runtime_mod, "check_compatibility", _ok_compat)
    code = cli_main.main(["--json", "check"])
    assert code == 0
    out = capsys.readouterr().out
    assert '"status": "ok"' in out


# --- status --------------------------------------------------------------

def test_status_with_valid_config(monkeypatch, capsys, tmp_path):
    active = tmp_path / "vkBasalt.conf"
    active.write_text((FIXTURES / "vkbasalt-valid.conf").read_text())
    monkeypatch.setattr(runtime_mod, "check_compatibility", _ok_compat)

    code = cli_main.main(["--config", str(active), "status"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Compatibility:    ok" in out
    assert "dbd:blm:cas:clr:crv:vib" in out
    assert "Last-known-good:  no" in out


def test_status_missing_config(monkeypatch, capsys, tmp_path):
    active = tmp_path / "nope.conf"
    monkeypatch.setattr(runtime_mod, "check_compatibility", _ok_compat)
    code = cli_main.main(["--config", str(active), "status"])
    assert code == 0
    out = capsys.readouterr().out
    assert "(config missing or unreadable)" in out


# --- apply ---------------------------------------------------------------

def test_apply_invalid_config(capsys, tmp_path):
    active = tmp_path / "vkBasalt.conf"
    active.write_text("effects = cas\n")
    # missing-effects.conf has no `effects` key; validation should fail.
    bad = FIXTURES / "vkbasalt-missing-effects.conf"

    code = cli_main.main(["--config", str(active), "apply", str(bad)])
    assert code == 5
    err = capsys.readouterr().err
    assert "validation failed" in err.lower() or "invalid config" in err.lower()


def test_apply_success(monkeypatch, capsys, tmp_path):
    active = tmp_path / "vkBasalt.conf"
    active.write_text("effects = cas\n")
    new = FIXTURES / "vkbasalt-valid.conf"

    def fake_launch(*, verify=False, **kw):
        return launch_mod.LaunchResult(
            mode="verify", success=True, exit_code=0,
            vkbasalt_loaded=True, vkbasalt_errors=[],
        )

    monkeypatch.setattr(launch_mod, "launch", fake_launch)
    code = cli_main.main(["--config", str(active), "apply", str(new)])
    assert code == 0
    out = capsys.readouterr().out
    assert "SUCCESS" in out
    # Active config should now be the new one.
    assert "dbd:blm:cas:clr:crv:vib" in active.read_text()


def test_apply_failure_restores(monkeypatch, capsys, tmp_path):
    active = tmp_path / "vkBasalt.conf"
    original = "effects = cas\n"
    active.write_text(original)
    new = FIXTURES / "vkbasalt-valid.conf"

    def fake_launch(*, verify=False, **kw):
        return launch_mod.LaunchResult(
            mode="verify", success=False, exit_code=1,
            vkbasalt_loaded=True,
            vkbasalt_errors=["error X3004: undeclared identifier 'X'"],
        )

    monkeypatch.setattr(launch_mod, "launch", fake_launch)
    code = cli_main.main(["--config", str(active), "apply", str(new)])
    assert code == 6
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "Restored last-known-good.conf" in out
    # Active config must be restored to the original.
    assert active.read_text() == original


def test_apply_no_active_config_success(monkeypatch, capsys, tmp_path):
    # No prior config exists; apply should write the new one and not try
    # to back up or restore anything.
    active = tmp_path / "vkBasalt.conf"
    new = FIXTURES / "vkbasalt-valid.conf"

    def fake_launch(*, verify=False, **kw):
        return launch_mod.LaunchResult(
            mode="verify", success=True, exit_code=0,
            vkbasalt_loaded=True, vkbasalt_errors=[],
        )

    monkeypatch.setattr(launch_mod, "launch", fake_launch)
    code = cli_main.main(["--config", str(active), "apply", str(new)])
    assert code == 0
    assert active.exists()


# --- launch --------------------------------------------------------------

def test_launch_missing_config(capsys, tmp_path):
    active = tmp_path / "nope.conf"
    code = cli_main.main(["--config", str(active), "launch"])
    assert code == 5
    err = capsys.readouterr().err
    assert "Config not found" in err


def test_launch_success(monkeypatch, capsys, tmp_path):
    active = tmp_path / "vkBasalt.conf"
    active.write_text("effects = cas\n")

    def fake_launch(*, detach=False, verify=False, **kw):
        return launch_mod.LaunchResult(
            mode="detached" if detach else "normal",
            success=True, exit_code=None, pid=42,
        )

    monkeypatch.setattr(launch_mod, "launch", fake_launch)
    code = cli_main.main(["--config", str(active), "launch"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Launched" in out


def test_launch_verify_failure(monkeypatch, capsys, tmp_path):
    active = tmp_path / "vkBasalt.conf"
    active.write_text("effects = cas\n")

    def fake_launch(*, detach=False, verify=False, **kw):
        return launch_mod.LaunchResult(
            mode="verify", success=False, exit_code=1,
            vkbasalt_loaded=False, vkbasalt_errors=[],
        )

    monkeypatch.setattr(launch_mod, "launch", fake_launch)
    code = cli_main.main(["--config", str(active), "launch", "--verify"])
    assert code == 9


# --- rollback ------------------------------------------------------------

def test_rollback_no_backup(capsys, tmp_path):
    active = tmp_path / "vkBasalt.conf"
    code = cli_main.main(["--config", str(active), "rollback"])
    assert code == 10
    err = capsys.readouterr().err
    assert "No backup" in err


def test_rollback_restores(monkeypatch, capsys, tmp_path):
    active = tmp_path / "vkBasalt.conf"
    backup = tmp_path / "last-known-good.conf"
    active.write_text("effects = dbd\n")
    backup.write_text("effects = cas\n")

    code = cli_main.main(["--config", str(active), "rollback"])
    assert code == 0
    assert active.read_text() == "effects = cas\n"
    out = capsys.readouterr().out
    assert "Rolled back" in out

def test_list_shaders_human(capsys, tmp_path):
    (tmp_path / "A.fx").write_text(
        'uniform float x < ui_type = "slider"; ui_min = 0.0; ui_max = 1.0; > = 0.5;\n'
    )
    (tmp_path / "B.fx").write_text('uniform int y = 2;\n')

    code = cli_main.main(["list-shaders", "--shader-dir", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "A.fx" in out
    assert "x = 0.5" in out
    assert "B.fx" in out


def test_list_shaders_empty(capsys, tmp_path):
    code = cli_main.main(["list-shaders", "--shader-dir", str(tmp_path)])
    assert code == 0
    err = capsys.readouterr().err
    assert "No .fx files found" in err


def test_list_shaders_json(capsys, tmp_path):
    (tmp_path / "A.fx").write_text("uniform float x = 0.5;\n")
    code = cli_main.main(
        ["--json", "list-shaders", "--shader-dir", str(tmp_path)]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert '"name": "A"' in out


def test_fetch_shaders_already_present(capsys, tmp_path, monkeypatch):
    from core import fetch as fetch_mod

    def fake_fetch(dest, *, update=False, **kw):
        return fetch_mod.FetchResult(
            action="already-present", dest=dest, success=True,
            message=f"{dest} already exists. Use --update to refresh.",
        )

    monkeypatch.setattr(fetch_mod, "fetch", fake_fetch)
    code = cli_main.main(["fetch-shaders", "--dest", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "already exists" in out


def test_fetch_shaders_clone_success(capsys, tmp_path, monkeypatch):
    from core import fetch as fetch_mod

    def fake_fetch(dest, *, update=False, **kw):
        return fetch_mod.FetchResult(
            action="cloned", dest=dest, success=True, message="",
        )

    monkeypatch.setattr(fetch_mod, "fetch", fake_fetch)
    code = cli_main.main(["fetch-shaders", "--dest", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Cloned GShade-Shaders" in out


def test_fetch_shaders_git_missing(capsys, tmp_path, monkeypatch):
    from core import fetch as fetch_mod

    def fake_fetch(dest, **kw):
        raise fetch_mod.FetchError("`git` not found in PATH")

    monkeypatch.setattr(fetch_mod, "fetch", fake_fetch)
    code = cli_main.main(["fetch-shaders", "--dest", str(tmp_path)])
    assert code == 11    
    
def test_validate_config_ok(capsys, tmp_path):
    """A config pointing at the shipped Deband.fx validates cleanly."""
    repo_shaders = Path(__file__).parent.parent / "shaders"
    deband = repo_shaders / "Deband.fx"
    if not deband.exists():
        pytest.skip("shipped Deband.fx not present")

    cfg = tmp_path / "test.conf"
    cfg.write_text(
        f"effects = dbd\n"
        f"dbd = {deband}\n"
        f"Range = 16.0\n"
        f"reshadeIncludePath = {repo_shaders}\n"
    )
    code = cli_main.main(
        ["validate-config", str(cfg), "--shader-dir", str(repo_shaders)]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "OK" in out


def test_validate_config_unknown_parameter(capsys, tmp_path):
    shader_dir = tmp_path / "Shaders"
    shader_dir.mkdir()
    (shader_dir / "Deband.fx").write_text("uniform float Range = 16.0;\n")
    cfg = tmp_path / "test.conf"
    cfg.write_text(
        f"effects = dbd\n"
        f"dbd = {shader_dir}/Deband.fx\n"
        f"range = 16.0\n"
        f"reshadeIncludePath = {shader_dir}\n"
    )
    code = cli_main.main(
        ["validate-config", str(cfg), "--shader-dir", str(shader_dir)]
    )
    assert code == 13
    err = capsys.readouterr().err
    assert "Unknown parameter: range" in err
    assert "did you mean Range?" in err    

def test_validate_config_rejects_host_path(capsys, tmp_path):
    """validate-config rejects paths outside /var/home/, matching the sandbox rule."""
    shader_dir = tmp_path / "Shaders"
    shader_dir.mkdir()
    (shader_dir / "Deband.fx").write_text("uniform float Range = 16.0;\n")
    cfg = tmp_path / "test.conf"
    cfg.write_text(
        f"effects = dbd\n"
        f"dbd = {shader_dir}/Deband.fx\n"
        f"Range = 16.0\n"
        f"reshadeIncludePath = {shader_dir}\n"
    )
    code = cli_main.main(
        ["validate-config", str(cfg), "--shader-dir", str(shader_dir)]
    )
    assert code == 13
    err = capsys.readouterr().err
    assert "not under /var/home/" in err    
