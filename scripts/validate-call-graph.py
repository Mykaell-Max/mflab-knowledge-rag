#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from mflab_knowledge.credentials import load_database_url
from mflab_knowledge.semantic_database import call_graph_chunk_ids
from mflab_knowledge.semantic_map import SEMANTIC_MAP_ALGORITHM


def _json_object(path: Path) -> dict[str, object]:
    resolved = path.expanduser().resolve()
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"objeto JSON esperado em {resolved}")
    return value


def _safe_error(error: Exception, database_url: str | None) -> str:
    message = str(error)
    if not database_url:
        return message
    message = message.replace(database_url, "<MFLAB_DATABASE_URL>")
    password = urlsplit(database_url).password
    return message.replace(password, "***") if password else message


def _resolved_sample(
    relations_path: Path,
    *,
    branch: str | None,
) -> dict[str, object]:
    priority = {
        "symbol_exact_qualified": 0,
        "symbol_unique_name": 1,
        "symbol_branch_unique": 2,
        "symbol_receiver_hint": 3,
    }
    candidates: list[dict[str, object]] = []
    with relations_path.expanduser().resolve().open(encoding="utf-8") as stream:
        for line in stream:
            value = json.loads(line)
            if not isinstance(value, dict) or value.get("kind") != "calls_symbol":
                continue
            if not value.get("target_document_id") or not value.get(
                "evidence_chunk_id"
            ):
                continue
            if value.get("target_document_id") == value.get("source_document_id"):
                continue
            occurrences = value.get("occurrences")
            if not isinstance(occurrences, list):
                continue
            if branch and not any(
                isinstance(item, dict) and item.get("branch") == branch
                for item in occurrences
            ):
                continue
            candidates.append(value)
    if not candidates:
        raise ValueError("nenhuma chamada resolvida encontrada no escopo solicitado")
    candidates.sort(
        key=lambda item: (
            priority.get(str(item.get("target_kind")), 99),
            str(item.get("source_path")),
            int(item.get("line") or 0),
            str(item.get("target_name")),
        )
    )
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Valida travessia bidirecional do grafo de chamadas persistido.",
    )
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--env-file", default=Path(".env"), type=Path)
    parser.add_argument("--branch")
    parser.add_argument(
        "--allow-access",
        action="append",
        choices=("public", "lab", "project", "restricted"),
    )
    args = parser.parse_args()

    database_url: str | None = None
    try:
        summary = _json_object(args.summary)
        if summary.get("algorithm") != SEMANTIC_MAP_ALGORITHM:
            raise ValueError("o resumo não foi produzido pelo mapa estrutural v2")
        relation_kinds = summary.get("relation_kinds")
        if not isinstance(relation_kinds, dict) or int(
            relation_kinds.get("calls_symbol") or 0
        ) < 1:
            raise ValueError("o mapa não contém relações calls_symbol")
        relations_file = Path(str(summary.get("relations_file") or ""))
        sample = _resolved_sample(relations_file, branch=args.branch)
        occurrences = sample["occurrences"]
        assert isinstance(occurrences, list)
        selected_branch = args.branch or str(occurrences[0]["branch"])
        project = str(sample["project"])
        caller_chunk = str(sample["evidence_chunk_id"])
        access = set(args.allow_access or ("public", "lab"))

        database_url = load_database_url(args.env_file)
        callees = call_graph_chunk_ids(
            database_url,
            chunk_id=caller_chunk,
            direction="callees",
            project=project,
            branch=selected_branch,
            allowed_access=access,
        )
        if not callees:
            raise ValueError("a chamada resolvida não retornou o chunk chamado")
        reverse_callers = call_graph_chunk_ids(
            database_url,
            chunk_id=callees[0],
            direction="callers",
            project=project,
            branch=selected_branch,
            allowed_access=access,
        )
        if caller_chunk not in reverse_callers:
            raise ValueError("a travessia inversa não recuperou o chunk chamador")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"\n[ERRO] {_safe_error(exc, database_url)}")
        return 1

    print(
        json.dumps(
            {
                "project": project,
                "branch": selected_branch,
                "source_path": sample["source_path"],
                "target_name": sample["target_name"],
                "resolution": sample["target_kind"],
                "callees": len(callees),
                "reverse_callers": len(reverse_callers),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("\n[OK] Grafo de chamadas percorrido nas duas direções.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
