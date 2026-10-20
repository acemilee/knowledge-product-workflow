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

from knowledge_product_workflow.verification_context import glob_files as _v_glob_files
from knowledge_product_workflow.verification_context import read_bytes as _v_read_bytes
from knowledge_product_workflow.verification_context import read_text as _v_read_text
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
    body = raw.decode("utf-8")
    from knowledge_product_workflow.reader_artifacts import reader_fences, resolve_reader_resources
    resources = resolve_reader_resources(workspace, body)
    excerpt_spans = {span for item in resources for span in item["excerpt_spans"]}
    if resources:
        blocks = [f.code for f in reader_fences(body) if f.closed and f.language in {"python", "py"}
                  and (f.start, f.end) not in excerpt_spans]
    else:
        blocks = re.findall(r"(?m)^\s*```(?:python|py)\s*\n(.*?)^\s*```\s*$", body, re.DOTALL)
    records = _verified_example_records(workspace, body)
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
    result = {"source": source.relative_to(workspace.root).as_posix(), "source_sha256": _hash(raw),
            "source_binding_scope": "source_sha256 records original prose; reuse binds verified code and raw outputs",
            "examples": examples,
            "execution": "仅明确授权并审阅的代码；不是安全沙箱，不自动运行不可信文档"}
    if resources:
        verified = _verified_resource_records(workspace, body, resources=resources)
        result["resources"] = [{"resource_path": item["resource_path"], "inputs": item["inputs"],
            "execution_scope": _observed_execution_scope(verified.get(_resource_key(item))),
            "execution_supported": Path(item["resource_path"]).suffix.casefold() == ".py",
            "binding_state": "current" if _resource_key(item) in verified else "missing_or_invalid",
            "needs_execution": _resource_key(item) not in verified if Path(item["resource_path"]).suffix.casefold() == ".py" else None,
            "unsupported_execution_action": None if Path(item["resource_path"]).suffix.casefold() == ".py" else
                "当前自动执行器仅支持Python；其他语言保持未验证，不重试Python命令或宣称已实跑。"} for item in resources]
        result["presentation_excerpts"] = len(excerpt_spans)
        result["branch_review_guidance"] = resource_branch_review_guidance()
    return result


_RESOURCE_LAUNCHER = ("import runpy,sys;folder,source=sys.argv[1:];"
                      "sys.path.insert(0,folder);sys.argv=[source];runpy.run_path(source,run_name='__main__')")


def resource_branch_review_guidance() -> dict:
    return {"actual_model_training_inferred": False,
        "scope": "实际默认入口成功不等于其他参数、模型、设备或训练分支已验证。",
        "questions": ["请求模式与实际执行模式是否一致？", "通用完成能否支持专用完成标签？",
                      "失败/未运行是否仍保留未验证？按承诺路径做便宜正反例，不为填表启动重型路径。"],
        "synthetic_rule": "合成/mock只证明状态分支；不签真实训练。单路径程序不强造分支矩阵。"}


def _observed_execution_scope(record: dict | None) -> dict:
    if record is None:
        return {"entrypoint": "unknown", "argv": None, "coverage": "not_executed"}
    if "execution_scope" in record:
        return record["execution_scope"]
    command = record.get("command", [])
    known = len(command) == 8 and command[1:5] == ["-I", "-X", "utf8", "-c"] and command[5] == _RESOURCE_LAUNCHER
    return {"entrypoint": "default" if known else "unknown", "argv": [] if known else None,
            "coverage": "legacy_default_entrypoint_only" if known else "unknown"}


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
    return ([data for data, _ in _verified_example_records(workspace, body).values()]
            + list(_verified_resource_records(workspace, body).values()))


def _resource_key(resource: dict) -> str:
    return _hash(json.dumps({"path": resource["resource_path"], "inputs": resource["inputs"]},
                            sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _verified_resource_records(workspace: Workspace, body: str, *, resources=None) -> dict[str, dict]:
    from knowledge_product_workflow.reader_artifacts import resolve_reader_resources
    resources = resolve_reader_resources(workspace, body, partial_scope=True) if resources is None else resources
    if not resources:
        return {}
    try:
        events = [json.loads(line) for line in _v_read_text(workspace.root / "runs/events.jsonl", encoding="utf-8").splitlines() if line.strip()]
        recorded = {event["payload"]["evidence"]: event["payload"].get("result_sha256")
                    for event in events if isinstance(event, dict) and event.get("event_type") == "example_executed"
                    and isinstance(event.get("payload"), dict) and "evidence" in event["payload"]}
    except (OSError, ValueError, TypeError):
        return {}
    wanted = {_resource_key(item): item for item in resources}
    result = {}
    for path in sorted(_v_glob_files(workspace.root / "runs/example-checks", "*/result.json")):
        try:
            raw = _v_read_bytes(path)
            if recorded.get(path.relative_to(workspace.root).as_posix()) != _hash(raw):
                continue
            data = json.loads(raw)
            if "execution_scope" in data and data["execution_scope"] != {
                    "entrypoint": "default", "argv": [], "coverage": "default_entrypoint_only"}:
                continue
            key = data.get("resource_inputs_sha256")
            item = wanted.get(key)
            if (item is None or data.get("binding_scope") != "reader_resource"
                    or data.get("status") != "passed" or data.get("exit_code") != 0
                    or data.get("execution_kind") != "actual_subprocess"
                    or data.get("resource_inputs") != item["inputs"]):
                continue
            valid = True
            expected_files = {(path.parent / relative).relative_to(workspace.root).as_posix(): value
                              for relative, value in item["inputs"].items()}
            script = (path.parent / item["resource_path"]).relative_to(workspace.root).as_posix()
            command = data.get("command")
            expected_script = workspace.root / script
            if ("execution_scope" in data and (not isinstance(command, list) or len(command) != 8
                    or command[1:6] != ["-I", "-X", "utf8", "-c", _RESOURCE_LAUNCHER]
                    or Path(command[6]).resolve() != expected_script.parent.resolve()
                    or Path(command[7]).resolve() != expected_script.resolve())):
                continue
            if (data.get("execution_files") != expected_files or data.get("script") != script
                    or data.get("script_sha256") != item["inputs"][item["resource_path"]]
                    or data.get("code_sha256") != data.get("script_sha256")
                    or data.get("approved_execution") is not True):
                continue
            for stored, expected in data.get("execution_files", {}).items():
                copy = (workspace.root / stored).resolve()
                if not copy.is_relative_to(path.parent.resolve()) or _hash(_v_read_bytes(copy)) != expected:
                    valid = False
            if len(data.get("execution_files", {})) != len(item["inputs"]):
                valid = False
            for name in ("stdout", "stderr"):
                raw_output = _v_read_bytes(path.parent / f"{name}.bin")
                if (_hash(raw_output) != data.get(name + "_sha256")
                        or data.get(name) != raw_output[:65536].decode("utf-8", "replace").replace("\r\n", "\n")
                        or data.get(name + "_truncated") != (len(raw_output) > 65536)):
                    valid = False
            if valid and (key not in result or data["finished_at"] > result[key]["finished_at"]):
                result[key] = data
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result


def run_python_resource(workspace: Workspace, source: Path, resource_path: str, *, approved: bool = False) -> Path:
    """Execute one declared, reviewed resource and explicit inputs in a private copy."""
    if not approved:
        raise ValueError("执行完整资源需要明确授权并审阅代码；不是安全沙箱")
    source, raw = _source(workspace, source)
    from knowledge_product_workflow.reader_artifacts import resolve_reader_resources
    matches = [item for item in resolve_reader_resources(workspace, raw.decode("utf-8"))
               if item["resource_path"] == resource_path]
    if not matches or len({_resource_key(item) for item in matches}) != 1 or Path(resource_path).suffix.casefold() != ".py":
        raise ValueError("须选择当前章节唯一声明的Python完整资源，不执行片段或未声明路径")
    resource = matches[0]
    folder = workspace.root / "runs/example-checks" / uuid.uuid4().hex
    folder.mkdir(parents=True)
    execution_files = {}
    for relative, expected in resource["inputs"].items():
        original = (workspace.root / relative).resolve()
        data = _v_read_bytes(original)
        if _hash(data) != expected:
            raise ValueError("审阅后资源或输入字节已改变；停止，不运行旧授权版本")
        copy = folder / relative
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(data)
        execution_files[copy.relative_to(workspace.root).as_posix()] = expected
    script = folder / resource_path
    launcher = _RESOURCE_LAUNCHER
    command = [sys.executable, "-I", "-X", "utf8", "-c", launcher, str(script.parent), str(script)]
    record = {"source": source.relative_to(workspace.root).as_posix(), "source_sha256": _hash(raw),
        "resource_path": resource_path, "resource_inputs": resource["inputs"],
        "resource_inputs_sha256": _resource_key(resource), "execution_files": execution_files,
        "script": script.relative_to(workspace.root).as_posix(), "script_sha256": _hash(script.read_bytes()),
        "code_sha256": _hash(script.read_bytes()), "approved_execution": True,
        "binding_scope": "reader_resource", "execution_kind": "actual_subprocess", "command": command,
        "execution_scope": {"entrypoint": "default", "argv": [], "coverage": "default_entrypoint_only"},
        "started_at": datetime.now().astimezone().isoformat(), "timeout_seconds": 10}
    stdout_path, stderr_path = folder / "stdout.bin", folder / "stderr.bin"
    env = {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            done = subprocess.run(command, cwd=script.parent, stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, timeout=10, env=env, check=False)
            record.update(exit_code=done.returncode, status="passed" if done.returncode == 0 else "failed")
        except subprocess.TimeoutExpired:
            record.update(exit_code=None, status="timeout", diagnostic="资源执行超时，不自动重试或推断通过")
    for name, path in (("stdout", stdout_path), ("stderr", stderr_path)):
        data = path.read_bytes()
        record[name] = data[:65536].decode("utf-8", "replace").replace("\r\n", "\n")
        record[name + "_sha256"] = _hash(data)
        record[name + "_truncated"] = len(data) > 65536
    if any(not (workspace.root / stored).is_file() or _hash((workspace.root / stored).read_bytes()) != expected
           for stored, expected in execution_files.items()):
        record.update(status="failed", diagnostic="执行复制件改变了已声明输入，不能复用该执行证明")
    record["finished_at"] = datetime.now().astimezone().isoformat()
    output = folder / "result.json"
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    workspace.append_event("example_executed", {"evidence": output.relative_to(workspace.root).as_posix(),
        "resource_inputs_sha256": record["resource_inputs_sha256"], "status": record["status"],
        "result_sha256": _hash(output.read_bytes())})
    return output


def _verified_example_records(workspace: Workspace, body: str) -> dict[str, tuple[dict, Path]]:
    codes = {_hash(code.encode("utf-8")) for code in re.findall(r"(?m)^\s*```(?:python|py)\s*\n(.*?)^\s*```\s*$", _python_newlines(body), re.DOTALL)}
    result = {}
    for path in sorted(_v_glob_files(workspace.root / "runs/example-checks", "*/result.json")):
        try:
            data = json.loads(_v_read_text(path, encoding="utf-8"))
            script = workspace.root / data["script"]
            if not script.resolve().is_relative_to(workspace.root.resolve()):
                continue
            script_raw = _v_read_bytes(script)
            script_sha256 = _hash(script_raw)
            if (script_sha256 != data["code_sha256"]
                    or script_sha256 != data.get("script_sha256", data["code_sha256"])):
                continue
            # Compare newline semantics only after the original executed bytes are verified.
            code_key = _hash(_python_newlines(script_raw.decode("utf-8")).encode("utf-8"))
            if code_key not in codes:
                continue
            if any(_hash(_v_read_bytes(path.parent / f"{name}.bin")) != data[name + "_sha256"] for name in ("stdout", "stderr")):
                continue
            previous = result.get(code_key)
            if previous is None or data["finished_at"] > previous[0]["finished_at"]:
                result[code_key] = (data, path)
        except (OSError, KeyError, ValueError, TypeError):
            continue
    return {key: result[key] for key in sorted(result)}
