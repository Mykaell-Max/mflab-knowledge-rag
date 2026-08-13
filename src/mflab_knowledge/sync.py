from __future__ import annotations

import copy
import hashlib
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from mflab_knowledge.credentials import GitCredentials
from mflab_knowledge.inventory import build_inventory, write_yaml
from mflab_knowledge.repository import (
    RepositoryBranch,
    compare_branch_to_canonical,
    list_repository_branches,
    materialize_repository_snapshot,
    prepare_repository_mirror,
)

LogCallback = Callable[[str, str], None]
ProgressCallback = Callable[[int, int, str], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    source: Path,
    project: str,
    canonical_ref: str,
    branch_scope: str,
    access_class: str,
    profile: str,
    cache_dir: Path,
    output_dir: Path,
    refresh_remote: bool = True,
    credentials: GitCredentials | None = None,
    log: LogCallback | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, object]:
    logger = log or (lambda _message, _level="info": None)
    destination = output_dir.expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)

    mirror = prepare_repository_mirror(
        source,
        project=project,
        cache_dir=cache_dir,
        refresh_remote=refresh_remote,
        credentials=credentials,
        log=logger,
    )
    branches = list_repository_branches(mirror, scope=branch_scope)
    canonical_name = _canonical_name(canonical_ref)
    canonical = next(
        (branch for branch in branches if branch.name == canonical_name),
        None,
    )
    if canonical is None:
        available = ", ".join(branch.name for branch in branches[:20])
        raise ValueError(
            f"branch canônica não descoberta: {canonical_ref}. Disponíveis: {available}"
        )

    ordered_branches = [canonical] + [
        branch for branch in branches if branch.name != canonical.name
    ]
    logger(
        f"Descobertas {len(ordered_branches)} branches; "
        f"canônica: {canonical.name}",
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
    entries: list[dict[str, object]] = []

    for index, branch in enumerate(ordered_branches, start=1):
        logger(
            f"Branch {index}/{len(ordered_branches)}: "
            f"{branch.name} @ {branch.commit_sha[:12]}"
        )
        reused = branch.commit_sha in inventories_by_commit
        if reused:
            inventory = copy.deepcopy(inventories_by_commit[branch.commit_sha])
        else:
            snapshot = materialize_repository_snapshot(
                mirror,
                project=project,
                cache_dir=cache_dir,
                ref=branch.requested_ref,
                log=logger,
            )
            inventory = build_inventory(
                source=snapshot.path,
                project=project,
                access_class=access_class,
                profile=profile,
                metadata_override=snapshot.metadata(),
                progress=progress,
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
            }
        )

        catalog = _catalog_path(destination, branch.name)
        write_yaml(inventory, catalog)
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
            "discovered_files": summary["discovered_files"],
            "indexable_files": summary["indexable_files"],
            "excluded_files": summary["excluded_files"],
            "errors": summary["errors"],
            "shared_commit": commit_counts[branch.commit_sha] > 1,
            "reused_inventory": reused,
            **relation,
        }
        entries.append(entry)

    tree = _render_tree(project, entries)
    tree_path = destination / "branches.generated.txt"
    tree_path.write_text(tree, encoding="utf-8", newline="\n")

    total_errors = sum(int(entry["errors"]) for entry in entries)
    manifest: dict[str, object] = {
        "schema_version": "0.1",
        "generated_at": _utc_now(),
        "project": project,
        "source": str(mirror.source_path),
        "remote_url": mirror.remote_url,
        "canonical_branch": canonical.name,
        "canonical_ref": canonical.requested_ref,
        "branch_scope": branch_scope,
        "summary": {
            "branches": len(entries),
            "unique_commits": len(inventories_by_commit),
            "catalogs": len(entries),
            "errors": total_errors,
        },
        "branches": entries,
    }
    manifest_path = destination / "manifest.generated.yaml"
    write_yaml(manifest, manifest_path)
    logger("Árvore de branches:\n" + tree.rstrip(), "result")
    return {
        "output_dir": str(destination),
        "manifest": str(manifest_path),
        "tree": str(tree_path),
        "branches": len(entries),
        "unique_commits": len(inventories_by_commit),
        "errors": total_errors,
    }
