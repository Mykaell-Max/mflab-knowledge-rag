from __future__ import annotations

import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

RETRIEVAL_POLICY_SCHEMA_VERSION = "0.1"


@dataclass(frozen=True)
class RetrievalPolicy:
    """Auditable limits for query-time structural context expansion."""

    max_context_results: int = 2
    same_document_min_hits: int = 2
    same_document_strength: int = 6
    paired_source_strength: int = 5
    directory_min_documents: int = 2
    directory_require_root_document: bool = True
    directory_strength: int = 7
    symbol_hints_limit: int = 24
    symbol_strength: int = 4
    context_candidate_limit: int = 50
    same_document_extensions: tuple[str, ...] = (".h", ".hh", ".hpp", ".hxx")
    directory_extensions: tuple[str, ...] = (
        ".cfg",
        ".ini",
        ".json",
        ".toml",
        ".xml",
        ".yaml",
        ".yml",
    )

    def __post_init__(self) -> None:
        for name, lower, upper in (
            ("max_context_results", 0, 10),
            ("same_document_min_hits", 2, 20),
            ("same_document_strength", 0, 100),
            ("paired_source_strength", 0, 100),
            ("directory_min_documents", 2, 20),
            ("directory_strength", 0, 100),
            ("symbol_hints_limit", 0, 100),
            ("symbol_strength", 0, 100),
            ("context_candidate_limit", 1, 500),
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"retrieval.context.{name} deve ser inteiro")
            if value < lower or value > upper:
                raise ValueError(
                    f"retrieval.context.{name} deve estar entre {lower} e {upper}"
                )
        for name in ("same_document_extensions", "directory_extensions"):
            extensions = getattr(self, name)
            if not extensions:
                raise ValueError(f"retrieval.context.{name} não pode ser vazio")
            normalized: list[str] = []
            for extension in extensions:
                if not isinstance(extension, str) or not extension.startswith("."):
                    raise ValueError(
                        f"retrieval.context.{name} exige extensões como .json"
                    )
                normalized.append(extension.casefold())
            object.__setattr__(self, name, tuple(dict.fromkeys(normalized)))
        if not isinstance(self.directory_require_root_document, bool):
            raise ValueError(
                "retrieval.context.directory_require_root_document deve ser booleano"
            )

    def fingerprint(self) -> dict[str, object]:
        return asdict(self)


def load_retrieval_policy(path: Path | None = None) -> RetrievalPolicy:
    """Load an optional local policy; absent default retrieval.toml uses defaults."""

    selected = path
    if selected is None:
        conventional = Path("retrieval.toml")
        if not conventional.is_file():
            return RetrievalPolicy()
        selected = conventional
    policy_file = selected.expanduser().resolve()
    try:
        value = tomllib.loads(policy_file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"política de recuperação não encontrada: {policy_file}") from exc
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"política de recuperação inválida: {policy_file}: {exc}") from exc
    if value.get("schema_version") != RETRIEVAL_POLICY_SCHEMA_VERSION:
        raise ValueError("versão incompatível da política de recuperação")
    context = value.get("context", {})
    if not isinstance(context, dict):
        raise ValueError("retrieval.context deve ser uma tabela TOML")
    allowed = set(RetrievalPolicy.__dataclass_fields__)
    unknown = sorted(set(context) - allowed)
    if unknown:
        raise ValueError(
            "opções desconhecidas em retrieval.context: " + ", ".join(unknown)
        )
    parameters = dict(context)
    for name in ("same_document_extensions", "directory_extensions"):
        extensions = parameters.get(name)
        if extensions is not None:
            if not isinstance(extensions, list):
                raise ValueError(f"retrieval.context.{name} deve ser uma lista")
            parameters[name] = tuple(extensions)
    return RetrievalPolicy(**parameters)
