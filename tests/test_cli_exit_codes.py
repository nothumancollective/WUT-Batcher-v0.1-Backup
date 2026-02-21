from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import cli


class CliExitCodeTests(unittest.TestCase):
    def test_doctor_returns_nonzero_on_fail_status(self) -> None:
        args = argparse.Namespace(
            config=None,
            fix=False,
            kill_zombies=False,
            report_path=None,
        )
        with patch("app.cli.AppConfig.load", return_value=object()):
            with patch("app.doctor_service.run_doctor_checks", return_value={"overall_status": "fail"}):
                with patch("builtins.print"):
                    exit_code = cli.cmd_doctor(args)
        self.assertEqual(exit_code, 3)

    def test_doctor_returns_zero_on_warn_status(self) -> None:
        args = argparse.Namespace(
            config=None,
            fix=False,
            kill_zombies=False,
            report_path=None,
        )
        with patch("app.cli.AppConfig.load", return_value=object()):
            with patch("app.doctor_service.run_doctor_checks", return_value={"overall_status": "warn"}):
                with patch("builtins.print"):
                    exit_code = cli.cmd_doctor(args)
        self.assertEqual(exit_code, 0)

    def test_run_pipeline_returns_nonzero_when_run_status_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_json = Path(tmp_dir) / "project.json"
            batch_json = Path(tmp_dir) / "batch.json"
            project_json.write_text("{}", encoding="utf-8")
            batch_json.write_text("{}", encoding="utf-8")

            args = argparse.Namespace(
                project_json=str(project_json),
                batch_json=str(batch_json),
                projects_root=str(Path(tmp_dir) / "projects"),
                template_cfg=None,
                ath_exe=None,
                akabak_exe=None,
                vacs_exe=None,
                continue_on_error=False,
                dry_run=True,
            )
            with patch("app.cli._read_json", return_value={}):
                with patch("app.cli.Project.from_dict", return_value=object()):
                    with patch("app.cli.Batch.from_dict", return_value=object()):
                        with patch("app.runtime_orchestrator.run_batch_pipeline", return_value={"run_status": "failed"}):
                            with patch("builtins.print"):
                                exit_code = cli.cmd_run_pipeline(args)
        self.assertEqual(exit_code, 3)

    def test_run_pipeline_returns_zero_when_run_status_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_json = Path(tmp_dir) / "project.json"
            batch_json = Path(tmp_dir) / "batch.json"
            project_json.write_text("{}", encoding="utf-8")
            batch_json.write_text("{}", encoding="utf-8")

            args = argparse.Namespace(
                project_json=str(project_json),
                batch_json=str(batch_json),
                projects_root=str(Path(tmp_dir) / "projects"),
                template_cfg=None,
                ath_exe=None,
                akabak_exe=None,
                vacs_exe=None,
                continue_on_error=False,
                dry_run=True,
            )
            with patch("app.cli._read_json", return_value={}):
                with patch("app.cli.Project.from_dict", return_value=object()):
                    with patch("app.cli.Batch.from_dict", return_value=object()):
                        with patch("app.runtime_orchestrator.run_batch_pipeline", return_value={"run_status": "success"}):
                            with patch("builtins.print"):
                                exit_code = cli.cmd_run_pipeline(args)
        self.assertEqual(exit_code, 0)

    def test_projectpage_ath_experiment_returns_nonzero_on_ath_error_status(self) -> None:
        args = argparse.Namespace(
            aggregate_run_groups=None,
            cases=1,
            seed=20260220,
            run_group="test_group",
            ath_exe="C:\\Tools\\ATH\\ath.exe",
            template_cfg="runner_test_cases/templates/smoke_fast_min.cfg",
            cfg_dir="cleanup/runtime/test_cfg",
            export_root="cleanup/runtime/test_export",
            reports_root="cleanup/runtime/test_reports",
            cleanup_files=False,
            max_dim_mm=2000.0,
            hard_cap_mm=5000.0,
            priors_path=None,
            commit_every=100,
            preclean_files=False,
            cleanup_cases="never",
            cleanup_log="never",
            backfill_legacy_null_run_groups=False,
            write_history_snapshots=False,
        )
        with patch("app.cli.SettingsStore.load", return_value=object()):
            with patch(
                "app.projectpage_ath_experiment.run_projectpage_ath_experiment",
                return_value={
                    "status_counts": {"ok": 0, "ath_error": 1, "pipeline_error": 0, "skipped": 0},
                    "run_status_counts": {"ath_error": 1},
                    "reports_preview": [{"status": "ath_error", "ath_result": {"exit_code": -1, "timed_out": True}}],
                },
            ):
                with patch("builtins.print"):
                    exit_code = cli.cmd_projectpage_ath_experiment(args)
        self.assertEqual(exit_code, 3)


if __name__ == "__main__":
    unittest.main()
