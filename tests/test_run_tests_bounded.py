from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class RunTestsBoundedTests(unittest.TestCase):
    def test_exits_nonzero_when_selected_test_fails(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "tools" / "audit" / "run_tests_bounded.py"
        self.assertTrue(script.exists())

        with tempfile.TemporaryDirectory(prefix="wut_bounded_runner_") as tmp_dir:
            tmp = Path(tmp_dir)
            suite_dir = tmp / "cases"
            suite_dir.mkdir(parents=True, exist_ok=True)
            (suite_dir / "__init__.py").write_text("", encoding="utf-8")
            (suite_dir / "test_stub_fail.py").write_text(
                "\n".join(
                    [
                        "import unittest",
                        "",
                        "class StubFailingTests(unittest.TestCase):",
                        "    def test_expected_failure(self):",
                        "        self.assertEqual(1, 2)",
                        "",
                        "if __name__ == '__main__':",
                        "    unittest.main()",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            audit_dir = tmp / "audit_out"
            env = dict(os.environ)
            existing_pythonpath = env.get("PYTHONPATH")
            env["PYTHONPATH"] = (
                str(tmp)
                if not existing_pythonpath
                else str(tmp) + os.pathsep + existing_pythonpath
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--start-dir",
                    str(suite_dir),
                    "--top-level-dir",
                    str(tmp),
                    "--pattern",
                    "test_stub_fail.py",
                    "--chunk-size",
                    "1",
                    "--timeout-s",
                    "30",
                    "--audit-dir",
                    str(audit_dir),
                ],
                cwd=str(repo_root),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 1, msg=f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}")

            summary_path = audit_dir / "tests_summary.md"
            self.assertTrue(summary_path.exists())
            summary = summary_path.read_text(encoding="utf-8")
            self.assertIn("- observed_failures_total: 1", summary)
            self.assertIn("- observed_errors_total: 0", summary)
            self.assertIn("test_expected_failure", summary)


if __name__ == "__main__":
    unittest.main()
