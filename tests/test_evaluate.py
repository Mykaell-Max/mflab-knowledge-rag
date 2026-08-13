from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mflab_knowledge.evaluate import evaluate_suite


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class EvaluateTests(unittest.TestCase):
    def test_reports_rank_recall_and_regressions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            chunks_path = root / "chunks.jsonl"
            suite_path = root / "suite.json"
            report_path = root / "report.json"
            chunks = [
                {
                    "chunk_id": "header",
                    "chunk_hash": "sha256:header",
                    "project": "MFSim-NG",
                    "path": "src/dpm_manager.hpp",
                    "title": "DPMManager",
                    "line_start": 1,
                    "line_end": 5,
                    "access_class": "lab",
                    "occurrences": [
                        {"branch": "master", "commit_sha": "a" * 40}
                    ],
                    "text": "class DPMManager {};",
                },
                {
                    "chunk_id": "source",
                    "chunk_hash": "sha256:source",
                    "project": "MFSim-NG",
                    "path": "src/dpm_manager.cpp",
                    "title": "DPMManager::countParticles",
                    "line_start": 10,
                    "line_end": 20,
                    "access_class": "lab",
                    "occurrences": [
                        {"branch": "master", "commit_sha": "a" * 40}
                    ],
                    "text": "void DPMManager::countParticles() {}",
                },
            ]
            chunks_path.write_text(
                "".join(json.dumps(chunk) + "\n" for chunk in chunks),
                encoding="utf-8",
            )
            _write_json(
                suite_path,
                {
                    "schema_version": "0.1",
                    "name": "piloto",
                    "cases": [
                        {
                            "id": "manager",
                            "query": "DPMManager",
                            "branch": "master",
                            "project": "MFSim-NG",
                            "expectations": [
                                {
                                    "path": "src/dpm_manager.hpp",
                                    "title_contains": "Manager",
                                    "within_rank": 2,
                                },
                                {
                                    "path": "src/dpm_manager.cpp",
                                    "within_rank": 2,
                                },
                            ],
                        },
                        {
                            "id": "arquivo-ausente",
                            "query": "DPMManager",
                            "branch": "master",
                            "expectations": [
                                {"path": "src/missing.cpp", "within_rank": 3}
                            ],
                        },
                    ],
                },
            )

            report = evaluate_suite(
                suite_path=suite_path,
                chunks_path=chunks_path,
                output=report_path,
            )

            self.assertEqual(report["summary"]["cases"], 2)
            self.assertEqual(report["summary"]["cases_passed"], 1)
            self.assertEqual(report["summary"]["cases_failed"], 1)
            self.assertEqual(report["summary"]["expectations"], 3)
            self.assertEqual(report["summary"]["expectations_met"], 2)
            self.assertAlmostEqual(
                report["summary"]["expectation_recall"], 2 / 3
            )
            self.assertEqual(report["summary"]["mean_reciprocal_rank"], 0.5)
            self.assertIn(report["cases"][0]["expectations"][0]["rank"], {1, 2})
            self.assertIsNone(report["cases"][1]["expectations"][0]["rank"])
            self.assertTrue(report_path.exists())

    def test_rejects_empty_suite_and_malformed_access_list(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            chunks_path = root / "chunks.jsonl"
            chunks_path.write_text("", encoding="utf-8")
            suite_path = root / "suite.json"
            _write_json(
                suite_path,
                {"schema_version": "0.1", "cases": []},
            )
            with self.assertRaisesRegex(ValueError, "não contém cases"):
                evaluate_suite(suite_path=suite_path, chunks_path=chunks_path)

            _write_json(
                suite_path,
                {
                    "schema_version": "0.1",
                    "cases": [
                        {
                            "id": "acl",
                            "query": "DPMManager",
                            "allowed_access": "lab",
                            "expectations": [{"path": "src/a.cpp"}],
                        }
                    ],
                },
            )
            with self.assertRaisesRegex(ValueError, "allowed_access inválido"):
                evaluate_suite(suite_path=suite_path, chunks_path=chunks_path)


if __name__ == "__main__":
    unittest.main()
