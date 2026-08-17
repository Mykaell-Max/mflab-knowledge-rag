from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit

from mflab_knowledge.api import ApiSettings, run_api
from mflab_knowledge.credentials import load_database_url, load_git_credentials
from mflab_knowledge.database import (
    database_fingerprint,
    database_status,
    initialize_database,
    initialize_vector_database,
    load_corpus,
    search_postgres,
)
from mflab_knowledge.embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_REVISION,
    DEFAULT_MAX_SEQUENCE_LENGTH,
    LocalEmbedder,
    embed_database,
    embedding_status,
    hybrid_fingerprint,
    hybrid_search,
    semantic_search,
)
from mflab_knowledge.evaluate import evaluate_suite
from mflab_knowledge.inventory import build_inventory, detect_git_metadata, write_yaml
from mflab_knowledge.index_pipeline import index_all_repositories
from mflab_knowledge.multi_sync import repository_uses_https, sync_all_repositories
from mflab_knowledge.normalize import normalize_manifest, search_chunks
from mflab_knowledge.repository import prepare_repository_snapshot
from mflab_knowledge.repository_config import load_repository_catalog
from mflab_knowledge.retrieval import load_retrieval_policy
from mflab_knowledge.service_runner import (
    RunAlreadyActiveError,
    RunStateRecorder,
    read_last_run,
    run_managed,
)
from mflab_knowledge.sync import sync_repository_branches


class ConsoleReporter:
    _STYLES = {
        "section": ("ETAPA", "1;36"),
        "info": ("INFO", "36"),
        "detail": ("DETALHE", "2"),
        "cache": ("CACHE", "1;34"),
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
        verbose: bool = False,
    ) -> None:
        self.quiet = quiet
        self.verbose = verbose
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
        if level in {"detail", "cache"} and not self.verbose:
            return
        if not self.quiet or level == "error":
            label, style = self._STYLES.get(level, self._STYLES["info"])
            prefix = self._styled(f"[mflab:{label}]", style)
            if level == "section":
                print(file=sys.stderr)
                rule = self._styled("=" * 72, style)
                print(rule, file=sys.stderr)
            lines = message.splitlines() or [""]
            print(f"{prefix} {lines[0]}", file=sys.stderr)
            continuation = " " * (len(f"[mflab:{label}]") + 1)
            for line in lines[1:]:
                print(f"{continuation}{line}", file=sys.stderr)
            if level == "section":
                print(rule, file=sys.stderr)
            sys.stderr.flush()

    def success(self, message: str) -> None:
        self.log(message, "success")

    def warning(self, message: str) -> None:
        self.log(message, "warning")

    def error(self, message: str) -> None:
        self.log(message, "error")

    def result(self, message: str) -> None:
        self.log(message, "result")

    def section(self, message: str) -> None:
        self.log(message, "section")

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
                short_path = path if len(path) <= 64 else f"...{path[-61:]}"
                print(
                    f"{prefix} {self.progress_label} "
                    f"{percent:3d}% ({current}/{total}) {short_path}",
                    file=sys.stderr,
                    flush=True,
                )


class _StateReporter:
    def __init__(
        self,
        reporter: ConsoleReporter,
        recorder: RunStateRecorder,
    ) -> None:
        self.reporter = reporter
        self.recorder = recorder

    def log(self, message: str, level: str = "info") -> None:
        self.recorder.log(message, level)
        self.reporter.log(message, level)

    def progress(self, current: int, total: int, path: str) -> None:
        self.recorder.progress(current, total, path)
        self.reporter.progress(current, total, path)

    def success(self, message: str) -> None:
        self.log(message, "success")

    def warning(self, message: str) -> None:
        self.log(message, "warning")

    def error(self, message: str) -> None:
        self.log(message, "error")

    def result(self, message: str) -> None:
        self.log(message, "result")

    def section(self, message: str) -> None:
        self.log(message, "section")


def _add_console_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="Cores nos logs: auto, always ou never (padrão: auto).",
    )
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Exibe detalhes por branch e cada reutilização de cache.",
    )


def _add_database_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--env-file",
        default=Path(".env"),
        type=Path,
        help="Arquivo local com MFLAB_DATABASE_URL (padrão: ./.env).",
    )
    _add_console_options(parser)


def _add_embedding_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--embedding-model",
        default=DEFAULT_EMBEDDING_MODEL,
        help=f"Modelo local (padrão: {DEFAULT_EMBEDDING_MODEL}).",
    )
    parser.add_argument(
        "--embedding-revision",
        default=DEFAULT_EMBEDDING_REVISION,
        help="Commit/revisão imutável dos pesos do modelo.",
    )
    parser.add_argument(
        "--device",
        default="auto",
        help="Dispositivo do modelo: auto, cpu, cuda etc. (padrão: auto).",
    )
    parser.add_argument(
        "--max-sequence-length",
        type=int,
        default=DEFAULT_MAX_SEQUENCE_LENGTH,
    )


def _add_search_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--query", required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--branch")
    parser.add_argument("--project")
    parser.add_argument("--path-prefix")
    parser.add_argument(
        "--max-per-path",
        type=int,
        default=2,
        help="Máximo de chunks por arquivo (padrão: 2).",
    )
    parser.add_argument(
        "--include-duplicate-content",
        action="store_true",
        help="Inclui chunks textualmente idênticos no resultado.",
    )
    parser.add_argument(
        "--allow-access",
        action="append",
        choices=("public", "lab", "project", "restricted"),
        help="Classe liberada; repita para mais de uma (padrão: public e lab).",
    )


def _add_retrieval_policy_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--retrieval-config",
        type=Path,
        help=(
            "Política TOML de recuperação. Sem a opção, usa ./retrieval.toml "
            "quando existir ou os padrões auditáveis do serviço."
        ),
    )


def _add_index_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default=Path("repositories.toml"),
        type=Path,
        help="Catálogo TOML de repositórios (padrão: ./repositories.toml).",
    )
    parser.add_argument(
        "--repository",
        action="append",
        help="ID a processar; repita para selecionar vários.",
    )
    parser.add_argument(
        "--env-file",
        default=Path(".env"),
        type=Path,
        help="Credenciais Git e PostgreSQL locais (padrão: ./.env).",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Não consulta remotes; usa somente os mirrors existentes.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Interrompe o pipeline após a primeira falha.",
    )
    parser.add_argument(
        "--no-embeddings",
        action="store_true",
        help="Executa sincronização, normalização e banco sem embeddings.",
    )
    _add_embedding_options(parser)
    parser.add_argument("--batch-size", type=int, default=4)
    _add_console_options(parser)


def _safe_database_error(error: Exception, database_url: str | None) -> str:
    message = str(error)
    if database_url:
        message = message.replace(database_url, "<MFLAB_DATABASE_URL>")
        password = urlsplit(database_url).password
        if password:
            message = message.replace(password, "***")
    return message


def _search_access(args: argparse.Namespace) -> set[str]:
    allowed_access = set(args.allow_access or ("public", "lab"))
    if "project" in allowed_access and args.project is None:
        raise ValueError("--allow-access project exige o filtro --project")
    return allowed_access


def _execute_configured_index(
    args: argparse.Namespace,
    reporter: ConsoleReporter | _StateReporter,
) -> dict[str, object]:
    database_url: str | None = None
    try:
        catalog = load_repository_catalog(args.config)
        selected_ids = set(args.repository) if args.repository else None
        known_ids = {repository.id for repository in catalog.repositories}
        if selected_ids is not None:
            unknown = sorted(selected_ids - known_ids)
            if unknown:
                raise ValueError(
                    "repositórios desconhecidos: " + ", ".join(unknown)
                )
        selected_repositories = [
            repository
            for repository in catalog.enabled
            if selected_ids is None or repository.id in selected_ids
        ]
        needs_https_credentials = not args.offline and any(
            repository_uses_https(repository)
            for repository in selected_repositories
        )
        credentials = (
            load_git_credentials(args.env_file)
            if needs_https_credentials
            else None
        )
        database_url = load_database_url(args.env_file)
        if credentials is not None:
            reporter.success("Credenciais HTTPS não interativas configuradas")
        return index_all_repositories(
            catalog=catalog,
            database_url=database_url,
            refresh_remote=not args.offline,
            credentials=credentials,
            repository_ids=selected_ids,
            fail_fast=args.fail_fast,
            include_embeddings=not args.no_embeddings,
            embedding_model=args.embedding_model,
            embedding_revision=args.embedding_revision,
            device=args.device,
            max_sequence_length=args.max_sequence_length,
            batch_size=args.batch_size,
            log=reporter.log,
            progress=reporter.progress,
        )
    except Exception as exc:
        raise ValueError(_safe_database_error(exc, database_url)) from exc


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
    sync.add_argument(
        "--canonical-ref",
        required=True,
        help="Ref canônica explícita deste repositório; não há nome padrão.",
    )
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
        default="generic",
        choices=("auto", "generic", "mfsim-ng-pilot"),
    )
    sync.add_argument("--cache-dir", default=Path("cache"), type=Path)
    sync.add_argument(
        "--fetch-timeout-seconds",
        type=int,
        default=1800,
        help="Limite do fetch remoto em segundos (padrão: 1800).",
    )
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
    sync.add_argument(
        "--include-branch",
        action="append",
        default=[],
        help="Padrão glob de branch a incluir; repita para mais de um.",
    )
    sync.add_argument(
        "--exclude-branch",
        action="append",
        default=[],
        help="Padrão glob de branch a excluir; a canônica sempre permanece.",
    )
    _add_console_options(sync)

    sync_all = subparsers.add_parser(
        "sync-all",
        help="Sincroniza os repositórios habilitados em repositories.toml.",
    )
    sync_all.add_argument(
        "--config",
        default=Path("repositories.toml"),
        type=Path,
        help="Catálogo TOML de repositórios (padrão: ./repositories.toml).",
    )
    sync_all.add_argument(
        "--repository",
        action="append",
        help="ID a processar; repita para selecionar vários.",
    )
    sync_all.add_argument(
        "--env-file",
        default=Path(".env"),
        type=Path,
        help="Credenciais HTTPS locais (padrão: ./.env).",
    )
    sync_all.add_argument(
        "--offline",
        action="store_true",
        help="Não consulta remotes; usa somente refs já disponíveis.",
    )
    sync_all.add_argument(
        "--fail-fast",
        action="store_true",
        help="Interrompe após a primeira falha de repositório.",
    )
    _add_console_options(sync_all)

    index_all = subparsers.add_parser(
        "index-all",
        help="Sincroniza, normaliza, carrega e gera embeddings incrementalmente.",
    )
    _add_index_options(index_all)

    run_scheduled = subparsers.add_parser(
        "run-scheduled",
        help="Executa indexação não assistida com trava e estado persistente.",
    )
    _add_index_options(run_scheduled)
    run_scheduled.add_argument(
        "--state-dir",
        default=Path("state"),
        type=Path,
        help="Estado operacional e histórico (padrão: ./state).",
    )
    run_scheduled.add_argument(
        "--lock-file",
        type=Path,
        help="Trava de processo (padrão: STATE_DIR/index.lock).",
    )
    run_scheduled.add_argument(
        "--history-limit",
        type=int,
        default=50,
        help="Quantidade de execuções mantidas no histórico (padrão: 50).",
    )

    run_status = subparsers.add_parser(
        "run-status",
        help="Mostra o estado persistido da última indexação agendada.",
    )
    run_status.add_argument(
        "--state-dir",
        default=Path("state"),
        type=Path,
        help="Estado operacional (padrão: ./state).",
    )
    _add_console_options(run_status)

    serve = subparsers.add_parser(
        "serve",
        help="Inicia a API RAG local e somente leitura.",
    )
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="Endereço loopback local (padrão: 127.0.0.1).",
    )
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument(
        "--state-dir",
        default=Path("state"),
        type=Path,
        help="Estado do indexador exibido pela API (padrão: ./state).",
    )
    serve.add_argument(
        "--allow-access",
        action="append",
        choices=("public", "lab", "project", "restricted"),
        help="Classe máxima liberada pela API; padrão: public e lab.",
    )
    serve.add_argument(
        "--log-level",
        choices=("critical", "error", "warning", "info", "debug"),
        default="info",
    )
    serve.add_argument(
        "--generation-config",
        default=Path("generation.toml"),
        type=Path,
        help=(
            "Provedor LLM local compatível com OpenAI "
            "(padrão: ./generation.toml)."
        ),
    )
    _add_retrieval_policy_option(serve)
    _add_embedding_options(serve)
    _add_database_options(serve)

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
    _add_search_options(search)
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

    db_init = subparsers.add_parser(
        "db-init",
        help="Cria ou atualiza o schema PostgreSQL do serviço.",
    )
    _add_database_options(db_init)

    db_load = subparsers.add_parser(
        "db-load",
        help="Carrega um corpus JSONL no PostgreSQL de forma idempotente.",
    )
    db_load.add_argument("--documents", required=True, type=Path)
    db_load.add_argument("--chunks", required=True, type=Path)
    _add_database_options(db_load)

    db_search = subparsers.add_parser(
        "db-search",
        help="Executa busca textual no corpus armazenado no PostgreSQL.",
    )
    _add_search_options(db_search)
    _add_retrieval_policy_option(db_search)
    db_search.add_argument(
        "--mode",
        choices=("lexical", "semantic", "hybrid"),
        default="lexical",
    )
    _add_embedding_options(db_search)
    _add_database_options(db_search)

    db_evaluate = subparsers.add_parser(
        "db-evaluate",
        help="Executa a suíte de recuperação contra o PostgreSQL.",
    )
    db_evaluate.add_argument("--suite", required=True, type=Path)
    db_evaluate.add_argument(
        "--output",
        default=Path("data/postgres-evaluation.generated.json"),
        type=Path,
    )
    db_evaluate.add_argument(
        "--mode",
        choices=("lexical", "semantic", "hybrid"),
        default="lexical",
    )
    _add_embedding_options(db_evaluate)
    _add_retrieval_policy_option(db_evaluate)
    _add_database_options(db_evaluate)

    db_status = subparsers.add_parser(
        "db-status",
        help="Mostra contagens e última carga do PostgreSQL.",
    )
    _add_database_options(db_status)

    db_vector_init = subparsers.add_parser(
        "db-vector-init",
        help="Valida pgvector e cria as tabelas de embeddings.",
    )
    _add_database_options(db_vector_init)

    db_embed = subparsers.add_parser(
        "db-embed",
        help="Calcula incrementalmente embeddings locais dos chunks.",
    )
    _add_embedding_options(db_embed)
    db_embed.add_argument("--batch-size", type=int, default=4)
    _add_database_options(db_embed)

    db_embedding_status = subparsers.add_parser(
        "db-embedding-status",
        help="Mostra perfis e quantidades de embeddings armazenados.",
    )
    _add_database_options(db_embedding_status)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "inventory":
        reporter = ConsoleReporter(args.quiet, args.color, verbose=args.verbose)
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
        reporter = ConsoleReporter(args.quiet, args.color, verbose=args.verbose)
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
                fetch_timeout_seconds=args.fetch_timeout_seconds,
                output_dir=args.output_dir,
                include_branches=tuple(args.include_branch),
                exclude_branches=tuple(args.exclude_branch),
                refresh_remote=not args.offline,
                credentials=credentials,
                log=reporter.log,
                progress=reporter.progress,
            )
        except (OSError, ValueError) as exc:
            reporter.error(str(exc))
            return 1
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.command == "sync-all":
        reporter = ConsoleReporter(args.quiet, args.color, verbose=args.verbose)
        try:
            catalog = load_repository_catalog(args.config)
            selected_ids = set(args.repository) if args.repository else None
            selected_repositories = [
                repository
                for repository in catalog.enabled
                if selected_ids is None or repository.id in selected_ids
            ]
            needs_https_credentials = not args.offline and any(
                repository_uses_https(repository)
                for repository in selected_repositories
            )
            credentials = (
                load_git_credentials(args.env_file)
                if needs_https_credentials
                else None
            )
            if credentials is not None:
                reporter.success("Credenciais HTTPS não interativas configuradas")
            result = sync_all_repositories(
                catalog=catalog,
                refresh_remote=not args.offline,
                credentials=credentials,
                repository_ids=selected_ids,
                fail_fast=args.fail_fast,
                log=reporter.log,
                progress=reporter.progress,
            )
        except (OSError, ValueError) as exc:
            reporter.error(str(exc))
            return 1
        print(json.dumps(result, ensure_ascii=False))
        return 1 if int(result["failed"]) or int(result["inventory_errors"]) else 0

    if args.command == "index-all":
        reporter = ConsoleReporter(
            args.quiet,
            args.color,
            progress_label="pipeline",
            verbose=args.verbose,
        )
        try:
            result = _execute_configured_index(args, reporter)
        except Exception as exc:
            reporter.error(str(exc))
            return 1
        print(json.dumps(result, ensure_ascii=False))
        return 1 if int(result["failed"]) or int(result["warnings"]) else 0

    if args.command == "run-scheduled":
        console = ConsoleReporter(
            args.quiet,
            args.color,
            progress_label="pipeline",
            verbose=args.verbose,
        )
        state_dir = args.state_dir.expanduser().resolve()
        lock_file = (
            args.lock_file.expanduser().resolve()
            if args.lock_file is not None
            else state_dir / "index.lock"
        )
        metadata: dict[str, object] = {
            "command": "run-scheduled",
            "config_file": str(args.config.expanduser().resolve()),
            "repositories": sorted(set(args.repository or [])),
            "all_enabled_repositories": not bool(args.repository),
            "refresh_remote": not args.offline,
            "include_embeddings": not args.no_embeddings,
            "batch_size": args.batch_size,
            "device": args.device,
        }

        def action(recorder: RunStateRecorder) -> dict[str, object]:
            reporter = _StateReporter(console, recorder)
            return _execute_configured_index(args, reporter)

        try:
            result = run_managed(
                state_dir=state_dir,
                lock_file=lock_file,
                metadata=metadata,
                action=action,
                history_limit=args.history_limit,
            )
        except RunAlreadyActiveError as exc:
            console.warning(str(exc))
            print(
                json.dumps(
                    {"status": "skipped", "reason": "already_running"},
                    ensure_ascii=False,
                )
            )
            return 0
        except Exception as exc:
            console.error(str(exc))
            return 1
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result["status"] in {"warning", "failed"} else 0

    if args.command == "run-status":
        reporter = ConsoleReporter(args.quiet, args.color, verbose=args.verbose)
        try:
            result = read_last_run(args.state_dir)
        except ValueError as exc:
            reporter.error(str(exc))
            return 1
        status = str(result.get("status", "unknown"))
        level = "result" if status == "success" else (
            "warning" if status in {"running", "warning"} else "error"
        )
        reporter.log(
            f"Última execução: {status}; run_id={result.get('run_id', '-')}",
            level,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if status == "failed" else 0

    if args.command == "normalize":
        reporter = ConsoleReporter(
            args.quiet,
            args.color,
            progress_label="normalização",
            verbose=args.verbose,
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
        reporter = ConsoleReporter(args.quiet, args.color, verbose=args.verbose)
        if args.limit < 1 or args.limit > 100:
            reporter.error("--limit deve estar entre 1 e 100")
            return 1
        try:
            allowed_access = _search_access(args)
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
        reporter = ConsoleReporter(args.quiet, args.color, verbose=args.verbose)
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

    if args.command == "serve":
        reporter = ConsoleReporter(
            args.quiet,
            args.color,
            progress_label="API",
            verbose=args.verbose,
        )
        database_url: str | None = None
        try:
            database_url = load_database_url(args.env_file)
            allowed_access = set(args.allow_access or ("public", "lab"))
            settings = ApiSettings(
                database_url=database_url,
                env_file=args.env_file,
                state_dir=args.state_dir,
                retrieval_config=args.retrieval_config,
                generation_config=args.generation_config,
                allowed_access=frozenset(allowed_access),
                embedding_model=args.embedding_model,
                embedding_revision=args.embedding_revision,
                device=args.device,
                max_sequence_length=args.max_sequence_length,
            )
            reporter.section("SERVIÇO HTTP LOCAL")
            reporter.log(
                f"Escutando em http://{args.host}:{args.port}; "
                f"documentação em /docs",
                "info",
            )
            reporter.log(
                "O modelo de embeddings será carregado na primeira busca "
                "semantic ou hybrid",
                "info",
            )
            run_api(
                settings,
                host=args.host,
                port=args.port,
                log_level=args.log_level,
                log=reporter.log,
            )
            return 0
        except Exception as exc:
            reporter.error(_safe_database_error(exc, database_url))
            return 1

    if args.command in {
        "db-init",
        "db-load",
        "db-search",
        "db-evaluate",
        "db-status",
        "db-vector-init",
        "db-embed",
        "db-embedding-status",
    }:
        reporter = ConsoleReporter(
            args.quiet,
            args.color,
            progress_label=(
                "embeddings" if args.command == "db-embed" else "banco"
            ),
            verbose=args.verbose,
        )
        database_url: str | None = None
        try:
            database_url = load_database_url(args.env_file)
            if args.command == "db-init":
                result = initialize_database(database_url, log=reporter.log)
                reporter.result("Banco PostgreSQL inicializado")
                print(json.dumps(result, ensure_ascii=False))
                return 0

            if args.command == "db-load":
                result = load_corpus(
                    database_url,
                    documents_path=args.documents,
                    chunks_path=args.chunks,
                    log=reporter.log,
                )
                state = "reutilizado" if result["reused"] else "atualizado"
                reporter.result(
                    f"Corpus PostgreSQL {state}: {result['documents']} documentos, "
                    f"{result['chunks']} chunks"
                )
                print(json.dumps(result, ensure_ascii=False))
                return 0

            if args.command == "db-vector-init":
                result = initialize_vector_database(database_url, log=reporter.log)
                reporter.result("Backend vetorial PostgreSQL inicializado")
                print(json.dumps(result, ensure_ascii=False))
                return 0

            if args.command == "db-embed":
                result = embed_database(
                    database_url,
                    model_id=args.embedding_model,
                    revision=args.embedding_revision,
                    device=args.device,
                    max_sequence_length=args.max_sequence_length,
                    batch_size=args.batch_size,
                    log=reporter.log,
                    progress=reporter.progress,
                )
                reporter.result(
                    f"Embeddings: {result['embedded']} calculados, "
                    f"{result['reused']} reutilizados; dispositivo "
                    f"{result['device']}"
                )
                print(json.dumps(result, ensure_ascii=False))
                return 0

            if args.command == "db-embedding-status":
                result = embedding_status(database_url)
                reporter.result(
                    f"Perfis de embeddings armazenados: {len(result['models'])}"
                )
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0

            if args.command == "db-search":
                allowed_access = _search_access(args)
                parameters = {
                    "query": args.query,
                    "limit": args.limit,
                    "branch": args.branch,
                    "project": args.project,
                    "path_prefix": args.path_prefix,
                    "allowed_access": allowed_access,
                    "max_per_path": args.max_per_path,
                    "include_duplicate_content": (
                        args.include_duplicate_content
                    ),
                }
                if args.mode == "lexical":
                    results = search_postgres(database_url, **parameters)
                else:
                    embedder = LocalEmbedder(
                        model_id=args.embedding_model,
                        revision=args.embedding_revision,
                        device=args.device,
                        max_sequence_length=args.max_sequence_length,
                        log=reporter.log,
                    )
                    search_function = (
                        semantic_search
                        if args.mode == "semantic"
                        else hybrid_search
                    )
                    if args.mode == "hybrid":
                        parameters["retrieval_policy"] = load_retrieval_policy(
                            args.retrieval_config
                        )
                    results = search_function(
                        database_url,
                        embedder,
                        **parameters,
                    )
                reporter.result(
                    f"Busca PostgreSQL {args.mode} retornou "
                    f"{len(results)} chunks citáveis"
                )
                for position, result in enumerate(results, start=1):
                    reporter.log(
                        f"{position}. {result['citation']} "
                        f"(score {result['score']})",
                        "success",
                    )
                print(json.dumps(results, ensure_ascii=False, indent=2))
                return 0

            if args.command == "db-evaluate":
                embedder: LocalEmbedder | None = None
                retrieval_policy = None
                if args.mode == "lexical":
                    backend = database_fingerprint(database_url)
                    selected_search = search_postgres
                else:
                    embedder = LocalEmbedder(
                        model_id=args.embedding_model,
                        revision=args.embedding_revision,
                        device=args.device,
                        max_sequence_length=args.max_sequence_length,
                        log=reporter.log,
                    )
                    if args.mode == "hybrid":
                        retrieval_policy = load_retrieval_policy(
                            args.retrieval_config
                        )
                    backend = hybrid_fingerprint(
                        database_url,
                        embedder,
                        retrieval_policy,
                    )
                    backend["mode"] = args.mode
                    if args.mode == "semantic":
                        backend["retrieval_algorithm"] = "exact_cosine"
                        backend.pop("retrieval_policy", None)
                    selected_search = (
                        semantic_search
                        if args.mode == "semantic"
                        else hybrid_search
                    )

                def database_search(**parameters: object) -> list[dict[str, object]]:
                    if embedder is None:
                        return selected_search(database_url, **parameters)
                    if retrieval_policy is not None:
                        parameters["retrieval_policy"] = retrieval_policy
                    return selected_search(database_url, embedder, **parameters)

                report = evaluate_suite(
                    suite_path=args.suite,
                    output=args.output,
                    log=reporter.log,
                    search=database_search,
                    backend=backend,
                )
                summary = report["summary"]
                assert isinstance(summary, dict)
                failures = int(summary["cases_failed"])
                reporter.log(
                    f"Avaliação PostgreSQL {args.mode}: "
                    f"{summary['cases_passed']}/{summary['cases']} casos; "
                    f"recall {float(summary['expectation_recall']):.1%}; "
                    f"MRR {float(summary['mean_reciprocal_rank']):.3f}",
                    "result" if failures == 0 else "warning",
                )
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 1 if failures else 0

            result = database_status(database_url)
            reporter.result(
                f"PostgreSQL: {result['repositories']} repositórios, "
                f"{result['documents']} documentos, {result['chunks']} chunks"
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except Exception as exc:
            reporter.error(_safe_database_error(exc, database_url))
            return 1

    return 2
