from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.legacy.compat_rules import dump_compatibility_rules, load_compatibility_rules


class CompatRulesTests(unittest.TestCase):
    def test_rules_include_required_machine_readable_fields(self) -> None:
        rules = load_compatibility_rules()
        self.assertGreater(len(rules), 0)
        sample = rules[0].to_dict()
        for key in (
            "rule_id",
            "description",
            "scope",
            "condition",
            "action",
            "severity",
            "kind",
            "applies_to",
            "evidence",
        ):
            self.assertIn(key, sample)
        self.assertIsInstance(sample["evidence"], dict)
        self.assertIn("type", sample["evidence"])
        self.assertIn("refs", sample["evidence"])
        self.assertIn("confidence", sample["evidence"])
        self.assertIn("notes", sample["evidence"])

    def test_dump_compatibility_rules_writes_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "compat_rules.json"
            dump_compatibility_rules(output)
            self.assertTrue(output.exists())
            text = output.read_text(encoding="utf-8")
            self.assertIn("\"rules\"", text)
            self.assertIn("\"semantic_facts\"", text)
            self.assertIn("\"schema_version\": \"1.1\"", text)


if __name__ == "__main__":
    unittest.main()
