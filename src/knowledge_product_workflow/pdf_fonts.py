"""Probe the finite default font set with the actual XeLaTeX font resolver."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .workspace_scratch import workspace_scratch

PREFERRED_FONTS = {
    "pingfang_sc": "PingFang SC",
    "pingfang_sc_semibold": "PingFang SC Semibold",
    "inter": "Inter",
    "jetbrains_mono": "JetBrains Mono",
}


def probe_preferred_fonts(root: Path) -> dict[str, dict[str, object]]:
    """Keep compiler scratch files inside the caller's workspace; never install fonts."""
    result = {key: {"font": name, "available": False, "source": "xelatex-fontspec", "status": "probe_unavailable"}
              for key, name in PREFERRED_FONTS.items()}
    executable = shutil.which("xelatex")
    if executable is None:
        for item in result.values():
            item["diagnostic"] = "缺少XeLaTeX，无法判断字体是否存在"
        return result
    source = "\\documentclass{article}\n\\usepackage{fontspec}\n\\begin{document}\n"
    for index, name in enumerate(PREFERRED_FONTS.values()):
        source += (f"\\IfFontExistsTF{{{name}}}{{\\typeout{{KP-FONT-{index}=YES}}}}"
                   f"{{\\typeout{{KP-FONT-{index}=NO}}}}\n")
    source += "\\end{document}\n"
    try:
        with workspace_scratch(prefix=".kp-font-probe-", root=root) as directory:
            folder = Path(directory)
            (folder / "probe.tex").write_text(source, encoding="utf-8")
            run = subprocess.run([executable, "-no-shell-escape", "-interaction=batchmode",
                                  "-halt-on-error", "probe.tex"], cwd=folder,
                                 capture_output=True, timeout=8, check=False)
            log = folder / "probe.log"
            if run.returncode or not log.is_file():
                for item in result.values():
                    item.update(status="compile_failed", diagnostic=f"原生字体探针编译失败（退出码{run.returncode}），不等于字体缺失")
                return result
            text = log.read_text(encoding="utf-8", errors="replace")
            for index, key in enumerate(PREFERRED_FONTS):
                result[key]["available"] = f"KP-FONT-{index}=YES" in text
                result[key]["status"] = "available" if result[key]["available"] else "missing"
    except subprocess.TimeoutExpired:
        for item in result.values():
            item.update(status="timeout", diagnostic="字体探测超过8秒；不推断字体缺失或已安装")
    except OSError as error:
        for item in result.values():
            item.update(status="probe_unavailable", diagnostic=f"字体探针不可用：{error}")
    return result
