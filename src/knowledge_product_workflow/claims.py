"""Current contract-bound Claim inventory helpers."""

from __future__ import annotations

import json
from hashlib import sha256

from knowledge_product_workflow.verification_context import (
    current_verification as _v_current_context,
)
from knowledge_product_workflow.verification_context import is_file as _v_is_file
from knowledge_product_workflow.verification_context import (
    read_bytes as _v_read_bytes,
)
from knowledge_product_workflow.verification_context import (
    read_text as _v_read_text,
)
from knowledge_product_workflow.verification_context import (
    verified_read as _verification_read,
)
from knowledge_product_workflow.workflows.authoring import (
    require_current_chapter_contract_in_view as require_current_chapter_contract,
)
from knowledge_product_workflow.workflows.curriculum import require_current_submitted_blueprint
from knowledge_product_workflow.workspace import Workspace


def _ledger_index(data: bytes, field: str) -> dict:
    rows, positions, common = [], {}, []
    text = data.decode("utf-8")
    try:
        for line in text.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise TypeError("Ledger row must be an object")
            ids = row.get(field, [])
            ids = [ids] if isinstance(ids, str) else ids
            if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
                raise TypeError("Ledger claim identifiers must be strings")
            number = len(rows)
            rows.append(row)
            if not ids:
                common.append(number)
            for claim in ids:
                positions.setdefault(claim, []).append(number)
    except (ValueError, TypeError):
        return {"invalid": sha256(data).hexdigest()}
    return {"rows": rows, "positions": positions, "common": common}


@_verification_read
def claim_scoped_evidence_digests(workspace: Workspace, claims: set[str]) -> dict[str, str]:
    """Index each ledger once per frozen bytes, preserving row order and scope."""
    context = _v_current_context(workspace)
    result = {}
    for relative, field in (
        ("research/source-ledger.jsonl", "claim_ids"),
        ("research/assumption-ledger.jsonl", "evidence_claim_ids"),
        ("research/claim-classifications.jsonl", "claim_id"),
    ):
        ledger = workspace.root / relative
        selected = []
        if _v_is_file(ledger):
            data = _v_read_bytes(ledger)
            loader = lambda raw, field=field: _ledger_index(raw, field)
            index = context.parsed_payload("ledger-index.v1:"+field, data, loader) if context is not None else loader(data)
            if "invalid" in index:
                selected = [{"invalid_ledger_sha256": index["invalid"]}]
            else:
                positions = set(index["common"])
                for claim in claims:
                    positions.update(index["positions"].get(claim, []))
                selected = [index["rows"][i] for i in sorted(positions)]
        result[relative] = sha256(json.dumps(selected, ensure_ascii=True, sort_keys=True,
                                            separators=(",", ":")).encode()).hexdigest()
    return result


@_verification_read
def current_claim_contract_bindings(workspace: Workspace, *, statements: dict | None = None) -> dict[str, str]:
    from knowledge_product_workflow.change_impact import semantic_digest

    context = _v_current_context(workspace)
    compute = lambda: _current_claim_contract_bindings(workspace, statements=statements)
    return context.memo(("claim-bindings.v1", semantic_digest(statements or {})), compute) if context is not None else compute()


def _current_claim_contract_bindings(workspace: Workspace, *, statements: dict | None = None) -> dict[str, str]:
    """Return every current Claim ID and the SHA-256 of its declaring chapter contract."""
    import json

    from knowledge_product_workflow.change_impact import (
        binding_policy_revision,
        proof_contract_digest,
        semantic_digest,
    )
    definitions = {}
    path = workspace.root / "research/claim-classifications.jsonl"
    if binding_policy_revision(workspace) == 2 and _v_is_file(path):
        for line in _v_read_text(path, encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                definitions[record["claim_id"]] = {k: record[k] for k in ("kind", "statement", "heading_anchor")}
    definitions.update(statements or {})
    _, _, blueprint = require_current_submitted_blueprint(workspace)
    bindings: dict[str, str] = {}
    for chapter in blueprint.chapters:
        contract_path, _ = require_current_chapter_contract(workspace, chapter.chapter_number)
        contract_sha256 = sha256(_v_read_bytes(contract_path)).hexdigest()
        for claim_id in chapter.claim_ids:
            normalized = claim_id.casefold()
            if normalized in bindings:
                raise ValueError(f"Claim ID 只能在当前蓝图中声明一次：{claim_id}")
            bindings[normalized] = (semantic_digest({"policy": 2, "claim": normalized,
                "scope": proof_contract_digest(workspace, chapter.chapter_number, "facts"),
                "definition": definitions.get(normalized)})
                if binding_policy_revision(workspace) == 2 else contract_sha256)
    return bindings
