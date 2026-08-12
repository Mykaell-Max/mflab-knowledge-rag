from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = "0.1"

EXCLUDED_DIRECTORY_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "install",
    "node_modules",
}

EXCLUDED_FILE_SUFFIXES = {
    ".a",
    ".bin",
    ".dll",
    ".dylib",
    ".exe",
    ".h5",
    ".h5part",
    ".hdf5",
    ".o",
    ".obj",
    ".pyc",
    ".so",
}

SECRET_NAME_MARKERS = {
    ".env",
    "credential",
    "credentials",
    "private_key",
    "secret",
    "secrets",
    "token",
}

SECRET_FILE_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}

FORMAT_BY_SUFFIX = {
    ".c": "c",
    ".cc": "cpp",
    ".cfg": "config",
    ".cmake": "cmake",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".f": "fortran",
    ".f03": "fortran",
    ".f08": "fortran",
    ".f90": "fortran",
    ".f95": "fortran",
    ".h": "cpp_header",
    ".hh": "cpp_header",
    ".hpp": "cpp_header",
    ".hxx": "cpp_header",
    ".ini": "config",
    ".json": "json",
    ".md": "markdown",
    ".py": "python",
    ".sh": "shell",
    ".toml": "toml",
    ".txt": "text",
    ".yaml": "yaml",
    ".yml": "yaml",
}

SPECIAL_FORMAT_BY_NAME = {
    "cmakelists.txt": "cmake",
    "dockerfile": "dockerfile",
    "makefile": "make",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _run_git(source: Path, *arguments: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(source), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def detect_git_metadata(source: Path) -> dict[str, Any]:
    top_level = _run_git(source, "rev-parse", "--show-toplevel")
    if top_level is None:
        return {
            "kind": "filesystem_snapshot",
            "versioned": False,
            "branch": None,
            "commit_sha": None,
            "evidence_limit": "unversioned_snapshot",
        }

    return {
        "kind": "git_worktree",
        "versioned": True,
        "branch": _run_git(source, "branch", "--show-current"),
        "commit_sha": _run_git(source, "rev-parse", "HEAD"),
        "remote_url": _run_git(source, "remote", "get-url", "origin"),
        "evidence_limit": None,
    }


def _normalized_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_generated_docs(relative_path: str) -> bool:
    parts = relative_path.casefold().split("/")
    return len(parts) >= 2 and parts[0] == "docs" and parts[1] == "html"


def _looks_secret(path: Path) -> bool:
    name = path.name.casefold()
    stem = path.stem.casefold()
    return (
        name == ".env"
        or name.startswith(".env.")
        or path.suffix.casefold() in SECRET_FILE_SUFFIXES
        or any(marker in stem for marker in SECRET_NAME_MARKERS)
    )


def _classify_format(path: Path) -> str:
    special = SPECIAL_FORMAT_BY_NAME.get(path.name.casefold())
    if special is not None:
        return special
    return FORMAT_BY_SUFFIX.get(path.suffix.casefold(), "unknown")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _stable_document_id(project: str, relative_path: str) -> str:
    identity = f"{project}\0{relative_path}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def _walk_files(root: Path) -> Iterator[tuple[Path, str | None]]:
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        retained_directories: list[str] = []

        for directory in sorted(directories):
            candidate = current_path / directory
            if candidate.is_symlink():
                yield candidate, "symlink_directory"
            elif directory.casefold() in EXCLUDED_DIRECTORY_NAMES:
                yield candidate, "excluded_directory"
            else:
                retained_directories.append(directory)

        directories[:] = retained_directories

        for filename in sorted(files):
            yield current_path / filename, None


def build_inventory(
    source: Path,
    project: str,
    access_class: str = "lab",
) -> dict[str, Any]:
    root = source.expanduser().resolve()
    if not root.exists():
        raise ValueError(f"fonte não encontrada: {root}")
    if not root.is_dir():
        raise ValueError(f"a fonte não é um diretório: {root}")

    git_metadata = detect_git_metadata(root)
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    formats: Counter[str] = Counter()
    included_bytes = 0

    for path, walk_reason in _walk_files(root):
        relative_path = _normalized_relative(path, root)

        if walk_reason is not None:
            excluded.append({"path": relative_path, "reason": walk_reason})
            continue
        if path.is_symlink():
            excluded.append({"path": relative_path, "reason": "symlink_file"})
            continue
        if _is_generated_docs(relative_path):
            excluded.append({"path": relative_path, "reason": "generated_documentation"})
            continue
        if _looks_secret(path):
            excluded.append({"path": relative_path, "reason": "possible_secret"})
            continue
        if path.suffix.casefold() in EXCLUDED_FILE_SUFFIXES:
            excluded.append({"path": relative_path, "reason": "binary_or_large_result"})
            continue

        try:
            size = path.stat().st_size
            content_hash = _sha256(path)
        except OSError as exc:
            errors.append({"path": relative_path, "error": str(exc)})
            continue

        file_format = _classify_format(path)
        formats[file_format] += 1
        included_bytes += size
        included.append(
            {
                "document_id": _stable_document_id(project, relative_path),
                "path": relative_path,
                "format": file_format,
                "size_bytes": size,
                "content_hash": f"sha256:{content_hash}",
                "access_class": access_class,
                "status": "current" if git_metadata["versioned"] else "unversioned",
            }
        )

    snapshot_digest = hashlib.sha256()
    for item in included:
        snapshot_digest.update(item["path"].encode("utf-8"))
        snapshot_digest.update(b"\0")
        snapshot_digest.update(item["content_hash"].encode("ascii"))
        snapshot_digest.update(b"\n")

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "source": {
            "project": project,
            "root": str(root),
            "access_class": access_class,
            **git_metadata,
            "snapshot_hash": f"sha256:{snapshot_digest.hexdigest()}",
        },
        "summary": {
            "included_files": len(included),
            "excluded_files": len(excluded),
            "included_bytes": included_bytes,
            "formats": dict(sorted(formats.items())),
            "errors": len(errors),
        },
        "included": included,
        "excluded": sorted(excluded, key=lambda item: item["path"]),
        "errors": errors,
    }


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _yaml_lines(value: Any, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        if not value:
            return [f"{prefix}{{}}"]
        lines: list[str] = []
        for key, child in value.items():
            rendered_key = str(key)
            if isinstance(child, (dict, list)) and child:
                lines.append(f"{prefix}{rendered_key}:")
                lines.extend(_yaml_lines(child, indent + 2))
            elif isinstance(child, dict):
                lines.append(f"{prefix}{rendered_key}: {{}}")
            elif isinstance(child, list):
                lines.append(f"{prefix}{rendered_key}: []")
            else:
                lines.append(f"{prefix}{rendered_key}: {_yaml_scalar(child)}")
        return lines

    if isinstance(value, list):
        if not value:
            return [f"{prefix}[]"]
        lines = []
        for child in value:
            if isinstance(child, dict) and child:
                first_key, first_value = next(iter(child.items()))
                if isinstance(first_value, (dict, list)):
                    lines.append(f"{prefix}- {first_key}:")
                    lines.extend(_yaml_lines(first_value, indent + 4))
                else:
                    lines.append(
                        f"{prefix}- {first_key}: {_yaml_scalar(first_value)}"
                    )
                remaining = dict(list(child.items())[1:])
                if remaining:
                    lines.extend(_yaml_lines(remaining, indent + 2))
            elif isinstance(child, list):
                lines.append(f"{prefix}-")
                lines.extend(_yaml_lines(child, indent + 2))
            else:
                lines.append(f"{prefix}- {_yaml_scalar(child)}")
        return lines

    return [f"{prefix}{_yaml_scalar(value)}"]


def write_yaml(inventory: dict[str, Any], output: Path) -> None:
    destination = output.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(_yaml_lines(inventory)) + "\n"
    destination.write_text(content, encoding="utf-8", newline="\n")

