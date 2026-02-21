# Path Resolver

Der Path Resolver liefert konsistente Pfade fuer Projekt- und Batch-Ordner.
Er nutzt `AppConfig.projects_root` und erstellt alle benoetigten Ordner bei Bedarf.

## Standard-Ordner
- `project_dir` -> `ProjectsRoot/Project_<project_id>`
- `batches_dir` -> `project_dir/batches`
- `batch_dir` -> `batches_dir/Batch_<batch_id>`
- `config_dir` -> `batch_dir/Config`
- `ath_export_archive_dir` -> `batch_dir/ATH Export`
- `result_dir` -> `batch_dir/Resultate`
- `logs_dir` -> `batch_dir/Logs`
- `dataset_dir` -> `project_dir/dataset`

## Aenderungshinweis (2026-02-10)
Der legacy Path Resolver folgt dem Stand in `tools/legacy/path_resolver.py` (quarantined, non-shipping).

Der Resolver verwendet wieder Runner-kompatible Ordnernamen (`Config`, `ATH Export`, `Resultate`, `Logs`),
damit Batch-Erzeugung, Run-Orchestrierung und Dataset-Import dieselbe Struktur nutzen.

## Beispielbaum
```
ProjectsRoot/
  Project_P001/
    batches/
      Batch_B001/
        Config/
        ATH Export/
        Resultate/
        Logs/
    dataset/
```

## Verwendung (Python)
```
from app.models import AppConfig
from tools.legacy.path_resolver import resolve_paths

config = AppConfig(app_name="Batch-Software", projects_root="Documents/WUT-Batches/Projects")
paths = resolve_paths(config, project_id="P001", batch_id="B001")
```
