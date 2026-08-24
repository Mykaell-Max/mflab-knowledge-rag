from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import PurePosixPath
from typing import Iterable

AGENT_INVESTIGATION_ALGORITHM = "bounded_tool_investigation_v26"
ANSWER_COVERAGE_ALGORITHM = "audited_answer_coverage_v6"
MAX_AGENT_ITERATIONS = 5
MAX_ACTIONS_PER_ITERATION = 3
MAX_OBSERVATIONS = 18
MAX_OBSERVATION_PREVIEW = 500
LATEST_TOOL_OBSERVATION_QUOTA = 6
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

_LIFECYCLE_VOCABULARY = {
    "lifecycle_start": {
        "bootstrap",
        "build",
        "configure",
        "configuration",
        "create",
        "creation",
        "init",
        "initialization",
        "initialize",
        "initializing",
        "setup",
        "configuracao",
        "configurar",
        "criacao",
        "criar",
        "inicializacao",
        "inicializar",
        "inicio",
    },
    "lifecycle_step": {
        "advance",
        "advancement",
        "execute",
        "execution",
        "run",
        "step",
        "update",
        "atualizar",
        "avancar",
        "avanco",
        "executar",
        "passo",
    },
    "lifecycle_finish": {
        "cleanup",
        "destroy",
        "finalization",
        "finalize",
        "shutdown",
        "destruir",
        "encerrar",
        "finalizacao",
        "finalizar",
    },
}

_LIFECYCLE_PREFIXES = {
    "lifecycle_start": (
        "bootstrap",
        "build",
        "configur",
        "creat",
        "cria",
        "init",
        "inicial",
        "setup",
    ),
    "lifecycle_step": (
        "advanc",
        "atualiz",
        "avanc",
        "execut",
        "passo",
        "run",
        "step",
        "updat",
    ),
    "lifecycle_finish": (
        "cleanup",
        "destroy",
        "destru",
        "encerr",
        "final",
        "shutdown",
    ),
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
            if isinstance(possible, dict) and (
                "actions" in possible or "coverage" in possible
            ):
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
    aspect_ids: dict[str, str] | None = None,
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
            aspect_id = _bounded_text(raw_item.get("aspect_id"), maximum=20)
            if aspect_ids is not None and aspect_id in aspect_ids:
                aspect = aspect_ids[aspect_id]
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


def normalize_answer_coverage(
    raw: str | dict[str, object],
    *,
    required_aspects: Iterable[str | dict[str, object]],
    valid_claim_ids: set[str],
) -> dict[str, object]:
    """Validate a completeness judgment over claims already audited as supported."""

    required: list[str] = []
    aspect_ids: dict[str, str] = {}
    required_keys: set[str] = set()
    for index, raw_aspect in enumerate(required_aspects, start=1):
        if isinstance(raw_aspect, dict):
            aspect = _bounded_text(raw_aspect.get("aspect"), maximum=120)
            aspect_id = _bounded_text(raw_aspect.get("aspect_id"), maximum=20)
        else:
            aspect = _bounded_text(raw_aspect, maximum=120)
            aspect_id = None
        if aspect is None or aspect.casefold() in required_keys:
            continue
        required.append(aspect)
        required_keys.add(aspect.casefold())
        aspect_ids[aspect_id or f"A{index}"] = aspect
        if len(required) >= 6:
            break
    value = _json_object(raw)
    by_aspect: dict[str, dict[str, object]] = {}
    raw_coverage = value.get("coverage")
    if isinstance(raw_coverage, list):
        for raw_item in raw_coverage:
            if not isinstance(raw_item, dict):
                continue
            aspect = _bounded_text(raw_item.get("aspect"), maximum=120)
            aspect_id = _bounded_text(raw_item.get("aspect_id"), maximum=20)
            if aspect_id in aspect_ids:
                # Stable IDs prevent a local model from accidentally invalidating
                # the whole judgment by translating or paraphrasing an aspect.
                aspect = aspect_ids[aspect_id]
            elif len(required) == 1:
                # Each final coverage request contains exactly one server-owned
                # aspect. Its position is therefore unambiguous even if a local
                # model omits the opaque ID or translates the display label.
                aspect = required[0]
            status = str(raw_item.get("status", ""))
            if (
                aspect is None
                or aspect.casefold() not in required_keys
                or status not in ALLOWED_COVERAGE
            ):
                continue
            raw_ids = raw_item.get("claim_ids")
            claim_ids = (
                list(
                    dict.fromkeys(
                        str(claim_id)
                        for claim_id in raw_ids
                        if str(claim_id) in valid_claim_ids
                    )
                )[:12]
                if isinstance(raw_ids, list)
                else []
            )
            if status == "covered" and not claim_ids:
                status = "partial"
            by_aspect[aspect.casefold()] = {
                "aspect": next(
                    value for value in required if value.casefold() == aspect.casefold()
                ),
                "status": status,
                "claim_ids": claim_ids,
            }
    coverage = [
        by_aspect.get(
            aspect.casefold(),
            {"aspect": aspect, "status": "gap", "claim_ids": []},
        )
        for aspect in required
    ]
    return {
        "algorithm": ANSWER_COVERAGE_ALGORITHM,
        "performed": bool(required),
        "complete": bool(coverage)
        and all(item["status"] == "covered" for item in coverage),
        "coverage": coverage,
        "summary": coverage_summary(coverage),
    }


def build_observations(
    retrievals: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    """Expose a bounded, provenance-preserving view of retrieved code."""

    observations: list[dict[str, object]] = []
    seen: set[tuple[str, str, str]] = set()
    retrieval_list = list(retrievals)
    result_groups = [
        (str(retrieval.get("mode", "")), raw_results)
        for retrieval in retrieval_list
        if isinstance((raw_results := retrieval.get("results")), list)
    ]

    def append_observation(result: object) -> bool:
        if not isinstance(result, dict):
            return False
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
            return False
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
        return True

    # The newest tool response is the only evidence that the model has not yet
    # had an opportunity to inspect. Reserve a small bounded part of the window
    # for it, while leaving most slots to independent earlier hypotheses.
    if result_groups and result_groups[0][0] == "agent_tools":
        latest_results = sorted(
            enumerate(result_groups[0][1]),
            key=lambda item: (
                str(item[1].get("source_kind", ""))
                not in {
                    "agent_callers_evidence",
                    "agent_callees_evidence",
                }
                if isinstance(item[1], dict)
                else True,
                item[0],
            ),
        )
        latest_added = 0
        for _position, result in latest_results:
            if append_observation(result):
                latest_added += 1
            if latest_added >= LATEST_TOOL_OBSERVATION_QUOTA:
                break

    # New tool results are inserted before the original retrievals. Walking a
    # whole group at once used to let one exploratory query evict every earlier
    # lead from the bounded observation window. Round-robin keeps independent
    # hypotheses observable and prevents semantic drift around one generic name.
    maximum_group_size = max(
        (len(group) for _mode, group in result_groups), default=0
    )
    for result_position in range(maximum_group_size):
        for _mode, raw_results in result_groups:
            if result_position >= len(raw_results):
                continue
            append_observation(raw_results[result_position])
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


def _structural_search_terms(value: object) -> set[str]:
    """Retain generic lifecycle intent without restoring noisy stopwords."""

    text = unicodedata.normalize("NFKD", str(value)).encode(
        "ascii", "ignore"
    ).decode("ascii")
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    raw_tokens = {
        token.casefold() for token in re.findall(r"[A-Za-z0-9]+", text)
    }
    terms = _search_terms(value)
    terms.update(
        marker
        for marker, vocabulary in _LIFECYCLE_VOCABULARY.items()
        if raw_tokens & vocabulary
        or any(
            token.startswith(prefix)
            for token in raw_tokens
            for prefix in _LIFECYCLE_PREFIXES[marker]
        )
    )
    return terms


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
    query_terms = _structural_search_terms(
        " ".join([question, *(str(hint) for hint in search_hints)])
    )
    ranked: list[tuple[float, int, dict[str, object]]] = []
    scores_by_object: dict[int, float] = {}
    for position, result in enumerate(results):
        path_title_terms = _structural_search_terms(
            f"{result.get('path', '')} {result.get('title', '')}"
        )
        text_terms = _structural_search_terms(result.get("text", ""))
        score = 4.0 * len(query_terms & path_title_terms)
        score += float(len(query_terms & text_terms))
        ranked.append((score, -position, result))
        scores_by_object[id(result)] = score
    if any(score > 0 for score, _position, _result in ranked):
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        candidate_tiers = [
            [item[2] for item in ranked if item[0] > 0],
            [item[2] for item in ranked if item[0] <= 0],
        ]
    else:
        sample_size = min(limit, len(results))
        selected_positions = {
            round(position * (len(results) - 1) / max(sample_size - 1, 1))
            for position in range(sample_size)
        }
        sampled = [
            result
            for position, result in enumerate(results)
            if position in selected_positions
        ]
        candidate_tiers = [
            [
                *sampled,
                *(
                    result
                    for position, result in enumerate(results)
                    if position not in selected_positions
                ),
            ]
        ]

    # A call frontier often contains many operations from one implementation
    # unit. Reserve most slots for distinct paths, but keep two positions for
    # repeated lifecycle methods from a coordinator. Otherwise an entry point,
    # temporal step and finalizer in the same file can never coexist when the
    # frontier also contains many helper paths.
    ordered_candidates: list[dict[str, object]] = []
    relevant_candidates: list[dict[str, object]] = []
    ordered_identities: set[str] = set()
    for candidates in candidate_tiers:
        for result in candidates:
            chunk_id = str(result.get("chunk_id", ""))
            path = str(result.get("path", ""))
            identity = chunk_id or f"{path}:{result.get('title', '')}"
            if identity in ordered_identities:
                continue
            ordered_identities.add(identity)
            ordered_candidates.append(result)
            score = scores_by_object.get(id(result), 0.0)
            if score > 0:
                relevant_candidates.append(result)

    selected: list[dict[str, object]] = []
    selected_ids: set[str] = set()
    selected_path_families: set[str] = set()

    def path_family(path: str) -> str:
        candidate = PurePosixPath(path)
        return str(candidate.with_suffix("")) if candidate.suffix else path

    def append_selected(result: dict[str, object]) -> bool:
        if len(selected) >= limit:
            return False
        chunk_id = str(result.get("chunk_id", ""))
        path = str(result.get("path", ""))
        identity = chunk_id or f"{path}:{result.get('title', '')}"
        if identity in selected_ids:
            return False
        selected.append(result)
        selected_ids.add(identity)
        if path:
            selected_path_families.add(path_family(path))
        return True

    # A flow explanation needs both sides of at least one verified edge. Keep
    # the strongest lexical anchor first, then reserve one upstream caller and
    # one downstream callee when observed. This prevents path diversity from
    # preserving either the coordinator or its implementation, but not both.
    if ordered_candidates:
        append_selected(ordered_candidates[0])
    for role in ("callers", "callees"):
        role_candidates = [
            result
            for result in ordered_candidates
            if role in str(result.get("source_kind", ""))
        ]
        candidate = next(
            (
                result
                for result in role_candidates
                if path_family(str(result.get("path", "")))
                not in selected_path_families
            ),
            role_candidates[0] if role_candidates else None,
        )
        if candidate is not None:
            append_selected(candidate)

    diversity_target = min(limit, max(3, limit - 2))
    # Diversity is useful only inside the relevant tier. Previously, a weakly
    # connected helper from an unrelated subsystem could take a distinct-path
    # slot before a second lifecycle method from the queried coordinator.
    diversity_candidates = relevant_candidates or ordered_candidates
    for result in diversity_candidates:
        path = str(result.get("path", ""))
        if path and path_family(path) in selected_path_families:
            continue
        append_selected(result)
        if len(selected) >= diversity_target:
            break
    if len(selected) >= limit:
        return selected
    # Consume all remaining query-relevant lifecycle methods before filling
    # unused capacity with merely connected frontier nodes.
    relevant_object_ids = {id(result) for result in relevant_candidates}
    completion_candidates = [
        *relevant_candidates,
        *(
            result
            for result in ordered_candidates
            if id(result) not in relevant_object_ids
        ),
    ]
    for result in completion_candidates:
        if not append_selected(result):
            continue
        if len(selected) >= limit:
            return selected
    return selected


def prioritize_kept_chunk_ids(
    kept_chunk_ids: Iterable[str],
    coverage: Iterable[dict[str, object]],
) -> list[str]:
    """Place evidence explicitly tied to coverage before incidental keeps."""

    kept = list(dict.fromkeys(str(chunk_id) for chunk_id in kept_chunk_ids))
    kept_set = set(kept)
    prioritized: list[str] = []
    deferred: list[str] = []
    for item in coverage:
        raw_ids = item.get("chunk_ids")
        if not isinstance(raw_ids, list):
            continue
        aspect_ids: list[str] = []
        for chunk_id in raw_ids:
            value = str(chunk_id)
            if value in kept_set and value not in aspect_ids:
                aspect_ids.append(value)
        if aspect_ids and aspect_ids[0] not in prioritized:
            prioritized.append(aspect_ids[0])
        deferred.extend(
            value
            for value in aspect_ids[1:]
            if value not in prioritized and value not in deferred
        )
    prioritized.extend(deferred)
    prioritized.extend(
        chunk_id for chunk_id in kept if chunk_id not in prioritized
    )
    return prioritized


def reserve_chunk_ids_by_aspect(
    coverage: Iterable[dict[str, object]],
) -> list[str]:
    """Reserve one distinct observed chunk for every evidenced answer facet."""

    reserved: list[str] = []
    for item in coverage:
        raw_ids = item.get("chunk_ids")
        if not isinstance(raw_ids, list):
            continue
        for chunk_id in raw_ids:
            value = str(chunk_id)
            if value and value not in reserved:
                reserved.append(value)
                break
    return reserved


def reconcile_answer_coverage_with_provenance(
    answer_coverage: dict[str, object],
    *,
    investigation_coverage: Iterable[dict[str, object]],
    sources: Iterable[dict[str, object]],
    supported_claims: Iterable[dict[str, object]],
    sectional_claim_ids: dict[str, set[str]] | None = None,
) -> dict[str, object]:
    """Join semantic coverage to the exact evidence retained for each facet.

    A supported claim citing an exact retained chunk is a conservative partial
    floor. Conversely, a semantic ``covered`` verdict cannot stand as complete
    when all referenced claims cite evidence assigned only to other facets.
    The latter is downgraded to partial rather than gap because the semantic
    judgment may still have identified useful adjacent support.
    """

    raw_items = answer_coverage.get("coverage")
    if not isinstance(raw_items, list):
        return answer_coverage
    source_ids_by_chunk: dict[str, set[str]] = {}
    for source in sources:
        chunk_id = str(source.get("chunk_id", ""))
        source_id = str(source.get("source_id", ""))
        if chunk_id and source_id:
            source_ids_by_chunk.setdefault(chunk_id, set()).add(source_id)
    claim_ids_by_source: dict[str, list[str]] = {}
    source_ids_by_claim: dict[str, set[str]] = {}
    for claim in supported_claims:
        claim_id = str(claim.get("claim_id", ""))
        raw_source_ids = claim.get("source_ids")
        if not claim_id or not isinstance(raw_source_ids, list):
            continue
        for source_id in raw_source_ids:
            value = str(source_id)
            if value:
                claim_ids_by_source.setdefault(value, []).append(claim_id)
                source_ids_by_claim.setdefault(claim_id, set()).add(value)
    investigation_by_aspect = {
        str(item.get("aspect", "")).casefold(): item
        for item in investigation_coverage
        if str(item.get("aspect", "")).strip()
    }
    reconciled: list[dict[str, object]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        investigation_item = investigation_by_aspect.get(
            str(item.get("aspect", "")).casefold()
        )
        raw_chunk_ids = (
            investigation_item.get("chunk_ids")
            if isinstance(investigation_item, dict)
            else None
        )
        aspect_source_ids: set[str] = set()
        supporting_claim_ids: list[str] = []
        if isinstance(raw_chunk_ids, list):
            for chunk_id in raw_chunk_ids:
                for source_id in source_ids_by_chunk.get(str(chunk_id), set()):
                    aspect_source_ids.add(source_id)
                    for claim_id in claim_ids_by_source.get(source_id, []):
                        if claim_id not in supporting_claim_ids:
                            supporting_claim_ids.append(claim_id)
        section_ids = (sectional_claim_ids or {}).get(
            str(item.get("aspect", "")).casefold(),
            set(),
        )
        if item.get("status") == "gap":
            # A local model may fail to associate an already verified claim with
            # the display label of its own generated section.  Section membership
            # is server-owned provenance, so it is a conservative partial floor in
            # exactly the same way as an investigation chunk retained for a facet.
            gap_claim_ids = list(
                dict.fromkeys([*supporting_claim_ids, *sorted(section_ids)])
            )
            if gap_claim_ids:
                item["status"] = "partial"
                item["claim_ids"] = gap_claim_ids[:12]
        elif item.get("status") == "covered" and aspect_source_ids:
            cited_for_verdict = {
                source_id
                for claim_id in item.get("claim_ids", [])
                for source_id in source_ids_by_claim.get(str(claim_id), set())
            }
            raw_audited_claim_ids = item.get("claim_ids")
            audited_claim_ids = (
                {str(value) for value in raw_audited_claim_ids}
                if isinstance(raw_audited_claim_ids, list)
                else set()
            )
            if not (
                cited_for_verdict & aspect_source_ids
                or audited_claim_ids & section_ids
            ):
                item["status"] = "partial"
        reconciled.append(item)
    result = dict(answer_coverage)
    result["coverage"] = reconciled
    result["complete"] = bool(reconciled) and all(
        item.get("status") == "covered" for item in reconciled
    )
    result["summary"] = coverage_summary(reconciled)
    return result


_COVERAGE_CODE_TERMS = {
    "code",
    "codigo",
    "excerpt",
    "excerpts",
    "snippet",
    "snippets",
    "trecho",
    "trechos",
}
_COVERAGE_RELATION_TERMS = {
    "call",
    "calls",
    "connect",
    "connection",
    "flow",
    "fluxo",
    "integracao",
    "integration",
    "mechanism",
    "mecanismo",
    "sequence",
    "sequencia",
}


def resolve_verified_answer_contract(
    answer_coverage: dict[str, object],
    *,
    answer: str,
    supported_claims: Iterable[dict[str, object]],
    sectional_claim_ids: dict[str, set[str]],
    sectional_code_aspects: set[str] | None = None,
) -> dict[str, object]:
    """Resolve coarse model labels with an objective verified-answer contract.

    The semantic auditor remains useful for finding omissions, but a probabilistic
    ``partial`` label must not veto an answer indefinitely when the server can
    establish all of the following facts without scientific assumptions:

    * the facet has claims that passed claim-to-source verification;
    * those claims occur in the generated section assigned to the facet;
    * a requested code form is visibly present as fenced code; and
    * a requested relation has more than one verified factual unit.

    This definition is intentionally about satisfying the explicit question, not
    exhaustively documenting the subsystem.  A real gap, a missing code form, or
    a relation represented by only one isolated fact remains partial.
    """

    raw_items = answer_coverage.get("coverage")
    if not isinstance(raw_items, list):
        return answer_coverage
    supported_by_id = {
        str(claim.get("claim_id", "")): claim
        for claim in supported_claims
        if str(claim.get("claim_id", ""))
        and claim.get("verdict") == "supported"
    }
    fenced_code_present = "```" in answer
    resolved: list[dict[str, object]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        aspect_key = str(item.get("aspect", "")).strip().casefold()
        raw_claim_ids = item.get("claim_ids")
        audited_ids = {
            str(value)
            for value in raw_claim_ids
            if str(value) in supported_by_id
        } if isinstance(raw_claim_ids, list) else set()
        section_ids = {
            value
            for value in sectional_claim_ids.get(aspect_key, set())
            if value in supported_by_id
        }
        contract_ids = audited_ids & section_ids
        if not contract_ids and item.get("status") != "covered":
            resolved.append(item)
            continue
        normalized_aspect = unicodedata.normalize(
            "NFKD", aspect_key.replace("_", " ")
        ).encode("ascii", "ignore").decode("ascii")
        terms = re.findall(r"[a-z0-9]+", normalized_aspect)
        requests_code = bool(set(terms) & _COVERAGE_CODE_TERMS)
        requests_relation = bool(set(terms) & _COVERAGE_RELATION_TERMS)
        form_satisfied = not requests_code or (
            fenced_code_present
            and aspect_key in (sectional_code_aspects or set())
        )
        relation_satisfied = not requests_relation or len(contract_ids) >= 2
        if (
            item.get("status") in {"partial", "gap"}
            and contract_ids
            and form_satisfied
            and relation_satisfied
        ):
            item["status"] = "covered"
            item["claim_ids"] = sorted(contract_ids)[:12]
            item["resolution"] = "verified_answer_contract"
        resolved.append(item)
    result = dict(answer_coverage)
    result["coverage"] = resolved
    result["complete"] = bool(resolved) and all(
        item.get("status") == "covered" for item in resolved
    )
    result["summary"] = coverage_summary(resolved)
    if result["complete"]:
        result["resolution"] = "verified_answer_contract"
    return result


def repeated_complete_coverage(
    previous: Iterable[dict[str, object]],
    current: Iterable[dict[str, object]],
) -> bool:
    """Detect a stable, fully evidenced ledger without domain assumptions."""

    def signature(
        values: Iterable[dict[str, object]],
    ) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
        normalized: list[tuple[str, str, tuple[str, ...]]] = []
        for item in values:
            raw_ids = item.get("chunk_ids")
            chunk_ids = (
                tuple(sorted(dict.fromkeys(str(value) for value in raw_ids)))
                if isinstance(raw_ids, list)
                else ()
            )
            normalized.append(
                (
                    str(item.get("aspect", "")).casefold(),
                    str(item.get("status", "")),
                    chunk_ids,
                )
            )
        return tuple(sorted(normalized))

    previous_signature = signature(previous)
    current_signature = signature(current)
    return bool(current_signature) and current_signature == previous_signature and all(
        status == "covered" and bool(chunk_ids)
        for _aspect, status, chunk_ids in current_signature
    )


def successful_graph_traversal(actions: Iterable[dict[str, object]]) -> bool:
    """Return whether a resolved call edge actually produced evidence."""

    for action in actions:
        if str(action.get("tool", "")) not in {"find_callers", "find_callees"}:
            continue
        try:
            count = int(str(action.get("result_count", "0")))
        except ValueError:
            count = 0
        if count > 0:
            return True
    return False


def coverage_needs_structural_connection(
    intent: object,
    coverage: Iterable[dict[str, object]],
) -> bool:
    """Require graph evidence for mechanisms and multi-stage locations."""

    normalized_intent = str(intent)
    if normalized_intent == "mechanism":
        return True
    if normalized_intent != "location":
        return False
    aspects = {
        str(item.get("aspect", "")).strip().casefold()
        for item in coverage
        if str(item.get("aspect", "")).strip()
    }
    return len(aspects) > 1


def coverage_integration_probes(
    coverage: Iterable[dict[str, object]],
    *,
    observable_chunk_ids: set[str],
    limit: int = MAX_ACTIONS_PER_ITERATION,
) -> list[dict[str, str]]:
    """Probe distinct covered aspects for their upstream integration points.

    The targets are supplied by the model's coverage ledger but must already be
    observable. Selecting at most one unique target per aspect prevents the
    first local method from monopolizing a multi-stage mechanism probe.
    """

    if limit < 1:
        return []
    actions: list[dict[str, str]] = []
    seen: set[str] = set()
    for coverage_item in coverage:
        raw_ids = coverage_item.get("chunk_ids")
        if not isinstance(raw_ids, list):
            continue
        target = next(
            (
                str(chunk_id)
                for chunk_id in raw_ids
                if str(chunk_id) in observable_chunk_ids
                and str(chunk_id) not in seen
            ),
            "",
        )
        if not target:
            continue
        seen.add(target)
        actions.append({"tool": "find_callers", "chunk_id": target})
        if len(actions) >= limit:
            break
    return actions


def pending_graph_continuations(
    results: Iterable[dict[str, object]],
    previous_actions: Iterable[dict[str, object]],
    *,
    limit: int = 8,
) -> list[dict[str, str]]:
    """Read local context and continue unresolved calls on the final frontier."""

    if limit < 1:
        return []
    prior = {
        (
            str(action.get("tool", "")),
            str(action.get("chunk_id", "")),
        )
        for action in previous_actions
    }
    neighborhood_candidates: list[dict[str, str]] = []
    call_candidates: list[dict[str, str]] = []
    seen_neighborhoods: set[str] = set()
    seen_calls: set[str] = set()
    for result in results:
        chunk_id = str(result.get("chunk_id", "")).strip()
        neighborhood_identity = ("open_neighborhood", chunk_id)
        if (
            chunk_id
            and chunk_id not in seen_neighborhoods
            and neighborhood_identity not in prior
        ):
            seen_neighborhoods.add(chunk_id)
            neighborhood_candidates.append(
                {"tool": "open_neighborhood", "chunk_id": chunk_id}
            )
        source_kind = str(result.get("source_kind", ""))
        if source_kind not in {
            "agent_coverage_anchor",
            "agent_callers_evidence",
            "agent_callees_evidence",
            "agent_terminal_neighborhood_evidence",
            "agent_terminal_callees_evidence",
        }:
            continue
        identity = ("find_callees", chunk_id)
        if not chunk_id or chunk_id in seen_calls or identity in prior:
            continue
        seen_calls.add(chunk_id)
        call_candidates.append({"tool": "find_callees", "chunk_id": chunk_id})

    # Local neighborhoods expose sibling lifecycle methods without requiring a
    # guessed symbol. Sample the whole ordered frontier, including its tail,
    # while retaining bounded room for actual call-edge continuation.
    neighborhood_quota = min(
        len(neighborhood_candidates),
        max(1, (limit + 1) // 2),
    )

    def sample(
        candidates: list[dict[str, str]], quota: int
    ) -> list[dict[str, str]]:
        if quota < 1 or not candidates:
            return []
        sample_size = min(quota, len(candidates))
        positions = {
            round(
                position * (len(candidates) - 1) / max(sample_size - 1, 1)
            )
            for position in range(sample_size)
        }
        return [
            action
            for position, action in enumerate(candidates)
            if position in positions
        ]

    selected = sample(neighborhood_candidates, neighborhood_quota)
    selected.extend(
        sample(call_candidates, max(0, limit - len(selected)))
    )
    return selected[:limit]


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
    query_terms = _structural_search_terms(
        " ".join([question, *(str(hint) for hint in search_hints)])
    )
    observation_terms: list[set[str]] = []
    for observation in observations:
        observation_terms.append(
            _structural_search_terms(
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
        path_title_terms = _structural_search_terms(
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


def merge_required_coverage(
    required_aspects: Iterable[str],
    previous: list[dict[str, object]],
    current: list[dict[str, object]],
    *,
    required_only: bool = False,
) -> list[dict[str, object]]:
    """Preserve the question's bounded coverage contract across model cycles."""

    required: list[str] = []
    required_keys: set[str] = set()
    for raw_aspect in required_aspects:
        aspect = _bounded_text(raw_aspect, maximum=120)
        if aspect is None or aspect.casefold() in required_keys:
            continue
        required.append(aspect)
        required_keys.add(aspect.casefold())
        if len(required) >= 6:
            break

    by_aspect: dict[str, dict[str, object]] = {
        aspect.casefold(): {
            "aspect": aspect,
            "status": "gap",
            "chunk_ids": [],
        }
        for aspect in required
    }
    order = [aspect.casefold() for aspect in required]
    for collection in (previous, current):
        for raw_item in collection:
            if not isinstance(raw_item, dict):
                continue
            aspect = _bounded_text(raw_item.get("aspect"), maximum=120)
            status = str(raw_item.get("status", ""))
            if aspect is None or status not in ALLOWED_COVERAGE:
                continue
            key = aspect.casefold()
            raw_ids = raw_item.get("chunk_ids")
            chunk_ids = (
                list(dict.fromkeys(str(value) for value in raw_ids if value))[:8]
                if isinstance(raw_ids, list)
                else []
            )
            if status == "covered" and not chunk_ids:
                status = "partial"
            if key not in by_aspect and required_only:
                continue
            if key not in by_aspect:
                order.append(key)
            by_aspect[key] = {
                "aspect": by_aspect.get(key, {}).get("aspect", aspect),
                "status": status,
                "chunk_ids": chunk_ids,
            }
            if len(order) >= 10:
                break
    return [by_aspect[key] for key in order[:10] if key in by_aspect]


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
        + ". The ledger is provisional planning metadata, not evidence and not a "
        "verdict about the final source package. Use it to organize a coherent "
        "explanation, but inspect the supplied sources themselves and support every "
        "factual statement with them. Before finishing, walk through every facet "
        "that names one or more source IDs; do not stop after explaining only the "
        "first facet. For a detailed request, give each supported stage its own "
        "paragraph or section with the source IDs that establish that stage. Do not "
        "compress several distinct stages into one large claim carrying only the "
        "first stage's citation. Connect stages only when the sources establish the "
        "connection. A partial facet still permits explaining its supported portion. "
        "A prior gap may be answered when a final source directly supports it; "
        "otherwise leave it unresolved instead of filling it with outside knowledge."
    )
