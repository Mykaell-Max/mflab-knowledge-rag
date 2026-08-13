from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit

from mflab_knowledge.credentials import load_git_credentials
from mflab_knowledge.evaluate import evaluate_suite
from mflab_knowledge.inventory import build_inventory, detect_git_metadata, write_yaml
from mflab_knowledge.normalize import normalize_manifest, search_chunks
from mflab_knowledge.repository import prepare_repository_snapshot
from mflab_knowledge.sync import sync_repository_branches


class ConsoleReporter:
    _STYLES = {
        "info": ("INFO", "36"),
        "success": ("OK", "1;32"),
        "warning": ("AVISO", "1;33"),
        "error": ("ERRO", "1;31"),
        "result": ("RESULTADO", "1;35"),
        "progress": ("PROGRESSO", "34"),
    }

    def __init__(
        self,
        quiet: bool = False,
        color: str = "auto",
        progress_label: str = "inventário",
    ) -> None:
        self.quiet = quiet
        self.progress_label = progress_label
        self._last_bucket = -1
        self._last_percent = -1
        self._interactive = sys.stderr.isatty()
        if color == "always":
            self._color = True
        elif color == "never":
            self._color = False
        else:
            self._color = (
                self._interactive
                and "NO_COLOR" not in os.environ
                and os.environ.get("TERM", "").casefold() != "dumb"
            )

    def _styled(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self._color else text

    def log(self, message: str, level: str = "info") -> None:
        if not self.quiet or level == "error":
            label, style = self._STYLES.get(level, self._STYLES["info"])
            prefix = self._styled(f"[mflab:{label}]", style)
            print(f"{prefix} {message}", file=sys.stderr, flush=True)

    def success(self, message: str) -> None:
        self.log(message, "success")

    def warning(self, message: str) -> None:
        self.log(message, "warning")

    def error(self, message: str) -> None:
        self.log(message, "error")

    def result(self, message: str) -> None:
        self.log(message, "result")

    def progress(self, current: int, total: int, path: str) -> None:
        if self.quiet:
            return
        percent = 100 if total == 0 else int(current * 100 / total)
        progress_name, progress_style = self._STYLES["progress"]
        prefix = self._styled(f"[mflab:{progress_name}]", progress_style)
        if self._interactive:
            if percent == self._last_percent and current < total:
                return
            self._last_percent = percent
            width = 28
            filled_width = int(width * percent / 100)
            filled = self._styled("#" * filled_width, "32")
            remaining = self._styled("-" * (width - filled_width), "2")
            bar = filled + remaining
            short_path = path if len(path) <= 48 else f"...{path[-45:]}"
            end = "\n" if current >= total else "\r"
            print(
                f"{prefix} [{bar}] {percent:3d}% "
                f"{current}/{total} {short_path:<48}",
                file=sys.stderr,
                end=end,
                flush=True,
            )
        else:
            bucket = percent // 10
            if bucket != self._last_bucket or current >= total:
                self._last_bucket = bucket
                print(
                    f"{prefix} {self.progress_label} "
                    f"{percent:3d}% ({current}/{total})",
                    file=sys.stderr,
                    flush=True,
                )


def _add_console_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="Cores nos logs: auto, always ou never (padrão: auto).",
    )
    parser.add_argument("--quiet", action="store_true")


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
    _add_console_options(inventory)
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
    _add_console_options(sync)

    normalize = subparsers.add_parser(
        "normalize",
        help="Normaliza catálogos sincronizados em documentos e chunks JSONL.",
    )
    normalize.add_argument("--manifest", required=True, type=Path)
    normalize.add_argument(
        "--output-dir",
        default=Path("data/normalized"),
        type=Path,
    )
    normalize.add_argument("--cache-dir", default=Path("cache"), type=Path)
    _add_console_options(normalize)

    search = subparsers.add_parser(
        "search",
        help="Executa busca lexical local nos chunks normalizados.",
    )
    search.add_argument("--chunks", required=True, type=Path)
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--branch")
    search.add_argument("--project")
    search.add_argument("--path-prefix")
    search.add_argument(
        "--max-per-path",
        type=int,
        default=2,
        help="Máximo de chunks por arquivo (padrão: 2).",
    )
    search.add_argument(
        "--include-duplicate-content",
        action="store_true",
        help="Inclui chunks textualmente idênticos no resultado.",
    )
    search.add_argument(
        "--allow-access",
        action="append",
        choices=("public", "lab", "project", "restricted"),
        help="Classe liberada; repita para mais de uma (padrão: public e lab).",
    )
    _add_console_options(search)

    evaluate = subparsers.add_parser(
        "evaluate",
        help="Executa uma suíte versionada de avaliação da recuperação.",
    )
    evaluate.add_argument("--suite", required=True, type=Path)
    evaluate.add_argument("--chunks", required=True, type=Path)
    evaluate.add_argument(
        "--output",
        default=Path("data/evaluation.generated.json"),
        type=Path,
    )
    _add_console_options(evaluate)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "inventory":
        reporter = ConsoleReporter(args.quiet, args.color)
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
                reporter.warning(
                    "Fonte sem Git: inventário direto em modo somente leitura"
                )

            reporter.log(f"Inventariando {source}")
            inventory = build_inventory(
                source=source,
                project=args.project,
                access_class=args.access_class,
                profile=args.profile,
                metadata_override=metadata_override,
                progress=reporter.progress,
            )
            reporter.success(f"Gravando catálogo em {args.output}")
            write_yaml(inventory, args.output)
        except (OSError, ValueError) as exc:
            reporter.error(str(exc))
            return 1

        summary = inventory["summary"]
        result_level = "warning" if int(summary["errors"]) else "result"
        reporter.log(
            f"Inventário concluído: {summary['indexable_files']} indexáveis, "
            f"{summary['excluded_files']} excluídos, {summary['errors']} erros",
            result_level,
        )
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
        reporter = ConsoleReporter(args.quiet, args.color)
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
                reporter.success("Credenciais HTTPS não interativas configuradas")
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
            reporter.error(str(exc))
            return 1
        result_level = "warning" if int(result["errors"]) else "result"
        reporter.log(
            f"Sincronização concluída: {result['branches']} branches, "
            f"{result['unique_commits']} commits, "
            f"{result['inventories_built']} inventários calculados, "
            f"{result['inventories_reused']} reutilizados, "
            f"{result['errors']} erros",
            result_level,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.command == "normalize":
        reporter = ConsoleReporter(
            args.quiet,
            args.color,
            progress_label="normalização",
        )
        try:
            result = normalize_manifest(
                manifest_path=args.manifest,
                output_dir=args.output_dir,
                cache_dir=args.cache_dir,
                log=reporter.log,
                progress=reporter.progress,
            )
        except (OSError, ValueError) as exc:
            reporter.error(str(exc))
            return 1
        result_level = "warning" if int(result["errors"]) else "result"
        reporter.log(
            f"Corpus concluído: {result['unique_documents']} documentos, "
            f"{result['chunks_count']} chunks, {result['documents_parsed']} "
            f"processados, {result['documents_reused']} reutilizados, "
            f"{result['errors']} erros",
            result_level,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.command == "search":
        reporter = ConsoleReporter(args.quiet, args.color)
        if args.limit < 1 or args.limit > 100:
            reporter.error("--limit deve estar entre 1 e 100")
            return 1
        allowed_access = set(args.allow_access or ("public", "lab"))
        if "project" in allowed_access and args.project is None:
            reporter.error("--allow-access project exige o filtro --project")
            return 1
        try:
            results = search_chunks(
                chunks_path=args.chunks,
                query=args.query,
                limit=args.limit,
                branch=args.branch,
                project=args.project,
                path_prefix=args.path_prefix,
                allowed_access=allowed_access,
                max_per_path=args.max_per_path,
                include_duplicate_content=args.include_duplicate_content,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            reporter.error(str(exc))
            return 1
        reporter.result(f"Busca retornou {len(results)} chunks citáveis")
        for position, result in enumerate(results, start=1):
            reporter.log(
                f"{position}. {result['citation']} (score {result['score']})",
                "success",
            )
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    if args.command == "evaluate":
        reporter = ConsoleReporter(args.quiet, args.color)
        try:
            report = evaluate_suite(
                suite_path=args.suite,
                chunks_path=args.chunks,
                output=args.output,
                log=reporter.log,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            reporter.error(str(exc))
            return 1
        summary = report["summary"]
        assert isinstance(summary, dict)
        failures = int(summary["cases_failed"])
        reporter.log(
            f"Avaliação: {summary['cases_passed']}/{summary['cases']} casos; "
            f"recall {float(summary['expectation_recall']):.1%}; "
            f"MRR {float(summary['mean_reciprocal_rank']):.3f}",
            "result" if failures == 0 else "warning",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if failures else 0

    return 2
