"""合成算法测试使用合成素材；不冒充原生AI图样或正式后端验收。"""
import hashlib
import xml.etree.ElementTree as ET

import pytest

from knowledge_product_workflow.experimental_composition import compose_stage_flow_probe
from tests.test_pdf_preview_set import _pixel_png


def test_probe_preserves_material_and_places_exact_vector_labels(tmp_path):
    material = tmp_path / "synthetic-material.png"
    material.write_bytes(_pixel_png())
    original = material.read_bytes()
    labels = ["检索", "上下文构造", "回答生成"]
    svg, evidence = compose_stage_flow_probe(material, labels)
    tree = ET.fromstring(svg)
    assert [node.text for node in tree.iter() if node.tag.endswith('text')] == labels
    assert material.read_bytes() == original
    assert evidence["source_png_sha256"] == hashlib.sha256(original).hexdigest()
    assert evidence["production_ready"] is False
    assert "not_inferred" in evidence["ai_origin"]


def test_probe_refuses_long_text_and_invalid_color_instead_of_shrinking(tmp_path):
    material = tmp_path / "synthetic-material.png"
    material.write_bytes(_pixel_png())
    with pytest.raises(ValueError):
        compose_stage_flow_probe(material, ["非常长的字段不能靠缩小字号塞入盒子"*3, "结果"])
    with pytest.raises(ValueError):
        compose_stage_flow_probe(material, ["输入", "输出"], accent="url(x)")
