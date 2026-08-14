from __future__ import annotations

import unittest
from unittest import mock

from mflab_knowledge import embeddings


def _result(chunk_id: str, path: str, chunk_hash: str) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "chunk_hash": chunk_hash,
        "path": path,
        "score": 1.0,
        "citation": f"MFSim-NG master@abc {path}:L1-L2",
    }


class EmbeddingTests(unittest.TestCase):
    def test_embed_reuses_count_returned_by_dict_row(self) -> None:
        connection = mock.MagicMock()
        connection.cursor.return_value.fetchall.return_value = []
        connection.execute.return_value.fetchone.return_value = {
            "embeddings_count": 12
        }
        context = mock.MagicMock()
        context.__enter__.return_value = connection

        with mock.patch.object(embeddings, "initialize_vector_database"):
            with mock.patch.object(embeddings, "_driver", return_value=(None, object())):
                with mock.patch.object(embeddings, "_connect", return_value=context):
                    with mock.patch.object(embeddings, "LocalEmbedder") as local:
                        result = embeddings.embed_database("postgresql://unused")

        self.assertEqual(result["embedded"], 0)
        self.assertEqual(result["reused"], 12)
        self.assertEqual(result["device"], "not_loaded")
        local.assert_not_called()

    def test_semantic_search_reads_available_count_from_dict_row(self) -> None:
        available = mock.MagicMock()
        available.fetchone.return_value = {"embeddings_count": 1}
        results = mock.MagicMock()
        results.fetchall.return_value = []
        connection = mock.MagicMock()
        connection.execute.side_effect = [available, results]
        context = mock.MagicMock()
        context.__enter__.return_value = connection
        embedder = mock.MagicMock()
        embedder.profile_id = "profile"
        embedder.encode_query.return_value = [0.0] * 1024

        with mock.patch.object(embeddings, "_driver", return_value=(None, object())):
            with mock.patch.object(embeddings, "_connect", return_value=context):
                values = embeddings.semantic_search(
                    "postgresql://unused",
                    embedder,
                    query="partículas",
                    allowed_access={"lab"},
                )

        self.assertEqual(values, [])
        embedder.register_vector.assert_called_once_with(connection)

    def test_profile_changes_with_semantic_configuration(self) -> None:
        first = embeddings.embedding_profile_id(
            "Qwen/model",
            revision="abc",
            max_sequence_length=4096,
        )
        same = embeddings.embedding_profile_id(
            "Qwen/model",
            revision="abc",
            max_sequence_length=4096,
        )
        changed = embeddings.embedding_profile_id(
            "Qwen/model",
            revision="abc",
            max_sequence_length=2048,
        )
        changed_revision = embeddings.embedding_profile_id(
            "Qwen/model",
            revision="def",
            max_sequence_length=4096,
        )
        self.assertEqual(first, same)
        self.assertNotEqual(first, changed)
        self.assertNotEqual(first, changed_revision)

    def test_embedding_text_preserves_project_path_title_and_content(self) -> None:
        text = embeddings._embedding_text(
            {
                "project": "MFSim-NG",
                "path": "tests/dpm_ram_1b/domains.json",
                "format": "json",
                "title": "arquivo",
                "text": '{"cores": 64}',
            }
        )
        self.assertIn("Project: MFSim-NG", text)
        self.assertIn("Path: tests/dpm_ram_1b/domains.json", text)
        self.assertIn("Title: arquivo", text)
        self.assertIn('{"cores": 64}', text)

    def test_diversity_limits_paths_and_equal_content(self) -> None:
        values = [
            _result("a1", "a.cpp", "same"),
            _result("a2", "a.cpp", "second"),
            _result("a3", "a.cpp", "third"),
            _result("b1", "b.cpp", "same"),
            _result("c1", "c.cpp", "fourth"),
        ]
        selected = embeddings._diversify(
            values,
            limit=10,
            max_per_path=2,
            include_duplicate_content=False,
        )
        self.assertEqual(
            [value["chunk_id"] for value in selected],
            ["a1", "a2", "c1"],
        )

    def test_rrf_rewards_results_found_by_both_rankings(self) -> None:
        lexical = [
            _result("lexical", "lexical.cpp", "l"),
            _result("shared", "shared.cpp", "s"),
        ]
        semantic = [
            _result("semantic", "semantic.cpp", "m"),
            _result("shared", "shared.cpp", "s"),
        ]
        with mock.patch.object(
            embeddings,
            "search_postgres",
            return_value=lexical,
        ) as lexical_search:
            with mock.patch.object(
                embeddings,
                "semantic_search",
                return_value=semantic,
            ) as semantic_search:
                results = embeddings.hybrid_search(
                    "postgresql://unused",
                    mock.Mock(),
                    query="particles",
                    limit=3,
                    branch="master",
                    project="MFSim-NG",
                    allowed_access={"lab"},
                )
        self.assertEqual(results[0]["chunk_id"], "shared")
        self.assertEqual(results[0]["lexical_rank"], 2)
        self.assertEqual(results[0]["semantic_rank"], 2)
        self.assertIn("rrf_score", results[0])
        for search_mock in (lexical_search, semantic_search):
            self.assertEqual(search_mock.call_args.kwargs["branch"], "master")
            self.assertEqual(search_mock.call_args.kwargs["project"], "MFSim-NG")
            self.assertEqual(search_mock.call_args.kwargs["allowed_access"], {"lab"})

    def test_context_expansion_promotes_referenced_symbol_from_candidate_pool(
        self,
    ) -> None:
        ranked = []
        for rank in range(1, 12):
            value = _result(f"chunk-{rank}", f"src/file_{rank}.cpp", f"hash-{rank}")
            value["text"] = "unrelated"
            value["title"] = f"Symbol{rank}"
            value["rrf_score"] = 1.0 / (60 + rank)
            ranked.append(value)
        ranked[0]["path"] = "src/dpm/common/dpm_particle.cpp"
        ranked[0]["text"] = "return ParticleIDGenerator::generate();"
        ranked[10]["path"] = "src/dpm/common/dpm_types.hpp"
        ranked[10]["title"] = "ParticleIDGenerator"

        expanded = embeddings._apply_context_expansion(
            ranked,
            limit=10,
            rrf_k=60,
        )

        promoted = next(
            value for value in expanded if value["chunk_id"] == "chunk-11"
        )
        self.assertLess(expanded.index(promoted), 10)
        self.assertEqual(promoted["context_relation"], "symbol_reference")
        self.assertEqual(promoted["context_source_rank"], 1)

    def test_context_expansion_promotes_companion_test_configuration(self) -> None:
        ranked = []
        for rank in range(1, 12):
            value = _result(f"chunk-{rank}", f"src/file_{rank}.cpp", f"hash-{rank}")
            value["text"] = "unrelated"
            value["title"] = "arquivo"
            value["rrf_score"] = 1.0 / (60 + rank)
            ranked.append(value)
        ranked[2]["path"] = "tests/dpm_ram_1b/domain_0/input/dpm.json"
        ranked[10]["path"] = "tests/dpm_ram_1b/domain_0/input/input.json"

        expanded = embeddings._apply_context_expansion(
            ranked,
            limit=10,
            rrf_k=60,
        )

        promoted = next(
            value for value in expanded if value["chunk_id"] == "chunk-11"
        )
        self.assertLess(expanded.index(promoted), 10)
        self.assertEqual(promoted["context_relation"], "test_bundle")
        self.assertEqual(promoted["context_source_rank"], 3)

    def test_context_expansion_does_not_create_or_promote_unrelated_results(
        self,
    ) -> None:
        ranked = []
        for rank in range(1, 12):
            value = _result(f"chunk-{rank}", f"src/area_{rank}/file.cpp", f"h-{rank}")
            value["text"] = "unrelated"
            value["title"] = "arquivo"
            value["rrf_score"] = 1.0 / (60 + rank)
            ranked.append(value)
        original_ids = [str(value["chunk_id"]) for value in ranked]

        expanded = embeddings._apply_context_expansion(
            ranked,
            limit=10,
            rrf_k=60,
        )

        self.assertEqual(
            [str(value["chunk_id"]) for value in expanded],
            original_ids,
        )
        self.assertTrue(all("context_rank" not in value for value in expanded))

    def test_vector_schema_has_fixed_dimension_and_no_approximate_index(self) -> None:
        schema = embeddings.initialize_vector_database.__globals__["_vector_schema_sql"]()
        self.assertIn("embedding vector(1024)", schema)
        self.assertNotIn("using hnsw", schema.casefold())

    def test_semantic_sql_filters_access_and_provenance_before_text(self) -> None:
        sql = embeddings.SEMANTIC_SEARCH_SQL
        self.assertIn(
            "document.access_class = ANY(%(allowed_access)s::text[])",
            sql,
        )
        self.assertIn("occurrence.branch = %(branch)s::text", sql)
        self.assertIn("repository.project = %(project)s::text", sql)
        self.assertLess(
            sql.index("WHERE embedding.model_id"),
            sql.rindex("ORDER BY embedding.embedding"),
        )


if __name__ == "__main__":
    unittest.main()
