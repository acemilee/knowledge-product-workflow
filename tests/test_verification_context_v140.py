from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("operation", ["create", "remove", "nested_create", "missing_directory"])
def test_directory_memo_tracks_owned_membership_changes(tmp_path, operation):
    from knowledge_product_workflow.verification_context import (
        glob_files,
        mkdir,
        unlink,
        verification_scope,
        write_text,
    )

    directory = tmp_path / "facts"
    if operation != "missing_directory":
        directory.mkdir()
    target = directory / "owned.json"
    pattern = "**/*.json" if operation == "nested_create" else "*.json"
    if operation == "remove":
        target.write_text("{}", encoding="utf-8")
    with verification_scope(SimpleNamespace(root=tmp_path)) as context:
        query = lambda: [p.relative_to(directory).as_posix() for p in glob_files(directory, pattern)]
        assert context.memo("members", query) == (["owned.json"] if operation == "remove" else [])
        if operation == "remove":
            unlink(target)
        else:
            if operation == "nested_create":
                target = directory / "nested/owned.json"
            mkdir(target.parent, parents=True, exist_ok=True)
            write_text(target, "{}", encoding="utf-8")
        expected = [] if operation == "remove" else [target.relative_to(directory).as_posix()]
        assert context.memo("members", query) == expected


def test_directory_dependency_propagates_through_nested_memo_hit(tmp_path):
    from knowledge_product_workflow.verification_context import (
        glob_files,
        verification_scope,
        write_text,
    )

    with verification_scope(SimpleNamespace(root=tmp_path)) as context:
        inner = lambda: context.memo("inner", lambda: [p.name for p in glob_files(tmp_path, "*.json")])
        assert inner() == []
        assert context.memo("outer", inner) == []
        write_text(tmp_path / "next.json", "{}", encoding="utf-8")
        assert context.memo("outer", inner) == ["next.json"]


def test_directory_memo_keeps_unrelated_pattern_and_output(tmp_path):
    from knowledge_product_workflow.verification_context import (
        glob_files,
        verification_scope,
        write_text,
    )

    directory = tmp_path / "facts"
    directory.mkdir()
    calls = []

    def query():
        calls.append(1)
        return [p.name for p in glob_files(directory, "*.json")]

    with verification_scope(SimpleNamespace(root=tmp_path)) as context:
        assert context.memo("members", query) == []
        write_text(directory / "notes.txt", "unrelated pattern", encoding="utf-8")
        write_text(tmp_path / "controlled-output.txt", "unrelated directory", encoding="utf-8")
        assert context.memo("members", query) == []
        assert calls == [1]


def test_external_glob_member_file_type_change_rejects_controlled_write(tmp_path):
    from knowledge_product_workflow.verification_context import (
        SnapshotChangedError,
        glob_files,
        verification_scope,
        write_text,
    )

    member = tmp_path / "member.json"
    member.write_text("{}", encoding="utf-8")
    output = tmp_path / "must-not-publish.txt"
    with pytest.raises(SnapshotChangedError), verification_scope(SimpleNamespace(root=tmp_path)) as context:
        assert context.memo("members", lambda: [p.name for p in glob_files(tmp_path, "*.json")]) == [member.name]
        member.unlink()
        member.mkdir()
        write_text(output, "must reject changed file kind", encoding="utf-8")
    assert not output.exists()


def _directory_link(link, target):
    import os
    import subprocess

    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       capture_output=True, check=True, timeout=10)
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("kind", ["bytes", "existence", "directory"])
def test_memo_never_reuses_untracked_junction_inputs(tmp_path, kind):
    from knowledge_product_workflow.verification_context import (
        glob_files,
        is_file,
        memoized_read,
        read_bytes,
        verification_scope,
    )

    root, outside = tmp_path / "workspace", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "linked"
    _directory_link(link, outside)
    target = outside / "value.txt"
    if kind == "bytes":
        target.write_bytes(b"before")

    @memoized_read
    def query(workspace):
        path = workspace.root / "linked/value.txt"
        if kind == "bytes":
            return read_bytes(path)
        if kind == "existence":
            return is_file(path)
        return [p.name for p in glob_files(path.parent, "*.txt")]

    with verification_scope(SimpleNamespace(root=root)):
        first = query(SimpleNamespace(root=root))
        target.write_bytes(b"after")
        assert query(SimpleNamespace(root=root)) != first


def test_memo_does_not_keep_an_internal_junction_after_retarget(tmp_path):
    import os

    from knowledge_product_workflow.verification_context import (
        SnapshotChangedError,
        read_bytes,
        verification_scope,
    )

    root, outside = tmp_path / "workspace", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    target = root / "inside"
    target.mkdir()
    (target / "value.txt").write_bytes(b"before")
    (outside / "value.txt").write_bytes(b"after")
    link = root / "linked"
    _directory_link(link, target)
    with pytest.raises(SnapshotChangedError), verification_scope(SimpleNamespace(root=root)) as context:
        assert context.memo("alias", lambda: read_bytes(link / "value.txt")) == b"before"
        if os.name == "nt":
            link.rmdir()  # Remove this test's junction only; leave the target intact.
        else:
            link.unlink()
        _directory_link(link, outside)
        assert context.memo("alias", lambda: read_bytes(link / "value.txt")) == b"after"


@pytest.mark.parametrize("kind", ["bytes", "existence", "directory"])
def test_observed_alias_retarget_rejects_before_controlled_write(tmp_path, kind):
    import os

    from knowledge_product_workflow.verification_context import (
        SnapshotChangedError,
        glob_files,
        is_file,
        read_bytes,
        verification_scope,
        write_text,
    )

    root = tmp_path / "workspace"
    root.mkdir()
    first, second = root / "first", root / "second"
    first.mkdir()
    second.mkdir()
    (first / "value.txt").write_text("same", encoding="utf-8")
    (second / "value.txt").write_text("same", encoding="utf-8")
    alias = root / "alias"
    _directory_link(alias, first)
    result = root / "result.txt"
    with pytest.raises(SnapshotChangedError), verification_scope(SimpleNamespace(root=root)):
        if kind == "bytes":
            assert read_bytes(alias / "value.txt") == b"same"
        elif kind == "existence":
            assert is_file(alias / "value.txt")
        else:
            assert len(glob_files(alias, "*.txt")) == 1
        if os.name == "nt":
            alias.rmdir()
        else:
            alias.unlink()
        _directory_link(alias, second)
        with pytest.raises(SnapshotChangedError):
            write_text(result, "stale input", encoding="utf-8")
        assert not result.exists()


def test_transaction_admission_observes_changed_bytes_before_scope_exit(tmp_path):
    import json

    from knowledge_product_workflow.verification_context import (
        SnapshotChangedError,
        verification_scope,
    )
    from knowledge_product_workflow.workspace_transactions import pending_workspace_transactions

    folder = tmp_path / "runs/orchestration/transactions"
    folder.mkdir(parents=True)
    path = folder / "tx-local.json"
    payload = {"operation_id": "tx-local", "book_id": "local", "kind": "chapter_submission",
               "scope_chapters": [1], "outputs": {}, "state": "completed"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SnapshotChangedError), verification_scope(SimpleNamespace(root=tmp_path)):
        assert pending_workspace_transactions(SimpleNamespace(root=tmp_path)) == []
        payload["state"] = "prepared"
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert pending_workspace_transactions(SimpleNamespace(root=tmp_path))[0]["state"] == "prepared"


def test_repeated_contract_checks_share_blueprint_validation(tmp_path, monkeypatch):
    from knowledge_product_workflow.verification_context import verification_scope
    from knowledge_product_workflow.workflows import authoring, curriculum
    from tests.test_structure_review_v133 import _fixture

    workspace, _, _ = _fixture(tmp_path)
    original = curriculum.parse_blueprint_submission
    calls = []

    def parse(body):
        calls.append(body)
        return original(body)

    monkeypatch.setattr(curriculum, "parse_blueprint_submission", parse)
    with verification_scope(workspace):
        first = authoring.require_current_chapter_contract(workspace, 1)
        second = authoring.require_current_chapter_contract(workspace, 1)
        assert first == second
        first[1].title = "caller mutation"
        assert authoring.require_current_chapter_contract(workspace, 1)[1].title != "caller mutation"
    assert len(calls) == 1


def test_brief_constraints_use_one_safe_parse_per_invocation(tmp_path, monkeypatch):
    import yaml

    from knowledge_product_workflow.verification_context import verification_scope
    from knowledge_product_workflow.workflows.product_brief import parse_detail_constraints
    from tests.test_structure_review_v133 import _fixture

    workspace, _, _ = _fixture(tmp_path)
    body = (workspace.root / "planning/product-brief.md").read_text(encoding="utf-8")
    original = yaml.load
    calls = []

    def load(value, *args, **kwargs):
        calls.append(value)
        return original(value, *args, **kwargs)

    monkeypatch.setattr(yaml, "load", load)
    with verification_scope(workspace):
        assert parse_detail_constraints(body) == parse_detail_constraints(body)
    assert len(calls) == 1


def test_transaction_scans_read_current_bytes_and_share_only_parse(tmp_path, monkeypatch):
    import io
    import json
    from pathlib import Path

    from knowledge_product_workflow.verification_context import verification_scope
    from knowledge_product_workflow.workspace_transactions import (
        WorkspaceTransaction,
        pending_workspace_transactions,
    )

    path = tmp_path / "runs/orchestration/transactions/tx-local.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"operation_id": "tx-local", "book_id": "local",
                               "kind": "chapter_submission", "scope_chapters": [1],
                               "outputs": {}, "state": "completed"}), encoding="utf-8")
    original = io.open
    reads = []
    parses = []
    parse = WorkspaceTransaction.model_validate_json

    def validate(value, *args, **kwargs):
        parses.append(value)
        return parse(value, *args, **kwargs)

    monkeypatch.setattr(WorkspaceTransaction, "model_validate_json", validate)

    def opened(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, Path)) and Path(file) == path and "r" in mode:
            reads.append(mode)
        return original(file, mode, *args, **kwargs)

    monkeypatch.setattr(io, "open", opened)
    with verification_scope(SimpleNamespace(root=tmp_path)):
        for _ in range(3):
            assert pending_workspace_transactions(SimpleNamespace(root=tmp_path)) == []
        assert len(reads) == 3
        assert len(parses) == 1
    assert len(reads) == 4


@pytest.mark.parametrize("new_journal", [False, True])
def test_transaction_snapshot_change_blocks_next_owned_write(tmp_path, new_journal):
    import json
    import os

    from knowledge_product_workflow.verification_context import (
        SnapshotChangedError,
        verification_scope,
        write_text,
    )
    from knowledge_product_workflow.workspace_transactions import pending_workspace_transactions

    folder = tmp_path / "runs/orchestration/transactions"
    folder.mkdir(parents=True)
    path = folder / "tx-local.json"
    path.write_text(json.dumps({"operation_id": "tx-local", "book_id": "local",
                               "kind": "chapter_submission", "scope_chapters": [1],
                               "outputs": {}, "state": "completed"}), encoding="utf-8")
    stamp = path.stat()
    output = tmp_path / "must-not-write.md"
    with pytest.raises(SnapshotChangedError), verification_scope(SimpleNamespace(root=tmp_path)):
        assert pending_workspace_transactions(SimpleNamespace(root=tmp_path)) == []
        if new_journal:
            (folder / "tx-unknown.json").write_text("{}", encoding="utf-8")
        else:
            path.write_text(path.read_text(encoding="utf-8").replace("completed", "prepared"), encoding="utf-8")
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        write_text(output, "unsafe", encoding="utf-8")
    assert not output.exists()


def test_cached_blueprint_rejects_external_change_before_owned_write(tmp_path):
    import os

    from knowledge_product_workflow.verification_context import (
        SnapshotChangedError,
        verification_scope,
        write_text,
    )
    from knowledge_product_workflow.workflows.curriculum import require_current_submitted_blueprint
    from tests.test_structure_review_v133 import _fixture

    workspace, _, _ = _fixture(tmp_path)
    brief = workspace.root / "planning/product-brief.md"
    stamp = brief.stat()
    output = workspace.root / "runs/must-not-write.md"
    with pytest.raises(SnapshotChangedError), verification_scope(workspace):
        require_current_submitted_blueprint(workspace)
        brief.write_text(brief.read_text(encoding="utf-8") + "\nexternal", encoding="utf-8")
        os.utime(brief, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        require_current_submitted_blueprint(workspace)
        write_text(output, "unsafe", encoding="utf-8")
    assert not output.exists()


def test_cached_blueprint_owned_change_rechecks_approval(tmp_path):
    from knowledge_product_workflow.frontmatter import (
        parse_markdown_artifact,
        render_markdown_artifact,
    )
    from knowledge_product_workflow.models import ArtifactStatus
    from knowledge_product_workflow.verification_context import verification_scope, write_text
    from knowledge_product_workflow.workflows.curriculum import require_current_submitted_blueprint
    from tests.test_structure_review_v133 import _fixture

    workspace, _, _ = _fixture(tmp_path)
    brief = workspace.root / "planning/product-brief.md"
    with verification_scope(workspace):
        require_current_submitted_blueprint(workspace)
        metadata, body = parse_markdown_artifact(brief.read_text(encoding="utf-8"))
        metadata.status = ArtifactStatus.DRAFT
        write_text(brief, render_markdown_artifact(metadata, body), encoding="utf-8")
        with pytest.raises(ValueError, match="批准"):
            require_current_submitted_blueprint(workspace)


def test_cached_contract_does_not_cache_transaction_admission(tmp_path):
    import json

    from knowledge_product_workflow.verification_context import verification_scope, write_text
    from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract
    from tests.test_structure_review_v133 import _fixture

    workspace, _, _ = _fixture(tmp_path)
    journal = workspace.root / "runs/orchestration/transactions/tx-new.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    with verification_scope(workspace):
        require_current_chapter_contract(workspace, 1)
        write_text(journal, json.dumps({"operation_id": "tx-new", "book_id": workspace.root.name,
                                       "kind": "chapter_submission", "scope_chapters": [1],
                                       "outputs": {"chapters/chapter-01-contract.md": {}},
                                       "state": "prepared"}), encoding="utf-8")
        with pytest.raises(ValueError, match="事务待恢复"):
            require_current_chapter_contract(workspace, 1)


def test_missing_fact_ledger_is_a_snapshot_input(tmp_path):
    from knowledge_product_workflow.claims import claim_scoped_evidence_digests
    from knowledge_product_workflow.verification_context import (
        SnapshotChangedError,
        verification_scope,
    )
    from knowledge_product_workflow.workspace import Workspace

    w = Workspace.create(tmp_path, "absence")
    ledger = w.root / "research/source-ledger.jsonl"
    ledger.unlink(missing_ok=True)
    with pytest.raises(SnapshotChangedError), verification_scope(w):
        claim_scoped_evidence_digests(w, {"claim-test"})
        ledger.write_text("{}\n", encoding="utf-8")


def test_missing_planning_exists_branch_is_a_snapshot_input(tmp_path):
    from knowledge_product_workflow.developmental import book_reading_index
    from knowledge_product_workflow.verification_context import (
        SnapshotChangedError,
        verification_scope,
    )
    from knowledge_product_workflow.workspace import Workspace

    w = Workspace.create(tmp_path, "bare-index")
    with pytest.raises(SnapshotChangedError), verification_scope(w):
        book_reading_index(w, 1, "## 1.1 输入\n合成阅读单元。")
        (w.root / "planning/product-brief.md").write_text("external new input", encoding="utf-8")


def test_same_invocation_reuses_reads_and_returns_independent_parse_values(tmp_path, monkeypatch):
    from knowledge_product_workflow.verification_context import verification_scope

    w = SimpleNamespace(root=tmp_path)
    p = tmp_path / "input.yaml"
    p.write_text("value: [1]", encoding="utf-8")
    calls = []

    def parse(data):
        calls.append(data)
        return {"value": [1]}

    with verification_scope(w) as context:
        a = context.parsed(p, "fixture", parse)
        a["value"].append(2)
        assert context.parsed(p, "fixture", parse) == {"value": [1]}
        with verification_scope(w) as nested:
            assert nested is context
        assert len(calls) == 1


def test_external_same_mtime_change_is_not_a_valid_snapshot(tmp_path):
    import os

    from knowledge_product_workflow.verification_context import (
        SnapshotChangedError,
        verification_scope,
    )

    p = tmp_path / "input.md"
    p.write_text("old", encoding="utf-8")
    stamp = p.stat()
    with (
        pytest.raises(SnapshotChangedError),
        verification_scope(SimpleNamespace(root=tmp_path)) as context,
    ):
        assert context.read_bytes(p) == b"old"
        p.write_bytes(b"new")
        os.utime(p, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))


def test_explicit_context_cannot_cross_workspaces_and_own_changes_invalidate(tmp_path):
    from knowledge_product_workflow.verification_context import verification_scope

    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    p = a / "input.md"
    p.write_bytes(b"old")
    with verification_scope(SimpleNamespace(root=a)) as context:
        assert context.read_bytes(p) == b"old"
        with (
            pytest.raises(ValueError),
            verification_scope(SimpleNamespace(root=b), context=context),
        ):
            pass
        context.verify_read_set()
        p.write_bytes(b"new")
        context.next_phase([p])
        assert context.read_bytes(p) == b"new"
    with (
        pytest.raises(ValueError),
        verification_scope(SimpleNamespace(root=a), context=context),
    ):
        pass


def test_new_unknown_journal_is_detected_but_owned_member_changes_are_valid(tmp_path):
    from knowledge_product_workflow.verification_context import (
        SnapshotChangedError,
        verification_scope,
    )

    with (
        pytest.raises(SnapshotChangedError),
        verification_scope(SimpleNamespace(root=tmp_path)) as context,
    ):
        assert context.watch_directory(tmp_path, "*.json") == []
        (tmp_path / "unknown.json").write_bytes(b"{}")
    with verification_scope(SimpleNamespace(root=tmp_path)) as context:
        context.watch_directory(tmp_path, "*.json")
        context.verify_read_set()
        target = tmp_path / "owned.json"
        target.write_bytes(b"{}")
        context.next_phase([target])
        assert target in context.watch_directory(tmp_path, "*.json")


def test_control_metadata_is_current_and_yaml_remains_safe(tmp_path):
    import yaml

    from knowledge_product_workflow.verification_context import (
        load_yaml,
        read_text,
        verification_scope,
    )

    p = tmp_path / "runs/orchestration/run-intent.json"
    p.parent.mkdir(parents=True)
    p.write_text('{"state":"active"}', encoding="utf-8")
    with verification_scope(SimpleNamespace(root=tmp_path)):
        assert "active" in read_text(p, encoding="utf-8")
        p.write_text('{"state":"paused"}', encoding="utf-8")
        assert "paused" in read_text(p, encoding="utf-8")
        with pytest.raises(yaml.constructor.ConstructorError):
            load_yaml('!!python/object/apply:os.system ["do-not-execute"]')


def test_review_without_comparisons_does_not_build_a_whole_book_index(tmp_path, monkeypatch):
    from knowledge_product_workflow import developmental
    from tests.test_structure_review_v133 import _fixture, simulated_review

    w, source, body = _fixture(tmp_path)
    source.write_text(body, encoding="utf-8")
    simulated_review(w, source)

    def unnecessary(*args, **kwargs):
        raise AssertionError("unreferenced whole-book reading index")

    monkeypatch.setattr(developmental, "book_reading_index", unnecessary)
    developmental.require_structure_review(w, 1, developmental._reader_body(source))


def test_reading_index_cannot_silence_a_required_invalid_plan(tmp_path):
    from knowledge_product_workflow.content_plan import ContentPlanError
    from knowledge_product_workflow.developmental import book_reading_index
    from tests.test_structure_review_v133 import _fixture

    w, _source, body = _fixture(tmp_path)
    (w.root / "chapters/chapter-01-content-plan.yaml").write_text(
        "sections: invalid", encoding="utf-8"
    )
    with pytest.raises(ContentPlanError):
        book_reading_index(w, 1, body)


def test_current_comparison_digest_preserves_existing_valid_proofs(tmp_path):
    import hashlib
    import json

    from knowledge_product_workflow.content_plan import require_current_content_plan
    from knowledge_product_workflow.developmental import book_reading_index
    from tests.test_structure_review_v133 import _fixture

    w, _source, body = _fixture(tmp_path)
    sections = {s.section_id: s for s in require_current_content_plan(w, 1).sections}
    for row in book_reading_index(w, 1, body):
        old_projection = {
            "unit": row["unit_sha256"],
            "task": sections[row["unit_id"]].model_dump(mode="json"),
        }
        assert (
            row["comparison_sha256"]
            == hashlib.sha256(json.dumps(old_projection, sort_keys=True).encode()).hexdigest()
        )


def test_different_claim_queries_share_one_ledger_parse_and_keep_scopes(tmp_path, monkeypatch):
    import json

    from knowledge_product_workflow import claims
    from knowledge_product_workflow.verification_context import verification_scope

    folder = tmp_path / "research"
    folder.mkdir()
    ledger = folder / "source-ledger.jsonl"
    ledger.write_text(
        "\n".join(
            json.dumps({"claim_ids": [name], "excerpt": name}) for name in ["claim-a", "claim-b"]
        ),
        encoding="utf-8",
    )
    calls = []
    original = json.loads

    def parse(value, *args, **kwargs):
        calls.append(value)
        return original(value, *args, **kwargs)

    monkeypatch.setattr(claims.json, "loads", parse)
    w = SimpleNamespace(root=tmp_path)
    with verification_scope(w):
        a = claims.claim_scoped_evidence_digests(w, {"claim-a"})
        b = claims.claim_scoped_evidence_digests(w, {"claim-b"})
    assert a != b
    assert len(calls) == 2
    ledger.write_text(ledger.read_text(encoding="utf-8") + "\n{broken", encoding="utf-8")
    assert claims.claim_scoped_evidence_digests(w, {"claim-a"}) != a
