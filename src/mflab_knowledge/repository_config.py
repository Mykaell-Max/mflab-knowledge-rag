from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from mflab_knowledge.normalize import ACCESS_CLASSES

REPOSITORY_CONFIG_SCHEMA_VERSION = "0.1"
REPOSITORY_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
BRANCH_SCOPES = {"remote", "local", "all"}


@dataclass(frozen=True)
class RepositoryDefinition:
    id: str
    enabled: bool
    project: str
    source: Path
    canonical_ref: str
    branch_scope: str
    access_class: str
    profile: str
    include_branches: tuple[str, ...] = ()
    exclude_branches: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepositoryCatalog:
    path: Path
    config_hash: str
    cache_root: Path
    inventory_root: Path
    normalized_root: Path
    repositories: tuple[RepositoryDefinition, ...]

    @property
    def enabled(self) -> tuple[RepositoryDefinition, ...]:
        return tuple(repository for repository in self.repositories if repository.enabled)


def _required_text(value: object, field: str, record: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{record} exige {field}")
    return value.strip()


def _optional_patterns(value: object, field: str, record: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{record}.{field} deve ser uma lista de padrões")
    return tuple(dict.fromkeys(item.strip() for item in value))


def _resolve_path(base: Path, value: object, field: str, record: str) -> Path:
    raw = _required_text(value, field, record)
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def load_repository_catalog(path: Path) -> RepositoryCatalog:
    config_file = path.expanduser().resolve()
    try:
        raw = config_file.read_bytes()
        value = tomllib.loads(raw.decode("utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"catálogo de repositórios não encontrado: {config_file}") from exc
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"catálogo de repositórios inválido: {config_file}: {exc}") from exc
    if value.get("schema_version") != REPOSITORY_CONFIG_SCHEMA_VERSION:
        raise ValueError("versão incompatível do catálogo de repositórios")
    unknown_top_level = sorted(set(value) - {"schema_version", "defaults", "repositories"})
    if unknown_top_level:
        raise ValueError(
            "opções desconhecidas no catálogo: " + ", ".join(unknown_top_level)
        )

    defaults = value.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ValueError("repositories.defaults deve ser uma tabela TOML")
    allowed_defaults = {
        "branch_scope",
        "access_class",
        "profile",
        "cache_root",
        "inventory_root",
        "normalized_root",
        "include_branches",
        "exclude_branches",
    }
    unknown_defaults = sorted(set(defaults) - allowed_defaults)
    if unknown_defaults:
        raise ValueError(
            "opções desconhecidas em repositories.defaults: "
            + ", ".join(unknown_defaults)
        )

    base = config_file.parent
    cache_root = _resolve_path(
        base,
        defaults.get("cache_root", "cache/repositories"),
        "cache_root",
        "defaults",
    )
    inventory_root = _resolve_path(
        base,
        defaults.get("inventory_root", "inventory/repositories"),
        "inventory_root",
        "defaults",
    )
    normalized_root = _resolve_path(
        base,
        defaults.get("normalized_root", "data/repositories"),
        "normalized_root",
        "defaults",
    )
    default_scope = _required_text(
        defaults.get("branch_scope", "remote"),
        "branch_scope",
        "defaults",
    )
    default_access = _required_text(
        defaults.get("access_class", "lab"),
        "access_class",
        "defaults",
    )
    default_profile = _required_text(
        defaults.get("profile", "generic"),
        "profile",
        "defaults",
    )
    default_include = _optional_patterns(
        defaults.get("include_branches"),
        "include_branches",
        "defaults",
    )
    default_exclude = _optional_patterns(
        defaults.get("exclude_branches"),
        "exclude_branches",
        "defaults",
    )

    raw_repositories = value.get("repositories")
    if not isinstance(raw_repositories, list) or not raw_repositories:
        raise ValueError("catálogo não contém [[repositories]]")
    allowed_repository = {
        "id",
        "enabled",
        "project",
        "source",
        "canonical_ref",
        "branch_scope",
        "access_class",
        "profile",
        "include_branches",
        "exclude_branches",
    }
    repositories: list[RepositoryDefinition] = []
    seen_ids: set[str] = set()
    for position, raw_repository in enumerate(raw_repositories, start=1):
        record = f"repositories[{position}]"
        if not isinstance(raw_repository, dict):
            raise ValueError(f"{record} deve ser uma tabela TOML")
        unknown = sorted(set(raw_repository) - allowed_repository)
        if unknown:
            raise ValueError(f"opções desconhecidas em {record}: " + ", ".join(unknown))
        repository_id = _required_text(raw_repository.get("id"), "id", record)
        if REPOSITORY_ID.fullmatch(repository_id) is None:
            raise ValueError(
                f"{record}.id inválido; use letras minúsculas, números, ponto, _ ou -"
            )
        if repository_id in seen_ids:
            raise ValueError(f"id de repositório duplicado: {repository_id}")
        seen_ids.add(repository_id)
        enabled = raw_repository.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"{record}.enabled deve ser booleano")
        scope = _required_text(
            raw_repository.get("branch_scope", default_scope),
            "branch_scope",
            record,
        )
        if scope not in BRANCH_SCOPES:
            raise ValueError(f"{record}.branch_scope inválido: {scope}")
        access_class = _required_text(
            raw_repository.get("access_class", default_access),
            "access_class",
            record,
        )
        if access_class not in ACCESS_CLASSES:
            raise ValueError(f"{record}.access_class inválido: {access_class}")
        if enabled and access_class == "pending":
            raise ValueError(f"{record} está habilitado com acesso pending")
        include = _optional_patterns(
            raw_repository.get("include_branches", list(default_include)),
            "include_branches",
            record,
        )
        exclude = _optional_patterns(
            raw_repository.get("exclude_branches", list(default_exclude)),
            "exclude_branches",
            record,
        )
        repositories.append(
            RepositoryDefinition(
                id=repository_id,
                enabled=enabled,
                project=_required_text(raw_repository.get("project"), "project", record),
                source=_resolve_path(base, raw_repository.get("source"), "source", record),
                canonical_ref=_required_text(
                    raw_repository.get("canonical_ref"),
                    "canonical_ref",
                    record,
                ),
                branch_scope=scope,
                access_class=access_class,
                profile=_required_text(
                    raw_repository.get("profile", default_profile),
                    "profile",
                    record,
                ),
                include_branches=include,
                exclude_branches=exclude,
            )
        )

    if not any(repository.enabled for repository in repositories):
        raise ValueError("catálogo não possui repositórios habilitados")
    return RepositoryCatalog(
        path=config_file,
        config_hash=f"sha256:{hashlib.sha256(raw).hexdigest()}",
        cache_root=cache_root,
        inventory_root=inventory_root,
        normalized_root=normalized_root,
        repositories=tuple(repositories),
    )
