"""Fixed >=32-page publishing stress fixtures; not autonomous teaching quality."""
import ast
import hashlib
import json
import os
from pathlib import Path

import pytest
from pypdf import PdfReader

import knowledge_product_workflow
from knowledge_product_workflow.callouts import render_reader_markdown
from knowledge_product_workflow.detail_constraints import DetailConstraints
from knowledge_product_workflow.example_checks import run_python_example
from knowledge_product_workflow.presentation import PresentationOverrides
from knowledge_product_workflow.publishing import create_quarto_source, render_pdf
from knowledge_product_workflow.visual_audit import audit_pdf_visual
from knowledge_product_workflow.workflows.product_brief import create_product_brief
from knowledge_product_workflow.workspace import Workspace


def manuscript(kind):
    chapters = []
    code = "rows = [('A', 2), ('B', 1)]\nranked = sorted(rows, key=lambda row: row[1], reverse=True)\nprint(ranked[0][0])\n"
    for n in range(1, 31):
        lead = f"本节固定案例编号为 {n}，用于检查同一内容关系在长书中的排版，不把重排结果当作外部事实。"
        causal = "先确认输入是否齐备，再比较候选与问题的对应关系。候选排列只说明当前规则下的相对顺序，不能直接推出材料支持结论；因此需要保留原文中的条件，并核对回答有没有超出该条件。若缺少支持段落，应先报告缺项，而不是用更肯定的措辞补出结论。"
        closure = "在这些条件不变时，读者可以沿同一核对方法比较两项选择；条件发生变化时则应回到对应步骤，而不是把前一页结论当作新的证据。"
        if kind == "explanation":
            content = (f"## {n}.1 条件与回指\n\n" + causal + "\n\n" +
                "连续解释要保留从输入、比较到结论的回指。删去重复措辞可以减轻阅读负担，但不能把支撑关系拆成互不相干的清单。这里的承接指向上一段所述条件，而不是增加新的独立判断。\n\n" +
                f"输入对象仍采用当前固定记录 INTRO{n:02d}：\n\n> OBJECT{n:02d}：只有记录中给出的条件才能用于这次比较。\n\n" +
                "核对时回到这个对象，确认所需条件确实存在；不存在的部分保持未知。\n\n" + closure)
        else:
            program = f"# CODE{n:02d}_BEGIN\n" + code + f"# CODE{n:02d}_END\n"
            content = (f"## {n}.1 固定程序与结果\n\n" + causal + "\n\n" +
                "输入为两条候选记录，数值只是本夹具的固定排序依据，不是模型评分。下面完整程序只操作内存数据，不访问网络或工作区外文件。\n\n```python\n" + program + "```\n\n" +
                "预期输出为 `A`；改变候选的数值后应重新核对排序，不能沿用原来的结果。\n\n" +
                "| 候选 | 固定值 | 核对用途 |\n| --- | --- | --- |\n| A | 2 | 第一条输入 |\n| B | 1 | 第二条输入 |\n\n" + closure)
        chapters.append(f"# {n}. {'连续解释' if kind == 'explanation' else '程序核对'}单元\n\n" +
            "::: {.kp-chapter-lead}\n" + lead + "\n:::\n\n" + content + "\n")
    return "\n".join(chapters), code


@pytest.mark.parametrize("kind", ["explanation", "practice"])
def test_final_candidate_long_book_clear_pages_code_copy_and_single_byline(tmp_path, kind):
    candidate_record = os.environ.get("KP_TEST_CANDIDATE_RECORD")
    if not candidate_record:
        pytest.skip("最终包长书验收须显式提供KP_TEST_CANDIDATE_RECORD并使用对应隔离运行时；skip表示未覆盖")
    record = Path(candidate_record)
    candidate = json.loads(record.read_text(encoding="utf-8"))
    assert Path(knowledge_product_workflow.__file__).resolve().is_relative_to(Path(candidate["site_root"])), "必须使用最终包隔离测试入口，不以源码通过冒包验证"
    workspace = Workspace.create(tmp_path, "long-" + kind)
    byline = "LONG_BOOK_FIXTURE_AUTHOR"
    create_product_brief(workspace, "解释长书排版回归" if kind == "explanation" else "实践长书排版回归",
        DetailConstraints(delivery_format="both"), presentation=PresentationOverrides(
            cover_summary_zh="固定长书验证页面覆盖、阅读闭合与完整代码，不是待发布的教学产品。",
            copyright_line="© 2026 " + byline))
    body, code = manuscript(kind)
    md = workspace.root / "delivery/book.md"
    md.write_text(render_reader_markdown(body), encoding="utf-8")
    qmd = create_quarto_source(workspace, md, qmd_markdown=body)
    pdf = render_pdf(qmd, workspace.root / "delivery", workspace)
    extracted_pages = [page.extract_text() or "" for page in PdfReader(pdf).pages]
    assert len(extracted_pages) >= 32
    assert "".join(extracted_pages).count(byline) == 1
    if kind == "explanation":
        normalized = ["".join(text.split()) for text in extracted_pages]
        for n in range(1, 31):
            assert any(f"INTRO{n:02d}" in page and f"OBJECT{n:02d}" in page for page in normalized), "引入与对象不得分离"
    else:
        for n in range(1, 31):
            text = "\n".join(extracted_pages)
            start, end = f"# CODE{n:02d}_BEGIN", f"# CODE{n:02d}_END"
            copied = text.split(start, 1)[1].split(end, 1)[0].strip()
            assert ast.dump(ast.parse(copied)) == ast.dump(ast.parse(code)), "最终PDF复制程序必须与实际源码一致"
        original = workspace.root / "runs/authorized-example.md"
        original.write_text("```python\n" + code + "```\n", encoding="utf-8")
        execution = run_python_example(workspace, original, 1, approved=True)
        assert json.loads(execution.read_text(encoding="utf-8"))["stdout"].strip() == "A"
    report = audit_pdf_visual(workspace, sample_limit=len(extracted_pages))
    assert report.sampled_pages == list(range(1, len(extracted_pages) + 1))
    assert all(page.high_resolution_image and page.high_resolution_dpi >= 144 for page in report.pages)
    assert not any(f.severity.value == "blocker" for f in report.findings)
    evidence = {"kind": kind, "workspace": str(workspace.root), "pdf": str(pdf), "markdown": str(md),
        "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(), "page_count": len(extracted_pages),
        "candidate_wheel_sha256": candidate["wheel_sha256"], "actual_package": knowledge_product_workflow.__file__,
        "clear_pages": report.sampled_pages, "byline_occurrences": 1,
        "fixed_render_fixture": True, "actual_visual_observation": "pending", "human_final_approval": "pending"}
    (workspace.root / "runs/LONG_READING_RESULT.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
