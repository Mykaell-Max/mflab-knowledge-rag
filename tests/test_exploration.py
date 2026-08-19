from __future__ import annotations

import unittest

from mflab_knowledge.exploration import (
    exploration_instructions,
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
        comparison = plan_exploration("Compare o método entre A e B")

        self.assertEqual(mechanism["intent"], "mechanism")
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


if __name__ == "__main__":
    unittest.main()
