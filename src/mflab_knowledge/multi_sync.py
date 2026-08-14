from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from mflab_knowledge.credentials import GitCredentials
from mflab_knowledge.inventory import detect_git_metadata, write_json, write_yaml
from mflab_knowledge.repository_config import RepositoryCatalog, RepositoryDefinition
from mflab_knowledge.sync import sync_repository_branches

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


def repository_uses_https(repository: RepositoryDefinition) -> bool:
    if repository.remote_url is not None:
        return urlsplit(repository.remote_url).scheme.casefold() in {"http", "https"}
    assert repository.source is not None
    metadata = detect_git_metadata(repository.source)
    remote_url = metadata.get("remote_url")
    if not isinstance(remote_url, str):
        return False
    return urlsplit(remote_url).scheme.casefold() in {"http", "https"}


def sync_all_repositories(
    *,
    catalog: RepositoryCatalog,
    refresh_remote: bool = True,
    credentials: GitCredentials | None = None,
    repository_ids: set[str] | None = None,
    fail_fast: bool = False,
    log: LogCallback | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, object]:
    synchronization_started = time.monotonic()
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

    catalog.inventory_root.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    branches = 0
    unique_commits = 0
    inventories_built = 0
    inventories_reused = 0
    inventory_errors = 0
    failed = 0

    for position, repository in enumerate(selected, start=1):
        repository_started = time.monotonic()
        logger(
            f"REPOSITÓRIO {position}/{len(selected)}  |  "
            f"{repository.project}  |  id={repository.id}",
            "section",
        )
        logger(
            f"fonte={repository.source_kind}  |  "
            f"branches={repository.branch_scope}  |  "
            f"canônica={repository.canonical_ref}  |  "
            f"perfil={repository.profile}  |  "
            f"modo={'online' if refresh_remote else 'offline'}",
            "info",
        )

        def repository_log(message: str, level: str = "info") -> None:
            logger(f"[{repository.id}] {message}", level)

        output_dir = catalog.inventory_root / repository.id
        cache_dir = catalog.cache_root / repository.id
        try:
            result = sync_repository_branches(
                source=repository.source,
                remote_url=repository.remote_url,
                fetch_timeout_seconds=repository.fetch_timeout_seconds,
                project=repository.project,
                canonical_ref=repository.canonical_ref,
                branch_scope=repository.branch_scope,
                access_class=repository.access_class,
                profile=repository.profile,
                cache_dir=cache_dir,
                output_dir=output_dir,
                include_branches=repository.include_branches,
                exclude_branches=repository.exclude_branches,
                refresh_remote=refresh_remote,
                credentials=(
                    credentials
                    if refresh_remote and repository_uses_https(repository)
                    else None
                ),
                log=repository_log,
                progress=progress,
            )
            result_errors = int(result["errors"])
            status = "warning" if result_errors else "success"
            entries.append(
                {
                    "id": repository.id,
                    "project": repository.project,
                    "status": status,
                    "source": repository.source_label,
                    "source_kind": repository.source_kind,
                    "remote_url": repository.remote_url,
                    "canonical_ref": repository.canonical_ref,
                    "branch_scope": repository.branch_scope,
                    "access_class": repository.access_class,
                    "profile": repository.profile,
                    "include_branches": list(repository.include_branches),
                    "exclude_branches": list(repository.exclude_branches),
                    "fetch_timeout_seconds": repository.fetch_timeout_seconds,
                    **result,
                }
            )
            branches += int(result["branches"])
            unique_commits += int(result["unique_commits"])
            inventories_built += int(result["inventories_built"])
            inventories_reused += int(result["inventories_reused"])
            inventory_errors += result_errors
            repository_log(
                f"Concluído em {_format_duration(time.monotonic() - repository_started)}: "
                f"{result['branches']} branches, {result['unique_commits']} commits, "
                f"cache {result['inventories_reused']} reutilizados / "
                f"{result['inventories_built']} calculados, {result_errors} erros",
                "warning" if result_errors else "success",
            )
        except (OSError, ValueError) as exc:
            failed += 1
            entries.append(
                {
                    "id": repository.id,
                    "project": repository.project,
                    "status": "failed",
                    "source": repository.source_label,
                    "source_kind": repository.source_kind,
                    "remote_url": repository.remote_url,
                    "canonical_ref": repository.canonical_ref,
                    "branch_scope": repository.branch_scope,
                    "access_class": repository.access_class,
                    "profile": repository.profile,
                    "fetch_timeout_seconds": repository.fetch_timeout_seconds,
                    "error": str(exc),
                }
            )
            repository_log(
                f"Falhou após "
                f"{_format_duration(time.monotonic() - repository_started)}: {exc}",
                "error",
            )
            if fail_fast:
                break

    succeeded = sum(entry["status"] == "success" for entry in entries)
    warnings = sum(entry["status"] == "warning" for entry in entries)
    elapsed_seconds = time.monotonic() - synchronization_started
    manifest: dict[str, object] = {
        "schema_version": "0.1",
        "generated_at": _utc_now(),
        "config_file": str(catalog.path),
        "config_hash": catalog.config_hash,
        "cache_root": str(catalog.cache_root),
        "inventory_root": str(catalog.inventory_root),
        "normalized_root": str(catalog.normalized_root),
        "summary": {
            "configured": len(catalog.repositories),
            "enabled": len(catalog.enabled),
            "selected": len(selected),
            "processed": len(entries),
            "succeeded": succeeded,
            "warnings": warnings,
            "failed": failed,
            "branches": branches,
            "unique_commits": unique_commits,
            "inventories_built": inventories_built,
            "inventories_reused": inventories_reused,
            "inventory_errors": inventory_errors,
            "duration_seconds": round(elapsed_seconds, 3),
        },
        "repositories": entries,
    }
    manifest_path = catalog.inventory_root / "manifest.generated.yaml"
    manifest_json_path = catalog.inventory_root / "manifest.generated.json"
    write_yaml(manifest, manifest_path)
    write_json(manifest, manifest_json_path)
    logger("RESUMO DA SINCRONIZAÇÃO", "section")
    logger(
        f"Repositórios: {succeeded} concluídos, {warnings} avisos, "
        f"{failed} falhas  |  tempo={_format_duration(elapsed_seconds)}",
        "warning" if failed or inventory_errors else "result",
    )
    logger(
        f"Conteúdo: {branches} branches, {unique_commits} commits únicos  |  "
        f"inventários={inventories_built} calculados + "
        f"{inventories_reused} reutilizados  |  erros={inventory_errors}",
        "warning" if inventory_errors else "result",
    )
    logger(f"Manifesto: {manifest_path}", "detail")
    return {
        "manifest": str(manifest_path),
        "manifest_json": str(manifest_json_path),
        "configured": len(catalog.repositories),
        "enabled": len(catalog.enabled),
        "selected": len(selected),
        "processed": len(entries),
        "succeeded": succeeded,
        "warnings": warnings,
        "failed": failed,
        "branches": branches,
        "unique_commits": unique_commits,
        "inventories_built": inventories_built,
        "inventories_reused": inventories_reused,
        "inventory_errors": inventory_errors,
        "duration_seconds": round(elapsed_seconds, 3),
    }
