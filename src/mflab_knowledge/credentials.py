from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


ENV_TEMPLATE = """# Credenciais HTTPS somente leitura do GitLab do MFLab.
# Nunca faça commit deste arquivo nem use um token com write_repository/api.
MFLAB_GIT_USERNAME=
MFLAB_GIT_READ_TOKEN=

# Conexão local com o PostgreSQL. Mantenha a senha somente neste arquivo.
MFLAB_DATABASE_URL=

# Chave compartilhada exigida somente quando a API é exposta fora do loopback.
MFLAB_API_KEY=

# Senha do painel administrativo da interface web.
MFLAB_ADMIN_PASSWORD=
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
        if name in {
            "MFLAB_GIT_USERNAME",
            "MFLAB_GIT_READ_TOKEN",
            "MFLAB_DATABASE_URL",
            "MFLAB_API_KEY",
            "MFLAB_ADMIN_PASSWORD",
        }:
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


def load_database_url(env_file: Path) -> str:
    """Load the PostgreSQL URL without logging or exporting it."""

    path = env_file.expanduser().resolve()
    database_url = os.environ.get("MFLAB_DATABASE_URL", "").strip()
    if database_url:
        return database_url

    if not path.exists():
        _create_env_file(path)
        raise ValueError(
            f"arquivo de configuração criado em {path}. "
            "Preencha MFLAB_DATABASE_URL e execute novamente"
        )
    _protect_env_file(path)
    values = _read_env_file(path)
    database_url = values.get("MFLAB_DATABASE_URL", "").strip()
    if not database_url:
        content = path.read_text(encoding="utf-8")
        if "MFLAB_DATABASE_URL=" not in content:
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                if content and not content.endswith("\n"):
                    handle.write("\n")
                handle.write(
                    "\n# Conexão local com o PostgreSQL.\n"
                    "MFLAB_DATABASE_URL=\n"
                )
        raise ValueError(
            f"conexão PostgreSQL não configurada em {path}: "
            "preencha MFLAB_DATABASE_URL"
        )
    if "\n" in database_url or "\r" in database_url:
        raise ValueError("MFLAB_DATABASE_URL contém quebra de linha inválida")
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise ValueError("MFLAB_DATABASE_URL deve usar postgresql://")
    return database_url


def load_api_key(env_file: Path, *, optional: bool = False) -> str | None:
    """Load the shared LAN API key without logging or exporting it."""

    path = env_file.expanduser().resolve()
    api_key = os.environ.get("MFLAB_API_KEY", "").strip()
    if not api_key and path.exists():
        _protect_env_file(path)
        api_key = _read_env_file(path).get("MFLAB_API_KEY", "").strip()
    if not api_key:
        if optional:
            return None
        raise ValueError(
            f"chave da API não configurada em {path}: preencha MFLAB_API_KEY"
        )
    if "\n" in api_key or "\r" in api_key:
        raise ValueError("MFLAB_API_KEY contém quebra de linha inválida")
    if len(api_key) < 32:
        raise ValueError("MFLAB_API_KEY deve possuir pelo menos 32 caracteres")
    return api_key


def load_admin_password(
    env_file: Path,
    *,
    optional: bool = False,
) -> str | None:
    """Load the web administration password without exposing it in settings."""

    path = env_file.expanduser().resolve()
    password = os.environ.get("MFLAB_ADMIN_PASSWORD", "").strip()
    if not password and path.exists():
        _protect_env_file(path)
        password = _read_env_file(path).get("MFLAB_ADMIN_PASSWORD", "").strip()
    if not password:
        if optional:
            return None
        raise ValueError(
            "senha administrativa não configurada em "
            f"{path}: preencha MFLAB_ADMIN_PASSWORD"
        )
    if "\n" in password or "\r" in password:
        raise ValueError("MFLAB_ADMIN_PASSWORD contém quebra de linha inválida")
    if len(password) < 12:
        raise ValueError(
            "MFLAB_ADMIN_PASSWORD deve possuir pelo menos 12 caracteres"
        )
    return password


def ensure_api_key(env_file: Path) -> bool:
    """Create a strong API key when absent. Return True only when created."""

    path = env_file.expanduser().resolve()
    if not path.exists():
        _create_env_file(path)
    _protect_env_file(path)
    configured = _read_env_file(path).get("MFLAB_API_KEY", "").strip()
    if configured:
        if "\n" in configured or "\r" in configured:
            raise ValueError("MFLAB_API_KEY contém quebra de linha inválida")
        if len(configured) < 32:
            raise ValueError("MFLAB_API_KEY deve possuir pelo menos 32 caracteres")
        return False
    content = path.read_text(encoding="utf-8")
    generated = secrets.token_urlsafe(48)
    if "MFLAB_API_KEY=" in content:
        lines = content.splitlines()
        replaced = False
        for index, line in enumerate(lines):
            if line.strip().startswith("MFLAB_API_KEY="):
                lines[index] = f"MFLAB_API_KEY={generated}"
                replaced = True
                break
        if not replaced:
            lines.append(f"MFLAB_API_KEY={generated}")
        updated = "\n".join(lines) + "\n"
    else:
        separator = "" if not content or content.endswith("\n") else "\n"
        updated = (
            content
            + separator
            + "\n# Chave compartilhada da API na rede local.\n"
            + f"MFLAB_API_KEY={generated}\n"
        )
    path.write_text(updated, encoding="utf-8", newline="\n")
    _protect_env_file(path)
    return True
