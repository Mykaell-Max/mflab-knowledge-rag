from __future__ import annotations

import hashlib
import importlib
import json
from importlib import resources
from pathlib import Path
from typing import Callable, Iterable

from mflab_knowledge.normalize import RETRIEVABLE_ACCESS_CLASSES

LogCallback = Callable[[str, str], None]


def _driver() -> tuple[object, object]:
    try:
        psycopg = importlib.import_module("psycopg")
        rows = importlib.import_module("psycopg.rows")
    except ImportError as exc:
        raise ValueError(
            "suporte PostgreSQL não instalado; execute "
            "python -m pip install -e '.[postgres]'"
        ) from exc
    return psycopg, rows.dict_row


def _connect(database_url: str, *, row_factory: object | None = None) -> object:
    psycopg, _dict_row = _driver()
    options: dict[str, object] = {
        "connect_timeout": 10,
        "application_name": "mflab-knowledge-rag",
    }
    if row_factory is not None:
        options["row_factory"] = row_factory
    return psycopg.connect(database_url, **options)


def _schema_sql() -> str:
    return (
        resources.files("mflab_knowledge")
        .joinpath("sql", "001_initial.sql")
        .read_text(encoding="utf-8")
    )


def _vector_schema_sql() -> str:
    return (
        resources.files("mflab_knowledge")
        .joinpath("sql", "002_vector.sql")
        .read_text(encoding="utf-8")
    )


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    try:
        lines = path.expanduser().resolve().read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"não foi possível abrir {path}: {exc}") from exc
    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON inválido em {path}:{line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"registro inválido em {path}:{line_number}")
        values.append(value)
    return values


def _required_text(value: dict[str, object], key: str, record: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{record} exige {key}")
    return result


def _prepare_corpus(
    documents_path: Path,
    chunks_path: Path,
) -> dict[str, object]:
    documents_file = documents_path.expanduser().resolve()
    chunks_file = chunks_path.expanduser().resolve()
    documents = _read_jsonl(documents_file)
    chunks = _read_jsonl(chunks_file)
    if not documents:
        raise ValueError("corpus sem documentos")

    document_ids: set[str] = set()
    repository_ids: set[str] = set()
    projects: set[str] = set()
    for document in documents:
        document_id = _required_text(document, "document_id", "documento")
        if document_id in document_ids:
            raise ValueError(f"document_id duplicado: {document_id}")
        document_ids.add(document_id)
        repository_ids.add(
            _required_text(document, "repository_id", f"documento {document_id}")
        )
        projects.add(_required_text(document, "project", f"documento {document_id}"))
        occurrences = document.get("occurrences")
        if not isinstance(occurrences, list):
            raise ValueError(f"documento {document_id} sem occurrences")
    if len(repository_ids) != 1 or len(projects) != 1:
        raise ValueError("uma carga deve conter exatamente um repositório e projeto")

    chunk_ids: set[str] = set()
    for chunk in chunks:
        chunk_id = _required_text(chunk, "chunk_id", "chunk")
        if chunk_id in chunk_ids:
            raise ValueError(f"chunk_id duplicado: {chunk_id}")
        chunk_ids.add(chunk_id)
        document_id = _required_text(chunk, "document_id", f"chunk {chunk_id}")
        if document_id not in document_ids:
            raise ValueError(
                f"chunk {chunk_id} referencia documento ausente: {document_id}"
            )

    first = documents[0]
    return {
        "documents_file": documents_file,
        "chunks_file": chunks_file,
        "documents_hash": _file_hash(documents_file),
        "chunks_hash": _file_hash(chunks_file),
        "repository_id": next(iter(repository_ids)),
        "project": next(iter(projects)),
        "remote_url": first.get("remote_url"),
        "documents": documents,
        "chunks": chunks,
    }


def initialize_database(
    database_url: str,
    *,
    log: LogCallback | None = None,
) -> dict[str, object]:
    logger = log or (lambda _message, _level="info": None)
    logger("Aplicando schema PostgreSQL", "info")
    with _connect(database_url) as connection:
        connection.execute(_schema_sql())
    logger("Schema PostgreSQL pronto", "success")
    return {"schema": "mflab_knowledge", "initialized": True}


def initialize_vector_database(
    database_url: str,
    *,
    log: LogCallback | None = None,
) -> dict[str, object]:
    logger = log or (lambda _message, _level="info": None)
    logger("Validando extensão pgvector e schema de embeddings", "info")
    with _connect(database_url) as connection:
        connection.execute(_schema_sql())
        version = connection.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
        if not version:
            raise ValueError(
                "extensão vector não ativada no banco; instale o pacote e "
                "execute como administrador: sudo -u postgres psql -d "
                "mflab_knowledge -c 'CREATE EXTENSION vector'"
            )
        connection.execute(_vector_schema_sql())
    logger(f"pgvector {version[0]} pronto", "success")
    return {
        "schema": "mflab_knowledge",
        "vector_initialized": True,
        "pgvector_version": version[0],
        "dimensions": 1024,
    }


DOCUMENT_UPSERT = """
INSERT INTO mflab_knowledge.documents (
    document_id, repository_id, path, format, size_bytes, content_hash,
    access_class, encoding, parser_version
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (document_id) DO UPDATE SET
    repository_id = EXCLUDED.repository_id,
    path = EXCLUDED.path,
    format = EXCLUDED.format,
    size_bytes = EXCLUDED.size_bytes,
    content_hash = EXCLUDED.content_hash,
    access_class = EXCLUDED.access_class,
    encoding = EXCLUDED.encoding,
    parser_version = EXCLUDED.parser_version,
    updated_at = now()
WHERE (
    mflab_knowledge.documents.repository_id,
    mflab_knowledge.documents.path,
    mflab_knowledge.documents.format,
    mflab_knowledge.documents.size_bytes,
    mflab_knowledge.documents.content_hash,
    mflab_knowledge.documents.access_class,
    mflab_knowledge.documents.encoding,
    mflab_knowledge.documents.parser_version
) IS DISTINCT FROM (
    EXCLUDED.repository_id, EXCLUDED.path, EXCLUDED.format, EXCLUDED.size_bytes,
    EXCLUDED.content_hash, EXCLUDED.access_class, EXCLUDED.encoding,
    EXCLUDED.parser_version
)
"""

CHUNK_UPSERT = """
INSERT INTO mflab_knowledge.chunks (
    chunk_id, document_id, title, kind, line_start, line_end, text,
    chunk_hash, embedding_key, parser_version
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (chunk_id) DO UPDATE SET
    document_id = EXCLUDED.document_id,
    title = EXCLUDED.title,
    kind = EXCLUDED.kind,
    line_start = EXCLUDED.line_start,
    line_end = EXCLUDED.line_end,
    text = EXCLUDED.text,
    chunk_hash = EXCLUDED.chunk_hash,
    embedding_key = EXCLUDED.embedding_key,
    parser_version = EXCLUDED.parser_version,
    updated_at = now()
WHERE (
    mflab_knowledge.chunks.document_id,
    mflab_knowledge.chunks.title,
    mflab_knowledge.chunks.kind,
    mflab_knowledge.chunks.line_start,
    mflab_knowledge.chunks.line_end,
    mflab_knowledge.chunks.text,
    mflab_knowledge.chunks.chunk_hash,
    mflab_knowledge.chunks.embedding_key,
    mflab_knowledge.chunks.parser_version
) IS DISTINCT FROM (
    EXCLUDED.document_id, EXCLUDED.title, EXCLUDED.kind, EXCLUDED.line_start,
    EXCLUDED.line_end, EXCLUDED.text, EXCLUDED.chunk_hash,
    EXCLUDED.embedding_key, EXCLUDED.parser_version
)
"""


def _document_rows(documents: Iterable[dict[str, object]]) -> Iterable[tuple[object, ...]]:
    for document in documents:
        yield (
            document["document_id"],
            document["repository_id"],
            document["path"],
            document.get("format", "unknown"),
            int(document.get("size_bytes", 0)),
            document["content_hash"],
            document["access_class"],
            document.get("encoding"),
            document["parser_version"],
        )


def _chunk_rows(chunks: Iterable[dict[str, object]]) -> Iterable[tuple[object, ...]]:
    for chunk in chunks:
        yield (
            chunk["chunk_id"],
            chunk["document_id"],
            chunk.get("title", "arquivo"),
            chunk.get("kind", "text"),
            int(chunk["line_start"]),
            int(chunk["line_end"]),
            chunk["text"],
            chunk["chunk_hash"],
            chunk["embedding_key"],
            chunk["parser_version"],
        )


def load_corpus(
    database_url: str,
    *,
    documents_path: Path,
    chunks_path: Path,
    log: LogCallback | None = None,
) -> dict[str, object]:
    logger = log or (lambda _message, _level="info": None)
    logger("Validando corpus normalizado", "info")
    corpus = _prepare_corpus(documents_path, chunks_path)
    documents = corpus["documents"]
    chunks = corpus["chunks"]
    assert isinstance(documents, list) and isinstance(chunks, list)
    repository_id = str(corpus["repository_id"])

    with _connect(database_url) as connection:
        connection.execute(_schema_sql())
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT documents_hash, chunks_hash
                FROM mflab_knowledge.ingestion_runs
                WHERE repository_id = %s
                ORDER BY run_id DESC
                LIMIT 1
                """,
                (repository_id,),
            )
            previous = cursor.fetchone()
            if previous and tuple(previous) == (
                corpus["documents_hash"],
                corpus["chunks_hash"],
            ):
                logger("Corpus PostgreSQL já está atualizado", "success")
                return {
                    "repository_id": repository_id,
                    "project": corpus["project"],
                    "documents": len(documents),
                    "chunks": len(chunks),
                    "reused": True,
                }

            logger(
                f"Carregando {len(documents)} documentos e {len(chunks)} chunks",
                "info",
            )
            cursor.execute(
                """
                INSERT INTO mflab_knowledge.repositories (
                    repository_id, project, remote_url
                ) VALUES (%s, %s, %s)
                ON CONFLICT (repository_id) DO UPDATE SET
                    project = EXCLUDED.project,
                    remote_url = EXCLUDED.remote_url,
                    updated_at = now()
                """,
                (repository_id, corpus["project"], corpus["remote_url"]),
            )
            cursor.execute(
                "CREATE TEMP TABLE staged_documents "
                "(document_id text PRIMARY KEY) ON COMMIT DROP"
            )
            cursor.executemany(
                "INSERT INTO staged_documents (document_id) VALUES (%s)",
                [(document["document_id"],) for document in documents],
            )
            cursor.executemany(DOCUMENT_UPSERT, _document_rows(documents))
            logger(f"Documentos preparados: {len(documents)}", "info")
            cursor.execute(
                """
                DELETE FROM mflab_knowledge.document_occurrences
                WHERE document_id IN (SELECT document_id FROM staged_documents)
                """
            )
            occurrence_rows: list[tuple[object, ...]] = []
            for document in documents:
                occurrences = document.get("occurrences", [])
                assert isinstance(occurrences, list)
                for occurrence in occurrences:
                    if not isinstance(occurrence, dict):
                        raise ValueError(
                            f"occurrence inválida no documento {document['document_id']}"
                        )
                    occurrence_rows.append(
                        (
                            document["document_id"],
                            occurrence.get("branch"),
                            occurrence.get("commit_sha"),
                            bool(occurrence.get("canonical")),
                            occurrence.get("requested_ref"),
                        )
                    )
            cursor.executemany(
                """
                INSERT INTO mflab_knowledge.document_occurrences (
                    document_id, branch, commit_sha, canonical, requested_ref
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                occurrence_rows,
            )
            logger(f"Ocorrências preparadas: {len(occurrence_rows)}", "info")
            cursor.execute(
                "CREATE TEMP TABLE staged_chunks "
                "(chunk_id text PRIMARY KEY) ON COMMIT DROP"
            )
            cursor.executemany(
                "INSERT INTO staged_chunks (chunk_id) VALUES (%s)",
                [(chunk["chunk_id"],) for chunk in chunks],
            )
            cursor.executemany(CHUNK_UPSERT, _chunk_rows(chunks))
            logger(f"Chunks preparados: {len(chunks)}", "info")
            logger("Removendo registros obsoletos do repositório", "info")
            cursor.execute(
                """
                DELETE FROM mflab_knowledge.chunks AS chunk
                WHERE chunk.document_id IN (
                    SELECT document_id FROM staged_documents
                ) AND NOT EXISTS (
                    SELECT 1 FROM staged_chunks AS staged
                    WHERE staged.chunk_id = chunk.chunk_id
                )
                """
            )
            cursor.execute(
                """
                DELETE FROM mflab_knowledge.documents AS document
                WHERE document.repository_id = %s
                  AND NOT EXISTS (
                    SELECT 1 FROM staged_documents AS staged
                    WHERE staged.document_id = document.document_id
                  )
                """,
                (repository_id,),
            )
            cursor.execute(
                """
                INSERT INTO mflab_knowledge.ingestion_runs (
                    repository_id, documents_hash, chunks_hash,
                    documents_count, chunks_count
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    repository_id,
                    corpus["documents_hash"],
                    corpus["chunks_hash"],
                    len(documents),
                    len(chunks),
                ),
            )
    logger("Carga PostgreSQL concluída em uma transação", "success")
    return {
        "repository_id": repository_id,
        "project": corpus["project"],
        "documents": len(documents),
        "chunks": len(chunks),
        "reused": False,
        "documents_hash": corpus["documents_hash"],
        "chunks_hash": corpus["chunks_hash"],
    }


SEARCH_SQL = """
WITH query_input AS (
    SELECT
        websearch_to_tsquery('simple', %(query)s) AS parsed,
        lower(%(query)s) AS raw
), scored AS (
    SELECT
        c.chunk_id,
        c.chunk_hash,
        r.project,
        d.repository_id,
        d.path,
        c.title,
        c.line_start,
        c.line_end,
        d.access_class,
        c.text,
        preferred.branch,
        preferred.commit_sha,
        occurrences.items AS occurrences,
        (
            ts_rank_cd(c.search_vector, query_input.parsed, 32) * 10.0
            + CASE WHEN strpos(lower(d.path), query_input.raw) > 0 THEN 8.0 ELSE 0 END
            + CASE WHEN strpos(lower(c.title), query_input.raw) > 0 THEN 4.0 ELSE 0 END
            + CASE WHEN strpos(lower(c.text), query_input.raw) > 0 THEN 2.0 ELSE 0 END
            + CASE WHEN preferred.canonical THEN 0.25 ELSE 0 END
        ) AS score
    FROM mflab_knowledge.chunks AS c
    JOIN mflab_knowledge.documents AS d ON d.document_id = c.document_id
    JOIN mflab_knowledge.repositories AS r
      ON r.repository_id = d.repository_id
    CROSS JOIN query_input
    JOIN LATERAL (
        SELECT occurrence.branch, occurrence.commit_sha, occurrence.canonical
        FROM mflab_knowledge.document_occurrences AS occurrence
        WHERE occurrence.document_id = d.document_id
          AND (
              %(branch)s::text IS NULL
              OR occurrence.branch = %(branch)s::text
          )
        ORDER BY occurrence.canonical DESC, occurrence.branch NULLS LAST
        LIMIT 1
    ) AS preferred ON true
    JOIN LATERAL (
        SELECT jsonb_agg(
            jsonb_build_object(
                'branch', occurrence.branch,
                'commit_sha', occurrence.commit_sha,
                'canonical', occurrence.canonical,
                'requested_ref', occurrence.requested_ref
            ) ORDER BY occurrence.canonical DESC, occurrence.branch
        ) AS items
        FROM mflab_knowledge.document_occurrences AS occurrence
        WHERE occurrence.document_id = d.document_id
          AND (
              %(branch)s::text IS NULL
              OR occurrence.branch = %(branch)s::text
          )
    ) AS occurrences ON true
    WHERE d.access_class = ANY(%(allowed_access)s::text[])
      AND (%(project)s::text IS NULL OR r.project = %(project)s::text)
      AND (
          %(path_prefix)s::text IS NULL
          OR d.path LIKE %(path_prefix)s::text || '%%'
      )
      AND (
          c.search_vector @@ query_input.parsed
          OR strpos(lower(d.path), query_input.raw) > 0
          OR strpos(lower(c.title), query_input.raw) > 0
          OR strpos(lower(c.text), query_input.raw) > 0
      )
), content_ranked AS (
    SELECT scored.*,
           row_number() OVER (
               PARTITION BY chunk_hash ORDER BY score DESC, path, chunk_id
           ) AS content_rank
    FROM scored
), path_ranked AS (
    SELECT content_ranked.*,
           row_number() OVER (
               PARTITION BY repository_id, path
               ORDER BY score DESC, chunk_id
           ) AS path_rank
    FROM content_ranked
    WHERE %(include_duplicates)s OR content_rank = 1
)
SELECT
    score, chunk_id, chunk_hash, project, path, title, line_start, line_end,
    access_class, branch, commit_sha, occurrences, text
FROM path_ranked
WHERE path_rank <= %(max_per_path)s
ORDER BY score DESC, path, chunk_id
LIMIT %(limit)s
"""


def search_postgres(
    database_url: str,
    *,
    query: str,
    limit: int = 10,
    branch: str | None = None,
    project: str | None = None,
    path_prefix: str | None = None,
    allowed_access: set[str] | None = None,
    max_per_path: int = 2,
    include_duplicate_content: bool = False,
) -> list[dict[str, object]]:
    query_text = query.strip()
    if not query_text:
        raise ValueError("consulta vazia")
    if limit < 1 or limit > 100:
        raise ValueError("limit deve estar entre 1 e 100")
    if max_per_path < 1 or max_per_path > 100:
        raise ValueError("max_per_path deve estar entre 1 e 100")
    effective_access = allowed_access if allowed_access is not None else {"public"}
    if not effective_access or not effective_access.issubset(
        RETRIEVABLE_ACCESS_CLASSES
    ):
        raise ValueError("filtro de acesso inválido ou vazio")
    _psycopg, dict_row = _driver()
    parameters = {
        "query": query_text,
        "limit": limit,
        "branch": branch,
        "project": project,
        "path_prefix": path_prefix,
        "allowed_access": sorted(effective_access),
        "max_per_path": max_per_path,
        "include_duplicates": include_duplicate_content,
    }
    with _connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(SEARCH_SQL, parameters).fetchall()
    return _rows_to_results(rows)


def _rows_to_results(rows: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for row in rows:
        result = dict(row)
        commit_sha = str(result.pop("commit_sha") or "?")
        selected_branch = str(result.pop("branch") or "?")
        result["score"] = round(float(result["score"]), 4)
        result["citation"] = (
            f"{result.get('project')} {selected_branch}@{commit_sha[:12]} "
            f"{result.get('path')}:L{result.get('line_start')}-"
            f"L{result.get('line_end')}"
        )
        results.append(result)
    return results


def database_status(database_url: str) -> dict[str, object]:
    _psycopg, dict_row = _driver()
    with _connect(database_url, row_factory=dict_row) as connection:
        row = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM mflab_knowledge.repositories) AS repositories,
                (SELECT count(*) FROM mflab_knowledge.documents) AS documents,
                (SELECT count(*) FROM mflab_knowledge.document_occurrences)
                    AS occurrences,
                (SELECT count(*) FROM mflab_knowledge.chunks) AS chunks,
                (SELECT max(completed_at) FROM mflab_knowledge.ingestion_runs)
                    AS last_ingestion
            """
        ).fetchone()
    if row is None:
        raise ValueError("não foi possível consultar o status PostgreSQL")
    result = dict(row)
    if result["last_ingestion"] is not None:
        result["last_ingestion"] = result["last_ingestion"].isoformat()
    return result


def repository_status(
    database_url: str,
    *,
    embedding_profile: str | None = None,
    allowed_access: set[str] | None = None,
) -> list[dict[str, object]]:
    """Return repository-level coverage without exposing document contents."""

    effective_access = allowed_access if allowed_access is not None else {"public"}
    if not effective_access or not effective_access.issubset(
        RETRIEVABLE_ACCESS_CLASSES
    ):
        raise ValueError("filtro de acesso inválido ou vazio")
    _psycopg, dict_row = _driver()
    with _connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            WITH document_stats AS (
                SELECT repository_id, count(*) AS documents
                FROM mflab_knowledge.documents
                WHERE access_class = ANY(%(allowed_access)s::text[])
                GROUP BY repository_id
            ), occurrence_stats AS (
                SELECT
                    document.repository_id,
                    count(*) AS occurrences,
                    count(DISTINCT occurrence.branch)
                        FILTER (WHERE occurrence.branch IS NOT NULL) AS branches,
                    array_agg(DISTINCT occurrence.branch ORDER BY occurrence.branch)
                        FILTER (
                            WHERE occurrence.canonical
                              AND occurrence.branch IS NOT NULL
                        ) AS canonical_branches
                FROM mflab_knowledge.documents AS document
                JOIN mflab_knowledge.document_occurrences AS occurrence
                  ON occurrence.document_id = document.document_id
                WHERE document.access_class = ANY(%(allowed_access)s::text[])
                GROUP BY document.repository_id
            ), chunk_stats AS (
                SELECT
                    document.repository_id,
                    count(*) AS chunks,
                    count(*) FILTER (
                        WHERE EXISTS (
                            SELECT 1
                            FROM mflab_knowledge.chunk_embeddings AS embedding
                            WHERE embedding.chunk_id = chunk.chunk_id
                              AND (
                                  %(embedding_profile)s::text IS NULL
                                  OR embedding.model_id =
                                      %(embedding_profile)s::text
                              )
                        )
                    ) AS embedded_chunks
                FROM mflab_knowledge.documents AS document
                JOIN mflab_knowledge.chunks AS chunk
                  ON chunk.document_id = document.document_id
                WHERE document.access_class = ANY(%(allowed_access)s::text[])
                GROUP BY document.repository_id
            ), latest_ingestion AS (
                SELECT DISTINCT ON (repository_id)
                    repository_id,
                    completed_at
                FROM mflab_knowledge.ingestion_runs
                ORDER BY repository_id, run_id DESC
            )
            SELECT
                repository.repository_id,
                repository.project,
                coalesce(document_stats.documents, 0) AS documents,
                coalesce(occurrence_stats.occurrences, 0) AS occurrences,
                coalesce(occurrence_stats.branches, 0) AS branches,
                coalesce(occurrence_stats.canonical_branches, ARRAY[]::text[])
                    AS canonical_branches,
                coalesce(chunk_stats.chunks, 0) AS chunks,
                coalesce(chunk_stats.embedded_chunks, 0) AS embedded_chunks,
                latest_ingestion.completed_at AS last_ingestion
            FROM mflab_knowledge.repositories AS repository
            LEFT JOIN document_stats
              ON document_stats.repository_id = repository.repository_id
            LEFT JOIN occurrence_stats
              ON occurrence_stats.repository_id = repository.repository_id
            LEFT JOIN chunk_stats
              ON chunk_stats.repository_id = repository.repository_id
            LEFT JOIN latest_ingestion
              ON latest_ingestion.repository_id = repository.repository_id
            WHERE EXISTS (
                SELECT 1
                FROM mflab_knowledge.documents AS visible_document
                WHERE visible_document.repository_id = repository.repository_id
                  AND visible_document.access_class =
                      ANY(%(allowed_access)s::text[])
            )
            ORDER BY repository.project, repository.repository_id
            """,
            {
                "embedding_profile": embedding_profile,
                "allowed_access": sorted(effective_access),
            },
        ).fetchall()

    results: list[dict[str, object]] = []
    for row in rows:
        value = dict(row)
        if value["last_ingestion"] is not None:
            value["last_ingestion"] = value["last_ingestion"].isoformat()
        value["canonical_branches"] = list(value["canonical_branches"] or [])
        chunks = int(value["chunks"])
        embedded = int(value["embedded_chunks"])
        value["embedding_coverage"] = (
            round(embedded / chunks, 6) if chunks else 0.0
        )
        value["embedding_profile"] = embedding_profile
        value["allowed_access"] = sorted(effective_access)
        results.append(value)
    return results


def database_fingerprint(database_url: str) -> dict[str, object]:
    _psycopg, dict_row = _driver()
    with _connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT ON (repository.repository_id)
                repository.repository_id,
                repository.project,
                run.documents_hash,
                run.chunks_hash,
                run.documents_count,
                run.chunks_count,
                run.completed_at
            FROM mflab_knowledge.repositories AS repository
            JOIN mflab_knowledge.ingestion_runs AS run
              ON run.repository_id = repository.repository_id
            ORDER BY repository.repository_id, run.run_id DESC
            """
        ).fetchall()
    corpora: list[dict[str, object]] = []
    for row in rows:
        value = dict(row)
        value["completed_at"] = value["completed_at"].isoformat()
        corpora.append(value)
    return {"type": "postgresql", "corpora": corpora}
