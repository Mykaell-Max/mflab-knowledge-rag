from __future__ import annotations

import importlib
import ipaddress
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from mflab_knowledge import __version__
from mflab_knowledge.database import (
    database_status,
    repository_status,
    search_postgres,
)
from mflab_knowledge.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_REVISION,
    DEFAULT_MAX_SEQUENCE_LENGTH,
    LocalEmbedder,
    embedding_status,
    embedding_profile_id,
    hybrid_search,
    semantic_search,
)
from mflab_knowledge.normalize import RETRIEVABLE_ACCESS_CLASSES
from mflab_knowledge.retrieval import RetrievalPolicy, load_retrieval_policy
from mflab_knowledge.service_runner import read_last_run

LogCallback = Callable[[str, str], None]
EmbedderFactory = Callable[[], LocalEmbedder]


@dataclass(frozen=True)
class ApiSettings:
    database_url: str
    state_dir: Path = Path("state")
    retrieval_config: Path | None = None
    allowed_access: frozenset[str] = frozenset({"public", "lab"})
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_revision: str = DEFAULT_EMBEDDING_REVISION
    device: str = "auto"
    max_sequence_length: int = DEFAULT_MAX_SEQUENCE_LENGTH

    def __post_init__(self) -> None:
        if not self.allowed_access or not self.allowed_access.issubset(
            RETRIEVABLE_ACCESS_CLASSES
        ):
            raise ValueError("classes de acesso do serviço inválidas ou vazias")


class RagApiService:
    """Small read-only facade shared by the HTTP transport and tests."""

    def __init__(
        self,
        settings: ApiSettings,
        *,
        log: LogCallback | None = None,
        embedder_factory: EmbedderFactory | None = None,
        retrieval_policy: RetrievalPolicy | None = None,
    ) -> None:
        self.settings = settings
        self.log = log or (lambda _message, _level="info": None)
        self.retrieval_policy = retrieval_policy or load_retrieval_policy(
            settings.retrieval_config
        )
        self._embedder_factory = embedder_factory or self._build_embedder
        self._embedder: LocalEmbedder | None = None
        self._model_lock = threading.Lock()

    @property
    def model_loaded(self) -> bool:
        return self._embedder is not None

    def _build_embedder(self) -> LocalEmbedder:
        return LocalEmbedder(
            model_id=self.settings.embedding_model,
            revision=self.settings.embedding_revision,
            device=self.settings.device,
            max_sequence_length=self.settings.max_sequence_length,
            log=self.log,
        )

    def _allowed_access(self, requested: set[str] | None) -> set[str]:
        selected = set(self.settings.allowed_access if requested is None else requested)
        if not selected:
            raise ValueError("allowed_access não pode ser vazio")
        if not selected.issubset(self.settings.allowed_access):
            denied = ", ".join(sorted(selected - self.settings.allowed_access))
            raise ValueError(
                f"classe de acesso não liberada por este serviço: {denied}"
            )
        return selected

    def health(self) -> dict[str, object]:
        try:
            status = database_status(self.settings.database_url)
        except Exception:
            return {
                "status": "unavailable",
                "version": __version__,
                "database": "unavailable",
            }
        return {
            "status": "ok",
            "version": __version__,
            "database": "ok",
            "repositories": status["repositories"],
            "chunks": status["chunks"],
        }

    def status(self) -> dict[str, object]:
        try:
            indexer = read_last_run(self.settings.state_dir)
        except ValueError:
            indexer = None
        return {
            "version": __version__,
            "database": database_status(self.settings.database_url),
            "embeddings": embedding_status(self.settings.database_url),
            "indexer": indexer,
            "search": {
                "default_mode": "hybrid",
                "allowed_access": sorted(self.settings.allowed_access),
                "model_loaded": self.model_loaded,
            },
        }

    def repositories(self) -> list[dict[str, object]]:
        profile = embedding_profile_id(
            self.settings.embedding_model,
            revision=self.settings.embedding_revision,
            max_sequence_length=self.settings.max_sequence_length,
        )
        return repository_status(
            self.settings.database_url,
            embedding_profile=profile,
            allowed_access=set(self.settings.allowed_access),
        )

    def search(
        self,
        *,
        query: str,
        mode: str = "hybrid",
        limit: int = 10,
        branch: str | None = None,
        project: str | None = None,
        path_prefix: str | None = None,
        allowed_access: set[str] | None = None,
        max_per_path: int = 2,
        include_duplicate_content: bool = False,
    ) -> dict[str, object]:
        query_text = query.strip()
        if not query_text:
            raise ValueError("consulta vazia")
        if len(query_text) > 2000:
            raise ValueError("consulta excede 2000 caracteres")
        if mode not in {"lexical", "semantic", "hybrid"}:
            raise ValueError("mode deve ser lexical, semantic ou hybrid")
        if limit < 1 or limit > 50:
            raise ValueError("limit deve estar entre 1 e 50")
        if max_per_path < 1 or max_per_path > 20:
            raise ValueError("max_per_path deve estar entre 1 e 20")

        selected_access = self._allowed_access(allowed_access)
        if "project" in selected_access and not project:
            raise ValueError("acesso project exige o filtro project")
        common: dict[str, object] = {
            "query": query_text,
            "limit": limit,
            "branch": branch,
            "project": project,
            "path_prefix": path_prefix,
            "allowed_access": selected_access,
            "max_per_path": max_per_path,
            "include_duplicate_content": include_duplicate_content,
        }
        if mode == "lexical":
            results = search_postgres(self.settings.database_url, **common)
        else:
            # SentenceTransformer and its CUDA context are shared and serialized.
            # This avoids loading one model per request and unsafe concurrent use.
            with self._model_lock:
                if self._embedder is None:
                    self._embedder = self._embedder_factory()
                if mode == "semantic":
                    results = semantic_search(
                        self.settings.database_url,
                        self._embedder,
                        **common,
                    )
                else:
                    results = hybrid_search(
                        self.settings.database_url,
                        self._embedder,
                        retrieval_policy=self.retrieval_policy,
                        **common,
                    )
        return {
            "query": query_text,
            "mode": mode,
            "count": len(results),
            "results": results,
        }


def _validate_loopback_host(host: str) -> None:
    if host.casefold() == "localhost":
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError(
            "o serviço sem autenticação aceita somente um endereço loopback"
        ) from exc
    if not address.is_loopback:
        raise ValueError(
            "o serviço sem autenticação aceita somente um endereço loopback"
        )


def run_api(
    settings: ApiSettings,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    log_level: str = "info",
    log: LogCallback | None = None,
) -> None:
    _validate_loopback_host(host)
    if port < 1024 or port > 65535:
        raise ValueError("port deve estar entre 1024 e 65535")
    try:
        uvicorn = importlib.import_module("uvicorn")
        create_app = importlib.import_module(
            "mflab_knowledge.api_http"
        ).create_app
    except ImportError as exc:
        raise ValueError(
            "suporte HTTP não instalado; execute "
            "python -m pip install -e '.[postgres,embeddings,service]'"
        ) from exc

    application = create_app(RagApiService(settings, log=log))
    uvicorn.run(
        application,
        host=host,
        port=port,
        workers=1,
        log_level=log_level,
        access_log=True,
    )
