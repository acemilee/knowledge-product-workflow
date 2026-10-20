"""Deterministic developmental editing checks for structured reader tasks."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from knowledge_product_workflow.change_impact import (
    binding_policy_revision,
    plan_binding_matches,
    proof_contract_digest,
    proof_contract_matches,
)
from knowledge_product_workflow.content_plan import (
    ContentPlanError,
    content_plan_path,
    require_current_content_plan,
)
from knowledge_product_workflow.frontmatter import parse_markdown_artifact
from knowledge_product_workflow.humanize import fenced_code_regions, protected_fragments_sha256
from knowledge_product_workflow.quality import QualityFinding, Severity
from knowledge_product_workflow.verification_context import (
    current_verification as _v_current_context,
)
from knowledge_product_workflow.verification_context import exists as _v_exists
from knowledge_product_workflow.verification_context import (
    glob_files as _v_glob_files,
)
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import (
    load_yaml as _v_load_yaml,
)
from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import (
    read_bytes as _v_read_bytes,
)
from knowledge_product_workflow.verification_context import (
    read_text as _v_read_text,
)
from knowledge_product_workflow.verification_context import (
    verified_read as _verification_read,
)
from knowledge_product_workflow.verification_context import (
    write_bytes as _v_write_bytes,
)
from knowledge_product_workflow.verification_context import (
    write_text as _v_write_text,
)
from knowledge_product_workflow.workflows.authoring import (
    require_current_chapter_contract_in_view as require_current_chapter_contract,
)
from knowledge_product_workflow.workspace import Workspace

_HEADING = re.compile(r"^#{2,6}\s+(?P<title>.+?)\s*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_IMAGE = re.compile(r"!\[[^\]]*\]\(assets/")
_FENCE = re.compile(r"^\s*```")
_COMPONENT = re.compile(r"^\s*:::\s*\{\.(?P<name>kp-[a-z-]+)")


class DevelopmentalEditError(ValueError):
    """Raised when a v5 chapter lacks a current developmental editing report."""


class _FinalStructureReviewPending(DevelopmentalEditError):
    """A fully validated review is usable during production, but not final delivery."""

    def __init__(self, unit_ids: list[str], review_sha256: str):
        self.review_sha256 = review_sha256
        super().__init__("最终发布前必审结构观察仍为uncertain：" + "、".join(unit_ids)
            + "；请补齐证据并重新审阅，不能以中间推进状态代替最终通过。")


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
    evidence_quote: str | None = Field(default=None, max_length=1200,
        description="新证据政策下引用当前单元的原文片段；引用定位不等于判断已被机器证明。")

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
    assessment: Literal['supported', 'unsupported', 'uncertain'] = Field(
        description='在原命题适用域及给定前提下判断；supported须输入充分、有当前支撑且无域内反例，不能与found/uncertain并填。')
    support_refs: list[str] = Field(default_factory=list)
    missing_premises: list[str] = Field(default_factory=list)
    counterexample: str | None = Field(default=None, description='满足原前提、处于命题适用域但推翻结论的反例；仅改变前提的域外边界写scope_limit或note。')
    note: str = Field(min_length=12, max_length=1200)
    input_status: Literal['sufficient', 'missing', 'uncertain'] | None = None
    inputs: list[str] = Field(default_factory=list)
    operation: str | None = None
    observed_output: str | None = None
    scope_limit: str | None = Field(default=None, description='如实保留原命题的适用域；可列改变前提的边界，不能自行缩小全称结论来排除反例。')
    counterexample_status: Literal['none_found', 'found', 'uncertain'] | None = Field(default=None,
        description='针对原命题适用域：已检查未发现为none_found，发现域内反例为found，未核清为uncertain；域外条件变化本身不等于found。')
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


def review_evidence_revision(workspace: Workspace) -> int:
    from knowledge_product_workflow.runtime import _read_orchestration_state
    state = _read_orchestration_state(workspace)
    return state.review_evidence_revision if state else 0


def _premise_audit_required(workspace: Workspace) -> bool:
    from knowledge_product_workflow.runtime import _read_orchestration_state
    state = _read_orchestration_state(workspace)
    return bool(state and state.request_capture_revision >= 1 and state.reading_review_rubric_revision >= 3)


@_verification_read(transaction_view=True)
def book_reading_index(workspace: Workspace, chapter_number: int, body: str, *, chapter_numbers: set[int] | None = None) -> list[dict]:
    """Derived locations/jobs, not another content or fact source."""
    from knowledge_product_workflow.production_intake import consistent_production_read
    with consistent_production_read(workspace, chapters={chapter_number} if binding_policy_revision(workspace) == 2 else None):
        return _book_reading_index(workspace, chapter_number, body, chapter_numbers=chapter_numbers)


def book_reading_index_in_view(workspace: Workspace, chapter_number: int, body: str, *, chapter_numbers: set[int] | None = None) -> list[dict]:
    """Reuse only a covering parent read guard; public calls still snapshot afresh."""
    from knowledge_product_workflow.production_intake import _has_covering_production_read
    from knowledge_product_workflow.workspace_transactions import has_transaction_read_view

    chapters = {chapter_number} if binding_policy_revision(workspace) == 2 else None
    if has_transaction_read_view(workspace) and _has_covering_production_read(workspace, chapters):
        return _book_reading_index(workspace, chapter_number, body, chapter_numbers=chapter_numbers)
    return book_reading_index(workspace, chapter_number, body, chapter_numbers=chapter_numbers)


def _book_reading_index(workspace: Workspace, chapter_number: int, body: str, *, chapter_numbers: set[int] | None = None) -> list[dict]:
    bodies = {}
    from knowledge_product_workflow.workspace_transactions import pending_workspace_transactions
    pending_paths = {relative for tx in pending_workspace_transactions(workspace)
                     for relative in tx.get("outputs", {})} if binding_policy_revision(workspace) == 2 else set()
    paths = (_v_glob_files(workspace.root / "chapters", "chapter-*.md") if chapter_numbers is None else
             [workspace.root / f"chapters/chapter-{n:02d}.md" for n in sorted(chapter_numbers)
              if _v_is_file(workspace.root / f"chapters/chapter-{n:02d}.md")])
    for path in paths:
        match = re.fullmatch(r"chapter-(\d+)\.md", path.name)
        if match:
            if chapter_numbers is not None and int(match.group(1)) not in chapter_numbers:
                continue
            if path.relative_to(workspace.root).as_posix() not in pending_paths:
                bodies[int(match.group(1))] = _reader_body(path)
                continue
            from knowledge_product_workflow.workspace_transactions import committed_reader_text
            stored = committed_reader_text(workspace, path.relative_to(workspace.root).as_posix())
            if stored is not None:
                try:
                    bodies[int(match.group(1))] = parse_markdown_artifact(stored)[1].strip() + "\n"
                except ValueError:
                    bodies[int(match.group(1))] = stored.strip() + "\n"
    if chapter_numbers is None or chapter_number in chapter_numbers:
        bodies[chapter_number] = body
    result = []
    context = _v_current_context(workspace)
    for number, text in sorted(bodies.items()):
        strict_plan = number == chapter_number or chapter_numbers is not None
        compute = lambda number=number, text=text, strict_plan=strict_plan: _reading_index_rows(workspace, number, text, strict_plan=strict_plan)
        rows = context.memo(("reading-index-rows.v2", number, _sha256_text(text), strict_plan), compute) if context is not None else compute()
        result.extend(rows)
    return result


def _reading_index_rows(workspace: Workspace, number: int, text: str, *, strict_plan: bool) -> list[dict]:
    from knowledge_product_workflow.workflows.curriculum import require_current_submitted_blueprint

    context = _v_current_context(workspace)
    def declared():
        if not _v_exists(workspace.root / 'planning/product-brief.md') and not _v_exists(workspace.root / 'planning/curriculum-blueprint.md'):
            return set()  # A bare, unmanaged reading inventory has no declared plan obligations.
        return {c.chapter_number for c in require_current_submitted_blueprint(workspace)[2].chapters}
    declared_numbers = context.memo("reading-index-declarations.v1", declared) if context is not None else declared()
    # Existing ad-hoc comparison documents have no course contract. They remain
    # explicitly body-only; an approved course chapter must have a valid plan.
    plan_error = None
    try:
        plan = require_current_content_plan(workspace, number) if number in declared_numbers else None
    except ContentPlanError as error:
        if strict_plan:
            raise
        # An unrelated repair is visible inventory, not a gate for this chapter.
        plan, plan_error = None, str(error)
    sections = {section.section_id: section for section in plan.sections} if plan else {}
    titles = {match.group(1): match.group(2) for match in
              re.finditer(r"^#{2,6}\s+(\d+(?:\.\d+)+)\s+([^\n]+)$", text, re.MULTILINE)}
    return [{"unit_id": unit["unit_id"], "chapter_number": number,
                   "plan_status": "invalid_plan_deferred" if plan_error else "current" if plan else "body_only_undeclared" if number not in declared_numbers else "legacy_not_required",
                   "plan_error": plan_error,
                   "title": titles.get(unit["unit_id"], ""),
                   "reader_job": sections[unit["unit_id"]].reader_job if unit["unit_id"] in sections else None,
                   "unit_sha256": unit["unit_sha256"], "start_line": unit["start_line"],
                   "comparison_sha256": _sha256_text(json.dumps({
                       "unit": unit["unit_sha256"],
                       **({'invalid_plan': plan_error} if plan_error else {}),
                       "task": sections[unit["unit_id"]].model_dump(mode="json")
                       if unit["unit_id"] in sections else None}, sort_keys=True)),
                   "source": f"chapters/chapter-{number:02d}.md"}
                  for unit in reading_units(text)]


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


def _reject_mechanical_observation(observation: StructureObservation) -> None:
    """New intake only: observed boilerplate is not evidence. Old records retain policy."""
    pattern = r'^\S+ 的\w+：与本节目标一致，按第 \d+ 章整体结构核对后未见冲突。$'
    if any(re.fullmatch(pattern, item.note.strip()) for item in (observation.teaching_checks or {}).values()):
        raise DevelopmentalEditError('机械占位观察缺少逐项内容依据；实际核对后重交，不批量签passed')
    observed_boilerplate = {
        '本节引入了新的判断维度，而不是重复上一章内容。',
        '用文字、表格或图示中更贴合该结构的一种表达。',
        '与前后章节形成互补，不重复同一论据。',
        '必要的复述有新角度，不做机械重复。',
        '读者读完后有明确的下一步动作。',
        '断言、解释、例证与收束的层次清楚。',
    }
    if any(re.sub(r'（[^（）]+）$', '', item.note.strip()).strip() in observed_boilerplate
           for item in (observation.teaching_checks or {}).values()):
        raise DevelopmentalEditError('机械占位教学理由缺少当前单元依据；附unit_id不构成实际审阅')
    generic_assertion = '该命题为正文内的操作指令或示例说明；本节已给出对应模板或承接内容，未主张外部事实，未超出正文范围。'
    if any(item.note.strip() == generic_assertion for item in observation.assertion_checks):
        raise DevelopmentalEditError('机械占位命题理由不能证明输入充分或推导成立；逐项核对前提与反例')


def _validate_teaching_evidence(workspace: Workspace, observation: StructureObservation, body: str) -> None:
    if review_evidence_revision(workspace) < 1:
        return
    units = reading_units(body)
    unit = next((u for u in units if u['unit_id'] == observation.unit_id), None)
    if unit is None:
        raise DevelopmentalEditError('教学依据单元不在当前正文')
    start = unit['start_line'] - 1
    end = next((u['start_line'] - 1 for u in units if u['start_line'] > unit['start_line']), len(body.splitlines()))
    visible = ''.join('\n'.join(body.splitlines()[start:end]).split())
    for key, check in (observation.teaching_checks or {}).items():
        quote = ''.join((check.evidence_quote or '').split())
        if not quote or quote not in visible:
            raise DevelopmentalEditError(f'教学检查缺少当前单元原文依据：{observation.unit_id}/{key}；读取当前模板，只重新判断受影响项')


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
    context = _v_current_context(workspace)
    compute = lambda: _build_assertion_support_context(workspace, body)
    return context.memo(("assertion-support-context.v1", _sha256_text(body)), compute) if context is not None else compute()


def _build_assertion_support_context(workspace: Workspace, body: str) -> list[dict]:
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
        if _v_is_file(path):
            semantic = DiagramInput.model_validate_json(_v_read_text(path, encoding="utf-8"))
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
    if not checks:
        return
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

    from knowledge_product_workflow.presentation import (
        PRESENTATION_PROFILE_ID,
        publication_profile_path,
    )

    path = publication_profile_path(workspace)
    if not _v_is_file(path):
        return False
    profile = _v_load_yaml(_v_read_text(path, encoding="utf-8"))
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
            "current_evidence_revision": state.review_evidence_revision if state else 0,
            "target_evidence_revision": 1,
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
    if (state.reading_structure_revision == 2 and state.reading_review_rubric_revision >= 2
            and state.review_evidence_revision >= 1):
        return reading_policy_preview(workspace)

    from knowledge_product_workflow.presentation import PublicationProfile, publication_profile_path
    path = publication_profile_path(workspace)
    profile = PublicationProfile.model_validate(_v_load_yaml(_v_read_text(path, encoding="utf-8"))) if _v_is_file(path) else None
    if profile is None or profile.reader_layout_revision != 2 or profile.profile_id != "knowledge-product-pdf-v6":
        raise DevelopmentalEditError("阅读政策2需要已确认的v6布局2；先通过出版更新确认布局，不静默换版")
    state = state.model_copy(update={"reading_structure_revision": 2,
        "reading_review_rubric_revision": max(2, state.reading_review_rubric_revision), "review_evidence_revision": 1})
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
               if _v_is_file(workspace.root / "research" / name) else None
               for name in ("source-ledger.jsonl", "claim-classifications.jsonl", "assumption-ledger.jsonl")}
    referenced = {int(reference) for pair in re.findall(r"第\s*(\d+)\s*章|(?<![\d.])(\d+)\.\d+", body)
                  for reference in pair if reference}
    if re.search(r"上一章|前一章|前章", body) and number > 1:
        referenced.add(number-1)
    for chapter in sorted(referenced - {number}):
        path = workspace.root / "chapters" / f"chapter-{chapter:02d}.md"
        context[f"chapter-{chapter}"] = _sha256_text(_reader_body(path)) if _v_is_file(path) else None
        contract = workspace.root / "chapters" / f"chapter-{chapter:02d}-contract.md"
        if _v_is_file(contract):
            _, related = require_current_chapter_contract(workspace, chapter)
            claims.update(related.claim_ids)
    if scoped:
        context.update(claim_scoped_evidence_digests(workspace, claims))
    context["diagram_semantics"] = diagram_semantics_digest(workspace, body)
    return _sha256_text(json.dumps(context, sort_keys=True))


def _reference_blocks(text: str) -> list[str]:
    """Keep prose soft lines together; table cells/list items have their own scope."""
    blocks, paragraph = [], []
    for line in text.splitlines():
        table = line.lstrip().startswith('|')
        boundary = not line.strip() or table or re.match(r'^\s*(?:#{1,6}\s|[-+*]\s|\d+[.)]\s)', line)
        if boundary and paragraph:
            blocks.append('\n'.join(paragraph))
            paragraph = []
        if table:
            blocks.extend(re.split(r'(?<!\\)\|', line))
        elif line.strip():
            paragraph.append(line)
    if paragraph:
        blocks.append('\n'.join(paragraph))
    return blocks


def review_dependencies(workspace: Workspace, number: int, body: str) -> list[dict]:
    """Navigation binds contracts; actual result/section references bind content."""
    text = _reference_text(body)
    sections = _section_references(text)
    result_chapters = {int(value.split('.')[0]) for value in sections}
    chapters = {int(m.group(1)) for m in re.finditer(r"第\s*(\d+)\s*章", text)}
    for block in _reference_blocks(text):
        for match in re.finditer(r"第\s*(\d+)\s*章", block):
            before, after = block[max(0, match.start()-12):match.start()], block[match.end():match.end()+24]
            # Exclude only a clearly local statement after a sentence boundary.
            # Ambiguous continuation keeps the old conservative result binding.
            clauses = re.split(r"[。！？!?；;]|\r?\n[ \t]*\r?\n", after, maxsplit=1)
            result_words = r"(?:结果|结论|计算|公式|假设|数值|定义|条件)"
            tail = clauses[1] if len(clauses) > 1 else ""
            local_statement = re.match(r"\s*(?:这里|本章|本节|当前章|当前节|接下来|现在)", tail)
            consumption = re.search(
                r"其|该章|上述|前述|这些|那些|之前|前面|根据|依据|沿用|使用|对照|引用|复用|继承|采用|基于", tail)
            continued = re.search(result_words, tail) and (not local_statement or consumption)
            if (re.search(r"(?:根据|依据|沿用|使用|对照)\s*$", before)
                    or re.search(r"(?:的|给出的|中的)?" + result_words, clauses[0]) or continued):
                result_chapters.add(int(match.group(1)))
    if re.search(r"上一章|前一章|前章", text) and number > 1:
        result_chapters.add(number-1)
    dependencies = []
    for chapter_number in sorted((chapters | result_chapters) - {number}):
        contract = workspace.root / "chapters" / f"chapter-{chapter_number:02d}-contract.md"
        dependencies.append({"kind": "contract", "chapter": chapter_number,
                             "sha256": proof_contract_digest(workspace, chapter_number,
                                 "content_plan" if chapter_number in result_chapters else "navigation") if _v_is_file(contract) else None})
        if chapter_number not in result_chapters:
            continue
        path = workspace.root / "chapters" / f"chapter-{chapter_number:02d}.md"
        related_body = _reader_body(path) if _v_is_file(path) else ""
        referenced_units = {s for s in sections if int(s.split('.')[0]) == chapter_number}
        if referenced_units:
            units = {unit['unit_id']: unit['unit_sha256'] for unit in reading_units(related_body)}
            dependencies.extend({"kind": "section", "chapter": chapter_number, "unit": unit,
                                 "sha256": units.get(unit)} for unit in sorted(referenced_units))
        else:
            dependencies.append({"kind": "result", "chapter": chapter_number,
                                 "sha256": _sha256_text(related_body) if _v_is_file(path) else None})
    return dependencies


def _review_context_digest(workspace: Workspace, number: int, body: str, *, scoped: bool = True) -> str:
    if not scoped:
        return _legacy_review_context_digest(workspace, number, body, scoped=False)
    return _sha256_text(json.dumps(_review_context_inputs(workspace, number, body), sort_keys=True))


def _review_context_inputs(workspace: Workspace, number: int, body: str) -> dict:
    """Exact inputs to the existing digest; no prose or source content snapshot."""
    from knowledge_product_workflow.claims import claim_scoped_evidence_digests
    from knowledge_product_workflow.diagrams import diagram_semantics_digest
    from knowledge_product_workflow.example_checks import example_evidence_for_body
    _, chapter = require_current_chapter_contract(workspace, number)
    claims = set(chapter.claim_ids)
    dependencies = review_dependencies(workspace, number, body)
    for dependency in dependencies:
        contract = workspace.root / "chapters" / f"chapter-{dependency['chapter']:02d}-contract.md"
        if dependency['kind'] != 'contract' and _v_is_file(contract):
            _, related = require_current_chapter_contract(workspace, dependency['chapter'])
            claims.update(related.claim_ids)
    context = {"dependency_revision": 3, "references": dependencies,
               "claims": claim_scoped_evidence_digests(workspace, claims),
               "execution": [{key: data.get(key) for key in ("code_sha256", "stdout_sha256", "stderr_sha256", "exit_code", "status")}
                             for data in example_evidence_for_body(workspace, body)],
               "diagram_semantics": diagram_semantics_digest(workspace, body)}
    from knowledge_product_workflow.reader_artifacts import resolve_reader_resources
    resources = resolve_reader_resources(workspace, body, chapter_number=number, partial_scope=True)
    if resources:
        context["reader_resources"] = {
            item["section_id"] + ":" + item["resource_path"]: _sha256_text(json.dumps(item["inputs"], sort_keys=True))
            for item in resources}
    if binding_policy_revision(workspace) == 2:
        unit_ids = {u["unit_id"] for u in reading_units(body)}
        from knowledge_product_workflow.change_impact import declared_result_bindings
        context["declared_results"] = declared_result_bindings(workspace, number, section_ids=unit_ids)
        context["approved_diagram_tasks"] = [t.model_dump(mode="json") for t in chapter.illustration_tasks
                                             if t.section_id in unit_ids]
    return context


def _diagnostic_digest(value, *, missing=False) -> bool:
    return (missing and value is None) or (
        isinstance(value, str) and len(value) == 64 and re.fullmatch(r"[0-9a-f]{64}", value) is not None)


def _context_entries(context: dict) -> dict:
    """Flatten identities and digests, never raw values, for diagnostic display."""
    allowed = {"dependency_revision", "references", "claims", "execution",
               "diagram_semantics", "declared_results", "approved_diagram_tasks", "reader_resources"}
    if not context.keys() <= allowed:
        raise ValueError("Unknown historical context fields")
    entries = {}
    for domain, value in context.items():
        if domain == "references":
            for ref in value:
                if (ref["kind"] not in {"contract", "result", "section"}
                        or type(ref["chapter"]) is not int or ref["chapter"] < 1
                        or len(str(ref["chapter"])) > 12
                        or not _diagnostic_digest(ref["sha256"], missing=True)
                        or ("unit" in ref and (not isinstance(ref["unit"], str) or len(ref["unit"]) > 160
                                              or not re.fullmatch(r"\d+(?:\.\d+)+", ref["unit"])))):
                    raise ValueError("Invalid historical reference identity")
                identity = (domain, str(ref["kind"]), str(ref["chapter"]), str(ref.get("unit", "")))
                if identity in entries:
                    raise ValueError("Duplicate historical reference identity")
                entries[identity] = ({"domain": domain, **{k: ref[k] for k in ("kind", "chapter", "unit") if k in ref}},
                                     ref["sha256"])
        elif domain in {"claims", "declared_results", "reader_resources"}:
            for name, digest in value.items():
                if not _diagnostic_digest(digest):
                    raise ValueError("Invalid historical input digest")
                entries[(domain, name)] = ({"domain": domain, "input": name[:160]}, digest)
        elif domain == "approved_diagram_tasks":
            for index, task in enumerate(value):
                task_id = task.get("task_id")
                if (task_id is not None and (not isinstance(task_id, str) or len(task_id) > 160)
                        or not isinstance(task["section_id"], str) or len(task["section_id"]) > 160):
                    raise ValueError("Invalid historical task identity")
                identity = (domain, str(task_id or index))
                entries[identity] = ({"domain": domain, "task_id": task_id, "unit": task["section_id"]},
                                     _sha256_text(json.dumps(task, sort_keys=True)))
        else:
            entries[(domain,)] = ({"domain": domain}, _sha256_text(json.dumps(value, sort_keys=True)))
    return entries


def _review_context_delta(previous: dict | None, context: dict, units: list[dict] | tuple = ()) -> dict:
    """Bounded explanation only. Never used as permission to reuse a review."""
    current_sha = _sha256_text(json.dumps(context, sort_keys=True))
    result = {"status": "no_previous_review", "changes": [], "change_count": 0,
              "changes_truncated": False, "affected_unit_ids": [],
              "new_context_sha256": current_sha, "diagnostic_only": True}
    if previous is None:
        return result
    stored_sha = previous.get("review_context_sha256")
    result["old_context_sha256"] = stored_sha if _diagnostic_digest(stored_sha) else None
    snapshot = previous.get("review_context_inputs")
    if snapshot is None:
        result["status"] = "history_unavailable"
        return result
    try:
        if (not _diagnostic_digest(stored_sha) or not isinstance(snapshot, dict)
                or _sha256_text(json.dumps(snapshot, sort_keys=True)) != previous.get("review_context_sha256")
                or previous.get("dependency_revision") != 3):
            raise ValueError("Unbound historical context")
        before, after = _context_entries(snapshot), _context_entries(context)
        old_units = {u["unit_id"]: u for u in previous.get("units", [])}
        result["affected_unit_ids"] = [u["unit_id"] for u in units
            if old_units.get(u["unit_id"], {}).get("dependency_sha256") != u["dependency_sha256"]]
        changes = []
        for key in sorted(before.keys() | after.keys()):
            if before.get(key) == after.get(key):
                continue
            label = after[key][0] if key in after else before[key][0]
            changes.append({**label, "old_present": key in before, "new_present": key in after,
                            "old_sha256": before[key][1] if key in before else None,
                            "new_sha256": after[key][1] if key in after else None})
        if not changes and result["old_context_sha256"] != current_sha:
            changes.append({"domain": "review_context", "old_sha256": result["old_context_sha256"],
                            "new_sha256": current_sha})
        result.update(status="changed" if changes else "unchanged", changes=changes[:64],
                      change_count=len(changes), changes_truncated=len(changes) > 64)
    except (AttributeError, KeyError, TypeError, ValueError):
        result.update(status="invalid_history", changes=[], affected_unit_ids=[])
    return result


def _comparison_context_delta(previous: dict | None, index: list[dict]) -> dict:
    result = {"status": "history_unavailable", "changes": [], "change_count": 0,
              "changes_truncated": False, "diagnostic_only": True}
    if previous is None or "comparison_context" not in previous:
        return result
    try:
        current = {u["unit_id"]: u["comparison_sha256"] for u in index}
        changes = []
        for unit, digest in sorted(previous["comparison_context"].items()):
            if (not isinstance(unit, str) or len(unit) > 160
                    or not re.fullmatch(r"\d+(?:\.\d+)+", unit) or not _diagnostic_digest(digest)):
                raise ValueError("Invalid historical comparison")
            if current.get(unit) != digest:
                changes.append({"unit": unit, "old_sha256": digest, "new_sha256": current.get(unit)})
        result.update(status="changed" if changes else "unchanged", changes=changes[:64],
                      change_count=len(changes), changes_truncated=len(changes) > 64)
    except (AttributeError, KeyError, TypeError, ValueError):
        result["status"] = "invalid_history"
    return result


def _review_delta_hint(delta: dict) -> str:
    if delta["status"] in {"history_unavailable", "invalid_history"}:
        return "；历史输入明细不可用（" + delta["status"] + "），不推测具体变化；读取developmental-review-template"
    labels = []
    for item in delta["changes"][:4]:
        label = item["domain"]
        for key in ("kind", "chapter", "unit", "input", "task_id"):
            if key in item:
                label += f" {key}={str(item[key])[:160]}"
        label += f" {str(item['old_sha256'])[:12]}→{str(item['new_sha256'])[:12]}"
        labels.append(label)
    return "；依赖差分：" + "、".join(labels) + "；读取developmental-review-template，仅按当前reusable_unit_ids复用"


def _review_context_current(workspace: Workspace, number: int, body: str, digest: str | None) -> bool:
    # An old full-ledger proof remains stronger than the scoped proof only while
    # every byte of its original context still matches. Never rewrite its hash.
    if digest == _review_context_digest(workspace, number, body):
        return True
    if binding_policy_revision(workspace) == 2:
        return False  # A legacy context cannot prove a policy2 result consumption.
    from knowledge_product_workflow.example_checks import example_evidence_for_body
    if example_evidence_for_body(workspace, body):
        return False
    return digest in {_legacy_review_context_digest(workspace, number, body),
                      _legacy_review_context_digest(workspace, number, body, scoped=False)}


def _read_structure_review(workspace: Workspace, path: Path) -> dict:
    def parse(data):
        result = json.loads(data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n"))
        if not isinstance(result, dict):
            raise TypeError("Structure review must be an object")
        return result
    context = _v_current_context(workspace)
    raw = _v_read_bytes(path)
    return context.parsed_payload("structure-review-json.v1", raw, parse) if context is not None else parse(raw)


def _reusable_observations(workspace: Workspace, number: int, binding: dict, units: list[dict], body: str) -> dict:
    path = structure_review_path(workspace, number)
    if not _v_is_file(path):
        return {}
    try:
        previous = _read_structure_review(workspace, path)
        if previous.get('evidence_revision', 0) != binding.get('evidence_revision', 0):
            return {}
        if previous.get("reading_structure_revision") != 2 or any(
            previous.get(key, 1 if key == "rubric_revision" else None) != binding[key]
            for key in ("rubric_revision",)
        ):
            return {}
        if (previous.get("contract_sha256") != binding["contract_sha256"]
                and (binding_policy_revision(workspace) != 2
                     or previous.get("contract_static_sha256") != binding.get("contract_static_sha256")
                     or not binding.get("contract_static_sha256"))):
            return {}
        if ((previous.get("dependency_revision", 1) < 3
                or previous.get("scoped_context_sha256") != previous.get("review_context_sha256"))
                and previous.get("review_context_sha256") != binding["review_context_sha256"]
                and (previous.get("chapter_body_sha256") != binding["chapter_body_sha256"]
                     or not _review_context_current(workspace, number, body, previous.get("review_context_sha256")))):
            return {}
        if not plan_binding_matches(workspace, number, previous.get("content_plan_sha256", "")):
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
        compared = previous.get("comparison_context", {})
        selected = {int(ref.split(".")[0]) for ref in compared}
        index = {u["unit_id"]: u["comparison_sha256"] for u in book_reading_index(workspace, number, "", chapter_numbers=selected)} if selected else {}
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


@_verification_read(transaction_view=True)
def create_structure_review_template(workspace: Workspace, chapter_number: int, source_path: Path) -> dict:
    return _create_structure_review_template(workspace, chapter_number, source_path)[0]


def _create_structure_review_template(workspace: Workspace, chapter_number: int, source_path: Path) -> tuple[dict, dict]:
    from knowledge_product_workflow.reader_artifacts import (
        reader_presentation_guidance,
        reader_presentation_metrics,
    )
    from knowledge_product_workflow.runtime import require_workflow_mutation_allowed
    require_workflow_mutation_allowed(workspace, "developmental-review-template")
    source = source_path.resolve()
    if not _v_is_file(source) or not source.is_relative_to(workspace.root.resolve()):
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
    context_inputs = _review_context_inputs(workspace, chapter_number, body)
    binding = {"chapter_body_sha256": _sha256_text(body), "contract_sha256": proof_contract_digest(workspace, chapter_number, "developmental"),
               "dependency_revision": 3,
               "rubric_revision": _rubric_revision(workspace),
               "evidence_revision": review_evidence_revision(workspace),
               "content_plan_sha256": _sha256_file(content_plan_path(workspace, chapter_number)),
               "contract_audit_sha256": _sha256_file(contract),
               "review_context_sha256": _sha256_text(json.dumps(context_inputs, sort_keys=True))}
    binding["scoped_context_sha256"] = binding["review_context_sha256"]
    if binding_policy_revision(workspace) == 2:
        binding["contract_static_sha256"] = proof_contract_digest(workspace, chapter_number, "structure_contract")
    reusable = _reusable_observations(workspace, chapter_number, binding, units, body)
    index = book_reading_index(workspace, chapter_number, body)
    index_path = workspace.root / "runs/editorial/book-reading-index.json"
    _v_mkdir(index_path.parent,parents=True,exist_ok=True)
    _v_write_text(index_path, json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    previous_sha = _sha256_file(structure_review_path(workspace, chapter_number)) if reusable else None
    from knowledge_product_workflow.example_checks import example_evidence_for_body
    factual_prompts = [{"line": line_number, "text": line.strip(), "question": "本主张的输入、成立条件、真实来源或实际执行输出是否齐备？缺少条件时保留未知，不补造数值或真实事件。"}
                       for line_number, line in enumerate(_reference_text(body).splitlines(), 1)
                       if re.search(r"(?:运行|执行|计算).{0,18}(?:得到|输出|结果)|真实(?:案例|事件)|\d+(?:\.\d+)?%|(?:得分|数值|结果).{0,10}(?:为|等于|变为|趋近)", line)]
    assertions = _assertion_prompts(body) if binding['rubric_revision'] >= 3 else []
    support_context = _assertion_support_context(workspace, body) if assertions else []
    fingerprint = _sha256_text(json.dumps({**binding, "reuse_evidence_sha256": previous_sha,
                                         "reading_index": index}, sort_keys=True))
    previous = None
    previous_path = structure_review_path(workspace, chapter_number)
    if _v_is_file(previous_path):
        try:
            previous = _read_structure_review(workspace, previous_path)
            if not isinstance(previous, dict):
                previous = {"review_context_inputs": False}
        except (ValueError, TypeError, OSError):
            previous = {"review_context_inputs": False}
    return {"template_id": fingerprint, "binding": binding, "units": units,
            "review_context_delta": _review_context_delta(previous, context_inputs, units),
            "comparison_context_delta": _comparison_context_delta(previous, index),
            "dependency_context": review_dependencies(workspace, chapter_number, body),
            "execution_evidence": example_evidence_for_body(workspace, body),
            "fact_check_prompts": factual_prompts,
            "assertion_prompts": assertions,
            "assertion_support_context": support_context,
            "assertion_audit_protocol": {"revision": 1, "required": _premise_audit_required(workspace),
                "required_fields": ["input_status", "inputs", "operation", "observed_output", "scope_limit",
                                    "counterexample_status", "evidence_kind"],
                "semantic_truth_machine_verified": False,
                "counterexample_rule": "反例须满足原前提并在原命题适用域内推翻结论；全称或范围命题允许的变量取值仍在域内。"
                    "已检查未发现填none_found，不是保证不存在；未核清填uncertain，不能默认通过。",
                "boundary_rule": "仅改变原前提的域外示例写scope_limit或note，不单因它填found；不得缩小原命题范围来回避真实反例。",
                "supported_requirements": {"input_status": "sufficient", "inputs": "具体前提，不能复写结论",
                    "support_refs": "当前支撑索引中的引用", "missing_premises": [], "counterexample_status": "none_found"},
                "aggregation_rule": "真实反例或未满足条件用unsupported，观察聚合revise；未核清用uncertain并列missing_premises，不能聚合passed。"
                    "实际执行与外部事实仍需相应证据，条件推导不能冒充实跑。",
                "review_sequence": ["读取具体输入与当前代码/图关系", "寻找缺失前提和不满足条件的反例",
                                    "核实际输出及执行范围", "限定结论范围后判断supported/unsupported/uncertain"]},
            "review_questions": ["本单元相对已写内容新增了什么判断或操作？列具体比较单元与增量，不按段落或图数打分。",
                                 "图/表/代码是否互补，还是以不同形式重复同一结论？",
                                 "没有真实作者外上下文时明确审核局限；辅助自评不能宣称正式独立审稿。"],
            **({"reader_presentation": guidance, "reader_presentation_metrics": reader_presentation_metrics(body)}
               if (guidance := reader_presentation_guidance(workspace)) else {}),
            "teaching_rubric": TEACHING_CHECKS if binding["rubric_revision"] >= 2 else {},
            "teaching_check_schema": TeachingCheck.model_json_schema() if binding['rubric_revision'] >= 2 else None,
            "teaching_evidence_protocol": {"revision":binding['evidence_revision'],
                "required_per_check":['evidence_quote','note'] if binding['evidence_revision'] else ['note'],
                "rule":"evidence_quote引用当前unit原文；note说明本维度的具体判断/不适用边界，不能只复写标题或加unit_id。只填受影响必审项，其余合法reuse。",
                "semantic_truth_machine_verified":False},
            "required_unit_ids": [u['unit_id'] for u in units if u['required']],
            "book_reading_index": index,
            "reusable_unit_ids": [u["unit_id"] for u in units if u["required"] and u["unit_id"] in reusable],
            "reuse_evidence_sha256": previous_sha,
            "observations": [{"unit_id": u["unit_id"], "assessment": "uncertain",
                              "note": "请实际审阅信息关系、示例与成功判断后填写具体理由。"}
                             for u in units if u["required"]],
            "review_input": {"skill": "kp-developmental-editor", "action": "structure-review",
                                    "artifact_sha256": fingerprint, "chapter_number": chapter_number}}, context_inputs


@_verification_read(transaction_view=True)
def record_structure_review(workspace: Workspace, chapter_number: int, source_path: Path,
                            payload: StructureReviewInput) -> Path:
    from knowledge_product_workflow.runtime import require_workflow_mutation_allowed
    require_workflow_mutation_allowed(workspace, "record-developmental-review")
    template, context_inputs = _create_structure_review_template(workspace, chapter_number, source_path)
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
        _reject_mechanical_observation(observation)
        _validate_teaching_checks(observation, template["binding"]["rubric_revision"])
        _validate_teaching_evidence(workspace, observation, _reader_body(source_path))
        _validate_assertion_checks(workspace, observation, _reader_body(source_path), template['binding']['rubric_revision'])
    output = structure_review_path(workspace, chapter_number)
    _v_mkdir(output.parent,parents=True,exist_ok=True)
    cached = (_reusable_observations(workspace, chapter_number, template["binding"],
                                     template["units"], _reader_body(source_path))
              if reused else {})
    if _v_is_file(output):
        history = output.parent / "history" / f"chapter-{chapter_number:02d}"
        _v_mkdir(history,parents=True,exist_ok=True)
        archived = history / f"structure-{_sha256_file(output)}.json"
        if not _v_exists(archived):
            _v_write_bytes(archived, _v_read_bytes(output))
    observations = payload.observations + [cached[unit_id] for unit_id in reused]
    index = {u["unit_id"]: u["comparison_sha256"] for u in template["book_reading_index"]}
    references = {reference for o in observations for reference in o.compared_unit_ids}
    if not references.issubset(index):
        raise DevelopmentalEditError("比较单元不在当前阅读索引中，不得声称已比较不存在的章节")
    invalid = {u['unit_id'] for u in template['book_reading_index'] if u.get('plan_status') == 'invalid_plan_deferred'}
    if references & invalid:
        raise DevelopmentalEditError('实际比较单元的内容计划待修，不能将索引提示签为通过')
    data = {"schema_version": 2, **template["binding"], "units": template["units"],
            "review_context_inputs": context_inputs,
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
    _v_write_text(output, json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    workspace.append_event("chapter_structure_reviewed", {"chapter_number": chapter_number})
    return output


def require_structure_review(workspace: Workspace, chapter_number: int, body: str, *, final_review: bool = False) -> str | None:
    if not structure_review_required(workspace):
        return None
    path = structure_review_path(workspace, chapter_number)
    if not _v_is_file(path):
        raise DevelopmentalEditError("缺少实际结构审核；运行developmental-review-template并提交观察")
    try:
        data = _read_structure_review(workspace, path)
    except (ValueError, TypeError, OSError) as error:
        raise DevelopmentalEditError("结构审核证明无效") from error
    _contract, _ = require_current_chapter_contract(workspace, chapter_number)
    expected = {"reading_structure_revision": 2, "rubric_revision": _rubric_revision(workspace),
                "evidence_revision": review_evidence_revision(workspace),
                "chapter_body_sha256": _sha256_text(body), "contract_sha256": proof_contract_digest(workspace, chapter_number, "developmental"),
                "content_plan_sha256": _sha256_file(content_plan_path(workspace, chapter_number))}
    changed = [key for key, value in expected.items()
               if data.get(key, 2 if key == "reading_structure_revision" else 1 if key == "rubric_revision" else 0 if key == "evidence_revision" else None) != value]
    delta_hint = ""
    if "review_context_sha256" in data and not _review_context_current(workspace, chapter_number, body, data["review_context_sha256"]):
        changed.append("review_context_current（实质引用/来源/图义/执行证据）")
        delta_hint = _review_delta_hint(_review_context_delta(data, _review_context_inputs(workspace, chapter_number, body)))
    if changed:
        raise DevelopmentalEditError("结构审核已过期；变化输入：" + "、".join(changed) + delta_hint)
    observations = [StructureObservation.model_validate(o) for o in data.get("observations", [])]
    current_body_refs = {f"body_unit:{unit['unit_id']}": unit['unit_sha256'] for unit in reading_units(body)}
    for observation in observations:
        _validate_teaching_checks(observation, _rubric_revision(workspace))
        _validate_teaching_evidence(workspace, observation, body)
        _validate_assertion_checks(workspace, observation, body, _rubric_revision(workspace))
        if _rubric_revision(workspace) >= 3:
            refs = {ref for check in observation.assertion_checks for ref in check.support_refs
                    if ref.startswith('body_unit:')}
            if refs and data.get('assertion_body_refs', {}).get(observation.unit_id, {}) != {
                ref: current_body_refs[ref] for ref in refs
            }:
                raise DevelopmentalEditError(f'正文前提引用指纹已过期或缺失：unit_id={observation.unit_id}')
    compared = data.get("comparison_context", {})
    try:
        selected = {int(ref.split(".")[0]) for ref in compared}
    except (TypeError, ValueError) as error:
        raise DevelopmentalEditError("结构审核比较单元无效") from error
    index = {u["unit_id"]: u["comparison_sha256"] for u in book_reading_index_in_view(workspace, chapter_number, body, chapter_numbers=selected)} if selected else {}
    for reference, digest in compared.items():
        if index.get(reference) != digest:
            raise DevelopmentalEditError("结构审核已过期：实际比较的案例单元已变化；"
                f"unit={str(reference)[:160]} {str(digest)[:12]}→{str(index.get(reference))[:12]}；"
                "读取developmental-review-template的comparison_context_delta")
    plan = require_current_content_plan(workspace, chapter_number)
    sections = {s.section_id: s for s in plan.sections} if plan else {}
    required = {unit["unit_id"] for unit in reading_units(body)
                if chapter_number == 1 or unit["warnings"] or
                _critical_reader_task(sections.get(unit["unit_id"]), _rubric_revision(workspace))}
    if {o.unit_id for o in observations} != required or any(o.assessment == "revise" for o in observations):
        raise DevelopmentalEditError("结构审核缺项或仍有待修订单元")
    unresolved = [o.unit_id for o in observations if o.assessment == "uncertain"]
    digest = _sha256_file(path)
    if final_review and unresolved:
        raise _FinalStructureReviewPending(unresolved, digest)
    return digest


def developmental_report_path(workspace: Workspace, chapter_number: int) -> Path:
    return workspace.root / "runs" / "editorial" / f"chapter-{chapter_number:02d}-developmental-report.json"


def _sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return sha256(_v_read_bytes(path)).hexdigest()


def _reader_body(path: Path) -> str:
    text = _v_read_text(path, encoding="utf-8")
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
    from knowledge_product_workflow.reader_artifacts import resolve_reader_resources
    try:
        resources = resolve_reader_resources(workspace, body, chapter_number=chapter_number)
    except (OSError, ValueError) as error:
        resources = []
        findings.append(QualityFinding(code="READER_RESOURCE_INVALID", severity=Severity.BLOCKER,
            location=f"chapters/chapter-{chapter_number:02d}.md", message=str(error)))
    delivered = {resource["section_id"] for resource in resources}
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
            present = {"code": section.section_id in delivered if section.code_delivery is not None
                       and section.code_delivery.mode != "inline_complete" else "code" in capabilities,
                       "prompt": "code" in capabilities,
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


@_verification_read(transaction_view=True)
def record_developmental_check(
    workspace: Workspace, chapter_number: int, source_path: Path
) -> tuple[Path | None, DevelopmentalEditReport | None]:
    """Record a current report for v5 workspaces; legacy v4 workspaces remain unchanged."""
    from knowledge_product_workflow.runtime import require_workflow_mutation_allowed
    require_workflow_mutation_allowed(workspace, "developmental-check")
    try:
        plan = require_current_content_plan(workspace, chapter_number)
    except ContentPlanError as error:
        raise DevelopmentalEditError(str(error)) from error
    if plan is None:
        return None, None
    resolved = source_path.expanduser().resolve()
    if not _v_is_file(resolved) or not resolved.is_relative_to(workspace.root.resolve()):
        raise DevelopmentalEditError("发展性编辑源稿必须位于当前书稿工作区内")
    contract_path, _ = require_current_chapter_contract(workspace, chapter_number)
    body = _reader_body(resolved)
    from knowledge_product_workflow.diagrams import diagram_semantics_digest

    plan_sha, findings = _findings_for_current_plan(workspace, chapter_number, body)
    report = DevelopmentalEditReport(
        chapter_number=chapter_number,
        chapter_body_sha256=_sha256_text(body),
        contract_sha256=proof_contract_digest(workspace, chapter_number, "developmental"),
        content_plan_sha256=plan_sha,
        protected_fragments_sha256=protected_fragments_sha256(body),
        findings=findings,
        diagram_semantics_sha256=diagram_semantics_digest(workspace, body),
        structure_review_sha256=require_structure_review(workspace, chapter_number, body),
    )
    output = developmental_report_path(workspace, chapter_number)
    _v_mkdir(output.parent,parents=True,exist_ok=True)
    _v_write_text(output, report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    workspace.append_event(
        "developmental_edit_checked",
        {"chapter_number": chapter_number, "contract_audit_sha256": _sha256_file(contract_path), "finding_count": len(findings)},
    )
    from knowledge_product_workflow.production_dependencies import finalize_serial_exports
    finalize_serial_exports(workspace, chapter_number=chapter_number)
    return output, report


def require_current_developmental_report(
    workspace: Workspace, chapter_number: int, body: str, contract_path: Path, *, final_review: bool = False
) -> DevelopmentalEditReport | None:
    """Fail closed only for v5 workspaces that explicitly require content planning."""
    try:
        plan = require_current_content_plan(workspace, chapter_number)
    except ContentPlanError as error:
        raise DevelopmentalEditError(str(error)) from error
    if plan is None:
        return None
    path = developmental_report_path(workspace, chapter_number)
    if not _v_is_file(path):
        raise DevelopmentalEditError("缺少当前发展性编辑报告；请先完成章节结构与读者工件检查。")
    try:
        report = DevelopmentalEditReport.model_validate_json(_v_read_text(path, encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DevelopmentalEditError("发展性编辑报告无效") from error
    if report.chapter_number != chapter_number:
        raise DevelopmentalEditError("发展性编辑报告的章节号不一致")
    if report.chapter_body_sha256 != _sha256_text(body):
        raise DevelopmentalEditError("发展性编辑报告已过期：它不再绑定当前正文。")
    if not proof_contract_matches(workspace, chapter_number, report.contract_sha256, "developmental", path):
        raise DevelopmentalEditError("发展性编辑报告已过期：它不再绑定当前章节合约。")
    if not plan_binding_matches(workspace, chapter_number, report.content_plan_sha256):
        raise DevelopmentalEditError("发展性编辑报告已过期：它不再绑定当前内容计划。")
    from knowledge_product_workflow.diagrams import diagram_semantics_digest

    if report.diagram_semantics_sha256 != diagram_semantics_digest(workspace, body):
        raise DevelopmentalEditError("发展性编辑报告已过期：图内标签或关系已变化。")
    if any(finding.severity is Severity.BLOCKER for finding in report.findings):
        raise DevelopmentalEditError("发展性编辑报告仍包含阻断项。")
    unresolved = None
    try:
        structure_digest = require_structure_review(workspace, chapter_number, body, final_review=final_review)
    except _FinalStructureReviewPending as error:
        structure_digest, unresolved = error.review_sha256, error
    if report.structure_review_sha256 != structure_digest:
        raise DevelopmentalEditError("发展性报告未绑定当前结构审核")
    if unresolved is not None:
        raise unresolved
    return report
