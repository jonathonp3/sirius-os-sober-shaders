from pathlib import Path

from core import importing, shader


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _texture(path: Path) -> Path:
    # Content is irrelevant; only existence + relative path matter.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return path


# --- discover_assets ------------------------------------------------------

def test_discover_assets_flat_layout(tmp_path):
    root = tmp_path / "qUINT"
    _write(root / "Shaders" / "qUINT_ssr.fx", "// x\n")
    _write(root / "Shaders" / "qUINT_common.fxh", "// h\n")

    shaders, includes, textures = importing.discover_assets(root)

    assert [p.name for p in shaders] == ["qUINT_ssr.fx"]
    assert includes == [(root / "Shaders" / "qUINT_common.fxh", "qUINT_common.fxh")]
    assert textures == []


def test_discover_assets_includes_and_textures_layout(tmp_path):
    root = tmp_path / "Reshade-Shaders"
    _write(root / "Shaders" / "BaBa_SSR.fx", "// x\n")
    _write(root / "Shaders" / "Includes" / "bb_common.fxh", "// h\n")
    _texture(root / "Textures" / "Barbatos" / "SS_BN2.png")

    shaders, includes, textures = importing.discover_assets(root)

    assert [p.name for p in shaders] == ["BaBa_SSR.fx"]
    assert includes == [
        (root / "Shaders" / "Includes" / "bb_common.fxh", "Includes/bb_common.fxh")
    ]
    assert textures == [
        (root / "Textures" / "Barbatos" / "SS_BN2.png", "Barbatos/SS_BN2.png")
    ]


def test_discover_assets_no_shaders_dir(tmp_path):
    root = tmp_path / "flat"
    _write(root / "A.fx", "// x\n")
    _write(root / "B.fx", "// y\n")
    _write(root / "C.fxh", "// h\n")

    shaders, includes, textures = importing.discover_assets(root)

    assert sorted(p.name for p in shaders) == ["A.fx", "B.fx"]
    assert includes == [(root / "C.fxh", "C.fxh")]


# --- build_available ------------------------------------------------------

def test_build_available_flattens_and_seeds(tmp_path):
    sandbox = tmp_path / "Shaders"
    _write(sandbox / "ReShade.fxh", "// framework\n")

    includes = [(tmp_path / "src" / "Includes" / "bb.fxh", "Includes/bb.fxh")]
    include_src, _, inc_collisions, _ = importing.build_available(
        includes, [], shader_dir=sandbox, texture_dir=tmp_path / "Textures",
    )

    # Source header reachable by both its subpath and its flat basename.
    assert "includes/bb.fxh" in include_src
    assert "bb.fxh" in include_src
    # Existing sandbox file seeded.
    assert "reshade.fxh" in include_src
    assert inc_collisions == []


# --- decide (qUINT-style flat layout) -------------------------------------

def test_decide_quint_flat_all_imported(tmp_path):
    root = tmp_path / "qUINT"
    _write(root / "Shaders" / "qUINT_ssr.fx", '#include "qUINT_common.fxh"\n')
    _write(root / "Shaders" / "qUINT_mxao.fx", '#include "qUINT_common.fxh"\n')
    _write(root / "Shaders" / "qUINT_common.fxh", "// shared header\n")

    decisions = importing.decide(root, tmp_path / "Shaders", tmp_path / "Textures")
    by_name = {d.name: d for d in decisions}

    assert sorted(by_name) == ["qUINT_mxao", "qUINT_ssr"]
    assert all(d.status == "imported" for d in decisions)
    assert by_name["qUINT_ssr"].resolved_includes == ["qUINT_common.fxh"]


def test_decide_rejects_missing_include(tmp_path):
    root = tmp_path / "qUINT"
    _write(root / "Shaders" / "qUINT_ssr.fx", '#include "missing.fxh"\n')
    _write(root / "Shaders" / "qUINT_common.fxh", "// header\n")

    decisions = importing.decide(root, tmp_path / "Shaders", tmp_path / "Textures")

    d = decisions[0]
    assert d.status == "rejected"
    assert "missing include: missing.fxh" in d.reason


def test_decide_uses_existing_sandbox_reshade(tmp_path):
    # ReShade.fxh is already in the sandbox from a prior Fetch, not the repo.
    sandbox = tmp_path / "Shaders"
    _write(sandbox / "ReShade.fxh", "// framework\n")

    root = tmp_path / "qUINT"
    _write(root / "Shaders" / "qUINT_bloom.fx", '#include "ReShade.fxh"\n')

    decisions = importing.decide(root, sandbox, tmp_path / "Textures")

    assert decisions[0].status == "imported", decisions[0].reason
    assert decisions[0].resolved_includes == ["ReShade.fxh"]


# --- decide (Barbatos-style Includes/ layout) -----------------------------

def test_decide_barbatos_backslash_paths(tmp_path):
    root = tmp_path / "Reshade-Shaders"
    _write(
        root / "Shaders" / "BaBa_SSR.fx",
        '#include ".\\Includes\\bb_common.fxh"\n'
        'texture2D t < source = "Barbatos\\SS_BN2.png"; > { Width = 1; Height = 1; };\n',
    )
    _write(root / "Shaders" / "Includes" / "bb_common.fxh", "// header\n")
    _texture(root / "Textures" / "Barbatos" / "SS_BN2.png")

    decisions = importing.decide(root, tmp_path / "Shaders", tmp_path / "Textures")

    d = decisions[0]
    assert d.status == "imported", d.reason
    assert d.resolved_includes == ["Includes/bb_common.fxh"]
    assert d.resolved_textures == ["Barbatos/SS_BN2.png"]


def test_decide_basename_only_include_flattened(tmp_path):
    root = tmp_path / "Reshade-Shaders"
    _write(
        root / "Shaders" / "BaBa_SSR.fx",
        '#include "bb_common.fxh"\n',  # basename only; header lives in Includes/
    )
    _write(root / "Shaders" / "Includes" / "bb_common.fxh", "// header\n")

    decisions = importing.decide(root, tmp_path / "Shaders", tmp_path / "Textures")

    # Flattening adds the basename to the flat namespace, so this resolves.
    assert decisions[0].status == "imported", decisions[0].reason


def test_decide_rejects_unmatched_subpath(tmp_path):
    root = tmp_path / "Reshade-Shaders"
    _write(
        root / "Shaders" / "BaBa_SSR.fx",
        '#include "Sub/Thing.fxh"\n',  # not a real subpath under Shaders/
    )
    _write(root / "Shaders" / "Includes" / "Thing.fxh", "// header\n")

    decisions = importing.decide(root, tmp_path / "Shaders", tmp_path / "Textures")

    d = decisions[0]
    # The old lenient basename search would have "resolved" Thing.fxh; the
    # compiler-accurate check must not.
    assert d.status == "rejected"
    assert "missing include: Sub/Thing.fxh" in d.reason


# --- rewrite_references ---------------------------------------------------

def test_rewrite_references_normalizes_include_and_texture_paths():
    text = (
        '#include ".\\Includes\\bb_common.fxh"\n'
        '#include "./Includes/bb_ui.fxh"\n'
        '#include <Includes\\bb_depth.fxh>\n'
        'texture2D t < source = "Barbatos\\SS_BN2.png"; > '
        '{ Width = 1; Height = 1; };\n'
    )

    out = importing.rewrite_references(text)

    assert '#include "Includes/bb_common.fxh"' in out
    assert '#include "Includes/bb_ui.fxh"' in out
    assert '#include <Includes/bb_depth.fxh>' in out
    assert 'source = "Barbatos/SS_BN2.png"' in out
    assert "\\" not in out


def test_rewrite_references_leaves_clean_source_alone():
    text = (
        '#include "ReShade.fxh"\n'
        'texture2D t < source = "frametime"; > '
        '{ Width = 1; Height = 1; };\n'
    )
    assert importing.rewrite_references(text) == text


def test_rewrite_references_is_idempotent():
    text = '#include ".\\Includes\\a.fxh"\n'
    once = importing.rewrite_references(text)
    assert importing.rewrite_references(once) == once


def test_rewritten_include_resolves_like_vkbasalt(tmp_path):
    # vkBasalt replaces the including file's name with the include string,
    # treating only '/' as a separator (std::filesystem on Linux).
    shader_dir = tmp_path / "Shaders"
    includes_dir = shader_dir / "Includes"
    includes_dir.mkdir(parents=True)
    (includes_dir / "bb_common.fxh").write_text("// header\n")

    fx = shader_dir / "BaBa_Test.fx"
    fx.write_text('#include ".\\Includes\\bb_common.fxh"\n')
    rewritten = importing.rewrite_references(fx.read_text())

    include = rewritten.split('"')[1]
    assert "\\" not in include
    assert (fx.parent / include).is_file()


# --- textures declared in headers ----------------------------------------

def test_decide_collects_textures_from_includes(tmp_path):
    root = tmp_path / "src"
    _write(root / "Shaders" / "S.fx", '#include "h.fxh"\n')
    _write(
        root / "Shaders" / "h.fxh",
        'texture2D t < source = "tex.png"; > { Width = 1; Height = 1; };\n',
    )
    _texture(root / "Textures" / "tex.png")

    decisions = importing.decide(root, tmp_path / "Shaders", tmp_path / "Textures")

    d = decisions[0]
    assert d.status == "imported", d.reason
    assert d.resolved_textures == ["tex.png"]


def test_decide_rejects_texture_only_referenced_by_include(tmp_path):
    root = tmp_path / "src"
    _write(root / "Shaders" / "S.fx", '#include "h.fxh"\n')
    _write(
        root / "Shaders" / "h.fxh",
        'texture2D t < source = "gone.png"; > { Width = 1; Height = 1; };\n',
    )

    decisions = importing.decide(root, tmp_path / "Shaders", tmp_path / "Textures")

    d = decisions[0]
    assert d.status == "rejected"
    assert "missing texture: gone.png" in d.reason

# --- copying --------------------------------------------------------------

def test_import_root_copies_rewritten_flat_and_subpath(tmp_path):
    root = tmp_path / "src"
    _write(
        root / "Shaders" / "BaBa_Test.fx",
        '#include ".\\Includes\\bb_common.fxh"\n'
        'texture2D t < source = "Barbatos\\SS_BN2.png"; > '
        '{ Width = 1; Height = 1; };\n',
    )
    _write(root / "Shaders" / "Includes" / "bb_common.fxh", "// header\n")
    _texture(root / "Textures" / "Barbatos" / "SS_BN2.png")

    shaders_dir = tmp_path / "sandbox" / "Shaders"
    textures_dir = tmp_path / "sandbox" / "Textures"
    result = importing.import_root(root, shaders_dir, textures_dir)

    assert result.decisions[0].status == "imported"

    copied_fx = (shaders_dir / "BaBa_Test.fx").read_text()
    assert '#include "Includes/bb_common.fxh"' in copied_fx
    assert "\\" not in copied_fx

    assert (shaders_dir / "bb_common.fxh").is_file()
    assert (shaders_dir / "Includes" / "bb_common.fxh").is_file()
    assert (textures_dir / "SS_BN2.png").is_file()
    assert (textures_dir / "Barbatos" / "SS_BN2.png").is_file()

    info = shader.parse_fx(shaders_dir / "BaBa_Test.fx")
    missing, _incs, _texs = shader.resolve_dependencies(
        info, shaders_dir, textures_dir,
    )
    assert missing == []


def test_import_root_skips_rejected_shaders(tmp_path):
    root = tmp_path / "src"
    _write(root / "Shaders" / "Broken.fx", '#include "missing.fxh"\n')

    shaders_dir = tmp_path / "sandbox" / "Shaders"
    textures_dir = tmp_path / "sandbox" / "Textures"
    result = importing.import_root(root, shaders_dir, textures_dir)

    assert result.decisions[0].status == "rejected"
    assert result.copied == 0
    assert not shaders_dir.exists()


def test_import_root_dry_run_writes_nothing(tmp_path):
    root = tmp_path / "src"
    _write(root / "Shaders" / "S.fx", '#include "h.fxh"\n')
    _write(root / "Shaders" / "h.fxh", "// header\n")

    shaders_dir = tmp_path / "sandbox" / "Shaders"
    textures_dir = tmp_path / "sandbox" / "Textures"
    result = importing.import_root(root, shaders_dir, textures_dir, dry=True)

    assert result.copied == 2  # shader + header
    assert not shaders_dir.exists()


def test_import_root_is_idempotent(tmp_path):
    root = tmp_path / "src"
    _write(root / "Shaders" / "S.fx", '#include ".\\Includes\\h.fxh"\n')
    _write(root / "Shaders" / "Includes" / "h.fxh", "// header\n")

    shaders_dir = tmp_path / "sandbox" / "Shaders"
    textures_dir = tmp_path / "sandbox" / "Textures"
    first = importing.import_root(root, shaders_dir, textures_dir)
    second = importing.import_root(root, shaders_dir, textures_dir)

    assert first.copied == 3  # shader + header subpath + header flat
    assert second.copied == 0


def test_build_available_source_wins_over_sandbox(tmp_path):
    sandbox = tmp_path / "Shaders"
    _write(sandbox / "bb.fxh", "old contents\n")
    src = tmp_path / "src" / "Includes" / "bb.fxh"
    _write(src, "new contents\n")

    include_src, _, collisions, _ = importing.build_available(
        [(src, "Includes/bb.fxh")], [], sandbox, tmp_path / "Textures",
    )

    assert include_src["bb.fxh"] == src
    assert include_src["includes/bb.fxh"] == src
    assert collisions == ["bb.fxh"]


def test_build_available_identical_sandbox_copy_is_not_a_collision(tmp_path):
    sandbox = tmp_path / "Shaders"
    _write(sandbox / "bb.fxh", "same contents\n")
    src = tmp_path / "src" / "Includes" / "bb.fxh"
    _write(src, "same contents\n")

    _, _, collisions, _ = importing.build_available(
        [(src, "Includes/bb.fxh")], [], sandbox, tmp_path / "Textures",
    )

    assert collisions == []


def test_import_root_force_handles_sandbox_only_dependency(tmp_path):
    # ReShade.fxh comes from the sandbox, not the repo, so src == dst.
    sandbox = tmp_path / "Shaders"
    _write(sandbox / "ReShade.fxh", "// framework\n")
    root = tmp_path / "src"
    _write(root / "Shaders" / "S.fx", '#include "ReShade.fxh"\n')

    result = importing.import_root(
        root, sandbox, tmp_path / "Textures", force=True,
    )

    assert result.decisions[0].status == "imported"
    assert (sandbox / "S.fx").is_file()


def test_build_available_rewritten_copy_is_not_a_collision(tmp_path):
    # The sandbox copy is already rewritten; the source still has backslashes.
    sandbox = tmp_path / "Shaders"
    _write(sandbox / "Includes" / "bb.fxh", '#include "Includes/b.fxh"\n')
    src = tmp_path / "src" / "Includes" / "bb.fxh"
    _write(src, '#include ".\\Includes\\b.fxh"\n')

    _, _, collisions, _ = importing.build_available(
        [(src, "Includes/bb.fxh")], [], sandbox, tmp_path / "Textures",
    )

    assert collisions == []

