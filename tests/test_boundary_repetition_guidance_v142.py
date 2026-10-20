import json

import pytest

from knowledge_product_workflow.developmental import book_reading_index, boundary_repetition_context
from knowledge_product_workflow.production_context import freeze_candidate_context
from knowledge_product_workflow.workspace import Workspace


def test_exact_candidates_are_bounded_and_conditions_are_not_erased(tmp_path):
    w = Workspace.create(tmp_path, "boundaries")
    statement = "本案例仅验证离线默认入口，不代表真实设备训练已经完成。"
    (w.root/"chapters/chapter-01.md").write_text("# 1. 原章\n\n## 1.1 范围\n\n"+statement, encoding="utf-8")
    body = "# 2. 当前章\n\n" + "\n\n".join(f"## 2.{n} 练习{n}\n\n{statement}" for n in range(1, 11))
    result = boundary_repetition_context(w, 2, body)
    assert result["total_candidates"] == 10 and len(result["candidates"]) == 8 and result["truncated"]
    assert result["advisory_only"]
    assert len(result["candidates"][0]["previous"][0]["quote"]) <= 160
    different = body.replace("离线默认入口", "GPU专用入口")
    assert not boundary_repetition_context(w, 2, different)["candidates"]
    code = "# 2. 当前\n\n## 2.1 提示\n\n```text\n"+statement+"\n```\n"
    assert not boundary_repetition_context(w, 2, code)["candidates"]
    assert all("boundary_snippets" not in row for row in book_reading_index(w, 2, body))


def test_native_candidate_freezes_advice_before_live_book_changes(tmp_path):
    w = Workspace.create(tmp_path, "frozen")
    text = "此处仅用于离线演示，不代表所有生产路径均已验证。"
    previous = w.root/"chapters/chapter-01.md"
    previous.write_text("# 1. 先前\n\n## 1.1 范围\n\n"+text, encoding="utf-8")
    source = w.root/"runs/candidate.md"
    source.write_text("# 2. 当前\n\n## 2.1 范围\n\n"+text, encoding="utf-8")
    ctx = freeze_candidate_context(w, chapter_number=2, candidate_version=1, source=source,
        input_paths=[], run_id="run-advice", task_id="writer-advice")
    assert ctx.boundary_repetition_advice["candidates"]
    frozen = json.dumps(ctx.boundary_repetition_advice, ensure_ascii=False)
    previous.write_text("全新正文", encoding="utf-8")
    ctx.verify()  # Advisory is a historical bounded hint, not an all-book live dependency.
    assert json.dumps(ctx.boundary_repetition_advice, ensure_ascii=False) == frozen
    assert "chapters/chapter-01.md" not in ctx.input_bindings
    with pytest.raises(ValueError, match="清单"):
        ctx.read_input("chapters/chapter-01.md")  # Actual comparison must request a new frozen version.
    ctx.boundary_repetition_advice["candidates"] = []
    with pytest.raises(ValueError, match="篡改"):
        ctx.verify()
