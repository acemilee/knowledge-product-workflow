"""明确授权的当前代码才可运行，失败/变更不冒充有效执行证据。"""
import json

import pytest

from knowledge_product_workflow.example_checks import current_example_checks, run_python_example
from knowledge_product_workflow.workspace import Workspace


def test_actual_example_execution_is_bound_to_current_code(tmp_path):
    workspace = Workspace.create(tmp_path, "examples")
    source = workspace.root / "runs/chapter-source.md"
    source.write_text("## 1.1 检索\n\n```python\nfrom math import hypot\nq=(1,0)\ndocs=[('doc-1',(1,0)),('doc-2',(0.8,0.6)),('doc-3',(0,1))]\nfor name,v in docs:\n print(name, f'{sum(a*b for a,b in zip(q,v))/(hypot(*q)*hypot(*v)):.2f}')\n```\n", encoding="utf-8")
    evidence = run_python_example(workspace, source, 1, approved=True)
    data = json.loads(evidence.read_text(encoding="utf-8"))
    assert data["status"] == "passed"
    assert data["stdout"] == "doc-1 1.00\ndoc-2 0.80\ndoc-3 0.00\n"
    assert len(current_example_checks(workspace, source)) == 1
    source.write_text(source.read_text(encoding="utf-8").replace("q=(1,0)", "q=(0,1)"), encoding="utf-8")
    assert current_example_checks(workspace, source) == []


def test_execution_is_never_automatic_or_outside_workspace(tmp_path):
    workspace = Workspace.create(tmp_path, "examples")
    source = workspace.root / "runs/code.md"
    source.write_text("```python\nprint(1)\n```\n", encoding="utf-8")
    with pytest.raises(ValueError, match="明确授权"):
        run_python_example(workspace, source, 1, approved=False)
    with pytest.raises(ValueError, match="工作区"):
        run_python_example(workspace, tmp_path / "outside.md", 1, approved=True)


def test_failed_actual_execution_is_retained_and_not_current_pass(tmp_path):
    workspace = Workspace.create(tmp_path, "examples")
    source = workspace.root / "runs/code.md"
    source.write_text("```python\nraise ValueError('example failure')\n```\n", encoding="utf-8")
    output = run_python_example(workspace, source, 1, approved=True)
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["status"] == "failed" and data["exit_code"] != 0
    assert "example failure" in data["stderr"]
    assert current_example_checks(workspace, source) == []
