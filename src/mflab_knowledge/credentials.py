from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ENV_TEMPLATE = """# Credenciais HTTPS somente leitura do GitLab do MFLab.
# Nunca faça commit deste arquivo nem use um token com write_repository/api.
MFLAB_GIT_USERNAME=
MFLAB_GIT_READ_TOKEN=
"""


@dataclass(frozen=True)
class GitCredentials:
    username: str
    token: str


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"linha inválida em {path}:{line_number}")
        name, value = line.split("=", 1)
        name = name.strip()
        if name in {"MFLAB_GIT_USERNAME", "MFLAB_GIT_READ_TOKEN"}:
            values[name] = _unquote(value)
    return values


def _create_env_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(ENV_TEMPLATE)
    except FileExistsError:
        return
    try:
        path.chmod(0o600)
    except OSError:
        # No Windows, as ACLs do usuário são a proteção efetiva.
        pass


def _protect_env_file(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        path.chmod(0o600)
    except OSError as exc:
        raise ValueError(f"não foi possível proteger o arquivo {path}") from exc


def load_git_credentials(env_file: Path) -> GitCredentials:
    """Load Git credentials without copying them into the process environment."""

    path = env_file.expanduser().resolve()
    username = os.environ.get("MFLAB_GIT_USERNAME", "").strip()
    token = os.environ.get("MFLAB_GIT_READ_TOKEN", "").strip()

    if not username or not token:
        if not path.exists():
            _create_env_file(path)
            raise ValueError(
                f"arquivo de credenciais criado em {path}. "
                "Crie um token GitLab com somente read_repository, preencha "
                "MFLAB_GIT_USERNAME e MFLAB_GIT_READ_TOKEN e execute novamente"
            )
        _protect_env_file(path)
        values = _read_env_file(path)
        username = username or values.get("MFLAB_GIT_USERNAME", "").strip()
        token = token or values.get("MFLAB_GIT_READ_TOKEN", "").strip()

    missing: list[str] = []
    if not username:
        missing.append("MFLAB_GIT_USERNAME")
    if not token:
        missing.append("MFLAB_GIT_READ_TOKEN")
    if missing:
        raise ValueError(
            f"credenciais incompletas em {path}: preencha {', '.join(missing)}"
        )
    if "\n" in username or "\r" in username or "\n" in token or "\r" in token:
        raise ValueError("credenciais Git contêm quebra de linha inválida")
    return GitCredentials(username=username, token=token)
