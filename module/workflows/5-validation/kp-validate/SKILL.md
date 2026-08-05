---
name: kp-validate
description: Produce a quality report and block unsupported claims.
---

# Validation Workflow

1. Gather required claim IDs from chapter contracts.
2. Run `kp validate --workspace <workspace>`; it collects every required Claim from current contracts.
3. Resolve every blocker in `delivery/qa-report.json`.
4. Do not proceed to publishing while `publishable` is false.
