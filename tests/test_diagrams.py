import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from pypdf import PdfReader

from knowledge_product_workflow.chapter_compliance import (
    create_chapter_compliance_template,
    submit_chapter,
)
from knowledge_product_workflow.d2 import (
    D2Resolution,
    d2_subprocess_environment,
    resolve_d2,
    resolve_diagram_browser,
)
from knowledge_product_workflow.diagrams import (
    DiagramInput,
    DiagramSpec,
    DiagramSubmissionError,
    create_diagram_template,
    diagram_schema,
    insert_diagram_reference,
    refresh_diagram_theme,
    render_d2_source,
    submit_diagram,
    validate_current_diagram_assets,
)
from knowledge_product_workflow.figure_templates import render_structured_svg
from knowledge_product_workflow.frontmatter import parse_markdown_artifact, render_markdown_artifact
from knowledge_product_workflow.humanize import record_humanize_check
from knowledge_product_workflow.models import ArtifactStatus
from knowledge_product_workflow.presentation import (
    PublicationPalette,
    PublicationUpdateInput,
    apply_publication_update,
    preview_publication_update,
)
from knowledge_product_workflow.state_machine import transition
from knowledge_product_workflow.workflows.authoring import (
    compile_chapter_contracts,
    create_chapter_draft,
)
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace
from tests.blueprint_support import submit_test_blueprint


def test_diagram_spec_renders_grouped_d2_with_stable_reader_labels() -> None:
    spec = DiagramSpec.model_validate(
        {
            "diagram_id": "agent-five-layer",
            "chapter_number": 1,
            "chapter_body_sha256": "a" * 64,
            "contract_sha256": "b" * 64,
            "diagram_type": "layered_architecture",
            "caption": "AI Agent 五层架构图",
            "reader_conclusion": "上层能力依赖下层工程与治理基础。",
            "nodes": [
                {"node_id": "application", "label": "应用层", "group": "业务"},
                {"node_id": "orchestration", "label": "编排层", "group": "业务"},
                {"node_id": "governance", "label": "治理层", "group": "基础"},
            ],
            "edges": [
                {"source": "application", "target": "orchestration"},
                {"source": "orchestration", "target": "governance"},
            ],
        }
    )

    source = render_d2_source(spec)

    assert 'label: "业务"' in source
    assert 'application: "应用层"' in source
    assert "group_1.application -> group_1.orchestration" in source
    assert "group_1.orchestration -> group_2.governance" in source
    assert 'stroke: "#C75D45"' in source
    assert "font-size: 28" in source
    assert "font-size: 22" in source


def test_wide_flow_uses_a_two_row_grid_to_preserve_pdf_text_size() -> None:
    spec = DiagramSpec.model_validate(
        {
            "diagram_id": "compact-flow",
            "chapter_number": 1,
            "chapter_body_sha256": "a" * 64,
            "contract_sha256": "b" * 64,
            "diagram_type": "flow",
            "direction": "right",
            "caption": "紧凑流程图",
            "reader_conclusion": "四步流程在有限版面内保持可读。",
            "nodes": [
                {"node_id": "a", "label": "第一步", "group": None},
                {"node_id": "b", "label": "第二步", "group": None},
                {"node_id": "c", "label": "第三步", "group": None},
                {"node_id": "d", "label": "第四步", "group": None},
            ],
            "edges": [
                {"source": "a", "target": "b", "label": "继续"},
                {"source": "b", "target": "c", "label": "继续"},
                {"source": "c", "target": "d", "label": "继续"},
            ],
        }
    )

    source = render_d2_source(spec)

    assert "direction: right" in source
    assert "grid-rows: 2" in source
    assert 'a -> b: "继续"' not in source
    assert "a -> b" in source


def test_d2_child_environment_removes_an_incompatible_debug_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEBUG", "release")

    environment = d2_subprocess_environment()

    assert "DEBUG" not in environment


def test_d2_resolution_reads_the_persisted_user_override_when_host_env_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "d2.exe"
    executable.write_bytes(b"fixture")
    monkeypatch.delenv("KP_D2_PATH", raising=False)
    monkeypatch.setattr("knowledge_product_workflow.d2._persistent_user_d2_path", lambda: executable)
    monkeypatch.setattr("knowledge_product_workflow.d2.shutil.which", lambda _name: None)
    monkeypatch.setattr(
        "knowledge_product_workflow.d2.subprocess.run",
        lambda *args, **kwargs: type("Completed", (), {"stdout": "v0.7.1\n", "stderr": ""})(),
    )

    resolution = resolve_d2()

    assert resolution.available is True
    assert resolution.path == executable
    assert resolution.source == "KP_D2_PATH"


def _approved_workspace(tmp_path: Path, *, v6: bool = False) -> Workspace:
    workspace = Workspace.create(tmp_path, "diagram-guide")
    if v6:
        from datetime import date

        import yaml

        from knowledge_product_workflow.presentation import (
            PublicationProfile,
            _brand_palette,
            publication_profile_path,
        )
        # Legacy v6 renderer/asset tests explicitly keep their old cover/style.
        # Current new-product defaults are verified in test_foundation_v134.
        legacy = PublicationProfile(subtitle="旧版渲染夹具", cover_label="知识产品工作流", footer_label="知识产品工作流",
            copyright_line="© 2026 知识产品工作流", edition_label="v1.0.0", publication_date=date(2026, 10, 8),
            reader_layout_revision=2, palette=_brand_palette("橙色基调"))
        publication_profile_path(workspace).write_text(yaml.safe_dump(legacy.model_dump(mode="json"), allow_unicode=True), encoding="utf-8")
    brief = create_product_brief(workspace, "AI Agent 工程实践", target_audience="工程师")
    metadata, body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    brief.write_text(
        render_markdown_artifact(transition(metadata, ArtifactStatus.APPROVED, approved_by="user"), body),
        encoding="utf-8",
    )
    blueprint = submit_test_blueprint(workspace, brief, titles=("架构基础",))
    metadata, body = parse_markdown_artifact(blueprint.read_text(encoding="utf-8"))
    blueprint.write_text(
        render_markdown_artifact(transition(metadata, ArtifactStatus.APPROVED, approved_by="user"), body),
        encoding="utf-8",
    )
    compile_chapter_contracts(workspace)
    create_chapter_draft(workspace, 1)
    return workspace


def _source_and_input(workspace: Workspace) -> tuple[Path, Path]:
    source = workspace.root / "runs" / "chapter.md"
    body = "# 1. 架构基础\n"
    source.write_text(body, encoding="utf-8")
    input_path = workspace.root / "runs" / "diagram.json"
    input_path.write_text(
        json.dumps(
            {
                "diagram_id": "agent-layers",
                "diagram_type": "layered_architecture",
                "caption": "Agent 分层架构",
                "reader_conclusion": "应用能力依赖底层的工程与治理基础。",
                "nodes": [
                    {"node_id": "application", "label": "应用层", "group": "业务"},
                    {"node_id": "governance", "label": "治理层", "group": "基础"},
                ],
                "edges": [{"source": "application", "target": "governance"}],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    insert_diagram_reference(workspace, 1, source, "agent-layers", input_path)
    return source, input_path


def test_diagram_submission_binds_every_generated_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _approved_workspace(tmp_path)
    source, input_path = _source_and_input(workspace)
    monkeypatch.setattr(
        "knowledge_product_workflow.diagrams.resolve_d2",
        lambda: D2Resolution(True, Path(r"C:\\tools\\d2.exe"), "test", "0.7", ()),
    )
    monkeypatch.setattr(
        "knowledge_product_workflow.diagrams.resolve_diagram_browser",
        lambda: type("Browser", (), {"available": True, "path": Path(r"C:\\tools\\chrome.exe")})(),
    )

    def fake_run(command, **kwargs):
        Path(command[-1]).write_bytes(b"diagram")
        return type("Completed", (), {"stdout": "", "stderr": ""})()

    monkeypatch.setattr("knowledge_product_workflow.diagrams.subprocess.run", fake_run)
    monkeypatch.setattr(
        "knowledge_product_workflow.diagrams.render_svg_with_browser",
        lambda _browser, _svg, pdf, png: (pdf.write_bytes(b"%PDF-diagram"), png.write_bytes(b"png")),
    )
    before_submit = source.read_text(encoding="utf-8")
    asset, manifest = submit_diagram(workspace, 1, source, input_path)

    assert asset == workspace.root / "assets" / "figures" / "agent-layers.pdf"
    assert manifest.d2_version == "0.7"
    assert (workspace.root / "runs" / "diagrams" / "agent-layers.spec.json").is_file()
    assert (workspace.root / "runs" / "diagrams" / "agent-layers.d2").is_file()
    assert (workspace.root / "runs" / "diagrams" / "agent-layers.svg").is_file()
    assert (workspace.root / "assets" / "figures" / "agent-layers.png").is_file()
    assert source.read_text(encoding="utf-8") == before_submit
    chapter = workspace.root / "chapters" / "chapter-01.md"
    body = source.read_text(encoding="utf-8").strip() + "\n"
    assert validate_current_diagram_assets(workspace, chapter, body) == []
    assert [finding.code for finding in validate_current_diagram_assets(workspace, chapter, body + "\n正文变更。\n")] == [
        "DIAGRAM_ASSET_UNBOUND"
    ]


def test_v6_svg_template_recolors_from_approved_profile_without_changing_chapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _approved_workspace(tmp_path, v6=True)
    source, input_path = _source_and_input(workspace)
    chapter = workspace.root / "chapters" / "chapter-01.md"
    metadata, _ = parse_markdown_artifact(chapter.read_text(encoding="utf-8"))
    chapter.write_text(render_markdown_artifact(metadata, source.read_text(encoding="utf-8")), encoding="utf-8")
    monkeypatch.setattr("knowledge_product_workflow.diagrams.resolve_d2", lambda: (_ for _ in ()).throw(AssertionError("SVG 模板不应调用 D2")))
    monkeypatch.setattr(
        "knowledge_product_workflow.diagrams.resolve_diagram_browser",
        lambda: type("Browser", (), {"available": True, "path": Path(r"C:\tools\chrome.exe")})(),
    )
    monkeypatch.setattr(
        "knowledge_product_workflow.diagrams.render_svg_with_browser",
        lambda _browser, _svg, pdf, png: (pdf.write_bytes(b"%PDF-diagram"), png.write_bytes(b"png")),
    )
    _, manifest = submit_diagram(workspace, 1, chapter, input_path)
    assert manifest.renderer == "svg-template"
    assert manifest.d2_sha256 is None
    svg = workspace.root / "runs" / "diagrams" / "agent-layers.svg"
    assert "#B65E2B" in svg.read_text(encoding="utf-8")
    assert validate_current_diagram_assets(workspace, chapter, source.read_text(encoding="utf-8").strip() + "\n") == []
    before_chapter = chapter.read_bytes()
    preview_publication_update(
        workspace,
        PublicationUpdateInput(brand_color_intent="紫色基调", version_bump="none"),
        language="zh-CN",
    )
    apply_publication_update(workspace, "用户")
    assert refresh_diagram_theme(workspace) == ["agent-layers"]
    assert "#6847A5" in svg.read_text(encoding="utf-8")
    assert chapter.read_bytes() == before_chapter


@pytest.mark.requires_svg_tools
def test_v6_svg_template_real_browser_export_keeps_chinese_labels(tmp_path: Path) -> None:
    if not resolve_diagram_browser().available:
        pytest.skip("当前环境缺少离线图表浏览器")
    workspace = _approved_workspace(tmp_path, v6=True)
    source, input_path = _source_and_input(workspace)
    pdf_path, manifest = submit_diagram(workspace, 1, source, input_path)
    assert manifest.renderer == "svg-template"
    assert (workspace.root / "assets" / "figures" / "agent-layers.png").stat().st_size > 0
    text = "".join(page.extract_text() for page in PdfReader(pdf_path).pages)
    assert "应用层" in text and "治理层" in text


def test_diagram_submission_refuses_missing_d2_without_ascii_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _approved_workspace(tmp_path)
    source, input_path = _source_and_input(workspace)
    monkeypatch.setattr(
        "knowledge_product_workflow.diagrams.resolve_d2",
        lambda: D2Resolution(False, None, None, None, ("d2",)),
    )

    with pytest.raises(DiagramSubmissionError, match="禁止回退为 ASCII"):
        submit_diagram(workspace, 1, source, input_path)


def test_diagram_reference_insertion_is_idempotent_and_rejects_png_bypass(tmp_path: Path) -> None:
    workspace = _approved_workspace(tmp_path)
    source = workspace.root / "runs" / "chapter.md"
    source.write_text("# 1. 架构基础\n", encoding="utf-8")

    insert_diagram_reference(workspace, 1, source, "agent-layers")
    insert_diagram_reference(workspace, 1, source, "agent-layers")

    assert source.read_text(encoding="utf-8").count("assets/figures/agent-layers.pdf") == 1
    source.write_text("# 1. 架构基础\n\n![图](assets/figures/agent-layers.png)\n", encoding="utf-8")
    with pytest.raises(DiagramSubmissionError, match="DIAGRAM_REFERENCE_INVALID"):
        insert_diagram_reference(workspace, 1, source, "agent-layers")


def test_diagram_template_and_schema_expose_no_internal_hashes(tmp_path: Path) -> None:
    workspace = _approved_workspace(tmp_path)
    source = workspace.root / "runs" / "chapter.md"
    source.write_text("# 1. 架构基础\n", encoding="utf-8")
    insert_diagram_reference(workspace, 1, source, "agent-layers")

    template, context = create_diagram_template(workspace, 1, source, "agent-layers")

    payload = json.loads(template.read_text(encoding="utf-8"))
    assert "chapter_body_sha256" not in payload
    assert json.loads(context.read_text(encoding="utf-8"))["controlled_reference"] == "assets/figures/agent-layers.pdf"
    assert "chapter_body_sha256" not in diagram_schema()["fields"]


def test_submit_diagram_rejects_legacy_spec_with_clear_migration_error(tmp_path: Path) -> None:
    workspace = _approved_workspace(tmp_path)
    source = workspace.root / "runs" / "chapter.md"
    source.write_text("# 1. 架构基础\n", encoding="utf-8")
    insert_diagram_reference(workspace, 1, source, "agent-layers")
    legacy = workspace.root / "runs" / "legacy.json"
    legacy.write_text(
        json.dumps(
            {
                "diagram_id": "agent-layers",
                "chapter_number": 1,
                "chapter_body_sha256": "a" * 64,
                "contract_sha256": "b" * 64,
                "diagram_type": "flow",
                "caption": "图",
                "reader_conclusion": "结论",
                "nodes": [{"node_id": "a", "label": "A"}, {"node_id": "b", "label": "B"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(DiagramSubmissionError, match="DIAGRAM_LEGACY_INPUT"):
        submit_diagram(workspace, 1, source, legacy)


def test_public_diagram_input_forbids_internal_hashes() -> None:
    with pytest.raises(ValueError):
        DiagramInput.model_validate(
            {
                "diagram_id": "agent-layers",
                "chapter_body_sha256": "a" * 64,
                "diagram_type": "flow",
                "caption": "图",
                "reader_conclusion": "结论",
                "nodes": [{"node_id": "a", "label": "A"}, {"node_id": "b", "label": "B"}],
            }
        )


def test_public_diagram_input_rejects_a_reader_visible_figure_number() -> None:
    with pytest.raises(ValueError, match="图注不得自行包含"):
        DiagramInput.model_validate(
            {
                "diagram_id": "agent-layers",
                "diagram_type": "layered_architecture",
                "caption": "图 1：Agent 分层架构",
                "reader_conclusion": "应用能力依赖底层工程与治理基础。",
                "nodes": [{"node_id": "application", "label": "应用层"}, {"node_id": "governance", "label": "治理层"}],
                "edges": [{"source": "application", "target": "governance"}],
            }
        )


def test_final_source_chain_binds_humanize_diagram_and_chapter_without_stale_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _approved_workspace(tmp_path)
    source = workspace.root / "runs" / "chapter.md"
    source.write_text(
        "# 1. 架构基础\n\n## 1.1 决策框架\n\n"
        "这一节先说明系统边界，再说明模块之间怎样交换信息。读者应当先识别输入、决策和输出，"
        "然后检查每个环节的责任是否清晰。这样可以避免把一组名词误当成可落地的工程方案。\n",
        encoding="utf-8",
    )
    diagram_input = workspace.root / "runs" / "agent-layers.input.json"
    diagram_input.write_text(
        json.dumps(
            {
                "diagram_id": "agent-layers",
                "diagram_type": "layered_architecture",
                "caption": "Agent 分层架构",
                "reader_conclusion": "应用能力依赖底层工程与治理基础。",
                "nodes": [
                    {"node_id": "application", "label": "应用层"},
                    {"node_id": "governance", "label": "治理层"},
                ],
                "edges": [{"source": "application", "target": "governance"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    create_diagram_template(workspace, 1, source, "agent-layers")
    insert_diagram_reference(workspace, 1, source, "agent-layers", diagram_input)
    record_humanize_check(workspace, 1, source)
    monkeypatch.setattr(
        "knowledge_product_workflow.diagrams.resolve_d2",
        lambda: D2Resolution(True, Path(r"C:\\tools\\d2.exe"), "test", "0.7", ()),
    )
    monkeypatch.setattr(
        "knowledge_product_workflow.diagrams.resolve_diagram_browser",
        lambda: type("Browser", (), {"available": True, "path": Path(r"C:\\tools\\chrome.exe")})(),
    )

    def fake_run(command, **kwargs):
        Path(command[-1]).write_bytes(b"diagram")
        return type("Completed", (), {"stdout": "", "stderr": ""})()

    monkeypatch.setattr("knowledge_product_workflow.diagrams.subprocess.run", fake_run)
    monkeypatch.setattr(
        "knowledge_product_workflow.diagrams.render_svg_with_browser",
        lambda _browser, _svg, pdf, png: (pdf.write_bytes(b"%PDF-diagram"), png.write_bytes(b"png")),
    )
    submit_diagram(workspace, 1, source, diagram_input)
    template, _ = create_chapter_compliance_template(workspace, 1, source)
    mapping = json.loads(template.read_text(encoding="utf-8"))
    mapping["learning_objective_anchors"] = {"建立可执行的判断框架": "1.1 决策框架"}
    mapping["unit_anchors"] = {"decision_framework": "1.1 决策框架"}
    mapping_path = workspace.root / "runs" / "chapter-compliance.input.json"
    mapping_path.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")

    chapter, _ = submit_chapter(workspace, 1, source, mapping_path)

    assert chapter.is_file()
    assert validate_current_diagram_assets(
        workspace, chapter, parse_markdown_artifact(chapter.read_text(encoding="utf-8"))[1]
    ) == []


def test_real_d2_renders_a_bound_pdf_asset_when_d2_is_available(tmp_path: Path) -> None:
    resolution = resolve_d2()
    if not resolution.available:
        pytest.skip("本机未安装 D2，跳过真实渲染验收")
    workspace = _approved_workspace(tmp_path)
    source, diagram_input = _source_and_input(workspace)

    asset, manifest = submit_diagram(workspace, 1, source, diagram_input)

    assert asset.read_bytes().startswith(b"%PDF")
    assert manifest.d2_version == resolution.version


def test_real_sequence_asset_preserves_request_return_and_followup_order(tmp_path: Path) -> None:
    if not resolve_d2().available or not resolve_diagram_browser().available:
        pytest.skip("真实时序验收需要已有D2及离线浏览器")
    workspace = _approved_workspace(tmp_path, v6=True)
    source = workspace.root / "runs" / "chapter-01-sequence.md"
    source.write_text("# 1. 架构基础\n", encoding="utf-8")
    labels = ["提出问题", "请求检索", "返回摘录", "给出草稿", "继续提问"]
    paths = [("user", "agent"), ("agent", "retrieval"), ("retrieval", "agent"),
             ("agent", "user"), ("user", "agent")]
    data = {
        "diagram_id": "retrieval-messages", "diagram_type": "sequence",
        "direction": "down", "caption": "检索请求与返回",
        "reader_conclusion": "返回草稿后的继续提问是新的请求。",
        "nodes": [{"node_id": "user", "label": "用户"},
                  {"node_id": "agent", "label": "主Agent"},
                  {"node_id": "retrieval", "label": "检索服务"}],
        "edges": [{"source": start, "target": end, "label": label}
                  for (start, end), label in zip(paths, labels, strict=True)],
    }
    input_path = workspace.root / "runs" / "sequence.json"
    input_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    insert_diagram_reference(workspace, 1, source, data["diagram_id"], input_path)
    asset, manifest = submit_diagram(workspace, 1, source, input_path)
    assert manifest.renderer == "d2"
    assert "shape: sequence_diagram" in (workspace.root / "runs/diagrams/retrieval-messages.d2").read_text(encoding="utf-8")
    text = "".join(page.extract_text() for page in PdfReader(asset).pages)
    compact = "".join(text.split())
    assert all(actor in compact for actor in ["用户", "主Agent", "检索服务"])
    positions = [compact.index(label) for label in labels]
    assert positions == sorted(positions)
    assert validate_current_diagram_assets(workspace, source, source.read_text(encoding="utf-8")) == []


def test_presented_overview_only_renders_declared_node_text() -> None:
    labels = ["接收\n问题与参数", "定位\n词集合匹配", "核对\n保留缺项", "草稿\n交接待核"]
    spec = DiagramSpec.model_validate({
        "diagram_id": "processing-overview", "chapter_number": 1,
        "chapter_body_sha256": "a" * 64, "contract_sha256": "b" * 64,
        "diagram_type": "stage_flow", "caption": "一次检索的内部阶段",
        "reader_conclusion": "阶段各有职责，草稿仍待核对。",
        "nodes": [{"node_id": f"stage{i}", "label": label}
                  for i, label in enumerate(labels)],
        "edges": [{"source": f"stage{i}", "target": f"stage{i+1}"} for i in range(3)],
        "presentation": {"scheme": "warm-focus", "group_colors": {}, "concept_nodes": {}, "node_colors": {
            "stage0": "peach", "stage1": "sand", "stage2": "rose", "stage3": "stone"}},
    })
    xml = ET.fromstring(render_structured_svg(spec, PublicationPalette()))
    texts = ["".join(element.itertext()) for element in xml.iter()
             if element.tag.endswith("}text")]
    assert texts == [line for label in labels for line in label.splitlines()]
