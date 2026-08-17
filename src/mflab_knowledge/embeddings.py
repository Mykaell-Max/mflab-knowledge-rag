from __future__ import annotations

import hashlib
import importlib
import re
from collections import Counter
from pathlib import PurePosixPath
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
from mflab_knowledge.retrieval import RetrievalPolicy

LogCallback = Callable[[str, str], None]
ProgressCallback = Callable[[int, int, str], None]

DEFAULT_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_EMBEDDING_REVISION = "84c1ea74ee30f1c5e0e4bb16ef369b39cf05a9ba"
EMBEDDING_DIMENSIONS = 1024
DEFAULT_MAX_SEQUENCE_LENGTH = 4096
DEFAULT_EMBEDDING_CHECKPOINT_SIZE = 1024
QUERY_PROMPT = (
    "Instruct: Given a technical question about scientific software, retrieve "
    "relevant code, configuration, or documentation passages that answer it\n"
    "Query:"
)
_PAIRED_EXTENSIONS = {
    ".c": (".h",),
    ".cc": (".hh", ".hpp"),
    ".cpp": (".hpp", ".h"),
    ".cxx": (".hxx", ".hpp"),
    ".h": (".c", ".cpp"),
    ".hh": (".cc", ".cpp"),
    ".hpp": (".cpp", ".cc", ".cxx"),
    ".hxx": (".cxx", ".cpp"),
}


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
        dimension_getter = getattr(self.model, "get_embedding_dimension", None)
        if dimension_getter is None:
            dimension_getter = self.model.get_sentence_embedding_dimension
        dimensions = int(dimension_getter())
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
    checkpoint_size: int = DEFAULT_EMBEDDING_CHECKPOINT_SIZE,
    initialize_vector_backend: bool = True,
    repository_ids: set[str] | None = None,
    log: LogCallback | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, object]:
    if batch_size < 1 or batch_size > 256:
        raise ValueError("batch_size deve estar entre 1 e 256")
    if checkpoint_size < 1 or checkpoint_size > 16384:
        raise ValueError("checkpoint_size deve estar entre 1 e 16384")
    effective_checkpoint_size = max(batch_size, checkpoint_size)
    logger = log or (lambda _message, _level="info": None)
    if initialize_vector_backend:
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
              AND (
                  %s::text[] IS NULL
                  OR document.repository_id = ANY(%s::text[])
              )
            ORDER BY chunk.chunk_id
            """,
            (
                profile_id,
                sorted(repository_ids) if repository_ids is not None else None,
                sorted(repository_ids) if repository_ids is not None else None,
            ),
        )
        missing = [dict(row) for row in cursor.fetchall()]
        reused_row = connection.execute(
            """
            SELECT count(*) AS embeddings_count
            FROM mflab_knowledge.chunk_embeddings AS embedding
            JOIN mflab_knowledge.chunks AS chunk
              ON chunk.chunk_id = embedding.chunk_id
            JOIN mflab_knowledge.documents AS document
              ON document.document_id = chunk.document_id
            WHERE embedding.model_id = %s
              AND (
                  %s::text[] IS NULL
                  OR document.repository_id = ANY(%s::text[])
              )
            """,
            (
                profile_id,
                sorted(repository_ids) if repository_ids is not None else None,
                sorted(repository_ids) if repository_ids is not None else None,
            ),
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
            "checkpoints": 0,
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
    checkpoints = 0
    logger(
        f"Processando {total} embeddings pendentes em checkpoints de até "
        f"{effective_checkpoint_size}",
        "info",
    )
    for checkpoint_start in range(0, total, effective_checkpoint_size):
        checkpoint = missing[
            checkpoint_start : checkpoint_start + effective_checkpoint_size
        ]
        checkpoint_rows: list[tuple[object, ...]] = []
        for batch_start in range(0, len(checkpoint), batch_size):
            batch = checkpoint[batch_start : batch_start + batch_size]
            vectors = embedder.encode_documents(
                [_embedding_text(row) for row in batch],
                batch_size=batch_size,
            )
            checkpoint_rows.extend(
                (row["chunk_id"], embedder.profile_id, vector)
                for row, vector in zip(batch, vectors, strict=True)
            )
            if progress is not None:
                progress(
                    checkpoint_start + batch_start + len(batch),
                    total,
                    str(batch[-1]["path"]),
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
                checkpoint_rows,
            )
        embedded += len(checkpoint)
        checkpoints += 1

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
        "checkpoints": checkpoints,
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


def _context_hints(
    seeds: list[dict[str, object]],
    policy: RetrievalPolicy | None = None,
) -> tuple[
    dict[str, tuple[str, int, int]],
    dict[str, tuple[str, int, int]],
    dict[str, int],
]:
    active_policy = policy or RetrievalPolicy()
    paths: dict[str, tuple[str, int, int]] = {}
    directory_prefixes: dict[str, tuple[str, int, int]] = {}
    symbols: dict[str, int] = {}
    seed_path_counts: Counter[str] = Counter(
        str(seed.get("path", "")) for seed in seeds if seed.get("path")
    )

    def add_path(path: PurePosixPath, reason: str, rank: int, strength: int) -> None:
        value = path.as_posix()
        current = paths.get(value)
        option = (reason, rank, strength)
        if current is None or (-strength, rank) < (-current[2], current[1]):
            paths[value] = option

    for source_rank, seed in enumerate(seeds, start=1):
        raw_path = str(seed.get("path", ""))
        if raw_path:
            path = PurePosixPath(raw_path)
            suffix = path.suffix.casefold()
            if suffix in active_policy.same_document_extensions and seed_path_counts[
                raw_path
            ] >= active_policy.same_document_min_hits:
                add_path(
                    path,
                    "same_document",
                    source_rank,
                    active_policy.same_document_strength,
                )
            for paired_suffix in _PAIRED_EXTENSIONS.get(suffix, ()):
                add_path(
                    path.with_suffix(paired_suffix),
                    "paired_source",
                    source_rank,
                    active_policy.paired_source_strength,
                )

        searchable = f"{seed.get('title', '')}\n{seed.get('text', '')}"
        for identifier in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", searchable):
            if len(identifier) < 10:
                continue
            if not any(char.islower() for char in identifier):
                continue
            if sum(char.isupper() for char in identifier) < 2:
                continue
            previous_rank = symbols.get(identifier)
            if previous_rank is None or source_rank < previous_rank:
                symbols[identifier] = source_rank

    ancestor_documents: dict[str, dict[str, int]] = {}
    for source_rank, seed in enumerate(seeds, start=1):
        raw_path = str(seed.get("path", ""))
        path = PurePosixPath(raw_path)
        if path.suffix.casefold() not in active_policy.directory_extensions:
            continue
        for parent in path.parents:
            parts = parent.parts
            if not parts or parent.as_posix() == ".":
                continue
            root = parent.as_posix()
            documents = ancestor_documents.setdefault(root, {})
            documents[raw_path] = min(source_rank, documents.get(raw_path, source_rank))

    qualifying = {
        root: documents
        for root, documents in ancestor_documents.items()
        if len(documents) >= active_policy.directory_min_documents
        and (
            not active_policy.directory_require_root_document
            or any(PurePosixPath(path).parent.as_posix() == root for path in documents)
        )
    }
    deepest = {
        root: documents
        for root, documents in qualifying.items()
        if not any(
            other != root and other.startswith(root + "/") for other in qualifying
        )
    }
    for root, documents in deepest.items():
        directory_prefixes[root] = (
            "directory_bundle",
            min(documents.values()),
            active_policy.directory_strength,
        )

    ordered_symbols = sorted(symbols, key=lambda value: (symbols[value], -len(value)))
    limited_symbols = {
        value: symbols[value]
        for value in ordered_symbols[: active_policy.symbol_hints_limit]
    }
    return paths, directory_prefixes, limited_symbols


def _line_distance(
    candidate: dict[str, object],
    seeds: list[dict[str, object]],
) -> int:
    path = str(candidate.get("path", ""))
    start = int(candidate.get("line_start") or 0)
    end = int(candidate.get("line_end") or start)
    distances: list[int] = []
    for seed in seeds:
        if str(seed.get("path", "")) != path:
            continue
        seed_start = int(seed.get("line_start") or 0)
        seed_end = int(seed.get("line_end") or seed_start)
        if not start or not end or not seed_start or not seed_end:
            continue
        if end < seed_start:
            distances.append(seed_start - end)
        elif seed_end < start:
            distances.append(start - seed_end)
        else:
            distances.append(0)
    return min(distances, default=1_000_000_000)


CONTEXT_SEARCH_SQL = """
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
      AND (%(branch)s::text IS NULL OR occurrence.branch = %(branch)s::text)
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
      AND (%(branch)s::text IS NULL OR occurrence.branch = %(branch)s::text)
) AS occurrences ON true
WHERE embedding.model_id = %(profile)s
  AND document.access_class = ANY(%(allowed_access)s::text[])
  AND (%(project)s::text IS NULL OR repository.project = %(project)s::text)
  AND (
      %(path_prefix)s::text IS NULL
      OR document.path LIKE %(path_prefix)s::text || '%%'
  )
  AND NOT (chunk.chunk_id = ANY(%(seed_chunk_ids)s::text[]))
  AND (
      document.path = ANY(%(exact_paths)s::text[])
      OR (
          NOT (document.path = ANY(%(seed_paths)s::text[]))
          AND EXISTS (
              SELECT 1
              FROM unnest(%(directory_prefixes)s::text[]) AS requested(prefix)
              WHERE document.path LIKE requested.prefix || '/%%'
          )
      )
      OR EXISTS (
          SELECT 1
          FROM unnest(%(symbols)s::text[]) AS requested(symbol)
          WHERE strpos(lower(chunk.title), lower(requested.symbol)) > 0
      )
  )
ORDER BY
    CASE
        WHEN document.path = ANY(%(same_document_paths)s::text[]) THEN 0
        WHEN document.path = ANY(%(exact_paths)s::text[]) THEN 1
        WHEN EXISTS (
            SELECT 1
            FROM unnest(%(directory_prefixes)s::text[]) AS requested(prefix)
            WHERE document.path LIKE requested.prefix || '/%%'
        ) THEN 2
        ELSE 3
    END,
    embedding.embedding <=> %(query_embedding)s
LIMIT %(context_candidate_limit)s
"""


def _contextual_search(
    database_url: str,
    embedder: LocalEmbedder,
    *,
    query: str,
    seeds: list[dict[str, object]],
    branch: str | None,
    project: str | None,
    path_prefix: str | None,
    allowed_access: set[str],
    policy: RetrievalPolicy | None = None,
) -> list[dict[str, object]]:
    active_policy = policy or RetrievalPolicy()
    if active_policy.max_context_results == 0:
        return []
    path_hints, directory_hints, symbol_hints = _context_hints(
        seeds,
        active_policy,
    )
    if not path_hints and not directory_hints and not symbol_hints:
        return []
    query_embedding = embedder.encode_query(query)
    _psycopg, dict_row = _driver()
    with _connect(database_url, row_factory=dict_row) as connection:
        embedder.register_vector(connection)
        rows = connection.execute(
            CONTEXT_SEARCH_SQL,
            {
                "query_embedding": query_embedding,
                "profile": embedder.profile_id,
                "branch": branch,
                "project": project,
                "path_prefix": path_prefix,
                "allowed_access": sorted(allowed_access),
                "seed_chunk_ids": sorted(
                    {
                        str(seed.get("chunk_id", ""))
                        for seed in seeds
                        if seed.get("chunk_id")
                    }
                ),
                "seed_paths": sorted(
                    {str(seed.get("path", "")) for seed in seeds if seed.get("path")}
                ),
                "exact_paths": sorted(path_hints),
                "same_document_paths": sorted(
                    path
                    for path, hint in path_hints.items()
                    if hint[0] == "same_document"
                ),
                "directory_prefixes": sorted(directory_hints),
                "symbols": list(symbol_hints),
                "context_candidate_limit": active_policy.context_candidate_limit,
            },
        ).fetchall()
    candidates = _rows_to_results(rows)
    annotated: list[tuple[int, int, int, float, dict[str, object]]] = []
    for candidate in candidates:
        path = str(candidate.get("path", ""))
        hint = path_hints.get(path)
        context_group: str
        if hint is not None:
            reason, source_rank, strength = hint
            context_group = f"{reason}:{path}"
        else:
            matching_directories = [
                (root, value)
                for root, value in directory_hints.items()
                if path.startswith(root + "/")
            ]
            if matching_directories:
                root, (reason, source_rank, strength) = max(
                    matching_directories,
                    key=lambda value: len(PurePosixPath(value[0]).parts),
                )
                context_group = f"{reason}:{root}"
            else:
                searchable = (
                    f"{candidate.get('title', '')}\n{candidate.get('text', '')}"
                ).casefold()
                matched = [
                    (rank, symbol)
                    for symbol, rank in symbol_hints.items()
                    if symbol.casefold() in searchable
                ]
                if not matched:
                    continue
                source_rank, symbol = min(matched)
                reason = "symbol_reference"
                strength = active_policy.symbol_strength
                context_group = f"{reason}:{symbol.casefold()}"
        candidate["context_relation"] = reason
        candidate["context_source_rank"] = source_rank
        candidate["context_group"] = context_group
        structural_distance = (
            _line_distance(candidate, seeds)
            if reason == "same_document"
            else 1_000_000_000
        )
        annotated.append(
            (
                -strength,
                source_rank,
                structural_distance,
                -float(candidate.get("score", 0.0)),
                candidate,
            )
        )
    annotated.sort(key=lambda value: value[:4])
    selected: list[tuple[int, int, int, float, dict[str, object]]] = []
    used_groups: set[str] = set()
    used_paths: set[str] = set()
    used_hashes: set[str] = set()
    for value in annotated:
        candidate = value[4]
        group = str(candidate.get("context_group", ""))
        path = str(candidate.get("path", ""))
        chunk_hash = str(candidate.get("chunk_hash", ""))
        if group in used_groups or path in used_paths:
            continue
        if chunk_hash and chunk_hash in used_hashes:
            continue
        selected.append(value)
        used_groups.add(group)
        used_paths.add(path)
        if chunk_hash:
            used_hashes.add(chunk_hash)
        if len(selected) >= active_policy.max_context_results:
            break

    results: list[dict[str, object]] = []
    for context_rank, (
        _strength,
        _source_rank,
        _distance,
        _score,
        candidate,
    ) in enumerate(
        selected,
        start=1,
    ):
        candidate["context_rank"] = context_rank
        results.append(candidate)
    return results


def _interleave_context(
    baseline: list[dict[str, object]],
    contextual: list[dict[str, object]],
    *,
    limit: int,
    max_per_path: int,
    include_duplicate_content: bool,
) -> list[dict[str, object]]:
    by_source: dict[int, list[dict[str, object]]] = {}
    for candidate in contextual:
        source_rank = int(candidate.get("context_source_rank", 0))
        if source_rank > 0:
            by_source.setdefault(source_rank, []).append(candidate)

    results: list[dict[str, object]] = []
    paths: Counter[str] = Counter()
    seen_content: set[str] = set()
    seen_chunks: set[str] = set()

    def append(candidate: dict[str, object]) -> None:
        chunk_id = str(candidate.get("chunk_id", ""))
        path = str(candidate.get("path", ""))
        chunk_hash = str(candidate.get("chunk_hash", ""))
        if chunk_id in seen_chunks or paths[path] >= max_per_path:
            return
        if not include_duplicate_content and chunk_hash in seen_content:
            return
        results.append(candidate)
        seen_chunks.add(chunk_id)
        paths[path] += 1
        if chunk_hash:
            seen_content.add(chunk_hash)

    for source_rank, candidate in enumerate(baseline, start=1):
        append(candidate)
        source_score = float(candidate.get("rrf_score", 0.0))
        for context_position, related in enumerate(
            by_source.get(source_rank, []),
            start=1,
        ):
            related["rrf_score"] = max(
                0.0,
                source_score - context_position * 0.000001,
            )
            append(related)
        if len(results) >= limit:
            break
    return results[:limit]


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
    retrieval_policy: RetrievalPolicy | None = None,
) -> list[dict[str, object]]:
    active_policy = retrieval_policy or RetrievalPolicy()
    effective_access = allowed_access if allowed_access is not None else {"public"}
    if not effective_access or not effective_access.issubset(
        RETRIEVABLE_ACCESS_CLASSES
    ):
        raise ValueError("filtro de acesso inválido ou vazio")
    candidate_limit = min(100, max(limit * 10, 50))
    broad_per_path = min(100, max(max_per_path, 10))
    common = {
        "query": query,
        "limit": candidate_limit,
        "branch": branch,
        "project": project,
        "path_prefix": path_prefix,
        "allowed_access": effective_access,
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
    baseline = _diversify(
        sorted(
            combined.values(),
            key=lambda value: (
                -float(value.get("rrf_score", 0.0)),
                str(value.get("path", "")),
            ),
        ),
        limit=candidate_limit,
        max_per_path=max_per_path,
        include_duplicate_content=include_duplicate_content,
    )
    seeds = baseline[:limit]
    contextual = _contextual_search(
        database_url,
        embedder,
        query=query,
        seeds=seeds,
        branch=branch,
        project=project,
        path_prefix=path_prefix,
        allowed_access=effective_access,
        policy=active_policy,
    )
    ranked = _interleave_context(
        baseline,
        contextual,
        limit=limit,
        max_per_path=max_per_path,
        include_duplicate_content=include_duplicate_content,
    )
    for value in ranked:
        value["score"] = round(float(value["rrf_score"]), 6)
    return ranked


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


def hybrid_fingerprint(
    database_url: str,
    embedder: LocalEmbedder,
    retrieval_policy: RetrievalPolicy | None = None,
) -> dict[str, object]:
    active_policy = retrieval_policy or RetrievalPolicy()
    fingerprint = database_fingerprint(database_url)
    fingerprint["mode"] = "hybrid"
    fingerprint["retrieval_algorithm"] = "structural_context_v2"
    fingerprint["retrieval_policy"] = active_policy.fingerprint()
    fingerprint["embedding_model"] = embedder.model_id
    fingerprint["embedding_revision"] = embedder.revision
    fingerprint["embedding_profile"] = embedder.profile_id
    fingerprint["embedding_dimensions"] = EMBEDDING_DIMENSIONS
    fingerprint["embedding_status"] = embedding_status(database_url)
    return fingerprint
