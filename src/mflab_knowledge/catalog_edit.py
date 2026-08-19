from __future__ import annotations

import json
import os
import re
import tempfile
import tomllib
from pathlib import Path

from mflab_knowledge.repository_config import load_repository_catalog

REPOSITORY_HEADER = re.compile(r"^\s*\[\[repositories\]\]\s*(?:#.*)?$")


def _toml_value(value: str | list[str]) -> str:
    return json.dumps(value, ensure_ascii=False)


def _replace_or_insert(
    lines: list[str],
    *,
    key: str,
    value: str | list[str],
    after: str,
    newline: str,
) -> list[str]:
    assignment = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index, line in enumerate(lines):
        if assignment.match(line):
            lines[index] = f"{key} = {_toml_value(value)}{newline}"
            return lines

    anchor = re.compile(rf"^\s*{re.escape(after)}\s*=")
    position = 1
    for index, line in enumerate(lines):
        if anchor.match(line):
            position = index + 1
            break
    lines.insert(position, f"{key} = {_toml_value(value)}{newline}")
    return lines


def configure_repository_routing(
    path: Path,
    *,
    repository_id: str,
    preferred_branch: str | None = None,
    aliases: tuple[str, ...] = (),
) -> dict[str, object]:
    """Update only routing metadata in one local repository record."""

    if preferred_branch is None and not aliases:
        raise ValueError("informe preferred_branch ou ao menos um alias")
    config_path = path.expanduser().resolve()
    catalog = load_repository_catalog(config_path)
    definitions = {
        repository.id: repository for repository in catalog.repositories
    }
    definition = definitions.get(repository_id)
    if definition is None:
        raise ValueError(f"repositório desconhecido no catálogo: {repository_id}")

    raw = config_path.read_bytes()
    text = raw.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    starts = [
        index
        for index, line in enumerate(lines)
        if REPOSITORY_HEADER.match(line.rstrip("\r\n"))
    ]
    selected: tuple[int, int] | None = None
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        try:
            record = tomllib.loads("".join(lines[start:end]))["repositories"][0]
        except (KeyError, IndexError, tomllib.TOMLDecodeError) as exc:
            raise ValueError("registro de repositório inválido") from exc
        if record.get("id") == repository_id:
            selected = (start, end)
            break
    if selected is None:
        raise ValueError(f"registro textual não encontrado: {repository_id}")

    start, end = selected
    block = lines[start:end]
    merged_aliases = list(definition.aliases)
    seen_aliases = {alias.casefold() for alias in merged_aliases}
    for raw_alias in aliases:
        alias = raw_alias.strip()
        if not alias:
            raise ValueError("alias não pode ser vazio")
        if "\n" in alias or "\r" in alias:
            raise ValueError("alias contém quebra de linha")
        if alias.casefold() not in seen_aliases:
            seen_aliases.add(alias.casefold())
            merged_aliases.append(alias)

    if aliases:
        block = _replace_or_insert(
            block,
            key="aliases",
            value=merged_aliases,
            after="project",
            newline=newline,
        )
    if preferred_branch is not None:
        branch = preferred_branch.strip()
        if not branch or "\n" in branch or "\r" in branch:
            raise ValueError("preferred_branch inválida")
        block = _replace_or_insert(
            block,
            key="preferred_branch",
            value=branch,
            after="canonical_ref",
            newline=newline,
        )
    lines[start:end] = block
    updated = "".join(lines)

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            dir=config_path.parent,
            delete=False,
        ) as temporary:
            temporary.write(updated)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        temporary_path = Path(temporary_name)
        temporary_path.chmod(config_path.stat().st_mode)
        validated = load_repository_catalog(temporary_path)
        validated_definition = next(
            item for item in validated.repositories if item.id == repository_id
        )
        if preferred_branch is not None and (
            validated_definition.preferred_branch != preferred_branch.strip()
        ):
            raise ValueError("preferred_branch não foi preservada na validação")
        os.replace(temporary_path, config_path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)

    final_catalog = load_repository_catalog(config_path)
    final_definition = next(
        item for item in final_catalog.repositories if item.id == repository_id
    )
    return {
        "config": str(config_path),
        "repository_id": repository_id,
        "project": final_definition.project,
        "preferred_branch": final_definition.preferred_branch,
        "aliases": list(final_definition.aliases),
        "config_hash": final_catalog.config_hash,
    }
