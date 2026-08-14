from __future__ import annotations

import copy
import fnmatch
import hashlib
import json
import re
import shutil
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from mflab_knowledge.credentials import GitCredentials
from mflab_knowledge.inventory import (
    SCHEMA_VERSION,
    build_inventory,
    write_json,
    write_yaml,
)
from mflab_knowledge.repository import (
    RepositoryBranch,
    compare_branch_to_canonical,
    list_repository_branches,
    materialize_repository_snapshot,
    prepare_repository_mirror,
    prepare_remote_repository_mirror,
    resolve_remote_default_branch,
)

LogCallback = Callable[[str, str], None]
ProgressCallback = Callable[[int, int, str], None]
INVENTORY_CACHE_SCHEMA_VERSION = "0.1"
INVENTORY_POLICY_VERSION = "1"


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


def _counter_bar(current: int, total: int, *, width: int = 20) -> str:
    percent = 100 if total == 0 else int(current * 100 / total)
    filled = int(width * percent / 100)
    return f"[{'#' * filled}{'-' * (width - filled)}] {current}/{total}"


def _canonical_name(ref: str) -> str:
    value = ref.strip()
    for prefix in (
        "refs/remotes/origin/",
        "refs/heads/",
        "origin/",
    ):
        if value.startswith(prefix):
            return value.removeprefix(prefix)
    return value


def _path_component(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    sanitized = sanitized or "branch"
    if sanitized != value:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
        return f"{sanitized}--{digest}"
    return sanitized


def _catalog_path(output_dir: Path, branch_name: str) -> Path:
    components = [_path_component(part) for part in branch_name.split("/")]
    return output_dir / "branches" / Path(*components[:-1]) / f"{components[-1]}.generated.yaml"


def _catalog_json_path(output_dir: Path, branch_name: str) -> Path:
    components = [_path_component(part) for part in branch_name.split("/")]
    return (
        output_dir
        / "branches"
        / Path(*components[:-1])
        / f"{components[-1]}.generated.json"
    )


def _branch_selected(
    name: str,
    *,
    include: tuple[str, ...],
    exclude: tuple[str, ...],
) -> bool:
    if include and not any(fnmatch.fnmatchcase(name, pattern) for pattern in include):
        return False
    return not any(fnmatch.fnmatchcase(name, pattern) for pattern in exclude)


def _inventory_cache_descriptor(
    *,
    repository: str,
    project: str,
    commit_sha: str,
    access_class: str,
    profile: str,
) -> dict[str, str]:
    return {
        "cache_schema_version": INVENTORY_CACHE_SCHEMA_VERSION,
        "inventory_schema_version": SCHEMA_VERSION,
        "policy_version": INVENTORY_POLICY_VERSION,
        "repository": repository,
        "project": project,
        "commit_sha": commit_sha,
        "access_class": access_class,
        "profile": profile,
    }


def _inventory_cache_path(
    cache_dir: Path,
    descriptor: dict[str, str],
) -> Path:
    serialized = json.dumps(
        descriptor,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    fingerprint = hashlib.sha256(serialized).hexdigest()
    commit_sha = descriptor["commit_sha"]
    return (
        cache_dir.expanduser().resolve()
        / "inventories"
        / commit_sha
        / f"{fingerprint}.json"
    )


def _load_cached_inventory(
    path: Path,
    descriptor: dict[str, str],
) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("descriptor") != descriptor:
        return None
    inventory = payload.get("inventory")
    if not isinstance(inventory, dict):
        return None
    source_metadata = inventory.get("source")
    if not isinstance(source_metadata, dict):
        return None
    if source_metadata.get("commit_sha") != descriptor["commit_sha"]:
        return None
    return inventory


def _write_cached_inventory(
    path: Path,
    descriptor: dict[str, str],
    inventory: dict[str, object],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"descriptor": descriptor, "inventory": inventory}
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(serialized)
            handle.write("\n")
        Path(temporary_name).replace(path)
    finally:
        if temporary_name is not None:
            temporary_path = Path(temporary_name)
            if temporary_path.exists():
                temporary_path.unlink()


def _render_tree(project: str, entries: list[dict[str, object]]) -> str:
    root: dict[str, object] = {}
    for entry in entries:
        node = root
        for component in str(entry["name"]).split("/"):
            children = node.setdefault("_children", {})
            assert isinstance(children, dict)
            node = children.setdefault(component, {})
            assert isinstance(node, dict)
        node["_entry"] = entry

    def contains_canonical(node: dict[str, object]) -> bool:
        entry = node.get("_entry")
        if isinstance(entry, dict) and bool(entry.get("canonical")):
            return True
        children = node.get("_children", {})
        if not isinstance(children, dict):
            return False
        return any(
            contains_canonical(child)
            for child in children.values()
            if isinstance(child, dict)
        )

    def label(name: str, node: dict[str, object]) -> str:
        entry = node.get("_entry")
        if not isinstance(entry, dict):
            return name
        marker = "★ " if entry.get("canonical") else ""
        suffix = " [canônica]" if entry.get("canonical") else ""
        reused = " [commit compartilhado]" if entry.get("shared_commit") else ""
        distance = f"  ↑{entry['ahead']} ↓{entry['behind']}"
        return (
            f"{marker}{name}  {str(entry['commit_sha'])[:12]}"
            f"{distance}  ({entry['indexable_files']} indexáveis){suffix}{reused}"
        )

    lines = [
        f"{project} — {len(entries)} branches, "
        f"{len({str(entry['commit_sha']) for entry in entries})} commits"
    ]

    def walk(node: dict[str, object], prefix: str) -> None:
        children = node.get("_children", {})
        if not isinstance(children, dict):
            return
        ordered = sorted(
            (
                (name, child)
                for name, child in children.items()
                if isinstance(child, dict)
            ),
            key=lambda item: (
                not contains_canonical(item[1]),
                item[0].casefold(),
            ),
        )
        for index, (name, child) in enumerate(ordered):
            last = index == len(ordered) - 1
            connector = "└── " if last else "├── "
            lines.append(f"{prefix}{connector}{label(name, child)}")
            walk(child, f"{prefix}{'    ' if last else '│   '}")

    walk(root, "")
    return "\n".join(lines) + "\n"


def sync_repository_branches(
    *,
    source: Path | None,
    remote_url: str | None = None,
    fetch_timeout_seconds: int = 1800,
    project: str,
    canonical_ref: str,
    branch_scope: str,
    access_class: str,
    profile: str,
    cache_dir: Path,
    output_dir: Path,
    include_branches: tuple[str, ...] = (),
    exclude_branches: tuple[str, ...] = (),
    refresh_remote: bool = True,
    credentials: GitCredentials | None = None,
    log: LogCallback | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, object]:
    synchronization_started = time.monotonic()
    logger = log or (lambda _message, _level="info": None)
    destination = output_dir.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)

    if (source is None) == (remote_url is None):
        raise ValueError("informe exatamente uma origem: source ou remote_url")
    if remote_url is not None:
        mirror = prepare_remote_repository_mirror(
            remote_url,
            project=project,
            cache_dir=cache_dir,
            refresh_remote=refresh_remote,
            credentials=credentials,
            fetch_timeout_seconds=fetch_timeout_seconds,
            log=logger,
        )
    else:
        assert source is not None
        mirror = prepare_repository_mirror(
            source,
            project=project,
            cache_dir=cache_dir,
            refresh_remote=refresh_remote,
            credentials=credentials,
            fetch_timeout_seconds=fetch_timeout_seconds,
            log=logger,
        )
    discovered_branches = list_repository_branches(mirror, scope=branch_scope)
    if canonical_ref == "remote_default":
        canonical_name = resolve_remote_default_branch(
            mirror,
            refresh_remote=refresh_remote,
            credentials=credentials,
        )
        logger(f"Branch canônica descoberta pelo remote: {canonical_name}", "success")
    else:
        canonical_name = _canonical_name(canonical_ref)
    canonical = next(
        (branch for branch in discovered_branches if branch.name == canonical_name),
        None,
    )
    if canonical is None:
        available = ", ".join(branch.name for branch in discovered_branches[:20])
        raise ValueError(
            f"branch canônica não descoberta: {canonical_ref}. Disponíveis: {available}"
        )

    branches = [
        branch
        for branch in discovered_branches
        if branch.name == canonical.name
        or _branch_selected(
            branch.name,
            include=include_branches,
            exclude=exclude_branches,
        )
    ]

    ordered_branches = [canonical] + [
        branch for branch in branches if branch.name != canonical.name
    ]
    logger(
        f"Descobertas {len(discovered_branches)} branches; "
        f"selecionadas {len(ordered_branches)}; canônica: {canonical.name}",
        "success",
    )

    # This directory is generated exclusively by this command. Rebuilding it
    # prevents catalogs for deleted/renamed branches from surviving a sync.
    branches_directory = (destination / "branches").resolve()
    if branches_directory.parent != destination:
        raise ValueError("diretório de catálogos fora do destino de sincronização")
    if branches_directory.exists():
        logger(
            "Removendo catálogos obsoletos da sincronização anterior",
            "warning",
        )
        shutil.rmtree(branches_directory)

    commit_counts = Counter(branch.commit_sha for branch in ordered_branches)
    inventories_by_commit: dict[str, dict[str, object]] = {}
    snapshot_roots: dict[str, Path] = {}
    entries: list[dict[str, object]] = []
    inventories_built = 0
    inventories_reused = 0
    repository_identity = mirror.remote_url or str(mirror.source_path)
    last_branch_progress_bucket = -1

    for index, branch in enumerate(ordered_branches, start=1):
        branch_started = time.monotonic()
        branch_percent = int(index * 100 / len(ordered_branches))
        branch_progress_bucket = branch_percent // 5
        if (
            index == 1
            or index == len(ordered_branches)
            or branch_progress_bucket != last_branch_progress_bucket
        ):
            logger(
                f"Branches {_counter_bar(index, len(ordered_branches))}  "
                f"{branch.name} @ {branch.commit_sha[:12]}"
            )
            last_branch_progress_bucket = branch_progress_bucket
        if branch.commit_sha in inventories_by_commit:
            inventory = copy.deepcopy(inventories_by_commit[branch.commit_sha])
            cache_status = "same_run"
        else:
            snapshot = materialize_repository_snapshot(
                mirror,
                project=project,
                cache_dir=cache_dir,
                ref=branch.requested_ref,
                log=logger,
            )
            snapshot_roots[branch.commit_sha] = snapshot.path
            descriptor = _inventory_cache_descriptor(
                repository=repository_identity,
                project=project,
                commit_sha=branch.commit_sha,
                access_class=access_class,
                profile=profile,
            )
            inventory_cache = _inventory_cache_path(cache_dir, descriptor)
            cached_inventory = _load_cached_inventory(inventory_cache, descriptor)
            if cached_inventory is not None:
                inventory = copy.deepcopy(cached_inventory)
                cache_status = "persistent"
                inventories_reused += 1
                logger(
                    f"Reutilizando inventário {branch.commit_sha[:12]} "
                    f"({profile}, {access_class})",
                    "cache",
                )
            else:
                if inventory_cache.exists():
                    logger(
                        f"Cache de inventário inválido para "
                        f"{branch.commit_sha[:12]}; recalculando",
                        "warning",
                    )
                def branch_progress(current: int, total: int, path: str) -> None:
                    if progress is not None:
                        progress(current, total, f"{branch.name} :: {path}")

                inventory = build_inventory(
                    source=snapshot.path,
                    project=project,
                    access_class=access_class,
                    profile=profile,
                    metadata_override=snapshot.metadata(),
                    progress=branch_progress if progress is not None else None,
                )
                cache_status = "built"
                inventories_built += 1
                summary = inventory.get("summary")
                inventory_errors = (
                    int(summary.get("errors", 0))
                    if isinstance(summary, dict)
                    else 1
                )
                if inventory_errors == 0:
                    _write_cached_inventory(
                        inventory_cache,
                        descriptor,
                        inventory,
                    )
                    logger(
                        f"Inventário armazenado em cache "
                        f"{branch.commit_sha[:12]}",
                        "success",
                    )
                else:
                    logger(
                        f"Inventário {branch.commit_sha[:12]} contém erros e "
                        "não será reutilizado",
                        "warning",
                    )
            inventories_by_commit[branch.commit_sha] = copy.deepcopy(inventory)

        source_metadata = inventory["source"]
        assert isinstance(source_metadata, dict)
        source_metadata.update(
            {
                "branch": branch.name,
                "requested_ref": branch.requested_ref,
                "canonical": branch.name == canonical.name,
                "branch_scope": branch.scope,
                "snapshot_root": str(snapshot_roots[branch.commit_sha]),
            }
        )

        catalog = _catalog_path(destination, branch.name)
        catalog_json = _catalog_json_path(destination, branch.name)
        write_yaml(inventory, catalog)
        write_json(inventory, catalog_json)
        summary = inventory["summary"]
        assert isinstance(summary, dict)
        relation = compare_branch_to_canonical(
            mirror,
            canonical_commit=canonical.commit_sha,
            branch_commit=branch.commit_sha,
        )
        entry: dict[str, object] = {
            "name": branch.name,
            "requested_ref": branch.requested_ref,
            "scope": branch.scope,
            "canonical": branch.name == canonical.name,
            "commit_sha": branch.commit_sha,
            "snapshot_hash": source_metadata["snapshot_hash"],
            "catalog": catalog.relative_to(destination).as_posix(),
            "catalog_json": catalog_json.relative_to(destination).as_posix(),
            "discovered_files": summary["discovered_files"],
            "indexable_files": summary["indexable_files"],
            "excluded_files": summary["excluded_files"],
            "errors": summary["errors"],
            "shared_commit": commit_counts[branch.commit_sha] > 1,
            "reused_inventory": cache_status != "built",
            "inventory_cache": cache_status,
            **relation,
        }
        entries.append(entry)
        logger(
            f"Branch concluída em "
            f"{_format_duration(time.monotonic() - branch_started)}; "
            f"{summary['indexable_files']} arquivos indexáveis; "
            f"cache={cache_status}",
            "detail",
        )

    tree = _render_tree(project, entries)
    tree_path = destination / "branches.generated.txt"
    tree_path.write_text(tree, encoding="utf-8", newline="\n")

    total_errors = sum(int(entry["errors"]) for entry in entries)
    elapsed_seconds = time.monotonic() - synchronization_started
    manifest: dict[str, object] = {
        "schema_version": "0.3",
        "generated_at": _utc_now(),
        "project": project,
        "source": remote_url or str(mirror.source_path),
        "source_kind": "remote_url" if remote_url is not None else "local_worktree",
        "remote_url": mirror.remote_url,
        "canonical_branch": canonical.name,
        "canonical_ref": canonical.requested_ref,
        "canonical_policy": canonical_ref,
        "branch_scope": branch_scope,
        "fetch_timeout_seconds": fetch_timeout_seconds,
        "branch_filters": {
            "include": list(include_branches),
            "exclude": list(exclude_branches),
            "canonical_always_included": True,
        },
        "summary": {
            "branches_discovered": len(discovered_branches),
            "branches_filtered": len(discovered_branches) - len(entries),
            "branches": len(entries),
            "unique_commits": len(inventories_by_commit),
            "catalogs": len(entries),
            "inventories_built": inventories_built,
            "inventories_reused": inventories_reused,
            "errors": total_errors,
            "duration_seconds": round(elapsed_seconds, 3),
        },
        "branches": entries,
    }
    manifest_path = destination / "manifest.generated.yaml"
    manifest_json_path = destination / "manifest.generated.json"
    write_yaml(manifest, manifest_path)
    write_json(manifest, manifest_json_path)
    elapsed = _format_duration(elapsed_seconds)
    logger(f"Árvore de branches gravada em {tree_path}", "result")
    logger(
        f"Repositório sincronizado em {elapsed}: {len(entries)} branches, "
        f"{len(inventories_by_commit)} commits únicos, "
        f"{inventories_built} inventários calculados, "
        f"{inventories_reused} reutilizados, {total_errors} erros",
        "warning" if total_errors else "result",
    )
    return {
        "output_dir": str(destination),
        "manifest": str(manifest_path),
        "manifest_json": str(manifest_json_path),
        "tree": str(tree_path),
        "branches": len(entries),
        "branches_discovered": len(discovered_branches),
        "branches_filtered": len(discovered_branches) - len(entries),
        "unique_commits": len(inventories_by_commit),
        "inventories_built": inventories_built,
        "inventories_reused": inventories_reused,
        "errors": total_errors,
        "duration_seconds": round(elapsed_seconds, 3),
    }
