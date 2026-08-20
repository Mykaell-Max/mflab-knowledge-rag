from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable

from mflab_knowledge.database import _connect, _driver, _read_jsonl, _schema_sql
from mflab_knowledge.normalize import RETRIEVABLE_ACCESS_CLASSES
from mflab_knowledge.semantic_map import (
    SEMANTIC_MAP_ALGORITHM,
    semantic_map_fingerprint,
)

LogCallback = Callable[[str, str], None]


SYMBOL_SEARCH_SQL = """
WITH query_input AS (
    SELECT
        websearch_to_tsquery('simple', %(query)s) AS parsed,
        lower(%(query)s) AS raw
)
SELECT
    (
        ts_rank_cd(symbol.search_vector, query_input.parsed, 32) * 10.0
        + CASE
            WHEN lower(symbol.qualified_name) = query_input.raw THEN 20.0
            WHEN lower(symbol.name) = query_input.raw THEN 16.0
            WHEN strpos(lower(symbol.qualified_name), query_input.raw) > 0 THEN 8.0
            ELSE 0.0
          END
        + CASE WHEN strpos(lower(document.path), query_input.raw) > 0
               THEN 4.0 ELSE 0.0 END
        + CASE WHEN preferred.canonical THEN 0.25 ELSE 0.0 END
    ) AS score,
    'symbol'::text AS result_type,
    symbol.symbol_id AS item_id,
    repository.project,
    document.repository_id,
    document.path,
    document.format,
    document.access_class,
    symbol.kind,
    symbol.name,
    symbol.qualified_name,
    symbol.line_start,
    symbol.line_end,
    symbol.evidence_chunk_id,
    NULL::text AS target_kind,
    NULL::text AS target_document_id,
    NULL::text AS target_path,
    preferred.branch,
    preferred.commit_sha
FROM mflab_knowledge.semantic_symbols AS symbol
JOIN mflab_knowledge.documents AS document
  ON document.document_id = symbol.document_id
JOIN mflab_knowledge.repositories AS repository
  ON repository.repository_id = document.repository_id
CROSS JOIN query_input
JOIN LATERAL (
    SELECT
        occurrence.branch,
        occurrence.commit_sha,
        occurrence.canonical
    FROM mflab_knowledge.document_occurrences AS occurrence
    WHERE occurrence.document_id = document.document_id
      AND (%(branch)s::text IS NULL
           OR occurrence.branch = %(branch)s::text)
    ORDER BY occurrence.canonical DESC, occurrence.branch NULLS LAST
    LIMIT 1
) AS preferred ON true
WHERE document.access_class = ANY(%(allowed_access)s::text[])
  AND (%(project)s::text IS NULL
       OR repository.project = %(project)s::text)
  AND (%(path_prefix)s::text IS NULL
       OR document.path LIKE %(path_prefix)s::text || '%%')
  AND (%(kind)s::text IS NULL OR symbol.kind = %(kind)s::text)
  AND (
      symbol.search_vector @@ query_input.parsed
      OR strpos(lower(symbol.qualified_name), query_input.raw) > 0
      OR strpos(lower(document.path), query_input.raw) > 0
  )
ORDER BY score DESC, document.path, symbol.line_start, symbol.symbol_id
LIMIT %(candidate_limit)s
"""


RELATION_SEARCH_SQL = """
WITH query_input AS (
    SELECT
        websearch_to_tsquery('simple', %(query)s) AS parsed,
        lower(%(query)s) AS raw
)
SELECT
    (
        ts_rank_cd(relation.search_vector, query_input.parsed, 32) * 10.0
        + CASE
            WHEN lower(relation.target_name) = query_input.raw THEN 20.0
            WHEN strpos(lower(relation.target_name), query_input.raw) > 0 THEN 8.0
            ELSE 0.0
          END
        + CASE WHEN strpos(lower(source.path), query_input.raw) > 0
               THEN 4.0 ELSE 0.0 END
        + CASE WHEN preferred.canonical THEN 0.25 ELSE 0.0 END
    ) AS score,
    'relation'::text AS result_type,
    relation.relation_id AS item_id,
    repository.project,
    source.repository_id,
    source.path,
    source.format,
    relation.access_class,
    relation.kind,
    relation.target_name AS name,
    relation.target_name AS qualified_name,
    coalesce(relation.line, evidence.line_start, 1) AS line_start,
    coalesce(relation.line, evidence.line_end, 1) AS line_end,
    relation.evidence_chunk_id,
    CASE
        WHEN relation.target_document_id IS NULL OR target.document_id IS NOT NULL
        THEN relation.target_kind
        ELSE 'unresolved_reference'
    END AS target_kind,
    CASE WHEN target.document_id IS NOT NULL
         THEN relation.target_document_id ELSE NULL END AS target_document_id,
    target.path AS target_path,
    preferred.branch,
    preferred.commit_sha
FROM mflab_knowledge.semantic_relations AS relation
JOIN mflab_knowledge.documents AS source
  ON source.document_id = relation.source_document_id
JOIN mflab_knowledge.repositories AS repository
  ON repository.repository_id = source.repository_id
LEFT JOIN mflab_knowledge.documents AS target
  ON target.document_id = relation.target_document_id
 AND target.access_class = ANY(%(allowed_access)s::text[])
LEFT JOIN mflab_knowledge.chunks AS evidence
  ON evidence.chunk_id = relation.evidence_chunk_id
CROSS JOIN query_input
JOIN LATERAL (
    SELECT
        occurrence.branch,
        occurrence.commit_sha,
        occurrence.canonical
    FROM mflab_knowledge.semantic_relation_occurrences AS occurrence
    WHERE occurrence.relation_id = relation.relation_id
      AND (%(branch)s::text IS NULL
           OR occurrence.branch = %(branch)s::text)
    ORDER BY occurrence.canonical DESC, occurrence.branch NULLS LAST
    LIMIT 1
) AS preferred ON true
WHERE relation.access_class = ANY(%(allowed_access)s::text[])
  AND source.access_class = ANY(%(allowed_access)s::text[])
  AND (%(project)s::text IS NULL
       OR repository.project = %(project)s::text)
  AND (%(path_prefix)s::text IS NULL
       OR source.path LIKE %(path_prefix)s::text || '%%')
  AND (%(kind)s::text IS NULL OR relation.kind = %(kind)s::text)
  AND (
      relation.search_vector @@ query_input.parsed
      OR strpos(lower(relation.target_name), query_input.raw) > 0
      OR strpos(lower(source.path), query_input.raw) > 0
      OR strpos(lower(coalesce(target.path, '')), query_input.raw) > 0
  )
ORDER BY score DESC, source.path, line_start, relation.relation_id
LIMIT %(candidate_limit)s
"""


RELATED_CHUNKS_SQL = """
WITH origin AS (
    SELECT document.document_id, document.repository_id
    FROM mflab_knowledge.chunks AS chunk
    JOIN mflab_knowledge.documents AS document
      ON document.document_id = chunk.document_id
    JOIN mflab_knowledge.repositories AS repository
      ON repository.repository_id = document.repository_id
    WHERE chunk.chunk_id = %(chunk_id)s
      AND document.access_class = ANY(%(allowed_access)s::text[])
      AND (%(project)s::text IS NULL
           OR repository.project = %(project)s::text)
      AND EXISTS (
          SELECT 1
          FROM mflab_knowledge.document_occurrences AS occurrence
          WHERE occurrence.document_id = document.document_id
            AND (%(branch)s::text IS NULL
                 OR occurrence.branch = %(branch)s::text)
      )
), related AS (
    SELECT
        relation.relation_id,
        relation.kind,
        relation.evidence_chunk_id,
        CASE
            WHEN relation.source_document_id = origin.document_id
            THEN relation.target_document_id
            ELSE relation.source_document_id
        END AS related_document_id,
        CASE WHEN relation.target_document_id = origin.document_id
             THEN 0 ELSE 1 END AS direction_rank
    FROM origin
    JOIN mflab_knowledge.semantic_relations AS relation
      ON relation.source_document_id = origin.document_id
      OR relation.target_document_id = origin.document_id
    WHERE relation.access_class = ANY(%(allowed_access)s::text[])
      AND EXISTS (
          SELECT 1
          FROM mflab_knowledge.semantic_relation_occurrences AS occurrence
          WHERE occurrence.relation_id = relation.relation_id
            AND (%(branch)s::text IS NULL
                 OR occurrence.branch = %(branch)s::text)
      )
), visible_related AS (
    SELECT related.*
    FROM related
    JOIN mflab_knowledge.documents AS document
      ON document.document_id = related.related_document_id
    JOIN mflab_knowledge.repositories AS repository
      ON repository.repository_id = document.repository_id
    WHERE document.access_class = ANY(%(allowed_access)s::text[])
      AND (%(project)s::text IS NULL
           OR repository.project = %(project)s::text)
      AND EXISTS (
          SELECT 1
          FROM mflab_knowledge.document_occurrences AS occurrence
          WHERE occurrence.document_id = document.document_id
            AND (%(branch)s::text IS NULL
                 OR occurrence.branch = %(branch)s::text)
      )
), candidates AS (
    SELECT
        relation.evidence_chunk_id AS chunk_id,
        relation.direction_rank AS priority,
        relation.relation_id
    FROM visible_related AS relation
    WHERE relation.evidence_chunk_id IS NOT NULL
    UNION ALL
    SELECT
        anchor.chunk_id,
        2 + relation.direction_rank AS priority,
        relation.relation_id
    FROM visible_related AS relation
    JOIN LATERAL (
        SELECT chunk.chunk_id
        FROM mflab_knowledge.chunks AS chunk
        WHERE chunk.document_id = relation.related_document_id
        ORDER BY
            CASE WHEN nullif(chunk.title, '') IS NULL THEN 1 ELSE 0 END,
            chunk.line_start,
            chunk.chunk_id
        LIMIT 2
    ) AS anchor ON true
), deduplicated AS (
    SELECT DISTINCT ON (candidate.chunk_id)
        candidate.chunk_id,
        candidate.priority,
        candidate.relation_id
    FROM candidates AS candidate
    ORDER BY candidate.chunk_id, candidate.priority, candidate.relation_id
)
SELECT chunk_id
FROM deduplicated
ORDER BY priority, relation_id, chunk_id
LIMIT %(limit)s
"""


CALLER_CHUNKS_SQL = r"""
WITH origin AS (
    SELECT
        chunk.document_id,
        lower(regexp_replace(chunk.title, '^.*::', '')) AS symbol_name
    FROM mflab_knowledge.chunks AS chunk
    JOIN mflab_knowledge.documents AS document
      ON document.document_id = chunk.document_id
    JOIN mflab_knowledge.repositories AS repository
      ON repository.repository_id = document.repository_id
    WHERE chunk.chunk_id = %(chunk_id)s
      AND document.access_class = ANY(%(allowed_access)s::text[])
      AND (%(project)s::text IS NULL
           OR repository.project = %(project)s::text)
      AND EXISTS (
          SELECT 1
          FROM mflab_knowledge.document_occurrences AS occurrence
          WHERE occurrence.document_id = document.document_id
            AND (%(branch)s::text IS NULL
                 OR occurrence.branch = %(branch)s::text)
      )
)
SELECT relation.evidence_chunk_id AS chunk_id
FROM origin
JOIN mflab_knowledge.semantic_relations AS relation
  ON relation.target_document_id = origin.document_id
 AND relation.kind = 'calls_symbol'
JOIN mflab_knowledge.documents AS caller
  ON caller.document_id = relation.source_document_id
JOIN mflab_knowledge.repositories AS repository
  ON repository.repository_id = caller.repository_id
WHERE relation.evidence_chunk_id IS NOT NULL
  AND relation.access_class = ANY(%(allowed_access)s::text[])
  AND caller.access_class = ANY(%(allowed_access)s::text[])
  AND (%(project)s::text IS NULL
       OR repository.project = %(project)s::text)
  AND lower(regexp_replace(relation.target_name, '^.*(::|->|\.)', ''))
      = origin.symbol_name
  AND EXISTS (
      SELECT 1
      FROM mflab_knowledge.semantic_relation_occurrences AS occurrence
      WHERE occurrence.relation_id = relation.relation_id
        AND (%(branch)s::text IS NULL
             OR occurrence.branch = %(branch)s::text)
  )
GROUP BY relation.evidence_chunk_id
ORDER BY
    min(caller.path),
    min(coalesce(relation.line, 0)),
    relation.evidence_chunk_id
LIMIT %(limit)s
"""


CALLEE_CHUNKS_SQL = r"""
WITH origin AS (
    SELECT chunk.document_id, chunk.chunk_id
    FROM mflab_knowledge.chunks AS chunk
    JOIN mflab_knowledge.documents AS document
      ON document.document_id = chunk.document_id
    JOIN mflab_knowledge.repositories AS repository
      ON repository.repository_id = document.repository_id
    WHERE chunk.chunk_id = %(chunk_id)s
      AND document.access_class = ANY(%(allowed_access)s::text[])
      AND (%(project)s::text IS NULL
           OR repository.project = %(project)s::text)
      AND EXISTS (
          SELECT 1
          FROM mflab_knowledge.document_occurrences AS occurrence
          WHERE occurrence.document_id = document.document_id
            AND (%(branch)s::text IS NULL
                 OR occurrence.branch = %(branch)s::text)
      )
), calls AS (
    SELECT relation.*,
           lower(regexp_replace(relation.target_name, '^.*(::|->|\.)', ''))
               AS symbol_name
    FROM origin
    JOIN mflab_knowledge.semantic_relations AS relation
      ON relation.source_document_id = origin.document_id
     AND relation.evidence_chunk_id = origin.chunk_id
     AND relation.kind = 'calls_symbol'
    WHERE relation.target_document_id IS NOT NULL
      AND relation.access_class = ANY(%(allowed_access)s::text[])
      AND EXISTS (
          SELECT 1
          FROM mflab_knowledge.semantic_relation_occurrences AS occurrence
          WHERE occurrence.relation_id = relation.relation_id
            AND (%(branch)s::text IS NULL
                 OR occurrence.branch = %(branch)s::text)
      )
), candidates AS (
SELECT DISTINCT ON (target_chunk.chunk_id)
    target_chunk.chunk_id,
    calls.line,
    target.path AS target_path
FROM calls
JOIN mflab_knowledge.documents AS target
  ON target.document_id = calls.target_document_id
JOIN mflab_knowledge.repositories AS repository
  ON repository.repository_id = target.repository_id
JOIN LATERAL (
    SELECT chunk.chunk_id
    FROM mflab_knowledge.chunks AS chunk
    WHERE chunk.document_id = target.document_id
    ORDER BY
        CASE
            WHEN lower(regexp_replace(chunk.title, '^.*::', ''))
                 = calls.symbol_name THEN 0
            ELSE 1
        END,
        chunk.line_start,
        chunk.chunk_id
    LIMIT 1
) AS target_chunk ON true
WHERE target.access_class = ANY(%(allowed_access)s::text[])
  AND (%(project)s::text IS NULL
       OR repository.project = %(project)s::text)
  AND EXISTS (
      SELECT 1
      FROM mflab_knowledge.document_occurrences AS occurrence
      WHERE occurrence.document_id = target.document_id
        AND (%(branch)s::text IS NULL
             OR occurrence.branch = %(branch)s::text)
  )
ORDER BY
    target_chunk.chunk_id,
    coalesce(calls.line, 0),
    target.path
)
SELECT chunk_id
FROM candidates
ORDER BY coalesce(line, 0), target_path, chunk_id
LIMIT %(limit)s
"""


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


def search_semantic_map(
    database_url: str,
    *,
    query: str,
    limit: int = 10,
    result_type: str = "any",
    project: str | None = None,
    branch: str | None = None,
    path_prefix: str | None = None,
    kind: str | None = None,
    allowed_access: set[str] | None = None,
) -> list[dict[str, object]]:
    """Search structural metadata while applying scope and ACL in SQL."""

    query_text = query.strip()
    if not query_text:
        raise ValueError("consulta vazia")
    if len(query_text) > 2_000:
        raise ValueError("consulta excede 2000 caracteres")
    if limit < 1 or limit > 100:
        raise ValueError("limit deve estar entre 1 e 100")
    if result_type not in {"any", "symbol", "relation"}:
        raise ValueError("result_type deve ser any, symbol ou relation")
    effective_access = allowed_access if allowed_access is not None else {"public"}
    if not effective_access or not effective_access.issubset(
        RETRIEVABLE_ACCESS_CLASSES
    ):
        raise ValueError("filtro de acesso inválido ou vazio")

    parameters = {
        "query": query_text,
        "candidate_limit": limit if result_type != "any" else limit * 2,
        "project": project,
        "branch": branch,
        "path_prefix": path_prefix,
        "kind": kind,
        "allowed_access": sorted(effective_access),
    }
    statements: list[str] = []
    if result_type in {"any", "symbol"}:
        statements.append(SYMBOL_SEARCH_SQL)
    if result_type in {"any", "relation"}:
        statements.append(RELATION_SEARCH_SQL)

    _psycopg, dict_row = _driver()
    rows: list[dict[str, object]] = []
    with _connect(database_url, row_factory=dict_row) as connection:
        for statement in statements:
            rows.extend(connection.execute(statement, parameters).fetchall())

    rows.sort(
        key=lambda row: (
            -float(row["score"]),
            str(row["path"]),
            int(row["line_start"]),
            str(row["item_id"]),
        )
    )
    results: list[dict[str, object]] = []
    for row in rows[:limit]:
        result = dict(row)
        commit_sha = str(result.pop("commit_sha") or "?")
        selected_branch = str(result.pop("branch") or "?")
        result["score"] = round(float(result["score"]), 4)
        result["evidence_available"] = bool(result["evidence_chunk_id"])
        result["selected_occurrence"] = {
            "branch": selected_branch,
            "commit_sha": commit_sha,
        }
        result["citation"] = (
            f"{result['project']} {selected_branch}@{commit_sha[:12]} "
            f"{result['path']}:L{result['line_start']}-L{result['line_end']}"
        )
        result["source_kind"] = f"semantic_{result['result_type']}"
        results.append(result)
    return results


def related_semantic_chunk_ids(
    database_url: str,
    *,
    chunk_id: str,
    limit: int = 12,
    project: str | None = None,
    branch: str | None = None,
    allowed_access: set[str] | None = None,
) -> list[str]:
    """Return citable chunks from structurally related authorized documents."""

    selected_id = chunk_id.strip()
    if not selected_id or len(selected_id) > 200:
        raise ValueError("chunk_id inválido")
    if limit < 1 or limit > 50:
        raise ValueError("limit deve estar entre 1 e 50")
    effective_access = allowed_access if allowed_access is not None else {"public"}
    if not effective_access or not effective_access.issubset(
        RETRIEVABLE_ACCESS_CLASSES
    ):
        raise ValueError("filtro de acesso inválido ou vazio")
    _psycopg, dict_row = _driver()
    with _connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            RELATED_CHUNKS_SQL,
            {
                "chunk_id": selected_id,
                "limit": limit,
                "project": project,
                "branch": branch,
                "allowed_access": sorted(effective_access),
            },
        ).fetchall()
    return [str(row["chunk_id"]) for row in rows if row.get("chunk_id")]


def call_graph_chunk_ids(
    database_url: str,
    *,
    chunk_id: str,
    direction: str,
    limit: int = 12,
    project: str | None = None,
    branch: str | None = None,
    allowed_access: set[str] | None = None,
) -> list[str]:
    """Return caller or callee evidence for one observed symbol chunk."""

    selected_id = chunk_id.strip()
    if not selected_id or len(selected_id) > 200:
        raise ValueError("chunk_id inválido")
    if direction not in {"callers", "callees"}:
        raise ValueError("direction deve ser callers ou callees")
    if limit < 1 or limit > 50:
        raise ValueError("limit deve estar entre 1 e 50")
    effective_access = allowed_access if allowed_access is not None else {"public"}
    if not effective_access or not effective_access.issubset(
        RETRIEVABLE_ACCESS_CLASSES
    ):
        raise ValueError("filtro de acesso inválido ou vazio")
    statement = CALLER_CHUNKS_SQL if direction == "callers" else CALLEE_CHUNKS_SQL
    _psycopg, dict_row = _driver()
    with _connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            statement,
            {
                "chunk_id": selected_id,
                "limit": limit,
                "project": project,
                "branch": branch,
                "allowed_access": sorted(effective_access),
            },
        ).fetchall()
    return [str(row["chunk_id"]) for row in rows if row.get("chunk_id")]
