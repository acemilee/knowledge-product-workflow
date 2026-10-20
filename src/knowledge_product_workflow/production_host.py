"""Scoped Codex stdio transport and native lifecycle evidence; replay never grants trust."""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import subprocess
import threading
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from knowledge_product_workflow.verification_context import mkdir as _v_mkdir
from knowledge_product_workflow.verification_context import (
    write_open as _v_write_open,
)


class NativeProtocolError(ValueError):
    """Unknown outcome or incompatible host protocol; never retry a model turn blindly."""


def summarize_native_usage(snapshots, turn_ids, scopes, *, top_level_model_turn_requests):
    """Project consumed native totals into a bounded diagnostic, without RPC or authority."""
    fields = {"input_tokens": "inputTokens", "cached_input_tokens": "cachedInputTokens",
              "output_tokens": "outputTokens", "reasoning_output_tokens": "reasoningOutputTokens",
              "total_tokens": "totalTokens", "cache_write_input_tokens": "cacheWriteInputTokens"}
    role_names = ("control", "writer", "semantic", "reviewer", "qualification", "unattributed_child")
    roles = {role: {"expected_threads": 0, "observed_threads": 0, "missing_threads": 0,
                    "stale_turn_threads": 0, "incompatible_threads": 0} for role in role_names}
    rows = {role: [] for role in role_names}
    bound, conflicts = {}, set()
    for identity, role, turn in scopes:
        role = role if role in roles else "unattributed_child"
        if identity in bound:
            if bound[identity] != (role, turn):
                conflicts.add(identity)
            continue  # Never count a thread twice, even if its binding is invalid.
        bound[identity] = (role, turn)

    def count(value):
        return type(value) is int and 0 <= value <= 2**63 - 1

    for identity, (role, turn) in bound.items():
        group = roles[role]
        group["expected_threads"] += 1
        if identity in conflicts:
            group["incompatible_threads"] += 1
            continue
        if identity not in snapshots:
            group["missing_threads"] += 1
            continue
        if not isinstance(turn, str) or not turn or turn_ids.get(identity) != turn:
            group["stale_turn_threads"] += 1
            continue
        payload = snapshots[identity]
        total = payload.get("total") if isinstance(payload, dict) else None
        if (not isinstance(total, dict)
                or not all(count(total.get(key)) for key in fields.values() if key != "cacheWriteInputTokens")
                or ("cacheWriteInputTokens" in total and not count(total["cacheWriteInputTokens"]))
                or total["cachedInputTokens"] > total["inputTokens"]
                or total["reasoningOutputTokens"] > total["outputTokens"]):
            group["incompatible_threads"] += 1
            continue
        row = {name: total.get(key) for name, key in fields.items()}
        row["uncached_input_tokens"] = row["input_tokens"] - row["cached_input_tokens"]
        rows[role].append(row)
        group["observed_threads"] += 1
    for role, group in roles.items():
        values = rows[role]
        group["observed_totals"] = {
            key: sum(row[key] for row in values) if values and all(row[key] is not None for row in values) else None
            for key in (*fields, "uncached_input_tokens")}
        group["cache_write_observed_threads"] = sum(row["cache_write_input_tokens"] is not None for row in values)
    expected = sum(group["expected_threads"] for group in roles.values())
    return {"schema_version": 1, "scope": "locally_bound_threads_latest_consumed_native_total",
            "all_bound_threads_observed": bool(expected) and all(
                group["observed_threads"] == group["expected_threads"] for group in roles.values()),
            "top_level_model_turn_requests": top_level_model_turn_requests if count(top_level_model_turn_requests) else None,
            "provider_request_count": None, "account_usage": None, "roles": roles,
            "limitations": "Not a live query, full run/account total, cost, quota or qualification. "
                           "Partial observed totals omit unknown threads; last is not added; reasoning is part of output."}


def redacted_protocol(value, *, secret_values=()):
    """Keep useful telemetry without persisting authentication material or login challenges."""
    if isinstance(value,dict):
        result={}
        for key,item in value.items():
            normalized=re.sub(r"[_-]","",key).lower()
            sensitive=normalized in {"apikey","accesstoken","refreshtoken","idtoken","token",
                "authorization","bearertoken","authbearertoken","authurl","usercode","password","secret",
                "codeverifier","email"}
            result[key]="[redacted]" if sensitive else redacted_protocol(item, secret_values=secret_values)
        return result
    if isinstance(value,list):
        return [redacted_protocol(item, secret_values=secret_values) for item in value]
    if isinstance(value,str):
        for secret in secret_values:
            if secret:
                value=value.replace(secret,"[redacted]")
        value=re.sub(r"(?i)Bearer\s+[^\s\"']+","Bearer [redacted]",value)
        value=re.sub(r"sk-[A-Za-z0-9_-]{8,}","[redacted]",value)
        value=re.sub(r"[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}","[redacted]",value)
        value=re.sub(r"(?i)((?:access_token|refresh_token|id_token|api_key)\s*[:=]\s*)[^\s,}]+",r"\1[redacted]",value)
        value=re.sub(r"https://(?:auth\.openai\.com|chatgpt\.com)/[^\s\"']*(?:authorize|auth)[^\s\"']*",
            "[redacted-auth-url]",value)
    return value


class NativeAppServerClient:
    """One owned process, one serial RPC stream, no model requests unless explicitly budgeted."""
    QUERY_METHODS = frozenset({"initialize", "thread/start", "thread/read", "thread/turns/list",
        "thread/items/list", "turn/interrupt", "thread/unsubscribe", "thread/archive",
        "windowsSandbox/readiness", "config/read", "account/read", "model/list",
        "account/login/start", "account/login/cancel"})

    def __init__(self, command: list[str], *, cwd: Path, private_home: Path,
                 native_binary_sha256: str | None = None, model_turn_limit: int = 0):
        self.command = list(command)
        self.cwd, self.home = cwd.resolve(), private_home.resolve()
        if (not command or not self.home.is_relative_to(self.cwd) or model_turn_limit < 0):
            raise NativeProtocolError("私有宿主目录必须在当前工作区内，调用数不能为负")
        self.native_binary_sha256 = native_binary_sha256
        self.native_origin = bool(native_binary_sha256 and Path(command[0]).name.lower() == "codex.exe"
            and hashlib.sha256(Path(command[0]).read_bytes()).hexdigest() == native_binary_sha256)
        if native_binary_sha256 and not self.native_origin:
            raise NativeProtocolError("原生Codex二进制身份不匹配")
        self.model_turn_limit, self.model_turns = model_turn_limit, 0
        self.process = None
        self._queue = queue.Queue(maxsize=4096)
        self._events = deque()
        self._responses = {}
        self._sequence = 0
        self.request_receipts = []
        self._stop = threading.Event()
        self._blocked = False
        self._reader_thread = None
        self._stderr_thread = None
        self._errors = None
        self._rpc_lock = threading.Lock()
        self._ephemeral_auth_verified = False
        self._metadata_preflight_thread = None
        self._login_attempted = False
        self._login_id = None
        self._login_state = "not_started"
        self._auth_summary = {"authenticated":False,"requires_auth":None}
        self._qualification_executor = None

    def __enter__(self):
        _v_mkdir(self.home,parents=True,exist_ok=True)
        temporary = self.home / "tmp"
        _v_mkdir(temporary,exist_ok=True)
        env = os.environ.copy()
        if self._qualification_executor is not None:
            env = self._qualification_executor.isolated_environment()
        for name in ("OPENAI_API_KEY","CODEX_API_KEY","CODEX_ACCESS_TOKEN"):
            env.pop(name,None)  # Do not inherit production credentials into the isolated child.
        env.update(CODEX_HOME=str(self.home), TMP=str(temporary), TEMP=str(temporary),
                   CODEX_SQLITE_HOME=str(self.home),
                   RUST_LOG="error",
                   PYTHONDONTWRITEBYTECODE="1", GIT_OPTIONAL_LOCKS="0")
        self._errors = (self.home / "server.stderr.log").open("ab")
        try:
            flags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0
            self.process = subprocess.Popen(self.command, cwd=self.cwd, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", bufsize=1, creationflags=flags)
            self._reader_thread = threading.Thread(target=self._read, daemon=True)
            self._reader_thread.start()
            self._stderr_thread=threading.Thread(target=self._read_stderr,daemon=True)
            self._stderr_thread.start()
            return self
        except BaseException:
            self.close()
            raise

    def _put(self, value):
        while not self._stop.is_set():
            try:
                self._queue.put(value, timeout=.1)
                return
            except queue.Full:
                continue

    def _read_stderr(self):
        try:
            for line in self.process.stderr:
                self._errors.write(str(self._redact(line)).encode("utf-8"))
                self._errors.flush()
        except (OSError,ValueError):
            pass

    def _read(self):
        try:
            while not self._stop.is_set():
                line = self.process.stdout.readline(16*1024*1024+1)
                if not line:
                    self._put(NativeProtocolError("宿主EOF；在途任务终态未知"))
                    return
                if len(line) > 16*1024*1024:
                    raise NativeProtocolError("宿主协议帧过大；拒绝截断后继续")
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise NativeProtocolError("宿主协议帧不是对象")
                self._put(value)
        except (OSError, ValueError) as error:
            self._put(NativeProtocolError("宿主流损坏："+type(error).__name__))

    def _receive(self, timeout, *, idle_allowed=False):
        try:
            frame = self._queue.get(timeout=max(.001, timeout))
        except queue.Empty as error:
            if idle_allowed:
                return None
            self._blocked = True
            raise NativeProtocolError("宿主响应超时，终态未知；禁止自动重发") from error
        if isinstance(frame, Exception):
            self._blocked = True
            raise frame
        with _v_write_open(self.home / "host-events.jsonl", "a", encoding="utf-8") as stream:
            stream.write(json.dumps(self._redact(frame),ensure_ascii=False)+"\n")
        if ("id" in frame and (isinstance(frame["id"],bool) or not isinstance(frame["id"],(str,int)))):
            self._blocked=True
            raise NativeProtocolError("宿主回执ID类型无效；终态未知，停止推理")
        if "method" not in frame and ("id" not in frame
                or ("result" in frame)==("error" in frame)
                or not isinstance(frame.get("result",frame.get("error")),dict)):
            self._blocked=True
            raise NativeProtocolError("宿主响应结构无效；终态未知，停止推理")
        if frame.get("method")=="configWarning":
            warning=json.dumps(frame.get("params",{})).lower()
            if any(key in warning for key in ("stream_max_retries","request_max_retries","stream_idle_timeout_ms","features.apps","web_search","project_doc_max_bytes","cli_auth_credentials_store")):
                self._blocked=True
                raise NativeProtocolError("宿主未接受本进程约束配置，禁止推理")
        if frame.get("method") == "error":
            self._blocked = True
            raise NativeProtocolError("宿主报告流错误；停止推理，不跟随自动重连")
        if "method" in frame and "id" in frame:
            # Client tools/approvals must not silently execute with the controller's rights.
            self._send({"id": frame["id"], "error": {"code": -32601,
                "message": "KP inline worker does not grant tools or approvals"}})
            self._blocked = True
            raise NativeProtocolError("宿主请求未授权的客户端动作，已拒绝")
        return frame

    def _send(self, value):
        if self.process is None or self.process.poll() is not None:
            raise NativeProtocolError("本次私有宿主已结束")
        try:
            self.process.stdin.write(json.dumps(value, ensure_ascii=False)+"\n")
            self.process.stdin.flush()
        except (OSError, ValueError) as error:
            self._blocked = True
            raise NativeProtocolError("宿主写入失败；请求结果未知") from error

    def notify_initialized(self):
        self._send({"method":"initialized","params":{}})

    def request(self, method: str, params: dict, *, timeout: float = 30) -> dict:
        if not 0 < timeout <= 30:
            raise NativeProtocolError("RPC超时必须在0至30秒内")
        environment_rpc = method in {"environment/add", "environment/info", "environment/status"}
        if environment_rpc:
            owner = self._qualification_executor
            if owner is None or not owner.admit_rpc(self, method, params):
                raise NativeProtocolError("执行环境RPC只允许本进程拥有的资格入口")
        if not environment_rpc and method not in {"turn/start", "turn/steer"} and method not in self.QUERY_METHODS:
            raise NativeProtocolError("RPC未在受控白名单中")
        if method=="account/read" and params!={"refreshToken":False}:
            raise NativeProtocolError("账户查询仅允许不强制刷新，不能改变生产认证")
        if method=="account/login/start":
            if (not self._ephemeral_auth_verified or not self._metadata_preflight_thread
                    or self._login_attempted or self._blocked
                    or params not in ({"type":"chatgpt"},{"type":"chatgptDeviceCode"})):
                raise NativeProtocolError("仅已核内存隔离与ephemeral元数据预检实例可单次官方登录；不接受密钥／外部token或重发")
            self._login_attempted=True
            self._login_state="request_claimed"
        if method=="account/login/cancel" and (not self._login_id or params!={"loginId":self._login_id}):
            raise NativeProtocolError("只能取消本进程拥有的登录")
        with self._rpc_lock:
            if method in {"turn/start", "turn/steer"}:
                if self._blocked or self.model_turns >= self.model_turn_limit:
                    raise NativeProtocolError("模型请求未获显式额度或前次终态未知")
                self.model_turns += 1
                params = {**params, "clientUserMessageId": params.get("clientUserMessageId") or uuid4().hex}
            self._sequence += 1
            number = self._sequence
            # Journal before sending: a missing acknowledgement must not cause another dispatch.
            with _v_write_open(self.home / "request-audit.jsonl", "a", encoding="utf-8") as audit:
                audit.write(json.dumps({"id":number,"method":method,
                    "params_sha256":hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest(),
                    "model_attempt":self.model_turns})+"\n")
            self._send({"id":number,"method":method,"params":params})
            deadline = time.monotonic()+timeout
            while True:
                if time.monotonic() >= deadline:
                    self._blocked = True
                    raise NativeProtocolError("宿主响应超时，终态未知；禁止自动重发")
                frame = self._responses.pop(number, None)
                if frame is None:
                    frame = self._receive(deadline-time.monotonic())
                if "id" in frame:
                    if frame["id"] != number:
                        if len(self._responses) >= 64:
                            self._blocked = True
                            raise NativeProtocolError("过多未归属RPC回执")
                        self._responses[frame["id"]] = frame
                        continue
                    if "error" in frame:
                        if method in {"turn/start", "turn/steer"}:
                            self._blocked = True
                        raise NativeProtocolError(f"{method}被宿主拒绝：{frame['error'].get('code', 'unknown')}")
                    value=frame.get("result", {})
                    self.request_receipts.append({"id":number,"method":method,
                        "params":self._redact(params),"result":self._redact(value)})
                    return value
                if len(self._events) >= 4096:
                    self._blocked = True
                    raise NativeProtocolError("原生事件积压，停止而不丢弃终态")
                self._events.append(frame)

    def _redact(self, value):
        owner = self._qualification_executor
        return redacted_protocol(value, secret_values=(owner._token,) if owner is not None else ())

    def verify_ephemeral_auth(self) -> bool:
        if 'cli_auth_credentials_store="ephemeral"' not in self.command:
            raise NativeProtocolError("登录前必须显式指定ephemeral内存认证")
        config=self.request("config/read",{"includeLayers":False,"cwd":str(self.cwd)}).get("config",{})
        if config.get("cli_auth_credentials_store")!="ephemeral":
            raise NativeProtocolError("宿主未确认ephemeral，拒绝文件／keyring回落")
        self._ephemeral_auth_verified=True
        return True

    def preflight_ephemeral_metadata(self, thread_id: str) -> dict:
        """Probe the exact no-history metadata RPC before login or model work."""
        if self.model_turns or not isinstance(thread_id,str) or not thread_id:
            raise NativeProtocolError("ephemeral元数据预检必须先于模型请求并绑定原线程")
        response=self.request("thread/read",{"threadId":thread_id,"includeTurns":False})
        thread=response.get("thread",{})
        if (thread.get("id")!=thread_id or thread.get("ephemeral") is not True
                or thread.get("environments")!=[] or thread.get("status",{}).get("type")!="idle"
                or thread.get("turns")!=[]):
            raise NativeProtocolError("宿主ephemeral元数据接口未确认隔离空闲线程；登录前停止")
        self._metadata_preflight_thread=thread_id
        return {"thread_id":thread_id,"metadata_rpc":"thread/read","include_turns":False,
            "supported":True,"model_turns_requested":self.model_turns}

    def auth_status(self) -> dict:
        response=self.request("account/read",{"refreshToken":False})
        account=response.get("account")
        self._auth_summary={"authenticated":isinstance(account,dict),
            "requires_auth":response.get("requiresOpenaiAuth"),
            "account_type":account.get("type") if isinstance(account,dict) else None}
        return dict(self._auth_summary)

    def begin_managed_login(self, *, mode: str = "browser") -> dict:
        if mode not in {"browser","device"}:
            raise NativeProtocolError("仅支持官方browser或device登录")
        self.verify_ephemeral_auth()
        response=self.request("account/login/start",{"type":"chatgpt" if mode=="browser" else "chatgptDeviceCode"})
        identity=response.get("loginId")
        url=response.get("authUrl") if mode=="browser" else response.get("verificationUrl")
        parsed=urlsplit(url or "")
        if (not isinstance(identity,str) or not identity or parsed.scheme!="https"
                or parsed.hostname not in {"auth.openai.com","chatgpt.com","auth0.openai.com"}
                or parsed.username or parsed.password or parsed.port not in {None,443}):
            self._blocked=True
            raise NativeProtocolError("登录挑战来源或归属无效，停止而不跟随外站")
        self._login_id=identity
        self._login_state="pending"
        return {"state":"pending","auth_url":url,"user_code":response.get("userCode"),
            "storage":"ephemeral","model_turns_requested":self.model_turns}

    def poll_managed_login(self, *, timeout: float = 1) -> dict:
        if not self._login_id or not 0<timeout<=30:
            raise NativeProtocolError("需要本进程登录及0至30秒等待窗口")
        if self._login_state in {"authenticated","failed","cancelled"}:
            return {"state":self._login_state,**self._auth_summary}
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            event=self.next_event(timeout=min(1,deadline-time.monotonic()))
            if event is None:continue
            if event.get("method")!="account/login/completed":continue
            params=event.get("params",{})
            if params.get("loginId")!=self._login_id:continue
            if params.get("success") is not True:
                self._login_state="failed"
                return {"state":"failed","authenticated":False}
            status=self.auth_status()
            if not status["authenticated"]:
                self._login_state="failed"
                return {"state":"failed",**status}
            self._login_state="authenticated"
            return {"state":"authenticated",**status}
        return {"state":"pending",**self._auth_summary}

    def cancel_managed_login(self) -> dict:
        if self._login_state!="pending":
            return {"state":self._login_state,"requested":False}
        response=self.request("account/login/cancel",{"loginId":self._login_id},timeout=3)
        self._login_state="cancelled" if response.get("status")=="canceled" else "unknown"
        return {"state":self._login_state,"requested":True}

    def next_event(self, *, timeout: float = 1) -> dict | None:
        if self._events:
            return self._events.popleft()
        return self._receive(timeout, idle_allowed=True)

    def close(self):
        if self.process is not None:
            if self.process.poll() is None and self._login_state=="pending" and not self._blocked:
                try:
                    self.cancel_managed_login()
                except NativeProtocolError:
                    self._login_state="unknown"
            if self.process.stdin:
                try:
                    self.process.stdin.close()
                except OSError:
                    pass
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=1)
            self._stop.set()
            if self._reader_thread:
                self._reader_thread.join(timeout=1)
            if self._stderr_thread:
                self._stderr_thread.join(timeout=1)
            if self.process.stdout:
                self.process.stdout.close()
            if self.process.stderr:
                self.process.stderr.close()
        if self._errors:
            self._errors.close()
        # Local process exit does not prove remote worker release.

    def __exit__(self, *_):
        self.close()


class NativeQualificationHost:
    """Own two private processes; only explicit qualification turns select execution."""

    def __init__(self, binary, *, cwd, private_home, shell_executable,
                 native_binary_sha256, model_turn_limit, max_children,
                 python_executable=None):
        import sys

        self.cwd, self.home = Path(cwd).resolve(), Path(private_home).resolve()
        if not self.home.is_relative_to(self.cwd) or self.home == self.cwd:
            raise NativeProtocolError("资格宿主私有目录必须位于工作区内")
        self.binary, self.shell = Path(binary).resolve(), Path(shell_executable).resolve()
        self.python = Path(python_executable or sys.executable).resolve()
        if (self.binary.name.lower() != "codex.exe" or not native_binary_sha256
                or hashlib.sha256(self.binary.read_bytes()).hexdigest() != native_binary_sha256
                or self.shell.name.lower() != "pwsh.exe" or not self.shell.is_file()
                or self.python.name.lower() != "python.exe" or not self.python.is_file()):
            raise NativeProtocolError("资格宿主及现有程序运行时身份无效")
        self.sha = native_binary_sha256
        self.root = self.home / "qualification-executor"
        self.materials, self.local_data = self.root / "materials", self.root / "localappdata"
        self.profile = "kp_qualification_" + uuid4().hex
        self.marker = "KP_ALLOWED_" + uuid4().hex
        self._entered = self._ready = self._closed = self._registration_sent = False
        self._token = self._endpoint = None
        self.executor = self.client = self._listener = None
        self._stop = threading.Event()
        self._threads, self._connections = [], []
        runtime_roots = {self.shell.parent, self.python.parent, Path(sys.base_prefix).resolve()}
        if any(self.home.is_relative_to(path) for path in runtime_roots):
            raise NativeProtocolError("程序运行时可读范围不能覆盖资格保护区")
        read_roots = {self.materials, *runtime_roots}
        self._identity_paths = (self.cwd, self.home, self.root, self.materials,
                                self.binary, self.shell, self.python, *runtime_roots)
        filesystem = '":minimal"="read",' + ",".join(
            json.dumps(path.as_posix()) + '="read"' for path in sorted(read_roots))
        self.command = codex_app_server_command(self.binary, self.home, max_children=max_children)
        self.command[-2:-2] = ["-c", 'windows.sandbox="mxc"', "-c",
            f"permissions.{self.profile}.filesystem={{{filesystem}}}", "-c",
            f"permissions.{self.profile}.network.enabled=false", "-c",
            "default_permissions=" + json.dumps(self.profile)]
        self.model_turn_limit = model_turn_limit
        self._command_identity = tuple(self.command)

    def isolated_environment(self):
        keep = {"systemroot", "windir", "comspec", "path", "pathext", "systemdrive",
                "programfiles", "programfiles(x86)", "programdata", "processor_architecture",
                "number_of_processors"}
        env = {key: value for key, value in os.environ.items() if key.lower() in keep}
        env.update(CODEX_HOME=str(self.root / "server-home"), CODEX_SQLITE_HOME=str(self.root / "server-home"),
            LOCALAPPDATA=str(self.local_data), APPDATA=str(self.root / "appdata"),
            TMP=str(self.root / "tmp"), TEMP=str(self.root / "tmp"), RUST_LOG="error")
        return env

    def __enter__(self):
        import secrets
        import socket

        self._check_paths()
        if hashlib.sha256(self.binary.read_bytes()).hexdigest() != self.sha:
            raise NativeProtocolError("资格宿主二进制在启动前发生变化")
        if self._entered or self.root.exists():
            raise NativeProtocolError("资格执行环境不能复用旧实例目录或句柄")
        self._entered = True
        try:
            for path in (self.materials, self.local_data, self.root / "server-home",
                         self.root / "appdata", self.root / "tmp"):
                path.mkdir(parents=True, exist_ok=False)
            (self.materials / "allowed.txt").write_text(self.marker, encoding="utf-8")
            if (self.home / "forbidden-write.txt").exists():
                raise NativeProtocolError("资格写入哨兵必须初始不存在")
            self._listener = socket.socket()
            self._listener.bind(("127.0.0.1", 0))
            self._listener.listen(16)
            self._listener.settimeout(.1)

            def canary():
                while not self._stop.is_set():
                    try:
                        connection, _ = self._listener.accept()
                    except TimeoutError:
                        continue
                    except OSError:
                        return
                    self._connections.append(time.monotonic())
                    connection.close()

            self._start_thread(canary)
            with socket.create_connection(self._listener.getsockname(), timeout=2):
                pass
            deadline = time.monotonic() + 2
            while not self._connections and time.monotonic() < deadline:
                self._stop.wait(.01)
            if len(self._connections) != 1:
                raise NativeProtocolError("资格网络哨兵缺少可达正对照")
            self._token = secrets.token_urlsafe(48)
            command = [str(self.binary), "-c", 'windows.sandbox="mxc"', "exec-server",
                "--listen", "ws://127.0.0.1:0", "--ws-auth", "capability-token",
                "--ws-token-sha256", hashlib.sha256(self._token.encode()).hexdigest(),
                "--concurrent-requests", "6"]
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            self.executor = subprocess.Popen(command, cwd=self.materials, env=self.isolated_environment(),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", creationflags=flags)
            endpoints = queue.Queue(maxsize=1)

            def drain(stream, filename, *, endpoint=False):
                with (self.root / filename).open("a", encoding="utf-8") as log:
                    for line in stream:
                        safe = redacted_protocol(line).replace(self._token or "[closed]", "[redacted]")
                        log.write(safe)
                        log.flush()
                        if endpoint and re.fullmatch(r"ws://127\.0\.0\.1:[1-9][0-9]*", line.strip()):
                            try:
                                endpoints.put_nowait(line.strip())
                            except queue.Full:
                                pass

            self._start_thread(lambda: drain(self.executor.stdout, "executor.stdout.log", endpoint=True))
            self._start_thread(lambda: drain(self.executor.stderr, "executor.stderr.log"))
            try:
                self._endpoint = endpoints.get(timeout=10)
            except queue.Empty as error:
                raise NativeProtocolError("原生资格执行服务未在10秒内提供本地端点") from error
            self.client = NativeAppServerClient(self.command, cwd=self.cwd, private_home=self.home,
                native_binary_sha256=self.sha, model_turn_limit=self.model_turn_limit)
            self.client._qualification_executor = self
            self.client.__enter__()
            return self
        except BaseException:
            self.close()
            raise

    def _start_thread(self, action):
        thread = threading.Thread(target=action, daemon=True)
        self._threads.append(thread)
        thread.start()

    def _processes_live(self, client):
        try:
            self._check_paths()
        except (NativeProtocolError, OSError):
            return False
        return bool(not self._closed and client is self.client and client is not None
            and client.native_origin and not client._blocked and client.process is not None
            and client.process.poll() is None and self.executor is not None
            and self.executor.poll() is None and tuple(client.command) == self._command_identity)

    def _check_paths(self):
        paths = getattr(self, "_identity_paths", ())
        if not paths or any(path.resolve() != path for path in paths):
            raise NativeProtocolError("资格宿主路径发生改绑，拒绝继续使用旧范围")

    def admit_rpc(self, client, method, params):
        if not self._processes_live(client):
            return False
        if method == "environment/add":
            expected = {"environmentId": self.profile, "execServerUrl": self._endpoint,
                "authBearerToken": self._token, "connectTimeoutMs": 5000}
            if self._registration_sent or client.model_turns or params != expected:
                return False
            self._registration_sent = True  # Unknown acknowledgement cannot be resent.
            return True
        return self._registration_sent and params == {"environmentId": self.profile}

    def register(self):
        if self.client is None or self.client.model_turns:
            raise NativeProtocolError("资格执行环境必须在模型请求前注册")
        self.client.request("environment/add", {"environmentId": self.profile, "execServerUrl": self._endpoint,
            "authBearerToken": self._token, "connectTimeoutMs": 5000}, timeout=10)
        info = self.client.request("environment/info", {"environmentId": self.profile}, timeout=10)
        status = self.client.request("environment/status", {"environmentId": self.profile}, timeout=10)
        if (info.get("shell", {}).get("path") != str(self.shell)
                or info.get("cwd") != self.materials.as_uri() or status.get("status") != "ready"):
            raise NativeProtocolError("资格执行环境的实际程序／目录／连通回执不匹配")
        self._ready = True
        return {"ready": True, "environment_id": self.profile, "production_qualification": False}

    def parameters(self, client):
        if not self._ready or not self._processes_live(client):
            raise NativeProtocolError("资格执行环境不属于当前活连接")
        return {"environments": [{"environmentId": self.profile, "cwd": str(self.materials),
            "runtimeWorkspaceRoots": []}], "permissions": self.profile, "runtimeWorkspaceRoots": []}

    def probe_commands(self):
        import base64

        def command(source):
            encoded = base64.b64encode(source.encode()).decode()
            executable = self.python.as_posix().replace("'", "''")
            return f"& '{executable}' -B -S -c \"exec(__import__('base64').b64decode('{encoded}'))\""

        return {"read": command(f"from pathlib import Path;Path({str(self.home / 'permission-canary.txt')!r}).read_text()"),
            "write": command(f"from pathlib import Path;Path({str(self.home / 'forbidden-write.txt')!r}).write_text('qualification-canary')"),
            "network": command(f"import socket;socket.create_connection(('127.0.0.1',{self._listener.getsockname()[1]}),timeout=2).close()"),
            "allowed_read": command(f"from pathlib import Path;print(Path({str(self.materials / 'allowed.txt')!r}).read_text())")}

    def check_canaries(self, expected_read_sha256):
        self.parameters(self.client)
        if (hashlib.sha256((self.home / "permission-canary.txt").read_bytes()).hexdigest() != expected_read_sha256
                or (self.home / "forbidden-write.txt").exists()
                or (self.materials / "allowed.txt").read_text(encoding="utf-8") != self.marker
                or len(self._connections) != 1):
            raise NativeProtocolError("资格哨兵已改变或出现被禁止的网络连接")

    def close(self):
        self._closed, self._ready = True, False
        self._stop.set()
        try:
            try:
                if self.client is not None:
                    self.client._blocked = True
                    if self.client._login_state == "pending":
                        try:
                            self.client.cancel_managed_login()
                        except NativeProtocolError:
                            self.client._login_state = "unknown"
                    self.client.close()
            finally:
                if self.executor is not None:
                    if self.executor.poll() is None:
                        self.executor.terminate()
                    try:
                        self.executor.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.executor.kill()
                        self.executor.wait(timeout=1)
        finally:
            if self._listener is not None:
                self._listener.close()
            for thread in self._threads:
                thread.join(timeout=1)
            if self.executor is not None:
                for stream in (self.executor.stdin, self.executor.stdout, self.executor.stderr):
                    if stream is not None:
                        stream.close()
            self._token = None

    def __exit__(self, *_):
        self.close()


def _matched_probe_command(item, commands):
    actions = item.get("commandActions")
    if actions is None:
        submitted = item.get("command")  # Existing native versions, exact binding only.
    elif (isinstance(actions, list) and len(actions) == 1 and isinstance(actions[0], dict)
            and set(actions[0]) == {"type", "command"} and actions[0]["type"] == "unknown"):
        submitted = actions[0]["command"]
    else:
        return None
    return next((key for key, value in commands.items() if value == submitted), None)


class NativeTaskLedger:
    """Interpret native frames only. Parent summaries and completed spawn items are not results."""
    def __init__(self, parent_id: str, expected_tasks: dict[str, dict]):
        self.parent_id, self.expected_tasks = parent_id, expected_tasks
        self.children = {}
        self.by_task = {}
        self.usage = {}
        self.usage_turn_ids = {}
        self.parent_turn_id = None
        self.parent_status = "unknown"
        self.peak_children = 0
        self.peak_running_children = 0
        self.peak_running_by_wave = {}
        self.running_turns = {}
        self.turn_intervals = []
        self.revision = 0
        self.permission_denials = {}
        self.permission_positive_controls = {}
        self.thread_scopes = {}

    @property
    def active_children(self) -> set[str]:
        return {identity for identity,value in self.children.items() if not value["released"]}

    @property
    def running_children(self) -> set[str]:
        """Observed executing turns, separate from conservative unreleased capacity."""
        return set(self.running_turns)

    def confirmed_running_overlap(self, *, wave=None) -> int:
        """Native millisecond intervals prove capacity; notification ordering alone does not."""
        intervals=[item for item in self.turn_intervals if wave is None or
            self.expected_tasks.get(self.children[item["thread_id"]]["task_id"],{}).get("dispatch_wave")==wave]
        if not intervals:
            return 0
        points=[]
        for item in intervals:
            start,end=item["start_ms"],item["end_ms"]
            if (type(start) is not int or type(end) is not int or end<=start):
                return 0
            points.extend(((start,1),(end,-1)))
        current=peak=0
        for _,delta in sorted(points):  # End before start at the same instant: half-open intervals.
            current+=delta
            peak=max(peak,current)
        return peak

    def _started(self, parent: str, identity: str, path: str | None):
        if parent != self.parent_id and parent not in self.children:
            return
        known = self.children.get(identity)
        if known and known["parent_id"] != parent:
            raise NativeProtocolError("子线程父归属发生变化")
        alias = path.rsplit("/",1)[-1] if path else None
        task = next((key for key,value in self.expected_tasks.items() if value.get("agent_name",key)==alias),None) if parent==self.parent_id else None
        if known and task and known.get("task_id") not in {None,task}:
            raise NativeProtocolError("同一子线程的任务归属发生变化")
        duplicate = bool(task and task in self.by_task and self.by_task[task] != identity)
        if duplicate:
            self.children.setdefault(identity,{"parent_id":parent,"task_id":None,"status":"unknown",
                "released":False,"message":None,"completed":False})
            self.peak_children=max(self.peak_children,len(self.active_children))
            raise NativeProtocolError("同一任务重复创建子线程；保留全部在途槽位")
        if not known:
            self.children[identity] = {"parent_id":parent,"task_id":task,"status":"running",
                "released":False,"execution_release":False,"release_kind":None,
                "residency_status":"unknown","message":None,"completed":False,
                "observed_started_at":datetime.now(UTC).isoformat()}
        if task:
            self.children[identity]["task_id"] = task
            self.by_task[task] = identity
        self.peak_children=max(self.peak_children,len(self.active_children))

    def consume(self, event: dict):
        try:
            self._consume(event)
            self.revision += 1
        except (TypeError,KeyError,AttributeError) as error:
            raise NativeProtocolError("原生事件字段不兼容；保留未知而不猜测") from error

    def _consume(self, event: dict):
        params=event.get("params",{})
        parent=params.get("threadId")
        method=event.get("method")
        if method=="thread/started":
            thread=params.get("thread",{})
            if thread.get("id"):
                self.thread_scopes[thread["id"]]=thread.get("environments")
            return
        if method=="thread/tokenUsage/updated":
            self.usage[parent]=params.get("tokenUsage")  # Latest cumulative snapshot; never sum updates.
            self.usage_turn_ids[parent]=params.get("turnId")
        if method=="turn/started":
            if parent==self.parent_id:
                self.parent_turn_id=params.get("turn",{}).get("id")
            elif parent in self.children:
                turn_id=params.get("turn",{}).get("id")
                if not turn_id:
                    raise NativeProtocolError("子turn缺少原生ID，不能证明运行区间")
                child=self.children[parent]
                if child.get("terminal_turn_id"):
                    child.update(released=False,execution_release=False,release_kind=None,
                        completed=False,message=None,status="unknown")
                    raise NativeProtocolError("单次任务子线程出现未授权新turn，撤销旧结果并保留槽位")
                known=self.running_turns.get(parent)
                if known and known["turn_id"]!=turn_id:
                    raise NativeProtocolError("同一子线程出现重叠turn，终态未知")
                self.running_turns[parent]={"turn_id":turn_id,"start_ms":event.get("emittedAtMs")}
                self.peak_running_children=max(self.peak_running_children,len(self.running_children))
                wave=self.expected_tasks.get(child["task_id"],{}).get("dispatch_wave")
                if wave is not None:
                    count=sum(self.expected_tasks.get(self.children[key]["task_id"],{}).get("dispatch_wave")==wave
                        for key in self.running_children)
                    self.peak_running_by_wave[wave]=max(self.peak_running_by_wave.get(wave,0),count)
        if method=="turn/completed":
            status=params.get("turn",{}).get("status","unknown")
            if parent==self.parent_id:
                self.parent_status=status
            elif parent in self.children:
                running=self.running_turns.get(parent)
                if running and params.get("turn",{}).get("id")!=running["turn_id"]:
                    raise NativeProtocolError("子turn终态ID不匹配，保留原运行区间")
                if running:
                    self.turn_intervals.append({"thread_id":parent,**self.running_turns.pop(parent),
                        "end_ms":event.get("emittedAtMs"),"status":status})
                self.children[parent]["status"]=status
                self.children[parent]["completed"]=status=="completed"
                if running:
                    self.children[parent]["terminal_turn_id"]=running["turn_id"]
            return
        if method not in {"item/started","item/completed"}:
            return
        item=params.get("item",{})
        if item.get("type")=="commandExecution" and method=="item/completed" and parent in self.children:
            task_id=self.children[parent]["task_id"]
            expected=self.expected_tasks.get(task_id,{})
            commands=expected.get("permission_probe_commands",{})
            output=str(item.get("aggregatedOutput","")).lower()
            permission_error = (any(word in output for word in (
                "permission denied", "access is denied", "environment access is disabled"))
                or re.search(r"(?m)^permissionerror: \[(?:errno 13|winerror (?:5|10013))\]", output))
            scope=_matched_probe_command(item, commands)
            expected_turn=self.running_turns.get(parent,{}).get("turn_id") or self.children[parent].get("terminal_turn_id")
            if (expected_turn and params.get("turnId")==expected_turn and scope and item.get("status")=="failed"
                    and type(item.get("exitCode")) is int
                    and item.get("exitCode") not in {0,None}
                    and permission_error):
                self.permission_denials.setdefault(parent,{})[scope]=item["id"]
            positive=_matched_probe_command(item, expected.get("positive_probe_commands",{}))
            marker=expected.get("positive_probe_marker")
            if (expected_turn and params.get("turnId")==expected_turn and positive
                    and item.get("status")=="completed" and type(item.get("exitCode")) is int and item.get("exitCode")==0
                    and marker and marker in str(item.get("aggregatedOutput","")).splitlines()):
                self.permission_positive_controls.setdefault(parent,{})[positive]=item["id"]
        if item.get("type")=="subAgentActivity":
            identity=item["agentThreadId"]
            if item.get("kind")=="started":
                self._started(parent,identity,item.get("agentPath"))
                model = item.get("model")
                if (identity in self.children and self.children[identity]["parent_id"] == parent
                        and isinstance(model, str) and model.strip()):
                    known_model = self.children[identity].get("observed_model")
                    if known_model is not None and known_model != model:
                        raise NativeProtocolError("同一原生子线程的模型来源冲突，拒绝覆盖")
                    self.children[identity]["observed_model"] = model
            elif identity in self.children:
                child=self.children[identity]
                if child["parent_id"]!=parent:
                    raise NativeProtocolError("其他父线程不能签子任务生命周期")
                # V2 activity describes an action, not capacity release or a child result.
                # A completed child turn remains usable after a later stop action.
                child["activity_kind"]=item.get("kind")
        elif item.get("type")=="agentMessage" and method=="item/completed" and parent in self.children:
            if item.get("phase") in {"final_answer","finalAnswer"}:
                child=self.children[parent]
                expected=self.running_turns.get(parent,{}).get("turn_id") or child.get("terminal_turn_id")
                if not expected or params.get("turnId")!=expected:
                    child.update(message=None,completed=False,message_binding_invalid=True,
                        released=False,execution_release=False,release_kind=None,status="unknown")
                    raise NativeProtocolError("子消息原生turn归属缺失或不匹配；拒绝结果并保留槽位")
                child["message"]=item.get("text")
                child["message_turn_id"]=expected
        elif item.get("type")=="collabAgentToolCall":
            if item.get("senderThreadId")!=parent:
                raise NativeProtocolError("原生协作事件sender不匹配")
            if parent!=self.parent_id and parent not in self.children:
                return
            receivers=item.get("receiverThreadIds",[])
            if item.get("tool")=="spawnAgent":
                for identity in receivers:
                    self._started(parent,identity,None)
            for identity,value in item.get("agentsStates",{}).items():
                if identity not in self.children:
                    continue
                child=self.children[identity]
                if child["parent_id"]!=parent:
                    raise NativeProtocolError("其他父线程不能签子任务终态")
                if value.get("status") in {"completed","interrupted","errored","shutdown"}:
                    child["status"]=value["status"]
                if value.get("status")=="completed":
                    child["completed"]=True
                    # Parent tool summaries do not carry the child's native message turn ID.
                    # Only direct, turn-attributed child agentMessage frames supply results.
                elif value.get("status") in {"interrupted","errored"}:
                    child["completed"]=False
                if (method=="item/completed" and item.get("tool")=="closeAgent"
                        and item.get("status")=="completed" and identity in receivers
                        and value.get("status")=="shutdown"):
                    child["released"]=True
                    child["release_kind"]="v1_shutdown"
                    child["execution_release"]=True
                    child["residency_status"]="shutdown"

    def result_for(self, task_id: str) -> dict | None:
        identity=self.by_task.get(task_id)
        if not identity:
            return None
        child=self.children[identity]
        if (not child["completed"] or not child["message"] or child.get("message_binding_invalid")
                or not child.get("terminal_turn_id")
                or child.get("message_turn_id")!=child["terminal_turn_id"]):
            return None
        try:
            payload=json.loads(child["message"])
        except ValueError as error:
            raise NativeProtocolError("子任务没有返回受控JSON结果") from error
        if not isinstance(payload,dict):
            raise NativeProtocolError("子任务没有返回受控JSON对象")
        expected=self.expected_tasks[task_id]
        if (payload.get("task_id")!=task_id
                or payload.get("candidate_version")!=expected["candidate_version"]
                or payload.get("input_manifest_sha256")!=expected["input_manifest_sha256"]
                or not isinstance(payload.get("result"),dict)):
            raise NativeProtocolError("子任务输入／候选版本不匹配，拒绝晚结果")
        return {**payload,"host_task_id":identity,"host_parent_id":child["parent_id"],
            "host_turn_id":child["terminal_turn_id"],
            "host_dispatched_at":child["observed_started_at"]}


def codex_app_server_command(binary: Path, private_home: Path, *,
                             max_children: int | None = None) -> list[str]:
    """Process-local settings; never import global configuration or retry inference streams."""
    if max_children is not None and (type(max_children) is not int or not 1 <= max_children <= 64):
        raise NativeProtocolError("原生子任务总池必须是1至64的整数；配置不是实测容量")
    return [str(binary),"-c",'mcp_servers={}',"-c",'features.apps=false',
        "-c",'cli_auth_credentials_store="ephemeral"',
        "-c",'web_search="disabled"',"-c",'project_doc_max_bytes=0',
        "-c",'model_provider="kp_native"',
        "-c",'model_providers.kp_native.name="KP isolated OpenAI"',
        "-c",'model_providers.kp_native.base_url="https://chatgpt.com/backend-api/codex"',
        "-c",'model_providers.kp_native.wire_api="responses"',
        "-c",'model_providers.kp_native.requires_openai_auth=true',
        "-c",'model_providers.kp_native.supports_websockets=false',
        "-c",'model_providers.kp_native.request_max_retries=0',
        "-c",'model_providers.kp_native.stream_max_retries=0',
        "-c",'model_providers.kp_native.stream_idle_timeout_ms=30000',
        "-c",'log_dir='+json.dumps((private_home/"logs").as_posix()),
        *(["-c",f"agents.max_threads={max_children}"] if max_children is not None else []),
        "app-server","--stdio"]
