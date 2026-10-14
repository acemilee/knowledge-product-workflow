"""明确授权的本地例子执行证据；不自动执行文档，也不声称提供代码沙箱。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

from knowledge_product_workflow.workspace import Workspace


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _python_newlines(text: str) -> str:
    """Python source uses universal newlines; retain every other payload character."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _source(workspace: Workspace, source: Path) -> tuple[Path, bytes]:
    source = source.expanduser().resolve()
    if not source.is_relative_to(workspace.root.resolve()) or not source.is_file():
        raise ValueError("例子源稿必须为当前工作区内的实际文件")
    return source, source.read_bytes()


def example_template(workspace: Workspace, source: Path) -> dict:
    source, raw = _source(workspace, source)
    blocks = re.findall(r"(?m)^\s*```(?:python|py)\s*\n(.*?)^\s*```\s*$", raw.decode("utf-8"), re.DOTALL)
    records = _verified_example_records(workspace, raw.decode("utf-8"))
    examples = []
    for index, code in enumerate(blocks, 1):
        current = records.get(_hash(_python_newlines(code).encode("utf-8")))
        data, path = current if current else ({}, None)
        passed = data.get("status") == "passed" and data.get("exit_code") == 0
        examples.append({"index": index, "code_sha256": _hash(code.encode("utf-8")), "code": code,
                         "binding_state": "current" if passed else data.get("status", "missing_or_invalid"),
                         "needs_execution": not passed,
                         "evidence_path": path.relative_to(workspace.root).as_posix() if path else None,
                         "original_source_sha256": data.get("source_sha256"),
                         "reuse_reason": "verified_code_payload_unchanged" if passed else
                         "latest_execution_not_passed" if current else "no_verified_current_execution"})
    return {"source": source.relative_to(workspace.root).as_posix(), "source_sha256": _hash(raw),
            "source_binding_scope": "source_sha256 records original prose; reuse binds verified code and raw outputs",
            "examples": examples,
            "execution": "仅明确授权并审阅的代码；不是安全沙箱，不自动运行不可信文档"}


def run_python_example(workspace: Workspace, source: Path, index: int, *, approved: bool = False) -> Path:
    if not approved:
        raise ValueError("执行代码需要明确授权并审阅当前代码；不是安全沙箱")
    template = example_template(workspace, source)
    if not 1 <= index <= len(template["examples"]):
        raise ValueError("Python例子索引不存在；先查看example-check-template")
    example = template["examples"][index-1]
    folder = workspace.root / "runs/example-checks" / uuid.uuid4().hex
    folder.mkdir(parents=True)
    script = folder / "example.py"
    script.write_text(example["code"], encoding="utf-8", newline="\n")
    command = [sys.executable, "-I", "-X", "utf8", str(script)]
    record = {"source": template["source"], "source_sha256": template["source_sha256"],
              "code_sha256": example["code_sha256"], "example_index": index, "command": command,
              "script_sha256": _hash(script.read_bytes()),
              "script": script.relative_to(workspace.root).as_posix(), "approved_execution": True,
              "started_at": datetime.now().astimezone().isoformat(), "timeout_seconds": 10,
              "execution_kind": "actual_subprocess", "binding_scope": "code_payload; prose/assumptions need semantic review"}
    stdout_path, stderr_path = folder / "stdout.bin", folder / "stderr.bin"
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            env = {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
            result = subprocess.run(command, cwd=folder, stdout=stdout, stderr=stderr, timeout=10, env=env, check=False)
            record.update(exit_code=result.returncode, status="passed" if result.returncode == 0 else "failed")
        except subprocess.TimeoutExpired:
            record.update(exit_code=None, status="timeout", diagnostic="执行超时；不得盲目重发或推断通过")
    for name, path in (("stdout", stdout_path), ("stderr", stderr_path)):
        raw = path.read_bytes()
        record[name] = raw[:65536].decode("utf-8", "replace").replace("\r\n", "\n")
        record[name + "_sha256"] = _hash(raw)
        record[name + "_truncated"] = len(raw) > 65536
    record["finished_at"] = datetime.now().astimezone().isoformat()
    output = folder / "result.json"
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    workspace.append_event("example_executed", {"evidence": output.relative_to(workspace.root).as_posix(),
                                               "code_sha256": record["code_sha256"], "status": record["status"]})
    return output


def current_example_checks(workspace: Workspace, source: Path) -> list[dict]:
    _, raw = _source(workspace, source)
    return [data for data in example_evidence_for_body(workspace, raw.decode("utf-8")) if data["status"] == "passed"]


def example_evidence_for_body(workspace: Workspace, body: str) -> list[dict]:
    return [data for data, _ in _verified_example_records(workspace, body).values()]


def _verified_example_records(workspace: Workspace, body: str) -> dict[str, tuple[dict, Path]]:
    codes = {_hash(code.encode("utf-8")) for code in re.findall(r"(?m)^\s*```(?:python|py)\s*\n(.*?)^\s*```\s*$", _python_newlines(body), re.DOTALL)}
    result = {}
    for path in sorted((workspace.root / "runs/example-checks").glob("*/result.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            script = (workspace.root / data["script"]).resolve()
            if not script.is_relative_to(workspace.root.resolve()):
                continue
            script_raw = script.read_bytes()
            script_sha256 = _hash(script_raw)
            if (script_sha256 != data["code_sha256"]
                    or script_sha256 != data.get("script_sha256", data["code_sha256"])):
                continue
            # Compare newline semantics only after the original executed bytes are verified.
            code_key = _hash(_python_newlines(script_raw.decode("utf-8")).encode("utf-8"))
            if code_key not in codes:
                continue
            if any(_hash((path.parent / f"{name}.bin").read_bytes()) != data[name + "_sha256"] for name in ("stdout", "stderr")):
                continue
            previous = result.get(code_key)
            if previous is None or data["finished_at"] > previous[0]["finished_at"]:
                result[code_key] = (data, path)
        except (OSError, KeyError, ValueError, TypeError):
            continue
    return {key: result[key] for key in sorted(result)}
