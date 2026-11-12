import subprocess
from pathlib import Path

import pytest

from core import runtime


FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_runner(outputs: dict[tuple[str, ...], tuple[int, str]]):
    """
    Build a Runner that returns canned output for specific argument tuples.
    Anything not in the dict raises AssertionError (so tests fail loudly on
    unexpected calls).
    """
    def runner(args):
        key = tuple(args)
        if key not in outputs:
            raise AssertionError(f"Unexpected command: {args}")
        code, stdout = outputs[key]
        return subprocess.CompletedProcess(args, code, stdout, "")
    return runner


def test_detect_sober_runtime_ok():
    runner = _fixture_runner({
        ("flatpak", "info", "--show-metadata", "org.vinegarhq.Sober"):
            (0, (FIXTURES / "sober-metadata.txt").read_text()),
    })
    assert runtime.detect_sober_runtime(runner) == "org.gnome.Platform//51"


def test_detect_sober_runtime_not_installed():
    runner = _fixture_runner({
        ("flatpak", "info", "--show-metadata", "org.vinegarhq.Sober"):
            (1, ""),
    })
    assert runtime.detect_sober_runtime(runner) is None


def test_detect_vulkanlayer_version_ok():
    runner = _fixture_runner({
        ("flatpak", "info", "--show-metadata", "org.gnome.Platform//51"):
            (0, (FIXTURES / "gnome-51-metadata.txt").read_text()),
    })
    assert runtime.detect_vulkanlayer_version("org.gnome.Platform//51", runner) == "26.08"


def test_detect_installed_vkbasalt_branches_ok():
    runner = _fixture_runner({
        ("flatpak", "list", "--columns=application,branch"):
            (0, (FIXTURES / "flatpak-list-vkbasalt-installed.txt").read_text()),
    })
def test_detect_installed_vkbasalt_branches_multiple():
    runner = _fixture_runner({
        ("flatpak", "list", "--columns=application,branch"):
            (0, (
                "org.freedesktop.Platform.VulkanLayer.vkBasalt 25.08\n"
                "org.freedesktop.Platform.VulkanLayer.vkBasalt 26.08\n"
                "org.vinegarhq.Sober                           stable\n"
            )),
    })
    assert runtime.detect_installed_vkbasalt_branches(runner) == ["25.08", "26.08"]


def test_check_compatibility_multiple_branches_expected_present():
    runner = _fixture_runner({
        ("flatpak", "info", "--show-metadata", "org.vinegarhq.Sober"):
            (0, (FIXTURES / "sober-metadata.txt").read_text()),
        ("flatpak", "info", "--show-metadata", "org.gnome.Platform//51"):
            (0, (FIXTURES / "gnome-51-metadata.txt").read_text()),
        ("flatpak", "list", "--columns=application,branch"):
            (0, (
                "org.freedesktop.Platform.VulkanLayer.vkBasalt 25.08\n"
                "org.freedesktop.Platform.VulkanLayer.vkBasalt 26.08\n"
            )),
    })
    result = runtime.check_compatibility(runner)
    assert result.status == "ok"
    assert result.installed_vkbasalt_branch == "26.08"


def test_check_compatibility_missing():
    runner = _fixture_runner({
        ("flatpak", "info", "--show-metadata", "org.vinegarhq.Sober"):
            (0, (FIXTURES / "sober-metadata.txt").read_text()),
        ("flatpak", "info", "--show-metadata", "org.gnome.Platform//51"):
            (0, (FIXTURES / "gnome-51-metadata.txt").read_text()),
        ("flatpak", "list", "--columns=application,branch"):
            (0, (FIXTURES / "flatpak-list-vkbasalt-missing.txt").read_text()),
    })
    result = runtime.check_compatibility(runner)
    assert result.status == "missing"
    assert result.fix_commands == [
        "flatpak install flathub org.freedesktop.Platform.VulkanLayer.vkBasalt//26.08"
    ]


def test_check_compatibility_no_sober():
    runner = _fixture_runner({
        ("flatpak", "info", "--show-metadata", "org.vinegarhq.Sober"):
            (1, ""),
    })
    result = runtime.check_compatibility(runner)
    assert result.status == "no_sober"
