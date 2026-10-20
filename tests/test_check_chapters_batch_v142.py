import json

import pytest
from typer.testing import CliRunner

from knowledge_product_workflow.chapter_compliance import check_chapter, check_chapters
from knowledge_product_workflow.cli import app
from tests.test_structure_review_v133 import _fixture


def test_batch_matches_single_reports_and_uses_one_lock(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from knowledge_product_workflow import production_intake
    w, source, body = _fixture(tmp_path)
    source.write_text(body, encoding="utf-8")
    canonical = w.root/"chapters/chapter-01.md"
    canonical.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    expected = check_chapter(w, 1, canonical)
    original = production_intake.production_lock
    calls = []
    @contextmanager
    def counted(*a, **kw):
        calls.append(1)
        with original(*a, **kw):
            yield
    monkeypatch.setattr(production_intake, "production_lock", counted)
    (w.root/"runs/production").mkdir(exist_ok=True)
    result = check_chapters(w, [1, 2])
    assert result["reports"][0] == expected
    assert result["reports"][1]["state"] == "missing"
    assert len(calls) == 1 and not result["all_current"]
    output = CliRunner().invoke(app, ["check-chapters", "--chapter", "1", "--chapter", "2", "--workspace", str(w.root), "--json"])
    assert output.exit_code == 0, output.output
    assert not json.loads(output.output)["all_current"]


@pytest.mark.parametrize("numbers", [[], [0], [1, 1], [True]])
def test_bad_batches_are_rejected(tmp_path, numbers):
    from knowledge_product_workflow.workspace import Workspace
    with pytest.raises(ValueError):
        check_chapters(Workspace.create(tmp_path, "bad"), numbers)


def test_batch_does_not_emit_coherent_json_after_same_mtime_change(tmp_path, monkeypatch):
    import os

    from knowledge_product_workflow import chapter_compliance
    w, _source, body = _fixture(tmp_path)
    canonical = w.root/"chapters/chapter-01.md"
    canonical.write_text(body, encoding="utf-8")
    original = chapter_compliance.check_chapter_in_view
    def changed(*a, **kw):
        result = original(*a, **kw)
        stamp = canonical.stat()
        canonical.write_text(body+"\n变更输入\n", encoding="utf-8")
        os.utime(canonical, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        return result
    monkeypatch.setattr(chapter_compliance, "check_chapter_in_view", changed)
    result = CliRunner().invoke(app, ["check-chapters", "--chapter", "1", "--workspace", str(w.root), "--json"])
    assert result.exit_code == 1
    assert '"coherent_snapshot": true' not in result.output
