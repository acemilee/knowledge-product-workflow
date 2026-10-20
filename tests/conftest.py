"""pytest 全局配置：Windows 下为测试输出提供 UTF-8 兜底。

根因：Python 3.13 在 Windows 上默认按 ANSI 代码页（cp936/GBK）编码 stdout，
而 PowerShell 7 终端按 UTF-8 解码，导致中文路径/消息乱码，且 GBK 无法表示的
字符（如数学符号）会直接触发 UnicodeEncodeError。

覆盖范围：pytest 启动早期（rootdir、收集摘要等）由 TerminalWriter 直接写
原始流，进程内 reconfigure 对其无效——完整解决请设置环境变量
`PYTHONUTF8=1`（见 README"Windows 中文输出乱码"）。本文件兜底的是测试运行
期的输出回放与测试内派生子进程的编码环境。
"""

from __future__ import annotations

import os
import shutil
import sys

import pytest


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                # 非标准流（例如已被重定向且不支持 reconfigure）保持原样。
                pass


_force_utf8_stdio()
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


@pytest.fixture(autouse=True)
def native_test_prerequisites(request):
    """原生检查缺真实前提即明确未执行；不替产品改字体或伪造工具。"""
    if request.node.get_closest_marker('requires_poppler'):
        from knowledge_product_workflow.pdf_preview import resolve_pdftoppm
        if resolve_pdftoppm() is None:
            pytest.skip('缺实际Poppler；原生页面检查未执行，不代表通过')
    if request.node.get_closest_marker('requires_pdf_tools'):
        missing = [tool for tool in ('quarto', 'xelatex') if shutil.which(tool) is None]
        if missing:
            pytest.skip('原生出版前提缺失：' + ', '.join(missing))
        from knowledge_product_workflow.pdf_fonts import probe_preferred_fonts
        fonts = probe_preferred_fonts(request.getfixturevalue('tmp_path'))
        if not all(item['available'] for item in fonts.values()):
            pytest.skip('缺默认授权字体或字体探针不可用；原生排版未执行，不代表通过')
    if request.node.get_closest_marker('requires_d2'):
        from knowledge_product_workflow.d2 import resolve_d2
        if not resolve_d2().available:
            pytest.skip('缺实际D2；原生矢量检查未执行，不代表通过')
    if request.node.get_closest_marker('requires_svg_tools'):
        from knowledge_product_workflow.d2 import diagram_font_css, resolve_diagram_browser
        if not resolve_diagram_browser().available:
            pytest.skip('缺实际浏览器导出器；原生SVG检查未执行')
        if shutil.which('fc-match') is None:
            pytest.skip('缺实际fontconfig；原生SVG字体检查未执行')
        try:
            diagram_font_css()
        except (OSError, ValueError):
            pytest.skip('缺首选矢量字体；原生SVG检查未执行，不代表通过')
