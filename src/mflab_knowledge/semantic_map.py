from __future__ import annotations

import hashlib
import json
import posixpath
import re
import tempfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Iterable

SEMANTIC_MAP_SCHEMA_VERSION = "0.3"
SEMANTIC_MAP_ALGORITHM = "deterministic_symbols_relations_v3"

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
CALL_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"([A-Za-z_]\w*(?:(?:::|->|\.)[A-Za-z_]\w*)*)\s*\("
)
FORTRAN_CALL = re.compile(r"\bcall\s+([A-Za-z_]\w*)", re.IGNORECASE)
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
QUOTED_LITERAL = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')

CALL_SOURCE_KINDS = {"function", "program", "subroutine"}
CALL_TARGET_KINDS = {"function", "macro", "subroutine"}
CALL_STOPWORDS = {
    "alignof",
    "catch",
    "decltype",
    "defined",
    "do",
    "else",
    "for",
    "if",
    "return",
    "sizeof",
    "switch",
    "typeid",
    "while",
}


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


def semantic_map_fingerprint(
    symbols: Iterable[dict[str, object]],
    relations: Iterable[dict[str, object]],
) -> str:
    return _fingerprint(
        {
            "algorithm": SEMANTIC_MAP_ALGORITHM,
            "symbols": list(symbols),
            "relations": list(relations),
        }
    )


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


def _symbol_owner(qualified_name: str) -> str:
    return qualified_name.rsplit("::", 1)[0].strip() if "::" in qualified_name else ""


def _is_symbol_definition(
    chunk: dict[str, object],
    *,
    file_format: str,
) -> bool:
    """Distinguish C/C++ declarations from bodies using only visible syntax."""

    if (
        file_format not in {"c", "cpp", "cpp_header"}
        or str(chunk.get("kind", "")).casefold() != "function"
    ):
        return True
    return any(
        "{" in line
        for line in _masked_code_lines(
            str(chunk.get("text", "")),
            file_format=file_format,
        )
    )


def _occurrences_by_scope(
    document: dict[str, object],
) -> dict[tuple[str, str], dict[str, object]]:
    values: dict[tuple[str, str], dict[str, object]] = {}
    raw_occurrences = document.get("occurrences")
    if not isinstance(raw_occurrences, list):
        return values
    for raw_occurrence in raw_occurrences:
        if not isinstance(raw_occurrence, dict):
            continue
        branch = str(raw_occurrence.get("branch") or "")
        commit_sha = str(raw_occurrence.get("commit_sha") or "")
        if branch and commit_sha:
            values[(branch, commit_sha)] = raw_occurrence
    return values


def _identifier_terms(value: str) -> set[str]:
    expanded = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", expanded)
    return {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9]+", expanded)
        if len(token) >= 2 and token.casefold() not in {"m", "self", "this"}
    }


def _masked_code_lines(text: str, *, file_format: str) -> list[str]:
    if file_format in {"c", "cpp", "cpp_header"}:
        text = BLOCK_COMMENT.sub(
            lambda match: "\n" * match.group(0).count("\n"),
            text,
        )
    lines: list[str] = []
    for line in text.splitlines():
        masked = QUOTED_LITERAL.sub("", line)
        if file_format in {"c", "cpp", "cpp_header"}:
            masked = masked.split("//", 1)[0]
        elif file_format == "fortran":
            masked = masked.split("!", 1)[0]
        elif file_format == "python":
            masked = masked.split("#", 1)[0]
        lines.append(masked)
    return lines


def _calls_for_line(line: str, *, file_format: str) -> list[tuple[str, int]]:
    calls: list[tuple[str, int]] = []
    if file_format == "fortran":
        calls.extend(
            (match.group(1), match.start(1))
            for match in FORTRAN_CALL.finditer(line)
        )
    if file_format not in {"c", "cpp", "cpp_header", "fortran", "python"}:
        return calls
    for match in CALL_TOKEN.finditer(line):
        value = match.group(1)
        leaf = re.split(r"::|->|\.", value)[-1].casefold()
        if leaf in CALL_STOPWORDS:
            continue
        candidate = (value, match.start(1))
        if candidate not in calls:
            calls.append(candidate)
    return calls


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
                "is_definition": _is_symbol_definition(
                    chunk,
                    file_format=str(document.get("format", "unknown")),
                ),
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


def _candidate_call_symbols(
    raw_target: str,
    *,
    repository_id: str,
    caller_qualified_name: str,
    symbols_by_name: dict[tuple[str, str], list[dict[str, object]]],
) -> tuple[list[dict[str, object]], str]:
    parts = re.split(r"::|->|\.", raw_target)
    leaf = parts[-1].casefold()
    candidates = list(symbols_by_name.get((repository_id, leaf), []))
    if not candidates:
        return [], "unresolved"

    normalized = raw_target.casefold()
    if "::" in raw_target:
        exact = [
            symbol
            for symbol in candidates
            if str(symbol["qualified_name"]).casefold().endswith(normalized)
        ]
        if exact:
            definitions = [
                symbol for symbol in exact if bool(symbol.get("is_definition"))
            ]
            return definitions or exact, "exact_qualified"

    if len(candidates) == 1:
        return candidates, "unique_name"

    # An unqualified member call inside ``Owner::method`` first refers to the
    # same owner. This is a language-level structural hint, not a repository
    # convention. It prevents equally named methods in unrelated classes from
    # turning a local call into an unresolved edge.
    if len(parts) == 1:
        caller_owner = _symbol_owner(caller_qualified_name).casefold()
        if caller_owner:
            same_owner = [
                symbol
                for symbol in candidates
                if _symbol_owner(str(symbol["qualified_name"])).casefold()
                == caller_owner
            ]
            if same_owner:
                definitions = [
                    symbol
                    for symbol in same_owner
                    if bool(symbol.get("is_definition"))
                ]
                return definitions or same_owner, "same_owner"

        # A declaration and its definition can occur in different documents.
        # When every candidate has the same qualified identity, prefer the
        # body while preserving ambiguity between genuinely different owners.
        qualified_names = {
            str(symbol["qualified_name"]).casefold() for symbol in candidates
        }
        if len(qualified_names) == 1:
            definitions = [
                symbol
                for symbol in candidates
                if bool(symbol.get("is_definition"))
            ]
            if definitions:
                return definitions, "definition"

    if len(parts) < 2:
        return candidates, "branch_unique"
    receiver_terms = _identifier_terms(" ".join(parts[:-1]))
    if not receiver_terms:
        return candidates, "branch_unique"
    ranked: list[tuple[float, dict[str, object]]] = []
    for symbol in candidates:
        qualified_name = str(symbol["qualified_name"])
        owner = qualified_name.rsplit("::", 1)[0] if "::" in qualified_name else ""
        owner_terms = _identifier_terms(owner)
        score = (
            len(receiver_terms & owner_terms) / len(owner_terms)
            if owner_terms
            else 0.0
        )
        ranked.append((score, symbol))
    highest = max(score for score, _symbol in ranked)
    if highest <= 0:
        return candidates, "branch_unique"
    return (
        (
            definitions
            if (
                definitions := [
                    symbol
                    for score, symbol in ranked
                    if score == highest and bool(symbol.get("is_definition"))
                ]
            )
            else [symbol for score, symbol in ranked if score == highest]
        ),
        "receiver_hint",
    )


def _call_target_scopes(
    *,
    source_document: dict[str, object],
    candidate_symbols: list[dict[str, object]],
    documents: dict[str, dict[str, object]],
    occurrences_by_document: dict[
        str, dict[tuple[str, str], dict[str, object]]
    ],
) -> tuple[
    dict[str, list[dict[str, object]]],
    list[dict[str, object]],
]:
    source_document_id = str(source_document["document_id"])
    source_scopes = occurrences_by_document.get(source_document_id, {})
    target_documents = {
        str(symbol["document_id"])
        for symbol in candidate_symbols
        if str(symbol.get("document_id") or "") in documents
    }
    targets_by_scope: dict[tuple[str, str], set[str]] = defaultdict(set)
    for target_document_id in target_documents:
        for scope in occurrences_by_document.get(target_document_id, {}):
            if scope in source_scopes:
                targets_by_scope[scope].add(target_document_id)

    resolved: dict[str, list[dict[str, object]]] = defaultdict(list)
    unresolved: list[dict[str, object]] = []
    for scope, occurrence in source_scopes.items():
        targets = targets_by_scope.get(scope, set())
        if len(targets) == 1:
            resolved[next(iter(targets))].append(occurrence)
        elif targets:
            unresolved.append(occurrence)
    return dict(resolved), unresolved


def _call_relations(
    documents: dict[str, dict[str, object]],
    chunks: list[dict[str, object]],
    symbols: list[dict[str, object]],
) -> list[dict[str, object]]:
    symbols_by_name: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for symbol in symbols:
        if str(symbol.get("kind")) not in CALL_TARGET_KINDS:
            continue
        symbols_by_name[
            (
                str(symbol["repository_id"]),
                str(symbol["name"]).casefold(),
            )
        ].append(symbol)

    occurrences_by_document = {
        document_id: _occurrences_by_scope(document)
        for document_id, document in documents.items()
    }

    relations: list[dict[str, object]] = []
    seen: set[tuple[str, str, int, str | None]] = set()
    for chunk in sorted(
        chunks,
        key=lambda item: (
            str(item.get("document_id") or ""),
            int(item.get("line_start") or 0),
            str(item.get("chunk_id") or ""),
        ),
    ):
        if str(chunk.get("kind") or "").casefold() not in CALL_SOURCE_KINDS:
            continue
        document_id = str(chunk.get("document_id") or "")
        source_document = documents.get(document_id)
        if source_document is None:
            continue
        file_format = str(source_document.get("format") or "unknown")
        current_name = _symbol_name(str(chunk.get("title") or "")).casefold()
        body_started = file_format not in {"c", "cpp", "cpp_header"}
        line_start = int(chunk.get("line_start") or 1)
        for offset, line in enumerate(
            _masked_code_lines(str(chunk.get("text") or ""), file_format=file_format)
        ):
            line_number = line_start + offset
            opening_brace = line.find("{")
            for raw_target, column in _calls_for_line(line, file_format=file_format):
                target_leaf = re.split(r"::|->|\.", raw_target)[-1].casefold()
                if target_leaf == current_name and (
                    (not body_started and (opening_brace < 0 or column < opening_brace))
                    or re.match(
                        r"^\s*(?:async\s+def|def|function|subroutine)\b",
                        line,
                        re.IGNORECASE,
                    )
                ):
                    continue
                candidates, resolution = _candidate_call_symbols(
                    raw_target,
                    repository_id=str(source_document["repository_id"]),
                    caller_qualified_name=str(chunk.get("title") or ""),
                    symbols_by_name=symbols_by_name,
                )
                if not candidates:
                    continue
                resolved, unresolved = _call_target_scopes(
                    source_document=source_document,
                    candidate_symbols=candidates,
                    documents=documents,
                    occurrences_by_document=occurrences_by_document,
                )
                targets: list[tuple[str | None, list[dict[str, object]]]] = [
                    *sorted(resolved.items())
                ]
                if unresolved:
                    targets.append((None, unresolved))
                for target_document_id, occurrences in targets:
                    identity = (
                        f"{document_id}:{chunk.get('title', '')}",
                        raw_target.casefold(),
                        line_number,
                        target_document_id,
                    )
                    if identity in seen:
                        continue
                    seen.add(identity)
                    target_document = (
                        documents[target_document_id]
                        if target_document_id is not None
                        else None
                    )
                    access_class = (
                        max(
                            (
                                str(source_document["access_class"]),
                                str(target_document["access_class"]),
                            ),
                            key=lambda value: ACCESS_ORDER.get(
                                value, len(ACCESS_ORDER)
                            ),
                        )
                        if target_document is not None
                        else str(source_document["access_class"])
                    )
                    relations.append(
                        {
                            "schema_version": SEMANTIC_MAP_SCHEMA_VERSION,
                            "algorithm": SEMANTIC_MAP_ALGORITHM,
                            "relation_id": _stable_id(
                                SEMANTIC_MAP_ALGORITHM,
                                str(chunk["chunk_id"]),
                                "calls_symbol",
                                raw_target,
                                str(line_number),
                                target_document_id or "unresolved",
                            ),
                            "repository_id": source_document["repository_id"],
                            "project": source_document["project"],
                            "source_document_id": document_id,
                            "source_path": source_document["path"],
                            "target_kind": (
                                f"symbol_{resolution}"
                                if target_document_id is not None
                                else "unresolved_symbol"
                            ),
                            "target_document_id": target_document_id,
                            "target_name": raw_target,
                            "kind": "calls_symbol",
                            "evidence_chunk_id": chunk["chunk_id"],
                            "line": line_number,
                            "access_class": access_class,
                            "occurrences": occurrences,
                        }
                    )
            if opening_brace >= 0:
                body_started = True
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
    relations.extend(_call_relations(documents_by_id, chunks, symbols))
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
        "call_resolutions": dict(
            sorted(
                {
                    target_kind: sum(
                        item["kind"] == "calls_symbol"
                        and item["target_kind"] == target_kind
                        for item in relations
                    )
                    for target_kind in {
                        str(item["target_kind"])
                        for item in relations
                        if item["kind"] == "calls_symbol"
                    }
                }.items()
            )
        ),
        "fingerprint": semantic_map_fingerprint(symbols, relations),
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
