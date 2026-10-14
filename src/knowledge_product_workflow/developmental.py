"""Deterministic developmental editing checks for structured reader tasks."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_product_workflow.content_plan import (
    ContentPlanError,
    content_plan_path,
    require_current_content_plan,
)
from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.humanize import protected_fragments_sha256, fenced_code_regions
from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract
from knowledge_product_workflow.workspace import Workspace

_HEADING = re.compile(r"^#{2,6}\s+(?P<title>.+?)\s*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_IMAGE = re.compile(r"!\[[^\]]*\]\(assets/")
_FENCE = re.compile(r"^\s*```")
_COMPONENT = re.compile(r"^\s*:::\s*\{\.(?P<name>kp-[a-z-]+)")


class DevelopmentalEditError(ValueError):
    """Raised when a v5 chapter lacks a current developmental editing report."""


class DevelopmentalEditReport(BaseModel):
    """A version-bound structural editing proof; it never claims authorship quality."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    rule_set: str = "kp-developmental-editor-v1"
    chapter_number: int = Field(gt=0)
    chapter_body_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    protected_fragments_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    findings: list[QualityFinding] = Field(default_factory=list)
    diagram_semantics_sha256: str | None = None
    structure_review_sha256: str | None = None


TEACHING_CHECKS = {
    "learning_increment": "本节新增的理解、判断或动作及其依据",
    "representation_fit": "表达形式与信息关系是否适配，保留必要因果/推导",
    "complementarity": "图表、例子与文字各自提供什么信息",
    "repetition_value": "重复案例是否新增条件/任务或支持独立操作",
    "actionability": "所承诺的操作或判断能否实施并核对",
    "reading_hierarchy": "核心问题、重点、例外与下一步是否易于定位",
}


class TeachingCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessment: Literal["passed", "revise", "uncertain", "not_applicable"]
    note: str = Field(min_length=12, max_length=600)

    @field_validator("note")
    @classmethod
    def concrete_note(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 12 or "请实际审阅" in value:
            raise ValueError("六项检查必须提供具体依据，不接受空白或模板占位")
        return value


class AssertionCheck(BaseModel):
    """审阅者声明的语义判断；字段合法不等于主张已被机器证明。"""
    model_config = ConfigDict(extra='forbid')
    assertion_id: str = Field(min_length=1)
    quote: str = Field(min_length=1, max_length=8000)
    assessment: Literal['supported', 'unsupported', 'uncertain']
    support_refs: list[str] = Field(default_factory=list)
    missing_premises: list[str] = Field(default_factory=list)
    counterexample: str | None = None
    note: str = Field(min_length=12, max_length=1200)
    input_status: Literal['sufficient', 'missing', 'uncertain'] | None = None
    inputs: list[str] = Field(default_factory=list)
    operation: str | None = None
    observed_output: str | None = None
    scope_limit: str | None = None
    counterexample_status: Literal['none_found', 'found', 'uncertain'] | None = None
    evidence_kind: Literal['deduction', 'actual_execution', 'diagram_semantics', 'source'] | None = None
    required_relations: list[tuple[str, str]] = Field(default_factory=list)


class StructureObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_id: str
    assessment: Literal["passed", "revise", "uncertain"]
    note: str = Field(min_length=12, max_length=1200)
    teaching_checks: dict[str, TeachingCheck] | None = None
    compared_unit_ids: list[str] = Field(default_factory=list)
    assertion_checks: list[AssertionCheck] = Field(default_factory=list)


def _rubric_revision(workspace: Workspace) -> int:
    from knowledge_product_workflow.runtime import _read_orchestration_state
    state = _read_orchestration_state(workspace)
    return state.reading_review_rubric_revision if state else 1


def _premise_audit_required(workspace: Workspace) -> bool:
    from knowledge_product_workflow.runtime import _read_orchestration_state
    state = _read_orchestration_state(workspace)
    return bool(state and state.request_capture_revision >= 1 and state.reading_review_rubric_revision >= 3)


def book_reading_index(workspace: Workspace, chapter_number: int, body: str) -> list[dict]:
    """Derived locations/jobs, not another content or fact source."""
    bodies = {}
    for path in sorted((workspace.root / "chapters").glob("chapter-*.md")):
        match = re.fullmatch(r"chapter-(\d+)\.md", path.name)
        if match:
            bodies[int(match.group(1))] = _reader_body(path)
    bodies[chapter_number] = body
    result = []
    for number, text in sorted(bodies.items()):
        try:
            plan = require_current_content_plan(workspace, number)
            sections = {section.section_id: section for section in plan.sections} if plan else {}
        except ContentPlanError:
            sections = {}
        titles = {match.group(1): match.group(2) for match in
                  re.finditer(r"^#{2,6}\s+(\d+(?:\.\d+)+)\s+([^\n]+)$", text, re.MULTILINE)}
        result.extend({"unit_id": unit["unit_id"], "chapter_number": number,
                       "title": titles.get(unit["unit_id"], ""),
                       "reader_job": sections[unit["unit_id"]].reader_job if unit["unit_id"] in sections else None,
                       "unit_sha256": unit["unit_sha256"], "start_line": unit["start_line"],
                       "comparison_sha256": _sha256_text(json.dumps({
                           "unit": unit["unit_sha256"],
                           "task": sections[unit["unit_id"]].model_dump(mode="json")
                           if unit["unit_id"] in sections else None}, sort_keys=True)),
                       "source": f"chapters/chapter-{number:02d}.md"}
                      for unit in reading_units(text))
    return result


def _validate_teaching_checks(observation: StructureObservation, revision: int) -> None:
    if revision < 2:
        return
    checks = observation.teaching_checks
    if checks is None or set(checks) != set(TEACHING_CHECKS):
        raise DevelopmentalEditError("新Rubric必须恰好覆盖六项教学检查")
    if any(checks[key].assessment == "not_applicable" for key in ("learning_increment", "reading_hierarchy")):
        raise DevelopmentalEditError("信息增量与阅读层次不能标为不适用")
    statuses = {item.assessment for item in checks.values()}
    overall = "revise" if "revise" in statuses else "uncertain" if "uncertain" in statuses else "passed"
    if observation.assessment != overall:
        negative_checks = {key: checks[key].assessment for key in TEACHING_CHECKS
                           if checks[key].assessment in {"revise", "uncertain"}}
        raise DevelopmentalEditError(
            "总体判断必须与六项教学检查一致："
            f"unit_id={observation.unit_id}；提交assessment={observation.assessment}；"
            f"期望assessment={overall}；"
            f"负项名称/状态={json.dumps(negative_checks, ensure_ascii=False)}"
        )


def _assertion_prompts(body: str) -> list[dict]:
    """定位关键命题，不确定性与真伪由实际审稿判断；不枚举全部自然语言。"""
    units = reading_units(body)
    visible = _reference_text(body).splitlines()
    original = body.splitlines()
    pattern = re.compile(r'(?:运行|执行|计算).{0,24}(?:得到|输出|结果)|真实(?:案例|事件)|'
        r'\d+(?:\.\d+)?%|(?:得分|分数|数值|结果).{0,16}(?:为|等于|变为|趋近|接近)|'
        r'断定|必然|一定|数学值|余弦.{0,12}(?:不变|为0|为 0)|相似.{0,12}证据不足|'
        r'图.{0,24}(?:关系|流程|连接|依赖)|(?:总是|保证|能够|可以).{0,20}(?:减少|避免|提高|降低)')
    result = []
    for line_number, line in enumerate(visible, 1):
        if not pattern.search(line):
            continue
        current = next((unit for unit in reversed(units) if unit['start_line'] <= line_number), None)
        if current is None:
            continue
        quote = original[line_number-1].strip()
        identifier = current['unit_id'] + ':' + _sha256_text(quote)[:16]
        if not any(item['assertion_id'] == identifier for item in result):
            result.append({'assertion_id':identifier, 'unit_id':current['unit_id'],
                           'line':line_number, 'quote':quote,
                           'question':'逐项定位输入、操作、实际输出和适用边界；先找缺失前提与反例，再判断支撑是否充分。实跑范围和图中关系须与主张一致。'})
    return result


def _assertion_support_context(workspace: Workspace, body: str) -> list[dict]:
    from knowledge_product_workflow.workflows.product_brief import read_request_contract
    result = [{'ref':'contract', 'kind':'chapter_contract'}, {'ref':'content_plan', 'kind':'content_plan'}]
    units = reading_units(body)
    body_sha256 = _sha256_text(body)
    for index, unit in enumerate(units):
        end_line = units[index + 1]['start_line'] - 1 if index + 1 < len(units) else len(body.splitlines())
        result.append({'ref': f"body_unit:{unit['unit_id']}",
                       'kind': 'author_scenario_and_derivation_context',
                       'unit_id': unit['unit_id'], 'start_line': unit['start_line'],
                       'end_line': end_line, 'unit_sha256': unit['unit_sha256'],
                       'chapter_body_sha256': body_sha256,
                       'evidence_scope': '只定位作者情景前提、给定条件与待独立核验的推导上下文；'
                                         '不是外部事实或实际执行证据；命题原话不能作为自身证明。',
                       'review_requirement': '审核note须指出前提、演算依据或反例；实际运行主张仍需实跑证据，'
                                             '外部事实仍须核验真实来源。'})
    contract = read_request_contract(workspace) or {}
    fixed = contract.get('requirements', {}).get('fixed_inputs', {}).get('value')
    if fixed:
        result.append({'ref':'reader_request.fixed_inputs', 'kind':'fixed_inputs', 'value':fixed})
    from knowledge_product_workflow.example_checks import example_evidence_for_body
    for index, evidence in enumerate(example_evidence_for_body(workspace, body)):
        result.append({'ref':f'example:{index}', 'kind':'actual_execution', 'value':evidence})
    from knowledge_product_workflow.diagrams import DiagramInput
    for identifier in sorted(set(re.findall(r'!\[[^\]]*\]\(assets/figures/([a-z0-9-]+)\.(?:pdf|png)\)', body))):
        path = workspace.root / "runs/diagrams" / f"{identifier}.input.json"
        if path.is_file():
            semantic = DiagramInput.model_validate_json(path.read_text(encoding="utf-8"))
            result.append({'ref': f'diagram:{identifier}', 'kind': 'controlled_diagram_semantics',
                           'sha256': _sha256_file(path), 'value': semantic.model_dump(mode="json"),
                           'evidence_scope': '节点、边、标题与结论的当前语义输入；不证明成页视觉或外部事实。'})
    return result


def _validate_assertion_checks(workspace: Workspace, observation: StructureObservation, body: str, revision: int) -> None:
    if revision < 3:
        return
    expected = {item['assertion_id']:item for item in _assertion_prompts(body) if item['unit_id']==observation.unit_id}
    checks = observation.assertion_checks
    if len(checks) != len(expected) or {item.assertion_id for item in checks} != set(expected):
        raise DevelopmentalEditError('关键命题检查必须覆盖当前模板，不得漏项、重复或添加不存在的命题')
    context = {item['ref']: item for item in _assertion_support_context(workspace, body)}
    allowed = set(context)
    for item in checks:
        if item.quote != expected[item.assertion_id]['quote']:
            raise DevelopmentalEditError('命题引文与当前正文不匹配')
        if not set(item.support_refs).issubset(allowed):
            raise DevelopmentalEditError('命题支撑引用不在当前输入/实际结果索引中：'
                                         f'unit_id={observation.unit_id}；'
                                         f'未知refs={sorted(set(item.support_refs) - allowed)}')
        if item.assessment == 'supported' and (not item.support_refs or item.missing_premises):
            raise DevelopmentalEditError('supported命题必须有当前支撑引用，不能仍缺必要前提')
        if item.assessment != 'supported' and not item.missing_premises:
            raise DevelopmentalEditError('未支持或不确定命题须记录缺失前提/未满足条件')
        if _premise_audit_required(workspace):
            if (item.input_status is None or item.counterexample_status is None or item.evidence_kind is None
                    or any(not value or not value.strip() for value in
                           (item.operation, item.observed_output, item.scope_limit))):
                raise DevelopmentalEditError('当前命题须明确输入前提、操作、输出、适用边界与反例检查')
            if item.assessment == 'supported':
                if (item.input_status != 'sufficient' or not item.inputs
                        or any(not value.strip() or value.strip() == item.quote.strip() for value in item.inputs)
                        or item.counterexample_status != 'none_found'):
                    raise DevelopmentalEditError('supported命题的输入前提不充分或仍存在反例/未知')
                if item.evidence_kind == 'actual_execution':
                    executions = [context[ref]['value'] for ref in item.support_refs
                                  if context[ref]['kind'] == 'actual_execution']
                    wanted = item.observed_output.strip('\r\n').splitlines()
                    if not wanted or not any(data.get('status') == 'passed' and data.get('exit_code') == 0
                               and any(data.get('stdout', '').splitlines()[start:start+len(wanted)] == wanted
                                       for start in range(len(data.get('stdout', '').splitlines())))
                               for data in executions):
                        raise DevelopmentalEditError('实际运行输出必须来自当前成功执行记录；计划/正文不能代替实跑')
                if item.evidence_kind == 'diagram_semantics' and not any(
                        context[ref]['kind'] == 'controlled_diagram_semantics' for ref in item.support_refs):
                    raise DevelopmentalEditError('图义命题必须核对当前节点与关系输入')
                if item.evidence_kind == 'diagram_semantics':
                    edges = {(edge['source'], edge['target']) for ref in item.support_refs
                             if context[ref]['kind'] == 'controlled_diagram_semantics'
                             for edge in context[ref]['value']['edges']}
                    if not item.required_relations or not set(item.required_relations).issubset(edges):
                        raise DevelopmentalEditError('图义命题声明的关系未被当前实际边覆盖')
    if any(item.assessment=='unsupported' for item in checks) and observation.assessment!='revise':
        raise DevelopmentalEditError('unsupported命题必须聚合为revise')
    if any(item.assessment=='uncertain' for item in checks) and observation.assessment=='passed':
        raise DevelopmentalEditError('uncertain命题不能聚合为passed')


def _critical_reader_task(section, rubric_revision: int) -> bool:
    if section is None:
        return False
    if getattr(section, "artifact", "none") != "none" or getattr(section, "asset_decision", "none") != "none":
        return True
    kinds = {"procedure", "exercise"}
    if rubric_revision >= 2:
        kinds |= {"example", "comparison", "decision", "worksheet", "table", "diagram"}
        return bool(kinds.intersection(section.block_sequence))
    return bool(kinds.intersection(section.block_sequence) or
                re.search(r"编写|调用|验收|核对|成功判断|输出", section.reader_job))


class StructureReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_id: str
    observations: list[StructureObservation]
    reuse_unit_ids: list[str] = Field(default_factory=list)
    reviewer_identity: str | None = Field(default=None, min_length=1, max_length=200)
    review_context_kind: Literal["author_separate_context", "self_review", "manual", "unspecified"] = "unspecified"


def structure_review_required(workspace: Workspace) -> bool:
    from knowledge_product_workflow.runtime import _read_orchestration_state

    state = _read_orchestration_state(workspace)
    if state is None or state.reading_structure_revision < 2:
        return False
    import yaml

    from knowledge_product_workflow.presentation import (
        PRESENTATION_PROFILE_ID,
        publication_profile_path,
    )

    path = publication_profile_path(workspace)
    if not path.is_file():
        return False
    profile = yaml.safe_load(path.read_text(encoding="utf-8"))
    return profile.get("profile_id") == PRESENTATION_PROFILE_ID and profile.get("reader_layout_revision") == 2


def structure_review_path(workspace: Workspace, chapter_number: int) -> Path:
    return workspace.root / "runs/editorial" / f"chapter-{chapter_number:02d}-structure-review.json"


def reading_policy_preview(workspace: Workspace) -> dict:
    from knowledge_product_workflow.runtime import _read_orchestration_state
    state = _read_orchestration_state(workspace)
    return {"current_revision": state.reading_structure_revision if state else None,
            "current_rubric_revision": state.reading_review_rubric_revision if state else None,
            "target_rubric_revision": max(2, state.reading_review_rubric_revision) if state else 2,
            "target_revision": 2, "managed": state is not None,
            "affected_chapters": [p.relative_to(workspace.root).as_posix()
                                  for p in sorted((workspace.root / "chapters").glob("chapter-*-contract.md"))],
            "effect": "旧正文和交付物保留；采用后必要结构审核缺失或过期会阻断修订交付。"}


def adopt_reading_policy(workspace: Workspace) -> dict:
    from knowledge_product_workflow.runtime import (
        _atomic_json_write,
        _orchestration_state_path,
        _read_orchestration_state,
        supersede_orchestration_work_orders,
    )
    state = _read_orchestration_state(workspace)
    if state is None:
        raise DevelopmentalEditError("先通过已确认的orchestration-plan --adopt-legacy纳入托管")
    if state.reading_structure_revision == 2 and state.reading_review_rubric_revision >= 2:
        return reading_policy_preview(workspace)
    import yaml

    from knowledge_product_workflow.presentation import PublicationProfile, publication_profile_path
    path = publication_profile_path(workspace)
    profile = PublicationProfile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))) if path.is_file() else None
    if profile is None or profile.reader_layout_revision != 2 or profile.profile_id != "knowledge-product-pdf-v6":
        raise DevelopmentalEditError("阅读政策2需要已确认的v6布局2；先通过出版更新确认布局，不静默换版")
    state = state.model_copy(update={"reading_structure_revision": 2, "reading_review_rubric_revision": 2})
    _atomic_json_write(_orchestration_state_path(workspace), state)
    supersede_orchestration_work_orders(workspace, "用户明确采用阅读结构政策2，刷新审核要求")
    workspace.append_event("reading_structure_policy_adopted", {"revision": 2})
    return reading_policy_preview(workspace)


def _scope_warnings(scope: str, first_line: int) -> list[dict]:
    runs, run, paragraph = [], [], []
    start = first_line
    fence = False
    ignored = False
    list_continuation = False

    def flush_paragraph():
        nonlocal paragraph
        if paragraph:
            run.append({"line": start, "text": " ".join(paragraph)})
            paragraph = []

    def flush_run():
        flush_paragraph()
        if run:
            runs.append(list(run))
            run.clear()

    for number, line in enumerate(scope.splitlines(), first_line):
        if _FENCE.match(line):
            flush_run()
            fence = not fence
            continue
        if fence:
            continue
        component = _COMPONENT.match(line)
        if component:
            flush_paragraph()
            ignored = component.group("name") in {"kp-source", "kp-chapter-lead"}
            if ignored:
                flush_run()
            continue
        if line.strip() == ":::":
            ignored = False
            continue
        if ignored:
            continue
        structured = (_TABLE_ROW.match(line) or _IMAGE.search(line)
                      or re.match(r"^\s*(?:[-+*]|\d+[.)])\s+", line))
        if structured or (list_continuation and line.startswith("  ")):
            flush_run()
            list_continuation = True
            continue
        if not line.strip():
            flush_paragraph()
            continue
        list_continuation = False
        if not paragraph:
            start = number
        paragraph.append(line.strip())
    flush_run()
    warnings = []
    for group in runs:
        lengths = [len(re.sub(r"\s|[`*_]", "", p["text"])) for p in group]
        if (any(n >= 200 for n in lengths) or sum(lengths) >= 420
                or (len(lengths) >= 3 and all(n >= 100 for n in lengths))):
            warnings.append({"kind": "density", "start_line": group[0]["line"],
                             "end_line": group[-1]["line"], "characters": lengths})
        if any(re.search(r"(?:[三四五六七八九十]|[3-9])(?:项|点|个)(?:检查|标准|条件|验收)?", p["text"]) for p in group):
            warnings.append({"kind": "enumeration", "start_line": group[0]["line"],
                             "end_line": group[-1]["line"]})
    return warnings


def _reference_text(body: str) -> str:
    """Only prose/link labels can declare dependencies; code/URLs are payload."""
    result, in_fence = [], False
    for line in body.splitlines(keepends=True):
        marker = bool(_FENCE.match(line))
        hidden = in_fence or marker
        if marker:
            in_fence = not in_fence
        result.append(re.sub(r"[^\r\n]", " ", line) if hidden else line)
    return re.sub(r"`+[^`\n]*`+|https?://\S+", " ", "".join(result))


def _section_references(text: str) -> set[str]:
    return set(re.findall(r"(?<![\d.])(\d+(?:\.\d+)+)\s*(?:节|小节)", _reference_text(text)))


def reading_units(body: str) -> list[dict]:
    """Index the permitted Markdown subset, not a general Markdown parser."""
    visible, in_fence = [], False
    for line in body.splitlines(keepends=True):
        marker = bool(_FENCE.match(line))
        hidden = in_fence or marker
        if marker:
            in_fence = not in_fence
        visible.append(re.sub(r"[^\r\n]", " ", line) if hidden else line)
    headings = list(re.finditer(r"^#{2,6}\s+(\d+(?:\.\d+)+)\s+[^\n]+$", "".join(visible), re.MULTILINE))
    units, scopes = [], {}
    for index, heading in enumerate(headings):
        end = headings[index+1].start() if index+1 < len(headings) else len(body)
        scope = body[heading.end():end]
        first_line = body[:heading.end()].count("\n") + 1
        warnings = _scope_warnings(scope, first_line)
        units.append({"unit_id": heading.group(1), "start_line": first_line,
                      "unit_sha256": _sha256_text(heading.group(0)+scope), "warnings": warnings})
        scopes[heading.group(1)] = scope
    prefix = _sha256_text(body[:headings[0].start()]) if headings else _sha256_text(body)
    hashes = {u["unit_id"]: u["unit_sha256"] for u in units}
    for index, unit in enumerate(units):
        references = sorted(_section_references(scopes[unit["unit_id"]]) & hashes.keys())
        context = [prefix, units[index-1]["unit_sha256"] if index else None,
                   unit["unit_sha256"], units[index+1]["unit_sha256"] if index+1 < len(units) else None,
                   [(reference, hashes[reference]) for reference in references]]
        unit["context_sha256"] = _sha256_text(json.dumps(context))
    return units


def _legacy_review_context_digest(workspace: Workspace, number: int, body: str, *, scoped: bool = True) -> str:
    from knowledge_product_workflow.claims import claim_scoped_evidence_digests
    from knowledge_product_workflow.diagrams import diagram_semantics_digest
    _, chapter = require_current_chapter_contract(workspace, number)
    claims = set(chapter.claim_ids)
    context = {} if scoped else {name: _sha256_file(workspace.root / "research" / name)
               if (workspace.root / "research" / name).is_file() else None
               for name in ("source-ledger.jsonl", "claim-classifications.jsonl", "assumption-ledger.jsonl")}
    referenced = {int(reference) for pair in re.findall(r"第\s*(\d+)\s*章|(?<![\d.])(\d+)\.\d+", body)
                  for reference in pair if reference}
    if re.search(r"上一章|前一章|前章", body) and number > 1:
        referenced.add(number-1)
    for chapter in sorted(referenced - {number}):
        path = workspace.root / "chapters" / f"chapter-{chapter:02d}.md"
        context[f"chapter-{chapter}"] = _sha256_text(_reader_body(path)) if path.is_file() else None
        contract = workspace.root / "chapters" / f"chapter-{chapter:02d}-contract.md"
        if contract.is_file():
            _, related = require_current_chapter_contract(workspace, chapter)
            claims.update(related.claim_ids)
    if scoped:
        context.update(claim_scoped_evidence_digests(workspace, claims))
    context["diagram_semantics"] = diagram_semantics_digest(workspace, body)
    return _sha256_text(json.dumps(context, sort_keys=True))


def review_dependencies(workspace: Workspace, number: int, body: str) -> list[dict]:
    """Navigation binds contracts; actual result/section references bind content."""
    text = _reference_text(body)
    sections = _section_references(text)
    result_chapters = {int(value.split('.')[0]) for value in sections}
    chapters = {int(m.group(1)) for m in re.finditer(r"第\s*(\d+)\s*章", text)}
    for match in re.finditer(r"第\s*(\d+)\s*章", text):
        before, after = text[max(0, match.start()-12):match.start()], text[match.end():match.end()+24]
        if re.search(r"(?:根据|依据|沿用|使用|对照)\s*$", before) or re.search(r"(?:的|给出的|中的)?(?:结果|结论|计算|公式|假设|数值|定义|条件)", after):
            result_chapters.add(int(match.group(1)))
    if re.search(r"上一章|前一章|前章", text) and number > 1:
        result_chapters.add(number-1)
    dependencies = []
    for chapter_number in sorted((chapters | result_chapters) - {number}):
        contract = workspace.root / "chapters" / f"chapter-{chapter_number:02d}-contract.md"
        dependencies.append({"kind": "contract", "chapter": chapter_number,
                             "sha256": _sha256_file(contract) if contract.is_file() else None})
        if chapter_number not in result_chapters:
            continue
        path = workspace.root / "chapters" / f"chapter-{chapter_number:02d}.md"
        related_body = _reader_body(path) if path.is_file() else ""
        referenced_units = {s for s in sections if int(s.split('.')[0]) == chapter_number}
        if referenced_units:
            units = {unit['unit_id']: unit['unit_sha256'] for unit in reading_units(related_body)}
            dependencies.extend({"kind": "section", "chapter": chapter_number, "unit": unit,
                                 "sha256": units.get(unit)} for unit in sorted(referenced_units))
        else:
            dependencies.append({"kind": "result", "chapter": chapter_number,
                                 "sha256": _sha256_text(related_body) if path.is_file() else None})
    return dependencies


def _review_context_digest(workspace: Workspace, number: int, body: str, *, scoped: bool = True) -> str:
    if not scoped:
        return _legacy_review_context_digest(workspace, number, body, scoped=False)
    from knowledge_product_workflow.claims import claim_scoped_evidence_digests
    from knowledge_product_workflow.diagrams import diagram_semantics_digest
    from knowledge_product_workflow.example_checks import example_evidence_for_body
    _, chapter = require_current_chapter_contract(workspace, number)
    claims = set(chapter.claim_ids)
    dependencies = review_dependencies(workspace, number, body)
    for dependency in dependencies:
        contract = workspace.root / "chapters" / f"chapter-{dependency['chapter']:02d}-contract.md"
        if dependency['kind'] != 'contract' and contract.is_file():
            _, related = require_current_chapter_contract(workspace, dependency['chapter'])
            claims.update(related.claim_ids)
    context = {"dependency_revision": 3, "references": dependencies,
               "claims": claim_scoped_evidence_digests(workspace, claims),
               "execution": [{key: data.get(key) for key in ("code_sha256", "stdout_sha256", "stderr_sha256", "exit_code", "status")}
                             for data in example_evidence_for_body(workspace, body)],
               "diagram_semantics": diagram_semantics_digest(workspace, body)}
    return _sha256_text(json.dumps(context, sort_keys=True))


def _review_context_current(workspace: Workspace, number: int, body: str, digest: str | None) -> bool:
    # An old full-ledger proof remains stronger than the scoped proof only while
    # every byte of its original context still matches. Never rewrite its hash.
    if digest == _review_context_digest(workspace, number, body):
        return True
    from knowledge_product_workflow.example_checks import example_evidence_for_body
    if example_evidence_for_body(workspace, body):
        return False
    return digest in {_legacy_review_context_digest(workspace, number, body),
                      _legacy_review_context_digest(workspace, number, body, scoped=False)}


def _reusable_observations(workspace: Workspace, number: int, binding: dict, units: list[dict], body: str) -> dict:
    path = structure_review_path(workspace, number)
    if not path.is_file():
        return {}
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
        if previous.get("reading_structure_revision") != 2 or any(
            previous.get(key, 1 if key == "rubric_revision" else None) != binding[key]
            for key in ("contract_sha256", "content_plan_sha256", "rubric_revision")
        ):
            return {}
        if ((previous.get("dependency_revision", 1) < 3
                or previous.get("scoped_context_sha256") != previous.get("review_context_sha256"))
                and previous.get("review_context_sha256") != binding["review_context_sha256"]
                and (previous.get("chapter_body_sha256") != binding["chapter_body_sha256"]
                     or not _review_context_current(workspace, number, body, previous.get("review_context_sha256")))):
            return {}
        old_units = {u["unit_id"]: u for u in previous["units"]}
        old_notes = {o["unit_id"]: StructureObservation.model_validate(o) for o in previous["observations"]}
        current_body_refs = {f"body_unit:{unit['unit_id']}": unit['unit_sha256'] for unit in units}
        body_refs_current = {}
        for observation in old_notes.values():
            _validate_teaching_checks(observation, binding["rubric_revision"])
            current = next((unit for unit in units if unit['unit_id']==observation.unit_id), None)
            if current and old_units.get(observation.unit_id, {}).get('unit_sha256') == current['unit_sha256']:
                _validate_assertion_checks(workspace, observation, body, binding['rubric_revision'])
            refs = {ref for check in observation.assertion_checks for ref in check.support_refs
                    if ref.startswith('body_unit:')} if binding['rubric_revision'] >= 3 else set()
            recorded_refs = previous.get('assertion_body_refs', {}).get(observation.unit_id, {})
            body_refs_current[observation.unit_id] = not refs or (
                refs.issubset(current_body_refs)
                and recorded_refs == {ref: current_body_refs[ref] for ref in refs})
        # A reviewed draft may not yet have been submitted to chapters/.
        index = {u["unit_id"]: u["comparison_sha256"] for u in book_reading_index(workspace, number, "")}
        plan = require_current_content_plan(workspace, number)
        sections = {s.section_id: s for s in plan.sections} if plan else {}
        index.update({u["unit_id"]: _sha256_text(json.dumps({"unit": u["unit_sha256"],
                      "task": sections[u["unit_id"]].model_dump(mode="json")
                      if u["unit_id"] in sections else None}, sort_keys=True)) for u in units})
        comparisons = previous.get("comparison_context", {})
        return {u["unit_id"]: old_notes[u["unit_id"]] for u in units
                if u["unit_id"] in old_notes and old_notes[u["unit_id"]].assessment != "revise"
                and body_refs_current[u['unit_id']]
                and all(comparisons.get(reference) == index.get(reference)
                        and reference in index for reference in old_notes[u["unit_id"]].compared_unit_ids)
                and old_units.get(u["unit_id"], {}).get("context_sha256") == u["context_sha256"]
                and (previous.get("dependency_revision", 1) < 3
                     or old_units.get(u["unit_id"], {}).get("dependency_sha256") == u["dependency_sha256"])}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def create_structure_review_template(workspace: Workspace, chapter_number: int, source_path: Path) -> dict:
    source = source_path.resolve()
    if not source.is_file() or not source.is_relative_to(workspace.root.resolve()):
        raise DevelopmentalEditError("结构审核源稿必须位于当前工作区")
    contract, _ = require_current_chapter_contract(workspace, chapter_number)
    plan = require_current_content_plan(workspace, chapter_number)
    if plan is None:
        raise DevelopmentalEditError("结构审核需要当前内容计划")
    body = _reader_body(source)
    plan_sections = {s.section_id: s for s in plan.sections}
    units = reading_units(body)
    for unit in units:
        section = plan_sections.get(unit["unit_id"])
        unit["required"] = (chapter_number == 1 or bool(unit["warnings"])
                            or _critical_reader_task(section, _rubric_revision(workspace)))
        lines = body.splitlines(keepends=True)
        start = unit["start_line"] - 1
        following = next((u["start_line"] - 1 for u in units if u["start_line"] > unit["start_line"]), len(lines))
        unit["dependency_sha256"] = _review_context_digest(workspace, chapter_number, "".join(lines[start:following]))
    binding = {"chapter_body_sha256": _sha256_text(body), "contract_sha256": _sha256_file(contract),
               "dependency_revision": 3,
               "rubric_revision": _rubric_revision(workspace),
               "content_plan_sha256": _sha256_file(content_plan_path(workspace, chapter_number)),
               "review_context_sha256": _review_context_digest(workspace, chapter_number, body)}
    binding["scoped_context_sha256"] = binding["review_context_sha256"]
    reusable = _reusable_observations(workspace, chapter_number, binding, units, body)
    index = book_reading_index(workspace, chapter_number, body)
    index_path = workspace.root / "runs/editorial/book-reading-index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    previous_sha = _sha256_file(structure_review_path(workspace, chapter_number)) if reusable else None
    from knowledge_product_workflow.example_checks import example_evidence_for_body
    factual_prompts = [{"line": line_number, "text": line.strip(), "question": "本主张的输入、成立条件、真实来源或实际执行输出是否齐备？缺少条件时保留未知，不补造数值或真实事件。"}
                       for line_number, line in enumerate(_reference_text(body).splitlines(), 1)
                       if re.search(r"(?:运行|执行|计算).{0,18}(?:得到|输出|结果)|真实(?:案例|事件)|\d+(?:\.\d+)?%|(?:得分|数值|结果).{0,10}(?:为|等于|变为|趋近)", line)]
    assertions = _assertion_prompts(body) if binding['rubric_revision'] >= 3 else []
    support_context = _assertion_support_context(workspace, body) if assertions else []
    fingerprint = _sha256_text(json.dumps({**binding, "reuse_evidence_sha256": previous_sha,
                                         "reading_index": index}, sort_keys=True))
    return {"template_id": fingerprint, "binding": binding, "units": units,
            "dependency_context": review_dependencies(workspace, chapter_number, body),
            "execution_evidence": example_evidence_for_body(workspace, body),
            "fact_check_prompts": factual_prompts,
            "assertion_prompts": assertions,
            "assertion_support_context": support_context,
            "assertion_audit_protocol": {"revision": 1, "required": _premise_audit_required(workspace),
                "required_fields": ["input_status", "inputs", "operation", "observed_output", "scope_limit",
                                    "counterexample_status", "evidence_kind"],
                "semantic_truth_machine_verified": False,
                "review_sequence": ["读取具体输入与当前代码/图关系", "寻找缺失前提和不满足条件的反例",
                                    "核实际输出及执行范围", "限定结论范围后判断supported/unsupported/uncertain"]},
            "review_questions": ["本单元相对已写内容新增了什么判断或操作？列具体比较单元与增量，不按段落或图数打分。",
                                 "图/表/代码是否互补，还是以不同形式重复同一结论？",
                                 "没有真实作者外上下文时明确审核局限；辅助自评不能宣称正式独立审稿。"],
            "teaching_rubric": TEACHING_CHECKS if binding["rubric_revision"] >= 2 else {},
            "book_reading_index": index,
            "reusable_unit_ids": [u["unit_id"] for u in units if u["required"] and u["unit_id"] in reusable],
            "reuse_evidence_sha256": previous_sha,
            "observations": [{"unit_id": u["unit_id"], "assessment": "uncertain",
                              "note": "请实际审阅信息关系、示例与成功判断后填写具体理由。"}
                             for u in units if u["required"]],
            "review_input": {"skill": "kp-developmental-editor", "action": "structure-review",
                                    "artifact_sha256": fingerprint, "chapter_number": chapter_number}}


def record_structure_review(workspace: Workspace, chapter_number: int, source_path: Path,
                            payload: StructureReviewInput) -> Path:
    template = create_structure_review_template(workspace, chapter_number, source_path)
    if payload.template_id != template["template_id"]:
        raise DevelopmentalEditError("结构审核模板已过期；重新取得当前模板")
    required = {u["unit_id"] for u in template["units"] if u["required"]}
    ids = [o.unit_id for o in payload.observations]
    reused = payload.reuse_unit_ids
    if (len(set(ids + reused)) != len(ids + reused) or set(ids + reused) != required
            or not set(reused).issubset(template["reusable_unit_ids"])):
        raise DevelopmentalEditError("结构观察必须恰好覆盖当前必审单元，不能遗漏或重复")
    if any("请实际审阅" in o.note for o in payload.observations):
        raise DevelopmentalEditError("不得提交模板占位观察")
    for observation in payload.observations:
        _validate_teaching_checks(observation, template["binding"]["rubric_revision"])
        _validate_assertion_checks(workspace, observation, _reader_body(source_path), template['binding']['rubric_revision'])
    output = structure_review_path(workspace, chapter_number)
    output.parent.mkdir(parents=True, exist_ok=True)
    cached = _reusable_observations(workspace, chapter_number, template["binding"], template["units"], _reader_body(source_path))
    if output.is_file():
        history = output.parent / "history" / f"chapter-{chapter_number:02d}"
        history.mkdir(parents=True, exist_ok=True)
        archived = history / f"structure-{_sha256_file(output)}.json"
        if not archived.exists():
            archived.write_bytes(output.read_bytes())
    observations = payload.observations + [cached[unit_id] for unit_id in reused]
    index = {u["unit_id"]: u["comparison_sha256"] for u in template["book_reading_index"]}
    references = {reference for o in observations for reference in o.compared_unit_ids}
    if not references.issubset(index):
        raise DevelopmentalEditError("比较单元不在当前阅读索引中，不得声称已比较不存在的章节")
    data = {"schema_version": 2, **template["binding"], "units": template["units"],
            "reviewer_context": {"identity": payload.reviewer_identity, "kind": payload.review_context_kind,
                                 "evidence_status": "declared; formal independent review uses the host adapter"},
            "reading_structure_revision": 2 if structure_review_required(workspace) else 1,
            "comparison_context": {reference: index[reference] for reference in sorted(references)},
            "reused_unit_ids": reused, "reuse_evidence_sha256": template["reuse_evidence_sha256"] if reused else None,
            "observations": [o.model_dump() for o in observations]}
    if template['binding']['rubric_revision'] >= 3:
        current_body_refs = {f"body_unit:{unit['unit_id']}": unit['unit_sha256'] for unit in template['units']}
        body_ref_bindings = {}
        for observation in observations:
            refs = {ref for check in observation.assertion_checks for ref in check.support_refs
                    if ref.startswith('body_unit:')}
            if refs:
                body_ref_bindings[observation.unit_id] = {ref: current_body_refs[ref] for ref in sorted(refs)}
        if body_ref_bindings:
            data['assertion_body_refs'] = body_ref_bindings
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    workspace.append_event("chapter_structure_reviewed", {"chapter_number": chapter_number})
    return output


def require_structure_review(workspace: Workspace, chapter_number: int, body: str) -> str | None:
    if not structure_review_required(workspace):
        return None
    path = structure_review_path(workspace, chapter_number)
    if not path.is_file():
        raise DevelopmentalEditError("缺少实际结构审核；运行developmental-review-template并提交观察")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as error:
        raise DevelopmentalEditError("结构审核证明无效") from error
    contract, _ = require_current_chapter_contract(workspace, chapter_number)
    expected = {"reading_structure_revision": 2, "rubric_revision": _rubric_revision(workspace),
                "chapter_body_sha256": _sha256_text(body), "contract_sha256": _sha256_file(contract),
                "content_plan_sha256": _sha256_file(content_plan_path(workspace, chapter_number))}
    changed = [key for key, value in expected.items()
               if data.get(key, 2 if key == "reading_structure_revision" else 1 if key == "rubric_revision" else None) != value]
    if "review_context_sha256" in data and not _review_context_current(workspace, chapter_number, body, data["review_context_sha256"]):
        changed.append("review_context_current（实质引用/来源/图义/执行证据）")
    if changed:
        raise DevelopmentalEditError("结构审核已过期；变化输入：" + "、".join(changed))
    observations = [StructureObservation.model_validate(o) for o in data.get("observations", [])]
    current_body_refs = {f"body_unit:{unit['unit_id']}": unit['unit_sha256'] for unit in reading_units(body)}
    for observation in observations:
        _validate_teaching_checks(observation, _rubric_revision(workspace))
        _validate_assertion_checks(workspace, observation, body, _rubric_revision(workspace))
        if _rubric_revision(workspace) >= 3:
            refs = {ref for check in observation.assertion_checks for ref in check.support_refs
                    if ref.startswith('body_unit:')}
            if refs and data.get('assertion_body_refs', {}).get(observation.unit_id, {}) != {
                ref: current_body_refs[ref] for ref in refs
            }:
                raise DevelopmentalEditError(f'正文前提引用指纹已过期或缺失：unit_id={observation.unit_id}')
    index = {u["unit_id"]: u["comparison_sha256"] for u in book_reading_index(workspace, chapter_number, body)}
    for reference, digest in data.get("comparison_context", {}).items():
        if index.get(reference) != digest:
            raise DevelopmentalEditError("结构审核已过期：实际比较的案例单元已变化")
    plan = require_current_content_plan(workspace, chapter_number)
    sections = {s.section_id: s for s in plan.sections} if plan else {}
    required = {unit["unit_id"] for unit in reading_units(body)
                if chapter_number == 1 or unit["warnings"] or
                _critical_reader_task(sections.get(unit["unit_id"]), _rubric_revision(workspace))}
    if {o.unit_id for o in observations} != required or any(o.assessment == "revise" for o in observations):
        raise DevelopmentalEditError("结构审核缺项或仍有待修订单元")
    return _sha256_file(path)


def developmental_report_path(workspace: Workspace, chapter_number: int) -> Path:
    return workspace.root / "runs" / "editorial" / f"chapter-{chapter_number:02d}-developmental-report.json"


def _sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _reader_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    try:
        _, body = parse_markdown_artifact(text)
    except ValueError:
        body = text
    return body.strip() + "\n"


def _paragraphs(body: str) -> list[tuple[int, str]]:
    paragraphs: list[tuple[int, str]] = []
    current: list[str] = []
    start_line = 0
    in_fence = False
    for line_number, line in enumerate(body.splitlines(), start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            if current:
                paragraphs.append((start_line, " ".join(current)))
                current, start_line = [], 0
            continue
        if in_fence or _HEADING.match(line) or _COMPONENT.match(line) or line.strip() == ":::":
            if current:
                paragraphs.append((start_line, " ".join(current)))
                current, start_line = [], 0
            continue
        if not line.strip():
            if current:
                paragraphs.append((start_line, " ".join(current)))
                current, start_line = [], 0
            continue
        if not current:
            start_line = line_number
        current.append(line.strip())
    if current:
        paragraphs.append((start_line, " ".join(current)))
    return paragraphs


def _body_capabilities(body: str) -> set[str]:
    capabilities: set[str] = set()
    if "::: {.kp-worksheet}" in body:
        capabilities.add("worksheet")
    if "::: {.kp-procedure}" in body:
        capabilities.add("procedure")
    if "::: {.kp-decision}" in body:
        capabilities.add("decision")
    if any(_TABLE_ROW.match(line) for line in body.splitlines()):
        capabilities.add("table")
    if _IMAGE.search(body):
        capabilities.add("diagram")
    if any(closed and any(re.sub(r"^ {0,3}(?:> ?)+", "", line).strip() for line in raw.splitlines()[1:-1])
           for _, _, raw, closed in fenced_code_regions(body)):
        capabilities.add("code")
    return capabilities


def _prose_artifact_candidate(scope: str) -> bool:
    """Presence only; the existing six teaching checks judge the actual promise."""
    from knowledge_product_workflow.content_quality import scan_markdown_content
    for start, end, _, _ in reversed(fenced_code_regions(scope)):
        scope = scope[:start] + "\n" + scope[end:]
    for paragraph in re.split(r"\n\s*\n", scope):
        lines = [line.strip() for line in paragraph.splitlines()
                 if line.strip() and not line.lstrip().startswith(("#", ":::" , "![", "|"))]
        if not lines:
            continue
        text = "\n".join(lines)
        if re.fullmatch(r"(?:下文|后文)会(?:给出|提供)[^\n]*", text):
            continue
        if any(f.severity is Severity.BLOCKER for f in scan_markdown_content(text, "reader-artifact")):
            continue
        return True
    return False


def _findings_for_current_plan(workspace: Workspace, chapter_number: int, body: str) -> tuple[str, list[QualityFinding]]:
    plan = require_current_content_plan(workspace, chapter_number)
    if plan is None:
        return "", []
    plan_sha = _sha256_file(content_plan_path(workspace, chapter_number))
    findings: list[QualityFinding] = []
    headings = list(re.finditer(r"^(?P<marks>#{2,6})\s+(?P<number>\d+(?:\.\d+)+)\s+.+$", body, re.MULTILINE))
    for section in plan.sections:
        scope = ""
        for index, heading in enumerate(headings):
            if heading.group("number") != section.section_id:
                continue
            end = len(body)
            for later in headings[index + 1:]:
                if len(later.group("marks")) <= len(heading.group("marks")):
                    end = later.start()
                    break
            scope = body[heading.end():end]
            break
        capabilities = _body_capabilities(scope)
        if section.artifact_kind:
            present = {"code": "code" in capabilities,
                       "derivation": bool(re.search(r"\$[^$]+\$", scope)) or _prose_artifact_candidate(scope),
                       "decision": _prose_artifact_candidate(scope)}[section.artifact_kind]
            if not present:
                findings.append(QualityFinding(code="DEVELOPMENTAL_ARTIFACT_MISSING", severity=Severity.BLOCKER,
                                               location=f"chapters/chapter-{chapter_number:02d}.md:{section.section_id}",
                                               message=f"声明的{section.artifact_kind}读者产物未在当前小节出现：{section.artifact}"))
        if section.asset_decision != "none" and section.asset_decision not in capabilities:
            findings.append(
                QualityFinding(
                    code="DEVELOPMENTAL_ARTIFACT_MISSING",
                    severity=Severity.BLOCKER,
                    location=f"chapters/chapter-{chapter_number:02d}.md:{section.section_id}",
                    message=f"内容计划要求的 {section.asset_decision} 资产未出现在当前正文中：{section.artifact}。",
                )
            )
    flagged = [unit for unit in reading_units(body) if unit["warnings"]]
    if "KP_DRAFT_UNFINISHED" in body:
        findings.append(QualityFinding(code="CHAPTER_SCAFFOLD_UNFINISHED", severity=Severity.BLOCKER,
                                       location=f"chapters/chapter-{chapter_number:02d}.md",
                                       message="章节仍含未完成骨架；补齐实际导读和各节内容后再提交。"))
    for unit in flagged:
        findings.append(
            QualityFinding(
                code="DEVELOPMENTAL_EXPLANATION_DENSITY_WARNING",
                severity=Severity.WARNING,
                location=f"chapters/chapter-{chapter_number:02d}.md:{unit['unit_id']}",
                message=f"复核信息关系与连续解释区：{unit['warnings']}；指标仅定位，需实际结构审核。",
            )
        )
    return plan_sha, findings


def record_developmental_check(
    workspace: Workspace, chapter_number: int, source_path: Path
) -> tuple[Path | None, DevelopmentalEditReport | None]:
    """Record a current report for v5 workspaces; legacy v4 workspaces remain unchanged."""
    try:
        plan = require_current_content_plan(workspace, chapter_number)
    except ContentPlanError as error:
        raise DevelopmentalEditError(str(error)) from error
    if plan is None:
        return None, None
    resolved = source_path.expanduser().resolve()
    if not resolved.is_file() or not resolved.is_relative_to(workspace.root.resolve()):
        raise DevelopmentalEditError("发展性编辑源稿必须位于当前书稿工作区内")
    contract_path, _ = require_current_chapter_contract(workspace, chapter_number)
    body = _reader_body(resolved)
    from knowledge_product_workflow.diagrams import diagram_semantics_digest

    plan_sha, findings = _findings_for_current_plan(workspace, chapter_number, body)
    report = DevelopmentalEditReport(
        chapter_number=chapter_number,
        chapter_body_sha256=_sha256_text(body),
        contract_sha256=_sha256_file(contract_path),
        content_plan_sha256=plan_sha,
        protected_fragments_sha256=protected_fragments_sha256(body),
        findings=findings,
        diagram_semantics_sha256=diagram_semantics_digest(workspace, body),
        structure_review_sha256=require_structure_review(workspace, chapter_number, body),
    )
    output = developmental_report_path(workspace, chapter_number)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    workspace.append_event(
        "developmental_edit_checked",
        {"chapter_number": chapter_number, "finding_count": len(findings)},
    )
    return output, report


def require_current_developmental_report(
    workspace: Workspace, chapter_number: int, body: str, contract_path: Path
) -> DevelopmentalEditReport | None:
    """Fail closed only for v5 workspaces that explicitly require content planning."""
    try:
        plan = require_current_content_plan(workspace, chapter_number)
    except ContentPlanError as error:
        raise DevelopmentalEditError(str(error)) from error
    if plan is None:
        return None
    path = developmental_report_path(workspace, chapter_number)
    if not path.is_file():
        raise DevelopmentalEditError("缺少当前发展性编辑报告；请先完成章节结构与读者工件检查。")
    try:
        report = DevelopmentalEditReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DevelopmentalEditError("发展性编辑报告无效") from error
    if report.chapter_number != chapter_number:
        raise DevelopmentalEditError("发展性编辑报告的章节号不一致")
    if report.chapter_body_sha256 != _sha256_text(body):
        raise DevelopmentalEditError("发展性编辑报告已过期：它不再绑定当前正文。")
    if report.contract_sha256 != _sha256_file(contract_path):
        raise DevelopmentalEditError("发展性编辑报告已过期：它不再绑定当前章节合约。")
    if report.content_plan_sha256 != _sha256_file(content_plan_path(workspace, chapter_number)):
        raise DevelopmentalEditError("发展性编辑报告已过期：它不再绑定当前内容计划。")
    from knowledge_product_workflow.diagrams import diagram_semantics_digest

    if report.diagram_semantics_sha256 != diagram_semantics_digest(workspace, body):
        raise DevelopmentalEditError("发展性编辑报告已过期：图内标签或关系已变化。")
    if any(finding.severity is Severity.BLOCKER for finding in report.findings):
        raise DevelopmentalEditError("发展性编辑报告仍包含阻断项。")
    if report.structure_review_sha256 != require_structure_review(workspace, chapter_number, body):
        raise DevelopmentalEditError("发展性报告未绑定当前结构审核")
    return report
