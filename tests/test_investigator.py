from __future__ import annotations

import unittest

from mflab_knowledge.investigator import (
    bounded_action_batch,
    build_observations,
    coverage_integration_probes,
    coverage_needs_structural_connection,
    fallback_investigation_actions,
    merge_required_coverage,
    normalize_answer_coverage,
    normalize_investigation_decision,
    pending_graph_continuations,
    prioritize_kept_chunk_ids,
    reconcile_answer_coverage_with_provenance,
    repeated_complete_coverage,
    reserve_chunk_ids_by_aspect,
    select_graph_frontier_results,
    successful_graph_traversal,
)


class InvestigatorTests(unittest.TestCase):
    def test_answer_coverage_accepts_only_required_aspects_and_supported_claims(
        self,
    ) -> None:
        result = normalize_answer_coverage(
            {
                "coverage": [
                    {
                        "aspect": "configuration",
                        "status": "covered",
                        "claim_ids": ["C1", "invented"],
                    },
                    {
                        "aspect": "boundary handling",
                        "status": "covered",
                        "claim_ids": ["C1"],
                    },
                ]
            },
            required_aspects=["configuration", "runtime flow"],
            valid_claim_ids={"C1"},
        )

        self.assertFalse(result["complete"])
        self.assertEqual(
            result["coverage"],
            [
                {
                    "aspect": "configuration",
                    "status": "covered",
                    "claim_ids": ["C1"],
                },
                {"aspect": "runtime flow", "status": "gap", "claim_ids": []},
            ],
        )

    def test_answer_coverage_uses_stable_aspect_ids_when_label_is_rephrased(
        self,
    ) -> None:
        result = normalize_answer_coverage(
            {
                "coverage": [
                    {
                        "aspect_id": "A1",
                        "aspect": "fluxo traduzido pelo modelo",
                        "status": "covered",
                        "claim_ids": ["C1"],
                    }
                ]
            },
            required_aspects=[
                {
                    "aspect_id": "A1",
                    "aspect": "runtime flow",
                    "question_span": "flow",
                }
            ],
            valid_claim_ids={"C1"},
        )

        self.assertTrue(result["complete"])
        self.assertEqual(
            result["coverage"],
            [
                {
                    "aspect": "runtime flow",
                    "status": "covered",
                    "claim_ids": ["C1"],
                }
            ],
        )

    def test_single_answer_aspect_is_unambiguous_without_echoed_id(self) -> None:
        result = normalize_answer_coverage(
            {
                "coverage": [
                    {
                        "aspect": "rótulo traduzido",
                        "status": "partial",
                        "claim_ids": ["C1"],
                    }
                ]
            },
            required_aspects=[
                {
                    "aspect_id": "A1",
                    "aspect": "runtime flow",
                    "question_span": "flow",
                }
            ],
            valid_claim_ids={"C1"},
        )

        self.assertEqual(
            result["coverage"],
            [
                {
                    "aspect": "runtime flow",
                    "status": "partial",
                    "claim_ids": ["C1"],
                }
            ],
        )

    def test_required_only_coverage_discards_planner_adjacent_facets(self) -> None:
        merged = merge_required_coverage(
            ["initialization"],
            [],
            [
                {
                    "aspect": "initialization",
                    "status": "covered",
                    "chunk_ids": ["init"],
                },
                {
                    "aspect": "boundary handling",
                    "status": "gap",
                    "chunk_ids": [],
                },
            ],
            required_only=True,
        )

        self.assertEqual(
            merged,
            [
                {
                    "aspect": "initialization",
                    "status": "covered",
                    "chunk_ids": ["init"],
                }
            ],
        )

    def test_required_coverage_survives_an_empty_or_incomplete_model_ledger(self) -> None:
        seeded = merge_required_coverage(
            ["configuration", "runtime integration"],
            [],
            [],
        )
        merged = merge_required_coverage(
            ["configuration", "runtime integration"],
            seeded,
            [
                {
                    "aspect": "configuration",
                    "status": "covered",
                    "chunk_ids": ["c1"],
                }
            ],
        )

        self.assertEqual(
            merged,
            [
                {
                    "aspect": "configuration",
                    "status": "covered",
                    "chunk_ids": ["c1"],
                },
                {
                    "aspect": "runtime integration",
                    "status": "gap",
                    "chunk_ids": [],
                },
            ],
        )

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

    def test_decision_uses_stable_aspect_id_when_model_rephrases_label(self) -> None:
        decision = normalize_investigation_decision(
            {
                "coverage": [
                    {
                        "aspect_id": "A1",
                        "aspect": "translated label",
                        "status": "covered",
                        "chunk_ids": ["c1"],
                    }
                ],
                "actions": [],
                "keep_chunk_ids": ["c1"],
                "stop": True,
            },
            observable_chunk_ids={"c1"},
            aspect_ids={"A1": "initialization flow"},
        )

        self.assertEqual(decision["coverage"][0]["aspect"], "initialization flow")
        self.assertTrue(decision["stop"])

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

    def test_observations_reserve_new_agent_tool_results(self) -> None:
        latest = [
            {
                "chunk_id": f"latest-{position}",
                "project": "Solver",
                "path": f"src/latest-{position}.cpp",
                "selected_occurrence": {"branch": "trunk"},
            }
            for position in range(12)
        ]
        older_groups = [
            {
                "results": [
                    {
                        "chunk_id": f"older-{group}-{position}",
                        "project": "Solver",
                        "path": f"src/older-{group}-{position}.cpp",
                        "selected_occurrence": {"branch": "trunk"},
                    }
                    for position in range(12)
                ]
            }
            for group in range(20)
        ]

        observations = build_observations(
            [{"mode": "agent_tools", "results": latest}, *older_groups]
        )

        self.assertEqual(
            [item["chunk_id"] for item in observations[:6]],
            [f"latest-{position}" for position in range(6)],
        )
        self.assertIn("older-0-0", [item["chunk_id"] for item in observations])

    def test_observations_show_new_call_edges_before_other_tool_results(self) -> None:
        def result(chunk_id: str, source_kind: str) -> dict[str, object]:
            return {
                "chunk_id": chunk_id,
                "project": "Solver",
                "path": f"src/{chunk_id}.cpp",
                "source_kind": source_kind,
                "selected_occurrence": {"branch": "trunk"},
            }

        observations = build_observations(
            [
                {
                    "mode": "agent_tools",
                    "results": [
                        result("nearby-1", "agent_neighborhood_evidence"),
                        result("nearby-2", "agent_search_evidence"),
                        result("caller", "agent_callers_evidence"),
                        result("callee", "agent_callees_evidence"),
                    ],
                }
            ]
        )

        self.assertEqual(
            [item["chunk_id"] for item in observations[:2]],
            ["caller", "callee"],
        )

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

    def test_fallback_expands_a_relevant_new_call_frontier(self) -> None:
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
            question="Explain how the worker applies the downstream step",
            search_hints=["worker apply flow"],
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

    def test_irrelevant_call_frontier_does_not_overpower_query_match(self) -> None:
        actions = fallback_investigation_actions(
            question="Explain adaptive grid initialization",
            search_hints=[],
            observations=[
                {
                    "chunk_id": "grid",
                    "path": "src/grid/manager.cpp",
                    "title": "GridManager::initialize",
                    "preview": "Initialize the adaptive grid.",
                },
                {
                    "chunk_id": "logging",
                    "path": "src/logging/timer.cpp",
                    "title": "Timer::begin",
                    "preview": "Record elapsed time.",
                    "source_kind": "agent_callees_evidence",
                },
            ],
            previous_actions=[],
        )

        self.assertEqual(actions[0]["chunk_id"], "grid")

    def test_selects_question_relevant_call_frontier(self) -> None:
        selected = select_graph_frontier_results(
            question="How is the adaptive grid initialized?",
            search_hints=["grid setup flow"],
            results=[
                {
                    "chunk_id": "timer",
                    "path": "src/runtime/timer.cpp",
                    "title": "Timer::begin",
                    "text": "Record elapsed time.",
                },
                {
                    "chunk_id": "grid",
                    "path": "src/grid/manager.cpp",
                    "title": "GridManager::initialize",
                    "text": "Initialize cells.",
                },
                {
                    "chunk_id": "output",
                    "path": "src/output/writer.cpp",
                    "title": "Writer::flush",
                    "text": "Write output.",
                },
            ],
            limit=2,
        )

        self.assertEqual(selected[0]["chunk_id"], "grid")

    def test_call_frontier_preserves_distinct_paths_before_siblings(self) -> None:
        selected = select_graph_frontier_results(
            question="Explain the worker lifecycle",
            search_hints=["worker runtime flow"],
            results=[
                {
                    "chunk_id": f"worker-{position}",
                    "path": "src/worker.cpp",
                    "title": f"Worker::step{position}",
                    "text": "worker runtime operation",
                }
                for position in range(6)
            ]
            + [
                {
                    "chunk_id": "driver",
                    "path": "src/driver.cpp",
                    "title": "Driver::advance",
                    "text": "advance the worker runtime flow",
                },
                {
                    "chunk_id": "state",
                    "path": "src/state.cpp",
                    "title": "State::update",
                    "text": "update worker state",
                },
            ],
            limit=4,
        )

        self.assertEqual(selected[0]["chunk_id"], "worker-0")
        self.assertEqual(
            {item["path"] for item in selected[:3]},
            {"src/worker.cpp", "src/driver.cpp", "src/state.cpp"},
        )

    def test_coverage_evidence_precedes_incidental_keeps(self) -> None:
        selected = prioritize_kept_chunk_ids(
            ["build-file", "entry", "runtime", "state"],
            [
                {"aspect": "entry", "chunk_ids": ["entry"]},
                {"aspect": "runtime", "chunk_ids": ["runtime", "state"]},
            ],
        )

        self.assertEqual(selected, ["entry", "runtime", "state", "build-file"])

    def test_coverage_prioritization_reserves_one_chunk_per_aspect(self) -> None:
        selected = prioritize_kept_chunk_ids(
            ["entry-a", "entry-b", "runtime", "integration"],
            [
                {
                    "aspect": "entry",
                    "chunk_ids": ["entry-a", "entry-b"],
                },
                {"aspect": "runtime", "chunk_ids": ["runtime"]},
                {
                    "aspect": "integration",
                    "chunk_ids": ["integration"],
                },
            ],
        )

        self.assertEqual(
            selected,
            ["entry-a", "runtime", "integration", "entry-b"],
        )

    def test_reservation_returns_one_distinct_chunk_per_aspect(self) -> None:
        self.assertEqual(
            reserve_chunk_ids_by_aspect(
                [
                    {"aspect": "entry", "chunk_ids": ["shared", "entry"]},
                    {"aspect": "runtime", "chunk_ids": ["shared", "runtime"]},
                    {"aspect": "gap", "chunk_ids": []},
                ]
            ),
            ["shared", "runtime"],
        )

    def test_answer_coverage_uses_audited_provenance_only_as_partial_floor(
        self,
    ) -> None:
        result = reconcile_answer_coverage_with_provenance(
            {
                "algorithm": "test",
                "performed": True,
                "complete": False,
                "coverage": [
                    {"aspect": "entry", "status": "gap", "claim_ids": []},
                    {"aspect": "flow", "status": "gap", "claim_ids": []},
                ],
            },
            investigation_coverage=[
                {"aspect": "entry", "chunk_ids": ["entry"]},
                {"aspect": "flow", "chunk_ids": ["flow"]},
            ],
            sources=[
                {"source_id": "S1", "chunk_id": "entry"},
                {"source_id": "S2", "chunk_id": "flow"},
            ],
            supported_claims=[
                {"claim_id": "C1", "source_ids": ["S1"]},
            ],
        )

        self.assertFalse(result["complete"])
        self.assertEqual(
            result["coverage"],
            [
                {"aspect": "entry", "status": "partial", "claim_ids": ["C1"]},
                {"aspect": "flow", "status": "gap", "claim_ids": []},
            ],
        )

    def test_complete_coverage_must_repeat_with_the_same_evidence(self) -> None:
        complete = [
            {
                "aspect": "runtime entry",
                "status": "covered",
                "chunk_ids": ["entry"],
            },
            {
                "aspect": "state change",
                "status": "covered",
                "chunk_ids": ["state"],
            },
        ]

        self.assertTrue(repeated_complete_coverage(complete, list(reversed(complete))))
        self.assertFalse(repeated_complete_coverage([], complete))
        self.assertFalse(
            repeated_complete_coverage(
                complete,
                [*complete[:1], {**complete[1], "status": "partial"}],
            )
        )

    def test_flow_stop_requires_a_successful_structural_traversal(self) -> None:
        self.assertFalse(successful_graph_traversal([]))
        self.assertFalse(
            successful_graph_traversal(
                [{"tool": "find_callers", "result_count": "0"}]
            )
        )
        self.assertTrue(
            successful_graph_traversal(
                [{"tool": "find_callees", "result_count": "3"}]
            )
        )

    def test_integration_probes_cover_distinct_observed_aspects(self) -> None:
        actions = coverage_integration_probes(
            [
                {"aspect": "construction", "chunk_ids": ["ctor", "helper"]},
                {"aspect": "runtime", "chunk_ids": ["advance"]},
                {"aspect": "state", "chunk_ids": ["advance", "state"]},
                {"aspect": "invented", "chunk_ids": ["not-observed"]},
            ],
            observable_chunk_ids={"ctor", "advance", "state"},
        )

        self.assertEqual(
            actions,
            [
                {"tool": "find_callers", "chunk_id": "ctor"},
                {"tool": "find_callers", "chunk_id": "advance"},
                {"tool": "find_callers", "chunk_id": "state"},
            ],
        )

    def test_multi_stage_location_requires_structural_connection(self) -> None:
        self.assertTrue(coverage_needs_structural_connection("mechanism", []))
        self.assertFalse(
            coverage_needs_structural_connection(
                "location",
                [{"aspect": "definition"}],
            )
        )
        self.assertTrue(
            coverage_needs_structural_connection(
                "location",
                [{"aspect": "construction"}, {"aspect": "runtime setup"}],
            )
        )

    def test_terminal_continuation_uses_only_observed_unexpanded_call_edges(self) -> None:
        results = [
            {
                "chunk_id": "downstream",
                "source_kind": "agent_callees_evidence",
            },
            {
                "chunk_id": "upstream",
                "source_kind": "agent_callers_evidence",
            },
            {"chunk_id": "lexical", "source_kind": "retrieval"},
        ]

        actions = pending_graph_continuations(
            results,
            [{"tool": "find_callees", "chunk_id": "upstream"}],
        )

        self.assertEqual(
            actions,
            [{"tool": "find_callees", "chunk_id": "downstream"}],
        )

    def test_samples_ordered_frontier_when_vocabulary_has_no_overlap(self) -> None:
        results = [
            {"chunk_id": str(position), "path": "", "title": "", "text": ""}
            for position in range(7)
        ]
        selected = select_graph_frontier_results(
            question="unmatched vocabulary",
            search_hints=[],
            results=results,
            limit=3,
        )

        self.assertEqual(
            [item["chunk_id"] for item in selected],
            ["0", "3", "6"],
        )

        all_selected = select_graph_frontier_results(
            question="unmatched vocabulary",
            search_hints=[],
            results=results[:2],
            limit=8,
        )
        self.assertEqual(
            [item["chunk_id"] for item in all_selected],
            ["0", "1"],
        )

    def test_reserves_supplemental_action_before_truncation(self) -> None:
        executed = {("find_callers", "already")}
        supplemental = {"tool": "find_callees", "chunk_id": "frontier"}
        actions = bounded_action_batch(
            model_actions=[
                {"tool": "find_callers", "chunk_id": "already"},
                {"tool": "open_related", "chunk_id": "primary"},
                {"tool": "open_neighborhood", "chunk_id": "primary"},
            ],
            supplemental_action=supplemental,
            executed_actions=executed,
            limit=3,
        )

        self.assertIn(supplemental, actions)
        self.assertEqual(len(actions), 3)


if __name__ == "__main__":
    unittest.main()
