"""Local D2 executable discovery; rendering never downloads a dependency."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .workspace_scratch import create_workspace_scratch


@dataclass(frozen=True)
class D2Resolution:
    available: bool
    path: Path | None
    source: str | None
    version: str | None
    failed_candidates: tuple[str, ...]


@dataclass(frozen=True)
class BrowserResolution:
    """A local Chromium-family browser used only to convert D2 SVG assets offline."""

    available: bool
    path: Path | None
    source: str | None
    version: str | None
    failed_candidates: tuple[str, ...]


def d2_subprocess_environment() -> dict[str, str]:
    """Return a D2-safe child environment without changing the host process environment."""
    environment = os.environ.copy()
    environment.pop("DEBUG", None)
    return environment


def _verify_executable(candidate: Path, *, environment: dict[str, str]) -> str | None:
    if not candidate.is_file():
        return None
    try:
        result = subprocess.run(
            [str(candidate), "--version"],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (result.stdout or result.stderr).strip()
    return output.splitlines()[0] if output else None


def _persistent_user_d2_path() -> Path | None:
    """Read a user-scoped D2 override when a long-lived host has a stale environment."""
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            configured, _ = winreg.QueryValueEx(key, "KP_D2_PATH")
    except OSError:
        return None
    if not isinstance(configured, str) or not configured.strip():
        return None
    path = Path(os.path.expandvars(configured.strip()))
    return path if path.is_absolute() else None


def resolve_d2() -> D2Resolution:
    """Find D2 from an explicit environment value or ordinary local executable paths."""
    candidates: list[tuple[str, Path]] = []
    environment = os.environ.get("KP_D2_PATH")
    if environment:
        configured = Path(environment)
        if configured.is_absolute():
            candidates.append(("KP_D2_PATH", configured))
    persistent = _persistent_user_d2_path()
    if persistent is not None:
        candidates.append(("KP_D2_PATH", persistent))
    on_path = shutil.which("d2")
    if on_path:
        candidates.append(("PATH", Path(on_path)))
    candidates.append(
        ("ProgramFiles", Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "d2" / "d2.exe")
    )
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(("LocalAppData", Path(local_app_data) / "Programs" / "d2" / "d2.exe"))
    failed: list[str] = []
    for source, candidate in candidates:
        if not candidate.is_file():
            failed.append(str(candidate))
            continue
        version = _verify_executable(candidate, environment=d2_subprocess_environment())
        if version is None:
            failed.append(str(candidate))
            continue
        return D2Resolution(True, candidate, source, version, tuple(failed))
    return D2Resolution(False, None, None, None, tuple(failed))


def resolve_diagram_browser() -> BrowserResolution:
    """Find a local Chromium browser without downloading a Playwright runtime."""
    candidates: list[tuple[str, Path]] = []
    configured = os.environ.get("KP_DIAGRAM_BROWSER_PATH")
    if configured and Path(configured).is_absolute():
        candidates.append(("KP_DIAGRAM_BROWSER_PATH", Path(configured)))
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    program_files_x86 = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    candidates.extend(
        [
            ("Chrome", program_files / "Google" / "Chrome" / "Application" / "chrome.exe"),
            ("Chrome", program_files_x86 / "Google" / "Chrome" / "Application" / "chrome.exe"),
            ("Edge", program_files / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
            ("Edge", program_files_x86 / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
        ]
    )
    for name in ("google-chrome", "chrome", "chromium", "msedge"):
        on_path = shutil.which(name)
        if on_path:
            candidates.append(("PATH", Path(on_path)))
    failed: list[str] = []
    for source, candidate in candidates:
        if not candidate.is_file():
            failed.append(str(candidate))
            continue
        # Do not execute Chrome just to probe it. Windows can forward a benign-looking
        # command to an existing interactive browser session and visibly open a window.
        return BrowserResolution(True, candidate, source, None, tuple(failed))
    return BrowserResolution(False, None, None, None, tuple(failed))


def _svg_viewport(svg_path: Path, *, tight: bool = False) -> tuple[int, int]:
    """Constrain screenshots to the D2 SVG viewBox without parsing untrusted XML."""
    text = svg_path.read_text(encoding="utf-8")
    match = re.search(r'viewBox="\s*0\s+0\s+([0-9.]+)\s+([0-9.]+)\s*"', text)
    if match is None:
        return 1600, 1200
    if tight:
        return tuple(max(64, min(4096, round(float(value)))) for value in match.groups())
    width = max(640, min(4096, round(float(match.group(1)) + 48)))
    height = max(480, min(4096, round(float(match.group(2)) + 48)))
    return width, height


def render_svg_with_browser(browser: Path, svg_path: Path, pdf_path: Path, png_path: Path, *, font_css: str | None = None) -> None:
    """Convert a D2-generated local SVG to PDF and PNG using an already installed browser.

    The wrapper contains no remote content and browser background networking is disabled. This avoids
    D2's Playwright bootstrap while keeping D2 as the only diagram-layout and SVG producer.
    """
    width, height = _svg_viewport(svg_path, tight=bool(font_css))
    from .svg_export import export_svg, resolve_svg_compiler
    compiler = resolve_svg_compiler()
    if compiler is not None:
        export_svg(compiler, svg_path, pdf_path, png_path, width, height, font_css=font_css)
        return
    html_path = svg_path.with_suffix(".render.html")
    html_path.write_text(
        "<!doctype html><html><head><meta charset=\"utf-8\"><style>"
        f"@page {{ size: {width}px {height}px; margin: 0; }}"
        "html,body{margin:0;padding:0;background:#fff;overflow:hidden;}"
        f"img{{display:block;width:{width}px;height:{height}px;object-fit:contain;}}"
        "</style></head><body><img src=\""
        + svg_path.as_uri()
        + "\"></body></html>\n",
        encoding="utf-8",
    )
    if font_css:
        html_path.write_text("<!doctype html><html><head><meta charset=\"utf-8\"><style>"
            + f"@page{{size:{width}px {height}px;margin:0}}html,body{{margin:0;padding:0;overflow:hidden}}"
            + f"body>svg{{display:block;width:{width}px!important;height:{height}px!important}}" + font_css + "</style></head><body>" + svg_path.read_text(encoding="utf-8") + "</body></html>",encoding="utf-8")
    profile = create_workspace_scratch(svg_path.parent, prefix="kp-diagram-browser-")
    base = [
        str(browser),
        "--headless=new",
        "--disable-gpu",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-sync",
        "--metrics-recording-only",
        "--no-first-run",
        "--allow-file-access-from-files",
        "--hide-scrollbars",
        "--virtual-time-budget=2000",
        f"--user-data-dir={profile}",
        f"--window-size={width},{height}",
    ]
    try:
        subprocess.run(
            [*base, f"--print-to-pdf={pdf_path}", "--no-pdf-header-footer", html_path.as_uri()],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=d2_subprocess_environment(),
        )
        subprocess.run(
            [*base, f"--screenshot={png_path}", html_path.as_uri()],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=d2_subprocess_environment(),
        )
    finally:
        try:
            html_path.unlink()
        except FileNotFoundError:
            pass
        shutil.rmtree(profile, ignore_errors=True)


def diagram_font_css() -> str:
    """利用已安装fontconfig定位字体，显式载入浏览器；不安装字体。"""
    names = [("kpDiagramLatin", "Inter", "400"), ("kpDiagramLatin", "Inter:style=SemiBold", "600"),
             ("kpDiagramCJK", "PingFang SC", "400"), ("kpDiagramCJK", "PingFang SC:style=Semibold", "600")]
    css=[]
    for family,query,weight in names:
        result=subprocess.run(["fc-match","-f","%{file}|%{family}",query],capture_output=True,text=True,encoding="utf-8",timeout=8,check=True)
        filename,actual=result.stdout.split("|",1)
        if query.split(":")[0].lower() not in actual.lower() or not Path(filename).is_file():
            raise ValueError("矢量首选字体未可用；不能静默回退")
        css.append(f'@font-face{{font-family:{family};src:url("{Path(filename).as_uri()}");font-weight:{weight}}}')
    css.append('svg text,svg tspan{font-family:kpDiagramLatin,kpDiagramCJK!important}')
    return "\n".join(css)
