from __future__ import annotations

import unittest

from mflab_knowledge.investigation_graph import (
    build_investigation_graph,
    traversal_edges,
)


class InvestigationGraphTests(unittest.TestCase):
    def result(self, chunk_id: str, title: str) -> dict[str, object]:
        return {
            "chunk_id": chunk_id,
            "project": "Solver",
            "path": f"src/{chunk_id}.cpp",
            "title": title,
            "line_start": 10,
            "line_end": 20,
            "selected_occurrence": {
                "branch": "trunk",
                "commit_sha": "a" * 40,
            },
        }

    def test_call_direction_is_preserved_from_completed_traversal(self) -> None:
        origin = self.result("origin", "Component::advance")
        caller = self.result("caller", "Domain::advance")
        callee = self.result("callee", "Particle::move")
        edges = [
            *traversal_edges(
                tool="find_callers",
                origin_chunk_id="origin",
                results=[caller],
                iteration=1,
            ),
            *traversal_edges(
                tool="find_callees",
                origin_chunk_id="origin",
                results=[callee],
                iteration=1,
            ),
        ]
        graph = build_investigation_graph(
            results=[origin, caller, callee],
            traversals=edges,
            sources=[{**origin, "source_id": "S1"}],
        )

        self.assertEqual(graph["status"], "available")
        self.assertEqual(graph["node_count"], 3)
        self.assertEqual(graph["edge_count"], 2)
        self.assertEqual(
            {(edge["source"], edge["target"], edge["kind"]) for edge in graph["edges"]},
            {
                ("chunk:caller", "chunk:origin", "calls"),
                ("chunk:origin", "chunk:callee", "calls"),
            },
        )
        source = next(node for node in graph["nodes"] if node["chunk_id"] == "origin")
        self.assertEqual(source["source_id"], "S1")
        self.assertNotIn("text", source)

    def test_model_search_actions_do_not_create_structural_edges(self) -> None:
        result = self.result("result", "Candidate")
        edges = traversal_edges(
            tool="search_code",
            origin_chunk_id="result",
            results=[result],
        )
        graph = build_investigation_graph(
            results=[result],
            traversals=edges,
            sources=[{**result, "source_id": "S1"}],
        )

        self.assertEqual(edges, [])
        self.assertEqual(graph["status"], "empty")
        self.assertEqual(graph["edge_count"], 0)


if __name__ == "__main__":
    unittest.main()
