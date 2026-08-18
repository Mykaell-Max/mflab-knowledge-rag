from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mflab_knowledge.api_evaluate import (
    evaluate_answer_suite,
    sample_nvidia_gpu,
)


class ApiEvaluateTests(unittest.TestCase):
    def _suite(self, root: Path, cases: list[dict[str, object]]) -> Path:
        path = root / "answers.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "0.1",
                    "name": "generic answer checks",
                    "cases": cases,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_evaluates_grounded_answer_and_abstention(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suite = self._suite(
                root,
                [
                    {
                        "id": "supported",
                        "query": "How does it work?",
                        "mode": "hybrid",
                        "project": "Generic Solver",
                        "branch": "trunk",
                        "expectations": {
                            "abstained": False,
                            "grounding_status": "cited",
                            "min_valid_citations": 1,
                            "max_invalid_citations": 0,
                            "min_sources": 1,
                            "min_citation_coverage": 1.0,
                            "allowed_finish_reasons": ["stop"],
                            "scope_warning": False,
                            "required_source_paths": ["src/solver.cpp"],
                        },
                    },
                    {
                        "id": "unknown",
                        "query": "no such evidence",
                        "mode": "lexical",
                        "expectations": {
                            "abstained": True,
                            "grounding_status": "no_sources",
                            "max_invalid_citations": 0,
                        },
                    },
                ],
            )
            responses = iter(
                [
                    {
                        "answer": "Supported fact [S1].",
                        "abstained": False,
                        "finish_reason": "stop",
                        "duration_seconds": 0.2,
                        "grounding_status": "cited",
                        "citations_used": ["S1"],
                        "invalid_citations": [],
                        "citation_coverage": {
                            "units": 1,
                            "cited_units": 1,
                            "coverage": 1.0,
                            "uncited_previews": [],
                        },
                        "scope_warning": False,
                        "sources": [{"path": "src/solver.cpp"}],
                    },
                    {
                        "answer": None,
                        "abstained": True,
                        "finish_reason": None,
                        "duration_seconds": 0.0,
                        "grounding_status": "no_sources",
                        "citations_used": [],
                        "invalid_citations": [],
                        "citation_coverage": {
                            "units": 0,
                            "cited_units": 0,
                            "coverage": None,
                            "uncited_previews": [],
                        },
                        "scope_warning": False,
                        "sources": [],
                    },
                ]
            )
            payloads: list[dict[str, object]] = []

            def request(payload: dict[str, object], timeout: int) -> dict[str, object]:
                payloads.append(payload)
                self.assertEqual(timeout, 30)
                return next(responses)

            def metrics() -> dict[str, object]:
                return {
                    "memory_used_mib": 100.0,
                    "memory_total_mib": 1000.0,
                    "utilization_percent": 50.0,
                }

            output = root / "report.json"
            report = evaluate_answer_suite(
                suite_path=suite,
                api_base_url="http://127.0.0.1:8765",
                output=output,
                timeout_seconds=30,
                request=request,
                metric_sampler=metrics,
                sampling_interval_seconds=0.01,
            )

            self.assertEqual(report["summary"]["cases_passed"], 2)
            self.assertEqual(report["summary"]["cases_failed"], 0)
            self.assertEqual(report["summary"]["peak_gpu_memory_used_mib"], 100.0)
            self.assertNotIn("expectations", payloads[0])
            self.assertTrue(output.exists())

    def test_reports_failed_checks_without_hiding_response(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suite = self._suite(
                root,
                [
                    {
                        "id": "partial",
                        "query": "Explain",
                        "expectations": {
                            "grounding_status": "cited",
                            "min_citation_coverage": 1.0,
                            "allowed_finish_reasons": ["stop"],
                        },
                    }
                ],
            )
            response = {
                "answer": "One fact [S1]. Another unsupported fact.",
                "abstained": False,
                "finish_reason": "length",
                "grounding_status": "partial_citations",
                "citations_used": ["S1"],
                "invalid_citations": [],
                "citation_coverage": {
                    "units": 2,
                    "cited_units": 1,
                    "coverage": 0.5,
                },
                "sources": [{"path": "src/a.cpp"}],
            }
            report = evaluate_answer_suite(
                suite_path=suite,
                request=lambda _payload, _timeout: response,
                metric_sampler=None,
            )

        self.assertEqual(report["summary"]["cases_failed"], 1)
        failed = report["cases"][0]
        self.assertFalse(failed["passed"])
        self.assertEqual(failed["response"]["finish_reason"], "length")
        self.assertGreaterEqual(
            sum(not check["passed"] for check in failed["checks"]),
            3,
        )

    def test_rejects_external_endpoint_and_unknown_case_options(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            suite = self._suite(
                root,
                [
                    {
                        "id": "case",
                        "query": "question",
                        "repository": "hardcoded-typo",
                        "expectations": {"abstained": True},
                    }
                ],
            )
            with self.assertRaisesRegex(ValueError, "loopback"):
                evaluate_answer_suite(
                    suite_path=suite,
                    api_base_url="https://example.com:443",
                    request=lambda _payload, _timeout: {},
                )
            with self.assertRaisesRegex(ValueError, "desconhecidas"):
                evaluate_answer_suite(
                    suite_path=suite,
                    request=lambda _payload, _timeout: {},
                )

    def test_samples_nvidia_gpu_without_shell_interpolation(self) -> None:
        completed = mock.Mock(
            stdout="0, Generic GPU, 1200, 16000, 75\n",
        )
        with mock.patch(
            "mflab_knowledge.api_evaluate.shutil.which",
            return_value="/usr/bin/nvidia-smi",
        ):
            with mock.patch(
                "mflab_knowledge.api_evaluate.subprocess.run",
                return_value=completed,
            ) as run:
                result = sample_nvidia_gpu()

        assert result is not None
        self.assertEqual(result["memory_used_mib"], 1200.0)
        self.assertEqual(result["memory_total_mib"], 16000.0)
        self.assertEqual(result["utilization_percent"], 75.0)
        self.assertIsInstance(run.call_args.args[0], list)
        self.assertNotIn("shell", run.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
