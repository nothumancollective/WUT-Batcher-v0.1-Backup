from __future__ import annotations

from pathlib import Path
import re
import tempfile
import unittest

from app.services import OrchestratorService, _apply_stl_export_hook
from app.settings_store import SettingsStore, UserSettings


class ServiceExportTests(unittest.TestCase):
    def test_export_uses_sql_params_and_omits_unset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            library_root = Path(tmp_dir) / "projects"
            template_cfg = Path(tmp_dir) / "template.cfg"
            template_cfg.write_text(
                "Length = 80\nThroat.Diameter = 10\nCoverage.Angle = 90\n",
                encoding="utf-8",
            )

            settings_path = Path(tmp_dir) / "settings.json"
            store = SettingsStore(settings_path)
            store.save(
                UserSettings(
                    library_root=str(library_root),
                    template_cfg=str(template_cfg),
                )
            )
            service = OrchestratorService(settings_store=store)

            project = service.create_project("Export Test", {"fixed_params": {"Length": 120}, "limits": {}})
            summary = service.create_batch(
                project_id=project.project_id,
                batch_name="B Export",
                selected_params={"Throat.Diameter": 30.0, "Coverage.Angle": None},
                sweeps={},
                sweep_mode="single",
                sim_export_params={},
            )

            manifest = service.export_version(
                project_id=project.project_id,
                batch_id=summary.batch_id,
                version_id=summary.version_ids[0],
                export_stl=False,
                export_abec=False,
            )

            cfg_path = Path(manifest["cfg_path"])
            cfg_text = cfg_path.read_text(encoding="utf-8")
            self.assertIn("Throat.Diameter", cfg_text)
            self.assertNotIn("Coverage.Angle", cfg_text)
            self.assertIn("Coverage.Angle", manifest["unset_params"])
            self.assertIn(str(Path(project.root_path) / "exports" / summary.batch_id / summary.version_ids[0]), manifest["export_dir"])

    def test_export_distinguishes_unset_from_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            library_root = Path(tmp_dir) / "projects"
            template_cfg = Path(tmp_dir) / "template.cfg"
            template_cfg.write_text(
                "Length = 80\nThroat.Diameter = 10\nCoverage.Angle = 90\n",
                encoding="utf-8",
            )
            settings_path = Path(tmp_dir) / "settings.json"
            store = SettingsStore(settings_path)
            store.save(
                UserSettings(
                    library_root=str(library_root),
                    template_cfg=str(template_cfg),
                )
            )
            service = OrchestratorService(settings_store=store)
            project = service.create_project("Unset vs Zero", {"fixed_params": {"Length": 120}, "limits": {}})
            summary = service.create_batch(
                project_id=project.project_id,
                batch_name="B1",
                selected_params={"Throat.Diameter": 0.0, "Coverage.Angle": None},
                sweeps={},
                sweep_mode="single",
                sim_export_params={},
            )
            manifest = service.export_version(
                project_id=project.project_id,
                batch_id=summary.batch_id,
                version_id=summary.version_ids[0],
                export_stl=False,
                export_abec=False,
            )
            cfg_text = Path(manifest["cfg_path"]).read_text(encoding="utf-8")
            self.assertRegex(cfg_text, re.compile(r"Throat\.Diameter\s*=\s*0\b"))
            self.assertNotIn("Coverage.Angle", cfg_text)

    def test_export_requires_ath_for_abec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            library_root = Path(tmp_dir) / "projects"
            settings_path = Path(tmp_dir) / "settings.json"
            store = SettingsStore(settings_path)
            store.save(UserSettings(library_root=str(library_root)))
            service = OrchestratorService(settings_store=store)
            project = service.create_project("ABEC Export", {"fixed_params": {"Length": 120}, "limits": {}})
            summary = service.create_batch(
                project_id=project.project_id,
                batch_name="B1",
                selected_params={"Throat.Diameter": 30.0},
                sweeps={},
                sweep_mode="single",
                sim_export_params={},
            )
            with self.assertRaises(ValueError):
                service.export_version(
                    project_id=project.project_id,
                    batch_id=summary.batch_id,
                    version_id=summary.version_ids[0],
                    export_stl=False,
                    export_abec=True,
                )

    def test_stl_hook_is_idempotent(self) -> None:
        cfg = "Length = 100\n"
        first, first_todo = _apply_stl_export_hook(cfg)
        second, second_todo = _apply_stl_export_hook(first)
        self.assertFalse(first_todo)
        self.assertFalse(second_todo)
        self.assertEqual(first, second)
        self.assertEqual(first.count("Output.STL = 1"), 1)

    def test_sync_global_db_returns_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            library_root = Path(tmp_dir) / "projects"
            settings_path = Path(tmp_dir) / "settings.json"
            store = SettingsStore(settings_path)
            store.save(UserSettings(library_root=str(library_root)))
            service = OrchestratorService(settings_store=store)
            service.create_project("Sync Test", {"fixed_params": {"Length": 100}, "limits": {}})
            summary = service.sync_global_db(max_items_per_project=10)
            self.assertIn("processed", summary)
            self.assertIn("synced", summary)
            self.assertIn("failed", summary)

    def test_list_versions_reads_sql_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            library_root = Path(tmp_dir) / "projects"
            settings_path = Path(tmp_dir) / "settings.json"
            store = SettingsStore(settings_path)
            store.save(UserSettings(library_root=str(library_root)))
            service = OrchestratorService(settings_store=store)
            project = service.create_project("List Versions", {"fixed_params": {"Length": 100}, "limits": {}})
            batch = service.create_batch(
                project_id=project.project_id,
                batch_name="B1",
                selected_params={"Throat.Diameter": 30.0},
                sweeps={},
                sweep_mode="single",
                sim_export_params={},
            )
            rows = service.list_versions(project.project_id)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["batch_id"], batch.batch_id)
            self.assertEqual(rows[0]["version_id"], batch.version_ids[0])

    def test_run_batch_auto_uses_dry_run_when_tools_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            library_root = Path(tmp_dir) / "projects"
            settings_path = Path(tmp_dir) / "settings.json"
            store = SettingsStore(settings_path)
            store.save(UserSettings(library_root=str(library_root)))
            service = OrchestratorService(settings_store=store)
            project = service.create_project("Dry run project", {"fixed_params": {"Length": 100}, "limits": {}})
            batch_summary = service.create_batch(
                project_id=project.project_id,
                batch_name="Dry run batch",
                selected_params={"Throat.Diameter": 30.0},
                sweeps={},
                sweep_mode="single",
                sim_export_params={},
            )
            summary = service.run_batch(project.project_id, batch_summary.batch_id, continue_on_error=True)
            self.assertTrue(summary.dry_run)

    def test_evaluate_batch_definition_missing_project_returns_structured_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            library_root = Path(tmp_dir) / "projects"
            settings_path = Path(tmp_dir) / "settings.json"
            store = SettingsStore(settings_path)
            store.save(UserSettings(library_root=str(library_root)))
            service = OrchestratorService(settings_store=store)
            state = service.evaluate_batch_definition(
                project_id="P404",
                selected_params={"Throat.Diameter": 30.0},
                sweeps={},
                sweep_mode="single",
            )
            self.assertFalse(bool(state.get("project_available", True)))
            issues = [item for item in list(state.get("issues", []) or []) if isinstance(item, dict)]
            self.assertTrue(issues)
            project_missing = [item for item in issues if str(item.get("rule_id")) == "project_missing"]
            self.assertTrue(project_missing)
            self.assertEqual(str(project_missing[0].get("severity", "")), "fatal")
            self.assertIn("Project not found", str(project_missing[0].get("message", "")))

    def test_create_batch_ignores_runner_locked_user_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            library_root = Path(tmp_dir) / "projects"
            settings_path = Path(tmp_dir) / "settings.json"
            store = SettingsStore(settings_path)
            store.save(UserSettings(library_root=str(library_root)))
            service = OrchestratorService(settings_store=store)
            project = service.create_project("Locked fields", {"fixed_params": {"Length": 100}, "limits": {}})
            summary = service.create_batch(
                project_id=project.project_id,
                batch_name="B locked",
                selected_params={"Throat.Diameter": 30.0, "Source.Shape": 4, "LE.Voltage": 9.0},
                sweeps={
                    "Source.Radius": {"start": 1, "end": 2, "steps": 2},
                    "Coverage.Angle": {"start": 60, "end": 70, "steps": 2},
                },
                sweep_mode="single",
                sim_export_params={},
            )
            self.assertGreaterEqual(summary.version_count, 1)
            manifest = service.export_version(
                project_id=project.project_id,
                batch_id=summary.batch_id,
                version_id=summary.version_ids[0],
                export_stl=False,
                export_abec=False,
            )
            cfg = Path(manifest["cfg_path"]).read_text(encoding="utf-8")
            self.assertIn("ABEC.AkabakMode    = 1", cfg)
            self.assertIn("LE.Voltage  = 1.0", cfg)
            self.assertNotIn("Source.Shape", cfg)
            self.assertNotIn("Source.Radius", cfg)
            self.assertNotIn("LE.Voltage         = 9", cfg)


if __name__ == "__main__":
    unittest.main()
