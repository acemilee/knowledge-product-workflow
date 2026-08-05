"""Current contract-bound Claim inventory helpers."""

from __future__ import annotations

from hashlib import sha256

from knowledge_product_workflow.workflows.authoring import require_current_chapter_contract
from knowledge_product_workflow.workflows.curriculum import require_current_submitted_blueprint
from knowledge_product_workflow.workspace import Workspace


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
