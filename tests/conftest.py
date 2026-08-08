"""pytest 全局配置：Windows 下为测试输出提供 UTF-8 兜底。

根因：Python 3.13 在 Windows 上默认按 ANSI 代码页（cp936/GBK）编码 stdout，
而 PowerShell 7 终端按 UTF-8 解码，导致中文路径/消息乱码，且 GBK 无法表示的
字符（如数学符号）会直接触发 UnicodeEncodeError。

覆盖范围：pytest 启动早期（rootdir、收集摘要等）由 TerminalWriter 直接写
原始流，进程内 reconfigure 对其无效——完整解决请设置环境变量
`PYTHONUTF8=1`（见 README"Windows 中文输出乱码"）。本文件兜底的是测试运行
期的输出回放与测试内派生子进程的编码环境。
"""

from __future__ import annotations

import os
import sys


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                # 非标准流（例如已被重定向且不支持 reconfigure）保持原样。
                pass


_force_utf8_stdio()
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
