from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import PurePosixPath
from typing import Iterable

AGENT_INVESTIGATION_ALGORITHM = "bounded_tool_investigation_v8"
MAX_AGENT_ITERATIONS = 4
MAX_ACTIONS_PER_ITERATION = 3
MAX_OBSERVATIONS = 18
MAX_OBSERVATION_PREVIEW = 500
CALL_FRONTIER_MINIMUM_BONUS = 0.5
CALL_FRONTIER_MAXIMUM_BONUS = 2.0

ALLOWED_ACTIONS = {
    "find_callees",
    "find_callers",
    "search_code",
    "find_symbol",
    "open_neighborhood",
    "open_related",
}
ALLOWED_COVERAGE = {"covered", "partial", "gap"}

# These are language and source-code navigation words, not repository concepts.
# Keeping them out of the relevance score lets terms learned from the question,
# planner and observed code decide which real chunk should be inspected next.
_NAVIGATION_STOPWORDS = {
    "about",
    "arquivo",
    "code",
    "codigo",
    "como",
    "component",
    "componente",
    "definition",
    "detail",
    "details",
    "entry",
    "explain",
    "explique",
    "file",
    "flow",
    "funciona",
    "function",
    "implementation",
    "initialization",
    "initialize",
    "mostre",
    "onde",
    "point",
    "responsavel",
    "setup",
    "source",
    "trecho",
    "work",
}


def _json_object(raw: str | dict[str, object]) -> dict[str, object]:
    if isinstance(raw, dict):
        return raw
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
            if isinstance(possible, dict) and "actions" in possible:
                value = possible
                break
    if not isinstance(value, dict):
        raise ValueError("decisão de investigação não retornou objeto JSON")
    return value


def _bounded_text(value: object, *, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip()
    if not text or len(text) > maximum:
        return None
    if any(ord(character) < 32 for character in text):
        return None
    return text


def normalize_investigation_decision(
    raw: str | dict[str, object],
    *,
    observable_chunk_ids: set[str],
) -> dict[str, object]:
    """Validate a model decision before any read-only tool is executed."""

    value = _json_object(raw)
    actions: list[dict[str, str]] = []
    raw_actions = value.get("actions")
    if isinstance(raw_actions, list):
        for raw_action in raw_actions:
            if not isinstance(raw_action, dict):
                continue
            tool = str(raw_action.get("tool", ""))
            if tool not in ALLOWED_ACTIONS:
                continue
            if tool in {"search_code", "find_symbol"}:
                query = _bounded_text(raw_action.get("query"), maximum=200)
                if query is None:
                    continue
                action = {"tool": tool, "query": query}
            else:
                chunk_id = _bounded_text(
                    raw_action.get("chunk_id"), maximum=200
                )
                if chunk_id is None or chunk_id not in observable_chunk_ids:
                    continue
                action = {"tool": tool, "chunk_id": chunk_id}
            if action not in actions:
                actions.append(action)
            if len(actions) >= MAX_ACTIONS_PER_ITERATION:
                break

    coverage: list[dict[str, object]] = []
    raw_coverage = value.get("coverage")
    if isinstance(raw_coverage, list):
        for raw_item in raw_coverage:
            if not isinstance(raw_item, dict):
                continue
            aspect = _bounded_text(raw_item.get("aspect"), maximum=120)
            status = str(raw_item.get("status", ""))
            if aspect is None or status not in ALLOWED_COVERAGE:
                continue
            raw_ids = raw_item.get("chunk_ids")
            chunk_ids = (
                list(
                    dict.fromkeys(
                        str(chunk_id)
                        for chunk_id in raw_ids
                        if str(chunk_id) in observable_chunk_ids
                    )
                )[:8]
                if isinstance(raw_ids, list)
                else []
            )
            if status == "covered" and not chunk_ids:
                status = "partial"
            coverage.append(
                {"aspect": aspect, "status": status, "chunk_ids": chunk_ids}
            )
            if len(coverage) >= 10:
                break

    raw_keep = value.get("keep_chunk_ids")
    keep_chunk_ids = (
        list(
            dict.fromkeys(
                str(chunk_id)
                for chunk_id in raw_keep
                if str(chunk_id) in observable_chunk_ids
            )
        )[:12]
        if isinstance(raw_keep, list)
        else []
    )
    return {
        "algorithm": AGENT_INVESTIGATION_ALGORITHM,
        "actions": actions,
        "coverage": coverage,
        "keep_chunk_ids": keep_chunk_ids,
        "stop": (
            bool(value.get("stop"))
            and not actions
            and bool(coverage)
            and all(item["status"] == "covered" for item in coverage)
        ),
    }


def build_observations(
    retrievals: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    """Expose a bounded, provenance-preserving view of retrieved code."""

    observations: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    result_groups = [
        raw_results
        for retrieval in retrievals
        if isinstance((raw_results := retrieval.get("results")), list)
    ]
    # New tool results are inserted before the original retrievals. Walking a
    # whole group at once used to let one exploratory query evict every earlier
    # lead from the bounded observation window. Round-robin keeps independent
    # hypotheses observable and prevents semantic drift around one generic name.
    maximum_group_size = max((len(group) for group in result_groups), default=0)
    for result_position in range(maximum_group_size):
        for raw_results in result_groups:
            if result_position >= len(raw_results):
                continue
            result = raw_results[result_position]
            if not isinstance(result, dict):
                continue
            chunk_id = str(result.get("chunk_id", ""))
            occurrence = result.get("selected_occurrence")
            if not isinstance(occurrence, dict):
                occurrence = {}
            identity = (
                str(result.get("project", "")),
                str(occurrence.get("branch", "")),
                chunk_id,
            )
            if not chunk_id or identity in seen:
                continue
            seen.add(identity)
            text = str(result.get("text", ""))
            observations.append(
                {
                    "chunk_id": chunk_id,
                    "project": identity[0],
                    "branch": identity[1],
                    "path": str(result.get("path", "")),
                    "format": str(result.get("format", "")),
                    "title": str(result.get("title", "")),
                    "kind": str(result.get("kind", "")),
                    "lines": [
                        result.get("line_start"),
                        result.get("line_end"),
                    ],
                    "preview": text[:MAX_OBSERVATION_PREVIEW],
                    "source_kind": str(result.get("source_kind", "retrieval")),
                }
            )
            if len(observations) >= MAX_OBSERVATIONS:
                return observations
    return observations


def _search_terms(value: object) -> set[str]:
    text = unicodedata.normalize("NFKD", str(value)).encode(
        "ascii", "ignore"
    ).decode("ascii")
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    tokens = re.findall(r"[A-Za-z0-9]+", text.casefold())
    return {
        token
        for token in tokens
        if len(token) >= 3 and token not in _NAVIGATION_STOPWORDS
    }


def _humanize_identifier(value: str) -> str:
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", text).split())


def select_graph_frontier_results(
    *,
    question: str,
    search_hints: Iterable[str],
    results: list[dict[str, object]],
    limit: int = 3,
) -> list[dict[str, object]]:
    """Select a small question-relevant sample from an ordered call frontier.

    Call order remains the deterministic tie-breaker, but paths and symbol
    titles receive more weight than incidental mentions inside function bodies.
    If no candidate overlaps the query vocabulary, the ordered frontier is
    sampled at evenly spaced positions instead of assuming that its first calls
    are representative of the complete flow.
    """

    if limit < 1 or not results:
        return []
    query_terms = _search_terms(
        " ".join([question, *(str(hint) for hint in search_hints)])
    )
    ranked: list[tuple[float, int, dict[str, object]]] = []
    for position, result in enumerate(results):
        path_title_terms = _search_terms(
            f"{result.get('path', '')} {result.get('title', '')}"
        )
        text_terms = _search_terms(result.get("text", ""))
        score = 4.0 * len(query_terms & path_title_terms)
        score += float(len(query_terms & text_terms))
        ranked.append((score, -position, result))
    if any(score > 0 for score, _position, _result in ranked):
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in ranked[:limit]]

    selected_positions = {
        round(position * (len(results) - 1) / max(limit - 1, 1))
        for position in range(min(limit, len(results)))
    }
    return [
        result
        for position, result in enumerate(results)
        if position in selected_positions
    ][:limit]


def bounded_action_batch(
    *,
    model_actions: list[dict[str, str]],
    supplemental_action: dict[str, str] | None,
    executed_actions: set[tuple[str, str]],
    limit: int = MAX_ACTIONS_PER_ITERATION,
) -> list[dict[str, str]]:
    """Reserve a bounded slot for an independent lead before deduplication."""

    candidates = list(model_actions)
    if supplemental_action is not None and limit > 0:
        candidates = [
            *candidates[: max(limit - 1, 0)],
            supplemental_action,
            *candidates[max(limit - 1, 0) :],
        ]
    selected: list[dict[str, str]] = []
    for action in candidates:
        value = str(action.get("query") or action.get("chunk_id") or "")
        identity = (str(action.get("tool", "")), value.casefold())
        if identity in executed_actions:
            continue
        executed_actions.add(identity)
        selected.append(action)
        if len(selected) >= limit:
            break
    return selected


def fallback_investigation_actions(
    *,
    question: str,
    search_hints: Iterable[str],
    observations: list[dict[str, object]],
    previous_actions: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Choose bounded reads from observed vocabulary when a model stalls.

    This is deliberately repository-agnostic. It cannot invent a path, symbol or
    chunk: every target comes from an authorized observation, while query terms
    come from the user's question and the already bounded retrieval plan.
    """

    if not observations:
        return []
    query_terms = _search_terms(
        " ".join([question, *(str(hint) for hint in search_hints)])
    )
    observation_terms: list[set[str]] = []
    for observation in observations:
        observation_terms.append(
            _search_terms(
                " ".join(
                    str(observation.get(field, ""))
                    for field in ("path", "title", "kind", "preview")
                )
            )
        )
    document_frequency = {
        term: sum(term in terms for terms in observation_terms)
        for term in query_terms
    }

    ranked: list[tuple[float, int, dict[str, object]]] = []
    for position, (observation, terms) in enumerate(
        zip(observations, observation_terms, strict=True)
    ):
        path_title_terms = _search_terms(
            f"{observation.get('path', '')} {observation.get('title', '')}"
        )
        score = 0.0
        for term in query_terms & terms:
            rarity = math.log(
                (len(observations) + 1)
                / (document_frequency.get(term, 0) + 1)
            ) + 1.0
            score += rarity * (3.0 if term in path_title_terms else 1.0)
        source_kind = str(observation.get("source_kind", ""))
        if source_kind in {
            "agent_callers_evidence",
            "agent_callees_evidence",
        }:
            # A resolved edge is worth exploring, but it must not overwhelm a
            # substantially better match from the user's actual question.
            score += min(
                max(score * 0.25, CALL_FRONTIER_MINIMUM_BONUS),
                CALL_FRONTIER_MAXIMUM_BONUS,
            )
        # Stable retrieval order remains the final tie-breaker.
        ranked.append((score, -position, observation))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    prior = {
        (
            str(action.get("tool", "")),
            str(action.get("query") or action.get("chunk_id") or "").casefold(),
        )
        for action in previous_actions
    }
    actions: list[dict[str, str]] = []
    for _score, _position, selected in ranked:
        candidates: list[dict[str, str]] = []
        chunk_id = str(selected.get("chunk_id", "")).strip()
        if chunk_id:
            source_kind = str(selected.get("source_kind", ""))
            if source_kind == "agent_callers_evidence":
                # From an upstream caller, its outgoing calls expose the
                # orchestration around the originally observed operation.
                candidates.append(
                    {"tool": "find_callees", "chunk_id": chunk_id}
                )
                candidates.append(
                    {"tool": "find_callers", "chunk_id": chunk_id}
                )
                candidates.append(
                    {"tool": "open_neighborhood", "chunk_id": chunk_id}
                )
            elif source_kind == "agent_callees_evidence":
                # Continue downstream before returning to the already explored
                # coordinator. The neighborhood remains an independent read.
                candidates.append(
                    {"tool": "find_callees", "chunk_id": chunk_id}
                )
                candidates.append(
                    {"tool": "open_neighborhood", "chunk_id": chunk_id}
                )
                candidates.append(
                    {"tool": "find_callers", "chunk_id": chunk_id}
                )
            else:
                candidates.append(
                    {"tool": "open_neighborhood", "chunk_id": chunk_id}
                )
                candidates.append(
                    {"tool": "find_callers", "chunk_id": chunk_id}
                )
                candidates.append(
                    {"tool": "find_callees", "chunk_id": chunk_id}
                )
            candidates.append(
                {"tool": "open_related", "chunk_id": chunk_id}
            )

        observed_title = " ".join(
            str(selected.get("title", "")).split()
        ).strip()
        if not observed_title:
            observed_title = PurePosixPath(str(selected.get("path", ""))).stem
        humanized_title = _humanize_identifier(observed_title)
        if observed_title and len(observed_title) <= 200:
            candidates.append({"tool": "find_symbol", "query": observed_title})
        if humanized_title and len(humanized_title) <= 200:
            candidates.append(
                {"tool": "search_code", "query": humanized_title}
            )

        for action in candidates:
            value = str(action.get("query") or action.get("chunk_id") or "")
            identity = (action["tool"], value.casefold())
            if identity in prior or action in actions:
                continue
            actions.append(action)
            if len(actions) >= MAX_ACTIONS_PER_ITERATION:
                return actions
    return actions


def coverage_summary(coverage: list[dict[str, object]]) -> dict[str, int]:
    return {
        status: sum(item.get("status") == status for item in coverage)
        for status in sorted(ALLOWED_COVERAGE)
    }


def synthesis_guidance(
    coverage: list[dict[str, object]],
    sources: list[dict[str, object]],
) -> str:
    """Turn the evidence ledger into organization guidance, never new evidence."""

    if not coverage:
        return ""
    source_ids_by_chunk = {
        str(source.get("chunk_id", "")): str(source.get("source_id", ""))
        for source in sources
        if source.get("chunk_id") and source.get("source_id")
    }
    items: list[str] = []
    for item in coverage:
        source_ids = [
            source_ids_by_chunk[str(chunk_id)]
            for chunk_id in item.get("chunk_ids", [])
            if str(chunk_id) in source_ids_by_chunk
        ]
        items.append(
            f"{item.get('aspect')}: {item.get('status')}"
            + (f" ({', '.join(source_ids)})" if source_ids else "")
        )
    return (
        " The read-only investigation produced this organizational coverage ledger: "
        + "; ".join(items)
        + ". The ledger is planning metadata, not evidence. Use it to organize a "
        "coherent explanation, but support every factual statement with the actual "
        "sources. Explain covered aspects, qualify partial aspects, and explicitly "
        "leave gaps unresolved instead of filling them with outside knowledge."
    )
