"""Quarto受限Windows子进程标准流桥；权限令牌不变，传输文件由调用者持有。"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path


def watch_owner(owner_pid: int) -> None:
    """固定持有父进程句柄，父退出时关闭本进程Job，不按可复用PID强杀外部进程。"""
    from ctypes import wintypes as w
    if owner_pid <= 0 or owner_pid == os.getpid():
        raise ValueError('INVALID_QUARTO_OWNER')
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    k.OpenProcess.restype = w.HANDLE
    k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    k.WaitForSingleObject.restype = w.DWORD
    handle = k.OpenProcess(0x100000, False, owner_pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    def wait():
        code = k.WaitForSingleObject(handle, 0xffffffff)
        if code in (0, 0xffffffff):
            os._exit(125)
    threading.Thread(target=wait, daemon=True).start()


def own_kill_job():
    """子进程不得在桥取消后继续；Windows8+支持嵌套Job。"""
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                    ('flags', w.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                    ('active', w.DWORD), ('affinity', ctypes.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]
    class Io(ctypes.Structure):
        _fields_ = [(key, ctypes.c_uint64) for key in ['read_count', 'write_count', 'other_count', 'read_bytes', 'write_bytes', 'other_bytes']]
    class Extended(ctypes.Structure):
        _fields_ = [('basic', Basic), ('io', Io), ('process_memory', ctypes.c_size_t),
                    ('job_memory', ctypes.c_size_t), ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    k.CreateJobObjectW.restype = w.HANDLE
    k.GetCurrentProcess.restype = w.HANDLE
    k.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    handle = k.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = Extended()
    limits.basic.flags = 0x2000
    if not k.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not k.AssignProcessToJobObject(handle, k.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    # 句柄仅由当前进程持有，正常或强制退出时由Windows关闭并终止后代。
    return handle


def main(folder: Path) -> None:
    request = json.loads((folder / 'request.json').read_text(encoding='utf-8'))
    command = request['command']
    args = request.get('args', [])
    if not isinstance(command, str) or not command or '\0' in command:
        raise ValueError('INVALID_COMMAND')
    if not isinstance(args, list) or any(not isinstance(a, str) or '\0' in a for a in args):
        raise ValueError('INVALID_ARGUMENTS')
    job_handle = own_kill_job() if os.name == 'nt' else None
    (folder / 'helper.json').write_text(json.dumps({'pid': os.getpid()}), encoding='utf-8')
    if os.name == 'nt':
        watch_owner(int(request['ownerPid']))
    streams = {}
    try:
        for name in ['stdout', 'stderr']:
            mode = request.get(name, 'inherit')
            if mode == 'piped':
                streams[name] = (folder / (name + '.bin')).open('wb', buffering=0)
            elif mode == 'null':
                streams[name] = subprocess.DEVNULL
            elif mode == 'inherit':
                streams[name] = None
            else:
                raise ValueError('INVALID_STREAM_MODE')
        mode = request.get('stdin', 'inherit')
        if mode == 'piped':
            # Quarto的输入为一次性有限文稿；写端close之后开始子命令。
            deadline = time.monotonic() + 60
            while not (folder / 'stdin.eof').exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError('STDIN_NOT_CLOSED')
                time.sleep(0.01)
            streams['stdin'] = (folder / 'stdin.bin').open('rb')
        elif mode == 'null':
            streams['stdin'] = subprocess.DEVNULL
        elif mode == 'inherit':
            streams['stdin'] = None
        else:
            raise ValueError('INVALID_STDIN_MODE')
        environment = {} if request.get('clearEnv', False) else os.environ.copy()
        environment.update(request.get('env', {}))
        executable = os.path.normpath(command) if os.name == 'nt' else command
        child = subprocess.Popen([executable, *args], cwd=request.get('cwd'), env=environment, **streams)
        (folder / 'pid.json').write_text(json.dumps({'pid': child.pid}), encoding='utf-8')
        code = child.wait()
        (folder / 'status.json').write_text(json.dumps({'code': code, 'success': code == 0, 'signal': None}), encoding='utf-8')
    finally:
        for stream in streams.values():
            if hasattr(stream, 'close'):
                stream.close()
        _ = job_handle


if __name__ == '__main__':
    root = Path(sys.argv[1]).resolve(strict=True)
    try:
        main(root)
    except Exception as error:
        (root / 'error.json').write_text(json.dumps({'type': type(error).__name__, 'message': str(error)}), encoding='utf-8')
        raise
