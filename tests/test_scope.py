from __future__ import annotations

import unittest

from mflab_knowledge.scope import resolve_query_scopes


class ScopeResolutionTests(unittest.TestCase):
    def repositories(self) -> list[dict[str, object]]:
        return [
            {
                "project": "Solver Modern",
                "aliases": ["modern"],
                "branch_names": ["main", "integration", "feature/particles"],
                "canonical_branches": ["main"],
                "preferred_branch": "integration",
            },
            {
                "project": "Solver Legacy",
                "aliases": ["legacy"],
                "branch_names": ["trunk", "maintenance"],
                "canonical_branches": ["trunk"],
                "preferred_branch": "trunk",
            },
        ]

    def test_project_alias_selects_its_preferred_branch(self) -> None:
        result = resolve_query_scopes(
            "Como o método funciona no modern?",
            self.repositories(),
        )

        self.assertEqual(result["mode"], "projects_from_query")
        self.assertEqual(
            result["scopes"],
            [
                {
                    "project": "Solver Modern",
                    "branch": "integration",
                    "reason": "project_mentioned",
                }
            ],
        )

    def test_two_projects_create_comparison_scopes(self) -> None:
        result = resolve_query_scopes(
            "Compare o método entre modern e legacy",
            self.repositories(),
        )

        self.assertEqual(len(result["scopes"]), 2)
        self.assertEqual(
            [scope["project"] for scope in result["scopes"]],
            ["Solver Modern", "Solver Legacy"],
        )

    def test_explicit_branch_can_identify_a_scope_without_project(self) -> None:
        result = resolve_query_scopes(
            "O que mudou em feature/particles?",
            self.repositories(),
        )

        self.assertEqual(result["mode"], "branches_from_query")
        self.assertEqual(
            result["scopes"],
            [
                {
                    "project": "Solver Modern",
                    "branch": "feature/particles",
                    "reason": "branch_mentioned",
                }
            ],
        )

    def test_unscoped_query_uses_each_repository_preference(self) -> None:
        result = resolve_query_scopes(
            "Como a conservação é implementada?",
            self.repositories(),
        )

        self.assertEqual(result["mode"], "preferred_defaults")
        self.assertEqual(len(result["scopes"]), 2)
        self.assertEqual(
            {scope["branch"] for scope in result["scopes"]},
            {"integration", "trunk"},
        )


if __name__ == "__main__":
    unittest.main()
