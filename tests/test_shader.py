from pathlib import Path

from core import shader


FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_deband():
    info = shader.parse_fx(FIXTURES / "Deband.fx")
    assert info.name == "Deband"
    assert info.path.name == "Deband.fx"
    # Deband has a `range` uniform per the GShade file
    names = [u.name for u in info.uniforms]
    assert "Range" in names
    range_u = next(u for u in info.uniforms if u.name == "Range")
    assert range_u.ui_type == "slider"
    assert range_u.ui_min is not None
    assert range_u.ui_max is not None
    assert range_u.default is not None


def test_parse_deband_includes_reshade():
    info = shader.parse_fx(FIXTURES / "Deband.fx")
    assert any("ReShade" in inc for inc in info.includes)


def test_parse_curves_combo_items():
    info = shader.parse_fx(FIXTURES / "Curves.fx")
    mode = next((u for u in info.uniforms if u.name == "Mode"), None)
    assert mode is not None
    assert mode.ui_type == "combo"
    assert mode.ui_items is not None
    assert len(mode.ui_items) >= 2


def test_parse_broken_shader_records_missing():
    info = shader.parse_fx(FIXTURES / "BrokenShader.fx")
    assert "DoesNotExist.fxh" in info.includes
    assert "DoesNotExist.png" in info.textures


def test_check_dependencies_missing(tmp_path):
    info = shader.parse_fx(FIXTURES / "BrokenShader.fx")
    missing = shader.check_dependencies(
        info, include_dir=tmp_path, texture_dir=tmp_path
    )
    assert any("DoesNotExist.fxh" in m for m in missing)
    assert any("DoesNotExist.png" in m for m in missing)


def test_check_dependencies_present(tmp_path):
    # Make the dependencies exist
    (tmp_path / "DoesNotExist.fxh").write_text("")
    (tmp_path / "DoesNotExist.png").write_bytes(b"")
    info = shader.parse_fx(FIXTURES / "BrokenShader.fx")
    missing = shader.check_dependencies(info, include_dir=tmp_path, texture_dir=tmp_path)
    assert missing == []


def test_discover_empty_dir(tmp_path):
    assert shader.discover(tmp_path) == []


def test_discover_nonexistent_dir(tmp_path):
    assert shader.discover(tmp_path / "nope") == []


def test_discover_finds_fx_files(tmp_path):
    (tmp_path / "A.fx").write_text("uniform float x = 1.0;")
    (tmp_path / "B.fx").write_text("uniform float y = 2.0;")
    (tmp_path / "not_a_shader.txt").write_text("ignore me")
    infos = shader.discover(tmp_path)
    assert [i.name for i in infos] == ["A", "B"]


def test_parse_does_not_crash_on_garbage(tmp_path):
    bad = tmp_path / "Bad.fx"
    bad.write_text("this is not HLSL {{{ uniform float ; ; ;")
    info = shader.parse_fx(bad)
    # No exception, possibly no uniforms, possibly a warning — but it returns.
    assert info.name == "Bad"
    
# --- normalize_reference / read_includes / resolve_dependencies -----------

def test_normalize_reference_windows_backslashes():
    assert shader.normalize_reference('.\\Includes\\bb_common.fxh') == 'Includes/bb_common.fxh'
    assert shader.normalize_reference('./Includes/bb_common.fxh') == 'Includes/bb_common.fxh'
    assert shader.normalize_reference('Includes\\bb_common.fxh') == 'Includes/bb_common.fxh'
    assert shader.normalize_reference('bb_common.fxh') == 'bb_common.fxh'


def test_normalize_reference_quotes_and_empties():
    assert shader.normalize_reference('"ReShade.fxh"') == 'ReShade.fxh'
    assert shader.normalize_reference("'ReShade.fxh'") == 'ReShade.fxh'
    assert shader.normalize_reference('.') == ''
    assert shader.normalize_reference('') == ''


def test_read_includes_skips_comments_and_normalizes(tmp_path):
    f = tmp_path / "a.fxh"
    f.write_text(
        '#include "b.fxh"\n'
        '#include <c.fxh>\n'
        '// #include "commented.fxh"\n'
        '#include ".\\Includes\\d.fxh"\n'
    )
    assert shader.read_includes(f) == ['b.fxh', 'c.fxh', 'Includes/d.fxh']


def test_resolve_dependencies_transitive(tmp_path):
    (tmp_path / "a.fxh").write_text('#include "b.fxh"\n')
    (tmp_path / "b.fxh").write_text('// nothing\n')
    fx = tmp_path / "S.fx"
    fx.write_text('#include "a.fxh"\n')
    info = shader.parse_fx(fx)

    missing, includes, textures = shader.resolve_dependencies(
        info, shader_dir=tmp_path, texture_dir=tmp_path,
    )
    assert missing == []
    assert includes == {'a.fxh', 'b.fxh'}
    assert textures == set()


def test_resolve_dependencies_missing_include_and_texture(tmp_path):
    fx = tmp_path / "S.fx"
    fx.write_text(
        '#include "nope.fxh"\n'
        'texture2D t < source = "gone.png"; > { Width = 1; Height = 1; };\n'
    )
    info = shader.parse_fx(fx)
    missing, includes, textures = shader.resolve_dependencies(
        info, shader_dir=tmp_path, texture_dir=tmp_path,
    )
    assert any("nope.fxh" in m for m in missing)
    assert any("gone.png" in m for m in missing)
    assert includes == set()
    assert textures == set()


def test_resolve_dependencies_backslash_include_resolves(tmp_path):
    (tmp_path / "Includes").mkdir()
    (tmp_path / "Includes" / "bb_common.fxh").write_text("// header\n")
    fx = tmp_path / "S.fx"
    fx.write_text('#include ".\\Includes\\bb_common.fxh"\n')
    info = shader.parse_fx(fx)

    missing, includes, _ = shader.resolve_dependencies(
        info, shader_dir=tmp_path, texture_dir=tmp_path,
    )
    assert missing == []
    assert includes == {'Includes/bb_common.fxh'}


def test_resolve_dependencies_collects_textures_from_headers(tmp_path):
    (tmp_path / "h.fxh").write_text(
        'texture2D t < source = "hdr.png"; > { Width = 1; Height = 1; };\n'
    )
    (tmp_path / "hdr.png").write_bytes(b"x")
    fx = tmp_path / "S.fx"
    fx.write_text('#include "h.fxh"\n')
    info = shader.parse_fx(fx)

    missing, includes, textures = shader.resolve_dependencies(
        info, shader_dir=tmp_path, texture_dir=tmp_path,
    )
    assert missing == []
    assert includes == {"h.fxh"}
    assert textures == {"hdr.png"}
