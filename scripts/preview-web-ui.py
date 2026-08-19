#!/usr/bin/env python3
"""Serve the real web UI with generic in-memory data for visual review."""

from __future__ import annotations

import argparse
import json
import tomllib
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "src" / "mflab_knowledge" / "web"


def _version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


VERSION = _version()
NOW = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

REPOSITORIES = [
    {
        "repository_id": "solver-principal-demo",
        "project": "Solver principal",
        "documents": 1380,
        "occurrences": 9820,
        "branches": 18,
        "canonical_branches": ["main"],
        "chunks": 12450,
        "embedded_chunks": 12450,
        "embedding_coverage": 1.0,
    },
    {
        "repository_id": "solver-legado-demo",
        "project": "Solver legado",
        "documents": 4210,
        "occurrences": 146200,
        "branches": 106,
        "canonical_branches": ["master"],
        "chunks": 83420,
        "embedded_chunks": 83420,
        "embedding_coverage": 1.0,
    },
]

SOURCE = {
    "source_id": "S1",
    "project": "Solver principal",
    "path": "src/model/solver.cpp",
    "citation": "Solver principal main@7d31c13 src/model/solver.cpp:L84-L112",
    "selected_occurrence": {
        "branch": "main",
        "commit_sha": "7d31c13a5c8e71ea560a17d45c27a48e35db9231",
    },
    "text": (
        "O trecho inicializa os dados do domínio, valida a configuração "
        "recebida e prepara as estruturas utilizadas pelo solucionador."
    ),
}


class PreviewHandler(BaseHTTPRequestHandler):
    server_version = "MFLabPreview/1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_bytes(self, value: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(value)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(value)

    def _send_json(self, value: Any, status: int = 200) -> None:
        self._send_bytes(
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def _send_asset(self, name: str, content_type: str) -> None:
        try:
            value = (WEB_ROOT / name).read_bytes()
        except OSError:
            self._send_json({"detail": "arquivo de interface ausente"}, 404)
            return
        self._send_bytes(value, content_type)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/ui")
            self.end_headers()
        elif path in {"/ui", "/ui/"}:
            self._send_asset("index.html", "text/html; charset=utf-8")
        elif path == "/ui/app.css":
            self._send_asset("app.css", "text/css; charset=utf-8")
        elif path == "/ui/app.js":
            self._send_asset("app.js", "text/javascript; charset=utf-8")
        elif path == "/health":
            self._send_json(
                {
                    "status": "ok",
                    "version": VERSION,
                    "database": "ok",
                    "repositories": len(REPOSITORIES),
                    "chunks": sum(int(item["chunks"]) for item in REPOSITORIES),
                }
            )
        elif path == "/status":
            self._send_json(
                {
                    "version": VERSION,
                    "database": {"status": "ok"},
                    "embeddings": {"status": "ready"},
                    "indexer": {
                        "run_id": "preview-local",
                        "status": "success",
                        "stage": "Pipeline concluído",
                        "duration_seconds": 54.2,
                        "updated_at": NOW,
                        "progress": {"percent": 100},
                    },
                    "search": {"default_mode": "hybrid", "model_loaded": True},
                    "generation": {"configured": True, "local_only": True},
                    "authentication": {"configured": False, "mode": "preview"},
                }
            )
        elif path == "/repositories":
            self._send_json({"count": len(REPOSITORIES), "repositories": REPOSITORIES})
        else:
            self._send_json({"detail": "rota não encontrada"}, 404)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length", "0"))
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json({"detail": "JSON inválido"}, 400)
            return

        if path == "/search":
            result = {**SOURCE, "source_id": None}
            result["text"] = f"Resultado ilustrativo para: {request.get('query', '')}"
            self._send_json(
                {
                    "query": request.get("query", ""),
                    "mode": request.get("mode", "hybrid"),
                    "count": 1,
                    "results": [result],
                }
            )
        elif path == "/ask":
            self._send_json(
                {
                    "answer": (
                        "Esta é uma resposta de demonstração. Na Morgoth, o texto será "
                        "gerado a partir das evidências recuperadas e acompanhado das "
                        "respectivas citações [S1]."
                    ),
                    "abstained": False,
                    "grounding_status": "cited",
                    "citations_used": ["S1"],
                    "duration_seconds": 3.4,
                    "sources": [SOURCE],
                }
            )
        else:
            self._send_json({"detail": "rota não encontrada"}, 404)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prévia visual local do painel MFLab")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port deve estar entre 1024 e 65535")

    url = f"http://127.0.0.1:{args.port}/ui"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), PreviewHandler)
    print("Prévia visual local do MFLab Knowledge")
    print(f"Abra: {url}")
    print("Use Ctrl+C para encerrar. Nenhum serviço ou credencial será acessado.")
    if args.open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPrévia encerrada.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
