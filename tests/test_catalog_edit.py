from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mflab_knowledge.catalog_edit import configure_repository_routing
from mflab_knowledge.repository_config import load_repository_catalog


CATALOG = '''schema_version = "0.1"

[[repositories]]
id = "solver-a"
enabled = true
project = "Solver A"
remote_url = "https://code.example/solver-a.git"
canonical_ref = "remote_default"
branch_scope = "remote"
access_class = "lab"
profile = "generic"

# This record must remain untouched.
[[repositories]]
id = "solver-b"
enabled = true
project = "Solver B"
aliases = ["old-name"]
remote_url = "https://code.example/solver-b.git"
canonical_ref = "remote_default"
preferred_branch = "trunk"
branch_scope = "remote"
access_class = "lab"
profile = "generic"
'''


class CatalogEditTests(unittest.TestCase):
    def test_adds_routing_without_rebuilding_other_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "repositories.toml"
            path.write_text(CATALOG, encoding="utf-8")

            result = configure_repository_routing(
                path,
                repository_id="solver-a",
                preferred_branch="integration",
                aliases=("a", "solver a"),
            )

            catalog = load_repository_catalog(path)
            solver_a, solver_b = catalog.repositories
            text = path.read_text(encoding="utf-8")

        self.assertEqual(result["preferred_branch"], "integration")
        self.assertEqual(solver_a.aliases, ("a", "solver a"))
        self.assertEqual(solver_b.preferred_branch, "trunk")
        self.assertEqual(solver_b.aliases, ("old-name",))
        self.assertIn("# This record must remain untouched.", text)

    def test_merges_aliases_and_replaces_existing_preference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "repositories.toml"
            path.write_text(CATALOG, encoding="utf-8")

            configure_repository_routing(
                path,
                repository_id="solver-b",
                preferred_branch="develop",
                aliases=("OLD-NAME", "b"),
            )
            definition = load_repository_catalog(path).repositories[1]

        self.assertEqual(definition.preferred_branch, "develop")
        self.assertEqual(definition.aliases, ("old-name", "b"))

    def test_rejects_unknown_repository_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "repositories.toml"
            path.write_text(CATALOG, encoding="utf-8")
            before = path.read_bytes()

            with self.assertRaisesRegex(ValueError, "desconhecido"):
                configure_repository_routing(
                    path,
                    repository_id="missing",
                    preferred_branch="main",
                )

            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
