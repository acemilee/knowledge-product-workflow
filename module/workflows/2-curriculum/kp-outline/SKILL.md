---
name: kp-outline
description: Submit and approve a topic-specific curriculum blueprint from an approved brief.
---

# Curriculum Workflow

1. Confirm the product brief is approved.
2. Run `kp outline --workspace <workspace>` to create the Brief-bound YAML input template only.
3. Fill the YAML with topic-specific chapters, weights, rationale, archetypes, teaching units, claims, and any interview-answer policy; run `kp submit-outline --workspace <workspace> --from <template.yaml>`.
4. Review the submitted learning path, weights, scope, and teaching tradeoffs with the human.
5. Approve `planning/curriculum-blueprint.md`, then run `kp compile-contracts --all --workspace <workspace>`.
