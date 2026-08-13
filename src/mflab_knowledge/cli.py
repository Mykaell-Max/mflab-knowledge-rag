from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit

from mflab_knowledge.credentials import load_git_credentials
from mflab_knowledge.inventory import build_inventory, detect_git_metadata, write_yaml
from mflab_knowledge.repository import prepare_repository_snapshot
from mflab_knowledge.sync import sync_repository_branches


class ConsoleReporter:
    def __init__(self, quiet: bool = False) -> None:
        self.quiet = quiet
        self._last_bucket = -1
        self._last_percent = -1
        self._interactive = sys.stderr.isatty()

    def log(self, message: str) -> None:
        if not self.quiet:
            print(f"[mflab] {message}", file=sys.stderr, flush=True)

    def progress(self, current: int, total: int, path: str) -> None:
        if self.quiet:
            return
        percent = 100 if total == 0 else int(current * 100 / total)
        if self._interactive:
            if percent == self._last_percent and current < total:
                return
            self._last_percent = percent
            width = 28
            filled = int(width * percent / 100)
            bar = "#" * filled + "-" * (width - filled)
            short_path = path if len(path) <= 48 else f"...{path[-45:]}"
            end = "\n" if current >= total else "\r"
            print(
                f"[mflab] [{bar}] {percent:3d}% {current}/{total} {short_path:<48}",
                file=sys.stderr,
                end=end,
                flush=True,
            )
        else:
            bucket = percent // 10
            if bucket != self._last_bucket or current >= total:
                self._last_bucket = bucket
                print(
                    f"[mflab] inventário {percent:3d}% ({current}/{total})",
                    file=sys.stderr,
                    flush=True,
                )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mflab-knowledge",
        description="Inventário e indexação somente leitura do MFLab.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser(
        "inventory",
        help="Descobre uma fonte local e gera um inventário YAML.",
    )
    inventory.add_argument("--source", required=True, type=Path)
    inventory.add_argument("--project", required=True)
    inventory.add_argument(
        "--access-class",
        default="lab",
        choices=("public", "lab", "project", "restricted", "pending"),
    )
    inventory.add_argument(
        "--profile",
        default="auto",
        choices=("auto", "generic", "mfsim-ng-pilot"),
        help="Política de seleção; 'auto' reconhece o MFSim-NG.",
    )
    inventory.add_argument(
        "--ref",
        help="Branch, tag ou commit. Por padrão usa a branch atual da fonte.",
    )
    inventory.add_argument(
        "--cache-dir",
        default=Path("cache"),
        type=Path,
        help="Cache privado para mirrors e snapshots (padrão: ./cache).",
    )
    inventory.add_argument(
        "--no-snapshot",
        action="store_true",
        help="Lê a fonte diretamente; use apenas para snapshots sem Git.",
    )
    inventory.add_argument("--quiet", action="store_true")
    inventory.add_argument("--output", required=True, type=Path)

    sync = subparsers.add_parser(
        "sync",
        help="Descobre branches e gera inventários isolados em uma execução.",
    )
    sync.add_argument("--source", required=True, type=Path)
    sync.add_argument("--project", required=True)
    sync.add_argument("--canonical-ref", default="origin/master")
    sync.add_argument(
        "--branch-scope",
        default="remote",
        choices=("remote", "local", "all"),
        help="Branches remotas são a fonte padrão do laboratório.",
    )
    sync.add_argument(
        "--access-class",
        default="lab",
        choices=("public", "lab", "project", "restricted", "pending"),
    )
    sync.add_argument(
        "--profile",
        default="auto",
        choices=("auto", "generic", "mfsim-ng-pilot"),
    )
    sync.add_argument("--cache-dir", default=Path("cache"), type=Path)
    sync.add_argument(
        "--env-file",
        default=Path(".env"),
        type=Path,
        help="Credenciais HTTPS locais (padrão: ./.env).",
    )
    sync.add_argument(
        "--output-dir",
        default=Path("inventory/sync"),
        type=Path,
    )
    sync.add_argument(
        "--offline",
        action="store_true",
        help="Não consulta o remote; usa apenas refs existentes na fonte.",
    )
    sync.add_argument("--quiet", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "inventory":
        reporter = ConsoleReporter(args.quiet)
        try:
            source = args.source
            metadata_override = None
            source_metadata = detect_git_metadata(source.expanduser().resolve())
            if source_metadata["versioned"] and not args.no_snapshot:
                snapshot = prepare_repository_snapshot(
                    source,
                    project=args.project,
                    cache_dir=args.cache_dir,
                    ref=args.ref,
                    log=reporter.log,
                )
                source = snapshot.path
                metadata_override = snapshot.metadata()
            elif args.ref:
                raise ValueError("--ref exige uma fonte Git e snapshot isolado")
            else:
                reporter.log("Fonte sem Git: inventário direto em modo somente leitura")

            reporter.log(f"Inventariando {source}")
            inventory = build_inventory(
                source=source,
                project=args.project,
                access_class=args.access_class,
                profile=args.profile,
                metadata_override=metadata_override,
                progress=reporter.progress,
            )
            reporter.log(f"Gravando catálogo em {args.output}")
            write_yaml(inventory, args.output)
        except (OSError, ValueError) as exc:
            raise SystemExit(f"erro: {exc}") from exc

        summary = inventory["summary"]
        print(
            json.dumps(
                {
                    "output": str(args.output.resolve()),
                    "discovered": summary["discovered_files"],
                    "indexable": summary["indexable_files"],
                    "excluded": summary["excluded_files"],
                    "errors": summary["errors"],
                },
                ensure_ascii=False,
            )
        )
        return 0

    if args.command == "sync":
        reporter = ConsoleReporter(args.quiet)
        try:
            source_metadata = detect_git_metadata(args.source.expanduser().resolve())
            remote_url = source_metadata.get("remote_url")
            remote_scheme = (
                urlsplit(remote_url).scheme.casefold()
                if isinstance(remote_url, str)
                else ""
            )
            needs_https_credentials = (
                not args.offline and remote_scheme in {"http", "https"}
            )
            credentials = (
                load_git_credentials(args.env_file)
                if needs_https_credentials
                else None
            )
            if credentials is not None:
                reporter.log("Credenciais HTTPS não interativas configuradas")
            result = sync_repository_branches(
                source=args.source,
                project=args.project,
                canonical_ref=args.canonical_ref,
                branch_scope=args.branch_scope,
                access_class=args.access_class,
                profile=args.profile,
                cache_dir=args.cache_dir,
                output_dir=args.output_dir,
                refresh_remote=not args.offline,
                credentials=credentials,
                log=reporter.log,
                progress=reporter.progress,
            )
        except (OSError, ValueError) as exc:
            raise SystemExit(f"erro: {exc}") from exc
        print(json.dumps(result, ensure_ascii=False))
        return 0

    return 2
