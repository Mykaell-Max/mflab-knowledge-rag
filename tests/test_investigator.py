from __future__ import annotations

import unittest

from mflab_knowledge.investigator import (
    build_observations,
    fallback_investigation_actions,
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
                    {"tool": "open_related", "chunk_id": "known"},
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
                {"tool": "open_related", "chunk_id": "known"},
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

    def test_observations_round_robin_independent_retrieval_groups(self) -> None:
        crowded = [
            {
                "chunk_id": f"expanded-{position}",
                "project": "Solver",
                "path": f"src/expanded-{position}.cpp",
                "selected_occurrence": {"branch": "trunk"},
            }
            for position in range(30)
        ]
        independent = {
            "chunk_id": "initial-lead",
            "project": "Solver",
            "path": "src/independent.cpp",
            "selected_occurrence": {"branch": "trunk"},
        }

        observations = build_observations(
            [{"results": crowded}, {"results": [independent]}]
        )

        self.assertEqual(len(observations), 18)
        self.assertEqual(observations[1]["chunk_id"], "initial-lead")

    def test_fallback_uses_qualified_terms_and_only_observed_targets(self) -> None:
        observations = [
            {
                "chunk_id": "generic",
                "path": "src/object.cpp",
                "title": "Object::initialize",
                "preview": "Initialize an unrelated output object.",
            },
            {
                "chunk_id": "qualified",
                "path": "src/grid/adaptive_manager.cpp",
                "title": "AdaptiveManager::buildGrid",
                "preview": "Build the adaptive grid and connect its parent cells.",
            },
        ]

        actions = fallback_investigation_actions(
            question="Onde a grade adaptativa é construída?",
            search_hints=["adaptive grid construction entry point"],
            observations=observations,
            previous_actions=[],
        )

        self.assertEqual(
            actions[0],
            {"tool": "open_neighborhood", "chunk_id": "qualified"},
        )
        self.assertEqual(
            actions[1],
            {"tool": "find_callers", "chunk_id": "qualified"},
        )
        self.assertEqual(
            actions[2],
            {"tool": "find_callees", "chunk_id": "qualified"},
        )
        self.assertTrue(
            all(
                action.get("chunk_id") != "invented"
                for action in actions
            )
        )

    def test_fallback_does_not_repeat_previous_actions(self) -> None:
        observations = [
            {
                "chunk_id": "observed",
                "path": "src/driver.cpp",
                "title": "Driver::advance",
                "preview": "Advance one iteration.",
            }
        ]

        actions = fallback_investigation_actions(
            question="How does the driver advance?",
            search_hints=[],
            observations=observations,
            previous_actions=[
                {
                    "tool": "open_neighborhood",
                    "chunk_id": "observed",
                    "result_count": "3",
                },
                {
                    "tool": "find_symbol",
                    "query": "Driver::advance",
                    "result_count": "1",
                },
            ],
        )

        self.assertEqual(
            actions,
            [
                {"tool": "find_callers", "chunk_id": "observed"},
                {"tool": "find_callees", "chunk_id": "observed"},
                {"tool": "open_related", "chunk_id": "observed"},
            ],
        )

    def test_fallback_moves_to_next_observation_after_exhausting_target(self) -> None:
        observations = [
            {
                "chunk_id": "first",
                "path": "src/grid/manager.cpp",
                "title": "GridManager::initialize",
                "preview": "Initialize the adaptive grid.",
            },
            {
                "chunk_id": "second",
                "path": "src/grid/factory.cpp",
                "title": "GridFactory::create",
                "preview": "Create cells used by the adaptive grid.",
            },
        ]
        previous = [
            {"tool": "open_neighborhood", "chunk_id": "first"},
            {"tool": "find_callers", "chunk_id": "first"},
            {"tool": "find_callees", "chunk_id": "first"},
            {"tool": "open_related", "chunk_id": "first"},
            {"tool": "find_symbol", "query": "GridManager::initialize"},
            {"tool": "search_code", "query": "Grid Manager initialize"},
        ]

        actions = fallback_investigation_actions(
            question="Explain the adaptive grid flow",
            search_hints=["adaptive grid creation"],
            observations=observations,
            previous_actions=previous,
        )

        self.assertEqual(actions[0]["chunk_id"], "second")

    def test_fallback_expands_a_new_call_frontier_before_old_matches(self) -> None:
        observations = [
            {
                "chunk_id": "coordinator",
                "path": "src/coordinator.cpp",
                "title": "Coordinator::advance",
                "preview": "Advance the complete operation.",
                "source_kind": "agent_search_evidence",
            },
            {
                "chunk_id": "downstream",
                "path": "src/worker.cpp",
                "title": "Worker::apply",
                "preview": "Apply one downstream step.",
                "source_kind": "agent_callees_evidence",
            },
        ]

        actions = fallback_investigation_actions(
            question="Explain how the coordinator advances",
            search_hints=["coordinator advance flow"],
            observations=observations,
            previous_actions=[],
        )

        self.assertEqual(
            actions,
            [
                {"tool": "find_callees", "chunk_id": "downstream"},
                {"tool": "open_neighborhood", "chunk_id": "downstream"},
                {"tool": "find_callers", "chunk_id": "downstream"},
            ],
        )

    def test_fallback_inspects_sibling_calls_from_an_upstream_caller(self) -> None:
        observations = [
            {
                "chunk_id": "upstream",
                "path": "src/driver.cpp",
                "title": "Driver::advance",
                "preview": "Call the selected operation in sequence.",
                "source_kind": "agent_callers_evidence",
            }
        ]

        actions = fallback_investigation_actions(
            question="Explain the operation flow",
            search_hints=[],
            observations=observations,
            previous_actions=[],
        )

        self.assertEqual(
            actions[0],
            {"tool": "find_callees", "chunk_id": "upstream"},
        )


if __name__ == "__main__":
    unittest.main()
