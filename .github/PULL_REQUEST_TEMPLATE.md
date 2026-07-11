## What & why

<!-- one or two sentences: what changes, and the problem it solves -->

## Changelog

- **Added**:
- **Changed**:
- **Fixed**:

## Checklist

- [ ] `QT_QPA_PLATFORM=offscreen .venv/bin/pytest` passes
- [ ] No Qt imports under `crew/engine/` (enforced by test)
- [ ] Config writes go through `patch_config_file` (never rewrite files wholesale)
- [ ] New models added to `providers/catalog.py` (cost tracking)
