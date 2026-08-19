#!/usr/bin/env python3
"""Serve the real web UI with generic in-memory data for visual review."""

from __future__ import annotations

import argparse
import json
import secrets
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

    def _send_bytes(
        self,
        value: bytes,
        content_type: str,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(value)))
        self.send_header("Cache-Control", "no-store")
        for name, header_value in (headers or {}).items():
            self.send_header(name, header_value)
        self.end_headers()
        self.wfile.write(value)

    def _send_json(
        self,
        value: Any,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._send_bytes(
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
            headers,
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
        elif path == "/ui/mflab-logo.svg":
            self._send_asset("mflab-logo.svg", "image/svg+xml")
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
        elif path in {"/repositories", "/ui-api/repositories"}:
            self._send_json({"count": len(REPOSITORIES), "repositories": REPOSITORIES})
        elif path == "/ui-api/admin/status":
            token = getattr(self.server, "admin_token", None)
            if not token or f"mflab_admin_session={token}" not in self.headers.get("Cookie", ""):
                self._send_json({"detail": "autenticação administrativa necessária"}, 401)
                return
            self._send_json(
                {
                    "service": {
                        "status": "ok",
                        "version": VERSION,
                        "uptime_seconds": 3725,
                        "process_id": 24817,
                    },
                    "machine": {
                        "hostname": "servidor-laboratorio",
                        "operating_system": "Linux",
                        "release": "6.14.0",
                        "architecture": "x86_64",
                        "python": "3.14.0",
                        "logical_cpus": 32,
                        "memory": {
                            "total_bytes": 68_719_476_736,
                            "used_bytes": 18_253_611_008,
                            "available_bytes": 50_465_865_728,
                            "used_percent": 26.6,
                        },
                        "disk": {
                            "total_bytes": 2_000_398_934_016,
                            "used_bytes": 713_843_507_200,
                            "free_bytes": 1_286_555_426_816,
                            "used_percent": 35.7,
                        },
                        "gpus": [
                            {
                                "name": "NVIDIA GPU",
                                "memory_total_mib": 16311,
                                "memory_used_mib": 2879,
                                "utilization_percent": 1,
                                "temperature_c": 42,
                            }
                        ],
                    },
                    "database": {
                        "status": "ok",
                        "repositories": len(REPOSITORIES),
                        "chunks": sum(int(item["chunks"]) for item in REPOSITORIES),
                    },
                    "embeddings": {"status": "ready", "models": []},
                    "generation": {
                        "configured": True,
                        "model": "modelo-local",
                        "local_only": True,
                    },
                    "indexer": {
                        "run_id": "preview-local",
                        "status": "success",
                        "stage": "Pipeline concluído",
                        "duration_seconds": 54.2,
                        "updated_at": NOW,
                        "progress": {"percent": 100},
                    },
                    "repositories": REPOSITORIES,
                    "authentication": {
                        "api_key_configured": True,
                        "admin_password_configured": True,
                    },
                }
            )
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

        if path in {"/search", "/ui-api/search"}:
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
        elif path in {"/ask", "/ui-api/ask"}:
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
        elif path == "/ui-api/admin/session":
            if request.get("password") != getattr(self.server, "admin_password", ""):
                self._send_json({"detail": "senha administrativa inválida"}, 401)
                return
            token = secrets.token_urlsafe(24)
            self.server.admin_token = token
            self._send_json(
                {"authenticated": True, "expires_in_seconds": 28800},
                headers={
                    "Set-Cookie": (
                        f"mflab_admin_session={token}; HttpOnly; "
                        "SameSite=Strict; Path=/ui-api/admin"
                    )
                },
            )
        else:
            self._send_json({"detail": "rota não encontrada"}, 404)

    def do_DELETE(self) -> None:
        path = self.path.split("?", 1)[0]
        if path != "/ui-api/admin/session":
            self._send_json({"detail": "rota não encontrada"}, 404)
            return
        self.server.admin_token = None
        self._send_json(
            {"authenticated": False},
            headers={
                "Set-Cookie": (
                    "mflab_admin_session=; Max-Age=0; HttpOnly; "
                    "SameSite=Strict; Path=/ui-api/admin"
                )
            },
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Prévia visual local do painel MFLab")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    parser.add_argument("--admin-password", default="preview-admin")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port deve estar entre 1024 e 65535")

    url = f"http://127.0.0.1:{args.port}/ui"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), PreviewHandler)
    server.admin_password = args.admin_password
    server.admin_token = None
    print("Prévia visual local do MFLab Knowledge")
    print(f"Abra: {url}")
    print(f"Senha administrativa da prévia: {args.admin_password}")
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
