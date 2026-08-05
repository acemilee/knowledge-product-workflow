import subprocess
from pathlib import Path

from knowledge_product_workflow import quarto


def test_resolve_quarto_uses_the_verified_candidate_with_short_health_check(
    tmp_path: Path, monkeypatch
) -> None:
    candidate = tmp_path / "quarto.exe"
    candidate.write_text("fixture", encoding="utf-8")
    calls: list[tuple[list[str], int]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs["timeout"]))
        return subprocess.CompletedProcess(command, 0, stdout="1.7.31\n", stderr="")

    monkeypatch.setattr(quarto, "_candidate_paths", lambda: ((candidate, "测试候选"),))
    monkeypatch.setattr(quarto.subprocess, "run", fake_run)

    resolution = quarto.resolve_quarto()

    assert resolution.available
    assert resolution.path == candidate.resolve()
    assert resolution.source == "测试候选"
    assert resolution.version == "1.7.31"
    assert calls == [([str(candidate), "--version"], 8)]


def test_resolve_quarto_reports_unavailable_without_disk_scan(monkeypatch) -> None:
    missing = Path(r"C:\\missing\\quarto.exe")
    monkeypatch.setattr(quarto, "_candidate_paths", lambda: ((missing, "PATH"),))

    resolution = quarto.resolve_quarto()

    assert not resolution.available
    assert resolution.failed_candidates == (f"PATH：不存在（{missing}）",)


def test_resolve_quarto_finds_current_user_install_when_host_path_is_stale(
    tmp_path: Path, monkeypatch
) -> None:
    local_app_data = tmp_path / "AppData" / "Local"
    candidate = local_app_data / "Programs" / "Quarto" / "bin" / "quarto.exe"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("fixture", encoding="utf-8")

    monkeypatch.setattr(quarto, "_is_windows", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.delenv("KP_QUARTO_PATH", raising=False)
    monkeypatch.setattr(quarto.shutil, "which", lambda _: None)
    monkeypatch.setattr(quarto, "_persistent_user_path_candidates", lambda: ())
    monkeypatch.setattr(quarto, "_registry_candidates", lambda: ())
    monkeypatch.setattr(
        quarto.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="1.10.18\n", stderr=""),
    )

    resolution = quarto.resolve_quarto()

    assert resolution.available
    assert resolution.path == candidate.resolve()
    assert resolution.source == "当前用户默认安装路径"
    assert resolution.version == "1.10.18"
