from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mflab_knowledge.inventory import build_inventory, write_yaml


class InventoryTests(unittest.TestCase):
    def test_discovers_source_and_excludes_unsafe_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "src").mkdir()
            (root / "src" / "solver.cpp").write_text(
                "int solver() { return 0; }\n", encoding="utf-8"
            )
            (root / "build").mkdir()
            (root / "build" / "solver.o").write_bytes(b"object")
            (root / "docs" / "html").mkdir(parents=True)
            (root / "docs" / "html" / "index.html").write_text(
                "generated", encoding="utf-8"
            )
            (root / ".env").write_text("TOKEN=do-not-read\n", encoding="utf-8")

            inventory = build_inventory(root, "MFSim-NG", "lab")

            included_paths = {item["path"] for item in inventory["included"]}
            exclusions = {
                item["path"]: item["reason"] for item in inventory["excluded"]
            }

            self.assertEqual(included_paths, {"src/solver.cpp"})
            self.assertEqual(inventory["summary"]["formats"], {"cpp": 1})
            self.assertEqual(exclusions["build"], "excluded_directory")
            self.assertEqual(exclusions["docs/html/index.html"], "generated_documentation")
            self.assertEqual(exclusions[".env"], "possible_secret")

    def test_writes_generated_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            source.mkdir()
            (source / "README.md").write_text("# Teste\n", encoding="utf-8")
            destination = root / "inventory" / "test.generated.yaml"

            inventory = build_inventory(source, "MFSim-NG", "lab")
            write_yaml(inventory, destination)

            content = destination.read_text(encoding="utf-8")
            self.assertIn('project: "MFSim-NG"', content)
            self.assertIn('path: "README.md"', content)
            self.assertIn("snapshot_hash:", content)


if __name__ == "__main__":
    unittest.main()

