from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mflab_knowledge.semantic_map import build_semantic_map


def _document(
    identifier: str,
    path: str,
    file_format: str,
    *,
    access_class: str = "lab",
    commit: str = "a" * 40,
) -> dict[str, object]:
    return {
        "document_id": identifier,
        "repository_id": "solver-stable",
        "project": "Solver",
        "path": path,
        "format": file_format,
        "access_class": access_class,
        "occurrences": [
            {
                "branch": "trunk",
                "commit_sha": commit,
                "canonical": True,
            }
        ],
    }


def _chunk(
    identifier: str,
    document_id: str,
    *,
    title: str,
    kind: str,
    line_start: int,
    line_end: int,
    text: str,
) -> dict[str, object]:
    return {
        "chunk_id": identifier,
        "document_id": document_id,
        "title": title,
        "kind": kind,
        "line_start": line_start,
        "line_end": line_end,
        "text": text,
    }


class SemanticMapTests(unittest.TestCase):
    def test_extracts_symbols_references_and_companions_with_provenance(self) -> None:
        documents = [
            _document("source", "src/model.cpp", "cpp"),
            _document("header", "src/model.hpp", "cpp_header"),
            _document("module", "src/numerics.f90", "fortran"),
            _document("script", "tools/report.py", "python"),
        ]
        chunks = [
            _chunk(
                "source-symbol",
                "source",
                title="Model::advance",
                kind="function",
                line_start=3,
                line_end=20,
                text='#include "model.hpp"\nvoid Model::advance() {}',
            ),
            _chunk(
                "source-continuation",
                "source",
                title="Model::advance",
                kind="function",
                line_start=16,
                line_end=28,
                text="return;",
            ),
            _chunk(
                "header-symbol",
                "header",
                title="Model",
                kind="type",
                line_start=2,
                line_end=10,
                text="class Model {};",
            ),
            _chunk(
                "fortran-symbol",
                "module",
                title="numerics",
                kind="module",
                line_start=1,
                line_end=8,
                text="module numerics\n  use iso_fortran_env\nend module",
            ),
            _chunk(
                "python-file",
                "script",
                title="arquivo",
                kind="file",
                line_start=1,
                line_end=3,
                text="from pathlib import Path\nimport json, re",
            ),
        ]

        with tempfile.TemporaryDirectory() as temporary:
            first = build_semantic_map(
                documents=documents,
                chunks=chunks,
                output_dir=Path(temporary),
            )
            symbols = [
                json.loads(line)
                for line in Path(str(first["symbols"])).read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            relations = [
                json.loads(line)
                for line in Path(str(first["relations"])).read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            second = build_semantic_map(
                documents=documents,
                chunks=chunks,
                output_dir=Path(temporary),
            )

        self.assertEqual(len(symbols), 3)
        self.assertEqual(
            [item["qualified_name"] for item in symbols],
            ["Model::advance", "Model", "numerics"],
        )
        self.assertEqual(
            {item["kind"] for item in relations},
            {"companion", "imports_module", "includes", "uses_module"},
        )
        include = next(item for item in relations if item["kind"] == "includes")
        self.assertEqual(include["target_document_id"], "header")
        self.assertEqual(include["occurrences"][0]["branch"], "trunk")
        self.assertEqual(first["fingerprint"], second["fingerprint"])

    def test_does_not_pair_companions_from_different_commits(self) -> None:
        documents = [
            _document("source", "src/model.cpp", "cpp", commit="a" * 40),
            _document("header", "src/model.hpp", "cpp_header", commit="b" * 40),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            result = build_semantic_map(
                documents=documents,
                chunks=[],
                output_dir=Path(temporary),
            )
            relations = Path(str(result["relations"])).read_text(
                encoding="utf-8"
            )

        self.assertEqual(relations, "")


if __name__ == "__main__":
    unittest.main()
