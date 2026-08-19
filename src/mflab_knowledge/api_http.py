from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from contextlib import asynccontextmanager
from functools import lru_cache
from importlib.resources import files
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from mflab_knowledge import __version__
from mflab_knowledge.api import RagApiService, api_request_authorized
from mflab_knowledge.ask_jobs import AskJobs
from mflab_knowledge.generation import (
    GenerationNotConfiguredError,
    GenerationUnavailableError,
)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    mode: Literal["lexical", "semantic", "hybrid"] = "hybrid"
    limit: int = Field(default=10, ge=1, le=50)
    branch: str | None = Field(default=None, max_length=512)
    project: str | None = Field(default=None, max_length=512)
    path_prefix: str | None = Field(default=None, max_length=2048)
    allowed_access: (
        set[Literal["public", "lab", "project", "restricted"]] | None
    ) = None
    max_per_path: int = Field(default=2, ge=1, le=20)
    include_duplicate_content: bool = False


class ContextRequest(SearchRequest):
    max_context_characters: int = Field(default=24000, ge=1000, le=100000)


class AskRequest(ContextRequest):
    max_output_tokens: int | None = Field(default=None, ge=64, le=8192)
    temperature: float | None = Field(default=None, ge=0, le=1)


class StructureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: str = Field(min_length=1, max_length=512)
    branch: str = Field(min_length=1, max_length=512)
    allowed_access: set[Literal["public", "lab", "project", "restricted"]] | None = None
    anchor_limit: int = Field(default=8, ge=1, le=50)


class AdminLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=512)


class _AdminSessions:
    cookie_name = "mflab_admin_session"
    lifetime_seconds = 8 * 60 * 60
    failure_window_seconds = 5 * 60
    maximum_failures = 5

    def __init__(self) -> None:
        self._sessions: dict[str, float] = {}
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def login(
        self,
        expected_password: str | None,
        supplied_password: str,
        client: str,
    ) -> tuple[str | None, bool]:
        now = time.monotonic()
        with self._lock:
            recent = [
                moment
                for moment in self._failures.get(client, [])
                if now - moment < self.failure_window_seconds
            ]
            if len(recent) >= self.maximum_failures:
                self._failures[client] = recent
                return None, True
            accepted = bool(expected_password) and hmac.compare_digest(
                expected_password.encode("utf-8"),
                supplied_password.encode("utf-8"),
            )
            if not accepted:
                recent.append(now)
                self._failures[client] = recent
                return None, False
            self._failures.pop(client, None)
            token = secrets.token_urlsafe(32)
            self._sessions[self._digest(token)] = now + self.lifetime_seconds
            return token, False

    def authorized(self, token: str | None) -> bool:
        if not token:
            return False
        now = time.monotonic()
        digest = self._digest(token)
        with self._lock:
            expiry = self._sessions.get(digest)
            if expiry is None:
                return False
            if expiry <= now:
                self._sessions.pop(digest, None)
                return False
            return True

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(self._digest(token), None)


@lru_cache(maxsize=4)
def _web_asset(name: str) -> str:
    return (
        files("mflab_knowledge")
        .joinpath("web", name)
        .read_text(encoding="utf-8")
    )


def create_app(service: RagApiService) -> FastAPI:
    ask_jobs = AskJobs()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        ask_jobs.close()

    app = FastAPI(
        title="MFLab Knowledge RAG",
        version=__version__,
        description="API local e somente leitura para recuperação citável.",
        lifespan=lifespan,
    )
    admin_sessions = _AdminSessions()

    def web_access() -> set[str]:
        selected = set(service.settings.allowed_access).intersection(
            {"public", "lab"}
        )
        if not selected:
            raise HTTPException(
                status_code=503,
                detail="nenhuma classe de acesso foi liberada para a interface",
            )
        return selected

    def web_request_values(request: SearchRequest) -> dict[str, object]:
        values = request.model_dump()
        requested = values.get("allowed_access")
        ceiling = web_access()
        if requested is not None and not set(requested).issubset(ceiling):
            raise HTTPException(
                status_code=400,
                detail="classe de acesso não liberada para a interface",
            )
        values["allowed_access"] = ceiling if requested is None else set(requested)
        return values

    @app.middleware("http")
    async def require_api_key(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        public_paths = {
            "/health",
            "/ui",
            "/ui/",
            "/ui/app.css",
            "/ui/app.js",
            "/ui/mflab-logo.svg",
            "/ui-api/repositories",
            "/ui-api/search",
            "/ui-api/ask",
            "/ui-api/ask-jobs",
            "/ui-api/admin/session",
            "/ui-api/admin/status",
        }
        is_public = request.url.path in public_paths or request.url.path.startswith(
            "/ui-api/ask-jobs/"
        )
        if not is_public and not api_request_authorized(
            service.settings.api_key,
            request.headers.get("authorization"),
            client_host=request.client.host if request.client else None,
        ):
            return JSONResponse(
                status_code=401,
                content={"detail": "chave da API ausente ou inválida"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)

    @app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/ui/", response_class=HTMLResponse, include_in_schema=False)
    def user_interface() -> HTMLResponse:
        return HTMLResponse(
            _web_asset("index.html"),
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; style-src 'self'; "
                    "img-src 'self' data:; connect-src 'self'; "
                    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
                ),
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

    @app.get("/ui/app.css", include_in_schema=False)
    def user_interface_css() -> Response:
        return Response(
            _web_asset("app.css"),
            media_type="text/css",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/ui/app.js", include_in_schema=False)
    def user_interface_javascript() -> Response:
        return Response(
            _web_asset("app.js"),
            media_type="text/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/ui/mflab-logo.svg", include_in_schema=False)
    def user_interface_logo() -> Response:
        return Response(
            _web_asset("mflab-logo.svg"),
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/")
    def root() -> dict[str, object]:
        return {
            "service": "mflab-knowledge-rag",
            "version": __version__,
            "docs": "/docs",
            "interface": "/ui",
            "endpoints": [
                "/health",
                "/status",
                "/repositories",
                "/structure",
                "/search",
                "/context",
                "/ask",
                "/ui",
            ],
        }

    @app.get("/health")
    def health() -> object:
        result = service.health()
        if result["status"] != "ok":
            return JSONResponse(status_code=503, content=result)
        return result

    @app.get("/status")
    def status() -> dict[str, object]:
        try:
            return service.status()
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="estado do serviço temporariamente indisponível",
            ) from None

    @app.get("/repositories")
    def repositories() -> dict[str, object]:
        try:
            values = service.repositories()
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="catálogo temporariamente indisponível",
            ) from None
        return {"count": len(values), "repositories": values}

    @app.get("/ui-api/repositories", include_in_schema=False)
    def web_repositories() -> dict[str, object]:
        try:
            values = service.repositories(allowed_access=web_access())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="catálogo temporariamente indisponível",
            ) from None
        return {"count": len(values), "repositories": values}

    @app.post("/search")
    def search(request: SearchRequest) -> dict[str, object]:
        try:
            return service.search(**request.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except Exception:
            raise HTTPException(
                status_code=500,
                detail="falha interna durante a recuperação",
            ) from None

    @app.post("/structure")
    def structure(request: StructureRequest) -> dict[str, object]:
        try:
            return service.structure(**request.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except Exception:
            raise HTTPException(
                status_code=500,
                detail="falha interna durante o mapeamento estrutural",
            ) from None

    @app.post("/ui-api/search", include_in_schema=False)
    def web_search(request: SearchRequest) -> dict[str, object]:
        try:
            return service.search(**web_request_values(request))
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except Exception:
            raise HTTPException(
                status_code=500,
                detail="falha interna durante a recuperação",
            ) from None

    @app.post("/context")
    def context(request: ContextRequest) -> dict[str, object]:
        try:
            return service.context(**request.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except Exception:
            raise HTTPException(
                status_code=500,
                detail="falha interna durante a montagem do contexto",
            ) from None

    @app.post("/ask")
    def ask(request: AskRequest) -> dict[str, object]:
        try:
            return service.ask(**request.model_dump())
        except GenerationNotConfiguredError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        except GenerationUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except Exception:
            raise HTTPException(
                status_code=500,
                detail="falha interna durante a geração da resposta",
            ) from None

    @app.post("/ui-api/ask", include_in_schema=False)
    def web_ask(request: AskRequest) -> dict[str, object]:
        try:
            return service.ask(**web_request_values(request))
        except HTTPException:
            raise
        except GenerationNotConfiguredError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        except GenerationUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except Exception:
            raise HTTPException(
                status_code=500,
                detail="falha interna durante a geração da resposta",
            ) from None

    @app.post("/ui-api/ask-jobs", status_code=202, include_in_schema=False)
    def create_web_ask_job(request: AskRequest) -> dict[str, object]:
        try:
            values = web_request_values(request)
            job_id = ask_jobs.submit(
                lambda progress: service.ask(
                    **values,
                    progress_callback=progress,
                )
            )
        except HTTPException:
            raise
        except RuntimeError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return {"job_id": job_id, "status": "queued"}

    @app.get("/ui-api/ask-jobs/{job_id}", include_in_schema=False)
    def web_ask_job(job_id: str) -> dict[str, object]:
        if len(job_id) > 128:
            raise HTTPException(status_code=404, detail="investigação não encontrada")
        value = ask_jobs.get(job_id)
        if value is None:
            raise HTTPException(status_code=404, detail="investigação não encontrada")
        return value

    @app.post("/ui-api/admin/session", include_in_schema=False)
    def admin_login(
        credentials: AdminLoginRequest,
        request: Request,
        response: Response,
    ) -> dict[str, object]:
        if service.settings.admin_password is None:
            raise HTTPException(
                status_code=503,
                detail="painel administrativo ainda não foi configurado",
            )
        client = request.client.host if request.client else "unknown"
        token, throttled = admin_sessions.login(
            service.settings.admin_password,
            credentials.password,
            client,
        )
        if throttled:
            raise HTTPException(
                status_code=429,
                detail="muitas tentativas; aguarde alguns minutos",
            )
        if token is None:
            raise HTTPException(
                status_code=401,
                detail="senha administrativa inválida",
            )
        response.set_cookie(
            key=admin_sessions.cookie_name,
            value=token,
            max_age=admin_sessions.lifetime_seconds,
            httponly=True,
            samesite="strict",
            path="/ui-api/admin",
        )
        return {
            "authenticated": True,
            "expires_in_seconds": admin_sessions.lifetime_seconds,
        }

    @app.delete("/ui-api/admin/session", include_in_schema=False)
    def admin_logout(request: Request, response: Response) -> dict[str, bool]:
        admin_sessions.logout(request.cookies.get(admin_sessions.cookie_name))
        response.delete_cookie(
            admin_sessions.cookie_name,
            path="/ui-api/admin",
            httponly=True,
            samesite="strict",
        )
        return {"authenticated": False}

    @app.get("/ui-api/admin/status", include_in_schema=False)
    def administration_status(request: Request) -> dict[str, object]:
        if not admin_sessions.authorized(
            request.cookies.get(admin_sessions.cookie_name)
        ):
            raise HTTPException(
                status_code=401,
                detail="autenticação administrativa necessária",
            )
        try:
            return service.administration_status()
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="estado administrativo temporariamente indisponível",
            ) from None

    return app
