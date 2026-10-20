import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledge_product_workflow import quarto_runtime, svg_export
from knowledge_product_workflow.publishing import _quarto_paths


def test_svg_font_binding_keeps_text_positions_weight_and_order():
    source = '<svg xmlns="http://www.w3.org/2000/svg"><text x="20" y="35" text-anchor="middle" font-weight="600">中文 doc-1 0.8<tspan x="20" dy="20">下一行 Python</tspan></text></svg>'
    actual = ET.fromstring(svg_export.font_bound_svg(source))
    text = actual[0]
    assert ''.join(actual.itertext()) == '中文 doc-1 0.8下一行 Python'
    assert {key: text.get(key) for key in ['x', 'y', 'text-anchor', 'font-weight']} == {
        'x': '20', 'y': '35', 'text-anchor': 'middle', 'font-weight': '600'}
    assert text[0].get('font-family') == 'PingFang SC'
    assert text[1].get('font-family') == 'Inter'
    assert text[2].get('x') == '20' and text[2].get('dy') == '20'


@pytest.mark.parametrize('source', [
    '<!DOCTYPE svg [<!ENTITY test "x">]><svg xmlns="http://www.w3.org/2000/svg"/>',
    '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/image.svg"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///C:/private.txt"/></svg>',
])
def test_svg_rejects_entity_and_external_resources(source):
    with pytest.raises(ValueError):
        svg_export.font_bound_svg(source)


def test_svg_export_uses_actual_font_file_parent_and_css_pixel_scale(tmp_path, monkeypatch):
    font = tmp_path / 'fonts/Inter-Regular.otf'
    font.parent.mkdir()
    font.write_bytes(b'font-fixture')
    svg = tmp_path / 'source.svg'
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>中文 Inter</text></svg>')
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b'controlled-output')
        source = Path(command[command.index('--root') + 1]) / 'figure.typ'
        assert '225.0pt' in source.read_text() and '75.0pt' in source.read_text()
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(svg_export.subprocess, 'run', run)
    svg_export.export_svg(Path('typst'), svg, tmp_path / 'figure.pdf', tmp_path / 'figure.png', 300, 100,
                          font_css=f'@font-face{{src:url("{font.as_uri()}")}}')
    assert all(str(font.parent) in command for command in commands)
    assert '--ppi' in commands[1] and '96' in commands[1]
    assert not list(tmp_path.glob('kp-svg-export-*'))


def test_quarto_native_process_keeps_original_invocation(tmp_path, monkeypatch):
    monkeypatch.setattr(quarto_runtime, 'restricted_windows', lambda: False)
    with quarto_runtime.quarto_command(Path('quarto'), ['render', 'book.qmd'], tmp_path, {'TEST': '1'}) as (command, env):
        assert command == ['quarto', 'render', 'book.qmd'] and env == {'TEST': '1'}


def test_quarto_restricted_transport_is_offline_private_and_preserves_installed_script(tmp_path, monkeypatch):
    executable = tmp_path / 'install/bin/quarto.exe'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'fixture')
    deno = executable.parent / 'tools/x86_64/deno.exe'
    deno.parent.mkdir(parents=True)
    deno.write_bytes(b'fixture')
    source = executable.parent / 'quarto.js'
    original = 'function quartoCacheDir(subdir) {\n  return quartoDir(userCacheDir, subdir);\n}\nconst content = "original";'
    source.write_text(original)
    monkeypatch.setattr(quarto_runtime, 'restricted_windows', lambda: True)
    with quarto_runtime.quarto_command(executable, ['render', 'delivery/book.qmd'], tmp_path,
                                       {'API_KEY': 'not-forwarded', 'KP_LOCAL_GATEWAY_CAPABILITY': 'not-forwarded'}) as (command, env):
        assert '--deny-net' in command
        assert command[-2:] == ['render', 'delivery/book.qmd']
        assert 'API_KEY' not in env and 'KP_LOCAL_GATEWAY_CAPABILITY' not in env
        assert 'const content = "original";' in Path(command[-3]).read_text()
        assert Path(env['KP_QUARTO_PIPE_TEMP']).is_relative_to(tmp_path)
    assert source.read_text() == original
    assert not list(tmp_path.glob('kp-quarto-runtime-*'))


def test_alias_mismatch_fails_before_any_renderer(tmp_path, monkeypatch):
    monkeypatch.setenv('KP_WORKSPACE_ASCII_ALIAS', 'Z:/')
    monkeypatch.setenv('KP_WORKSPACE_PHYSICAL_ROOT', str(tmp_path))
    with pytest.raises(RuntimeError, match='别名与实际目录不一致'), _quarto_paths(
        tmp_path / 'book.qmd', tmp_path, tmp_path, Path('quarto'), None
    ):
        pytest.fail('must not run')
