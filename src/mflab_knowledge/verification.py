from __future__ import annotations

import json
import re
import textwrap
from collections.abc import Callable

from mflab_knowledge.grounding import citation_ids, factual_units

VERIFICATION_ALGORITHM = "claim_evidence_audit_v9"
SUPPORT_DISCOVERY_ALGORITHM = "claim_support_discovery_v1"
INVESTIGATION_ALGORITHM = "bounded_investigation_v30"

ProgressCallback = Callable[[dict[str, object]], None]

_SENTENCE_BOUNDARY = re.compile(
    r"(?<=[.!?])(?:\s+(?P<citation>\[\s*S\d+\s*"
    r"(?:(?:,|;)\s*S\d+\s*)*\]))?"
    r"(?:\s+(?=(?:[`*_>(\[]*[A-ZÀ-ÖØ-Þ0-9]))|(?=$))"
)
_MARKDOWN_HEADING = re.compile(r"(?m)^#{1,6}[ \t]+[^\r\n]+[ \t]*$")
_TRAILING_CITATION = re.compile(
    r"\s*\[\s*S\d+\s*(?:(?:,|;)\s*S\d+\s*)*\]\s*$"
)
_CALLABLE_REFERENCE = re.compile(
    r"(?:\b[A-Za-z_]\w*(?:::|->|\.)?)*\b([A-Za-z_]\w*)\s*\(\s*\)"
)
_BEHAVIOR_ASSERTION = re.compile(
    r"\b(?:calcul|comput|control|gerenc|handle|implement|inicializ|initializ|"
    r"respons[aá]vel|simul|solv|atualiz|updat)\w*\b",
    re.IGNORECASE,
)
_CODE_FORMATS = {
    "c",
    "cpp",
    "cpp_header",
    "fortran",
    "python",
}

_OPERATION_FAMILIES = {
    "configure": ("config",),
    "initialize": ("initial", "inicial", "setup"),
    "create": ("creat", "cria", "add", "adicion", "alloc", "aloc"),
    "advance": ("advanc", "avan", "move", "mov", "step", "passo"),
    "cleanup": ("cleanup", "clean", "limp", "remove", "remov"),
    "count": ("count", "contag", "contador"),
    "transfer": ("transfer", "migr", "send", "envi"),
    "output": ("output", "write", "save", "grav", "saída"),
    "load": ("load", "read", "carreg", "leit"),
    "solve": ("solv", "resolv", "calcul", "comput"),
    "update": ("updat", "atualiz"),
}


def _operation_families(value: object) -> set[str]:
    normalized = "".join(
        character.casefold() if character.isalnum() else " "
        for character in str(value)
    )
    terms = normalized.split()
    return {
        family
        for family, prefixes in _OPERATION_FAMILIES.items()
        if any(term.startswith(prefix) for term in terms for prefix in prefixes)
    }


def _atomic_factual_units(unit: str) -> list[str]:
    """Split prose into independently auditable sentence-sized claims.

    A citation at the end of a generated paragraph applies to that complete
    paragraph under the answer contract.  Preserve that intent by attaching
    the final citation group to preceding uncited sentences, but do not merge
    distinct citations that the model placed on different sentences.
    """

    def boundary(match: re.Match[str]) -> str:
        citation = match.group("citation")
        return (f" {citation}" if citation else "") + "\n"

    parts = [
        value.strip()
        for value in _SENTENCE_BOUNDARY.sub(boundary, unit).splitlines()
        if value.strip()
    ]
    if len(parts) <= 1:
        return parts or [unit.strip()]
    cited_positions = [
        position for position, part in enumerate(parts) if citation_ids(part)
    ]
    inherited_ids = (
        sorted(citation_ids(parts[-1]))
        if cited_positions == [len(parts) - 1]
        else []
    )
    if not inherited_ids:
        return parts
    suffix = "[" + ", ".join(inherited_ids) + "]"
    return [
        part if citation_ids(part) else f"{part} {suffix}"
        for part in parts
    ]


def emit_progress(
    callback: ProgressCallback | None,
    *,
    stage: str,
    title: str,
    detail: str | None = None,
    data: dict[str, object] | None = None,
) -> dict[str, object]:
    """Create and optionally publish a safe, user-facing investigation event."""

    event: dict[str, object] = {"stage": stage, "title": title}
    if detail:
        event["detail"] = detail
    if data:
        event["data"] = data
    if callback is not None:
        try:
            callback(dict(event))
        except Exception:
            # Progress is observational. A disconnected client or failed state
            # sink must never change the evidence or the answer.
            pass
    return event


def claims_for_verification(answer: str) -> list[dict[str, object]]:
    atomic_units = [
        claim
        for unit in factual_units(answer)
        for claim in _atomic_factual_units(unit)
    ]
    claims = [
        {
            "claim_id": f"C{position}",
            "text": unit,
            "cited_source_ids": sorted(citation_ids(unit)),
        }
        for position, unit in enumerate(atomic_units, start=1)
    ]
    if not claims and answer.strip():
        claims.append(
            {
                "claim_id": "C1",
                "text": answer.strip(),
                "cited_source_ids": sorted(citation_ids(answer)),
            }
        )
    return claims


def _json_object(value: str) -> dict[str, object]:
    candidate = value.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        parsed = None
        for match in re.finditer(r"\{", candidate):
            try:
                possible, _end = decoder.raw_decode(candidate, match.start())
            except json.JSONDecodeError:
                continue
            if isinstance(possible, dict) and "claims" in possible:
                parsed = possible
                break
    if not isinstance(parsed, dict):
        raise ValueError("auditoria de evidência não retornou um objeto JSON")
    return parsed


def normalize_verification(
    raw: str | dict[str, object],
    *,
    claims: list[dict[str, object]],
    valid_source_ids: set[str],
) -> dict[str, object]:
    """Validate the model audit instead of trusting its JSON at face value."""

    value = _json_object(raw) if isinstance(raw, str) else raw
    raw_findings = value.get("claims")
    if not isinstance(raw_findings, list):
        raise ValueError("auditoria de evidência não contém claims")

    expected = {str(claim["claim_id"]): claim for claim in claims}
    findings_by_id: dict[str, dict[str, object]] = {}
    allowed = {"supported", "unsupported", "uncertain"}
    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        claim_id = str(item.get("claim_id", ""))
        verdict = str(item.get("verdict", ""))
        if claim_id not in expected or claim_id in findings_by_id or verdict not in allowed:
            continue
        raw_ids = item.get("source_ids")
        claim_source_ids = {
            str(source_id)
            for source_id in expected[claim_id].get("cited_source_ids", [])
        }
        source_ids = (
            sorted(
                {
                    str(source_id)
                    for source_id in raw_ids
                    if str(source_id) in valid_source_ids
                    and str(source_id) in claim_source_ids
                }
            )
            if isinstance(raw_ids, list)
            else []
        )
        finding = str(item.get("finding", "")).strip()[:500]
        if verdict == "supported" and not source_ids:
            verdict = "uncertain"
            finding = "A auditoria não ligou a afirmação a uma fonte citada válida."
        findings_by_id[claim_id] = {
            "claim_id": claim_id,
            "claim": str(expected[claim_id]["text"]),
            "verdict": verdict,
            "source_ids": source_ids,
            "finding": finding,
        }

    findings: list[dict[str, object]] = []
    for claim_id, claim in expected.items():
        finding = findings_by_id.get(claim_id)
        if finding is None:
            finding = {
                "claim_id": claim_id,
                "claim": str(claim["text"]),
                "verdict": "uncertain",
                "source_ids": [],
                "finding": "A auditoria não avaliou esta afirmação.",
            }
        findings.append(finding)

    counts = {
        verdict: sum(item["verdict"] == verdict for item in findings)
        for verdict in sorted(allowed)
    }
    passed = bool(findings) and counts["unsupported"] == 0 and counts["uncertain"] == 0
    return {
        "algorithm": VERIFICATION_ALGORITHM,
        "performed": True,
        "passed": passed,
        "claims": findings,
        "counts": counts,
    }


def _identifier_signature(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", value).casefold()


def _claim_callable_owner(claim: str, name: str) -> str:
    escaped = re.escape(name)
    explicit = re.search(
        rf"\b([A-Za-z_]\w*(?:::\w+)*)\s*(?:::|->|\.)\s*{escaped}\s*\(",
        claim,
    )
    if explicit:
        return explicit.group(1).rsplit("::", 1)[-1]
    possessive = re.search(
        rf"\b{escaped}\s*\(\s*\)\s+"
        rf"(?:do|da|de|of)\s+(?:the\s+)?([A-Za-z_]\w*)",
        claim,
        re.IGNORECASE,
    )
    return possessive.group(1) if possessive else ""


def _source_defines_callable(
    source: dict[str, object],
    name: str,
    *,
    owner: str = "",
) -> bool:
    title = str(source.get("title", ""))
    title_match = re.search(
        rf"(?:(?P<owner>[A-Za-z_]\w*)::)?{re.escape(name)}$",
        title.strip(),
    )
    expected_owner = _identifier_signature(owner)
    if title_match and (
        not expected_owner
        or _identifier_signature(title_match.group("owner") or "")
        == expected_owner
    ):
        return True
    text = str(source.get("text", ""))
    escaped = re.escape(name)
    if expected_owner:
        qualified_definition = re.search(
            rf"(?ms)^\s*(?!if\b|for\b|while\b|switch\b|return\b)"
            rf"(?:[A-Za-z_]\w*(?:::\w+)*(?:\s*[*&]+)?\s+)+"
            rf"(?P<owner>(?:[A-Za-z_]\w*::)*[A-Za-z_]\w*)::"
            rf"{escaped}\s*\([^;{{}}]*\)"
            rf"\s*(?:const\s*)?(?:noexcept\s*)?\{{",
            text,
        )
        if qualified_definition:
            actual_owner = qualified_definition.group("owner").rsplit("::", 1)[-1]
            return _identifier_signature(actual_owner) == expected_owner
        return False
    definitions = (
        rf"(?mi)^\s*(?:def|function|subroutine)\s+{escaped}\s*\(",
        rf"(?ms)^\s*(?!if\b|for\b|while\b|switch\b|return\b)"
        rf"(?:[A-Za-z_]\w*(?:::\w+)*(?:\s*[*&]+)?\s+)+"
        rf"(?:(?:[A-Za-z_]\w*)::)*{escaped}\s*\([^;{{}}]*\)"
        rf"\s*(?:const\s*)?(?:noexcept\s*)?\{{",
    )
    return any(re.search(pattern, text) for pattern in definitions)


def downgrade_callsite_only_claims(
    verification: dict[str, object],
    *,
    sources: list[dict[str, object]],
) -> dict[str, object]:
    """Reject behavioral descriptions supported only by a function call site.

    The semantic auditor remains authoritative for prose and scientific
    meaning. This narrow deterministic guard handles one structural invariant:
    seeing ``operation()`` called does not reveal what ``operation`` implements.
    A code definition or a source whose symbol title names that callable is
    required before a behavioral claim can remain supported.
    """

    raw_findings = verification.get("claims")
    if not isinstance(raw_findings, list):
        return verification
    source_by_id = {
        str(source.get("source_id", "")): source
        for source in sources
        if str(source.get("source_id", ""))
    }
    findings: list[dict[str, object]] = []
    for raw_finding in raw_findings:
        if not isinstance(raw_finding, dict):
            continue
        finding = dict(raw_finding)
        claim = str(finding.get("claim", ""))
        if (
            finding.get("verdict") != "supported"
            or not _BEHAVIOR_ASSERTION.search(claim)
        ):
            findings.append(finding)
            continue
        callable_names = list(dict.fromkeys(_CALLABLE_REFERENCE.findall(claim)))
        cited_sources = [
            source_by_id[str(source_id)]
            for source_id in finding.get("source_ids", [])
            if str(source_id) in source_by_id
            and str(source_by_id[str(source_id)].get("format", ""))
            in _CODE_FORMATS
        ]
        unsupported_callable = next(
            (
                name
                for name in callable_names
                if any(
                    re.search(
                        rf"\b{re.escape(name)}\s*\(",
                        str(source.get("text", "")),
                    )
                    for source in cited_sources
                )
                and not any(
                    _source_defines_callable(
                        source,
                        name,
                        owner=_claim_callable_owner(claim, name),
                    )
                    for source in cited_sources
                )
            ),
            None,
        )
        if unsupported_callable is not None:
            finding["verdict"] = "uncertain"
            finding["finding"] = (
                f"A fonte citada mostra apenas a chamada de {unsupported_callable}(), "
                "não sua implementação."
            )
        findings.append(finding)
    counts = {
        verdict: sum(item.get("verdict") == verdict for item in findings)
        for verdict in ("supported", "unsupported", "uncertain")
    }
    return {
        **verification,
        "algorithm": VERIFICATION_ALGORITHM,
        "claims": findings,
        "counts": counts,
        "passed": bool(findings)
        and counts["unsupported"] == 0
        and counts["uncertain"] == 0,
    }


def downgrade_unanchored_subject_claims(
    verification: dict[str, object],
    *,
    sources: list[dict[str, object]],
    subject_identifiers: list[str],
    related_chunk_ids: list[str] | None = None,
) -> dict[str, object]:
    """Require a cited source to mention subjects asserted by a claim.

    Planner identifiers are used only as conservative lexical guards. They do
    not establish a fact. In an answer scoped to a named subject, each supported
    claim must cite either a source that visibly contains that subject or a
    definition reached directly from an anchored structural lineage. This
    blocks a locally correct neighboring function from becoming an unrelated
    subsystem stage merely because it was reachable in the broader graph.
    """

    raw_findings = verification.get("claims")
    if not isinstance(raw_findings, list):
        return verification
    identifiers: list[tuple[str, str]] = []
    for value in subject_identifiers:
        label = " ".join(str(value).split()).strip()
        signature = _identifier_signature(label)
        if len(signature) >= 3 and signature not in {
            item[1] for item in identifiers
        }:
            identifiers.append((label, signature))
    if not identifiers:
        return verification
    source_by_id = {
        str(source.get("source_id", "")): source
        for source in sources
        if str(source.get("source_id", ""))
    }
    related = {str(value) for value in related_chunk_ids or [] if str(value)}
    findings: list[dict[str, object]] = []
    for raw_finding in raw_findings:
        if not isinstance(raw_finding, dict):
            continue
        finding = dict(raw_finding)
        if finding.get("verdict") != "supported":
            findings.append(finding)
            continue
        cited_sources = [
            source_by_id[str(source_id)]
            for source_id in finding.get("source_ids", [])
            if str(source_id) in source_by_id
        ]
        cited_signatures = [
            _identifier_signature(
                " ".join(
                    str(source.get(field, ""))
                    for field in ("project", "path", "title", "text")
                )
            )
            for source in cited_sources
        ]
        visibly_anchored = any(
            signature in source_signature
            for _label, signature in identifiers
            for source_signature in cited_signatures
        )
        structurally_anchored = any(
            str(source.get("chunk_id", "")) in related
            for source in cited_sources
        )
        if not visibly_anchored and not structurally_anchored:
            finding["verdict"] = "uncertain"
            finding["finding"] = (
                "As fontes citadas não vinculam esta afirmação ao assunto "
                + ", ".join(label for label, _signature in identifiers)
                + "."
            )
        findings.append(finding)
    counts = {
        verdict: sum(item.get("verdict") == verdict for item in findings)
        for verdict in ("supported", "unsupported", "uncertain")
    }
    return {
        **verification,
        "algorithm": VERIFICATION_ALGORITHM,
        "claims": findings,
        "counts": counts,
        "passed": bool(findings)
        and counts["unsupported"] == 0
        and counts["uncertain"] == 0,
    }


def downgrade_operation_mismatch_claims(
    verification: dict[str, object],
    *,
    sources: list[dict[str, object]],
) -> dict[str, object]:
    """Reject lifecycle claims supported only by a differently scoped operation.

    A named subsystem appearing in both claim and source is not sufficient when
    the claim asserts configuration, creation, movement, cleanup, or another
    concrete software operation. This language-agnostic-ish lexical guard uses
    generic programming verbs only; it contains no repository, branch, symbol,
    or scientific vocabulary. The semantic verifier remains authoritative for
    claims that do not expose a recognizable operation.
    """

    raw_findings = verification.get("claims")
    if not isinstance(raw_findings, list):
        return verification
    source_by_id = {
        str(source.get("source_id", "")): source
        for source in sources
        if str(source.get("source_id", ""))
    }
    findings: list[dict[str, object]] = []
    for raw_finding in raw_findings:
        if not isinstance(raw_finding, dict):
            continue
        finding = dict(raw_finding)
        if finding.get("verdict") != "supported":
            findings.append(finding)
            continue
        claim_families = _operation_families(finding.get("claim", ""))
        if not claim_families:
            findings.append(finding)
            continue
        cited_text = " ".join(
            " ".join(
                str(source.get(field, ""))
                for field in ("path", "title", "text")
            )
            for source_id in finding.get("source_ids", [])
            if (source := source_by_id.get(str(source_id))) is not None
        )
        source_families = _operation_families(cited_text)
        if not (claim_families & source_families):
            finding["verdict"] = "uncertain"
            finding["finding"] = (
                "A afirmação descreve uma operação de software diferente "
                "das operações visíveis nas fontes citadas."
            )
        findings.append(finding)
    counts = {
        verdict: sum(item.get("verdict") == verdict for item in findings)
        for verdict in ("supported", "unsupported", "uncertain")
    }
    return {
        **verification,
        "algorithm": VERIFICATION_ALGORITHM,
        "claims": findings,
        "counts": counts,
        "passed": bool(findings)
        and counts["unsupported"] == 0
        and counts["uncertain"] == 0,
    }


def select_query_subject_identifiers(
    question: str,
    candidates: list[str],
    *,
    excluded_labels: list[str] | None = None,
) -> list[str]:
    """Select named entities from planner vocabulary without domain lists.

    The planner may mix entity names with generic retrieval words. Only values
    visibly present in the question and shaped like acronyms, code identifiers,
    qualified names, hyphenated names, numbers, or multiword proper names are
    retained. Scope labels are excluded separately because repository and
    branch names select provenance but do not define the scientific subject.
    """

    question_signature = _identifier_signature(question)
    excluded = {
        _identifier_signature(value)
        for value in excluded_labels or []
        if _identifier_signature(value)
    }
    selected: list[str] = []
    selected_signatures: set[str] = set()

    def append(value: str) -> None:
        label = " ".join(value.split()).strip()
        signature = _identifier_signature(label)
        if (
            len(signature) < 3
            or signature in excluded
            or signature in selected_signatures
            or signature not in question_signature
        ):
            return
        selected.append(label)
        selected_signatures.add(signature)

    for raw_value in candidates:
        label = " ".join(str(raw_value).split()).strip()
        if not label or _identifier_signature(label) in excluded:
            continue
        letters = "".join(character for character in label if character.isalpha())
        acronym = len(letters) >= 2 and letters == letters.upper()
        qualified = any(marker in label for marker in ("::", "->", ".", "/", "_"))
        hyphenated = "-" in label
        numbered = any(character.isdigit() for character in label)
        camel_case = bool(re.search(r"[a-zà-öø-ÿ][A-ZÀ-ÖØ-Þ]", label))
        words = re.findall(r"[^\W\d_]+", label, flags=re.UNICODE)
        proper_phrase = (
            len(words) >= 2
            and sum(word[:1].isupper() for word in words) >= 2
        )
        if acronym or qualified or hyphenated or numbered or camel_case or proper_phrase:
            append(label)

        # A planner can return an entity together with an adjacent retrieval
        # word, for example an acronym followed by "initialization". Preserve
        # only the syntactically distinctive token in that case.
        for token in re.findall(r"[\w]+", label, flags=re.UNICODE):
            token_letters = "".join(
                character for character in token if character.isalpha()
            )
            token_acronym = (
                len(token_letters) >= 2 and token_letters == token_letters.upper()
            )
            token_camel = bool(
                re.search(r"[a-zà-öø-ÿ][A-ZÀ-ÖØ-Þ]", token)
            )
            if token_acronym or token_camel or any(char.isdigit() for char in token):
                append(token)
    return selected


def normalize_support_discovery(
    raw: str | dict[str, object],
    *,
    claims: list[dict[str, object]],
    valid_source_ids: set[str],
) -> dict[str, object]:
    """Validate proposed support for uncited claims before citations are added."""

    value = _json_object(raw) if isinstance(raw, str) else raw
    raw_findings = value.get("claims")
    if not isinstance(raw_findings, list):
        raise ValueError("descoberta de suporte não contém claims")
    expected = {str(claim["claim_id"]): claim for claim in claims}
    allowed = {"supported", "unsupported", "uncertain"}
    findings_by_id: dict[str, dict[str, object]] = {}
    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        claim_id = str(item.get("claim_id", ""))
        verdict = str(item.get("verdict", ""))
        if claim_id not in expected or claim_id in findings_by_id or verdict not in allowed:
            continue
        raw_ids = item.get("source_ids")
        source_ids = (
            sorted(
                {
                    str(source_id)
                    for source_id in raw_ids
                    if str(source_id) in valid_source_ids
                }
            )
            if isinstance(raw_ids, list)
            else []
        )
        finding = str(item.get("finding", "")).strip()[:500]
        if verdict == "supported" and not source_ids:
            verdict = "uncertain"
            finding = "Nenhuma fonte válida foi associada à afirmação."
        findings_by_id[claim_id] = {
            "claim_id": claim_id,
            "claim": str(expected[claim_id]["text"]),
            "verdict": verdict,
            "source_ids": source_ids,
            "finding": finding,
        }
    findings: list[dict[str, object]] = []
    for claim_id, claim in expected.items():
        findings.append(
            findings_by_id.get(
                claim_id,
                {
                    "claim_id": claim_id,
                    "claim": str(claim["text"]),
                    "verdict": "uncertain",
                    "source_ids": [],
                    "finding": "A descoberta não avaliou esta afirmação.",
                },
            )
        )
    return {
        "algorithm": SUPPORT_DISCOVERY_ALGORITHM,
        "claims": findings,
        "counts": {
            verdict: sum(item["verdict"] == verdict for item in findings)
            for verdict in sorted(allowed)
        },
    }


def attach_discovered_citations(
    answer: str,
    discovery: dict[str, object],
) -> tuple[str, int]:
    """Append only validated source IDs to exact uncited factual units."""

    raw_claims = discovery.get("claims")
    if not isinstance(raw_claims, list):
        return answer, 0
    result = answer
    attached = 0
    for raw_claim in raw_claims:
        if not isinstance(raw_claim, dict) or raw_claim.get("verdict") != "supported":
            continue
        claim = str(raw_claim.get("claim", "")).strip()
        source_ids = raw_claim.get("source_ids")
        if (
            not claim
            or citation_ids(claim)
            or not isinstance(source_ids, list)
            or not source_ids
            or claim not in result
        ):
            continue
        citation = "[" + ", ".join(str(value) for value in source_ids) + "]"
        result = result.replace(claim, f"{claim} {citation}", 1)
        attached += 1
    return result, attached


def unavailable_verification(reason: str) -> dict[str, object]:
    return {
        "algorithm": VERIFICATION_ALGORITHM,
        "performed": False,
        "passed": None,
        "reason": reason,
        "claims": [],
        "counts": {"supported": 0, "unsupported": 0, "uncertain": 0},
    }


_FENCED_CODE_BLOCK = re.compile(
    r"```(?P<language>[^\n`]*)\n(?P<code>.*?)```",
    re.DOTALL,
)


def _line_aligned_code_excerpt(code: str, source_text: str) -> bool:
    """Accept only complete contiguous source lines, allowing common dedent."""

    candidate = textwrap.dedent(code.replace("\r\n", "\n")).strip("\n")
    if not candidate:
        return False
    candidate_lines = [line.rstrip() for line in candidate.splitlines()]
    source_lines = source_text.replace("\r\n", "\n").splitlines()
    width = len(candidate_lines)
    for start in range(0, len(source_lines) - width + 1):
        window = textwrap.dedent(
            "\n".join(source_lines[start : start + width])
        ).strip("\n")
        if [line.rstrip() for line in window.splitlines()] == candidate_lines:
            return True
    return False


def _claim_position(answer: str, claim: str, *, start: int = 0) -> int:
    """Locate an audited unit even when paragraph citation inheritance added IDs."""

    position = answer.find(claim, start)
    if position >= 0:
        return position
    uncited = _TRAILING_CITATION.sub("", claim).rstrip()
    return answer.find(uncited, start) if uncited else -1


def _ordered_supported_answer(
    answer: str,
    *,
    selected: list[str],
    sources: list[dict[str, object]],
    allowed_source_ids: set[str],
) -> str:
    """Preserve narrative and inline-code order while dropping rejected prose.

    The function introduces no prose. Supported sentence-sized claims are grouped
    back into their original paragraphs. Exact authorized code fences stay at
    their original position instead of being moved into a generated appendix.
    """

    events: list[tuple[int, int, str]] = []
    paragraph_claims: dict[tuple[int, int], list[tuple[int, str]]] = {}
    search_starts: dict[str, int] = {}
    for claim in selected:
        start = search_starts.get(claim, 0)
        position = _claim_position(answer, claim, start=start)
        if position < 0:
            position = len(answer) + len(events)
            paragraph = (position, position)
        else:
            search_starts[claim] = position + 1
            paragraph_start = answer.rfind("\n\n", 0, position) + 2
            paragraph_end = answer.find("\n\n", position)
            if paragraph_end < 0:
                paragraph_end = len(answer)
            paragraph = (paragraph_start, paragraph_end)
        paragraph_claims.setdefault(paragraph, []).append((position, claim))

    for (start, _end), claims in paragraph_claims.items():
        ordered = [claim for _position, claim in sorted(claims)]
        events.append((start, 2, " ".join(dict.fromkeys(ordered))))

    source_text = {
        str(source.get("source_id", "")): str(source.get("text", ""))
        for source in sources
        if str(source.get("source_id", "")) in allowed_source_ids
    }
    seen_code: set[tuple[str, str]] = set()
    for match in _FENCED_CODE_BLOCK.finditer(answer):
        code = textwrap.dedent(
            match.group("code").replace("\r\n", "\n")
        ).strip("\n")
        if not code or len(code) > 8_000:
            continue
        source_id = next(
            (
                candidate
                for candidate, text in source_text.items()
                if _line_aligned_code_excerpt(code, text)
            ),
            None,
        )
        if source_id is None or (source_id, code) in seen_code:
            continue
        seen_code.add((source_id, code))
        language = re.sub(
            r"[^A-Za-z0-9_+.#-]", "", match.group("language")
        )[:30]
        events.append(
            (
                match.start(),
                1,
                f"```{language}\n{code}\n```\n\n[{source_id}]",
            )
        )

    if not events:
        return "\n\n".join(selected)

    headings = list(_MARKDOWN_HEADING.finditer(answer))
    content_positions = [position for position, _kind, _text in events]
    for index, heading in enumerate(headings):
        boundary = headings[index + 1].start() if index + 1 < len(headings) else len(answer)
        if any(heading.end() <= position < boundary for position in content_positions):
            events.append((heading.start(), 0, heading.group(0).strip()))

    events.sort(key=lambda item: (item[0], item[1]))
    output: list[str] = []
    for _position, _kind, text in events:
        if text and text not in output:
            output.append(text)
    return "\n\n".join(output)


def sanitize_fenced_code_blocks(
    answer: str,
    sources: list[dict[str, object]],
) -> tuple[str, int, int]:
    """Remove code that is not a complete-line excerpt from cited evidence."""

    source_text = {
        str(source.get("source_id", "")): str(source.get("text", ""))
        for source in sources
        if source.get("source_id")
    }
    removed = 0
    citations_attached = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal removed, citations_attached
        code = match.group("code")
        nearby = answer[max(0, match.start() - 240) : match.end() + 120]
        cited = citation_ids(nearby)
        matching_ids = [
            source_id
            for source_id, text in source_text.items()
            if code and _line_aligned_code_excerpt(code, text)
        ]
        if any(source_id in cited for source_id in matching_ids):
            return match.group(0)
        # Exact code is itself deterministic evidence discovery. Attach a
        # source only when the visible excerpt identifies one unambiguous scope;
        # common one-liners duplicated across branches or files still require
        # an explicit nearby citation from the model.
        if len(matching_ids) == 1:
            citations_attached += 1
            return match.group(0) + f"\n\n[{matching_ids[0]}]"
        removed += 1
        return ""

    return _FENCED_CODE_BLOCK.sub(replace, answer), removed, citations_attached


def supported_claim_subset(
    verification: dict[str, object],
    *,
    answer: str | None = None,
    sources: list[dict[str, object]] | None = None,
) -> str | None:
    """Return only prose and verbatim code approved by the bounded audit."""

    raw_claims = verification.get("claims")
    if not isinstance(raw_claims, list):
        return None
    selected: list[str] = []
    allowed_source_ids: set[str] = set()
    for raw_claim in raw_claims:
        if not isinstance(raw_claim, dict):
            continue
        if raw_claim.get("verdict") != "supported":
            continue
        claim = str(raw_claim.get("claim", "")).strip()
        source_ids = raw_claim.get("source_ids")
        if not claim or not isinstance(source_ids, list) or not source_ids:
            continue
        allowed_source_ids.update(str(value) for value in source_ids)
        selected_claim = claim
        if not citation_ids(claim):
            selected_claim = (
                claim
                + " ["
                + ", ".join(str(value) for value in source_ids)
                + "]"
            )
        if selected_claim not in selected:
            selected.append(selected_claim)
    if not selected:
        return None
    if answer is not None and sources:
        return _ordered_supported_answer(
            answer,
            selected=selected,
            sources=sources,
            allowed_source_ids=allowed_source_ids,
        )
    return "\n\n".join(selected)
