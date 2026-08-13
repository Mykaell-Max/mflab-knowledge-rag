from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

from mflab_knowledge.normalize import search_chunks

LogCallback = Callable[[str, str], None]
EVALUATION_SCHEMA_VERSION = "0.1"


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _load_suite(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"suíte de avaliação inválida: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("a suíte de avaliação deve ser um objeto JSON")
    if value.get("schema_version") != EVALUATION_SCHEMA_VERSION:
        raise ValueError("versão incompatível da suíte de avaliação")
    if not isinstance(value.get("cases"), list) or not value["cases"]:
        raise ValueError("a suíte de avaliação não contém cases")
    return value


def _expectation_rank(
    results: list[dict[str, object]],
    expectation: dict[str, object],
) -> int | None:
    expected_path = expectation.get("path")
    path_prefix = expectation.get("path_prefix")
    title_contains = expectation.get("title_contains")
    if expected_path is None and path_prefix is None:
        raise ValueError("expectativa exige path ou path_prefix")
    if expected_path is not None and not isinstance(expected_path, str):
        raise ValueError("path de expectativa deve ser texto")
    if path_prefix is not None and not isinstance(path_prefix, str):
        raise ValueError("path_prefix de expectativa deve ser texto")
    if title_contains is not None and not isinstance(title_contains, str):
        raise ValueError("title_contains de expectativa deve ser texto")

    for rank, result in enumerate(results, start=1):
        result_path = str(result.get("path", ""))
        if expected_path is not None and result_path != expected_path:
            continue
        if path_prefix is not None and not result_path.startswith(path_prefix):
            continue
        if title_contains is not None and title_contains.casefold() not in str(
            result.get("title", "")
        ).casefold():
            continue
        return rank
    return None


def evaluate_suite(
    *,
    suite_path: Path,
    chunks_path: Path,
    output: Path | None = None,
    log: LogCallback | None = None,
) -> dict[str, object]:
    logger = log or (lambda _message, _level="info": None)
    suite_file = suite_path.expanduser().resolve()
    suite = _load_suite(suite_file)
    cases = suite["cases"]
    assert isinstance(cases, list)
    evaluated: list[dict[str, object]] = []
    reciprocal_ranks: list[float] = []
    expectations_total = 0
    expectations_met = 0

    for position, raw_case in enumerate(cases, start=1):
        if not isinstance(raw_case, dict):
            raise ValueError(f"caso {position} inválido")
        case_id = raw_case.get("id")
        query = raw_case.get("query")
        expectations = raw_case.get("expectations")
        if not isinstance(case_id, str) or not isinstance(query, str):
            raise ValueError(f"caso {position} exige id e query")
        if not isinstance(expectations, list) or not expectations:
            raise ValueError(f"caso {case_id} exige expectations")
        limit = int(raw_case.get("limit", 10))
        if limit < 1:
            raise ValueError(f"caso {case_id} exige limit maior que zero")
        raw_allowed_access = raw_case.get("allowed_access", ["public", "lab"])
        if not isinstance(raw_allowed_access, list) or not all(
            isinstance(value, str) for value in raw_allowed_access
        ):
            raise ValueError(f"allowed_access inválido no caso {case_id}")
        allowed_access = set(raw_allowed_access)
        results = search_chunks(
            chunks_path=chunks_path,
            query=query,
            limit=limit,
            branch=(str(raw_case["branch"]) if "branch" in raw_case else None),
            project=(str(raw_case["project"]) if "project" in raw_case else None),
            path_prefix=(
                str(raw_case["path_prefix"])
                if "path_prefix" in raw_case
                else None
            ),
            allowed_access=allowed_access,
            max_per_path=int(raw_case.get("max_per_path", 2)),
        )
        checked_expectations: list[dict[str, object]] = []
        case_passed = True
        first_rank: int | None = None
        for raw_expectation in expectations:
            if not isinstance(raw_expectation, dict):
                raise ValueError(f"expectativa inválida no caso {case_id}")
            rank = _expectation_rank(results, raw_expectation)
            within_rank = int(raw_expectation.get("within_rank", limit))
            passed = rank is not None and rank <= within_rank
            case_passed = case_passed and passed
            expectations_total += 1
            expectations_met += int(passed)
            if rank is not None and (first_rank is None or rank < first_rank):
                first_rank = rank
            checked_expectations.append(
                {**raw_expectation, "rank": rank, "passed": passed}
            )
        reciprocal_rank = 0.0 if first_rank is None else 1.0 / first_rank
        reciprocal_ranks.append(reciprocal_rank)
        evaluated.append(
            {
                "id": case_id,
                "query": query,
                "passed": case_passed,
                "results": len(results),
                "reciprocal_rank": reciprocal_rank,
                "expectations": checked_expectations,
                "top_citations": [result.get("citation") for result in results[:3]],
            }
        )
        logger(
            f"[{position}/{len(cases)}] {case_id}: "
            f"{'PASSOU' if case_passed else 'FALHOU'}",
            "success" if case_passed else "error",
        )

    cases_passed = sum(bool(case["passed"]) for case in evaluated)
    report: dict[str, object] = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "suite": suite.get("name", suite_file.stem),
        "suite_file": str(suite_file),
        "suite_hash": _file_hash(suite_file),
        "chunks_file": str(chunks_path.expanduser().resolve()),
        "chunks_hash": _file_hash(chunks_path.expanduser().resolve()),
        "summary": {
            "cases": len(evaluated),
            "cases_passed": cases_passed,
            "cases_failed": len(evaluated) - cases_passed,
            "pass_rate": cases_passed / len(evaluated) if evaluated else 0.0,
            "expectations": expectations_total,
            "expectations_met": expectations_met,
            "expectation_recall": (
                expectations_met / expectations_total if expectations_total else 0.0
            ),
            "mean_reciprocal_rank": (
                sum(reciprocal_ranks) / len(reciprocal_ranks)
                if reciprocal_ranks
                else 0.0
            ),
        },
        "cases": evaluated,
    }
    if output is not None:
        destination = output.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return report
