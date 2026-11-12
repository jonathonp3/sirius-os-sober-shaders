import subprocess
from pathlib import Path

import pytest

from core import fetch


def _fake_runner(outputs: dict[tuple[str, ...], tuple[int, str, str]]):
    def runner(args):
        key = tuple(args)
        if key not in outputs:
            raise AssertionError(f"Unexpected command: {args}")
        code, stdout, stderr = outputs[key]
        return subprocess.CompletedProcess(args, code, stdout, stderr)
    return runner


def test_fetch_clone_success(tmp_path):
    dest = tmp_path / "GShade-Shaders"
    runner = _fake_runner({
        ("git", "clone", "--depth=1", fetch.GSHADE_REPO_URL, str(dest)):
            (0, "Cloning into...\n", ""),
    })
    result = fetch.fetch(dest, runner=runner)
    assert result.success is True
    assert result.action == "cloned"


def test_fetch_already_present_without_update(tmp_path):
    dest = tmp_path / "GShade-Shaders"
    dest.mkdir()
    result = fetch.fetch(
        dest,
        update=False,
        runner=lambda a: (_ for _ in ()).throw(
            AssertionError("should not call runner")
        ),
    )
    assert result.success is True
    assert result.action == "already-present"


def test_fetch_update_success(tmp_path):
    dest = tmp_path / "GShade-Shaders"
    dest.mkdir()
    runner = _fake_runner({
        ("git", "-C", str(dest), "pull", "--ff-only"):
            (0, "Already up to date.\n", ""),
    })
    result = fetch.fetch(dest, update=True, runner=runner)
    assert result.success is True
    assert result.action == "updated"


def test_fetch_clone_failure(tmp_path):
    dest = tmp_path / "GShade-Shaders"
    runner = _fake_runner({
        ("git", "clone", "--depth=1", fetch.GSHADE_REPO_URL, str(dest)):
            (128, "", "fatal: repository not found\n"),
    })
    result = fetch.fetch(dest, runner=runner)
    assert result.success is False
    assert result.action == "failed"
    assert "not found" in result.message


def test_fetch_git_missing_raises(tmp_path):
    def runner(args):
        raise FileNotFoundError()
    with pytest.raises(fetch.FetchError):
        fetch.fetch(tmp_path / "x", runner=runner)
