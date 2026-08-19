from __future__ import annotations

import unittest

from mflab_knowledge.investigator import (
    build_observations,
    normalize_investigation_decision,
)


class InvestigatorTests(unittest.TestCase):
    def test_decision_accepts_only_bounded_read_tools_and_observed_chunks(self) -> None:
        decision = normalize_investigation_decision(
            {
                "coverage": [
                    {
                        "aspect": "entry point",
                        "status": "covered",
                        "chunk_ids": ["known", "invented"],
                    },
                    {
                        "aspect": "runtime flow",
                        "status": "covered",
                        "chunk_ids": [],
                    },
                ],
                "actions": [
                    {"tool": "search_code", "query": "factory create initialize"},
                    {"tool": "open_neighborhood", "chunk_id": "known"},
                    {"tool": "open_neighborhood", "chunk_id": "invented"},
                    {"tool": "shell", "query": "rm -rf"},
                ],
                "keep_chunk_ids": ["known", "invented"],
                "stop": True,
            },
            observable_chunk_ids={"known"},
        )

        self.assertEqual(
            decision["actions"],
            [
                {"tool": "search_code", "query": "factory create initialize"},
                {"tool": "open_neighborhood", "chunk_id": "known"},
            ],
        )
        self.assertEqual(decision["keep_chunk_ids"], ["known"])
        self.assertEqual(decision["coverage"][0]["chunk_ids"], ["known"])
        self.assertEqual(decision["coverage"][1]["status"], "partial")
        self.assertFalse(decision["stop"])

    def test_stop_requires_every_reported_aspect_to_be_covered(self) -> None:
        decision = normalize_investigation_decision(
            {
                "coverage": [
                    {"aspect": "entry", "status": "covered", "chunk_ids": ["c1"]},
                    {"aspect": "runtime", "status": "gap", "chunk_ids": []},
                ],
                "actions": [],
                "keep_chunk_ids": ["c1"],
                "stop": True,
            },
            observable_chunk_ids={"c1"},
        )

        self.assertFalse(decision["stop"])

    def test_observations_are_deduplicated_and_preserve_scope(self) -> None:
        result = {
            "chunk_id": "chunk-1",
            "project": "Solver",
            "path": "src/domain.cpp",
            "title": "Domain::setup",
            "line_start": 10,
            "line_end": 20,
            "text": "x" * 800,
            "selected_occurrence": {
                "branch": "trunk",
                "commit_sha": "a" * 40,
            },
        }

        observations = build_observations(
            [{"results": [result]}, {"results": [dict(result)]}]
        )

        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["branch"], "trunk")
        self.assertEqual(observations[0]["path"], "src/domain.cpp")
        self.assertEqual(len(observations[0]["preview"]), 500)


if __name__ == "__main__":
    unittest.main()
