"""实际程序原始证据先核验；只在当前代码比较层统一Python换行。"""

import json
from hashlib import sha256
from pathlib import Path

import pytest
from typer.testing import CliRunner

from knowledge_product_workflow.cli import app
from knowledge_product_workflow.developmental import _reader_body
from knowledge_product_workflow.example_checks import (
    current_example_checks,
    example_evidence_for_body,
    example_template,
    run_python_example,
)
from knowledge_product_workflow.workspace import Workspace
from knowledge_product_workflow.runtime import _atomic_json_write, _orchestration_state_path, _read_orchestration_state
from tests.test_structure_review_v133 import _fixture


CODE = 'value = 0.6\nprint(f"score={value:.3f}")\nprint("literal \\n escape")\n'


def source_with_code(workspace, code=CODE, eol="\r\n"):
    source = workspace.root / "runs/example-source.md"
    text = "## 1.1 核对\n\n```python\n" + code + "```\n"
    source.write_bytes(text.replace("\n", eol).encode("utf-8"))
    return source


def executed(tmp_path, code=CODE, eol="\r\n"):
    workspace = Workspace.create(tmp_path, "examples")
    source = source_with_code(workspace, code, eol)
    output = run_python_example(workspace, source, 1, approved=True)
    return workspace, source, output, json.loads(output.read_text(encoding="utf-8"))


def test_actual_crlf_cli_execution_populates_current_developmental_index(tmp_path):
    workspace, source, body = _fixture(tmp_path)
    state = _read_orchestration_state(workspace)
    _atomic_json_write(_orchestration_state_path(workspace), state.model_copy(update={"reading_review_rubric_revision": 3}))
    source.write_bytes((body + "\n运行例子后得到数值为0.600，核对当前输出。\n\n```python\n" + CODE + "```\n").replace("\n", "\r\n").encode("utf-8"))
    source_before = source.read_bytes()
    runner = CliRunner()
    listed = runner.invoke(app, ["example-check-template", "--workspace", str(workspace.root), "--from", str(source)])
    assert listed.exit_code == 0, listed.output
    listing = json.loads(listed.output)
    command = ["run-example-check", "1", "--workspace", str(workspace.root),
               "--from", str(source), "--example-index", "1", "--approve-execution"]
    actual = runner.invoke(app, command)
    assert actual.exit_code == 0, actual.output
    receipt = Path(actual.output.strip())
    receipt_before = receipt.read_bytes()
    record = json.loads(receipt.read_text(encoding="utf-8"))
    script = workspace.root / record["script"]
    assert record["exit_code"] == 0 and record["status"] == "passed"
    assert record["timeout_seconds"] == 10 and record["approved_execution"] is True
    assert record["source_sha256"] == listing["source_sha256"] == sha256(source_before).hexdigest()
    assert record["code_sha256"] == listing["examples"][0]["code_sha256"] == sha256(script.read_bytes()).hexdigest()
    review = runner.invoke(app, ["developmental-review-template", "1", "--workspace", str(workspace.root), "--from", str(source)])
    assert review.exit_code == 0, review.output
    current = json.loads(review.output)
    print(json.dumps({"actual_cli": command, "script_sha256": sha256(script.read_bytes()).hexdigest(),
                      "record": record, "execution_evidence": current["execution_evidence"]}, ensure_ascii=False))
    assert len(current["execution_evidence"]) == 1
    assert current["execution_evidence"][0] == record
    assert any(item["ref"] == "example:0" and item["kind"] == "actual_execution"
               for item in current["assertion_support_context"])
    assert record["script_sha256"] == sha256(script.read_bytes()).hexdigest()
    assert receipt.read_bytes() == receipt_before
    assert source.read_bytes() == source_before


@pytest.mark.parametrize("source_eol", ["\n", "\r\n"])
@pytest.mark.parametrize("body_eol", ["\n", "\r\n", "\r"])
def test_current_evidence_accepts_only_python_newline_equivalence(tmp_path, source_eol, body_eol):
    workspace, source, output, record = executed(tmp_path, eol=source_eol)
    before = output.read_bytes()
    script = workspace.root / record["script"]
    script_before = script.read_bytes()
    body = _reader_body(source).replace("\n", body_eol)
    assert example_evidence_for_body(workspace, body) == [record]
    assert current_example_checks(workspace, source) == [record]
    assert output.read_bytes() == before and script.read_bytes() == script_before


def test_old_valid_record_without_additive_script_hash_remains_compatible(tmp_path):
    workspace, source, output, record = executed(tmp_path)
    record.pop("script_sha256", None)
    output.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    assert current_example_checks(workspace, source) == [record]
    assert example_evidence_for_body(workspace, _reader_body(source)) == [record]


@pytest.mark.parametrize("replacement", [
    ('value = 0.6', 'value = 0.8'),
    ('value = 0.6', 'value  = 0.6'),
    ('print(f"score', ' print(f"score'),
    ('literal \\n escape', 'literal \\r\\n escape'),
    ('value = 0.6', 'value = 0.6  # different comment'),
])
def test_non_newline_payload_changes_are_not_equivalent(tmp_path, replacement):
    workspace, source, _, _ = executed(tmp_path)
    body = _reader_body(source).replace(*replacement)
    assert example_evidence_for_body(workspace, body) == []


@pytest.mark.parametrize("target", ["script", "stdout", "stderr", "script_hash"])
def test_raw_script_and_output_tampering_is_rejected_before_normalization(tmp_path, target):
    workspace, source, output, record = executed(tmp_path)
    if target == "script":
        script = workspace.root / record["script"]
        script.write_bytes(script.read_bytes().replace(b"\r\n", b"\n"))
    elif target == "script_hash":
        record["script_sha256"] = "f" * 64
        output.write_text(json.dumps(record), encoding="utf-8")
    else:
        path = output.parent / f"{target}.bin"
        path.write_bytes(path.read_bytes() + b"tampered")
    assert example_evidence_for_body(workspace, _reader_body(source)) == []
    assert current_example_checks(workspace, source) == []


@pytest.mark.parametrize("target", ["script", "stdout", "stderr"])
def test_missing_raw_evidence_does_not_sign_current_pass(tmp_path, target):
    workspace, source, output, record = executed(tmp_path)
    path = workspace.root / record["script"] if target == "script" else output.parent / f"{target}.bin"
    path.rename(path.with_name(path.name + ".missing"))
    assert example_evidence_for_body(workspace, _reader_body(source)) == []
    assert current_example_checks(workspace, source) == []


@pytest.mark.parametrize("code,status", [
    ("raise ValueError('actual failure')\n", "failed"),
    ("import time\ntime.sleep(30)\n", "timeout"),
])
def test_failed_and_timed_out_actual_execution_remains_negative(tmp_path, code, status):
    workspace, source, _, record = executed(tmp_path, code=code)
    assert record["status"] == status and record["exit_code"] != 0
    assert record["timeout_seconds"] == 10
    assert example_evidence_for_body(workspace, _reader_body(source)) == [record]
    assert current_example_checks(workspace, source) == []


def test_latest_failure_for_same_code_across_eols_does_not_reuse_old_pass(tmp_path):
    workspace = Workspace.create(tmp_path, "examples")
    marker = workspace.root / "runs/fail-marker.txt"
    code = ("from pathlib import Path\n"
            f"if Path({str(marker)!r}).exists():\n"
            "    raise ValueError('changed environment fails')\n"
            "print('initial execution passed')\n")
    source = source_with_code(workspace, code, "\r\n")
    first = run_python_example(workspace, source, 1, approved=True)
    passed = json.loads(first.read_text(encoding="utf-8"))
    assert passed["status"] == "passed"
    marker.write_text("fail", encoding="utf-8")
    source_with_code(workspace, code, "\n")
    second = run_python_example(workspace, source, 1, approved=True)
    failed = json.loads(second.read_text(encoding="utf-8"))
    assert failed["status"] == "failed"
    assert passed["code_sha256"] != failed["code_sha256"]
    assert example_evidence_for_body(workspace, _reader_body(source)) == [failed]
    assert current_example_checks(workspace, source) == []
    assert json.loads(first.read_text(encoding="utf-8")) == passed
