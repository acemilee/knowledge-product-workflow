"""O09：只读可见 PNG 量测；夹具不是字体或真实教程质量证明。"""

import json
import struct
import zlib
from hashlib import sha256

import pytest
import yaml

from knowledge_product_workflow import illustrations
from knowledge_product_workflow.illustrations import (
    AssetStyleInput,
    asset_check_path,
    create_style_review_template,
    read_asset_style,
    record_asset_style,
    validate_style_assets,
)
from tests.test_illustration_style import review, style_workspace


def chunk(kind, payload):
    return (struct.pack("!I", len(payload)) + kind + payload
            + struct.pack("!I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def png_bytes(*, alpha=None, ink_height=26, separated=False, color=(248, 229, 216),
              width=1200, height=360, bit_depth=8, color_type=None, interlace=0,
              filter_type=0):
    channels = 3 if alpha is None else 4
    fill = bytes(color) + (bytes([alpha]) if alpha is not None else b"")
    ink = bytes((0, 0, 0)) + (bytes([alpha]) if alpha is not None else b"")
    rows = []
    for y in range(height):
        row = bytearray(fill * width)
        occupied = 10 <= y < 10 + ink_height
        if separated:
            occupied = 10 <= y < 18 or 26 <= y < 34
        if occupied:
            row[10 * channels:30 * channels] = ink * 20
        rows.append(bytes([filter_type]) + row)
    ihdr = struct.pack("!IIBBBBB", width, height, bit_depth,
                       color_type if color_type is not None else (2 if alpha is None else 6),
                       0, 0, interlace)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


def measurement_workspace(tmp_path, image_bytes, *, renderer="svg-template", revision=2,
                          admission=True):
    workspace = style_workspace(tmp_path)
    profile_path = workspace.root / "planning/publication-profile.yaml"
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    profile["illustration_style_revision"] = revision
    profile_path.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    semantic = {
        "diagram_id": "fixture", "diagram_type": "flow", "backend": "vector",
        "caption": "合成像素测试", "reader_conclusion": "仅验证只读量测",
        "nodes": [{"node_id": "a", "label": "输入"}, {"node_id": "b", "label": "输出"}],
        "edges": [{"source": "a", "target": "b"}],
        "presentation": {"scheme": "warm-focus", "node_colors": {"a": "peach", "b": "sand"},
                         "group_colors": {}, "concept_nodes": {}},
    }
    if admission:
        semantic["admission"] = {
            "family": "flow", "reader_job": "测试读取分支关系和当前边界",
            "visual_gain": "测试显示分支关系和当前边界", "information_form": "branching_flow",
            "evidence_boundary": "人工合成像素区域不冒充真实中文字体", "planned_width_mm": 184,
        }
    semantic_path = workspace.root / "runs/diagrams/fixture.input.json"
    semantic_path.write_text(json.dumps(semantic, ensure_ascii=False), encoding="utf-8")
    image = workspace.root / "assets/figures/fixture.png"
    image.write_bytes(image_bytes)
    manifest_path = workspace.root / "runs/diagrams/fixture.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest.update(renderer=renderer, png_sha256=sha256(image_bytes).hexdigest(),
                    semantic_input_sha256=sha256(semantic_path.read_bytes()).hexdigest())
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return workspace


def measurement_review(**updates):
    values = {"measurement_source": "pixel_measurement",
              "text_regions": [{"role": "node", "box": [8, 8, 24, 30]}],
              "color_regions": [{"object_id": "a", "box": [80, 80, 8, 8]}]}
    values.update(updates)
    return review(**values)


@pytest.mark.parametrize("alpha", [None, 255, 128])
def test_opaque_and_half_alpha_keep_visible_text_and_color_measurements(tmp_path, alpha):
    raw = png_bytes(alpha=alpha)
    workspace = measurement_workspace(tmp_path, raw)
    record_asset_style(workspace, measurement_review())
    proof = read_asset_style(workspace, "fixture")
    assert proof["png_sha256"] == sha256(raw).hexdigest()
    assert (workspace.root / "assets/figures/fixture.png").read_bytes() == raw
    assert proof["pixel_measurements"]["node"]["glyph_height_ratio"] == pytest.approx(26 / 360)
    assert proof["pixel_measurements"]["node"]["planned_glyph_pt"] == pytest.approx(
        26 * 184 * 72 / 25.4 / 1200)
    expected = [248, 229, 216] if alpha != 128 else [251, 242, 235]
    assert proof["color_measurements"]["color:node:a"]["median_rgb"] == expected
    assert not proof["numeric_blockers"]


def test_fully_transparent_dark_channels_are_white_and_cannot_count_as_text(tmp_path):
    raw = png_bytes(alpha=0)
    workspace = measurement_workspace(tmp_path, raw)
    record_asset_style(workspace, measurement_review())
    proof = read_asset_style(workspace, "fixture")
    assert proof["pixel_measurements"] == {}
    assert "node" in proof["numeric_reviews"]
    assert proof["color_measurements"]["color:node:a"]["median_rgb"] == [255, 255, 255]
    assert "color:node:a" in proof["numeric_blockers"]


@pytest.mark.parametrize("pixel, expected", [
    ((1, 128, 255), (1, 128, 255)),
    ((1, 128, 255, 255), (1, 128, 255)),
    ((1, 128, 255, 0), (255, 255, 255)),
    ((1, 128, 255, 128), (128, 191, 255)),
])
def test_visible_row_adapter_preserves_rgb_and_rounds_rgba_on_white(tmp_path, pixel, expected):
    path = tmp_path / "pixel.png"
    path.write_bytes(png_bytes(alpha=pixel[3] if len(pixel) == 4 else None,
                               color=pixel[:3], width=1, height=1, ink_height=0))
    before = path.read_bytes()
    assert [bytes(row) for row in illustrations._iter_visible_png_rgb_rows(path)] == [bytes(expected)]
    assert path.read_bytes() == before


@pytest.mark.parametrize("mutation, message", [
    ("crc", "校验失败"), ("truncated", "结束块"), ("partial-chunk", "块不完整"),
    ("bit-depth", "RGB 编码"), ("color-type", "RGB 编码"),
    ("interlace", "编码不支持"), ("filter", "无效滤波器"),
])
@pytest.mark.parametrize("branch", ["text", "color"])
@pytest.mark.parametrize("alpha", [None, 255])
def test_both_measurement_branches_keep_strict_png_rejections(tmp_path, mutation, message, branch, alpha):
    options = {"alpha": alpha}
    if mutation == "bit-depth":
        options["bit_depth"] = 16
    elif mutation == "color-type":
        options["color_type"] = 3
    elif mutation == "interlace":
        options["interlace"] = 1
    elif mutation == "filter":
        options["filter_type"] = 5
    raw = png_bytes(**options)
    if mutation == "crc":
        raw = raw[:29] + bytes([raw[29] ^ 1]) + raw[30:]
    elif mutation == "truncated":
        raw = raw[:-12]
    elif mutation == "partial-chunk":
        raw = raw[:-14]
    workspace = measurement_workspace(tmp_path, raw)
    value = measurement_review(color_regions=[]) if branch == "text" else measurement_review(text_regions=[])
    with pytest.raises(ValueError, match=message):
        record_asset_style(workspace, value)
    assert not asset_check_path(workspace, "fixture").exists()
    assert (workspace.root / "assets/figures/fixture.png").read_bytes() == raw


@pytest.mark.parametrize("branch", ["text", "color"])
@pytest.mark.parametrize("box", [[-1, 0, 1, 1], [0, 0, 0, 1], [1199, 0, 2, 1],
                                 [0, 359, 1, 2], [906, 195, 1078, 220]])
def test_rgba_regions_use_xywh_and_reject_out_of_bounds_without_writing_proof(tmp_path, branch, box):
    workspace = measurement_workspace(tmp_path, png_bytes(alpha=255))
    updates = {"text_regions": [], "color_regions": []}
    updates[f"{branch}_regions"] = [{"role": "node", "box": box}] if branch == "text" else [
        {"object_id": "a", "box": box}]
    with pytest.raises(ValueError, match="图片内"):
        record_asset_style(workspace, measurement_review(**updates))
    assert not asset_check_path(workspace, "fixture").exists()


@pytest.mark.parametrize("branch", ["text", "color"])
def test_region_can_touch_right_and_bottom_edge(tmp_path, branch):
    workspace = measurement_workspace(tmp_path, png_bytes(alpha=255))
    updates = {"text_regions": [], "color_regions": []}
    updates[f"{branch}_regions"] = [{"role": "node", "box": [1192, 352, 8, 8]}] if branch == "text" else [
        {"object_id": "a", "box": [1192, 352, 8, 8]}]
    record_asset_style(workspace, measurement_review(**updates))
    assert read_asset_style(workspace, "fixture") is not None


@pytest.mark.parametrize("script, separated", [("latin", False), ("cjk", True)])
def test_unreliable_text_measurements_stay_pending(tmp_path, script, separated):
    workspace = measurement_workspace(tmp_path, png_bytes(alpha=255, separated=separated))
    value = measurement_review(text_regions=[{"role": "node", "script": script, "box": [8, 8, 24, 30]}],
                               color_regions=[])
    record_asset_style(workspace, value)
    proof = read_asset_style(workspace, "fixture")
    assert "node" in proof["numeric_reviews"]
    assert not proof["numeric_blockers"]
    if separated:
        assert proof["pixel_measurements"]["node"]["reliable"] is False
    else:
        assert proof["pixel_measurements"] == {}


@pytest.mark.parametrize("height, blocked", [(20, True), (26, False)])
def test_rgba_vector_role_exemption_keeps_absolute_planned_legibility(tmp_path, height, blocked):
    workspace = measurement_workspace(tmp_path, png_bytes(alpha=255, ink_height=height))
    record_asset_style(workspace, measurement_review(color_regions=[]))
    proof = read_asset_style(workspace, "fixture")
    assert ("node:planned-legibility" in proof["numeric_blockers"]) is blocked
    assert "node" not in proof["numeric_blockers"]
    assert any(f.severity.value == "blocker" for f in validate_style_assets(
        workspace, "assets/figures/fixture.pdf")) is blocked


def test_wrong_rgba_flat_color_still_blocks_and_mixed_region_stays_pending(tmp_path):
    workspace = measurement_workspace(tmp_path, png_bytes(alpha=255, color=(100, 229, 216)))
    record_asset_style(workspace, measurement_review(text_regions=[]))
    assert "color:node:a" in read_asset_style(workspace, "fixture")["numeric_blockers"]
    assert any(f.severity.value == "blocker" for f in validate_style_assets(
        workspace, "assets/figures/fixture.pdf"))
    record_asset_style(workspace, measurement_review(text_regions=[], color_regions=[
        {"object_id": "a", "box": [80, 80, 8, 8], "kind": "mixed"}]))
    proof = read_asset_style(workspace, "fixture")
    assert proof["color_measurements"]["color:node:a"]["reliable"] is False
    assert "color:node:a" in proof["numeric_reviews"]
    assert not proof["numeric_blockers"]


@pytest.mark.parametrize("failed_check", ["labels", "relations"])
def test_rgba_measurements_do_not_override_failed_label_or_relation_observations(tmp_path, failed_check):
    workspace = measurement_workspace(tmp_path, png_bytes(alpha=255))
    value = measurement_review()
    value.observations[failed_check].assessment = "failed"
    record_asset_style(workspace, value)
    proof = read_asset_style(workspace, "fixture")
    assert not proof["numeric_blockers"]
    findings = validate_style_assets(workspace, "assets/figures/fixture.pdf")
    assert any(f.code == "ILLUSTRATION_CONTENT_FAILED" and f.severity.value == "blocker"
               for f in findings)


@pytest.mark.parametrize("renderer", ["host-image", "host-image+svg"])
def test_host_canvas_gate_remains_before_rgba_measurement(tmp_path, renderer):
    workspace = measurement_workspace(tmp_path, png_bytes(alpha=255), renderer=renderer)
    with pytest.raises(ValueError, match="16:9"):
        record_asset_style(workspace, measurement_review())
    assert not asset_check_path(workspace, "fixture").exists()


@pytest.mark.parametrize("revision, renderer, expected_gate", [
    (1, "d2", True), (2, "d2", False), (2, "svg-template", False),
    (2, "host-image", True), (2, "host-image+svg", True),
])
@pytest.mark.parametrize("measurement_source", ["pixel_measurement", "visual_estimate"])
def test_renderer_role_ratio_gate_keeps_scalar_measurement_source_identity(
        tmp_path, monkeypatch, revision, renderer, expected_gate, measurement_source):
    workspace = measurement_workspace(tmp_path, png_bytes(alpha=255), revision=revision, renderer=renderer)
    if renderer == "host-image+svg":
        # Keep the role gate real; composition identity is checked by the separate mixed suite.
        monkeypatch.setattr(illustrations, "review_binding", lambda *args, **kwargs: "a" * 64)
    record_asset_style(workspace, measurement_review(
        measurement_source=measurement_source, text_regions=[], color_regions=[],
        role_glyph_height_ratios={"node": 0.09}))
    proof = read_asset_style(workspace, "fixture")
    assert proof["input"]["measurement_source"] == measurement_source
    assert ("node" in proof["numeric_blockers"]) is (
        expected_gate and measurement_source == "pixel_measurement")
    assert ("node" in proof["numeric_reviews"]) is (
        expected_gate and measurement_source == "visual_estimate")
    assert proof["pixel_measurements"] == {}


@pytest.mark.parametrize("revision, renderer, host_canvas, role_gate", [
    (1, "d2", False, True), (2, "d2", False, False),
    (2, "svg-template", False, False), (2, "host-image", True, True),
    (2, "host-image+svg", True, True),
])
def test_measurement_policy_matches_renderer_branches_without_changing_review_schema(
        tmp_path, monkeypatch, revision, renderer, host_canvas, role_gate):
    workspace = measurement_workspace(tmp_path, png_bytes(alpha=255), revision=revision, renderer=renderer)
    if renderer == "host-image+svg":
        # Composite binding has a separate existing integration suite; this test reads policy only.
        monkeypatch.setattr(illustrations, "style_review_fingerprint", lambda *args: "a" * 64)
    result = create_style_review_template(workspace, "fixture")
    policy = result["measurement_policy"]
    assert policy["renderer"] == result["renderer"] == renderer
    assert policy["host_canvas_gate"]["applicable"] is host_canvas
    assert policy["role_ratio_gate"]["applicable"] is role_gate
    assert policy["planned_legibility_gate"]["applicable"] is (revision == 2)
    assert policy["planned_legibility_gate"]["minimum_glyph_pt"] == 9
    assert policy["region_box"] == "[x, y, width, height]，单位像素；非右下角坐标，须完整位于当前图片内"
    assert policy["png_measurement"]["encodings"] == ["8-bit RGB", "8-bit RGBA"]
    assert policy["png_measurement"]["rgba_background_rgb"] == [255, 255, 255]
    assert "最小" in policy["text_observation"]
    assert "uncertain" in policy["unreliable_measurements"]
    payload = json.loads((workspace.root / "runs/templates/fixture.style-review.template.json").read_bytes())
    assert set(payload) == {"candidate_call_id", "diagram_id", "reviewed_by", "measurement_source",
                            "role_glyph_height_ratios", "observations"}
    assert "measurement_policy" not in AssetStyleInput.model_json_schema()["properties"]
    assert payload["measurement_source"] == "unavailable"
    assert all(item["assessment"] == "uncertain" for item in payload["observations"].values())


def test_policy_without_admission_does_not_claim_an_absolute_glyph_measurement(tmp_path):
    workspace = measurement_workspace(tmp_path, png_bytes(alpha=255), admission=False)
    policy = create_style_review_template(workspace, "fixture")["measurement_policy"]
    assert policy["planned_legibility_gate"]["applicable"] is False
    assert policy["planned_legibility_gate"]["planned_width_mm"] is None
