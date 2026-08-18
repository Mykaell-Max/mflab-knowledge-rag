from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from mflab_knowledge.normalize import ACCESS_CLASSES

REPOSITORY_CONFIG_SCHEMA_VERSION = "0.1"
REPOSITORY_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
BRANCH_SCOPES = {"remote", "local", "all"}
REMOTE_URL_SCHEMES = {"file", "git", "http", "https", "ssh"}
SCP_REMOTE_URL = re.compile(r"^[^@\s]+@[^:\s]+:.+$")


@dataclass(frozen=True)
class RepositoryDefinition:
    id: str
    enabled: bool
    project: str
    source: Path | None
    canonical_ref: str
    branch_scope: str
    access_class: str
    profile: str
    include_branches: tuple[str, ...] = ()
    exclude_branches: tuple[str, ...] = ()
    remote_url: str | None = None
    fetch_timeout_seconds: int = 1800

    @property
    def source_kind(self) -> str:
        return "remote_url" if self.remote_url is not None else "local_worktree"

    @property
    def source_label(self) -> str:
        return self.remote_url or str(self.source)


@dataclass(frozen=True)
class RepositoryCatalog:
    path: Path
    config_hash: str
    cache_root: Path
    inventory_root: Path
    normalized_root: Path
    repositories: tuple[RepositoryDefinition, ...]
    inventory_policy_file: Path | None = None

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


def _remote_url(value: object, field: str, record: str) -> str:
    remote_url = _required_text(value, field, record)
    parsed = urlsplit(remote_url)
    scheme = parsed.scheme.casefold()
    if scheme in REMOTE_URL_SCHEMES and parsed.path:
        if parsed.password is not None or (
            scheme in {"http", "https"} and parsed.username is not None
        ):
            raise ValueError(f"{record}.{field} não pode conter credenciais")
        return remote_url
    if SCP_REMOTE_URL.fullmatch(remote_url):
        return remote_url
    raise ValueError(f"{record}.{field} não é uma URL Git suportada")


def _fetch_timeout(value: object, field: str, record: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{record}.{field} deve ser um inteiro em segundos")
    if not 30 <= value <= 86400:
        raise ValueError(f"{record}.{field} deve estar entre 30 e 86400 segundos")
    return value


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
        "fetch_timeout_seconds",
        "inventory_policy_file",
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
    configured_policy_file = defaults.get("inventory_policy_file")
    if configured_policy_file is not None:
        inventory_policy_file = _resolve_path(
            base,
            configured_policy_file,
            "inventory_policy_file",
            "defaults",
        )
    else:
        conventional_policy = base / "inventory-policies.toml"
        inventory_policy_file = (
            conventional_policy.resolve()
            if conventional_policy.is_file()
            else None
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
    default_fetch_timeout = _fetch_timeout(
        defaults.get("fetch_timeout_seconds", 1800),
        "fetch_timeout_seconds",
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
        "remote_url",
        "canonical_ref",
        "branch_scope",
        "access_class",
        "profile",
        "include_branches",
        "exclude_branches",
        "fetch_timeout_seconds",
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
        has_source = raw_repository.get("source") is not None
        has_remote_url = raw_repository.get("remote_url") is not None
        if has_source == has_remote_url:
            raise ValueError(
                f"{record} exige exatamente uma origem: source ou remote_url"
            )
        source = (
            _resolve_path(base, raw_repository.get("source"), "source", record)
            if has_source
            else None
        )
        remote_url = (
            _remote_url(raw_repository.get("remote_url"), "remote_url", record)
            if has_remote_url
            else None
        )
        if remote_url is not None and scope == "local":
            raise ValueError(
                f"{record}.branch_scope local exige uma origem source"
            )
        canonical_ref = _required_text(
            raw_repository.get("canonical_ref"),
            "canonical_ref",
            record,
        )
        if canonical_ref == "remote_default" and remote_url is None:
            raise ValueError(
                f"{record}.canonical_ref remote_default exige remote_url"
            )
        repositories.append(
            RepositoryDefinition(
                id=repository_id,
                enabled=enabled,
                project=_required_text(raw_repository.get("project"), "project", record),
                source=source,
                canonical_ref=canonical_ref,
                branch_scope=scope,
                access_class=access_class,
                profile=_required_text(
                    raw_repository.get("profile", default_profile),
                    "profile",
                    record,
                ),
                include_branches=include,
                exclude_branches=exclude,
                remote_url=remote_url,
                fetch_timeout_seconds=_fetch_timeout(
                    raw_repository.get(
                        "fetch_timeout_seconds",
                        default_fetch_timeout,
                    ),
                    "fetch_timeout_seconds",
                    record,
                ),
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
        inventory_policy_file=inventory_policy_file,
    )
