from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from mflab_knowledge.semantic_map import build_semantic_map

LogCallback = Callable[[str, str], None]
ProgressCallback = Callable[[int, int, str], None]

NORMALIZATION_SCHEMA_VERSION = "0.1"
PARSER_VERSION = "baseline-structural-2"
MAX_CHUNK_CHARS = 3_500
OVERLAP_LINES = 5

MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
CPP_TYPE = re.compile(r"^\s*(?:class|struct|enum(?:\s+class)?)\s+([A-Za-z_]\w*)")
CPP_FUNCTION = re.compile(
    r"^\s*(?:template\s*<.*>\s*)?"
    r"(?:[A-Za-z_]\w*(?:::\w+)*(?:\s*[*&]+)?\s+)+"
    r"(?P<name>[~A-Za-z_]\w*(?:::\w+)*)\s*\([^;]*\)\s*"
    r"(?:const\s*)?(?:noexcept\s*)?(?:override\s*)?(?:final\s*)?(?:\{|$)"
)
CPP_SCOPED_FUNCTION = re.compile(
    r"^\s*(?P<name>[~A-Za-z_]\w*(?:::\w+)+)\s*\([^;]*\)\s*"
    r"(?:const\s*)?(?:noexcept\s*)?(?:override\s*)?(?:final\s*)?(?:\{|$)"
)
FORTRAN_UNIT = re.compile(
    r"^\s*(?P<kind>module|subroutine|function|program)\s+"
    r"(?P<name>[A-Za-z_]\w*)",
    re.IGNORECASE,
)
CMAKE_UNIT = re.compile(
    r"^\s*(?P<kind>function|macro)\s*\(\s*(?P<name>[^\s)]+)",
    re.IGNORECASE,
)
SHELL_FUNCTION = re.compile(
    r"^\s*(?:function\s+)?(?P<name>[A-Za-z_]\w*)\s*(?:\(\s*\))?\s*\{"
)
TOKEN = re.compile(r"[\wÀ-ÖØ-öø-ÿ:.+/-]+", re.UNICODE)
ACCESS_CLASSES = {"public", "lab", "project", "restricted", "pending"}
RETRIEVABLE_ACCESS_CLASSES = ACCESS_CLASSES - {"pending"}
CPP_CONTROL_WORDS = {"if", "for", "while", "switch", "catch"}
STRUCTURED_FORMATS = {
    "markdown",
    "c",
    "cpp",
    "cpp_header",
    "fortran",
    "cmake",
    "shell",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON inválido: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"objeto JSON esperado em {path}")
    return value


def _safe_child(root: Path, relative: str) -> Path:
    boundary = root.expanduser().resolve()
    target = (boundary / relative).resolve()
    if target != boundary and boundary not in target.parents:
        raise ValueError(f"caminho fora da raiz autorizada: {relative}")
    return target


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


def _anchors(lines: list[str], file_format: str) -> list[tuple[int, str, str]]:
    anchors: list[tuple[int, str, str]] = []
    if file_format == "markdown":
        hierarchy: list[str] = []
        for index, line in enumerate(lines):
            match = MARKDOWN_HEADING.match(line)
            if match is None:
                continue
            level = len(match.group(1))
            title = match.group(2).strip()
            hierarchy[level - 1 :] = [title]
            anchors.append((index, " / ".join(hierarchy), "section"))
    elif file_format in {"c", "cpp", "cpp_header"}:
        for index, line in enumerate(lines):
            type_match = CPP_TYPE.match(line)
            if type_match is not None:
                anchors.append((index, type_match.group(1), "type"))
                continue
            function_match = CPP_SCOPED_FUNCTION.match(line) or CPP_FUNCTION.match(line)
            if function_match is not None:
                function_name = function_match.group("name")
                if function_name.casefold() not in CPP_CONTROL_WORDS:
                    anchors.append((index, function_name, "function"))
    elif file_format == "fortran":
        for index, line in enumerate(lines):
            match = FORTRAN_UNIT.match(line)
            if match is not None and not line.lstrip().casefold().startswith("module procedure"):
                anchors.append(
                    (index, match.group("name"), match.group("kind").casefold())
                )
    elif file_format == "cmake":
        for index, line in enumerate(lines):
            match = CMAKE_UNIT.match(line)
            if match is not None:
                anchors.append(
                    (index, match.group("name"), match.group("kind").casefold())
                )
    elif file_format == "shell":
        for index, line in enumerate(lines):
            match = SHELL_FUNCTION.match(line)
            if match is not None:
                anchors.append((index, match.group("name"), "function"))
    return anchors


def _window_ranges(
    lines: list[str],
    start: int,
    end: int,
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        limit = cursor
        characters = 0
        last_blank: int | None = None
        while limit < end:
            line_size = len(lines[limit]) + 1
            if limit > cursor and characters + line_size > MAX_CHUNK_CHARS:
                break
            characters += line_size
            limit += 1
            if not lines[limit - 1].strip():
                last_blank = limit
        if limit < end and last_blank is not None and last_blank > cursor:
            limit = last_blank
        if limit <= cursor:
            limit = cursor + 1
        ranges.append((cursor, limit))
        if limit >= end:
            break
        cursor = max(cursor + 1, limit - OVERLAP_LINES)
    return ranges


def _parse_text(text: str, file_format: str) -> list[dict[str, object]]:
    lines = text.splitlines()
    if not lines:
        return []
    anchors = _anchors(lines, file_format)
    sections: list[tuple[int, int, str, str]] = []
    if anchors:
        if anchors[0][0] > 0:
            sections.append((0, anchors[0][0], "preâmbulo", "preamble"))
        for position, (start, title, kind) in enumerate(anchors):
            end = anchors[position + 1][0] if position + 1 < len(anchors) else len(lines)
            sections.append((start, end, title, kind))
    else:
        sections.append((0, len(lines), "arquivo", "file"))

    chunks: list[dict[str, object]] = []
    for section_start, section_end, title, kind in sections:
        for start, end in _window_ranges(lines, section_start, section_end):
            content = "\n".join(lines[start:end]).strip()
            if not content:
                continue
            chunks.append(
                {
                    "line_start": start + 1,
                    "line_end": end,
                    "title": title,
                    "kind": kind,
                    "text": content,
                    "chunk_hash": f"sha256:{_sha256_text(content)}",
                }
            )
    return chunks


def _normalization_cache_path(
    cache_dir: Path,
    content_hash: str,
    file_format: str,
) -> Path:
    cache_key = _stable_id(
        content_hash,
        NORMALIZATION_SCHEMA_VERSION,
        PARSER_VERSION,
        str(MAX_CHUNK_CHARS),
        str(OVERLAP_LINES),
        file_format,
    )
    return (
        cache_dir.expanduser().resolve()
        / "normalization"
        / cache_key[:2]
        / f"{cache_key}.json"
    )


def _read_and_verify(path: Path, expected_hash: str) -> tuple[str, str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"não foi possível ler {path}: {exc}") from exc
    actual_hash = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    if actual_hash != expected_hash:
        raise ValueError(
            f"hash divergente em {path}: esperado {expected_hash}, obtido {actual_hash}"
        )
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace"), "utf-8-replacement"


def _load_or_parse(
    *,
    source_path: Path,
    content_hash: str,
    file_format: str,
    cache_dir: Path,
) -> tuple[list[dict[str, object]], str, bool]:
    cache_path = _normalization_cache_path(cache_dir, content_hash, file_format)
    if cache_path.is_file():
        try:
            payload = _load_json(cache_path)
            chunks = payload.get("chunks")
            encoding = payload.get("encoding")
            if (
                payload.get("schema_version") == NORMALIZATION_SCHEMA_VERSION
                and payload.get("parser_version") == PARSER_VERSION
                and payload.get("content_hash") == content_hash
                and payload.get("format") == file_format
                and isinstance(chunks, list)
                and isinstance(encoding, str)
            ):
                return chunks, encoding, True
        except ValueError:
            pass

    text, encoding = _read_and_verify(source_path, content_hash)
    chunks = _parse_text(text, file_format)
    _write_json(
        cache_path,
        {
            "schema_version": NORMALIZATION_SCHEMA_VERSION,
            "parser_version": PARSER_VERSION,
            "content_hash": content_hash,
            "format": file_format,
            "encoding": encoding,
            "chunks": chunks,
        },
    )
    return chunks, encoding, False


def normalize_manifest(
    *,
    manifest_path: Path,
    output_dir: Path,
    cache_dir: Path,
    log: LogCallback | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, object]:
    logger = log or (lambda _message, _level="info": None)
    manifest_file = manifest_path.expanduser().resolve()
    manifest = _load_json(manifest_file)
    project = manifest.get("project")
    remote_url = manifest.get("remote_url")
    branches = manifest.get("branches")
    if not isinstance(project, str) or not isinstance(branches, list):
        raise ValueError("manifesto de sincronização incompatível")
    repository_identity = remote_url if isinstance(remote_url, str) else str(project)
    repository_id = f"{re.sub(r'[^a-z0-9]+', '-', project.casefold()).strip('-')}-"
    repository_id += _stable_id(repository_identity)[:12]

    occurrences_by_document: dict[str, list[dict[str, object]]] = {}
    document_inputs: dict[str, dict[str, object]] = {}
    input_occurrences = 0
    for branch_entry in branches:
        if not isinstance(branch_entry, dict):
            continue
        catalog_relative = branch_entry.get("catalog_json")
        if not isinstance(catalog_relative, str):
            raise ValueError(
                "manifesto sem catalog_json; execute novamente o comando sync"
            )
        catalog_path = _safe_child(manifest_file.parent, catalog_relative)
        catalog = _load_json(catalog_path)
        source = catalog.get("source")
        indexable = catalog.get("indexable")
        if not isinstance(source, dict) or not isinstance(indexable, list):
            raise ValueError(f"catálogo incompatível: {catalog_path}")
        snapshot_root_value = source.get("snapshot_root")
        if not isinstance(snapshot_root_value, str):
            raise ValueError(
                f"catálogo sem snapshot_root: {catalog_path}; execute sync novamente"
            )
        snapshot_root = Path(snapshot_root_value).expanduser().resolve()
        occurrence = {
            "branch": branch_entry.get("name"),
            "commit_sha": branch_entry.get("commit_sha"),
            "canonical": bool(branch_entry.get("canonical")),
            "requested_ref": branch_entry.get("requested_ref"),
        }
        for item in indexable:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            content_hash = item.get("content_hash")
            if not isinstance(path, str) or not isinstance(content_hash, str):
                continue
            access_class = str(item.get("access_class", source.get("access_class")))
            if access_class not in ACCESS_CLASSES:
                raise ValueError(
                    f"classe de acesso inválida para {path}: {access_class!r}"
                )
            document_id = _stable_id(
                repository_id,
                path,
                content_hash,
                access_class,
            )
            document_inputs.setdefault(
                document_id,
                {
                    "document_id": document_id,
                    "repository_id": repository_id,
                    "project": project,
                    "remote_url": remote_url,
                    "path": path,
                    "format": item.get("format", "unknown"),
                    "size_bytes": item.get("size_bytes", 0),
                    "content_hash": content_hash,
                    "access_class": access_class,
                    "snapshot_path": str(_safe_child(snapshot_root, path)),
                },
            )
            document_occurrences = occurrences_by_document.setdefault(document_id, [])
            if occurrence not in document_occurrences:
                document_occurrences.append(dict(occurrence))
            input_occurrences += 1

    total_documents = len(document_inputs)
    documents: list[dict[str, object]] = []
    chunks: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    parsers = Counter()
    parsed_documents = 0
    reused_documents = 0
    for position, (document_id, item) in enumerate(
        sorted(document_inputs.items(), key=lambda pair: str(pair[1]["path"])),
        start=1,
    ):
        if progress is not None:
            progress(position, total_documents, str(item["path"]))
        try:
            parsed_chunks, encoding, reused = _load_or_parse(
                source_path=Path(str(item["snapshot_path"])),
                content_hash=str(item["content_hash"]),
                file_format=str(item["format"]),
                cache_dir=cache_dir,
            )
        except ValueError as exc:
            errors.append({"path": str(item["path"]), "error": str(exc)})
            continue
        if reused:
            reused_documents += 1
        else:
            parsed_documents += 1
        parser_strategy = (
            "structural_anchors"
            if str(item["format"]) in STRUCTURED_FORMATS
            else "line_windows"
        )
        parsers[parser_strategy] += 1
        occurrences = occurrences_by_document[document_id]
        chunk_ids: list[str] = []
        for parsed_chunk in parsed_chunks:
            chunk_id = _stable_id(
                document_id,
                str(parsed_chunk["line_start"]),
                str(parsed_chunk["line_end"]),
                PARSER_VERSION,
            )
            chunk_ids.append(chunk_id)
            chunks.append(
                {
                    "schema_version": NORMALIZATION_SCHEMA_VERSION,
                    "chunk_id": chunk_id,
                    "document_id": document_id,
                    "repository_id": repository_id,
                    "project": project,
                    "path": item["path"],
                    "format": item["format"],
                    "access_class": item["access_class"],
                    "title": parsed_chunk["title"],
                    "kind": parsed_chunk["kind"],
                    "line_start": parsed_chunk["line_start"],
                    "line_end": parsed_chunk["line_end"],
                    "text": parsed_chunk["text"],
                    "chunk_hash": parsed_chunk["chunk_hash"],
                    "embedding_key": parsed_chunk["chunk_hash"],
                    "parser_version": PARSER_VERSION,
                    "occurrences": occurrences,
                }
            )
        documents.append(
            {
                "schema_version": NORMALIZATION_SCHEMA_VERSION,
                **{key: value for key, value in item.items() if key != "snapshot_path"},
                "encoding": encoding,
                "parser_version": PARSER_VERSION,
                "occurrences": occurrences,
                "chunk_ids": chunk_ids,
            }
        )

    destination = output_dir.expanduser().resolve()
    documents_path = destination / "documents.jsonl"
    chunks_path = destination / "chunks.jsonl"
    _write_jsonl(documents_path, documents)
    _write_jsonl(chunks_path, chunks)
    semantic_map = build_semantic_map(
        documents=documents,
        chunks=chunks,
        output_dir=destination,
    )
    summary: dict[str, object] = {
        "schema_version": NORMALIZATION_SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "source_manifest": str(manifest_file),
        "repository_id": repository_id,
        "project": project,
        "branches": len(branches),
        "input_occurrences": input_occurrences,
        "unique_documents": len(documents),
        "discovered_unique_documents": total_documents,
        "deduplicated_occurrences": input_occurrences - total_documents,
        "chunks": len(chunks),
        "symbols": semantic_map["symbols_count"],
        "relations": semantic_map["relations_count"],
        "documents_parsed": parsed_documents,
        "documents_reused": reused_documents,
        "parser_strategies": dict(sorted(parsers.items())),
        "errors": errors,
        "documents_file": str(documents_path),
        "chunks_file": str(chunks_path),
        "semantic_map": semantic_map,
    }
    summary_path = destination / "normalization.generated.json"
    _write_json(summary_path, summary)
    logger(
        f"Normalização: {len(documents)} documentos únicos de "
        f"{input_occurrences} ocorrências; {len(chunks)} chunks; "
        f"{semantic_map['symbols_count']} símbolos; "
        f"{semantic_map['relations_count']} relações",
        "result" if not errors else "warning",
    )
    return {
        "output_dir": str(destination),
        "summary": str(summary_path),
        "documents": str(documents_path),
        "chunks": str(chunks_path),
        "symbols": semantic_map["symbols"],
        "relations": semantic_map["relations"],
        "semantic_map_summary": semantic_map["summary"],
        "unique_documents": len(documents),
        "input_occurrences": input_occurrences,
        "chunks_count": len(chunks),
        "symbols_count": semantic_map["symbols_count"],
        "relations_count": semantic_map["relations_count"],
        "documents_parsed": parsed_documents,
        "documents_reused": reused_documents,
        "errors": len(errors),
    }


def search_chunks(
    *,
    chunks_path: Path,
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
    if max_per_path < 1 or max_per_path > 100:
        raise ValueError("max_per_path deve estar entre 1 e 100")
    query_folded = query_text.casefold()
    query_tokens = [token.casefold() for token in TOKEN.findall(query_text)]
    effective_access = allowed_access if allowed_access is not None else {"public"}
    if not effective_access or not effective_access.issubset(
        RETRIEVABLE_ACCESS_CLASSES
    ):
        raise ValueError("filtro de acesso inválido ou vazio")
    candidates: list[tuple[float, dict[str, object]]] = []
    try:
        lines = chunks_path.expanduser().resolve().read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"não foi possível abrir o índice: {exc}") from exc
    for raw_line in lines:
        if not raw_line.strip():
            continue
        value = json.loads(raw_line)
        if not isinstance(value, dict):
            continue
        if project is not None and value.get("project") != project:
            continue
        if value.get("access_class") not in effective_access:
            continue
        value_path = str(value.get("path", ""))
        if path_prefix is not None and not value_path.startswith(path_prefix):
            continue
        occurrences = value.get("occurrences", [])
        if branch is not None and not any(
            isinstance(occurrence, dict) and occurrence.get("branch") == branch
            for occurrence in occurrences
        ):
            continue
        text = str(value.get("text", ""))
        searchable = f"{value_path}\n{value.get('title', '')}\n{text}".casefold()
        phrase_count = searchable.count(query_folded)
        token_counts = [searchable.count(token) for token in query_tokens]
        if phrase_count == 0 and not any(token_counts):
            continue
        score = 8.0 * (1.0 + math.log(phrase_count)) if phrase_count else 0.0
        score += sum(1.0 + math.log(count) for count in token_counts if count)
        score += sum(2.0 for token in query_tokens if token in value_path.casefold())
        if any(
            isinstance(occurrence, dict) and occurrence.get("canonical")
            for occurrence in occurrences
        ):
            score += 0.25
        selected_occurrences = [
            occurrence
            for occurrence in occurrences
            if isinstance(occurrence, dict)
            and (branch is None or occurrence.get("branch") == branch)
        ]
        preferred_occurrence = next(
            (
                occurrence
                for occurrence in selected_occurrences
                if occurrence.get("canonical")
            ),
            selected_occurrences[0] if selected_occurrences else {},
        )
        citation = (
            f"{value.get('project')} "
            f"{preferred_occurrence.get('branch', '?')}@"
            f"{str(preferred_occurrence.get('commit_sha', '?'))[:12]} "
            f"{value_path}:L{value.get('line_start')}-L{value.get('line_end')}"
        )
        result = {
            "score": round(score, 4),
            "chunk_id": value.get("chunk_id"),
            "chunk_hash": value.get("chunk_hash"),
            "project": value.get("project"),
            "path": value_path,
            "title": value.get("title"),
            "line_start": value.get("line_start"),
            "line_end": value.get("line_end"),
            "access_class": value.get("access_class"),
            "citation": citation,
            "occurrences": selected_occurrences,
            "text": text,
        }
        candidates.append((score, result))
    candidates.sort(key=lambda item: (-item[0], str(item[1]["path"])))
    results: list[dict[str, object]] = []
    paths: Counter[str] = Counter()
    seen_content: set[str] = set()
    for _, result in candidates:
        result_path = str(result["path"])
        content_hash = str(result.get("chunk_hash") or "")
        if paths[result_path] >= max_per_path:
            continue
        if (
            not include_duplicate_content
            and content_hash
            and content_hash in seen_content
        ):
            continue
        results.append(result)
        paths[result_path] += 1
        if content_hash:
            seen_content.add(content_hash)
        if len(results) >= limit:
            break
    return results
