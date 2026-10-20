"""受限Windows Quarto启动适配；仍执行同一安装的Quarto JS与XeLaTeX。"""
from __future__ import annotations

import ctypes
import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .workspace_scratch import workspace_scratch


def wait_stream_bridges(folder: Path) -> None:
    """取消后等流桥退出再清理其目录；查询不发送终止信号。"""
    if os.name != 'nt':
        return
    from ctypes import wintypes as w
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    k.OpenProcess.restype = w.HANDLE
    k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    k.WaitForSingleObject.restype = w.DWORD
    k.CloseHandle.argtypes = [w.HANDLE]
    for file in folder.glob('*/helper.json'):
        pid = json.loads(file.read_text(encoding='utf-8'))['pid']
        handle = k.OpenProcess(0x100000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                continue
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if k.WaitForSingleObject(handle, 5000) != 0:
                raise RuntimeError('Quarto标准流桥未确认退出：' + str(file.parent))
        finally:
            k.CloseHandle(handle)


def restricted_windows() -> bool:
    if os.name != 'nt':
        return False
    from ctypes import wintypes as w
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    a = ctypes.WinDLL('advapi32', use_last_error=True)
    k.GetCurrentProcess.restype = w.HANDLE
    k.CloseHandle.argtypes = [w.HANDLE]
    a.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    a.IsTokenRestricted.argtypes = [w.HANDLE]
    token = w.HANDLE()
    if not a.OpenProcessToken(k.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return bool(a.IsTokenRestricted(token))
    finally:
        k.CloseHandle(token)


@contextmanager
def quarto_command(executable: Path, arguments: list[str], cwd: Path, environment: dict[str, str]) -> Iterator[tuple[list[str], dict[str, str]]]:
    if not restricted_windows():
        yield [str(executable), *arguments], environment
        return
    bin_dir = executable.parent
    deno = bin_dir / 'tools/x86_64/deno.exe'
    original = bin_dir / 'quarto.js'
    shim = Path(__file__).parent / 'templates/runtime/quarto-stdio.mjs'
    if not all(file.is_file() for file in [deno, original, shim]):
        raise RuntimeError('受限Quarto适配缺少已安装Deno或受控运行资源')
    with workspace_scratch(cwd, prefix='kp-quarto-runtime-') as scratch:
        # helper规范化到物理路径；传给Quarto的名称仍保留父进程已核验的ASCII别名。
        folder = cwd / Path(scratch).name
        source = original.read_text(encoding='utf-8')
        cache_function = 'function quartoCacheDir(subdir) {\n  return quartoDir(userCacheDir, subdir);\n}'
        if source.count(cache_function) != 1:
            raise RuntimeError('已安装Quarto缓存接口已变化，不能静默应用旧兼容层')
        source = source.replace(cache_function,
            'function quartoCacheDir(subdir) {const dir=Deno.env.get("KP_QUARTO_PIPE_TEMP")+"/cache"+(subdir?"/"+subdir:""); ensureDirSync(dir); return dir;}')
        main = folder / 'quarto-main.mjs'
        main.write_text('import ' + json.dumps(shim.as_uri()) + ';\n' + source, encoding='utf-8')
        env = {**environment, 'QUARTO_BIN_PATH': str(bin_dir), 'QUARTO_SHARE_PATH': str(bin_dir.parent / 'share'),
               'DENO_DOM_PLUGIN': str(bin_dir / 'tools/deno_dom/plugin.dll'), 'DENO_DIR': str(folder / 'deno-cache'),
               'DENO_NO_UPDATE_CHECK': '1', 'KP_QUARTO_PIPE_PYTHON': sys.executable,
               'KP_QUARTO_PIPE_BRIDGE': str(Path(__file__).parent / 'quarto_stdio_bridge.py'),
               'KP_QUARTO_PIPE_TEMP': str(folder)}
        env = {name: value for name, value in env.items()
               if not any(part in name.upper() for part in ('API_KEY', 'ACCESS_TOKEN', 'PASSWORD', 'SECRET', 'CAPABILITY'))}
        try:
            yield [str(deno), 'run', '--allow-all', '--deny-net', '--no-config', '--no-lock', '--no-check',
                   '--unstable-kv', '--unstable-ffi', '--v8-flags=--enable-experimental-regexp-engine',
                   str(main), *arguments], env
        finally:
            wait_stream_bridges(folder)
