from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.legacy.ui_risk_layer import UiRiskLayer


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class UiRiskLayerTests(unittest.TestCase):
    def test_missing_files_disables_layer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            layer = UiRiskLayer(
                range_path=root / "missing_range.json",
                candidates_path=root / "missing_candidates.json",
            )
            self.assertFalse(layer.enabled)
            issues = layer.evaluate({"fixed_params": {}, "limits": {}, "param_states": []})
            self.assertEqual(issues, [])

    def test_range_warn_and_fatal_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            range_path = root / "range.json"
            candidates_path = root / "candidates.json"
            _write_json(
                range_path,
                {
                    "per_key": {
                        "Length": {
                            "safe_min": 100.0,
                            "safe_max": 1200.0,
                            "rec_p05": 200.0,
                            "rec_p95": 1000.0,
                            "notes": "test",
                        }
                    }
                },
            )
            _write_json(candidates_path, {"candidates": []})
            layer = UiRiskLayer(range_path=range_path, candidates_path=candidates_path)

            warn_issues = layer.evaluate(
                {
                    "fixed_params": {"Length": 1100},
                    "limits": {},
                    "param_states": [{"param_name": "Length", "is_set": 1, "value": 1100}],
                },
                visible_keys={"Length"},
            )
            self.assertEqual(len(warn_issues), 1)
            self.assertEqual(warn_issues[0].get("severity"), "warn")

            fatal_issues = layer.evaluate(
                {
                    "fixed_params": {"Length": 1400},
                    "limits": {},
                    "param_states": [{"param_name": "Length", "is_set": 1, "value": 1400}],
                },
                visible_keys={"Length"},
            )
            self.assertEqual(len(fatal_issues), 1)
            self.assertEqual(fatal_issues[0].get("severity"), "fatal")

    def test_combo_candidate_triggers_warn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            range_path = root / "range.json"
            candidates_path = root / "candidates.json"
            _write_json(range_path, {"per_key": {}})
            _write_json(
                candidates_path,
                {
                    "candidates": [
                        {
                            "id": "warn_superformula_osse_combo",
                            "kind": "warn",
                            "when": "and(eq('GCurve.Type', 2), eq('Throat.Profile', 1))",
                            "then": "show_warning('combo risk')",
                            "confidence": "high",
                            "evidence": {"refs": {"consistent_multi_group": True}},
                        }
                    ]
                },
            )
            layer = UiRiskLayer(range_path=range_path, candidates_path=candidates_path)

            issues = layer.evaluate(
                {
                    "fixed_params": {"GCurve.Type": 2, "Throat.Profile": 1},
                    "limits": {},
                    "param_states": [
                        {"param_name": "GCurve.Type", "is_set": 1, "value": 2},
                        {"param_name": "Throat.Profile", "is_set": 1, "value": 1},
                    ],
                },
                visible_keys={"GCurve.Type", "Throat.Profile"},
            )
            keys = sorted(str(item.get("field_key")) for item in issues)
            self.assertEqual(keys, ["GCurve.Type", "Throat.Profile"])
            self.assertTrue(all(str(item.get("severity")) == "warn" for item in issues))

    def test_in_range_emits_ok_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            range_path = root / "range.json"
            candidates_path = root / "candidates.json"
            _write_json(
                range_path,
                {
                    "per_key": {
                        "Length": {
                            "safe_min": 100.0,
                            "safe_max": 1200.0,
                            "rec_p05": 200.0,
                            "rec_p95": 1000.0,
                            "notes": "test",
                        }
                    }
                },
            )
            _write_json(candidates_path, {"candidates": []})
            layer = UiRiskLayer(range_path=range_path, candidates_path=candidates_path)
            issues = layer.evaluate(
                {
                    "fixed_params": {"Length": 600},
                    "limits": {},
                    "param_states": [{"param_name": "Length", "is_set": 1, "value": 600}],
                },
                visible_keys={"Length"},
            )
            self.assertEqual(len(issues), 1)
            self.assertEqual(str(issues[0].get("severity")), "ok")


if __name__ == "__main__":
    unittest.main()
