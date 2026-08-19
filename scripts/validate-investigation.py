#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    timeout: int = 30,
) -> dict[str, object]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc).get("detail")
        except Exception:
            detail = None
        raise RuntimeError(detail or f"HTTP {exc.code} em {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"resposta inválida em {path}")
    return value


def wait_for_job(
    base_url: str,
    job_id: str,
    *,
    timeout_seconds: int,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    seen = 0
    while time.monotonic() < deadline:
        job = request_json(
            base_url,
            f"/ui-api/ask-jobs/{job_id}",
            timeout=20,
        )
        raw_steps = job.get("steps")
        steps = raw_steps if isinstance(raw_steps, list) else []
        for raw_step in steps[seen:]:
            if not isinstance(raw_step, dict):
                continue
            elapsed = float(raw_step.get("elapsed_seconds", 0))
            print(f"[{elapsed:6.1f}s] {raw_step.get('title', 'Etapa')}")
            if raw_step.get("detail"):
                print(f"          {raw_step['detail']}")
            if isinstance(raw_step.get("data"), dict) and raw_step["data"]:
                print(
                    "          "
                    + json.dumps(
                        raw_step["data"],
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
        seen = len(steps)
        if job.get("status") == "completed":
            result = job.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("job concluído sem resultado válido")
            return result
        if job.get("status") == "failed":
            raise RuntimeError(str(job.get("error") or "investigação falhou"))
        time.sleep(0.4)
    raise RuntimeError("tempo limite excedido aguardando a investigação")


def validate_result(result: dict[str, object]) -> None:
    investigation = result.get("investigation")
    steps = (
        investigation.get("steps", [])
        if isinstance(investigation, dict)
        else []
    )
    stages = {
        str(step.get("stage"))
        for step in steps
        if isinstance(step, dict)
    }
    required = {
        "planning",
        "scope",
        "retrieval",
        "evidence",
        "generation",
        "verification",
        "complete",
    }
    missing = required - stages
    if missing:
        raise RuntimeError(
            "etapas ausentes: " + ", ".join(sorted(missing))
        )

    verification = result.get("verification")
    if (
        not isinstance(verification, dict)
        or verification.get("performed") is not True
    ):
        raise RuntimeError("a auditoria semântica não foi executada")
    if result.get("abstained"):
        if result.get("reason") != "evidence_not_supported":
            raise RuntimeError("a resposta foi recusada por motivo inesperado")
    elif verification.get("passed") is not True:
        raise RuntimeError("uma resposta foi entregue sem passar pela auditoria")


def validate_interface(base_url: str) -> None:
    with urllib.request.urlopen(base_url.rstrip("/") + "/ui", timeout=10) as response:
        html = response.read().decode("utf-8")
    with urllib.request.urlopen(
        base_url.rstrip("/") + "/ui/app.js",
        timeout=10,
    ) as response:
        javascript = response.read().decode("utf-8")
    if "ask-investigation" not in html or "Investigação" not in html:
        raise RuntimeError("painel de investigação ausente da interface")
    if "/ui-api/ask-jobs" not in javascript or "renderInvestigation" not in javascript:
        raise RuntimeError("acompanhamento de jobs ausente da interface")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Valida a investigação e a auditoria de uma API já iniciada.",
    )
    parser.add_argument("--question", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--expected-version")
    parser.add_argument("--timeout-seconds", type=int, default=360)
    parser.add_argument("--report", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    health = request_json(args.base_url, "/health", timeout=10)
    print("Health:", json.dumps(health, ensure_ascii=False, sort_keys=True))
    if health.get("status") != "ok":
        raise RuntimeError("API não está saudável")
    if args.expected_version and health.get("version") != args.expected_version:
        raise RuntimeError(
            f"versão {health.get('version')}; esperada {args.expected_version}"
        )

    created = request_json(
        args.base_url,
        "/ui-api/ask-jobs",
        method="POST",
        payload={"query": args.question, "mode": "hybrid", "limit": 10},
    )
    job_id = str(created.get("job_id", ""))
    if not job_id:
        raise RuntimeError("API não devolveu o ID da investigação")
    print("Job:", job_id)
    print("Pergunta:", args.question)
    result = wait_for_job(
        args.base_url,
        job_id,
        timeout_seconds=args.timeout_seconds,
    )
    validate_result(result)
    validate_interface(args.base_url)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    context = result.get("context")
    context = context if isinstance(context, dict) else {}
    print("\nEscopo:")
    print(
        json.dumps(
            context.get("scope_resolution", {}),
            ensure_ascii=False,
            indent=2,
        )
    )
    print("\nAuditoria:")
    verification = result["verification"]
    print(json.dumps(verification.get("counts", {}), ensure_ascii=False))
    print("Revisão automática:", context.get("evidence_repair"))
    print("\nResposta:")
    if result.get("abstained"):
        print("A resposta candidata não foi entregue:", result.get("reason"))
    else:
        print(result.get("answer"))
    print("\n[OK] Investigação, auditoria e interface validadas.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"\n[ERRO] {exc}")
        raise SystemExit(1) from None
