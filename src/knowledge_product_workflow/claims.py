"""Current contract-bound Claim inventory helpers."""

from __future__ import annotations

import json
from hashlib import sha256

from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract
from knowledge_product_workflow.workflows.curriculum import require_current_submitted_blueprint
from knowledge_product_workflow.workspace import Workspace


def claim_scoped_evidence_digests(workspace: Workspace, claims: set[str]) -> dict[str, str]:
    """Hash relevant ledger rows; malformed or unscoped evidence stays conservative."""
    result = {}
    for relative, field in (
        ("research/source-ledger.jsonl", "claim_ids"),
        ("research/assumption-ledger.jsonl", "evidence_claim_ids"),
        ("research/claim-classifications.jsonl", "claim_id"),
    ):
        ledger = workspace.root / relative
        selected = []
        if ledger.is_file():
            for line in ledger.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise TypeError("Ledger row must be an object")
                    ids = row.get(field, [])
                    ids = [ids] if isinstance(ids, str) else ids
                    if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
                        raise TypeError("Ledger claim identifiers must be strings")
                except (ValueError, TypeError):
                    selected = [{"invalid_ledger_sha256": sha256(ledger.read_bytes()).hexdigest()}]
                    break
                if not ids or claims.intersection(ids):
                    selected.append(row)
        result[relative] = sha256(json.dumps(selected, ensure_ascii=True, sort_keys=True,
                                            separators=(",", ":")).encode()).hexdigest()
    return result


def current_claim_contract_bindings(workspace: Workspace) -> dict[str, str]:
    """Return every current Claim ID and the SHA-256 of its declaring chapter contract."""
    _, _, blueprint = require_current_submitted_blueprint(workspace)
    bindings: dict[str, str] = {}
    for chapter in blueprint.chapters:
        contract_path, _ = require_current_chapter_contract(workspace, chapter.chapter_number)
        contract_sha256 = sha256(contract_path.read_bytes()).hexdigest()
        for claim_id in chapter.claim_ids:
            normalized = claim_id.casefold()
            if normalized in bindings:
                raise ValueError(f"Claim ID 只能在当前蓝图中声明一次：{claim_id}")
            bindings[normalized] = contract_sha256
    return bindings
