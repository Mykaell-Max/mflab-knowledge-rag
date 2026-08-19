from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Iterable

from mflab_knowledge.exploration import overview_authority

STRUCTURE_ALGORITHM = "repository_structure_v1"
_ACCESS_ORDER = {
    "public": 0,
    "lab": 1,
    "project": 2,
    "restricted": 3,
}


def _fingerprint(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(serialized).hexdigest()}"


def _most_restrictive_access(values: Iterable[str]) -> str:
    visible = [value for value in values if value in _ACCESS_ORDER]
    return max(visible, key=_ACCESS_ORDER.__getitem__) if visible else "public"


def build_repository_structures(
    rows: Iterable[dict[str, object]],
    *,
    requested_project: str,
    requested_branch: str,
    allowed_access: set[str],
    anchor_limit: int = 8,
) -> list[dict[str, object]]:
    """Build deterministic maps from ACL-filtered document metadata."""

    grouped: dict[str, list[dict[str, object]]] = {}
    for original in rows:
        row = dict(original)
        if str(row.get("project") or "") != requested_project:
            raise ValueError("linha estrutural fora do projeto solicitado")
        grouped.setdefault(str(row["repository_id"]), []).append(row)

    structures: list[dict[str, object]] = []
    for repository_id, documents in sorted(grouped.items()):
        formats: dict[str, Counter[str]] = {}
        top_level: dict[tuple[str, str], Counter[str]] = {}
        commit_counts: Counter[str] = Counter()
        access_classes: set[str] = set()
        anchors: list[dict[str, object]] = []
        total_chunks = 0
        total_bytes = 0

        for row in documents:
            path = str(row["path"])
            chunks = int(row.get("chunk_count") or 0)
            size_bytes = int(row.get("size_bytes") or 0)
            total_chunks += chunks
            total_bytes += size_bytes
            commit_sha = str(row.get("commit_sha") or "?")
            commit_counts[commit_sha] += 1
            access_class = str(row.get("access_class") or "public")
            access_classes.add(access_class)

            format_name = str(row.get("format") or "unknown")
            format_stats = formats.setdefault(format_name, Counter())
            format_stats.update(documents=1, chunks=chunks, bytes=size_bytes)

            first, separator, _remaining = path.partition("/")
            node_kind = "directory" if separator else "root_file"
            node_stats = top_level.setdefault((first, node_kind), Counter())
            node_stats.update(documents=1, chunks=chunks, bytes=size_bytes)

            if row.get("anchor_chunk_id") is None:
                continue
            anchor = {
                "score": 0.0,
                "chunk_id": str(row["anchor_chunk_id"]),
                "chunk_hash": str(row.get("anchor_chunk_hash") or ""),
                "project": str(row["project"]),
                "repository_id": repository_id,
                "path": path,
                "format": format_name,
                "title": str(row.get("anchor_title") or ""),
                "line_start": int(row.get("anchor_line_start") or 1),
                "line_end": int(row.get("anchor_line_end") or 1),
                "access_class": access_class,
                "text": str(row.get("anchor_text") or ""),
                "selected_occurrence": {
                    "branch": requested_branch,
                    "commit_sha": commit_sha,
                },
                "occurrences": [
                    {
                        "branch": requested_branch,
                        "commit_sha": commit_sha,
                        "canonical": bool(row.get("canonical")),
                        "requested_ref": row.get("requested_ref"),
                    }
                ],
                "source_kind": "primary_structure_anchor",
            }
            anchor["citation"] = (
                f"{anchor['project']} {requested_branch}@{commit_sha[:12]} "
                f"{path}:L{anchor['line_start']}-L{anchor['line_end']}"
            )
            anchors.append(anchor)

        format_values = [
            {
                "format": name,
                "documents": stats["documents"],
                "chunks": stats["chunks"],
                "bytes": stats["bytes"],
            }
            for name, stats in sorted(
                formats.items(),
                key=lambda item: (-item[1]["documents"], item[0]),
            )
        ]
        top_level_values = [
            {
                "name": name,
                "kind": kind,
                "documents": stats["documents"],
                "chunks": stats["chunks"],
                "bytes": stats["bytes"],
            }
            for (name, kind), stats in sorted(
                top_level.items(),
                key=lambda item: (-item[1]["documents"], item[0][0]),
            )
        ]
        commits = [
            {"commit_sha": sha, "documents": count}
            for sha, count in sorted(
                commit_counts.items(), key=lambda item: (-item[1], item[0])
            )
        ]
        project = str(documents[0]["project"])
        identity = {
            "algorithm": STRUCTURE_ALGORITHM,
            "repository_id": repository_id,
            "project": project,
            "branch": requested_branch,
            "commits": commits,
            "documents": len(documents),
            "chunks": total_chunks,
            "bytes": total_bytes,
            "formats": format_values,
            "top_level": top_level_values,
        }
        fingerprint = _fingerprint(identity)
        selected_anchors = sorted(anchors, key=overview_authority)[:anchor_limit]
        structures.append(
            {
                "schema_version": "0.1",
                **identity,
                "access_class": _most_restrictive_access(access_classes),
                "allowed_access": sorted(allowed_access),
                "anchors": selected_anchors,
                "fingerprint": fingerprint,
                "derived_only_from_indexed_metadata": True,
            }
        )

    return structures


def structure_source(structure: dict[str, object]) -> dict[str, object]:
    """Represent an auditable structure map as bounded derived evidence."""

    formats = structure.get("formats")
    top_level = structure.get("top_level")
    commits = structure.get("commits")
    format_items = formats if isinstance(formats, list) else []
    top_items = top_level if isinstance(top_level, list) else []
    commit_items = commits if isinstance(commits, list) else []
    lines = [
        "Deterministic structural map derived from ACL-filtered indexed metadata.",
        "It supports claims about indexed layout and file formats only; it does not "
        "establish scientific purpose or capabilities.",
        f"Indexed documents: {int(structure.get('documents') or 0)}; "
        f"chunks: {int(structure.get('chunks') or 0)}.",
        "Indexed formats: "
        + ", ".join(
            f"{item.get('format')} ({item.get('documents')} documents)"
            for item in format_items[:12]
            if isinstance(item, dict)
        ),
        "Top-level entries: "
        + ", ".join(
            f"{item.get('name')} ({item.get('kind')}, "
            f"{item.get('documents')} documents)"
            for item in top_items[:16]
            if isinstance(item, dict)
        ),
    ]
    text = "\n".join(lines)
    project = str(structure.get("project") or "?")
    branch = str(structure.get("branch") or "?")
    commit_sha = (
        str(commit_items[0].get("commit_sha") or "?")
        if commit_items and isinstance(commit_items[0], dict)
        else "?"
    )
    fingerprint = str(structure.get("fingerprint") or "")
    path = ".mflab-derived/repository-structure.txt"
    return {
        "score": 0.0,
        "chunk_id": f"structure:{fingerprint}",
        "chunk_hash": fingerprint,
        "project": project,
        "repository_id": str(structure.get("repository_id") or ""),
        "path": path,
        "title": "Indexed repository structure",
        "line_start": 1,
        "line_end": len(lines),
        "access_class": str(structure.get("access_class") or "public"),
        "text": text,
        "selected_occurrence": {
            "branch": branch,
            "commit_sha": commit_sha,
        },
        "occurrences": [],
        "source_kind": "derived_structure",
        "derivation": {
            "algorithm": structure.get("algorithm"),
            "fingerprint": fingerprint,
            "derived_only_from_indexed_metadata": True,
        },
        "citation": (
            f"{project} {branch}@{commit_sha[:12]} {path}:L1-L{len(lines)}"
        ),
    }
