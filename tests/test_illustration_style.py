import json
import shutil
from datetime import date
from hashlib import sha256
from importlib.resources import files

import pytest
import yaml

from knowledge_product_workflow.diagrams import DiagramManifest
from knowledge_product_workflow.illustrations import (
    STYLE_CHECKS,
    AssetStyleInput,
    read_asset_style,
    record_asset_style,
    style_resources,
    validate_style_assets,
)
from knowledge_product_workflow.presentation import PublicationProfile
from knowledge_product_workflow.workspace import Workspace


def style_workspace(tmp_path):
    workspace = Workspace.create(tmp_path, "style-book")
    profile = PublicationProfile(subtitle="测试出版副题", cover_label="测试", footer_label="测试",
                                 copyright_line="测试", edition_label="v1.0.0", publication_date=date(2026, 10, 1),
                                 illustration_style_revision=1)
    (workspace.root / "planning" / "publication-profile.yaml").write_text(
        yaml.safe_dump(profile.model_dump(mode="json"), allow_unicode=True), encoding="utf-8")
    assets = workspace.root / "assets" / "figures"
    assets.mkdir()
    source = files("knowledge_product_workflow").joinpath("templates", "illustrations", "style-anchor.png")
    image = assets / "fixture.png"
    shutil.copyfile(source, image)
    root = workspace.root / "runs" / "diagrams"
    root.mkdir()
    digest = sha256(image.read_bytes()).hexdigest()
    manifest = DiagramManifest(diagram_id="fixture", chapter_number=1, chapter_body_sha256="a" * 64,
                               contract_sha256="b" * 64, spec_sha256="c" * 64,
                               svg_sha256="d" * 64, pdf_sha256="e" * 64, png_sha256=digest,
                               semantic_input_sha256="f" * 64, caption="测试固定母版", node_count=6,
                               max_label_characters=8, renderer="host-image")
    (root / "fixture.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    return workspace


def review(**updates):
    payload = {"diagram_id": "fixture", "reviewed_by": "测试视觉审阅",
               "measurement_source": "visual_estimate", "role_glyph_height_ratios": {},
               "observations": {key: {"assessment": "passed", "note": "固定测试母版逐项视觉观察通过"}
                                for key in STYLE_CHECKS}}
    payload.update(updates)
    return AssetStyleInput.model_validate(payload)


def test_missing_and_tampered_style_proof_block_submission(tmp_path):
    workspace = style_workspace(tmp_path)
    body = "![测试](assets/figures/fixture.pdf)"
    assert validate_style_assets(workspace, body)[0].severity.value == "blocker"
    path = record_asset_style(workspace, review())
    assert validate_style_assets(workspace, body) == []
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["input"]["reviewed_by"] = "篡改"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert read_asset_style(workspace, "fixture") is None


@pytest.mark.parametrize("key", ["typography", "decorations", "palette", "geometry", "reference_leakage"])
def test_large_drift_and_reference_leakage_cannot_be_accepted(tmp_path, key):
    workspace = style_workspace(tmp_path)
    submission = review()
    submission.observations[key].assessment = "failed"
    record_asset_style(workspace, submission)
    findings = validate_style_assets(workspace, "assets/figures/fixture.pdf")
    assert any(item.severity.value == "blocker" for item in findings)


def test_reliable_large_role_deviation_blocks_estimated_deviation_goes_to_manual(tmp_path):
    workspace = style_workspace(tmp_path)
    record_asset_style(workspace, review(role_glyph_height_ratios={"node": 0.09}, measurement_source="pixel_measurement"))
    assert validate_style_assets(workspace, "assets/figures/fixture.pdf")[0].severity.value == "blocker"
    record_asset_style(workspace, review(role_glyph_height_ratios={"node": 0.09}))
    assert validate_style_assets(workspace, "assets/figures/fixture.pdf")[0].severity.value == "warning"
    assert style_resources()["anchor_sha256"]


def test_actual_png_text_region_measurement_and_oversized_description_block(tmp_path):
    workspace = style_workspace(tmp_path)
    path = record_asset_style(workspace, review(text_regions=[{"role": "title", "box": [455, 25, 745, 75]}]))
    proof = json.loads(path.read_text(encoding="utf-8"))
    assert proof["pixel_measurements"]["title"]["method"].startswith("dark-pixel")
    assert not proof["numeric_blockers"]
    record_asset_style(workspace, review(text_regions=[{"role": "description", "box": [455, 25, 745, 75]}]))
    assert any(item.severity.value == "blocker" for item in validate_style_assets(workspace, "assets/figures/fixture.pdf"))
    with pytest.raises(ValueError, match="图片内"):
        record_asset_style(workspace, review(text_regions=[{"role": "node", "box": [1600, 900, 200, 100]}]))
