from knowledge_product_workflow import publishing
from tests.test_reader_delivery_integration_v142 import _resource_book


def test_assembly_builds_expensive_resource_closure_once(tmp_path, monkeypatch):
    w, *_ = _resource_book(tmp_path)
    calls = []
    original = publishing._reader_resource_payloads
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(publishing, "_reader_resource_payloads", counted)
    publishing.assemble_book(w)
    assert len(calls) == 1
