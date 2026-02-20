from __future__ import annotations

from pathlib import Path
import csv
import io
import sys
import subprocess
import tempfile
import unittest

from app.runners import AthRunner, parse_ath_dimensions


class RunnerTests(unittest.TestCase):
    def test_ath_runner_logs_and_dimension_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            logs_dir = Path(tmp_dir) / "logs"
            cfg_path = Path(tmp_dir) / "input.cfg"
            cfg_path.write_text("; cfg\n", encoding="utf-8")

            runner = AthRunner(
                executable=sys.executable,
                base_args=["-c", "print('Length=320.5 Width=280.1 Height=140.0')"],
            )
            result = runner.run_cfg(cfg_path, version_logs_dir=logs_dir)
            self.assertTrue(result.ok)
            self.assertTrue(Path(result.stdout_log).exists())
            self.assertTrue(Path(result.stderr_log).exists())

            stdout_text = Path(result.stdout_log).read_text(encoding="utf-8")
            parsed = parse_ath_dimensions(stdout_text)
            self.assertEqual(parsed.horn_length_mm, 320.5)
            self.assertEqual(parsed.horn_width_mm, 280.1)
            self.assertEqual(parsed.horn_height_mm, 140.0)
            self.assertIn("Length=320.5", parsed.raw_line)

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows-only process-tree timeout test")
    def test_timeout_kills_process_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            logs_dir = Path(tmp_dir) / "logs"
            launcher = (
                "import subprocess,sys,time;"
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
                "print(child.pid, flush=True);"
                "time.sleep(60)"
            )
            runner = AthRunner(executable=sys.executable, base_args=["-c", launcher])
            result = runner.run(
                [],
                version_logs_dir=logs_dir,
                timeout_s=1,
                retries=1,
                log_prefix="ath_timeout_tree",
            )
            self.assertTrue(result.timed_out)
            stdout_text = Path(result.stdout_log).read_text(encoding="utf-8", errors="replace")
            child_pid = None
            for raw_line in stdout_text.splitlines():
                value = str(raw_line).strip()
                if value.isdigit():
                    child_pid = int(value)
                    break
            self.assertIsNotNone(child_pid)
            assert child_pid is not None
            self.assertFalse(_windows_pid_exists(child_pid))

def _windows_pid_exists(pid: int) -> bool:
    cp = subprocess.run(
        ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    rows = list(csv.reader(io.StringIO(str(cp.stdout or ""))))
    for row in rows:
        if len(row) < 2:
            continue
        raw_pid = str(row[1] or "").strip().strip('"')
        if raw_pid.isdigit() and int(raw_pid) == int(pid):
            return True
    return False


if __name__ == "__main__":
    unittest.main()
