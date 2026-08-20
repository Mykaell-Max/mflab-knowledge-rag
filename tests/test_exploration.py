from __future__ import annotations

import unittest

from mflab_knowledge.exploration import (
    exploration_instructions,
    navigation_terms,
    normalize_query_plan,
    overview_authority,
    overview_quality_issues,
    plan_exploration,
)


class ExplorationTests(unittest.TestCase):
    def test_overview_question_creates_bounded_generic_queries(self) -> None:
        plan = plan_exploration("O que é o Solver?")

        self.assertEqual(plan["intent"], "overview")
        self.assertTrue(plan["expanded"])
        self.assertTrue(plan["require_scope_coverage"])
        self.assertEqual(len(plan["queries"]), 4)
        self.assertTrue(all("Solver" in query for query in plan["queries"]))

    def test_location_question_explores_definition_and_usage(self) -> None:
        plan = plan_exploration("Onde a classe Particle é declarada?")

        self.assertEqual(plan["intent"], "location")
        self.assertEqual(len(plan["queries"]), 3)
        self.assertFalse(plan["require_scope_coverage"])
        instruction = exploration_instructions(plan, [])
        self.assertIn("merely mentions", instruction)

    def test_unclassified_direct_question_is_not_expanded(self) -> None:
        plan = plan_exploration("Liste os parâmetros disponíveis")

        self.assertEqual(plan["intent"], "direct")
        self.assertEqual(plan["queries"], ["Liste os parâmetros disponíveis"])

    def test_mechanism_and_comparison_have_bounded_distinct_plans(self) -> None:
        mechanism = plan_exploration("Como o método é resolvido no código?")
        direct_mechanism = plan_exploration("Como funciona o componente atualmente?")
        flow_mechanism = plan_exploration("Explique o fluxo do componente no código")
        comparison = plan_exploration("Compare o método entre A e B")

        self.assertEqual(mechanism["intent"], "mechanism")
        self.assertEqual(direct_mechanism["intent"], "mechanism")
        self.assertEqual(flow_mechanism["intent"], "mechanism")
        self.assertEqual(len(mechanism["queries"]), 3)
        self.assertIn(
            "general domain knowledge",
            exploration_instructions(mechanism, []),
        )
        self.assertEqual(comparison["intent"], "comparison")
        self.assertTrue(comparison["require_scope_coverage"])
        self.assertIn(
            "each available scope",
            exploration_instructions(comparison, []),
        )

    def test_overview_authority_prefers_root_readme_to_candidate_document(self) -> None:
        values = [
            {"path": "docs/MASTER_CANDIDATE_feature.md"},
            {"path": "README.md"},
            {"path": "src/solver.cpp"},
        ]

        ordered = sorted(values, key=overview_authority)

        self.assertEqual(ordered[0]["path"], "README.md")
        self.assertEqual(ordered[-1]["path"], "docs/MASTER_CANDIDATE_feature.md")

    def test_overview_instruction_requires_each_available_project(self) -> None:
        instruction = exploration_instructions(
            plan_exploration("What is Solver?"),
            [
                {"project": "Solver A"},
                {"project": "Solver B"},
            ],
        )

        self.assertIn("Solver A", instruction)
        self.assertIn("Solver B", instruction)
        self.assertIn("specialized feature", instruction)
        self.assertIn("do not claim they are the only", instruction)

    def test_overview_flags_definitive_project_scope_claims(self) -> None:
        plan = plan_exploration("O que é o Solver?")

        self.assertEqual(
            overview_quality_issues(
                "Ele é composto por dois projetos principais.", plan
            ),
            ["available_scopes_presented_as_definitive"],
        )
        self.assertEqual(
            overview_quality_issues(
                "Entre os projetos atualmente indexados estão A e B.", plan
            ),
            [],
        )

    def test_model_query_plan_is_bounded_and_keeps_original_query_first(self) -> None:
        plan = normalize_query_plan(
            """```json
            {"queries":["mesh creation call flow","adaptive mesh configuration",
            "mesh creation call flow","caller tests"],
            "identifiers":["MeshFactory","initialize","generate"]}
            ```""",
            original_query="Onde a malha é inicializada?",
            fallback_queries=["Onde a malha é inicializada? definition caller"],
        )

        self.assertEqual(plan["queries"][0], "Onde a malha é inicializada?")
        self.assertLessEqual(len(plan["queries"]), 6)
        self.assertEqual(plan["identifiers"], ["MeshFactory", "initialize", "generate"])
        self.assertTrue(plan["generated"])

    def test_query_plan_rejects_malformed_output(self) -> None:
        with self.assertRaisesRegex(ValueError, "JSON"):
            normalize_query_plan(
                "not json",
                original_query="question",
                fallback_queries=["question implementation"],
            )

    def test_navigation_terms_combine_plan_and_candidate_metadata(self) -> None:
        terms = navigation_terms(
            {"identifiers": ["MeshFactory"]},
            [
                {
                    "results": [
                        {"title": "Domain::initialize", "path": "src/mesh_manager.cpp"}
                    ]
                }
            ],
        )

        self.assertEqual(terms, ["MeshFactory", "Domain::initialize", "mesh_manager"])


if __name__ == "__main__":
    unittest.main()
