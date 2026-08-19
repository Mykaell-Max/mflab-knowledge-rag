from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from mflab_knowledge import database


def _write_jsonl(path: Path, values: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(value) + "\n" for value in values),
        encoding="utf-8",
    )


def _document() -> dict[str, object]:
    return {
        "document_id": "document-1",
        "repository_id": "mfsim-ng-123",
        "project": "MFSim-NG",
        "remote_url": "https://gitlab.example/mfsim-ng.git",
        "path": "src/dpm.cpp",
        "format": "cpp",
        "size_bytes": 42,
        "content_hash": "sha256:document",
        "access_class": "lab",
        "encoding": "utf-8",
        "parser_version": "parser-1",
        "occurrences": [
            {
                "branch": "master",
                "commit_sha": "a" * 40,
                "canonical": True,
                "requested_ref": "origin/master",
            }
        ],
    }


def _chunk(document_id: str = "document-1") -> dict[str, object]:
    return {
        "chunk_id": "chunk-1",
        "document_id": document_id,
        "title": "DPMManager",
        "kind": "symbol",
        "line_start": 10,
        "line_end": 20,
        "text": "class DPMManager {};",
        "chunk_hash": "sha256:chunk",
        "embedding_key": "sha256:chunk",
        "parser_version": "parser-1",
    }


class _Result:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows


class _Connection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.sql = ""
        self.parameters: dict[str, object] = {}

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, parameters: dict[str, object]) -> _Result:
        self.sql = sql
        self.parameters = parameters
        return _Result(self.rows)


class DatabaseTests(unittest.TestCase):
    def test_schema_preserves_acl_occurrences_and_uses_gin(self) -> None:
        schema = database._schema_sql()
        self.assertIn("document_occurrences", schema)
        self.assertIn("access_class IN", schema)
        self.assertIn("GENERATED ALWAYS AS", schema)
        self.assertIn("USING GIN (search_vector)", schema)

    def test_prepares_single_repository_and_rejects_broken_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            documents_path = root / "documents.jsonl"
            chunks_path = root / "chunks.jsonl"
            _write_jsonl(documents_path, [_document()])
            _write_jsonl(chunks_path, [_chunk()])

            corpus = database._prepare_corpus(documents_path, chunks_path)
            self.assertEqual(corpus["repository_id"], "mfsim-ng-123")
            self.assertEqual(corpus["project"], "MFSim-NG")
            self.assertTrue(str(corpus["documents_hash"]).startswith("sha256:"))

            _write_jsonl(chunks_path, [_chunk("missing")])
            with self.assertRaisesRegex(ValueError, "documento ausente"):
                database._prepare_corpus(documents_path, chunks_path)

    def test_postgres_search_filters_acl_before_returning_text(self) -> None:
        connection = _Connection(
            [
                {
                    "score": 12.25,
                    "chunk_id": "chunk-1",
                    "chunk_hash": "sha256:chunk",
                    "project": "MFSim-NG",
                    "path": "src/dpm.cpp",
                    "format": "cpp",
                    "title": "DPMManager",
                    "line_start": 10,
                    "line_end": 20,
                    "access_class": "lab",
                    "branch": "master",
                    "commit_sha": "a" * 40,
                    "occurrences": [{"branch": "master"}],
                    "text": "class DPMManager {};",
                }
            ]
        )
        with mock.patch.object(database, "_driver", return_value=(object(), object())):
            with mock.patch.object(database, "_connect", return_value=connection):
                results = database.search_postgres(
                    "postgresql://not-logged",
                    query="DPMManager",
                    branch="master",
                    project="MFSim-NG",
                    allowed_access={"lab"},
                )

        self.assertIn(
            "d.access_class = ANY(%(allowed_access)s::text[])", connection.sql
        )
        self.assertIn("%(branch)s::text IS NULL", connection.sql)
        self.assertIn("%(project)s::text IS NULL", connection.sql)
        self.assertIn("%(path_prefix)s::text IS NULL", connection.sql)
        self.assertEqual(connection.parameters["allowed_access"], ["lab"])
        self.assertEqual(results[0]["citation"], (
            "MFSim-NG master@aaaaaaaaaaaa src/dpm.cpp:L10-L20"
        ))
        self.assertEqual(
            results[0]["selected_occurrence"],
            {"branch": "master", "commit_sha": "a" * 40},
        )
        self.assertEqual(results[0]["format"], "cpp")
        self.assertNotIn("commit_sha", results[0])

    def test_postgres_search_rejects_pending_access(self) -> None:
        with self.assertRaisesRegex(ValueError, "filtro de acesso"):
            database.search_postgres(
                "postgresql://unused",
                query="DPMManager",
                allowed_access={"pending"},
            )

    def test_fetch_chunks_by_id_reapplies_scope_and_acl(self) -> None:
        connection = _Connection(
            [
                {
                    "score": 999.0,
                    "chunk_id": "chunk-1",
                    "chunk_hash": "sha256:chunk",
                    "project": "Solver",
                    "path": "src/mesh.cpp",
                    "format": "cpp",
                    "title": "Mesh::initialize",
                    "line_start": 10,
                    "line_end": 20,
                    "access_class": "lab",
                    "branch": "trunk",
                    "commit_sha": "a" * 40,
                    "occurrences": [{"branch": "trunk"}],
                    "text": "void Mesh::initialize() {}",
                }
            ]
        )
        with mock.patch.object(database, "_driver", return_value=(object(), object())):
            with mock.patch.object(database, "_connect", return_value=connection):
                results = database.fetch_chunks_by_id(
                    "postgresql://not-logged",
                    chunk_ids=["chunk-1", "chunk-1"],
                    project="Solver",
                    branch="trunk",
                    allowed_access={"lab"},
                )

        self.assertIn("document.access_class = ANY", connection.sql)
        self.assertEqual(connection.parameters["chunk_ids"], ["chunk-1"])
        self.assertEqual(connection.parameters["project"], "Solver")
        self.assertEqual(connection.parameters["branch"], "trunk")
        self.assertEqual(results[0]["selected_occurrence"]["branch"], "trunk")

    def test_fetch_chunk_neighborhood_is_bounded_and_acl_scoped(self) -> None:
        connection = _Connection([])
        with mock.patch.object(database, "_driver", return_value=(object(), object())):
            with mock.patch.object(database, "_connect", return_value=connection):
                results = database.fetch_chunk_neighborhood(
                    "postgresql://not-logged",
                    chunk_id="chunk-1",
                    radius=2,
                    project="Solver",
                    branch="trunk",
                    allowed_access={"lab"},
                )

        self.assertEqual(results, [])
        self.assertIn("document.access_class = ANY", connection.sql)
        self.assertIn("occurrence.branch = %(branch)s::text", connection.sql)
        self.assertEqual(connection.parameters["neighbor_limit"], 5)
        self.assertEqual(connection.parameters["allowed_access"], ["lab"])

        with self.assertRaisesRegex(ValueError, "radius"):
            database.fetch_chunk_neighborhood(
                "postgresql://unused",
                chunk_id="chunk-1",
                radius=6,
            )

    def test_repository_status_reports_branch_and_embedding_coverage(self) -> None:
        class StatusConnection:
            def __enter__(self) -> StatusConnection:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def execute(
                self,
                sql: str,
                parameters: dict[str, object],
            ) -> _Result:
                self.sql = sql
                self.parameters = parameters
                return _Result(
                    [
                        {
                            "repository_id": "solver-a1",
                            "project": "Solver",
                            "documents": 20,
                            "occurrences": 35,
                            "branches": 4,
                            "branch_names": [
                                "feature/a",
                                "release/1",
                                "research",
                                "trunk",
                            ],
                            "canonical_branches": ["trunk"],
                            "chunks": 100,
                            "embedded_chunks": 75,
                            "last_ingestion": datetime(
                                2026, 8, 17, tzinfo=timezone.utc
                            ),
                        }
                    ]
                )

        connection = StatusConnection()
        with mock.patch.object(database, "_driver", return_value=(object(), object())):
            with mock.patch.object(database, "_connect", return_value=connection):
                results = database.repository_status(
                    "postgresql://not-logged",
                    embedding_profile="profile-1",
                    allowed_access={"public", "lab"},
                )

        self.assertIn("count(DISTINCT occurrence.branch)", connection.sql)
        self.assertIn("access_class = ANY(%(allowed_access)s::text[])", connection.sql)
        self.assertEqual(results[0]["canonical_branches"], ["trunk"])
        self.assertEqual(
            results[0]["branch_names"],
            ["feature/a", "release/1", "research", "trunk"],
        )
        self.assertEqual(results[0]["embedding_coverage"], 0.75)
        self.assertEqual(results[0]["embedding_profile"], "profile-1")
        self.assertEqual(connection.parameters["embedding_profile"], "profile-1")
        self.assertEqual(connection.parameters["allowed_access"], ["lab", "public"])
        self.assertEqual(
            results[0]["last_ingestion"], "2026-08-17T00:00:00+00:00"
        )

    def test_repository_structure_filters_project_branch_and_acl(self) -> None:
        connection = _Connection([])
        with mock.patch.object(database, "_driver", return_value=(object(), object())):
            with mock.patch.object(database, "_connect", return_value=connection):
                results = database.repository_structures(
                    "postgresql://not-logged",
                    project="Solver",
                    branch="trunk",
                    allowed_access={"public", "lab"},
                )

        self.assertEqual(results, [])
        self.assertIn("repository.project = %(project)s::text", connection.sql)
        self.assertIn("occurrence.branch = %(branch)s::text", connection.sql)
        self.assertIn(
            "document.access_class = ANY(%(allowed_access)s::text[])",
            connection.sql,
        )
        self.assertEqual(connection.parameters["project"], "Solver")
        self.assertEqual(connection.parameters["branch"], "trunk")
        self.assertEqual(connection.parameters["allowed_access"], ["lab", "public"])


if __name__ == "__main__":
    unittest.main()
