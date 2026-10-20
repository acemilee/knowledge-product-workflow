"""Minimal root and write-boundary guards retained after rejecting path caching."""

from types import SimpleNamespace

import pytest

from knowledge_product_workflow import verification_context as vc


def _rebind(directory, foreign):
    from tests.test_verification_context_v140 import _directory_link

    directory.rename(directory.with_name(directory.name + "-retained"))
    _directory_link(directory, foreign)


@pytest.mark.parametrize("boundary", ["write", "return", "public", "phase"])
def test_same_byte_root_rebinding_is_refused_before_further_authority(tmp_path, boundary):
    root, foreign = tmp_path / "book", tmp_path / "foreign"
    root.mkdir()
    foreign.mkdir()
    for path in [root, foreign]:
        (path / "input.txt").write_bytes(b"same bytes")
        (path / "output.txt").write_bytes(b"before")
    workspace = SimpleNamespace(root=root)

    @vc.verified_read(transaction_view=True)
    def public(current):
        return vc.read_bytes(current.root / "input.txt")

    with pytest.raises(vc.SnapshotChangedError), vc.verification_scope(workspace) as context:
        assert context.read_bytes(root / "input.txt") == b"same bytes"
        _rebind(root, foreign)
        if boundary == "write":
            vc.write_bytes(root / "output.txt", b"must not publish")
            pytest.fail("root guard must run even when owns(path) becomes false")
        elif boundary == "public":
            public(workspace)
            pytest.fail("new public entry must reject a rebound root")
        elif boundary == "phase":
            context.next_phase([])
            pytest.fail("owned phase must reject a rebound root")
    assert (foreign / "output.txt").read_bytes() == b"before"


@pytest.mark.parametrize("existing_alias", [False, True])
@pytest.mark.parametrize("root_alias", [False, True])
def test_workspace_write_name_cannot_point_outside_root(tmp_path, existing_alias, root_alias):
    from tests.test_verification_context_v140 import _directory_link

    root, foreign = tmp_path / "book", tmp_path / "foreign"
    root.mkdir()
    foreign.mkdir()
    if root_alias:
        exposed = tmp_path / "book-link"
        _directory_link(exposed, root)
        root = exposed
    (foreign / "output.txt").write_bytes(b"foreign bytes")
    outputs = root / "outputs"
    if existing_alias:
        _directory_link(outputs, foreign)
    else:
        outputs.mkdir()
    workspace = SimpleNamespace(root=root)
    with vc.verification_scope(workspace):
        if not existing_alias:
            _rebind(outputs, foreign)
        with pytest.raises(vc.SnapshotChangedError):
            vc.write_bytes(outputs / "output.txt", b"must not publish")
    assert (foreign / "output.txt").read_bytes() == b"foreign bytes"


@pytest.mark.parametrize("root_alias", [False, True])
def test_internal_alias_and_normal_owned_writes_keep_original_behavior(tmp_path, root_alias):
    from tests.test_verification_context_v140 import _directory_link

    root = tmp_path / "book"
    root.mkdir()
    actual = root / "actual"
    actual.mkdir()
    alias = root / "alias"
    _directory_link(alias, actual)
    if root_alias:
        exposed = tmp_path / "book-link"
        _directory_link(exposed, root)
        root = exposed
        alias = root / "alias"
    workspace = SimpleNamespace(root=root)
    with vc.verification_scope(workspace):
        vc.write_bytes(alias / "output.txt", b"owned bytes")
        vc.write_bytes(root / "ordinary.txt", b"ordinary")
    assert (actual / "output.txt").read_bytes() == b"owned bytes"
    assert (root / "ordinary.txt").read_bytes() == b"ordinary"


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("operation", ["run", "event", "write"])
def test_nested_scope_binds_each_original_root_alias(tmp_path, explicit, operation):
    from knowledge_product_workflow import production_tasks as production
    from knowledge_product_workflow.workspace import Workspace
    from tests.test_verification_context_v140 import _directory_link

    original = Workspace.create(tmp_path, "original")
    foreign = Workspace.create(tmp_path, "foreign")
    alias = tmp_path / "book-alias"
    _directory_link(alias, original.root)
    exposed = Workspace(alias)
    run = production.ProductionRun.diagnostic_run(1, production.ProductionPolicy())
    before = (foreign.root / "runs/events.jsonl").read_bytes()
    with (
        pytest.raises(vc.SnapshotChangedError),
        vc.verification_scope(original) as outer,
        vc.verification_scope(exposed, context=outer if explicit else None),
    ):
        _rebind(alias, foreign.root)
        if operation == "run":
            production.save_run(exposed, run)
        elif operation == "event":
            exposed.append_event("must_not_publish", {})
        else:
            vc.write_text(alias / "wrong.txt", "must not publish")
        pytest.fail("a second scope's root alias must stay bound before writing")
    assert not (foreign.root / "wrong.txt").exists()
    assert not (foreign.root / "runs/production" / run.run_id).exists()
    assert (foreign.root / "runs/events.jsonl").read_bytes() == before


def test_nested_alias_stays_bound_until_outer_command_finishes(tmp_path):
    from tests.test_verification_context_v140 import _directory_link

    root, foreign = tmp_path / "original", tmp_path / "foreign"
    root.mkdir()
    foreign.mkdir()
    alias = tmp_path / "alias"
    _directory_link(alias, root)
    with pytest.raises(vc.SnapshotChangedError), vc.verification_scope(root):
        with vc.verification_scope(alias):
            pass
        _rebind(alias, foreign)
        vc.write_text(root / "result.txt", "must not publish")
        pytest.fail("the outer command must retain every root binding it used")
    assert not (root / "result.txt").exists()
