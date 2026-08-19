from __future__ import annotations

import unittest

from mflab_knowledge.structure import (
    build_repository_structures,
    structure_source,
)


class StructureTests(unittest.TestCase):
    def test_builds_deterministic_acl_scoped_structure(self) -> None:
        rows = [
            {
                "repository_id": "solver-a1",
                "project": "Solver",
                "path": "README.md",
                "format": "markdown",
                "size_bytes": 100,
                "access_class": "public",
                "commit_sha": "a" * 40,
                "canonical": True,
                "requested_ref": "origin/trunk",
                "chunk_count": 2,
                "anchor_chunk_id": "readme-1",
                "anchor_chunk_hash": "sha256:readme",
                "anchor_title": "Solver",
                "anchor_line_start": 1,
                "anchor_line_end": 5,
                "anchor_text": "Repository overview.",
            },
            {
                "repository_id": "solver-a1",
                "project": "Solver",
                "path": "src/core.cpp",
                "format": "cpp",
                "size_bytes": 250,
                "access_class": "lab",
                "commit_sha": "a" * 40,
                "canonical": True,
                "requested_ref": "origin/trunk",
                "chunk_count": 4,
                "anchor_chunk_id": None,
            },
        ]

        first = build_repository_structures(
            rows,
            requested_project="Solver",
            requested_branch="trunk",
            allowed_access={"public", "lab"},
        )
        second = build_repository_structures(
            reversed(rows),
            requested_project="Solver",
            requested_branch="trunk",
            allowed_access={"public", "lab"},
        )

        self.assertEqual(first[0]["fingerprint"], second[0]["fingerprint"])
        self.assertEqual(first[0]["documents"], 2)
        self.assertEqual(first[0]["chunks"], 6)
        self.assertEqual(first[0]["access_class"], "lab")
        self.assertEqual(
            {item["name"] for item in first[0]["top_level"]},
            {"README.md", "src"},
        )
        self.assertEqual(first[0]["anchors"][0]["path"], "README.md")

    def test_derived_source_states_its_evidentiary_limit(self) -> None:
        structure = build_repository_structures(
            [
                {
                    "repository_id": "solver-a1",
                    "project": "Solver",
                    "path": "src/main.f90",
                    "format": "fortran",
                    "size_bytes": 50,
                    "access_class": "lab",
                    "commit_sha": "b" * 40,
                    "canonical": False,
                    "requested_ref": "work",
                    "chunk_count": 1,
                    "anchor_chunk_id": None,
                }
            ],
            requested_project="Solver",
            requested_branch="work",
            allowed_access={"lab"},
        )[0]

        source = structure_source(structure)

        self.assertEqual(source["source_kind"], "derived_structure")
        self.assertIn("does not establish scientific purpose", source["text"])
        self.assertEqual(source["selected_occurrence"]["branch"], "work")
        self.assertEqual(
            source["derivation"]["fingerprint"], structure["fingerprint"]
        )


if __name__ == "__main__":
    unittest.main()
