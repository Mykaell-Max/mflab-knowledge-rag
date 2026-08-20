from __future__ import annotations

import json
import re
from collections.abc import Callable

from mflab_knowledge.grounding import citation_ids, factual_units

VERIFICATION_ALGORITHM = "claim_evidence_audit_v2"
INVESTIGATION_ALGORITHM = "bounded_investigation_v8"

ProgressCallback = Callable[[dict[str, object]], None]


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
        callback(dict(event))
    return event


def claims_for_verification(answer: str) -> list[dict[str, object]]:
    claims = [
        {
            "claim_id": f"C{position}",
            "text": unit,
            "cited_source_ids": sorted(citation_ids(unit)),
        }
        for position, unit in enumerate(factual_units(answer), start=1)
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


def unavailable_verification(reason: str) -> dict[str, object]:
    return {
        "algorithm": VERIFICATION_ALGORITHM,
        "performed": False,
        "passed": None,
        "reason": reason,
        "claims": [],
        "counts": {"supported": 0, "unsupported": 0, "uncertain": 0},
    }


def supported_claim_subset(verification: dict[str, object]) -> str | None:
    """Return only claim text already approved by the bounded evidence audit."""

    raw_claims = verification.get("claims")
    if not isinstance(raw_claims, list):
        return None
    selected: list[str] = []
    for raw_claim in raw_claims:
        if not isinstance(raw_claim, dict):
            continue
        if raw_claim.get("verdict") != "supported":
            continue
        claim = str(raw_claim.get("claim", "")).strip()
        source_ids = raw_claim.get("source_ids")
        if not claim or not isinstance(source_ids, list) or not source_ids:
            continue
        if claim not in selected:
            selected.append(claim)
    return "\n\n".join(selected) if selected else None
