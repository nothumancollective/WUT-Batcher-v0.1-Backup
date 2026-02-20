from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import app.audit_mode as audit_mode


class AuditModeTests(unittest.TestCase):
    _ENV_KEYS = ("AUDIT_MODE", "AUDIT_SCENARIO", "AUDIT_TRACE_DIR", "AUDIT_TRACE_FILE")

    def setUp(self) -> None:
        self._saved_env = {key: os.environ.get(key) for key in self._ENV_KEYS}
        for key in self._ENV_KEYS:
            os.environ.pop(key, None)
        audit_mode._reset_for_tests()

    def tearDown(self) -> None:
        audit_mode._reset_for_tests()
        for key in self._ENV_KEYS:
            old = self._saved_env.get(key)
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

    def test_disabled_mode_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["AUDIT_MODE"] = "0"
            os.environ["AUDIT_TRACE_DIR"] = str(Path(tmp_dir) / "traces")

            original_connect = sqlite3.connect
            original_run = subprocess.run

            enabled = audit_mode.enable_audit_mode(entrypoint="tests.disabled")
            self.assertFalse(enabled)
            self.assertIs(sqlite3.connect, original_connect)
            self.assertIs(subprocess.run, original_run)
            self.assertFalse((Path(tmp_dir) / "traces").exists())

    def test_enabled_mode_writes_trace_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_root = Path(tmp_dir) / "traces"
            os.environ["AUDIT_MODE"] = "1"
            os.environ["AUDIT_SCENARIO"] = "S_TEST_AUDIT"
            os.environ["AUDIT_TRACE_DIR"] = str(trace_root)

            enabled = audit_mode.enable_audit_mode(entrypoint="tests.enabled")
            self.assertTrue(enabled)

            with sqlite3.connect(":memory:") as conn:
                conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, value TEXT)")
                conn.execute("INSERT INTO t(value) VALUES (?)", ("x",))
                rows = conn.execute("SELECT value FROM t").fetchall()
            self.assertEqual(rows[0][0], "x")

            run = subprocess.run(
                [sys.executable, "-c", "print('audit-mode-test')"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(run.returncode, 0)

            audit_mode.flush_audit_mode()
            audit_mode._reset_for_tests()

            scenario_dir = trace_root / "S_TEST_AUDIT"
            jsonl_files = sorted(scenario_dir.glob("*.jsonl"))
            self.assertEqual(len(jsonl_files), 1)
            base = jsonl_files[0].with_suffix("")
            coverage_file = Path(str(base) + ".coverage.json")
            summary_file = Path(str(base) + ".summary.json")
            self.assertTrue(coverage_file.exists())
            self.assertTrue(summary_file.exists())

            events = []
            for line in jsonl_files[0].read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
            event_types = {str(item.get("event")) for item in events}
            self.assertIn("process_start", event_types)
            self.assertIn("sqlite_connect", event_types)
            self.assertIn("sqlite_query", event_types)
            self.assertIn("subprocess_run", event_types)

            coverage_payload = json.loads(coverage_file.read_text(encoding="utf-8"))
            self.assertIn("module_call_counts", coverage_payload)
            self.assertIn("function_call_counts", coverage_payload)

            summary_payload = json.loads(summary_file.read_text(encoding="utf-8"))
            self.assertGreaterEqual(int(summary_payload.get("event_count", 0)), 1)

    def test_subprocess_popen_events_are_emitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_root = Path(tmp_dir) / "traces"
            os.environ["AUDIT_MODE"] = "1"
            os.environ["AUDIT_SCENARIO"] = "S_TEST_POPEN"
            os.environ["AUDIT_TRACE_DIR"] = str(trace_root)

            self.assertTrue(audit_mode.enable_audit_mode(entrypoint="tests.popen"))
            proc = subprocess.Popen(
                [sys.executable, "-c", "print('popen')"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = proc.communicate(timeout=10)
            self.assertIn("popen", stdout)
            self.assertEqual(stderr, "")
            self.assertEqual(proc.returncode, 0)

            audit_mode.flush_audit_mode()
            audit_mode._reset_for_tests()

            scenario_dir = trace_root / "S_TEST_POPEN"
            jsonl_files = sorted(scenario_dir.glob("*.jsonl"))
            self.assertEqual(len(jsonl_files), 1)
            event_types = {
                str(json.loads(line).get("event"))
                for line in jsonl_files[0].read_text(encoding="utf-8").splitlines()
                if line.strip()
            }
            self.assertIn("subprocess_popen_start", event_types)
            self.assertIn("subprocess_popen_end", event_types)


if __name__ == "__main__":
    unittest.main()

