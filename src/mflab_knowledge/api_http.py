from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from mflab_knowledge import __version__
from mflab_knowledge.api import RagApiService
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
    allowed_access: set[Literal["public", "lab", "project", "restricted"]] | None = None
    max_per_path: int = Field(default=2, ge=1, le=20)
    include_duplicate_content: bool = False


class ContextRequest(SearchRequest):
    max_context_characters: int = Field(default=24000, ge=1000, le=100000)


class AskRequest(ContextRequest):
    max_output_tokens: int | None = Field(default=None, ge=64, le=8192)
    temperature: float | None = Field(default=None, ge=0, le=1)


def create_app(service: RagApiService) -> FastAPI:
    app = FastAPI(
        title="MFLab Knowledge RAG",
        version=__version__,
        description="API local e somente leitura para recuperação citável.",
    )

    @app.get("/")
    def root() -> dict[str, object]:
        return {
            "service": "mflab-knowledge-rag",
            "version": __version__,
            "docs": "/docs",
            "endpoints": [
                "/health",
                "/status",
                "/repositories",
                "/search",
                "/context",
                "/ask",
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

    return app
