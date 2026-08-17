from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from mflab_knowledge import __version__
from mflab_knowledge.api import RagApiService


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
            "endpoints": ["/health", "/status", "/repositories", "/search"],
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

    return app
