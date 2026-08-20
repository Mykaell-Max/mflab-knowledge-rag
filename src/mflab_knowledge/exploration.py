from __future__ import annotations

import json
import re
import unicodedata
from pathlib import PurePosixPath

QUERY_PLAN_ALGORITHM = "bounded_query_plan_v2"
MAX_PLANNED_QUERIES = 6
MAX_NAVIGATION_IDENTIFIERS = 12

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

COMPARISON_PATTERNS = (
    r"\bcompar(?:e|ar|acao)\b",
    r"\bdiferencas?\b",
    r"\bversus\b",
    r"\bcompare\b",
    r"\bdifferences?\b",
)

LOCATION_PATTERNS = (
    r"\bonde (?:fica|esta|e implementad[oa])\b",
    r"\bonde .+\b(?:declarad[oa]|definid[oa]|implementad[oa])\b",
    r"\bonde .+\b(?:inicializad[oa]|criad[oa]|construid[oa])\b",
    r"\bresponsavel por\b.+\b(?:inicializar|criar|construir|executar)\b",
    r"\b(?:qual|que) (?:arquivo|funcao|classe|trecho)\b",
    r"\bmostre (?:o |a )?(?:codigo|trecho|implementacao)\b",
    r"\bwhere (?:is|does)\b",
    r"\bshow (?:the )?(?:code|implementation)\b",
    r"\bwhich (?:file|function|class)\b",
)

MECHANISM_PATTERNS = (
    r"\bcomo(?: .+)? (?:funciona|opera|implementad[oa]|resolvid[oa]|calculad[oa])\b",
    r"\bexplique (?:como|o funcionamento)\b",
    r"\bexplique .+\b(?:fluxo|ciclo|arquitetura|integracao)\b",
    r"\bhow (?:does|is|are)\b",
    r"\bexplain how\b",
    r"\bexplain .+\b(?:flow|lifecycle|architecture|integration)\b",
)

CONSTRUCTION_PATTERNS = (
    r"\b(?:inicializ|cria|constr)[a-z0-9]*\b",
    r"\b(?:initializ|creat|construct)[a-z0-9]*\b",
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
    comparison = any(
        re.search(pattern, normalized) for pattern in COMPARISON_PATTERNS
    )
    location = any(re.search(pattern, normalized) for pattern in LOCATION_PATTERNS)
    mechanism = any(
        re.search(pattern, normalized) for pattern in MECHANISM_PATTERNS
    )
    if overview:
        intent = "overview"
        hints = (
            "README purpose overview",
            "architecture modules components",
            "programming languages entry point capabilities",
        )
    elif comparison:
        intent = "comparison"
        hints = (
            "definition implementation",
            "configuration tests behavior",
        )
    elif location:
        intent = "location"
        construction = any(
            re.search(pattern, normalized) for pattern in CONSTRUCTION_PATTERNS
        )
        hints = tuple(
            [
                *(
                    ["construction factory creation concrete implementation"]
                    if construction
                    else []
                ),
                "definition declaration implementation",
                "call usage configuration",
            ]
        )
    elif mechanism:
        intent = "mechanism"
        hints = (
            "implementation algorithm call flow",
            "configuration tests",
        )
    else:
        intent = "direct"
        hints = ()
    return {
        "intent": intent,
        "expanded": bool(hints),
        "queries": [query, *(f"{query} {hint}" for hint in hints)],
        "require_scope_coverage": intent in {"overview", "comparison"},
    }


def normalize_query_plan(
    raw: str | dict[str, object],
    *,
    original_query: str,
    fallback_queries: list[str],
) -> dict[str, object]:
    """Validate model-proposed search vocabulary without accepting factual claims."""

    if isinstance(raw, str):
        candidate = raw.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, re.DOTALL)
        if fenced:
            candidate = fenced.group(1)
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            value = None
            decoder = json.JSONDecoder()
            for match in re.finditer(r"\{", candidate):
                try:
                    possible, _end = decoder.raw_decode(candidate, match.start())
                except json.JSONDecodeError:
                    continue
                if isinstance(possible, dict) and (
                    "queries" in possible or "identifiers" in possible
                ):
                    value = possible
                    break
    else:
        value = raw
    if not isinstance(value, dict):
        raise ValueError("planejamento de busca deve ser um objeto JSON")

    def safe_strings(raw_values: object, *, maximum: int, length: int) -> list[str]:
        if not isinstance(raw_values, list):
            return []
        selected: list[str] = []
        seen: set[str] = set()
        for raw_value in raw_values:
            if not isinstance(raw_value, str):
                continue
            text = " ".join(raw_value.split()).strip()
            if not text or len(text) > length or any(ord(char) < 32 for char in text):
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            selected.append(text)
            if len(selected) >= maximum:
                break
        return selected

    original = " ".join(original_query.split()).strip()
    if not original or len(original) > 2_000:
        raise ValueError("consulta original inválida para planejamento")
    proposed_queries = safe_strings(
        value.get("queries"), maximum=MAX_PLANNED_QUERIES - 2, length=200
    )
    queries = safe_strings(
        [original, *proposed_queries, *fallback_queries],
        maximum=MAX_PLANNED_QUERIES,
        length=2_000,
    )
    identifiers = safe_strings(
        value.get("identifiers"),
        maximum=MAX_NAVIGATION_IDENTIFIERS,
        length=120,
    )
    return {
        "algorithm": QUERY_PLAN_ALGORITHM,
        "generated": bool(proposed_queries or identifiers),
        "queries": queries,
        "identifiers": identifiers,
    }


def navigation_terms(
    query_plan: dict[str, object],
    retrievals: list[dict[str, object]],
) -> list[str]:
    """Derive bounded structural lookup terms from plan and retrieved metadata."""

    candidates: list[str] = []
    raw_identifiers = query_plan.get("identifiers")
    if isinstance(raw_identifiers, list):
        candidates.extend(
            str(value) for value in raw_identifiers if isinstance(value, str)
        )
    for retrieval in retrievals:
        results = retrieval.get("results")
        if not isinstance(results, list):
            continue
        for result in results[:5]:
            if not isinstance(result, dict):
                continue
            title = result.get("title")
            if isinstance(title, str) and title.strip():
                candidates.append(title)
            path = result.get("path")
            if isinstance(path, str) and path.strip():
                candidates.append(PurePosixPath(path).stem)

    selected: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        value = " ".join(candidate.split()).strip()
        normalized = _normalized(value)
        if len(normalized) < 3 or normalized in seen:
            continue
        seen.add(normalized)
        selected.append(value[:120])
        if len(selected) >= MAX_NAVIGATION_IDENTIFIERS:
            break
    return selected


def overview_authority(result: dict[str, object]) -> tuple[int, int, str]:
    """Prefer broad entry documents over narrow or provisional artifacts."""

    raw_path = str(result.get("path", ""))
    path = PurePosixPath(raw_path)
    name = path.name.casefold()
    parts = [part.casefold() for part in path.parts]
    score = 0
    if result.get("source_kind") == "derived_structure":
        score += 30
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
    intent = plan.get("intent")
    if intent == "location":
        return (
            " This is a code-location question. A source that merely mentions a term, "
            "type, or parameter is not proof that it implements or performs the "
            "requested operation. Distinguish definitions, declarations, callers, and "
            "configuration. Show code only when its behavior directly supports the "
            "location claim; otherwise state the remaining evidence gap."
        )
    if intent == "mechanism":
        return (
            " This is a mechanism question. Explain only the flow established by the "
            "available implementation, its callers or configuration, and tests. Do not "
            "fill missing algorithmic steps with general domain knowledge. Distinguish "
            "what the code demonstrates from what the evidence does not establish."
        )
    if intent == "comparison":
        return (
            " This is a comparison question. Compare only equivalent evidence from "
            "each available scope, preserve project and branch distinctions, and cite "
            "every side of each claimed difference. If one side lacks evidence, report "
            "the asymmetry instead of inferring a difference."
        )
    if intent != "overview":
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
        "A source marked derived_structure is a deterministic map of indexed "
        "metadata: use it only for layout, file-format, and coverage claims, never "
        "as evidence of scientific purpose or capabilities. "
        f"The available project scopes are: {scopes}. Cover every available project "
        "scope and cite at least one source from each. The source IDs available per "
        f"scope are: {source_map}. If the evidence supports only a partial overview, "
        "state that limitation explicitly. Describe these as the indexed or available "
        "project scopes; do not claim they are the only, principal, or complete set "
        "unless the evidence explicitly establishes that fact."
    )


def overview_quality_issues(
    answer: str,
    plan: dict[str, object],
) -> list[str]:
    """Detect generic scope overclaims in repository-wide answers."""

    if plan.get("intent") != "overview":
        return []
    normalized = _normalized(answer)
    overclaim_patterns = (
        r"\bprojetos principais\b",
        r"\bprincipais projetos\b",
        r"\bunicos projetos\b",
        r"\bmain projects\b",
        r"\bprincipal projects\b",
        r"\bonly projects\b",
        r"\bcomplete set of projects\b",
    )
    return (
        ["available_scopes_presented_as_definitive"]
        if any(re.search(pattern, normalized) for pattern in overclaim_patterns)
        else []
    )
