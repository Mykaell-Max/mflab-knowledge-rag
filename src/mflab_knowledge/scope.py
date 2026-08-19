from __future__ import annotations

import re
import unicodedata


def _terms(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    ascii_value = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"[a-z0-9]+", ascii_value))


def _contains_alias(query_terms: str, alias: str) -> bool:
    candidate = _terms(alias)
    return bool(candidate) and f" {candidate} " in f" {query_terms} "


def _branch_is_explicit(query: str, branch: str) -> bool:
    escaped = re.escape(branch.casefold())
    raw = query.casefold()
    if re.search(rf"[`'\"]{escaped}[`'\"]", raw):
        return True
    if re.search(rf"\b(?:branch|ref)\s+[`'\"]?{escaped}(?![\w/.-])", raw):
        return True
    if "/" in branch or "-" in branch:
        return re.search(rf"(?<![\w/.-]){escaped}(?![\w/.-])", raw) is not None
    return False


def resolve_query_scopes(
    query: str,
    repositories: list[dict[str, object]],
) -> dict[str, object]:
    """Resolve only high-confidence catalog names; never invent a scope."""

    query_terms = _terms(query)
    matched_repositories: list[dict[str, object]] = []
    for repository in repositories:
        project = str(repository.get("project", ""))
        aliases = [project, *map(str, repository.get("aliases", []))]
        if any(_contains_alias(query_terms, alias) for alias in aliases):
            matched_repositories.append(repository)

    candidates = matched_repositories or repositories
    scopes: list[dict[str, object]] = []
    branch_matches = False
    for repository in candidates:
        project = str(repository.get("project", ""))
        branch_names = [str(value) for value in repository.get("branch_names", [])]
        mentioned = [
            branch for branch in branch_names if _branch_is_explicit(query, branch)
        ]
        if mentioned:
            branch_matches = True
            scopes.extend(
                {
                    "project": project,
                    "branch": branch,
                    "reason": "branch_mentioned",
                }
                for branch in mentioned
            )
        elif matched_repositories:
            scopes.append(
                {
                    "project": project,
                    "branch": repository.get("preferred_branch"),
                    "reason": "project_mentioned",
                }
            )

    if not matched_repositories and branch_matches:
        scopes = [scope for scope in scopes if scope["reason"] == "branch_mentioned"]
    elif not matched_repositories and not branch_matches:
        scopes = [
            {
                "project": str(repository.get("project", "")),
                "branch": repository.get("preferred_branch"),
                "reason": "preferred_default",
            }
            for repository in repositories
            if repository.get("preferred_branch")
        ]

    unique: list[dict[str, object]] = []
    seen: set[tuple[str, str | None]] = set()
    for scope in scopes:
        key = (
            str(scope["project"]),
            str(scope["branch"]) if scope.get("branch") is not None else None,
        )
        if key not in seen:
            seen.add(key)
            unique.append(scope)

    if matched_repositories:
        mode = "projects_from_query"
    elif branch_matches:
        mode = "branches_from_query"
    elif unique:
        mode = "preferred_defaults"
    else:
        mode = "broad"
    return {
        "mode": mode,
        "automatic": bool(unique),
        "scopes": unique,
    }
