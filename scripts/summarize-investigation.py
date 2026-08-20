#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_report(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"relatório inválido: {path}")
    return value


def show_response(label: str, response: dict[str, object]) -> None:
    context = response.get("context")
    context = context if isinstance(context, dict) else {}
    agent = context.get("agent_investigation")
    agent = agent if isinstance(agent, dict) else {}
    verification = response.get("verification")
    verification = verification if isinstance(verification, dict) else {}
    actions = agent.get("actions")
    actions = actions if isinstance(actions, list) else []
    graph_frontier = agent.get("graph_frontier_chunk_ids")
    graph_frontier = graph_frontier if isinstance(graph_frontier, list) else []
    graph_frontier_details = agent.get("graph_frontier")
    graph_frontier_details = (
        graph_frontier_details if isinstance(graph_frontier_details, list) else []
    )
    sources = response.get("sources")
    sources = sources if isinstance(sources, list) else []

    print(f"\n{label}")
    print(
        "  agente:"
        f" status={agent.get('status')}"
        f" | ciclos={agent.get('iterations')}"
        f" | ações={len(actions)}"
        f" | fronteira do grafo={len(graph_frontier)}"
    )
    for raw_action in actions:
        if not isinstance(raw_action, dict):
            continue
        value = raw_action.get("query") or raw_action.get("chunk_id")
        print(
            f"    - {raw_action.get('tool')}: {value}"
            f" -> {raw_action.get('result_count', '?')} resultados"
        )
    if graph_frontier_details:
        print("  fronteira estrutural selecionada:")
        for raw_frontier in graph_frontier_details:
            if not isinstance(raw_frontier, dict):
                continue
            print(
                f"    - {raw_frontier.get('path')}"
                f" :: {raw_frontier.get('title')}"
            )

    counts = verification.get("counts")
    counts = counts if isinstance(counts, dict) else {}
    print(
        "  auditoria:"
        f" executada={verification.get('performed')}"
        f" | lotes={verification.get('batches', 0)}"
        f" | sustentadas={counts.get('supported', 0)}"
        f" | incertas={counts.get('uncertain', 0)}"
        f" | não sustentadas={counts.get('unsupported', 0)}"
    )
    print(
        f"  resposta: abstida={response.get('abstained')}"
        f" | grounding={response.get('grounding_status')}"
        f" | fontes={len(sources)}"
    )
    rejected = verification.get("claims")
    rejected = rejected if isinstance(rejected, list) else []
    for raw_claim in rejected:
        if not isinstance(raw_claim, dict):
            continue
        verdict = str(raw_claim.get("verdict", ""))
        if verdict == "supported":
            continue
        claim = " ".join(str(raw_claim.get("claim", "")).split())[:240]
        finding = " ".join(str(raw_claim.get("finding", "")).split())[:240]
        print(
            f"    - {raw_claim.get('claim_id')} {verdict}: {claim}"
        )
        if finding:
            print(f"      motivo: {finding}")

    source_paths = sorted(
        {
            str(source.get("path", ""))
            for source in sources
            if isinstance(source, dict) and source.get("path")
        }
    )
    if source_paths:
        print("  caminhos recuperados:")
        for path in source_paths:
            print(f"    - {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resume relatórios JSON de investigação sem expor segredos.",
    )
    parser.add_argument("--direct-report", type=Path)
    parser.add_argument("--suite-report", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.direct_report and not args.suite_report:
        raise ValueError("informe ao menos um relatório")

    if args.direct_report:
        direct = load_report(args.direct_report)
        show_response("Teste direto da interface", direct)

    if args.suite_report:
        suite = load_report(args.suite_report)
        summary = suite.get("summary")
        summary = summary if isinstance(summary, dict) else {}
        print(
            "\nSuíte:"
            f" {summary.get('cases_passed', 0)}/{summary.get('cases', 0)} casos"
            f" | cobertura média={summary.get('mean_citation_coverage')}"
            f" | pico GPU={summary.get('peak_gpu_memory_used_mib')} MiB"
        )
        cases = suite.get("cases")
        cases = cases if isinstance(cases, list) else []
        for raw_case in cases:
            if not isinstance(raw_case, dict):
                continue
            state = "OK" if raw_case.get("passed") else "FALHOU"
            response = raw_case.get("response")
            show_response(
                f"[{state}] {raw_case.get('id')}",
                response if isinstance(response, dict) else {},
            )
            checks = raw_case.get("checks")
            checks = checks if isinstance(checks, list) else []
            for raw_check in checks:
                if not isinstance(raw_check, dict) or raw_check.get("passed"):
                    continue
                print(
                    f"    expectativa reprovada: {raw_check.get('name')}"
                    f" | esperado={raw_check.get('expected')}"
                    f" | observado={raw_check.get('actual')}"
                )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[ERRO] {exc}")
        raise SystemExit(1) from None
