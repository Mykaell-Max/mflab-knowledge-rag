from __future__ import annotations

import hashlib
import json
import posixpath
import re
import tempfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Iterable

SEMANTIC_MAP_SCHEMA_VERSION = "0.1"
SEMANTIC_MAP_ALGORITHM = "deterministic_symbols_relations_v1"

SYMBOL_KINDS = {
    "function",
    "macro",
    "module",
    "program",
    "subroutine",
    "type",
}

ACCESS_ORDER = {
    "public": 0,
    "lab": 1,
    "project": 2,
    "restricted": 3,
    "pending": 4,
}

CPP_INCLUDE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')
FORTRAN_USE = re.compile(
    r"^\s*use(?:\s*,\s*[^:]*)?\s*(?:::)?\s*([A-Za-z_]\w*)",
    re.IGNORECASE,
)
PYTHON_IMPORT = re.compile(r"^\s*import\s+(.+?)\s*(?:#.*)?$")
PYTHON_FROM = re.compile(r"^\s*from\s+([A-Za-z_][\w.]*)\s+import\s+")
CMAKE_RELATION = re.compile(
    r"^\s*(add_subdirectory|include)\s*\(\s*([^\s)]+)",
    re.IGNORECASE,
)
SHELL_SOURCE = re.compile(r"^\s*(?:source|\.)\s+([^\s;]+)")


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _fingerprint(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(serialized).hexdigest()}"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_jsonl(path: Path, values: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
            for value in values:
                handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
        Path(temporary_name).replace(path)
    finally:
        if temporary_name is not None:
            temporary = Path(temporary_name)
            if temporary.exists():
                temporary.unlink()


def _symbol_name(qualified_name: str) -> str:
    return qualified_name.rsplit("::", 1)[-1].strip()


def _symbol_records(
    documents: dict[str, dict[str, object]],
    chunks: list[dict[str, object]],
) -> list[dict[str, object]]:
    symbols: list[dict[str, object]] = []
    active_until: dict[tuple[str, str, str], int] = {}
    for chunk in sorted(
        chunks,
        key=lambda item: (
            str(item.get("document_id") or ""),
            int(item.get("line_start") or 0),
            str(item.get("kind") or ""),
            str(item.get("title") or ""),
        ),
    ):
        kind = str(chunk.get("kind") or "").casefold()
        qualified_name = str(chunk.get("title") or "").strip()
        document_id = str(chunk.get("document_id") or "")
        line_start = int(chunk.get("line_start") or 0)
        if (
            kind not in SYMBOL_KINDS
            or not qualified_name
            or document_id not in documents
            or line_start < 1
        ):
            continue
        identity = (document_id, kind, qualified_name)
        if line_start <= active_until.get(identity, 0):
            active_until[identity] = max(
                active_until[identity],
                int(chunk.get("line_end") or line_start),
            )
            continue
        active_until[identity] = int(chunk.get("line_end") or line_start)
        document = documents[document_id]
        symbol_id = _stable_id(
            SEMANTIC_MAP_ALGORITHM,
            document_id,
            kind,
            qualified_name,
            str(line_start),
        )
        symbols.append(
            {
                "schema_version": SEMANTIC_MAP_SCHEMA_VERSION,
                "algorithm": SEMANTIC_MAP_ALGORITHM,
                "symbol_id": symbol_id,
                "repository_id": document["repository_id"],
                "project": document["project"],
                "document_id": document_id,
                "evidence_chunk_id": chunk["chunk_id"],
                "path": document["path"],
                "format": document.get("format", "unknown"),
                "access_class": document["access_class"],
                "name": _symbol_name(qualified_name),
                "qualified_name": qualified_name,
                "kind": kind,
                "line_start": line_start,
                "line_end": int(chunk.get("line_end") or line_start),
                "occurrences": document.get("occurrences", []),
            }
        )
    return sorted(
        symbols,
        key=lambda item: (
            str(item["path"]),
            int(item["line_start"]),
            str(item["kind"]),
            str(item["qualified_name"]),
        ),
    )


def _references_for_line(
    line: str,
    *,
    file_format: str,
) -> list[tuple[str, str]]:
    if file_format in {"c", "cpp", "cpp_header"}:
        match = CPP_INCLUDE.match(line)
        return [("includes", match.group(1))] if match else []
    if file_format == "fortran":
        match = FORTRAN_USE.match(line)
        return [("uses_module", match.group(1))] if match else []
    if file_format == "python":
        from_match = PYTHON_FROM.match(line)
        if from_match:
            return [("imports_module", from_match.group(1))]
        import_match = PYTHON_IMPORT.match(line)
        if not import_match:
            return []
        modules: list[tuple[str, str]] = []
        for value in import_match.group(1).split(","):
            name = value.strip().split()[0]
            if name:
                modules.append(("imports_module", name))
        return modules
    if file_format == "cmake":
        match = CMAKE_RELATION.match(line)
        if not match:
            return []
        kind = (
            "builds_subdirectory"
            if match.group(1).casefold() == "add_subdirectory"
            else "includes_build_file"
        )
        return [(kind, match.group(2).strip('"\''))]
    if file_format == "shell":
        match = SHELL_SOURCE.match(line)
        return [("sources_file", match.group(1).strip('"\''))] if match else []
    return []


def _resolve_target_document(
    source_path: str,
    target: str,
    *,
    by_path: dict[str, list[str]],
    by_suffix: dict[str, list[str]],
) -> str | None:
    if not target or "$" in target or "${" in target:
        return None
    source_parent = str(PurePosixPath(source_path).parent)
    relative = posixpath.normpath(posixpath.join(source_parent, target))
    relative_candidates = by_path.get(relative, [])
    if len(relative_candidates) == 1:
        return relative_candidates[0]
    normalized = target.lstrip("./")
    candidates = by_suffix.get(normalized, [])
    return candidates[0] if len(candidates) == 1 else None


def _reference_relations(
    documents: dict[str, dict[str, object]],
    chunks: list[dict[str, object]],
) -> list[dict[str, object]]:
    by_path: dict[str, list[str]] = defaultdict(list)
    for document_id, document in documents.items():
        by_path[str(document["path"])].append(document_id)
    by_suffix: dict[str, list[str]] = defaultdict(list)
    for path, document_ids in by_path.items():
        parts = PurePosixPath(path).parts
        for offset in range(len(parts)):
            by_suffix["/".join(parts[offset:])].extend(document_ids)

    relations: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    for chunk in chunks:
        document_id = str(chunk.get("document_id") or "")
        document = documents.get(document_id)
        if document is None:
            continue
        file_format = str(document.get("format") or "unknown")
        line_start = int(chunk.get("line_start") or 1)
        for offset, line in enumerate(str(chunk.get("text") or "").splitlines()):
            line_number = line_start + offset
            for kind, target_name in _references_for_line(
                line,
                file_format=file_format,
            ):
                identity = (document_id, kind, target_name)
                if identity in seen:
                    continue
                seen.add(identity)
                target_document_id = _resolve_target_document(
                    str(document["path"]),
                    target_name,
                    by_path=by_path,
                    by_suffix=by_suffix,
                )
                relations.append(
                    {
                        "schema_version": SEMANTIC_MAP_SCHEMA_VERSION,
                        "algorithm": SEMANTIC_MAP_ALGORITHM,
                        "relation_id": _stable_id(
                            SEMANTIC_MAP_ALGORITHM,
                            document_id,
                            kind,
                            target_name,
                            str(line_number),
                        ),
                        "repository_id": document["repository_id"],
                        "project": document["project"],
                        "source_document_id": document_id,
                        "source_path": document["path"],
                        "target_kind": (
                            "document" if target_document_id else "unresolved_reference"
                        ),
                        "target_document_id": target_document_id,
                        "target_name": target_name,
                        "kind": kind,
                        "evidence_chunk_id": chunk["chunk_id"],
                        "line": line_number,
                        "access_class": document["access_class"],
                        "occurrences": document.get("occurrences", []),
                    }
                )
    return relations


def _companion_relations(
    documents: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    source_formats = {"c", "cpp", "fortran"}
    header_formats = {"cpp_header"}
    by_parent_stem: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for document in documents.values():
        path = PurePosixPath(str(document["path"]))
        by_parent_stem[(str(path.parent), path.stem.casefold())].append(document)

    relations: list[dict[str, object]] = []
    for grouped in by_parent_stem.values():
        sources = [
            item for item in grouped if str(item.get("format")) in source_formats
        ]
        headers = [
            item for item in grouped if str(item.get("format")) in header_formats
        ]
        for source in sources:
            for target in headers:
                source_occurrences = {
                    (
                        str(item.get("branch") or ""),
                        str(item.get("commit_sha") or ""),
                    ): item
                    for item in source.get("occurrences", [])
                    if isinstance(item, dict)
                }
                target_occurrences = {
                    (
                        str(item.get("branch") or ""),
                        str(item.get("commit_sha") or ""),
                    )
                    for item in target.get("occurrences", [])
                    if isinstance(item, dict)
                }
                shared_scopes = sorted(
                    set(source_occurrences).intersection(target_occurrences)
                )
                if not shared_scopes:
                    continue
                access_class = max(
                    (str(source["access_class"]), str(target["access_class"])),
                    key=lambda value: ACCESS_ORDER.get(value, len(ACCESS_ORDER)),
                )
                relations.append(
                    {
                        "schema_version": SEMANTIC_MAP_SCHEMA_VERSION,
                        "algorithm": SEMANTIC_MAP_ALGORITHM,
                        "relation_id": _stable_id(
                            SEMANTIC_MAP_ALGORITHM,
                            str(source["document_id"]),
                            "companion",
                            str(target["document_id"]),
                        ),
                        "repository_id": source["repository_id"],
                        "project": source["project"],
                        "source_document_id": source["document_id"],
                        "source_path": source["path"],
                        "target_kind": "document",
                        "target_document_id": target["document_id"],
                        "target_name": target["path"],
                        "kind": "companion",
                        "evidence_chunk_id": None,
                        "line": None,
                        "access_class": access_class,
                        "occurrences": [
                            source_occurrences[scope] for scope in shared_scopes
                        ],
                    }
                )
    return relations


def build_semantic_map(
    *,
    documents: list[dict[str, object]],
    chunks: list[dict[str, object]],
    output_dir: Path,
) -> dict[str, object]:
    """Build generic structural artifacts without interpreting domain meaning."""

    documents_by_id = {
        str(document["document_id"]): document
        for document in documents
    }
    symbols = _symbol_records(documents_by_id, chunks)
    relations = _reference_relations(documents_by_id, chunks)
    relations.extend(_companion_relations(documents_by_id))
    relations.sort(
        key=lambda item: (
            str(item["source_path"]),
            int(item["line"] or 0),
            str(item["kind"]),
            str(item["target_name"]),
        )
    )

    destination = output_dir.expanduser().resolve()
    symbols_path = destination / "symbols.jsonl"
    relations_path = destination / "relations.jsonl"
    _write_jsonl(symbols_path, symbols)
    _write_jsonl(relations_path, relations)
    summary = {
        "schema_version": SEMANTIC_MAP_SCHEMA_VERSION,
        "algorithm": SEMANTIC_MAP_ALGORITHM,
        "repository_id": (
            next(iter(documents_by_id.values()))["repository_id"]
            if documents_by_id
            else None
        ),
        "project": (
            next(iter(documents_by_id.values()))["project"]
            if documents_by_id
            else None
        ),
        "documents": len(documents_by_id),
        "symbols": len(symbols),
        "relations": len(relations),
        "relation_kinds": dict(
            sorted(
                {
                    kind: sum(item["kind"] == kind for item in relations)
                    for kind in {str(item["kind"]) for item in relations}
                }.items()
            )
        ),
        "fingerprint": _fingerprint(
            {
                "algorithm": SEMANTIC_MAP_ALGORITHM,
                "symbols": [item["symbol_id"] for item in symbols],
                "relations": [item["relation_id"] for item in relations],
            }
        ),
        "symbols_file": str(symbols_path),
        "relations_file": str(relations_path),
    }
    summary_path = destination / "semantic-map.generated.json"
    _write_json(summary_path, summary)
    return {
        "summary": str(summary_path),
        "symbols": str(symbols_path),
        "relations": str(relations_path),
        "symbols_count": len(symbols),
        "relations_count": len(relations),
        "fingerprint": summary["fingerprint"],
    }
