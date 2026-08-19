from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable

from mflab_knowledge.database import _connect, _driver, _read_jsonl, _schema_sql
from mflab_knowledge.semantic_map import (
    SEMANTIC_MAP_ALGORITHM,
    semantic_map_fingerprint,
)

LogCallback = Callable[[str, str], None]


SYMBOL_UPSERT = """
INSERT INTO mflab_knowledge.semantic_symbols (
    symbol_id, document_id, evidence_chunk_id, name, qualified_name,
    kind, line_start, line_end, algorithm
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (symbol_id) DO UPDATE SET
    document_id = EXCLUDED.document_id,
    evidence_chunk_id = EXCLUDED.evidence_chunk_id,
    name = EXCLUDED.name,
    qualified_name = EXCLUDED.qualified_name,
    kind = EXCLUDED.kind,
    line_start = EXCLUDED.line_start,
    line_end = EXCLUDED.line_end,
    algorithm = EXCLUDED.algorithm,
    updated_at = now()
WHERE (
    mflab_knowledge.semantic_symbols.document_id,
    mflab_knowledge.semantic_symbols.evidence_chunk_id,
    mflab_knowledge.semantic_symbols.name,
    mflab_knowledge.semantic_symbols.qualified_name,
    mflab_knowledge.semantic_symbols.kind,
    mflab_knowledge.semantic_symbols.line_start,
    mflab_knowledge.semantic_symbols.line_end,
    mflab_knowledge.semantic_symbols.algorithm
) IS DISTINCT FROM (
    EXCLUDED.document_id, EXCLUDED.evidence_chunk_id, EXCLUDED.name,
    EXCLUDED.qualified_name, EXCLUDED.kind, EXCLUDED.line_start,
    EXCLUDED.line_end, EXCLUDED.algorithm
)
"""


RELATION_UPSERT = """
INSERT INTO mflab_knowledge.semantic_relations (
    relation_id, source_document_id, target_document_id, evidence_chunk_id,
    kind, target_kind, target_name, line, access_class, algorithm
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (relation_id) DO UPDATE SET
    source_document_id = EXCLUDED.source_document_id,
    target_document_id = EXCLUDED.target_document_id,
    evidence_chunk_id = EXCLUDED.evidence_chunk_id,
    kind = EXCLUDED.kind,
    target_kind = EXCLUDED.target_kind,
    target_name = EXCLUDED.target_name,
    line = EXCLUDED.line,
    access_class = EXCLUDED.access_class,
    algorithm = EXCLUDED.algorithm,
    updated_at = now()
WHERE (
    mflab_knowledge.semantic_relations.source_document_id,
    mflab_knowledge.semantic_relations.target_document_id,
    mflab_knowledge.semantic_relations.evidence_chunk_id,
    mflab_knowledge.semantic_relations.kind,
    mflab_knowledge.semantic_relations.target_kind,
    mflab_knowledge.semantic_relations.target_name,
    mflab_knowledge.semantic_relations.line,
    mflab_knowledge.semantic_relations.access_class,
    mflab_knowledge.semantic_relations.algorithm
) IS DISTINCT FROM (
    EXCLUDED.source_document_id, EXCLUDED.target_document_id,
    EXCLUDED.evidence_chunk_id, EXCLUDED.kind, EXCLUDED.target_kind,
    EXCLUDED.target_name, EXCLUDED.line, EXCLUDED.access_class,
    EXCLUDED.algorithm
)
"""


def _required_text(value: dict[str, object], key: str, record: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{record} exige {key}")
    return result


def _load_summary(path: Path) -> dict[str, object]:
    resolved = path.expanduser().resolve()
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"resumo do mapa semântico inválido: {resolved}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("resumo do mapa semântico deve ser um objeto JSON")
    return value


def _validate_records(
    *,
    summary: dict[str, object],
    symbols: list[dict[str, object]],
    relations: list[dict[str, object]],
) -> tuple[str, str, str]:
    if summary.get("algorithm") != SEMANTIC_MAP_ALGORITHM:
        raise ValueError("algoritmo do mapa semântico não reconhecido")
    repository_id = _required_text(summary, "repository_id", "resumo")
    project = _required_text(summary, "project", "resumo")
    if len(symbols) != int(summary.get("symbols") or 0):
        raise ValueError("contagem de símbolos diverge do resumo")
    if len(relations) != int(summary.get("relations") or 0):
        raise ValueError("contagem de relações diverge do resumo")

    symbol_ids: set[str] = set()
    for symbol in symbols:
        symbol_id = _required_text(symbol, "symbol_id", "símbolo")
        if symbol_id in symbol_ids:
            raise ValueError(f"symbol_id duplicado: {symbol_id}")
        symbol_ids.add(symbol_id)
        if symbol.get("repository_id") != repository_id:
            raise ValueError("símbolo pertence a outro repositório")
        _required_text(symbol, "document_id", f"símbolo {symbol_id}")
        _required_text(symbol, "evidence_chunk_id", f"símbolo {symbol_id}")
        _required_text(symbol, "qualified_name", f"símbolo {symbol_id}")

    relation_ids: set[str] = set()
    for relation in relations:
        relation_id = _required_text(relation, "relation_id", "relação")
        if relation_id in relation_ids:
            raise ValueError(f"relation_id duplicado: {relation_id}")
        relation_ids.add(relation_id)
        if relation.get("repository_id") != repository_id:
            raise ValueError("relação pertence a outro repositório")
        _required_text(
            relation,
            "source_document_id",
            f"relação {relation_id}",
        )
        occurrences = relation.get("occurrences")
        if not isinstance(occurrences, list):
            raise ValueError(f"relação {relation_id} exige occurrences")

    fingerprint = semantic_map_fingerprint(symbols, relations)
    if summary.get("fingerprint") != fingerprint:
        raise ValueError("fingerprint do mapa semântico diverge dos artefatos")
    return repository_id, project, fingerprint


def _symbol_rows(
    symbols: Iterable[dict[str, object]],
) -> Iterable[tuple[object, ...]]:
    for symbol in symbols:
        yield (
            symbol["symbol_id"],
            symbol["document_id"],
            symbol["evidence_chunk_id"],
            symbol.get("name", symbol["qualified_name"]),
            symbol["qualified_name"],
            symbol["kind"],
            int(symbol["line_start"]),
            int(symbol["line_end"]),
            symbol["algorithm"],
        )


def _relation_rows(
    relations: Iterable[dict[str, object]],
) -> Iterable[tuple[object, ...]]:
    for relation in relations:
        yield (
            relation["relation_id"],
            relation["source_document_id"],
            relation.get("target_document_id"),
            relation.get("evidence_chunk_id"),
            relation["kind"],
            relation["target_kind"],
            relation["target_name"],
            int(relation["line"]) if relation.get("line") is not None else None,
            relation["access_class"],
            relation["algorithm"],
        )


def load_semantic_map(
    database_url: str,
    *,
    summary_path: Path,
    symbols_path: Path,
    relations_path: Path,
    log: LogCallback | None = None,
) -> dict[str, object]:
    """Load one repository map transactionally and idempotently."""

    logger = log or (lambda _message, _level="info": None)
    logger("Validando mapa semântico derivado", "info")
    summary = _load_summary(summary_path)
    symbols = _read_jsonl(symbols_path)
    relations = _read_jsonl(relations_path)
    repository_id, project, fingerprint = _validate_records(
        summary=summary,
        symbols=symbols,
        relations=relations,
    )

    with _connect(database_url) as connection:
        connection.execute(_schema_sql())
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT fingerprint, symbols_count, relations_count
                FROM mflab_knowledge.semantic_map_runs
                WHERE repository_id = %s
                ORDER BY run_id DESC
                LIMIT 1
                """,
                (repository_id,),
            )
            previous = cursor.fetchone()
            if previous and tuple(previous) == (
                fingerprint,
                len(symbols),
                len(relations),
            ):
                logger("Mapa semântico PostgreSQL já está atualizado", "success")
                return {
                    "repository_id": repository_id,
                    "project": project,
                    "symbols": len(symbols),
                    "relations": len(relations),
                    "fingerprint": fingerprint,
                    "reused": True,
                }

            cursor.execute(
                "CREATE TEMP TABLE staged_semantic_symbols "
                "(symbol_id text PRIMARY KEY) ON COMMIT DROP"
            )
            cursor.execute(
                "CREATE TEMP TABLE staged_semantic_relations "
                "(relation_id text PRIMARY KEY) ON COMMIT DROP"
            )
            if symbols:
                cursor.executemany(
                    "INSERT INTO staged_semantic_symbols (symbol_id) VALUES (%s)",
                    [(symbol["symbol_id"],) for symbol in symbols],
                )
                cursor.executemany(SYMBOL_UPSERT, _symbol_rows(symbols))
            if relations:
                cursor.executemany(
                    "INSERT INTO staged_semantic_relations (relation_id) VALUES (%s)",
                    [(relation["relation_id"],) for relation in relations],
                )
                cursor.executemany(RELATION_UPSERT, _relation_rows(relations))

            cursor.execute(
                """
                DELETE FROM mflab_knowledge.semantic_relation_occurrences
                WHERE relation_id IN (
                    SELECT relation_id FROM staged_semantic_relations
                )
                """
            )
            occurrence_rows: list[tuple[object, ...]] = []
            for relation in relations:
                occurrences = relation["occurrences"]
                assert isinstance(occurrences, list)
                for occurrence in occurrences:
                    if not isinstance(occurrence, dict):
                        raise ValueError("occurrence de relação inválida")
                    occurrence_rows.append(
                        (
                            relation["relation_id"],
                            occurrence.get("branch"),
                            occurrence.get("commit_sha"),
                            bool(occurrence.get("canonical")),
                            occurrence.get("requested_ref"),
                        )
                    )
            if occurrence_rows:
                cursor.executemany(
                    """
                    INSERT INTO mflab_knowledge.semantic_relation_occurrences (
                        relation_id, branch, commit_sha, canonical, requested_ref
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    occurrence_rows,
                )

            cursor.execute(
                """
                DELETE FROM mflab_knowledge.semantic_relations AS relation
                USING mflab_knowledge.documents AS document
                WHERE relation.source_document_id = document.document_id
                  AND document.repository_id = %s
                  AND NOT EXISTS (
                      SELECT 1 FROM staged_semantic_relations AS staged
                      WHERE staged.relation_id = relation.relation_id
                  )
                """,
                (repository_id,),
            )
            cursor.execute(
                """
                DELETE FROM mflab_knowledge.semantic_symbols AS symbol
                USING mflab_knowledge.documents AS document
                WHERE symbol.document_id = document.document_id
                  AND document.repository_id = %s
                  AND NOT EXISTS (
                      SELECT 1 FROM staged_semantic_symbols AS staged
                      WHERE staged.symbol_id = symbol.symbol_id
                  )
                """,
                (repository_id,),
            )
            cursor.execute(
                """
                INSERT INTO mflab_knowledge.semantic_map_runs (
                    repository_id, fingerprint, symbols_count, relations_count
                ) VALUES (%s, %s, %s, %s)
                """,
                (repository_id, fingerprint, len(symbols), len(relations)),
            )

    logger(
        f"Mapa PostgreSQL atualizado: {len(symbols)} símbolos, "
        f"{len(relations)} relações",
        "success",
    )
    return {
        "repository_id": repository_id,
        "project": project,
        "symbols": len(symbols),
        "relations": len(relations),
        "fingerprint": fingerprint,
        "reused": False,
    }


def semantic_map_status(
    database_url: str,
    *,
    repository_id: str | None = None,
) -> list[dict[str, object]]:
    """Return aggregate map coverage without exposing source contents."""

    _psycopg, dict_row = _driver()
    with _connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT
                repository.repository_id,
                repository.project,
                coalesce(symbols.items, 0) AS symbols,
                coalesce(relations.items, 0) AS relations,
                coalesce(relations.resolved, 0) AS resolved_relations,
                latest.fingerprint,
                latest.completed_at
            FROM mflab_knowledge.repositories AS repository
            LEFT JOIN LATERAL (
                SELECT count(*) AS items
                FROM mflab_knowledge.semantic_symbols AS symbol
                JOIN mflab_knowledge.documents AS document
                  ON document.document_id = symbol.document_id
                WHERE document.repository_id = repository.repository_id
            ) AS symbols ON true
            LEFT JOIN LATERAL (
                SELECT
                    count(*) AS items,
                    count(*) FILTER (
                        WHERE relation.target_document_id IS NOT NULL
                    ) AS resolved
                FROM mflab_knowledge.semantic_relations AS relation
                JOIN mflab_knowledge.documents AS document
                  ON document.document_id = relation.source_document_id
                WHERE document.repository_id = repository.repository_id
            ) AS relations ON true
            LEFT JOIN LATERAL (
                SELECT run.fingerprint, run.completed_at
                FROM mflab_knowledge.semantic_map_runs AS run
                WHERE run.repository_id = repository.repository_id
                ORDER BY run.run_id DESC
                LIMIT 1
            ) AS latest ON true
            WHERE (%(repository_id)s::text IS NULL
                   OR repository.repository_id = %(repository_id)s::text)
            ORDER BY repository.project, repository.repository_id
            """,
            {"repository_id": repository_id},
        ).fetchall()
    return [
        {
            **dict(row),
            "symbols": int(row["symbols"]),
            "relations": int(row["relations"]),
            "resolved_relations": int(row["resolved_relations"]),
            "completed_at": (
                row["completed_at"].isoformat()
                if row.get("completed_at") is not None
                else None
            ),
        }
        for row in rows
    ]
