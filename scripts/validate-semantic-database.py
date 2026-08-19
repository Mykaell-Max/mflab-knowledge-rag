#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from mflab_knowledge.credentials import load_database_url
from mflab_knowledge.semantic_database import semantic_map_status


def _summary(path: Path) -> dict[str, object]:
    resolved = path.expanduser().resolve()
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"resumo inválido: {resolved}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("resumo do mapa deve ser um objeto JSON")
    return value


def _safe_error(error: Exception, database_url: str | None) -> str:
    message = str(error)
    if not database_url:
        return message
    message = message.replace(database_url, "<MFLAB_DATABASE_URL>")
    password = urlsplit(database_url).password
    return message.replace(password, "***") if password else message


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compara o mapa PostgreSQL com seus artefatos auditáveis.",
    )
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--env-file", default=Path(".env"), type=Path)
    args = parser.parse_args()

    database_url: str | None = None
    try:
        expected = _summary(args.summary)
        repository_id = str(expected.get("repository_id") or "")
        if not repository_id:
            raise ValueError("resumo não contém repository_id")
        database_url = load_database_url(args.env_file)
        values = semantic_map_status(
            database_url,
            repository_id=repository_id,
        )
        if len(values) != 1:
            raise ValueError("repositório não encontrado no mapa PostgreSQL")
        actual = values[0]
        comparisons = {
            "symbols": int(expected.get("symbols") or 0),
            "relations": int(expected.get("relations") or 0),
            "fingerprint": expected.get("fingerprint"),
        }
        for key, expected_value in comparisons.items():
            if actual.get(key) != expected_value:
                raise ValueError(
                    f"{key} diverge: PostgreSQL={actual.get(key)!r}; "
                    f"artefato={expected_value!r}"
                )
    except Exception as exc:
        print(f"\n[ERRO] {_safe_error(exc, database_url)}")
        return 1

    print(json.dumps(actual, ensure_ascii=False, indent=2))
    print("\n[OK] Mapa PostgreSQL idêntico aos artefatos auditáveis.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
