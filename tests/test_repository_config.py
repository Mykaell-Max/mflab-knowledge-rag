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
fetch_timeout_seconds = 2400

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
remote_url = "https://gitlab.example.invalid/tools/desktop-ui.git"
canonical_ref = "remote_default"
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
            self.assertEqual(ui.canonical_ref, "remote_default")
            self.assertEqual(ui.branch_scope, "remote")
            self.assertEqual(ui.access_class, "project")
            self.assertIsNone(ui.source)
            self.assertEqual(
                ui.remote_url,
                "https://gitlab.example.invalid/tools/desktop-ui.git",
            )
            self.assertEqual(ui.source_kind, "remote_url")
            self.assertEqual(ui.fetch_timeout_seconds, 2400)
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

    def test_requires_one_safe_source_kind(self) -> None:
        cases = {
            "missing": "",
            "both": (
                'source = "source"\n'
                'remote_url = "https://gitlab.example.invalid/group/repo.git"\n'
            ),
            "credentials": (
                'remote_url = "https://user:token@gitlab.example.invalid/repo.git"\n'
            ),
            "local_scope": (
                'remote_url = "ssh://git@gitlab.example.invalid/group/repo.git"\n'
                'branch_scope = "local"\n'
            ),
            "local_default": (
                'source = "source"\n'
                'canonical_ref = "remote_default"\n'
            ),
            "bad_timeout": (
                'source = "source"\n'
                'fetch_timeout_seconds = 10\n'
            ),
        }
        expected = {
            "missing": "exatamente uma origem",
            "both": "exatamente uma origem",
            "credentials": "não pode conter credenciais",
            "local_scope": "local exige uma origem source",
            "local_default": "remote_default exige remote_url",
            "bad_timeout": "entre 30 e 86400",
        }
        for name, source_options in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                config = Path(temporary) / "repositories.toml"
                config.write_text(
                    "schema_version = \"0.1\"\n"
                    "[[repositories]]\n"
                    "id = \"repo\"\n"
                    "project = \"Repo\"\n"
                    + (
                        "canonical_ref = \"origin/trunk\"\n"
                        if name != "local_default"
                        else ""
                    )
                    + source_options,
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, expected[name]):
                    load_repository_catalog(config)


if __name__ == "__main__":
    unittest.main()
