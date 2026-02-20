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


if __name__ == "__main__":
    unittest.main()
