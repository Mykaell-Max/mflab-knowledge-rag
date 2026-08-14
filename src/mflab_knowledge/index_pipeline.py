from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from mflab_knowledge.credentials import GitCredentials
from mflab_knowledge.database import initialize_database, initialize_vector_database, load_corpus
from mflab_knowledge.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_REVISION,
    DEFAULT_MAX_SEQUENCE_LENGTH,
    embed_database,
)
from mflab_knowledge.inventory import write_json, write_yaml
from mflab_knowledge.multi_sync import sync_all_repositories
from mflab_knowledge.normalize import normalize_manifest
from mflab_knowledge.repository_config import RepositoryCatalog

LogCallback = Callable[[str, str], None]
ProgressCallback = Callable[[int, int, str], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _format_duration(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}min {seconds:02d}s"
    if minutes:
        return f"{minutes}min {seconds:02d}s"
    return f"{seconds}s"


def _safe_error(error: Exception, database_url: str) -> str:
    message = str(error).replace(database_url, "<MFLAB_DATABASE_URL>")
    password = urlsplit(database_url).password
    if password:
        message = message.replace(password, "***")
    return message


def index_all_repositories(
    *,
    catalog: RepositoryCatalog,
    database_url: str,
    refresh_remote: bool = True,
    credentials: GitCredentials | None = None,
    repository_ids: set[str] | None = None,
    fail_fast: bool = False,
    include_embeddings: bool = True,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    embedding_revision: str = DEFAULT_EMBEDDING_REVISION,
    device: str = "auto",
    max_sequence_length: int = DEFAULT_MAX_SEQUENCE_LENGTH,
    batch_size: int = 4,
    log: LogCallback | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, object]:
    """Run the read-only source-to-RAG pipeline for configured repositories."""

    started = time.monotonic()
    logger = log or (lambda _message, _level="info": None)
    known_ids = {repository.id for repository in catalog.repositories}
    if repository_ids is not None:
        unknown = sorted(repository_ids - known_ids)
        if unknown:
            raise ValueError("repositórios desconhecidos: " + ", ".join(unknown))
    selected = [
        repository
        for repository in catalog.enabled
        if repository_ids is None or repository.id in repository_ids
    ]
    if not selected:
        raise ValueError("nenhum repositório habilitado foi selecionado")

    logger("PRÉ-REQUISITOS DO PIPELINE", "section")
    try:
        if include_embeddings:
            vector_state = initialize_vector_database(database_url, log=logger)
        else:
            vector_state = None
            initialize_database(database_url, log=logger)
    except Exception as exc:
        raise ValueError(_safe_error(exc, database_url)) from exc

    logger("SINCRONIZAÇÃO DAS FONTES", "section")
    synchronization = sync_all_repositories(
        catalog=catalog,
        refresh_remote=refresh_remote,
        credentials=credentials,
        repository_ids=repository_ids,
        fail_fast=fail_fast,
        log=logger,
        progress=progress,
    )
    synchronized_entries = synchronization.get("repositories", [])
    if not isinstance(synchronized_entries, list):
        raise ValueError("resultado interno da sincronização é incompatível")

    repository_entries: list[dict[str, object]] = []
    loaded_repositories = 0
    loaded_database_ids: set[str] = set()
    for position, synchronized in enumerate(synchronized_entries, start=1):
        if not isinstance(synchronized, dict):
            continue
        repository_id = str(synchronized.get("id", ""))
        project = str(synchronized.get("project", repository_id))
        sync_status = str(synchronized.get("status", "failed"))
        entry: dict[str, object] = {
            "id": repository_id,
            "project": project,
            "status": sync_status,
            "synchronization": synchronized,
        }
        if sync_status == "failed":
            entry["pipeline_status"] = "failed"
            entry["error"] = synchronized.get("error", "sincronização falhou")
            repository_entries.append(entry)
            if fail_fast:
                break
            continue

        repository_started = time.monotonic()
        logger(
            f"NORMALIZAÇÃO E CARGA {position}/{len(synchronized_entries)}  |  "
            f"{project}  |  id={repository_id}",
            "section",
        )

        def repository_log(message: str, level: str = "info") -> None:
            logger(f"[{repository_id}] {message}", level)

        def repository_progress(current: int, total: int, path: str) -> None:
            if progress is not None:
                progress(current, total, f"{repository_id} :: {path}")

        try:
            manifest_path = Path(str(synchronized["manifest"]))
            normalization = normalize_manifest(
                manifest_path=manifest_path,
                output_dir=catalog.normalized_root / repository_id,
                cache_dir=catalog.cache_root / repository_id / "normalization",
                log=repository_log,
                progress=repository_progress if progress is not None else None,
            )
            entry["normalization"] = normalization
            if int(normalization["errors"]):
                entry["pipeline_status"] = "failed"
                entry["error"] = (
                    f"normalização terminou com {normalization['errors']} erros"
                )
                repository_entries.append(entry)
                repository_log(str(entry["error"]), "error")
                if fail_fast:
                    break
                continue

            database = load_corpus(
                database_url,
                documents_path=Path(str(normalization["documents"])),
                chunks_path=Path(str(normalization["chunks"])),
                log=repository_log,
            )
            entry["database"] = database
            entry["pipeline_status"] = (
                "warning" if sync_status == "warning" else "success"
            )
            entry["duration_seconds"] = round(
                time.monotonic() - repository_started,
                3,
            )
            loaded_repositories += 1
            loaded_database_ids.add(str(database["repository_id"]))
            repository_log(
                f"Corpus {'reutilizado' if database['reused'] else 'atualizado'} "
                f"em {_format_duration(time.monotonic() - repository_started)}: "
                f"{database['documents']} documentos, {database['chunks']} chunks",
                "success",
            )
        except Exception as exc:
            safe_message = _safe_error(exc, database_url)
            entry["pipeline_status"] = "failed"
            entry["error"] = safe_message
            repository_log(
                f"Falhou após {_format_duration(time.monotonic() - repository_started)}: "
                f"{safe_message}",
                "error",
            )
            repository_entries.append(entry)
            if fail_fast:
                break
            continue
        repository_entries.append(entry)

    embedding_result: dict[str, object]
    if not include_embeddings:
        embedding_result = {"status": "skipped", "reason": "disabled"}
    elif loaded_repositories == 0:
        embedding_result = {"status": "skipped", "reason": "no_loaded_repository"}
        logger("Embeddings ignorados: nenhum corpus foi carregado", "warning")
    else:
        logger("EMBEDDINGS INCREMENTAIS", "section")
        embedding_started = time.monotonic()
        try:
            embedded = embed_database(
                database_url,
                model_id=embedding_model,
                revision=embedding_revision,
                device=device,
                max_sequence_length=max_sequence_length,
                batch_size=batch_size,
                initialize_vector_backend=False,
                repository_ids=loaded_database_ids,
                log=logger,
                progress=progress,
            )
            embedding_result = {
                "status": "success",
                **embedded,
                "duration_seconds": round(time.monotonic() - embedding_started, 3),
            }
        except Exception as exc:
            safe_message = _safe_error(exc, database_url)
            embedding_result = {"status": "failed", "error": safe_message}
            logger(f"Embeddings falharam: {safe_message}", "error")

    succeeded = sum(
        entry.get("pipeline_status") == "success" for entry in repository_entries
    )
    warnings = sum(
        entry.get("pipeline_status") == "warning" for entry in repository_entries
    )
    failed = sum(
        entry.get("pipeline_status") == "failed" for entry in repository_entries
    )
    if embedding_result["status"] == "failed":
        failed += 1
    elapsed_seconds = time.monotonic() - started
    manifest: dict[str, object] = {
        "schema_version": "0.1",
        "generated_at": _utc_now(),
        "config_file": str(catalog.path),
        "config_hash": catalog.config_hash,
        "database": {"configured": True, "credentials_recorded": False},
        "vector_backend": vector_state,
        "summary": {
            "selected": len(selected),
            "processed": len(repository_entries),
            "succeeded": succeeded,
            "warnings": warnings,
            "failed": failed,
            "loaded_repositories": loaded_repositories,
            "duration_seconds": round(elapsed_seconds, 3),
        },
        "synchronization_manifest": synchronization["manifest"],
        "repositories": repository_entries,
        "embeddings": embedding_result,
    }
    catalog.normalized_root.mkdir(parents=True, exist_ok=True)
    manifest_path = catalog.normalized_root / "index-all.generated.yaml"
    manifest_json_path = catalog.normalized_root / "index-all.generated.json"
    write_yaml(manifest, manifest_path)
    write_json(manifest, manifest_json_path)

    logger("RESUMO DO PIPELINE", "section")
    level = "warning" if failed or warnings else "result"
    logger(
        f"Repositórios: {succeeded} concluídos, {warnings} avisos, "
        f"{failed} falhas  |  carregados={loaded_repositories}  |  "
        f"tempo={_format_duration(elapsed_seconds)}",
        level,
    )
    logger(
        f"Embeddings: status={embedding_result['status']}  |  "
        f"calculados={embedding_result.get('embedded', 0)}  |  "
        f"reutilizados={embedding_result.get('reused', 0)}",
        "error" if embedding_result["status"] == "failed" else "result",
    )
    logger(f"Manifesto do pipeline: {manifest_path}", "detail")
    return {
        "manifest": str(manifest_path),
        "manifest_json": str(manifest_json_path),
        "selected": len(selected),
        "processed": len(repository_entries),
        "succeeded": succeeded,
        "warnings": warnings,
        "failed": failed,
        "loaded_repositories": loaded_repositories,
        "embedding_status": embedding_result["status"],
        "embeddings_built": int(embedding_result.get("embedded", 0)),
        "embeddings_reused": int(embedding_result.get("reused", 0)),
        "duration_seconds": round(elapsed_seconds, 3),
    }
