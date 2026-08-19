from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from mflab_knowledge import database, semantic_database
from mflab_knowledge.semantic_map import (
    SEMANTIC_MAP_ALGORITHM,
    semantic_map_fingerprint,
)


class _Cursor:
    def __init__(self, previous: tuple[object, ...] | None = None) -> None:
        self.previous = previous
        self.executed: list[tuple[str, object]] = []
        self.many: list[tuple[str, list[tuple[object, ...]]]] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, parameters: object = None) -> None:
        self.executed.append((sql, parameters))

    def executemany(
        self,
        sql: str,
        rows: object,
    ) -> None:
        self.many.append((sql, list(rows)))

    def fetchone(self) -> tuple[object, ...] | None:
        return self.previous


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self.value = cursor
        self.schema = ""

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str) -> None:
        self.schema = sql

    def cursor(self) -> _Cursor:
        return self.value


class _Rows:
    def __init__(self, values: list[dict[str, object]]) -> None:
        self.values = values

    def fetchall(self) -> list[dict[str, object]]:
        return self.values


class _StatusConnection:
    def __init__(self, values: list[dict[str, object]]) -> None:
        self.values = values
        self.sql = ""
        self.parameters: dict[str, object] = {}

    def __enter__(self) -> _StatusConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(
        self,
        sql: str,
        parameters: dict[str, object],
    ) -> _Rows:
        self.sql = sql
        self.parameters = parameters
        return _Rows(self.values)


def _write_jsonl(path: Path, values: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(value) + "\n" for value in values),
        encoding="utf-8",
    )


class SemanticDatabaseTests(unittest.TestCase):
    def _artifacts(self, root: Path) -> tuple[Path, Path, Path, str]:
        symbols = [
            {
                "algorithm": SEMANTIC_MAP_ALGORITHM,
                "symbol_id": "symbol-1",
                "repository_id": "solver-stable",
                "project": "Solver",
                "document_id": "document-1",
                "evidence_chunk_id": "chunk-1",
                "name": "advance",
                "qualified_name": "Solver::advance",
                "kind": "function",
                "line_start": 2,
                "line_end": 8,
            }
        ]
        relations = [
            {
                "algorithm": SEMANTIC_MAP_ALGORITHM,
                "relation_id": "relation-1",
                "repository_id": "solver-stable",
                "project": "Solver",
                "source_document_id": "document-1",
                "target_document_id": "document-2",
                "evidence_chunk_id": "chunk-1",
                "kind": "includes",
                "target_kind": "document",
                "target_name": "model.hpp",
                "line": 2,
                "access_class": "lab",
                "occurrences": [
                    {
                        "branch": "trunk",
                        "commit_sha": "a" * 40,
                        "canonical": True,
                    }
                ],
            }
        ]
        fingerprint = semantic_map_fingerprint(symbols, relations)
        summary = {
            "algorithm": SEMANTIC_MAP_ALGORITHM,
            "repository_id": "solver-stable",
            "project": "Solver",
            "symbols": 1,
            "relations": 1,
            "fingerprint": fingerprint,
        }
        summary_path = root / "semantic-map.generated.json"
        symbols_path = root / "symbols.jsonl"
        relations_path = root / "relations.jsonl"
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        _write_jsonl(symbols_path, symbols)
        _write_jsonl(relations_path, relations)
        return summary_path, symbols_path, relations_path, fingerprint

    def test_schema_contains_acl_aware_semantic_map_tables(self) -> None:
        schema = database._schema_sql()
        self.assertIn("semantic_symbols", schema)
        self.assertIn("semantic_relations", schema)
        self.assertIn("semantic_relation_occurrences", schema)
        self.assertIn("semantic_map_runs", schema)
        self.assertIn("USING GIN (search_vector)", schema)

    def test_loads_map_transactionally_and_records_occurrences(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary, symbols, relations, fingerprint = self._artifacts(root)
            cursor = _Cursor()
            connection = _Connection(cursor)
            with mock.patch.object(
                semantic_database,
                "_connect",
                return_value=connection,
            ):
                result = semantic_database.load_semantic_map(
                    "postgresql://not-logged",
                    summary_path=summary,
                    symbols_path=symbols,
                    relations_path=relations,
                )

        self.assertFalse(result["reused"])
        self.assertEqual(result["fingerprint"], fingerprint)
        self.assertTrue(any("semantic_symbols" in sql for sql, _rows in cursor.many))
        self.assertTrue(any("semantic_relations" in sql for sql, _rows in cursor.many))
        self.assertTrue(
            any("semantic_relation_occurrences" in sql for sql, _rows in cursor.many)
        )
        self.assertTrue(
            any("semantic_map_runs" in sql for sql, _parameters in cursor.executed)
        )

    def test_reuses_identical_fingerprint_without_rewriting_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summary, symbols, relations, fingerprint = self._artifacts(root)
            cursor = _Cursor((fingerprint, 1, 1))
            with mock.patch.object(
                semantic_database,
                "_connect",
                return_value=_Connection(cursor),
            ):
                result = semantic_database.load_semantic_map(
                    "postgresql://not-logged",
                    summary_path=summary,
                    symbols_path=symbols,
                    relations_path=relations,
                )

        self.assertTrue(result["reused"])
        self.assertEqual(cursor.many, [])

    def test_status_reports_counts_without_source_text(self) -> None:
        connection = _StatusConnection(
            [
                {
                    "repository_id": "solver-stable",
                    "project": "Solver",
                    "symbols": 15,
                    "relations": 9,
                    "resolved_relations": 4,
                    "fingerprint": "sha256:" + "a" * 64,
                    "completed_at": datetime(
                        2026,
                        8,
                        19,
                        tzinfo=timezone.utc,
                    ),
                }
            ]
        )
        with mock.patch.object(
            semantic_database,
            "_driver",
            return_value=(object(), object()),
        ):
            with mock.patch.object(
                semantic_database,
                "_connect",
                return_value=connection,
            ):
                result = semantic_database.semantic_map_status(
                    "postgresql://not-logged",
                    repository_id="solver-stable",
                )

        self.assertEqual(result[0]["symbols"], 15)
        self.assertEqual(result[0]["resolved_relations"], 4)
        self.assertEqual(result[0]["completed_at"], "2026-08-19T00:00:00+00:00")
        self.assertIn("%(repository_id)s::text", connection.sql)
        self.assertEqual(connection.parameters["repository_id"], "solver-stable")


if __name__ == "__main__":
    unittest.main()
