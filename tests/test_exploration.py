from __future__ import annotations

import unittest

from mflab_knowledge.exploration import (
    exploration_instructions,
    overview_authority,
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

    def test_direct_question_is_not_expanded(self) -> None:
        plan = plan_exploration("Onde a classe Particle é declarada?")

        self.assertEqual(plan["intent"], "direct")
        self.assertEqual(plan["queries"], ["Onde a classe Particle é declarada?"])

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


if __name__ == "__main__":
    unittest.main()
