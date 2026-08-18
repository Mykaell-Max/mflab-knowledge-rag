from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mflab_knowledge.inventory_policy import load_inventory_policy


class InventoryPolicyTests(unittest.TestCase):
    def test_generic_policy_is_project_agnostic(self) -> None:
        first = load_inventory_policy("generic")
        legacy_auto = load_inventory_policy("auto")

        self.assertEqual(first, legacy_auto)
        self.assertIsNone(first.exclusion_reason("any/project/source.cpp"))

    def test_loads_named_globs_and_hash_changes_with_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "policies.toml"
            path.write_text(
                """schema_version = "0.1"
[profiles.focused]
include_paths = ["source/**", "docs/*.md"]
exclude_paths = ["source/vendor/**"]
""",
                encoding="utf-8",
            )
            first = load_inventory_policy("focused", path)

            self.assertIsNone(first.exclusion_reason("source/core/solver.cpp"))
            self.assertEqual(
                first.exclusion_reason("source/vendor/dependency.cpp"),
                "profile_excluded",
            )
            self.assertEqual(
                first.exclusion_reason("tests/case.json"),
                "outside_profile_scope",
            )

            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    '"docs/*.md"',
                    '"examples/**"',
                ),
                encoding="utf-8",
            )
            changed = load_inventory_policy("focused", path)
            self.assertNotEqual(first.policy_hash, changed.policy_hash)

    def test_rejects_unknown_profiles_and_unsafe_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "policies.toml"
            path.write_text(
                """schema_version = "0.1"
[profiles.focused]
include_paths = ["../outside/**"]
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "inseguro"):
                load_inventory_policy("focused", path)
            with self.assertRaisesRegex(ValueError, "não configurado"):
                load_inventory_policy("missing", path)


if __name__ == "__main__":
    unittest.main()
