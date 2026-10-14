"""使用已安装Typst导出矢量SVG；不依赖浏览器IPC、不下载工具。"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

from .quarto import resolve_quarto
from .workspace_scratch import workspace_scratch

SVG = 'http://www.w3.org/2000/svg'
_CJK = re.compile(r'[\u2e80-\u9fff\uac00-\ud7ff\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef]+|[^\u2e80-\u9fff\uac00-\ud7ff\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef]+')


def resolve_svg_compiler() -> Path | None:
    configured = os.environ.get('KP_SVG_COMPILER_PATH')
    if configured:
        path = Path(configured)
        return path if path.is_absolute() and path.is_file() else None
    quarto = resolve_quarto()
    if quarto.path:
        path = quarto.path.parent / 'tools/x86_64/typst.exe'
        if path.is_file():
            return path
    command = shutil.which('typst')
    return Path(command) if command else None


def font_bound_svg(source: str) -> str:
    """按文字字形范围显式选择已核验字体，保留原位置、锚点、字号与字重。"""
    if len(source.encode('utf-8')) > 16 * 1024 * 1024 or re.search(r'<!DOCTYPE|<!ENTITY', source, re.IGNORECASE):
        raise ValueError('SVG源不支持外部实体或超过安全长度')
    root = ET.fromstring(source)
    if root.tag != f'{{{SVG}}}svg':
        raise ValueError('SVG根元素无效')
    for node in root.iter():
        for name, value in node.attrib.items():
            if name.split('}')[-1] == 'href' and not value.startswith(('#', 'data:')):
                raise ValueError('SVG不允许外部资源')
    for node in list(root.iter()):
        if node.tag not in (f'{{{SVG}}}text', f'{{{SVG}}}tspan'):
            continue
        node.set('font-family', 'Inter')
        if not node.text:
            continue
        text = node.text
        node.text = None
        for index, match in enumerate(_CJK.finditer(text)):
            value = match.group()
            is_cjk = bool(re.match(r'[\u2e80-\u9fff\uac00-\ud7ff\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef]', value))
            child = ET.Element(f'{{{SVG}}}tspan', {'font-family': 'PingFang SC' if is_cjk else 'Inter'})
            child.text = value
            node.insert(index, child)
    ET.register_namespace('', SVG)
    return ET.tostring(root, encoding='unicode')


def export_svg(compiler: Path, svg: Path, pdf: Path, png: Path, width: int, height: int, *, font_css: str | None) -> None:
    with workspace_scratch(svg.parent, prefix='kp-svg-export-') as scratch:
        folder = Path(scratch)
        source = svg.read_text(encoding='utf-8')
        font_paths = []
        if font_css:
            source = font_bound_svg(source)
            for url in re.findall(r'src:url\("([^"]+)"\)', font_css):
                parsed = urlsplit(url)
                path = Path(url2pathname(parsed.path))
                if parsed.scheme != 'file' or parsed.netloc or not path.is_file():
                    raise ValueError('矢量首选字体路径无效，不允许静默回退')
                parent = str(path.parent)
                if parent not in font_paths:
                    font_paths.append(parent)
            if not font_paths:
                raise ValueError('矢量首选字体无显式文件，不允许静默回退')
        (folder / 'figure.svg').write_text(source, encoding='utf-8')
        w, h = width * 0.75, height * 0.75
        (folder / 'figure.typ').write_text(
            f'#set page(width: {w}pt, height: {h}pt, margin: 0pt)\n'
            f'#place(top + left, image("figure.svg", width: {w}pt, height: {h}pt, fit: "contain"))\n',
            encoding='utf-8',
        )
        args = [str(compiler), 'compile', '--root', str(folder), str(folder / 'figure.typ')]
        for path in font_paths:
            args.extend(['--font-path', path])
        environment = os.environ.copy()
        environment.pop('DEBUG', None)
        for output, options in [(pdf, []), (png, ['--ppi', '96'])]:
            subprocess.run([*args, *options, str(output)], capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=30, check=True, env=environment)
            if not output.is_file() or output.stat().st_size == 0:
                raise RuntimeError('SVG导出工具未生成预期输出')
