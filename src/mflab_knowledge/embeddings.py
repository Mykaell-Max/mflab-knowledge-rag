from __future__ import annotations

import hashlib
import importlib
from collections import Counter
from typing import Callable, Iterable

from mflab_knowledge.database import (
    _connect,
    _driver,
    _rows_to_results,
    database_fingerprint,
    initialize_vector_database,
    search_postgres,
)
from mflab_knowledge.normalize import RETRIEVABLE_ACCESS_CLASSES

LogCallback = Callable[[str, str], None]
ProgressCallback = Callable[[int, int, str], None]

DEFAULT_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_EMBEDDING_REVISION = "84c1ea74ee30f1c5e0e4bb16ef369b39cf05a9ba"
EMBEDDING_DIMENSIONS = 1024
DEFAULT_MAX_SEQUENCE_LENGTH = 4096
QUERY_PROMPT = (
    "Instruct: Given a technical question about scientific software, retrieve "
    "relevant code, configuration, or documentation passages that answer it\n"
    "Query:"
)


def embedding_profile_id(
    model_id: str,
    *,
    revision: str,
    max_sequence_length: int,
) -> str:
    identity = (
        f"{model_id}\0{revision}\0{EMBEDDING_DIMENSIONS}\0"
        f"{max_sequence_length}\0"
        f"{QUERY_PROMPT}"
    )
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    short_name = model_id.rsplit("/", 1)[-1].casefold()
    return f"{short_name}-mflab-{suffix}"


def _embedding_dependencies() -> tuple[object, object]:
    try:
        sentence_transformers = importlib.import_module("sentence_transformers")
        pgvector_psycopg = importlib.import_module("pgvector.psycopg")
    except ImportError as exc:
        raise ValueError(
            "suporte a embeddings não instalado; execute "
            "python -m pip install -e '.[postgres,embeddings]'"
        ) from exc
    return sentence_transformers.SentenceTransformer, pgvector_psycopg.register_vector


class LocalEmbedder:
    def __init__(
        self,
        *,
        model_id: str = DEFAULT_EMBEDDING_MODEL,
        revision: str = DEFAULT_EMBEDDING_REVISION,
        device: str = "auto",
        max_sequence_length: int = DEFAULT_MAX_SEQUENCE_LENGTH,
        log: LogCallback | None = None,
    ) -> None:
        if max_sequence_length < 128 or max_sequence_length > 32768:
            raise ValueError("max_sequence_length deve estar entre 128 e 32768")
        logger = log or (lambda _message, _level="info": None)
        sentence_transformer, register_vector = _embedding_dependencies()
        logger(f"Carregando modelo local {model_id}", "info")
        selected_device = None if device == "auto" else device
        self.model = sentence_transformer(
            model_id,
            revision=revision,
            device=selected_device,
            truncate_dim=EMBEDDING_DIMENSIONS,
        )
        dimensions = int(self.model.get_sentence_embedding_dimension())
        if dimensions != EMBEDDING_DIMENSIONS:
            raise ValueError(
                f"modelo produziu {dimensions} dimensões; esperado "
                f"{EMBEDDING_DIMENSIONS}"
            )
        self.model.max_seq_length = max_sequence_length
        self.model_id = model_id
        self.revision = revision
        self.max_sequence_length = max_sequence_length
        self.profile_id = embedding_profile_id(
            model_id,
            revision=revision,
            max_sequence_length=max_sequence_length,
        )
        self.device = str(self.model.device)
        self._register_vector = register_vector
        logger(
            f"Modelo pronto em {self.device}; perfil {self.profile_id}",
            "success",
        )

    def register_vector(self, connection: object) -> None:
        self._register_vector(connection)

    def encode_documents(
        self,
        texts: list[str],
        *,
        batch_size: int,
    ) -> object:
        return self.model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )

    def encode_query(self, query: str) -> object:
        values = self.model.encode(
            [query],
            prompt=QUERY_PROMPT,
            batch_size=1,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return values[0]


def _embedding_text(row: dict[str, object]) -> str:
    return (
        f"Project: {row.get('project', '')}\n"
        f"Path: {row.get('path', '')}\n"
        f"Format: {row.get('format', '')}\n"
        f"Title: {row.get('title', '')}\n"
        f"Content:\n{row.get('text', '')}"
    )


def embed_database(
    database_url: str,
    *,
    model_id: str = DEFAULT_EMBEDDING_MODEL,
    revision: str = DEFAULT_EMBEDDING_REVISION,
    device: str = "auto",
    max_sequence_length: int = DEFAULT_MAX_SEQUENCE_LENGTH,
    batch_size: int = 4,
    log: LogCallback | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, object]:
    if batch_size < 1 or batch_size > 256:
        raise ValueError("batch_size deve estar entre 1 e 256")
    logger = log or (lambda _message, _level="info": None)
    initialize_vector_database(database_url, log=logger)
    profile_id = embedding_profile_id(
        model_id,
        revision=revision,
        max_sequence_length=max_sequence_length,
    )
    _psycopg, dict_row = _driver()
    with _connect(database_url, row_factory=dict_row) as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                chunk.chunk_id,
                repository.project,
                document.path,
                document.format,
                chunk.title,
                chunk.text
            FROM mflab_knowledge.chunks AS chunk
            JOIN mflab_knowledge.documents AS document
              ON document.document_id = chunk.document_id
            JOIN mflab_knowledge.repositories AS repository
              ON repository.repository_id = document.repository_id
            LEFT JOIN mflab_knowledge.chunk_embeddings AS embedding
              ON embedding.chunk_id = chunk.chunk_id
             AND embedding.model_id = %s
            WHERE embedding.chunk_id IS NULL
              AND document.access_class <> 'pending'
            ORDER BY chunk.chunk_id
            """,
            (profile_id,),
        )
        missing = [dict(row) for row in cursor.fetchall()]
        reused_row = connection.execute(
            """
            SELECT count(*) AS embeddings_count
            FROM mflab_knowledge.chunk_embeddings
            WHERE model_id = %s
            """,
            (profile_id,),
        ).fetchone()
        reused = int(reused_row["embeddings_count"]) if reused_row else 0

    total = len(missing)
    if total == 0:
        logger(f"Todos os {reused} embeddings já estão atualizados", "success")
        return {
            "model": model_id,
            "revision": revision,
            "profile": profile_id,
            "dimensions": EMBEDDING_DIMENSIONS,
            "device": "not_loaded",
            "embedded": 0,
            "reused": reused,
            "total": reused,
        }

    embedder = LocalEmbedder(
        model_id=model_id,
        revision=revision,
        device=device,
        max_sequence_length=max_sequence_length,
        log=logger,
    )
    with _connect(database_url) as connection:
        connection.execute(
            """
            INSERT INTO mflab_knowledge.embedding_models (
                model_id, source_model, source_revision, dimensions,
                max_sequence_length, query_prompt
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (model_id) DO UPDATE SET
                source_model = EXCLUDED.source_model,
                source_revision = EXCLUDED.source_revision,
                dimensions = EXCLUDED.dimensions,
                max_sequence_length = EXCLUDED.max_sequence_length,
                query_prompt = EXCLUDED.query_prompt,
                updated_at = now()
            """,
            (
                embedder.profile_id,
                embedder.model_id,
                embedder.revision,
                EMBEDDING_DIMENSIONS,
                embedder.max_sequence_length,
                QUERY_PROMPT,
            ),
        )
    embedded = 0
    for start in range(0, total, batch_size):
        batch = missing[start : start + batch_size]
        vectors = embedder.encode_documents(
            [_embedding_text(row) for row in batch],
            batch_size=batch_size,
        )
        with _connect(database_url) as connection:
            embedder.register_vector(connection)
            connection.cursor().executemany(
                """
                INSERT INTO mflab_knowledge.chunk_embeddings (
                    chunk_id, model_id, embedding
                ) VALUES (%s, %s, %s)
                ON CONFLICT (chunk_id, model_id) DO UPDATE SET
                    embedding = EXCLUDED.embedding,
                    updated_at = now()
                """,
                [
                    (row["chunk_id"], embedder.profile_id, vector)
                    for row, vector in zip(batch, vectors, strict=True)
                ],
            )
        embedded += len(batch)
        if progress is not None:
            progress(embedded, total, str(batch[-1]["path"]))

    with _connect(database_url) as connection:
        connection.execute(
            """
            INSERT INTO mflab_knowledge.embedding_runs (
                model_id, chunks_embedded, chunks_reused, device
            ) VALUES (%s, %s, %s, %s)
            """,
            (embedder.profile_id, embedded, reused, embedder.device),
        )
    logger(
        f"Embeddings concluídos: {embedded} calculados, {reused} reutilizados",
        "success",
    )
    return {
        "model": embedder.model_id,
        "revision": embedder.revision,
        "profile": embedder.profile_id,
        "dimensions": EMBEDDING_DIMENSIONS,
        "device": embedder.device,
        "embedded": embedded,
        "reused": reused,
        "total": embedded + reused,
    }


SEMANTIC_SEARCH_SQL = """
SELECT
    1.0 - (embedding.embedding <=> %(query_embedding)s) AS score,
    chunk.chunk_id,
    chunk.chunk_hash,
    repository.project,
    document.path,
    chunk.title,
    chunk.line_start,
    chunk.line_end,
    document.access_class,
    preferred.branch,
    preferred.commit_sha,
    occurrences.items AS occurrences,
    chunk.text
FROM mflab_knowledge.chunk_embeddings AS embedding
JOIN mflab_knowledge.chunks AS chunk ON chunk.chunk_id = embedding.chunk_id
JOIN mflab_knowledge.documents AS document
  ON document.document_id = chunk.document_id
JOIN mflab_knowledge.repositories AS repository
  ON repository.repository_id = document.repository_id
JOIN LATERAL (
    SELECT occurrence.branch, occurrence.commit_sha, occurrence.canonical
    FROM mflab_knowledge.document_occurrences AS occurrence
    WHERE occurrence.document_id = document.document_id
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
    WHERE occurrence.document_id = document.document_id
      AND (
          %(branch)s::text IS NULL
          OR occurrence.branch = %(branch)s::text
      )
) AS occurrences ON true
WHERE embedding.model_id = %(profile)s
  AND document.access_class = ANY(%(allowed_access)s::text[])
  AND (%(project)s::text IS NULL OR repository.project = %(project)s::text)
  AND (
      %(path_prefix)s::text IS NULL
      OR document.path LIKE %(path_prefix)s::text || '%%'
  )
ORDER BY embedding.embedding <=> %(query_embedding)s
LIMIT %(candidate_limit)s
"""


def semantic_search(
    database_url: str,
    embedder: LocalEmbedder,
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
    query_embedding = embedder.encode_query(query_text)
    _psycopg, dict_row = _driver()
    candidate_limit = min(500, max(limit * 10, 50))
    with _connect(database_url, row_factory=dict_row) as connection:
        embedder.register_vector(connection)
        available = connection.execute(
            """
            SELECT count(*) AS embeddings_count
            FROM mflab_knowledge.chunk_embeddings
            WHERE model_id = %s
            """,
            (embedder.profile_id,),
        ).fetchone()
        if not available or int(available["embeddings_count"]) == 0:
            raise ValueError(
                f"nenhum embedding encontrado para {embedder.profile_id}; "
                "execute db-embed com o mesmo modelo e max-sequence-length"
            )
        rows = connection.execute(
            SEMANTIC_SEARCH_SQL,
            {
                "query_embedding": query_embedding,
                "profile": embedder.profile_id,
                "branch": branch,
                "project": project,
                "path_prefix": path_prefix,
                "allowed_access": sorted(effective_access),
                "candidate_limit": candidate_limit,
            },
        ).fetchall()
    candidates = _rows_to_results(rows)
    return _diversify(
        candidates,
        limit=limit,
        max_per_path=max_per_path,
        include_duplicate_content=include_duplicate_content,
    )


def _diversify(
    candidates: Iterable[dict[str, object]],
    *,
    limit: int,
    max_per_path: int,
    include_duplicate_content: bool,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    paths: Counter[str] = Counter()
    seen_content: set[str] = set()
    for result in candidates:
        path = str(result.get("path", ""))
        chunk_hash = str(result.get("chunk_hash", ""))
        if paths[path] >= max_per_path:
            continue
        if not include_duplicate_content and chunk_hash in seen_content:
            continue
        results.append(result)
        paths[path] += 1
        if chunk_hash:
            seen_content.add(chunk_hash)
        if len(results) >= limit:
            break
    return results


def hybrid_search(
    database_url: str,
    embedder: LocalEmbedder,
    *,
    query: str,
    limit: int = 10,
    branch: str | None = None,
    project: str | None = None,
    path_prefix: str | None = None,
    allowed_access: set[str] | None = None,
    max_per_path: int = 2,
    include_duplicate_content: bool = False,
    rrf_k: int = 60,
) -> list[dict[str, object]]:
    candidate_limit = min(100, max(limit * 5, 30))
    broad_per_path = min(100, max(max_per_path, 10))
    common = {
        "query": query,
        "limit": candidate_limit,
        "branch": branch,
        "project": project,
        "path_prefix": path_prefix,
        "allowed_access": allowed_access,
        "max_per_path": broad_per_path,
        "include_duplicate_content": include_duplicate_content,
    }
    lexical = search_postgres(database_url, **common)
    semantic = semantic_search(database_url, embedder, **common)
    combined: dict[str, dict[str, object]] = {}
    for source_name, values in (("lexical", lexical), ("semantic", semantic)):
        for rank, value in enumerate(values, start=1):
            chunk_id = str(value["chunk_id"])
            item = combined.setdefault(chunk_id, dict(value))
            item[f"{source_name}_rank"] = rank
            item[f"{source_name}_score"] = value["score"]
            item["rrf_score"] = float(item.get("rrf_score", 0.0)) + 1.0 / (
                rrf_k + rank
            )
    ranked = sorted(
        combined.values(),
        key=lambda value: (
            -float(value["rrf_score"]),
            str(value.get("path", "")),
        ),
    )
    for value in ranked:
        value["score"] = round(float(value["rrf_score"]), 6)
    return _diversify(
        ranked,
        limit=limit,
        max_per_path=max_per_path,
        include_duplicate_content=include_duplicate_content,
    )


def embedding_status(database_url: str) -> dict[str, object]:
    _psycopg, dict_row = _driver()
    with _connect(database_url, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT
                model.model_id AS profile,
                model.source_model AS model,
                model.source_revision AS revision,
                model.dimensions,
                model.max_sequence_length,
                count(DISTINCT embedding.chunk_id) AS embeddings,
                max(run.completed_at) AS last_run
            FROM mflab_knowledge.embedding_models AS model
            LEFT JOIN mflab_knowledge.chunk_embeddings AS embedding
              ON embedding.model_id = model.model_id
            LEFT JOIN mflab_knowledge.embedding_runs AS run
              ON run.model_id = model.model_id
            GROUP BY model.model_id
            ORDER BY model.model_id
            """
        ).fetchall()
    models: list[dict[str, object]] = []
    for row in rows:
        value = dict(row)
        if value["last_run"] is not None:
            value["last_run"] = value["last_run"].isoformat()
        models.append(value)
    return {"models": models}


def hybrid_fingerprint(database_url: str, embedder: LocalEmbedder) -> dict[str, object]:
    fingerprint = database_fingerprint(database_url)
    fingerprint["mode"] = "hybrid_rrf"
    fingerprint["embedding_model"] = embedder.model_id
    fingerprint["embedding_revision"] = embedder.revision
    fingerprint["embedding_profile"] = embedder.profile_id
    fingerprint["embedding_dimensions"] = EMBEDDING_DIMENSIONS
    fingerprint["embedding_status"] = embedding_status(database_url)
    return fingerprint
