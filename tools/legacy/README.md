# Legacy Quarantine

This folder holds modules that are intentionally not part of the shipping production
surface. They remain available for historical analysis, docs parity, or targeted tests.

Status:
- Non-shipping by default.
- Keep isolated from `app` runtime orchestration paths.
- Remove only after downstream references are cleaned up.

Quarantined modules:
- `tools/legacy/gui_theme.py`
- `tools/legacy/parameter_registry.py`
- `tools/legacy/path_resolver.py`
- `tools/legacy/storage_migrations.py`
- `tools/legacy/ui_risk_layer.py`
- `tools/legacy/compat_rules.py`

