import json

import pytest

from knowledge_product_workflow.content_plan import ContentPlanInput
from knowledge_product_workflow.illustration_tasks import IllustrationTask
from knowledge_product_workflow.workflows.curriculum import BlueprintSubmission


def approved_workspace(tmp_path, tasks):
    from hashlib import sha256

    import yaml

    from knowledge_product_workflow.detail_constraints import DetailConstraints
    from knowledge_product_workflow.frontmatter import (
        parse_markdown_artifact,
        render_markdown_artifact,
    )
    from knowledge_product_workflow.models import ArtifactStatus
    from knowledge_product_workflow.presentation import load_or_create_publication_profile
    from knowledge_product_workflow.state_machine import transition
    from knowledge_product_workflow.workflows.authoring import (
        compile_chapter_contracts,
        create_chapter_draft,
    )
    from knowledge_product_workflow.workflows.curriculum import submit_curriculum_blueprint
    from knowledge_product_workflow.workflows.product_brief import (
        brief_contract_fingerprints,
        create_product_brief,
    )
    from knowledge_product_workflow.workspace import Workspace

    w = Workspace.create(tmp_path, "teaching-diagrams")
    brief = create_product_brief(w, "离线资料检索", DetailConstraints(target_words=1, delivery_format="markdown"),
                                target_audience="需要交接证据的工程师")
    meta, body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    brief.write_text(render_markdown_artifact(transition(meta, ArtifactStatus.APPROVED, approved_by="测试用户"), body), encoding="utf-8")
    meta, body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
    value = blueprint(2, tasks)
    value.update(brief_sha256=sha256(brief.read_bytes()).hexdigest(), brief_version=meta.version,
                 audience_level="intermediate",
                 brief_content_sha256=brief_contract_fingerprints(body)["content_contract_sha256"])
    supplied = w.root/"runs/blueprint.yaml"
    supplied.write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")
    path = submit_curriculum_blueprint(w, supplied)
    meta, body = parse_markdown_artifact(path.read_text(encoding="utf-8"))
    path.write_text(render_markdown_artifact(transition(meta, ArtifactStatus.APPROVED, approved_by="测试用户"), body), encoding="utf-8")
    compile_chapter_contracts(w)
    load_or_create_publication_profile(w, language="zh-CN")
    create_chapter_draft(w, 1)
    return w


def task(task_id="dependency-map", **changes):
    return {"section_id": "1.1", "task_id": task_id, "decision": "required",
            "production": "create", "trigger": "structure", "read_question": "资料与索引如何相互依赖？",
            "viewpoint": "组件依赖关系", "visual_gain": "同时看到各组件的依赖及边界关系",
            "alternative_rationale": "表格便于查属性，但不直接表示组件的依赖",
            "diagram_type": "layered_architecture", "backend": "vector",
            "placement": "orientation"} | changes


def blueprint(policy=1, tasks=None):
    value = {"brief_sha256": "a"*64, "brief_version": 1, "topic": "离线资料检索",
             "target_audience": "需要交接证据的工程师", "audience_level": "进阶",
             "chapters": [{"chapter_number": 1, "title": "资料边界", "learning_objectives": ["分清依赖"],
                           "scope": "离线资料依赖和交接", "weight": 1, "rationale": "先明确输入输出边界",
                           "chapter_archetype": "concept", "section_outline": [{"number": "1.1", "title": "输入关系"}]}]}
    if policy == 2:
        value["illustration_policy_revision"] = 2
        value["chapters"][0]["illustration_tasks"] = tasks if tasks is not None else [task()]
    return value


def plan(policy=1, tasks=None):
    value = {"sections": [{"section_id": "1.1", "reader_job": "分清资料和索引的不同职责及依赖",
                          "opening_mode": "claim", "block_sequence": ["explanation", "table"],
                          "asset_decision": "table", "artifact": "可查属性表",
                          "asset_rationale": "对照参数与具体使用边界，便于查询"}]}
    if policy == 2:
        value["illustration_policy_revision"] = 2
        value["sections"][0]["illustration_tasks"] = tasks if tasks is not None else [task()]
        for item in value["sections"][0]["illustration_tasks"]:
            if item.get("production") == "create":
                value["sections"][0]["block_sequence"] += ["explanation", "diagram"]
    return value


def test_old_blueprint_and_content_plan_serialization_keeps_old_contract_bytes():
    old = BlueprintSubmission.model_validate(blueprint())
    assert "illustration_policy_revision" not in old.model_dump()
    assert "illustration_tasks" not in old.chapters[0].model_dump()
    old_plan = ContentPlanInput.model_validate(plan())
    assert old_plan.model_dump(mode="json") == plan()
    assert "illustration_policy" not in old.model_dump_json()
    assert json.loads(old.model_dump_json()) == old.model_dump(mode="json")


def test_public_outline_schema_none_example_can_complete_policy_two_contract(tmp_path):
    import yaml
    from typer.testing import CliRunner

    from knowledge_product_workflow.cli import app
    from knowledge_product_workflow.illustration_tasks import approved_illustration_tasks

    result = CliRunner().invoke(app, ["outline-schema"])
    assert result.exit_code == 0, result.output
    schema = yaml.safe_load(result.output)
    fields = schema["illustration_tasks"]["item_schema"]["properties"]
    assert {"section_id", "decision", "alternative_rationale"} <= fields.keys()
    workspace = approved_workspace(tmp_path, [schema["illustration_tasks"]["none_example"]])
    policy, _fingerprint, tasks = approved_illustration_tasks(workspace)
    assert policy == 2
    assert len(tasks) == 1 and tasks[0][1].decision == "none"
    assert tasks[0][1].task_id is None


def test_two_views_in_table_section_are_valid_only_for_policy_two():
    second = task("message-sequence", read_question="谁在何时返回证据和最终结论？",
                  viewpoint="跨角色消息顺序", trigger="coordination", diagram_type="sequence")
    body = plan(2, [task(), second])
    parsed = ContentPlanInput.model_validate(body)
    assert len(parsed.sections[0].illustration_tasks) == 2
    assert parsed.sections[0].asset_decision == "table"
    legacy = plan(); legacy["sections"][0]["block_sequence"] += ["explanation", "diagram"]
    with pytest.raises(ValueError, match="不能重复"):
        ContentPlanInput.model_validate(legacy)


def test_new_blueprint_cannot_omit_decisions_or_grant_duplicate_views():
    with pytest.raises(ValueError, match="逐小节"):
        BlueprintSubmission.model_validate(blueprint(2, []))
    with pytest.raises(ValueError, match="同义"):
        BlueprintSubmission.model_validate(blueprint(2, [task(), task("renamed")]))
    with pytest.raises(ValueError, match="不一致"):
        changed = blueprint(2)
        changed["chapters"][0]["illustration_policy_revision"] = 1
        BlueprintSubmission.model_validate(changed)


def test_none_and_useful_omit_do_not_invent_native_calls():
    none = IllustrationTask(section_id="1.1", decision="none", alternative_rationale="参数表和短操作清单已能完成当前任务")
    assert none.production == "omit" and none.task_id is None
    useful = IllustrationTask.model_validate(task(decision="useful", production=None))
    assert useful.production == "omit"
    with pytest.raises(ValueError, match="必需图"):
        IllustrationTask.model_validate(task(production="omit"))


@pytest.mark.parametrize("before", [False, True])
def test_anchor_insertion_is_ordered_idempotent_and_refuses_changed_body(tmp_path, before):
    import yaml

    from knowledge_product_workflow.content_plan import submit_content_plan
    from knowledge_product_workflow.diagrams import DiagramSubmissionError, insert_diagram_reference
    from knowledge_product_workflow.illustration_tasks import paragraph_anchors

    second = task("dependency-details", read_question="局部索引在整体中承担哪些职责？", viewpoint="局部职责展开")
    w = approved_workspace(tmp_path, [task(), second])
    source = w.root/"runs/chapter-source.md"
    source.write_text("# 1 资料边界\n\n## 1.1 输入关系\n\n先分清原始资料和索引的依赖关系。\n\n下面开始分别解释组件的具体边界。\n", encoding="utf-8")
    anchors = paragraph_anchors(source.read_text(encoding="utf-8"))
    value = plan(2, [task(), second])
    for item in value["sections"][0]["illustration_tasks"]:
        item["before_anchor" if before else "after_anchor"] = anchors[1 if before else 0]["anchor"]
    supplied = w.root/"runs/plan.yaml"
    supplied.write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")
    submit_content_plan(w, 1, supplied)
    inputs = []
    for number, item in enumerate([task(), second]):
        path = w.root/"runs"/f"diagram-{number}.json"
        path.write_text(json.dumps({"diagram_id": item["task_id"], "task_id": item["task_id"],
                        "diagram_type": item["diagram_type"], "caption": f"关系图{number}",
                        "reader_conclusion": "依赖关系应与正文保持一致", "heading_anchor": "1.1 输入关系",
                        "nodes": [{"node_id":"input","label":"原始资料"},{"node_id":"index","label":"资料索引"}],
                        "edges":[{"source":"input","target":"index"}]}, ensure_ascii=False), encoding="utf-8")
        inputs.append((item["task_id"], path))
    # Reverse call order must not reverse the approved reading order.
    for name, path in reversed(inputs):
        insert_diagram_reference(w, 1, source, name, path)
    original = source.read_bytes()
    for name, path in inputs:
        insert_diagram_reference(w, 1, source, name, path)
    assert source.read_bytes() == original
    text = source.read_text(encoding="utf-8")
    assert text.index("dependency-map.pdf") < text.index("dependency-details.pdf")
    assert text.index("dependency-details.pdf") < text.index("下面开始")
    source.write_text(text.replace("先分清原始资料和索引的依赖关系。", "此处正文已修改，旧锚点不再适用。") if not before else text.replace("下面开始分别解释组件的具体边界。", "此处正文已修改，旧锚点不再适用。"), encoding="utf-8")
    changed = source.read_bytes()
    with pytest.raises(DiagramSubmissionError, match="锚点"):
        insert_diagram_reference(w, 1, source, *inputs[0])
    assert source.read_bytes() == changed


def test_code_blocks_and_ambiguous_paragraphs_are_not_split_by_anchors():
    from knowledge_product_workflow.illustration_tasks import paragraph_anchors
    body = "## 1.1 输入关系\n\n重复段落。\n\n重复段落。\n\n```python\n# 2.1 不是标题\nprint('hello')\n```\n\n正文。\n"
    anchors = paragraph_anchors(body)
    assert len(anchors) == 2
    assert anchors[0]["preview"] == "```python"
    assert body[anchors[0]["start"]:anchors[0]["end"]].strip().endswith("```")


def test_budget_seven_tasks_in_one_chapter_is_derived_from_approved_blueprint(tmp_path):
    from knowledge_product_workflow.illustration_budget import (
        freeze_image_budget,
        image_budget_findings,
        load_image_budget,
        record_image_call,
        reserve_image,
    )
    tasks = [task(f"task-{i}", backend="host-image", read_question=f"组件{i}的特定依赖关系是什么？",
                  viewpoint=f"组件{i}的依赖视角") for i in range(7)]
    w = approved_workspace(tmp_path, tasks)
    budget = freeze_image_budget(w, approved_by="测试用户")
    assert budget.illustration_policy_revision == 2 and budget.authorized_total_calls == 14
    assert "book_slots" not in budget.model_dump() and not budget.slots
    for i, item in enumerate(tasks):
        call, hit = reserve_image(w, diagram_id=item["task_id"], chapter_number=1,
                                  semantic_sha256=f"semantic-{i}", cache_key=f"cache-{i}",
                                  prompt_sha256="a"*64, task_id=item["task_id"])
        assert not hit
        record_image_call(w, call.call_id, state="dispatched")
        record_image_call(w, call.call_id, state="failed")
    assert len(load_image_budget(w).slots) == 7
    assert not image_budget_findings(w)
    with pytest.raises(ValueError, match="未获"):
        reserve_image(w, diagram_id="unapproved", chapter_number=1, semantic_sha256="new",
                      cache_key="new", prompt_sha256="b"*64, task_id="unapproved")
    call, _ = reserve_image(w, diagram_id="renamed", chapter_number=1, semantic_sha256="changed",
                            cache_key="new-color", prompt_sha256="b"*64, task_id="task-0")
    record_image_call(w, call.call_id, state="dispatched")
    record_image_call(w, call.call_id, state="failed")
    with pytest.raises(ValueError, match="两次"):
        reserve_image(w, diagram_id="another-name", chapter_number=1, semantic_sha256="another",
                      cache_key="different", prompt_sha256="c"*64, task_id="task-0")


def test_vector_only_policy_two_has_zero_image_authorization(tmp_path):
    from knowledge_product_workflow.illustration_budget import freeze_image_budget, reserve_image
    w = approved_workspace(tmp_path, [task()])
    budget = freeze_image_budget(w, approved_by="测试用户")
    assert budget.authorized_total_calls == 0 and budget.approved_tasks == {} and budget.slots == []
    with pytest.raises(ValueError, match="未获"):
        reserve_image(w, diagram_id="dependency-map", chapter_number=1, semantic_sha256="a",
                      cache_key="a", prompt_sha256="a"*64, task_id="dependency-map")


def revise_blueprint(w, tasks):
    import yaml

    from knowledge_product_workflow.frontmatter import (
        parse_markdown_artifact,
        render_markdown_artifact,
    )
    from knowledge_product_workflow.models import ArtifactStatus
    from knowledge_product_workflow.state_machine import transition
    from knowledge_product_workflow.workflows.curriculum import amend_curriculum_blueprint
    source = w.root/"runs/blueprint.yaml"
    value = yaml.safe_load(source.read_text(encoding="utf-8"))
    value["chapters"][0]["illustration_tasks"] = tasks
    source.write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")
    path = amend_curriculum_blueprint(w, source)
    meta, body = parse_markdown_artifact(path.read_text(encoding="utf-8"))
    path.write_text(render_markdown_artifact(transition(meta, ArtifactStatus.APPROVED, approved_by="测试用户"), body), encoding="utf-8")


def test_budget_revision_keeps_retired_spending_and_stale_reservation_cannot_dispatch(tmp_path):
    from knowledge_product_workflow.illustration_budget import (
        freeze_image_budget,
        load_image_budget,
        record_image_call,
        reserve_image,
    )
    w = approved_workspace(tmp_path, [task("original", backend="host-image")])
    freeze_image_budget(w, approved_by="测试用户")
    call, _ = reserve_image(w, diagram_id="original", task_id="original", chapter_number=1,
                            semantic_sha256="old", cache_key="old", prompt_sha256="a"*64)
    record_image_call(w, call.call_id, state="dispatched")
    record_image_call(w, call.call_id, state="failed")
    new = [task(f"new-{i}", backend="host-image", read_question=f"组件{i}的新增边界如何识别？", viewpoint=f"新的视角{i}") for i in range(2)]
    revise_blueprint(w, new)
    with pytest.raises(ValueError, match="过期"):
        reserve_image(w, diagram_id="new-0", task_id="new-0", chapter_number=1,
                      semantic_sha256="new", cache_key="new", prompt_sha256="a"*64)
    revised = freeze_image_budget(w, approved_by="测试用户")
    assert revised.authorized_total_calls == 5
    assert revised.slots[0].calls[0].call_id == call.call_id and revised.slots[0].calls[0].state == "failed"
    pending, _ = reserve_image(w, diagram_id="new-0", task_id="new-0", chapter_number=1,
                               semantic_sha256="new", cache_key="new", prompt_sha256="a"*64)
    revise_blueprint(w, [task()])
    with pytest.raises(ValueError, match="过期"):
        record_image_call(w, pending.call_id, state="dispatched")
    record_image_call(w, pending.call_id, state="cancelled")
    final = freeze_image_budget(w, approved_by="测试用户")
    assert final.authorized_total_calls == 1 and final.approved_tasks == {}
    assert len(load_image_budget(w).slots) == 2


def test_vector_only_revision_two_does_not_require_ai_anchor_even_with_old_image(tmp_path):
    from knowledge_product_workflow.pdf_visual import _current_style_anchor
    from tests.test_illustration_v2 import v2_workspace
    w = v2_workspace(tmp_path, (248, 229, 216))
    # The fixture contains an old host-image manifest; only current references count.
    source = w.root/"delivery/book.qmd"
    source.write_text("# 本书无AI图\n", encoding="utf-8")
    assert _current_style_anchor(w) is None
    source.write_text("![fixture](assets/figures/fixture.pdf)", encoding="utf-8")
    assert _current_style_anchor(w) is not None
    manifest_path = w.root/"runs/diagrams/fixture.json"
    value = json.loads(manifest_path.read_bytes()); value["renderer"] = "svg-template"
    manifest_path.write_text(json.dumps(value), encoding="utf-8")
    assert _current_style_anchor(w) is None


def test_approval_metadata_does_not_change_new_policy_visual_cache(tmp_path, monkeypatch):
    from knowledge_product_workflow.diagrams import DiagramInput
    from knowledge_product_workflow.illustrations import style_binding, style_resources
    from tests.test_illustration_v2 import v2_workspace
    w = v2_workspace(tmp_path, (248, 229, 216))
    semantic = DiagramInput.model_validate_json((w.root/"runs/diagrams/fixture.input.json").read_bytes())
    semantic.task_id = "dependency-map"
    first = style_binding(w, semantic=semantic)
    resource = style_resources(2); resource["approval_state"] = "approved"
    monkeypatch.setattr("knowledge_product_workflow.illustrations.style_resources", lambda revision: resource)
    assert style_binding(w, semantic=semantic) == first


@pytest.mark.requires_svg_tools
def test_policy_two_vector_assets_render_and_bind_to_the_same_frozen_source(tmp_path):
    import yaml

    from knowledge_product_workflow.content_plan import submit_content_plan
    from knowledge_product_workflow.diagrams import (
        insert_diagram_reference,
        submit_diagram,
        validate_current_diagram_assets,
    )
    from knowledge_product_workflow.illustration_tasks import paragraph_anchors
    from knowledge_product_workflow.presentation import publication_profile_path

    second = task("local-details", read_question="每个局部组件的具体职责如何划分？", viewpoint="局部职责结构")
    w = approved_workspace(tmp_path, [task(), second])
    profile_path = publication_profile_path(w)
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    profile.update(illustration_style_revision=2)
    profile_path.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    source = w.root/"runs/chapter-source.md"
    source.write_text("# 1 资料边界\n\n## 1.1 输入关系\n\n先建立资料和索引的整体关系。\n\n再看局部职责，这个视角强调每个组件的作用。\n\n| 项目 | 边界 |\n|---|---|\n| 原始资料 | 保留原文 |\n| 索引 | 定位内容 |\n", encoding="utf-8")
    anchors = paragraph_anchors(source.read_text(encoding="utf-8"))
    value = plan(2, [task(), second])
    for item, anchor in zip(value["sections"][0]["illustration_tasks"], anchors[:2], strict=True):
        item["after_anchor"] = anchor["anchor"]
    supplied = w.root/"runs/plan.yaml";supplied.write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")
    submit_content_plan(w, 1, supplied)
    inputs = []
    for item in [task(), second]:
        path = w.root/"runs"/f'{item["task_id"]}.json'
        path.write_text(json.dumps({"diagram_id":item["task_id"],"task_id":item["task_id"],
                                   "diagram_type":"layered_architecture","heading_anchor":"1.1 输入关系",
                                   "caption":item["viewpoint"],"reader_conclusion":"索引定位原文，结论必须保留出处",
                                   "nodes":[{"node_id":"raw","label":"原始资料"},{"node_id":"index","label":"资料索引"}],
                                   "edges":[{"source":"raw","target":"index"}],
                                   "presentation":{"scheme":"warm-focus","node_colors":{"raw":"peach","index":"sand"},"group_colors":{},"concept_nodes":{}}}, ensure_ascii=False), encoding="utf-8")
        insert_diagram_reference(w, 1, source, item["task_id"], path)
        inputs.append(path)
    for path in inputs:
        pdf, manifest = submit_diagram(w, 1, source, path)
        assert pdf.is_file() and manifest.task_id is not None and manifest.renderer == "svg-template"
        from knowledge_product_workflow.illustrations import (
            record_asset_style,
        )
        from tests.test_illustration_style import review
        record_asset_style(w, review(diagram_id=manifest.diagram_id, measurement_source="vector",
                                    reviewed_by="隔离测试模拟观察，不代表真实用户认可"))
    body = source.read_text(encoding="utf-8").strip()+"\n"
    findings = validate_current_diagram_assets(w, w.root/"chapters/chapter-01.md", body)
    assert not [item for item in findings if item.severity.value == "blocker"]


def test_process_overview_preflight_uses_finite_grammar_and_stable_task_budget(tmp_path, monkeypatch):
    import yaml

    from knowledge_product_workflow.diagrams import DiagramInput
    from knowledge_product_workflow.illustration_budget import freeze_image_budget
    from knowledge_product_workflow.illustrations import preflight_illustration
    from knowledge_product_workflow.presentation import publication_profile_path

    item = task("hidden-process", backend="host-image", diagram_type="flow", trigger="hidden_mechanism", direction="right")
    w = approved_workspace(tmp_path, [item])
    path = publication_profile_path(w)
    profile = yaml.safe_load(path.read_text(encoding="utf-8"))
    profile.update(illustration_style_revision=2, illustration_backend_preference="host-image")
    path.write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    from knowledge_product_workflow.content_plan import submit_content_plan
    value = plan(2, [item])
    supplied = w.root/"runs/plan.yaml";supplied.write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")
    submit_content_plan(w, 1, supplied)
    freeze_image_budget(w, approved_by="测试用户")
    graph = DiagramInput(diagram_id="hidden-process", task_id="hidden-process", diagram_type="flow",
                         backend="host-image", caption="一次调用的内部阶段", reader_conclusion="阶段按冻结输入输出工作",
                         heading_anchor="1.1 输入关系", direction="right",
                         nodes=[{"node_id":name,"label":label} for name,label in [("a","接收输入"),("b","定位资料"),("c","保留证据")]],
                         edges=[{"source":"a","target":"b"},{"source":"b","target":"c"}],
                         admission={"family":"flow","information_form":"process_overview",
                                    "reader_job":value["sections"][0]["reader_job"],"visual_gain":"同时看到隐藏的处理阶段与各自职责",
                                    "evidence_boundary":"仅表示本文离线示例定义的处理关系"},
                         presentation={"scheme":"warm-focus","node_colors":{"a":"peach","b":"sand","c":"brand"},"group_colors":{},"concept_nodes":{}})
    from pathlib import Path
    monkeypatch.setattr("knowledge_product_workflow.d2.resolve_diagram_browser", lambda:type("Browser",(),{"available":True,"path":Path("browser.exe")})())
    monkeypatch.setattr("knowledge_product_workflow.d2.render_svg_with_browser", lambda browser,svg,pdf,png:(pdf.write_bytes(b'fixture-pdf'),png.write_bytes(b'fixture-card')))
    result = preflight_illustration(w, 1, graph, supports_references=True, reference_capacity=3)
    assert result["cache_hit"] is False and len(result["references"])==2
    prompt = Path(result["prompt"]).read_text(encoding="utf-8")
    assert "阶段卡片" in prompt and "不放条件菱形" in prompt
    from knowledge_product_workflow.illustration_budget import load_image_budget
    assert load_image_budget(w).slots[0].task_id == "hidden-process"
    graph.edges = [graph.edges[0]]
    with pytest.raises(ValueError, match="阶段链"):
        preflight_illustration(w, 1, graph, supports_references=True, reference_capacity=3)


def test_failed_sample_stops_other_native_tasks_without_spending_a_new_call(tmp_path):
    from knowledge_product_workflow.illustration_budget import (
        freeze_image_budget,
        load_image_budget,
        record_image_call,
        reserve_image,
    )
    from knowledge_product_workflow.illustrations import require_verified_sample
    second = task("next", backend="host-image", read_question="另一个组件的依赖关系如何识别？", viewpoint="另一个依赖视角")
    w = approved_workspace(tmp_path, [task("sample", backend="host-image"), second])
    freeze_image_budget(w, approved_by="测试用户")
    for index in range(2):
        call, _ = reserve_image(w, diagram_id="sample", task_id="sample", chapter_number=1,
                                semantic_sha256="sample", cache_key=f"sample-{index}", prompt_sha256="a"*64)
        record_image_call(w, call.call_id, state="dispatched")
        record_image_call(w, call.call_id, state="failed")
    with pytest.raises(ValueError, match="样图尚未合格"):
        require_verified_sample(w, "next")
    assert len(load_image_budget(w).slots) == 1
