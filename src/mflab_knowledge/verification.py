from __future__ import annotations

import json
import re
import textwrap
from collections.abc import Callable

from mflab_knowledge.grounding import citation_ids, factual_units

VERIFICATION_ALGORITHM = "claim_evidence_audit_v4"
SUPPORT_DISCOVERY_ALGORITHM = "claim_support_discovery_v1"
INVESTIGATION_ALGORITHM = "bounded_investigation_v19"

ProgressCallback = Callable[[dict[str, object]], None]

_SENTENCE_BOUNDARY = re.compile(
    r"(?<=[.!?])(?:\s+(?P<citation>\[\s*S\d+\s*"
    r"(?:(?:,|;)\s*S\d+\s*)*\]))?"
    r"(?:\s+(?=(?:[`*_>(\[]*[A-ZÀ-ÖØ-Þ0-9]))|(?=$))"
)


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


def _exact_supported_code_blocks(
    answer: str,
    sources: list[dict[str, object]],
    *,
    allowed_source_ids: set[str],
) -> list[str]:
    """Keep generated code only when it is verbatim authorized evidence."""

    source_text = {
        str(source.get("source_id", "")): str(source.get("text", ""))
        for source in sources
        if str(source.get("source_id", "")) in allowed_source_ids
    }
    selected: list[str] = []
    seen: set[tuple[str, str]] = set()
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
        if source_id is None or (source_id, code) in seen:
            continue
        seen.add((source_id, code))
        language = re.sub(r"[^A-Za-z0-9_+.#-]", "", match.group("language"))[:30]
        selected.append(f"```{language}\n{code}\n```\n\n[{source_id}]")
        if len(selected) >= 6:
            break
    return selected


def sanitize_fenced_code_blocks(
    answer: str,
    sources: list[dict[str, object]],
) -> tuple[str, int]:
    """Remove code that is not a complete-line excerpt from cited evidence."""

    source_text = {
        str(source.get("source_id", "")): str(source.get("text", ""))
        for source in sources
        if source.get("source_id")
    }
    removed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal removed
        code = match.group("code")
        nearby = answer[max(0, match.start() - 240) : match.end() + 120]
        cited = citation_ids(nearby)
        if code and any(
            source_id in cited and _line_aligned_code_excerpt(code, text)
            for source_id, text in source_text.items()
        ):
            return match.group(0)
        removed += 1
        return ""

    return _FENCED_CODE_BLOCK.sub(replace, answer), removed


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
        if claim not in selected:
            selected.append(claim)
    if not selected:
        return None
    result = "\n\n".join(selected)
    if answer is not None and sources:
        code_blocks = _exact_supported_code_blocks(
            answer,
            sources,
            allowed_source_ids=allowed_source_ids,
        )
        if code_blocks:
            result += "\n\n### Trechos de código citados\n\n" + "\n\n".join(
                code_blocks
            )
    return result
