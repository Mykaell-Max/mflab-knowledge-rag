from __future__ import annotations

import fnmatch
import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

INVENTORY_POLICY_SCHEMA_VERSION = "0.1"
PROFILE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


@dataclass(frozen=True)
class InventoryPolicy:
    name: str
    include_paths: tuple[str, ...]
    exclude_paths: tuple[str, ...]
    policy_hash: str
    source_file: Path | None = None

    def exclusion_reason(self, relative_path: str) -> str | None:
        if any(
            fnmatch.fnmatchcase(relative_path, pattern)
            for pattern in self.exclude_paths
        ):
            return "profile_excluded"
        if self.include_paths and not any(
            fnmatch.fnmatchcase(relative_path, pattern)
            for pattern in self.include_paths
        ):
            return "outside_profile_scope"
        return None


def _generic_policy() -> InventoryPolicy:
    identity = f"{INVENTORY_POLICY_SCHEMA_VERSION}\0generic\0".encode("utf-8")
    return InventoryPolicy(
        name="generic",
        include_paths=(),
        exclude_paths=(),
        policy_hash=f"sha256:{hashlib.sha256(identity).hexdigest()}",
        source_file=None,
    )


def _patterns(value: object, field: str, profile: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(
            f"inventory.profiles.{profile}.{field} deve ser uma lista de globs"
        )
    normalized: list[str] = []
    for raw in value:
        pattern = raw.strip()
        path = PurePosixPath(pattern)
        if (
            pattern.startswith("/")
            or "\\" in pattern
            or ".." in path.parts
        ):
            raise ValueError(
                f"inventory.profiles.{profile}.{field} contém caminho inseguro: "
                f"{pattern}"
            )
        normalized.append(pattern)
    return tuple(dict.fromkeys(normalized))


def load_inventory_policy(
    profile: str,
    path: Path | None = None,
) -> InventoryPolicy:
    selected_name = "generic" if profile == "auto" else profile.strip()
    if PROFILE_NAME.fullmatch(selected_name) is None:
        raise ValueError(f"perfil de inventário inválido: {profile}")

    selected_file = path
    if selected_file is None:
        conventional = Path("inventory-policies.toml")
        if conventional.is_file():
            selected_file = conventional
        elif selected_name == "generic":
            return _generic_policy()
        else:
            raise ValueError(
                f"perfil {selected_name} exige inventory-policies.toml "
                "ou --inventory-policy-file"
            )

    policy_file = selected_file.expanduser().resolve()
    try:
        raw = policy_file.read_bytes()
        value = tomllib.loads(raw.decode("utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"política de inventário não encontrada: {policy_file}"
        ) from exc
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(
            f"política de inventário inválida: {policy_file}: {exc}"
        ) from exc

    if value.get("schema_version") != INVENTORY_POLICY_SCHEMA_VERSION:
        raise ValueError("versão incompatível da política de inventário")
    unknown_top = sorted(set(value) - {"schema_version", "profiles"})
    if unknown_top:
        raise ValueError(
            "opções desconhecidas na política de inventário: "
            + ", ".join(unknown_top)
        )
    profiles = value.get("profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("inventory.profiles deve ser uma tabela TOML")
    selected = profiles.get(selected_name)
    if selected is None:
        if selected_name == "generic":
            return _generic_policy()
        raise ValueError(f"perfil de inventário não configurado: {selected_name}")
    if not isinstance(selected, dict):
        raise ValueError(f"inventory.profiles.{selected_name} deve ser uma tabela")
    unknown = sorted(set(selected) - {"include_paths", "exclude_paths"})
    if unknown:
        raise ValueError(
            f"opções desconhecidas em inventory.profiles.{selected_name}: "
            + ", ".join(unknown)
        )
    include_paths = _patterns(selected.get("include_paths"), "include_paths", selected_name)
    exclude_paths = _patterns(selected.get("exclude_paths"), "exclude_paths", selected_name)
    identity = (
        f"{INVENTORY_POLICY_SCHEMA_VERSION}\0{selected_name}\0"
        + "\0".join(include_paths)
        + "\0--exclude--\0"
        + "\0".join(exclude_paths)
    ).encode("utf-8")
    return InventoryPolicy(
        name=selected_name,
        include_paths=include_paths,
        exclude_paths=exclude_paths,
        policy_hash=f"sha256:{hashlib.sha256(identity).hexdigest()}",
        source_file=policy_file,
    )
