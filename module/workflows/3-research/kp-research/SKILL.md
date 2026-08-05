---
name: kp-research
description: Build a source ledger and evidence pack for required claims.
---

# Research Workflow

1. Define claim IDs for each factual assertion required by a chapter contract.
2. Append verified sources to `research/source-ledger.jsonl`.
3. Record source ID, URL, access time, excerpt, license note, and mapped claim IDs.
4. Run `kp validate --workspace <workspace>` before publication; Claim IDs are collected from current contracts.
