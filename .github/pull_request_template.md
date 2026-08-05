## Summary

<!-- Explain the user-visible or engineering outcome. -->

## Validation

- [ ] `uv run pytest -q`
- [ ] `uv run ruff check src tests`
- [ ] `uv lock --check`
- [ ] I ran the relevant PDF, D2, or host integration checks, or explained why they are unavailable.

## Checklist

- [ ] No API Key, customer material, personal information, absolute local path, generated book, PDF, runtime trace, or temporary artifact is included.
- [ ] New behavior has regression coverage where practical.
- [ ] User-facing Chinese messages do not expose internal control-plane details.
