from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from mflab_knowledge.inventory import detect_git_metadata

LogCallback = Callable[[str], None]


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
            "cache_mirror": str(self.mirror_path),
        }


def _run(
    command: list[str],
    *,
    timeout: int = 120,
    cwd: Path | None = None,
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


def prepare_repository_snapshot(
    source: Path,
    *,
    project: str,
    cache_dir: Path,
    ref: str | None = None,
    log: LogCallback | None = None,
) -> RepositorySnapshot:
    logger = log or (lambda _message: None)
    source_root = source.expanduser().resolve()
    metadata = detect_git_metadata(source_root)
    if not metadata["versioned"]:
        raise ValueError(f"a fonte não é um repositório Git: {source_root}")

    requested_ref = ref or str(metadata.get("branch") or "HEAD")
    cache_root = cache_dir.expanduser().resolve()
    repositories_dir = cache_root / "repositories"
    snapshots_dir = cache_root / "snapshots"
    repositories_dir.mkdir(parents=True, exist_ok=True)
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    mirror_name = f"{_slug(project)}-{_source_identity(source_root)}.git"
    mirror = repositories_dir / mirror_name
    bundle = repositories_dir / f".{mirror_name}.bundle.tmp"

    if mirror.exists():
        logger(f"Atualizando mirror isolado: {mirror}")
    else:
        logger(f"Criando mirror isolado: {mirror}")
    _refresh_mirror_from_local_source(source_root, mirror, bundle)

    commit_sha, branch = _resolve_ref(mirror, requested_ref)
    snapshot_container = snapshots_dir / _slug(project) / commit_sha
    snapshot = snapshot_container / "tree"
    marker = snapshot_container / ".complete"

    if marker.exists() and marker.read_text(encoding="utf-8").strip() == commit_sha:
        logger(f"Reutilizando snapshot imutável {commit_sha[:12]}")
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
                str(mirror),
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
        source_path=source_root,
        mirror_path=mirror,
        requested_ref=requested_ref,
        commit_sha=commit_sha,
        branch=branch,
        remote_url=(
            metadata.get("remote_url")
            if isinstance(metadata.get("remote_url"), str)
            else None
        ),
    )
