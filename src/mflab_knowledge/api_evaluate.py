from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

LogCallback = Callable[[str, str], None]
RequestCallback = Callable[[dict[str, object], int], dict[str, object]]
MetricSampler = Callable[[], dict[str, object] | None]

ANSWER_EVALUATION_SCHEMA_VERSION = "0.1"
MAX_API_RESPONSE_BYTES = 8 * 1024 * 1024

CASE_FIELDS = {
    "id",
    "query",
    "mode",
    "limit",
    "branch",
    "project",
    "path_prefix",
    "allowed_access",
    "max_per_path",
    "include_duplicate_content",
    "max_context_characters",
    "max_output_tokens",
    "temperature",
    "expectations",
}
EXPECTATION_FIELDS = {
    "abstained",
    "grounding_status",
    "min_valid_citations",
    "max_invalid_citations",
    "min_sources",
    "min_citation_coverage",
    "min_scope_citation_coverage",
    "allowed_finish_reasons",
    "max_client_duration_seconds",
    "max_generation_duration_seconds",
    "scope_warning",
    "exploration_intent",
    "required_source_projects",
    "required_source_paths",
    "forbidden_answer_phrases",
}


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


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
        raise ValueError(f"suíte de respostas inválida: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("a suíte de respostas deve ser um objeto JSON")
    unknown = set(value) - {"schema_version", "name", "cases"}
    if unknown:
        raise ValueError(
            "opções desconhecidas na suíte de respostas: "
            + ", ".join(sorted(unknown))
        )
    if value.get("schema_version") != ANSWER_EVALUATION_SCHEMA_VERSION:
        raise ValueError("versão incompatível da suíte de respostas")
    if not isinstance(value.get("cases"), list) or not value["cases"]:
        raise ValueError("a suíte de respostas não contém cases")
    return value


def _validate_loopback_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("api_base_url deve usar http:// ou https://")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "api_base_url não aceita credenciais, query ou fragmento"
        )
    if parsed.hostname not in {"127.0.0.1", "::1"}:
        raise ValueError("api_base_url deve apontar diretamente para loopback")
    if not parsed.port:
        raise ValueError("api_base_url exige porta explícita")
    if parsed.path not in {"", "/"}:
        raise ValueError("api_base_url não aceita caminho")
    return value.strip().rstrip("/")


def _http_requester(base_url: str) -> RequestCallback:
    endpoint = f"{_validate_loopback_base_url(base_url)}/ask"
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirects(),
    ).open

    def request(payload: dict[str, object], timeout: int) -> dict[str, object]:
        http_request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            response = opener(http_request, timeout=timeout)
            with response:
                raw = response.read(MAX_API_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"API RAG respondeu HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("API RAG local indisponível") from exc
        if len(raw) > MAX_API_RESPONSE_BYTES:
            raise RuntimeError("resposta da API RAG excedeu 8 MiB")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("API RAG retornou JSON inválido") from exc
        if not isinstance(value, dict):
            raise RuntimeError("API RAG retornou resposta inválida")
        return value

    return request


def sample_nvidia_gpu() -> dict[str, object] | None:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [
                executable,
                "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    gpus: list[dict[str, object]] = []
    for row in csv.reader(completed.stdout.splitlines()):
        if len(row) != 5:
            continue
        try:
            gpus.append(
                {
                    "index": int(row[0].strip()),
                    "name": row[1].strip(),
                    "memory_used_mib": float(row[2].strip()),
                    "memory_total_mib": float(row[3].strip()),
                    "utilization_percent": float(row[4].strip()),
                }
            )
        except ValueError:
            continue
    if not gpus:
        return None
    return {
        "gpus": gpus,
        "memory_used_mib": sum(float(gpu["memory_used_mib"]) for gpu in gpus),
        "memory_total_mib": sum(
            float(gpu["memory_total_mib"]) for gpu in gpus
        ),
        "utilization_percent": max(
            float(gpu["utilization_percent"]) for gpu in gpus
        ),
    }


def _request_with_metrics(
    action: Callable[[], dict[str, object]],
    *,
    sampler: MetricSampler | None,
    interval_seconds: float,
) -> tuple[dict[str, object], float, dict[str, object] | None]:
    samples: list[dict[str, object]] = []
    stop = threading.Event()

    def sample() -> None:
        if sampler is None:
            return
        try:
            value = sampler()
        except Exception:
            return
        if isinstance(value, dict):
            samples.append(value)

    def monitor() -> None:
        while not stop.wait(interval_seconds):
            sample()

    sample()
    thread = (
        threading.Thread(target=monitor, name="gpu-monitor", daemon=True)
        if sampler is not None
        else None
    )
    if thread is not None:
        thread.start()
    started = time.monotonic()
    try:
        result = action()
    finally:
        elapsed = time.monotonic() - started
        stop.set()
        if thread is not None:
            thread.join(timeout=max(interval_seconds * 2, 1.0))
        sample()
    if not samples:
        return result, elapsed, None
    return result, elapsed, {
        "samples": len(samples),
        "peak_memory_used_mib": max(
            float(sample.get("memory_used_mib", 0.0)) for sample in samples
        ),
        "memory_total_mib": max(
            float(sample.get("memory_total_mib", 0.0)) for sample in samples
        ),
        "peak_utilization_percent": max(
            float(sample.get("utilization_percent", 0.0)) for sample in samples
        ),
        "last": samples[-1],
    }


def _check(
    checks: list[dict[str, object]],
    name: str,
    expected: object,
    actual: object,
    passed: bool,
) -> None:
    checks.append(
        {
            "name": name,
            "expected": expected,
            "actual": actual,
            "passed": passed,
        }
    )


def _evaluate_expectations(
    response: dict[str, object],
    expectations: dict[str, object],
    *,
    client_duration: float,
) -> list[dict[str, object]]:
    unknown = set(expectations) - EXPECTATION_FIELDS
    if unknown:
        raise ValueError(
            "expectativas desconhecidas: " + ", ".join(sorted(unknown))
        )
    checks: list[dict[str, object]] = []

    if "abstained" in expectations:
        expected = expectations["abstained"]
        if not isinstance(expected, bool):
            raise ValueError("expectations.abstained deve ser booleano")
        actual = response.get("abstained")
        _check(checks, "abstained", expected, actual, actual is expected)

    if "grounding_status" in expectations:
        expected = expectations["grounding_status"]
        actual = response.get("grounding_status")
        _check(checks, "grounding_status", expected, actual, actual == expected)

    valid_citations = response.get("citations_used")
    invalid_citations = response.get("invalid_citations")
    sources = response.get("sources")
    valid_count = len(valid_citations) if isinstance(valid_citations, list) else 0
    invalid_count = (
        len(invalid_citations) if isinstance(invalid_citations, list) else 0
    )
    source_count = len(sources) if isinstance(sources, list) else 0

    if "min_valid_citations" in expectations:
        minimum = int(expectations["min_valid_citations"])
        _check(
            checks,
            "min_valid_citations",
            minimum,
            valid_count,
            valid_count >= minimum,
        )
    if "max_invalid_citations" in expectations:
        maximum = int(expectations["max_invalid_citations"])
        _check(
            checks,
            "max_invalid_citations",
            maximum,
            invalid_count,
            invalid_count <= maximum,
        )
    if "min_sources" in expectations:
        minimum = int(expectations["min_sources"])
        _check(
            checks,
            "min_sources",
            minimum,
            source_count,
            source_count >= minimum,
        )

    coverage_value: float | None = None
    coverage = response.get("citation_coverage")
    if isinstance(coverage, dict) and coverage.get("coverage") is not None:
        coverage_value = float(coverage["coverage"])
    if "min_citation_coverage" in expectations:
        minimum = float(expectations["min_citation_coverage"])
        _check(
            checks,
            "min_citation_coverage",
            minimum,
            coverage_value,
            coverage_value is not None and coverage_value >= minimum,
        )

    scope_coverage_value: float | None = None
    scope_coverage = response.get("scope_citation_coverage")
    if (
        isinstance(scope_coverage, dict)
        and scope_coverage.get("coverage") is not None
    ):
        scope_coverage_value = float(scope_coverage["coverage"])
    if "min_scope_citation_coverage" in expectations:
        minimum = float(expectations["min_scope_citation_coverage"])
        _check(
            checks,
            "min_scope_citation_coverage",
            minimum,
            scope_coverage_value,
            scope_coverage_value is not None
            and scope_coverage_value >= minimum,
        )

    if "allowed_finish_reasons" in expectations:
        allowed = expectations["allowed_finish_reasons"]
        if not isinstance(allowed, list) or not all(
            isinstance(value, str) for value in allowed
        ):
            raise ValueError("allowed_finish_reasons deve ser uma lista de textos")
        actual = response.get("finish_reason")
        _check(checks, "allowed_finish_reasons", allowed, actual, actual in allowed)

    if "max_client_duration_seconds" in expectations:
        maximum = float(expectations["max_client_duration_seconds"])
        _check(
            checks,
            "max_client_duration_seconds",
            maximum,
            round(client_duration, 3),
            client_duration <= maximum,
        )
    if "max_generation_duration_seconds" in expectations:
        maximum = float(expectations["max_generation_duration_seconds"])
        actual = float(response.get("duration_seconds") or 0.0)
        _check(
            checks,
            "max_generation_duration_seconds",
            maximum,
            actual,
            actual <= maximum,
        )
    if "scope_warning" in expectations:
        expected = expectations["scope_warning"]
        if not isinstance(expected, bool):
            raise ValueError("expectations.scope_warning deve ser booleano")
        actual = response.get("scope_warning")
        _check(checks, "scope_warning", expected, actual, actual is expected)

    if "exploration_intent" in expectations:
        expected = expectations["exploration_intent"]
        if not isinstance(expected, str):
            raise ValueError("exploration_intent deve ser texto")
        context = response.get("context")
        exploration = (
            context.get("exploration") if isinstance(context, dict) else None
        )
        actual = (
            exploration.get("intent")
            if isinstance(exploration, dict)
            else None
        )
        _check(checks, "exploration_intent", expected, actual, actual == expected)

    if "required_source_projects" in expectations:
        expected_projects = expectations["required_source_projects"]
        if not isinstance(expected_projects, list) or not all(
            isinstance(value, str) for value in expected_projects
        ):
            raise ValueError(
                "required_source_projects deve ser uma lista de textos"
            )
        actual_projects = {
            str(source.get("project"))
            for source in (sources if isinstance(sources, list) else [])
            if isinstance(source, dict)
        }
        missing = sorted(set(expected_projects) - actual_projects)
        _check(
            checks,
            "required_source_projects",
            expected_projects,
            {"present": sorted(actual_projects), "missing": missing},
            not missing,
        )

    if "required_source_paths" in expectations:
        expected_paths = expectations["required_source_paths"]
        if not isinstance(expected_paths, list) or not all(
            isinstance(value, str) for value in expected_paths
        ):
            raise ValueError("required_source_paths deve ser uma lista de textos")
        actual_paths = {
            str(source.get("path"))
            for source in (sources if isinstance(sources, list) else [])
            if isinstance(source, dict)
        }
        missing = sorted(set(expected_paths) - actual_paths)
        _check(
            checks,
            "required_source_paths",
            expected_paths,
            {"present": sorted(actual_paths), "missing": missing},
            not missing,
        )
    if "forbidden_answer_phrases" in expectations:
        forbidden = expectations["forbidden_answer_phrases"]
        if not isinstance(forbidden, list) or not all(
            isinstance(value, str) and value for value in forbidden
        ):
            raise ValueError(
                "forbidden_answer_phrases deve ser uma lista de textos"
            )
        answer = str(response.get("answer") or "").casefold()
        found = [value for value in forbidden if value.casefold() in answer]
        _check(
            checks,
            "forbidden_answer_phrases",
            forbidden,
            {"found": found},
            not found,
        )
    return checks


def _source_value(source: object, key: str) -> object:
    if not isinstance(source, dict):
        return None
    return source.get(key)


def _source_branch(source: object) -> object:
    occurrence = _source_value(source, "selected_occurrence")
    return occurrence.get("branch") if isinstance(occurrence, dict) else None


def _path_is_within(path: object, prefix: str) -> bool:
    value = str(path or "").strip("/")
    normalized = prefix.strip("/")
    return value == normalized or value.startswith(f"{normalized}/")


def _evaluate_filter_contract(
    response: dict[str, object],
    payload: dict[str, object],
) -> list[dict[str, object]]:
    """Verify that /ask did not return sources outside requested filters."""

    raw_sources = response.get("sources")
    sources = raw_sources if isinstance(raw_sources, list) else []
    checks: list[dict[str, object]] = []

    expected_project = payload.get("project")
    if isinstance(expected_project, str):
        actual = sorted(
            {str(_source_value(source, "project")) for source in sources}
        )
        _check(
            checks,
            "source_project_filter",
            expected_project,
            actual,
            all(
                _source_value(source, "project") == expected_project
                for source in sources
            ),
        )

    expected_branch = payload.get("branch")
    if isinstance(expected_branch, str):
        actual = sorted({str(_source_branch(source)) for source in sources})
        _check(
            checks,
            "source_branch_filter",
            expected_branch,
            actual,
            all(
                _source_branch(source) == expected_branch
                for source in sources
            ),
        )

    expected_prefix = payload.get("path_prefix")
    if isinstance(expected_prefix, str):
        actual = sorted(
            {str(_source_value(source, "path")) for source in sources}
        )
        _check(
            checks,
            "source_path_prefix_filter",
            expected_prefix,
            actual,
            all(
                _path_is_within(_source_value(source, "path"), expected_prefix)
                for source in sources
            ),
        )

    expected_access = payload.get("allowed_access")
    if isinstance(expected_access, list):
        allowed = {str(value) for value in expected_access}
        actual = sorted(
            {str(_source_value(source, "access_class")) for source in sources}
        )
        _check(
            checks,
            "source_access_filter",
            sorted(allowed),
            actual,
            all(
                _source_value(source, "access_class") in allowed
                for source in sources
            ),
        )

    return checks


def evaluate_answer_suite(
    *,
    suite_path: Path,
    api_base_url: str = "http://127.0.0.1:8765",
    output: Path | None = None,
    timeout_seconds: int = 300,
    log: LogCallback | None = None,
    request: RequestCallback | None = None,
    metric_sampler: MetricSampler | None = sample_nvidia_gpu,
    sampling_interval_seconds: float = 0.5,
) -> dict[str, object]:
    if timeout_seconds < 1 or timeout_seconds > 900:
        raise ValueError("timeout_seconds deve estar entre 1 e 900")
    if sampling_interval_seconds <= 0 or sampling_interval_seconds > 60:
        raise ValueError("sampling_interval_seconds deve estar entre 0 e 60")
    logger = log or (lambda _message, _level="info": None)
    suite_file = suite_path.expanduser().resolve()
    suite = _load_suite(suite_file)
    base_url = _validate_loopback_base_url(api_base_url)
    requester = request or _http_requester(base_url)
    raw_cases = suite["cases"]
    assert isinstance(raw_cases, list)
    evaluated: list[dict[str, object]] = []

    for position, raw_case in enumerate(raw_cases, start=1):
        if not isinstance(raw_case, dict):
            raise ValueError(f"caso {position} inválido")
        unknown = set(raw_case) - CASE_FIELDS
        if unknown:
            raise ValueError(
                f"opções desconhecidas no caso {position}: "
                + ", ".join(sorted(unknown))
            )
        case_id = raw_case.get("id")
        query = raw_case.get("query")
        expectations = raw_case.get("expectations")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"caso {position} exige id")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"caso {case_id} exige query")
        if not isinstance(expectations, dict) or not expectations:
            raise ValueError(f"caso {case_id} exige expectations")

        payload = {
            key: value
            for key, value in raw_case.items()
            if key not in {"id", "expectations"}
        }
        logger(
            f"[{position}/{len(raw_cases)}] {case_id}: consultando /ask",
            "info",
        )
        response, client_duration, gpu = _request_with_metrics(
            lambda: requester(payload, timeout_seconds),
            sampler=metric_sampler,
            interval_seconds=sampling_interval_seconds,
        )
        checks = _evaluate_expectations(
            response,
            expectations,
            client_duration=client_duration,
        )
        checks.extend(_evaluate_filter_contract(response, payload))
        passed = all(bool(check["passed"]) for check in checks)
        evaluated.append(
            {
                "id": case_id,
                "query": query,
                "mode": payload.get("mode", "hybrid"),
                "project": payload.get("project"),
                "branch": payload.get("branch"),
                "passed": passed,
                "client_duration_seconds": round(client_duration, 3),
                "gpu": gpu,
                "checks": checks,
                "response": response,
            }
        )
        gpu_text = (
            f"; pico GPU {float(gpu['peak_memory_used_mib']):.0f} MiB"
            if gpu is not None
            else ""
        )
        logger(
            f"[{position}/{len(raw_cases)}] {case_id}: "
            f"{'PASSOU' if passed else 'FALHOU'} em {client_duration:.1f}s"
            f"{gpu_text}",
            "success" if passed else "error",
        )

    cases_passed = sum(bool(case["passed"]) for case in evaluated)
    citation_coverages = [
        float(coverage["coverage"])
        for case in evaluated
        if isinstance((response := case.get("response")), dict)
        and isinstance((coverage := response.get("citation_coverage")), dict)
        and coverage.get("coverage") is not None
    ]
    gpu_cases = [
        case["gpu"] for case in evaluated if isinstance(case.get("gpu"), dict)
    ]
    summary: dict[str, object] = {
        "cases": len(evaluated),
        "cases_passed": cases_passed,
        "cases_failed": len(evaluated) - cases_passed,
        "pass_rate": cases_passed / len(evaluated),
        "mean_client_duration_seconds": sum(
            float(case["client_duration_seconds"]) for case in evaluated
        )
        / len(evaluated),
        "mean_citation_coverage": (
            sum(citation_coverages) / len(citation_coverages)
            if citation_coverages
            else None
        ),
        "peak_gpu_memory_used_mib": (
            max(float(gpu["peak_memory_used_mib"]) for gpu in gpu_cases)
            if gpu_cases
            else None
        ),
        "peak_gpu_utilization_percent": (
            max(float(gpu["peak_utilization_percent"]) for gpu in gpu_cases)
            if gpu_cases
            else None
        ),
    }
    report: dict[str, object] = {
        "schema_version": ANSWER_EVALUATION_SCHEMA_VERSION,
        "suite": suite.get("name", suite_file.stem),
        "suite_file": str(suite_file),
        "suite_hash": _file_hash(suite_file),
        "api_base_url": base_url,
        "summary": summary,
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
