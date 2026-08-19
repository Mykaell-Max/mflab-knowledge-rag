#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON inválido em {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"objeto JSON esperado em {path}")
    return value


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"não foi possível ler {path}: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON inválido em {path}:{line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"registro inválido em {path}:{line_number}")
        values.append(value)
    return values


def _resolve_output(summary_path: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("resumo não informa um arquivo de saída")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = summary_path.parent / candidate
    resolved = candidate.resolve()
    boundary = summary_path.parent.resolve()
    if resolved != boundary and boundary not in resolved.parents:
        raise ValueError("arquivo do mapa está fora do diretório normalizado")
    return resolved


def validate(summary_path: Path) -> dict[str, object]:
    resolved_summary = summary_path.expanduser().resolve()
    summary = _load_json(resolved_summary)
    if summary.get("algorithm") != "deterministic_symbols_relations_v1":
        raise ValueError("algoritmo do mapa semântico não reconhecido")
    fingerprint = str(summary.get("fingerprint") or "")
    if not fingerprint.startswith("sha256:") or len(fingerprint) != 71:
        raise ValueError("fingerprint do mapa inválido")

    symbols_path = _resolve_output(resolved_summary, summary.get("symbols_file"))
    relations_path = _resolve_output(
        resolved_summary,
        summary.get("relations_file"),
    )
    symbols = _load_jsonl(symbols_path)
    relations = _load_jsonl(relations_path)
    if len(symbols) != int(summary.get("symbols") or 0):
        raise ValueError("contagem de símbolos diverge do resumo")
    if len(relations) != int(summary.get("relations") or 0):
        raise ValueError("contagem de relações diverge do resumo")
    if not symbols:
        raise ValueError("nenhum símbolo foi extraído")

    symbol_ids: set[str] = set()
    for symbol in symbols:
        required = {
            "symbol_id",
            "repository_id",
            "project",
            "document_id",
            "evidence_chunk_id",
            "path",
            "access_class",
            "qualified_name",
            "kind",
            "line_start",
            "occurrences",
        }
        if required - set(symbol):
            raise ValueError("símbolo sem proveniência completa")
        symbol_id = str(symbol["symbol_id"])
        if symbol_id in symbol_ids:
            raise ValueError(f"symbol_id duplicado: {symbol_id}")
        symbol_ids.add(symbol_id)
        if not isinstance(symbol["occurrences"], list) or not symbol["occurrences"]:
            raise ValueError("símbolo sem ocorrência de branch/commit")

    relation_ids: set[str] = set()
    for relation in relations:
        required = {
            "relation_id",
            "repository_id",
            "project",
            "source_document_id",
            "source_path",
            "target_kind",
            "target_name",
            "kind",
            "access_class",
            "occurrences",
        }
        if required - set(relation):
            raise ValueError("relação sem proveniência completa")
        relation_id = str(relation["relation_id"])
        if relation_id in relation_ids:
            raise ValueError(f"relation_id duplicado: {relation_id}")
        relation_ids.add(relation_id)
        if not isinstance(relation["occurrences"], list):
            raise ValueError("occurrences inválido em relação")

    result = {
        "project": summary.get("project"),
        "repository_id": summary.get("repository_id"),
        "documents": int(summary.get("documents") or 0),
        "symbols": len(symbols),
        "symbol_kinds": dict(
            sorted(Counter(str(item["kind"]) for item in symbols).items())
        ),
        "relations": len(relations),
        "relation_kinds": dict(
            sorted(Counter(str(item["kind"]) for item in relations).items())
        ),
        "resolved_relations": sum(
            item.get("target_document_id") is not None for item in relations
        ),
        "fingerprint": fingerprint,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Valida os artefatos determinísticos do mapa semântico.",
    )
    parser.add_argument("--summary", required=True, type=Path)
    args = parser.parse_args()
    result = validate(args.summary)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("\n[OK] Símbolos, relações, ACL e proveniência validados.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"\n[ERRO] {exc}")
        raise SystemExit(1) from None
