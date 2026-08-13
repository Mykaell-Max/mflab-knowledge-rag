from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from mflab_knowledge.credentials import GitCredentials
from mflab_knowledge.inventory import detect_git_metadata

LogCallback = Callable[[str, str], None]


@dataclass(frozen=True)
class RepositorySnapshot:
    path: Path
    source_path: Path
    mirror_path: Path
    requested_ref: str
    commit_sha: str
    branch: str | None
    remote_url: str | None

    def metadata(self) -> dict[str, object]:
        return {
            "kind": "git_cached_snapshot",
            "versioned": True,
            "branch": self.branch,
            "requested_ref": self.requested_ref,
            "commit_sha": self.commit_sha,
            "remote_url": self.remote_url,
            "evidence_limit": None,
            "snapshot_strategy": "independent_mirror_and_git_archive",
            "source_root": str(self.source_path),
            "snapshot_root": str(self.path),
            "cache_mirror": str(self.mirror_path),
        }


@dataclass(frozen=True)
class RepositoryMirror:
    source_path: Path
    mirror_path: Path
    remote_url: str | None


@dataclass(frozen=True)
class RepositoryBranch:
    name: str
    requested_ref: str
    full_ref: str
    commit_sha: str
    scope: str


def _run(
    command: list[str],
    *,
    timeout: int = 120,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as exc:
        raise ValueError("Git não foi encontrado no PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"tempo excedido ao executar: {' '.join(command[:3])}") from exc

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "erro desconhecido"
        raise ValueError(f"Git falhou ({' '.join(command[:3])}): {detail}")
    return result.stdout.strip()


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9._-]+", "-", value.casefold()).strip("-.")
    return normalized or "repository"


def _source_identity(source: Path) -> str:
    return hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:12]


def _safe_ref(ref: str) -> str:
    value = ref.strip()
    if not value or value.startswith("-") or "\x00" in value:
        raise ValueError(f"ref Git inválida: {ref!r}")
    return value


def _resolve_ref(mirror: Path, requested_ref: str) -> tuple[str, str | None]:
    ref = _safe_ref(requested_ref)
    candidates = [ref]
    if not ref.startswith("refs/"):
        candidates = [
            f"refs/heads/{ref}",
            f"refs/tags/{ref}",
            f"refs/remotes/{ref}",
            f"refs/remotes/origin/{ref}",
            ref,
        ]

    for candidate in candidates:
        result = subprocess.run(
            [
                "git",
                "--git-dir",
                str(mirror),
                "rev-parse",
                "--verify",
                f"{candidate}^{{commit}}",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if result.returncode == 0:
            if candidate.startswith("refs/heads/"):
                branch = candidate.removeprefix("refs/heads/")
            elif candidate.startswith("refs/remotes/origin/"):
                branch = candidate.removeprefix("refs/remotes/origin/")
            elif candidate.startswith("refs/remotes/"):
                branch = candidate.removeprefix("refs/remotes/")
            else:
                branch = None
            return result.stdout.strip(), branch

    raise ValueError(f"ref não encontrada no mirror: {requested_ref}")


def _refresh_mirror_from_local_source(
    source: Path,
    mirror: Path,
    bundle: Path,
) -> None:
    if bundle.exists():
        bundle.unlink()
    try:
        _run(
            ["git", "-C", str(source), "bundle", "create", str(bundle), "--all"],
            timeout=300,
        )
        if not mirror.exists():
            mirror.parent.mkdir(parents=True, exist_ok=True)
            _run(["git", "init", "--bare", str(mirror)])
        else:
            refs = _run(
                [
                    "git",
                    "--git-dir",
                    str(mirror),
                    "for-each-ref",
                    "--format=%(refname)",
                    "refs/heads",
                    "refs/tags",
                    "refs/remotes",
                ]
            )
            for ref in refs.splitlines():
                if ref:
                    _run(["git", "--git-dir", str(mirror), "update-ref", "-d", ref])
        _run(
            [
                "git",
                "--git-dir",
                str(mirror),
                "fetch",
                "--prune",
                str(bundle),
                "+refs/heads/*:refs/heads/*",
                "+refs/tags/*:refs/tags/*",
                "+refs/remotes/*:refs/remotes/*",
            ],
            timeout=300,
        )
    finally:
        if bundle.exists():
            bundle.unlink()


def _askpass_environment(
    directory: Path,
    credentials: GitCredentials,
) -> tuple[dict[str, str], tempfile.TemporaryDirectory[str]]:
    temporary = tempfile.TemporaryDirectory(prefix=".mflab-askpass-", dir=directory)
    temporary_path = Path(temporary.name)
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "MFLAB_ASKPASS_USERNAME": credentials.username,
            "MFLAB_ASKPASS_TOKEN": credentials.token,
        }
    )

    if os.name == "nt":
        script = temporary_path / "askpass.cmd"
        script.write_text(
            "@echo off\r\n"
            "echo %~1 | %SystemRoot%\\System32\\findstr.exe /I username >nul\r\n"
            "if errorlevel 1 (echo %MFLAB_ASKPASS_TOKEN%) else "
            "(echo %MFLAB_ASKPASS_USERNAME%)\r\n",
            encoding="utf-8",
        )
    else:
        script = temporary_path / "askpass.sh"
        script.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  *sername*) printf '%s\\n' \"$MFLAB_ASKPASS_USERNAME\" ;;\n"
            "  *) printf '%s\\n' \"$MFLAB_ASKPASS_TOKEN\" ;;\n"
            "esac\n",
            encoding="utf-8",
            newline="\n",
        )
        script.chmod(0o700)
    environment["GIT_ASKPASS"] = str(script)
    return environment, temporary


def _refresh_mirror_from_remote(
    mirror: Path,
    remote_url: str,
    credentials: GitCredentials | None,
) -> None:
    command = [
        "git",
        "-c",
        "credential.helper=",
        "--git-dir",
        str(mirror),
        "fetch",
        "--prune",
        "--no-tags",
        remote_url,
        "+refs/heads/*:refs/remotes/origin/*",
        "+refs/tags/*:refs/tags/*",
    ]
    if credentials is None:
        environment = os.environ.copy()
        environment["GIT_TERMINAL_PROMPT"] = "0"
        _run(command, timeout=300, env=environment)
        return

    environment, temporary = _askpass_environment(mirror.parent, credentials)
    try:
        _run(command, timeout=300, env=environment)
    finally:
        temporary.cleanup()


def _extract_git_archive(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    boundary = destination.resolve()
    with tarfile.open(archive, mode="r:") as handle:
        for member in handle:
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"caminho inseguro no archive Git: {member.name}")
            target = destination.joinpath(*relative.parts)
            resolved_target = target.resolve()
            if resolved_target != boundary and boundary not in resolved_target.parents:
                raise ValueError(f"caminho fora do snapshot: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source_handle = handle.extractfile(member)
                if source_handle is None:
                    raise ValueError(f"não foi possível extrair: {member.name}")
                with source_handle, target.open("wb") as destination_handle:
                    shutil.copyfileobj(source_handle, destination_handle)
            # Links e tipos especiais são deliberadamente ignorados.


def prepare_repository_mirror(
    source: Path,
    *,
    project: str,
    cache_dir: Path,
    refresh_remote: bool = False,
    credentials: GitCredentials | None = None,
    log: LogCallback | None = None,
) -> RepositoryMirror:
    logger = log or (lambda _message, _level="info": None)
    source_root = source.expanduser().resolve()
    metadata = detect_git_metadata(source_root)
    if not metadata["versioned"]:
        raise ValueError(f"a fonte não é um repositório Git: {source_root}")

    cache_root = cache_dir.expanduser().resolve()
    repositories_dir = cache_root / "repositories"
    repositories_dir.mkdir(parents=True, exist_ok=True)

    mirror_name = f"{_slug(project)}-{_source_identity(source_root)}.git"
    mirror = repositories_dir / mirror_name
    bundle = repositories_dir / f".{mirror_name}.bundle.tmp"

    if mirror.exists():
        logger(f"Atualizando mirror isolado: {mirror}")
    else:
        logger(f"Criando mirror isolado: {mirror}")
    _refresh_mirror_from_local_source(source_root, mirror, bundle)

    remote_url = (
        metadata.get("remote_url")
        if isinstance(metadata.get("remote_url"), str)
        else None
    )
    if refresh_remote:
        if remote_url is None:
            logger(
                "Fonte sem remote origin; usando apenas refs locais conhecidas",
                "warning",
            )
        else:
            logger(f"Atualizando branches diretamente do remote: {remote_url}")
            _refresh_mirror_from_remote(mirror, remote_url, credentials)
    return RepositoryMirror(
        source_path=source_root,
        mirror_path=mirror,
        remote_url=remote_url,
    )


def list_repository_branches(
    mirror: RepositoryMirror,
    *,
    scope: str = "remote",
) -> list[RepositoryBranch]:
    if scope not in {"remote", "local", "all"}:
        raise ValueError(f"escopo de branches desconhecido: {scope}")

    prefixes: list[tuple[str, str]] = []
    if scope in {"remote", "all"}:
        prefixes.append(("refs/remotes/origin", "remote"))
    if scope in {"local", "all"}:
        prefixes.append(("refs/heads", "local"))

    discovered: dict[str, RepositoryBranch] = {}
    for prefix, branch_scope in prefixes:
        output = _run(
            [
                "git",
                "--git-dir",
                str(mirror.mirror_path),
                "for-each-ref",
                "--format=%(refname)%09%(objectname)",
                prefix,
            ]
        )
        for line in output.splitlines():
            if not line.strip():
                continue
            full_ref, commit_sha = line.split("\t", 1)
            if full_ref.endswith("/HEAD"):
                continue
            if branch_scope == "remote":
                name = full_ref.removeprefix("refs/remotes/origin/")
                requested_ref = f"origin/{name}"
            else:
                name = full_ref.removeprefix("refs/heads/")
                requested_ref = name

            # No modo `all`, a branch remota é a representação preferida.
            if name in discovered and branch_scope == "local":
                continue
            discovered[name] = RepositoryBranch(
                name=name,
                requested_ref=requested_ref,
                full_ref=full_ref,
                commit_sha=commit_sha,
                scope=branch_scope,
            )

    return sorted(discovered.values(), key=lambda branch: branch.name.casefold())


def compare_branch_to_canonical(
    mirror: RepositoryMirror,
    *,
    canonical_commit: str,
    branch_commit: str,
) -> dict[str, object]:
    if canonical_commit == branch_commit:
        return {
            "relation": "same_commit",
            "ahead": 0,
            "behind": 0,
            "merge_base": canonical_commit,
            "merged_into_canonical": True,
        }

    counts = _run(
        [
            "git",
            "--git-dir",
            str(mirror.mirror_path),
            "rev-list",
            "--left-right",
            "--count",
            f"{canonical_commit}...{branch_commit}",
        ]
    )
    behind_text, ahead_text = counts.split()
    behind = int(behind_text)
    ahead = int(ahead_text)
    merge_base = _run(
        [
            "git",
            "--git-dir",
            str(mirror.mirror_path),
            "merge-base",
            canonical_commit,
            branch_commit,
        ]
    )
    ancestor = subprocess.run(
        [
            "git",
            "--git-dir",
            str(mirror.mirror_path),
            "merge-base",
            "--is-ancestor",
            branch_commit,
            canonical_commit,
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if ancestor.returncode not in {0, 1}:
        raise ValueError("Git falhou ao verificar ancestralidade da branch")
    merged = ancestor.returncode == 0

    if merged:
        relation = "merged"
    elif behind == 0:
        relation = "ahead"
    elif ahead == 0:
        relation = "behind"
    else:
        relation = "diverged"
    return {
        "relation": relation,
        "ahead": ahead,
        "behind": behind,
        "merge_base": merge_base,
        "merged_into_canonical": merged,
    }


def materialize_repository_snapshot(
    mirror: RepositoryMirror,
    *,
    project: str,
    cache_dir: Path,
    ref: str,
    log: LogCallback | None = None,
) -> RepositorySnapshot:
    logger = log or (lambda _message, _level="info": None)
    requested_ref = _safe_ref(ref)
    commit_sha, branch = _resolve_ref(mirror.mirror_path, requested_ref)
    snapshots_dir = cache_dir.expanduser().resolve() / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    snapshot_container = snapshots_dir / _slug(project) / commit_sha
    snapshot = snapshot_container / "tree"
    marker = snapshot_container / ".complete"

    if marker.exists() and marker.read_text(encoding="utf-8").strip() == commit_sha:
        logger(f"Reutilizando snapshot imutável {commit_sha[:12]}", "success")
    else:
        if snapshot_container.exists():
            cache_boundary = snapshots_dir.resolve()
            resolved_snapshot = snapshot_container.resolve()
            if cache_boundary not in resolved_snapshot.parents:
                raise ValueError(f"snapshot fora do cache permitido: {resolved_snapshot}")
            shutil.rmtree(resolved_snapshot)
        snapshot_container.mkdir(parents=True, exist_ok=True)
        logger(f"Materializando {requested_ref} em snapshot {commit_sha[:12]}")
        archive = snapshot_container / "tree.tar"
        _run(
            [
                "git",
                "--git-dir",
                str(mirror.mirror_path),
                "archive",
                "--format=tar",
                f"--output={archive}",
                commit_sha,
            ],
            timeout=300,
        )
        try:
            _extract_git_archive(archive, snapshot)
        finally:
            if archive.exists():
                archive.unlink()
        marker.write_text(f"{commit_sha}\n", encoding="utf-8")

    return RepositorySnapshot(
        path=snapshot,
        source_path=mirror.source_path,
        mirror_path=mirror.mirror_path,
        requested_ref=requested_ref,
        commit_sha=commit_sha,
        branch=branch,
        remote_url=mirror.remote_url,
    )


def prepare_repository_snapshot(
    source: Path,
    *,
    project: str,
    cache_dir: Path,
    ref: str | None = None,
    log: LogCallback | None = None,
) -> RepositorySnapshot:
    metadata = detect_git_metadata(source.expanduser().resolve())
    requested_ref = ref or str(metadata.get("branch") or "HEAD")
    mirror = prepare_repository_mirror(
        source,
        project=project,
        cache_dir=cache_dir,
        log=log,
    )
    return materialize_repository_snapshot(
        mirror,
        project=project,
        cache_dir=cache_dir,
        ref=requested_ref,
        log=log,
    )
