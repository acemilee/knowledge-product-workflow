"""本地编译预算用自有进程桩验证，不真实等待或调用Quarto。"""
import subprocess
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from knowledge_product_workflow import publishing
from knowledge_product_workflow.cli import app


def _owned_compile(tmp_path, monkeypatch):
    qmd = tmp_path / "book.qmd"
    qmd.write_text("# 预算探针\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(publishing, "resolve_quarto", lambda: SimpleNamespace(available=True, path=tmp_path / "quarto.exe"))
    monkeypatch.setattr(publishing, "_quarto_paths", lambda *_: nullcontext((qmd, tmp_path, tmp_path, {})))
    monkeypatch.setattr("knowledge_product_workflow.quarto_runtime.quarto_command", lambda *_: nullcontext((["owned-compile"], {})))
    def run(command, **kwargs):
        calls.append(kwargs["timeout"])
        if kwargs["timeout"] < 70:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        (tmp_path / "book.pdf").write_bytes(b"owned process probe; not a real PDF acceptance")
        return SimpleNamespace(stdout="Output created", stderr="", returncode=0)
    monkeypatch.setattr(publishing.subprocess, "run", run)
    return qmd, calls


def test_normal_compile_longer_than_old_sixty_seconds_uses_default_three_hundred(tmp_path, monkeypatch):
    qmd, calls = _owned_compile(tmp_path, monkeypatch)
    assert publishing.render_pdf(qmd, tmp_path).is_file()
    assert calls == [300]


def test_explicit_short_budget_fails_once_and_restores_previous_output(tmp_path, monkeypatch):
    qmd, calls = _owned_compile(tmp_path, monkeypatch)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"previous artifact")
    with pytest.raises(RuntimeError, match="渲染失败"):
        publishing.render_pdf(qmd, tmp_path, compile_timeout_seconds=60)
    assert calls == [60] and pdf.read_bytes() == b"previous artifact"


@pytest.mark.parametrize("bad", [0, -1, 601, True, 300.5])
def test_invalid_budget_is_rejected_before_path_or_tool_discovery(tmp_path, monkeypatch, bad):
    def unexpected():
        pytest.fail("bad budget must not discover or start a compiler")
    monkeypatch.setattr(publishing, "resolve_quarto", unexpected)
    with pytest.raises(ValueError, match="编译预算"):
        publishing.render_pdf(tmp_path / "missing.qmd", tmp_path, compile_timeout_seconds=bad)


def test_cli_exposes_compile_budget_without_changing_network_timeout():
    result = CliRunner().invoke(app, ["render-pdf", "--help"])
    assert result.exit_code == 0 and "--compile-timeout" in result.stdout
