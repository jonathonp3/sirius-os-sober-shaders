import subprocess
from pathlib import Path

import pytest

from core import launch


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_runner(stdout: str, stderr: str = "", code: int = 0):
    def runner(args):
        return subprocess.CompletedProcess(args, code, stdout, stderr)
    return runner


def test_parse_success_log():
    text = (FIXTURES / "vkbasalt-log-success.txt").read_text()
    loaded, errors = launch._parse_vkbasalt_log(text)
    assert loaded is True
    assert errors == []


def test_parse_compile_error_log():
    text = (FIXTURES / "vkbasalt-log-compile-error.txt").read_text()
    loaded, errors = launch._parse_vkbasalt_log(text)
    assert loaded is True
    assert len(errors) == 1
    assert "BUFFER_COLOR_BIT_DEPTH" in errors[0]


def test_parse_missing_texture_log():
    text = (FIXTURES / "vkbasalt-log-missing-texture.txt").read_text()
    loaded, errors = launch._parse_vkbasalt_log(text)
    assert loaded is True
    assert len(errors) == 1
    assert "LensDBA.png" in errors[0]


def test_parse_no_layer_log():
    text = (FIXTURES / "vkbasalt-log-no-layer.txt").read_text()
    loaded, errors = launch._parse_vkbasalt_log(text)
    assert loaded is False
    assert errors == []


def test_verify_success():
    log = (FIXTURES / "vkbasalt-log-success.txt").read_text()
    result = launch.launch(verify=True, runner=_fixture_runner(log))
    assert result.mode == "verify"
    assert result.success is True
    assert result.vkbasalt_loaded is True
    assert result.vkbasalt_errors == []


def test_verify_compile_error():
    log = (FIXTURES / "vkbasalt-log-compile-error.txt").read_text()
    result = launch.launch(verify=True, runner=_fixture_runner(log))
    assert result.mode == "verify"
    assert result.success is False
    assert result.vkbasalt_loaded is True
    assert len(result.vkbasalt_errors) == 1


def test_verify_no_layer():
    log = (FIXTURES / "vkbasalt-log-no-layer.txt").read_text()
    result = launch.launch(verify=True, runner=_fixture_runner(log))
    assert result.success is False
    assert result.vkbasalt_loaded is False


def test_verify_nonzero_exit_fails():
    log = (FIXTURES / "vkbasalt-log-success.txt").read_text()
    result = launch.launch(
        verify=True, runner=_fixture_runner(log, code=1)
    )
    assert result.success is False


def test_launch_normal_success():
    result = launch.launch(runner=_fixture_runner("", code=0))
    assert result.mode == "normal"
    assert result.success is True
    assert result.exit_code == 0


def test_launch_normal_failure():
    result = launch.launch(runner=_fixture_runner("boom", code=1))
    assert result.mode == "normal"
    assert result.success is False
    assert result.exit_code == 1


def test_launch_missing_flatpak_raises():
    def runner(args):
        raise FileNotFoundError()
    with pytest.raises(launch.LaunchError):
        launch.launch(runner=runner)


class _FakeProc:
    def __init__(self, pid: int, exit_code: int | None):
        self.pid = pid
        self._exit_code = exit_code

    def poll(self):
        return self._exit_code


def test_launch_detached_running():
    def detached_runner(args):
        return _FakeProc(pid=12345, exit_code=None)

    result = launch.launch(
        detach=True, detached_runner=detached_runner, settle_seconds=0
    )
    assert result.mode == "detached"
    assert result.success is True
    assert result.pid == 12345
    assert result.exit_code is None


def test_launch_detached_immediate_failure():
    def detached_runner(args):
        return _FakeProc(pid=9999, exit_code=1)

    result = launch.launch(
        detach=True, detached_runner=detached_runner, settle_seconds=0
    )
    assert result.mode == "detached"
    assert result.success is False
    assert result.exit_code == 1
