"""异形SVG必须完整进入给定画布，不能由Typst默认fit裁切。"""
from pathlib import Path
import struct
import pytest
from pypdf import PdfReader
from knowledge_product_workflow.svg_export import export_svg, resolve_svg_compiler
from knowledge_product_workflow.pdf_preview import iter_png_rgb_rows

def colored_svg(width, height):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">'
            f'<rect width="{width}" height="{height}" fill="white"/>'
            f'<rect x="0" y="0" width="{width}" height="20" fill="#FF0000"/>'
            f'<rect x="0" y="{height-20}" width="{width}" height="20" fill="#0000FF"/>'
            f'<rect x="0" y="0" width="20" height="{height}" fill="#00FF00"/>'
            f'<rect x="{width-20}" y="0" width="20" height="{height}" fill="#FFFF00"/>'
            '</svg>')

def visible_edge_colors(path):
    raw=path.read_bytes();color_type=raw[25];stride=4 if color_type==6 else 3
    colors=[bytes(c) for c in [(255,0,0),(0,0,255),(0,255,0),(255,255,0)]]
    counts=[0,0,0,0]
    for row in iter_png_rgb_rows(path,include_alpha=True):
        for i in range(0,len(row),stride):
            pixel=row[i:i+3]
            for k,color in enumerate(colors):counts[k]+=pixel==color
    return counts

@pytest.mark.parametrize('shape,canvas',[((315,826),(640,874)),((826,315),(874,640)),((315,826),(315,826))])
def test_svg_different_canvas_preserves_both_extremes(tmp_path,shape,canvas):
    compiler=resolve_svg_compiler()
    if compiler is None:pytest.skip('需要已有Typst，不安装')
    svg=tmp_path/'figure.svg';svg.write_text(colored_svg(*shape),encoding='utf-8')
    pdf=tmp_path/'figure.pdf';png=tmp_path/'figure.png'
    export_svg(compiler,svg,pdf,png,*canvas,font_css=None)
    page=PdfReader(pdf).pages[0]
    assert float(page.mediabox.width)==pytest.approx(canvas[0]*.75)
    assert float(page.mediabox.height)==pytest.approx(canvas[1]*.75)
    assert struct.unpack('>II',png.read_bytes()[16:24])==canvas
    assert all(count>0 for count in visible_edge_colors(png)), '画布比例变化不能裁掉SVG任何边；不以文字提取掩盖实际裁切'
