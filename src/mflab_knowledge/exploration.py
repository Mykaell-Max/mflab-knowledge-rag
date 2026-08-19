from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath

OVERVIEW_PATTERNS = (
    r"^o que (?:e|sao)\b",
    r"^para que serve\b",
    r"^qual (?:e|a) (?:finalidade|proposito)\b",
    r"^visao geral\b",
    r"^what (?:is|are)\b",
    r"^what does\b",
    r"^overview\b",
    r"^describe\b",
)


def _normalized(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"[a-z0-9]+", plain))


def plan_exploration(query: str) -> dict[str, object]:
    """Build bounded retrieval hints without asserting domain facts."""

    normalized = _normalized(query)
    overview = any(re.search(pattern, normalized) for pattern in OVERVIEW_PATTERNS)
    if not overview:
        return {
            "intent": "direct",
            "expanded": False,
            "queries": [query],
            "require_scope_coverage": False,
        }
    return {
        "intent": "overview",
        "expanded": True,
        "queries": [
            query,
            f"{query} README purpose overview",
            f"{query} architecture modules components",
            f"{query} programming languages entry point capabilities",
        ],
        "require_scope_coverage": True,
    }


def overview_authority(result: dict[str, object]) -> tuple[int, int, str]:
    """Prefer broad entry documents over narrow or provisional artifacts."""

    raw_path = str(result.get("path", ""))
    path = PurePosixPath(raw_path)
    name = path.name.casefold()
    parts = [part.casefold() for part in path.parts]
    score = 0
    if name.startswith("readme"):
        score += 12 if len(parts) == 1 else 7
    if len(parts) == 1:
        score += 4
    if name in {"cmakelists.txt", "makefile", "doxyfile"}:
        score += 3
    if any(part in {"docs", "doc", "documentation"} for part in parts[:-1]):
        score += 2
    provisional = {
        "candidate",
        "draft",
        "experimental",
        "archive",
        "deprecated",
        "generated",
    }
    if any(token in provisional for part in parts for token in re.split(r"[^a-z]+", part)):
        score -= 6
    depth = len(parts)
    return (-score, depth, raw_path.casefold())


def exploration_instructions(
    plan: dict[str, object],
    sources: list[dict[str, object]],
) -> str:
    if plan.get("intent") != "overview":
        return ""
    projects = sorted(
        {
            str(source.get("project"))
            for source in sources
            if source.get("project")
        }
    )
    scopes = ", ".join(projects)
    source_scopes: dict[tuple[str, str], list[str]] = {}
    for source in sources:
        occurrence = source.get("selected_occurrence")
        if not isinstance(occurrence, dict):
            occurrence = {}
        scope = (
            str(source.get("project", "?")),
            str(occurrence.get("branch", "?")),
        )
        source_scopes.setdefault(scope, []).append(
            str(source.get("source_id", "?"))
        )
    source_map = "; ".join(
        f"{project} / {branch}: {', '.join(source_ids)}"
        for (project, branch), source_ids in sorted(source_scopes.items())
    )
    return (
        " This is a broad overview question. Define the subject using repository-wide "
        "evidence: purpose, major architecture or modules, implementation languages, "
        "and representative capabilities only when the sources support them. Treat a "
        "specialized feature as an example, never as the complete definition. "
        f"The available project scopes are: {scopes}. Cover every available project "
        "scope and cite at least one source from each. The source IDs available per "
        f"scope are: {source_map}. If the evidence supports only a partial overview, "
        "state that limitation explicitly."
    )
