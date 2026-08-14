from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mflab_knowledge.repository_config import load_repository_catalog


class RepositoryConfigTests(unittest.TestCase):
    def test_loads_distinct_branch_policies_without_name_assumptions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "repositories.toml"
            config.write_text(
                """schema_version = "0.1"
[defaults]
branch_scope = "remote"
access_class = "lab"
profile = "generic"
cache_root = "state/cache"
inventory_root = "state/inventory"
normalized_root = "state/data"

[[repositories]]
id = "solver-next"
project = "Solver Next"
source = "sources/next"
canonical_ref = "origin/trunk"
include_branches = ["trunk", "research/*"]
exclude_branches = ["research/retired-*" ]

[[repositories]]
id = "desktop-ui"
project = "Desktop UI"
source = "sources/ui"
canonical_ref = "origin/stable/qt6"
branch_scope = "all"
access_class = "project"

[[repositories]]
id = "legacy"
enabled = false
project = "Legacy"
source = "sources/legacy"
canonical_ref = "origin/release-2019"
access_class = "pending"
""",
                encoding="utf-8",
            )

            catalog = load_repository_catalog(config)

            self.assertEqual(len(catalog.repositories), 3)
            self.assertEqual([item.id for item in catalog.enabled], ["solver-next", "desktop-ui"])
            solver, ui, legacy = catalog.repositories
            self.assertEqual(solver.canonical_ref, "origin/trunk")
            self.assertEqual(solver.include_branches, ("trunk", "research/*"))
            self.assertEqual(solver.exclude_branches, ("research/retired-*",))
            self.assertEqual(ui.canonical_ref, "origin/stable/qt6")
            self.assertEqual(ui.branch_scope, "all")
            self.assertEqual(ui.access_class, "project")
            self.assertFalse(legacy.enabled)
            self.assertEqual(catalog.cache_root, (root / "state/cache").resolve())
            self.assertTrue(catalog.config_hash.startswith("sha256:"))

    def test_rejects_enabled_pending_and_duplicate_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pending = root / "pending.toml"
            pending.write_text(
                """schema_version = "0.1"
[[repositories]]
id = "pending"
project = "Pending"
source = "source"
canonical_ref = "origin/main"
access_class = "pending"
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "habilitado com acesso pending"):
                load_repository_catalog(pending)

            duplicate = root / "duplicate.toml"
            duplicate.write_text(
                """schema_version = "0.1"
[[repositories]]
id = "same"
project = "First"
source = "first"
canonical_ref = "origin/main"
[[repositories]]
id = "same"
project = "Second"
source = "second"
canonical_ref = "origin/develop"
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicado"):
                load_repository_catalog(duplicate)

    def test_rejects_unknown_options_instead_of_ignoring_typos(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "repositories.toml"
            config.write_text(
                """schema_version = "0.1"
unexpected = true
[[repositories]]
id = "solver"
project = "Solver"
source = "source"
canonical_ref = "origin/trunk"
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "desconhecidas"):
                load_repository_catalog(config)


if __name__ == "__main__":
    unittest.main()
