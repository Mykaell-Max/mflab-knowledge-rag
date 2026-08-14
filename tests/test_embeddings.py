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

    def test_embed_can_reuse_prevalidated_vector_backend(self) -> None:
        connection = mock.MagicMock()
        connection.cursor.return_value.fetchall.return_value = []
        connection.execute.return_value.fetchone.return_value = {
            "embeddings_count": 3
        }
        context = mock.MagicMock()
        context.__enter__.return_value = connection

        with mock.patch.object(embeddings, "initialize_vector_database") as initialize:
            with mock.patch.object(embeddings, "_driver", return_value=(None, object())):
                with mock.patch.object(embeddings, "_connect", return_value=context):
                    result = embeddings.embed_database(
                        "postgresql://unused",
                        initialize_vector_backend=False,
                    )

        initialize.assert_not_called()
        self.assertEqual(result["reused"], 3)

    def test_embed_filters_pipeline_scope_by_repository_id(self) -> None:
        connection = mock.MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = []
        connection.execute.return_value.fetchone.return_value = {
            "embeddings_count": 0
        }
        context = mock.MagicMock()
        context.__enter__.return_value = connection

        with mock.patch.object(embeddings, "initialize_vector_database"):
            with mock.patch.object(embeddings, "_driver", return_value=(None, object())):
                with mock.patch.object(embeddings, "_connect", return_value=context):
                    embeddings.embed_database(
                        "postgresql://unused",
                        repository_ids={"repository-b", "repository-a"},
                    )

        missing_parameters = cursor.execute.call_args.args[1]
        reused_parameters = connection.execute.call_args.args[1]
        self.assertEqual(missing_parameters[1], ["repository-a", "repository-b"])
        self.assertEqual(reused_parameters[1], ["repository-a", "repository-b"])

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
                with mock.patch.object(
                    embeddings,
                    "_contextual_search",
                    return_value=[],
                ):
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

    def test_context_hints_are_explicit_and_bounded(self) -> None:
        seeds = [
            {
                "path": "src/output/hdf5/hdf5_lag_output.cpp",
                "title": "writeParticles",
                "text": "ParticleIDGenerator::generate();",
            },
            {
                "path": "tests/dpm_ram_1b/domain_0/input/dpm.json",
                "title": "arquivo",
                "text": "{}",
            },
            {
                "path": "tests/dpm_ram_1b/domains.json",
                "title": "arquivo",
                "text": "{}",
            },
            {
                "path": "src/dpm/common/dpm_types.hpp",
                "title": "getUniqueID",
                "text": "ParticleID getUniqueID();",
            },
            {
                "path": "src/dpm/common/dpm_types.hpp",
                "title": "preâmbulo",
                "text": "using ParticleID = long;",
            },
        ]

        paths, directories, symbols = embeddings._context_hints(seeds)

        self.assertIn("src/output/hdf5/hdf5_lag_output.hpp", paths)
        self.assertEqual(
            paths["src/output/hdf5/hdf5_lag_output.hpp"][0],
            "paired_source",
        )
        self.assertIn("tests/dpm_ram_1b", directories)
        self.assertEqual(
            directories["tests/dpm_ram_1b"][0],
            "directory_bundle",
        )
        self.assertEqual(
            paths["src/dpm/common/dpm_types.hpp"][0],
            "same_document",
        )
        self.assertEqual(symbols["ParticleIDGenerator"], 1)
        self.assertLessEqual(len(symbols), 24)

    def test_single_test_hit_does_not_expand_the_whole_bundle(self) -> None:
        _paths, directories, _symbols = embeddings._context_hints(
            [
                {
                    "path": "tests/dpm_ram_1b/domain_0/input/dpm.json",
                    "title": "arquivo",
                    "text": "{}",
                }
            ]
        )

        self.assertEqual(directories, {})

    def test_directory_bundles_are_inferred_without_repository_names(self) -> None:
        _paths, directories, _symbols = embeddings._context_hints(
            [
                {
                    "path": "experiments/case-alpha/setup/physics.yaml",
                    "title": "arquivo",
                    "text": "model: particles",
                },
                {
                    "path": "experiments/case-alpha/resources.toml",
                    "title": "arquivo",
                    "text": "workers = 8",
                },
                {
                    "path": "experiments/case-beta/setup/physics.yaml",
                    "title": "arquivo",
                    "text": "model: particles",
                },
                {
                    "path": "experiments/case-beta/resources.toml",
                    "title": "arquivo",
                    "text": "workers = 16",
                },
            ]
        )

        self.assertEqual(
            set(directories),
            {"experiments/case-alpha", "experiments/case-beta"},
        )

    def test_directory_bundle_does_not_merge_isolated_child_directories(self) -> None:
        _paths, directories, _symbols = embeddings._context_hints(
            [
                {
                    "path": "experiments/case-alpha/setup/physics.yaml",
                    "title": "arquivo",
                    "text": "model: particles",
                },
                {
                    "path": "experiments/case-beta/setup/physics.yaml",
                    "title": "arquivo",
                    "text": "model: particles",
                },
            ]
        )

        self.assertEqual(directories, {})

    def test_context_search_filters_before_returning_related_text(self) -> None:
        row = {
            "score": 0.8,
            "chunk_id": "types",
            "chunk_hash": "types-hash",
            "project": "MFSim-NG",
            "path": "src/dpm/common/dpm_types.hpp",
            "title": "ParticleIDGenerator",
            "line_start": 1,
            "line_end": 20,
            "access_class": "lab",
            "branch": "diagnostic/dpm",
            "commit_sha": "a" * 40,
            "occurrences": [{"branch": "diagnostic/dpm"}],
            "text": "class ParticleIDGenerator {};",
        }
        result_cursor = mock.MagicMock()
        result_cursor.fetchall.return_value = [row]
        connection = mock.MagicMock()
        connection.execute.return_value = result_cursor
        context = mock.MagicMock()
        context.__enter__.return_value = connection
        embedder = mock.MagicMock()
        embedder.profile_id = "profile"
        embedder.encode_query.return_value = [0.0] * 1024
        seeds = [
            {
                "path": "src/dpm/common/dpm_particle.cpp",
                "title": "generateId",
                "text": "ParticleIDGenerator::generate();",
            }
        ]

        with mock.patch.object(embeddings, "_driver", return_value=(None, object())):
            with mock.patch.object(embeddings, "_connect", return_value=context):
                values = embeddings._contextual_search(
                    "postgresql://unused",
                    embedder,
                    query="identificadores distribuídos",
                    seeds=seeds,
                    branch="diagnostic/dpm",
                    project="MFSim-NG",
                    path_prefix=None,
                    allowed_access={"lab"},
                )

        sql, parameters = connection.execute.call_args.args
        self.assertIn(
            "document.access_class = ANY(%(allowed_access)s::text[])",
            sql,
        )
        self.assertIn("occurrence.branch = %(branch)s::text", sql)
        self.assertIn(
            "document.path = ANY(%(same_document_paths)s::text[])",
            sql,
        )
        self.assertIn(
            "chunk.chunk_id = ANY(%(seed_chunk_ids)s::text[])",
            sql,
        )
        self.assertEqual(parameters["allowed_access"], ["lab"])
        self.assertEqual(parameters["branch"], "diagnostic/dpm")
        self.assertEqual(parameters["context_candidate_limit"], 50)
        self.assertEqual(values[0]["context_relation"], "symbol_reference")
        self.assertEqual(values[0]["context_source_rank"], 1)

    def test_context_search_keeps_one_complement_per_directory_bundle(self) -> None:
        def bundle_row(chunk_id: str, path: str, score: float) -> dict[str, object]:
            return {
                "score": score,
                "chunk_id": chunk_id,
                "chunk_hash": f"{chunk_id}-hash",
                "project": "MFSim-NG",
                "path": path,
                "title": "arquivo",
                "line_start": 1,
                "line_end": 20,
                "access_class": "lab",
                "branch": "diagnostic/dpm",
                "commit_sha": "a" * 40,
                "occurrences": [{"branch": "diagnostic/dpm"}],
                "text": "{}",
            }

        rows = [
            bundle_row(
                "regular-input",
                "tests/dpm_ram/domain_0/input/input.json",
                0.95,
            ),
            bundle_row(
                "regular-domains",
                "tests/dpm_ram/domains.json",
                0.90,
            ),
            bundle_row(
                "billion-input",
                "tests/dpm_ram_1b/domain_0/input/input.json",
                0.85,
            ),
        ]
        result_cursor = mock.MagicMock()
        result_cursor.fetchall.return_value = rows
        connection = mock.MagicMock()
        connection.execute.return_value = result_cursor
        context = mock.MagicMock()
        context.__enter__.return_value = connection
        embedder = mock.MagicMock()
        embedder.profile_id = "profile"
        embedder.encode_query.return_value = [0.0] * 1024
        seeds = [
            {
                "chunk_id": "regular-dpm",
                "path": "tests/dpm_ram/domain_0/input/dpm.json",
                "title": "arquivo",
                "text": "{}",
            },
            {
                "chunk_id": "billion-dpm",
                "path": "tests/dpm_ram_1b/domain_0/input/dpm.json",
                "title": "arquivo",
                "text": "{}",
            },
            {
                "chunk_id": "regular-seed-domains",
                "path": "tests/dpm_ram/domains.json",
                "title": "arquivo",
                "text": "{}",
            },
            {
                "chunk_id": "billion-seed-domains",
                "path": "tests/dpm_ram_1b/domains.json",
                "title": "arquivo",
                "text": "{}",
            },
        ]

        with mock.patch.object(embeddings, "_driver", return_value=(None, object())):
            with mock.patch.object(embeddings, "_connect", return_value=context):
                values = embeddings._contextual_search(
                    "postgresql://unused",
                    embedder,
                    query="caso com um bilhao de particulas",
                    seeds=seeds,
                    branch="diagnostic/dpm",
                    project="MFSim-NG",
                    path_prefix=None,
                    allowed_access={"lab"},
                )

        self.assertEqual(
            [value["path"] for value in values],
            [
                "tests/dpm_ram/domain_0/input/input.json",
                "tests/dpm_ram_1b/domain_0/input/input.json",
            ],
        )
        self.assertTrue(
            all(value["context_relation"] == "directory_bundle" for value in values)
        )

    def test_same_document_context_prefers_the_nearest_chunk(self) -> None:
        def row(chunk_id: str, title: str, start: int, end: int, score: float):
            return {
                "score": score,
                "chunk_id": chunk_id,
                "chunk_hash": f"{chunk_id}-hash",
                "project": "AnyProject",
                "path": "src/model/types.hpp",
                "title": title,
                "line_start": start,
                "line_end": end,
                "access_class": "lab",
                "branch": "feature/ids",
                "commit_sha": "b" * 40,
                "occurrences": [{"branch": "feature/ids"}],
                "text": title,
            }

        result_cursor = mock.MagicMock()
        result_cursor.fetchall.return_value = [
            row("far", "UnrelatedType", 60, 70, 0.95),
            row("adjacent", "RelevantType", 24, 30, 0.70),
        ]
        connection = mock.MagicMock()
        connection.execute.return_value = result_cursor
        context = mock.MagicMock()
        context.__enter__.return_value = connection
        embedder = mock.MagicMock()
        embedder.profile_id = "profile"
        embedder.encode_query.return_value = [0.0] * 1024
        seeds = [
            {
                "chunk_id": "before",
                "path": "src/model/types.hpp",
                "line_start": 1,
                "line_end": 23,
                "title": "preamble",
                "text": "types",
            },
            {
                "chunk_id": "after",
                "path": "src/model/types.hpp",
                "line_start": 90,
                "line_end": 100,
                "title": "factory",
                "text": "types",
            },
        ]

        with mock.patch.object(embeddings, "_driver", return_value=(None, object())):
            with mock.patch.object(embeddings, "_connect", return_value=context):
                values = embeddings._contextual_search(
                    "postgresql://unused",
                    embedder,
                    query="como os identificadores são criados",
                    seeds=seeds,
                    branch="feature/ids",
                    project="AnyProject",
                    path_prefix=None,
                    allowed_access={"lab"},
                )

        self.assertEqual(values[0]["chunk_id"], "adjacent")
        self.assertEqual(values[0]["context_relation"], "same_document")

    def test_hybrid_promotes_at_most_two_explicit_context_results(self) -> None:
        semantic = []
        for rank in range(1, 11):
            value = _result(f"semantic-{rank}", f"src/{rank}.cpp", f"h-{rank}")
            value["text"] = "semantic"
            value["title"] = f"Result{rank}"
            value["score"] = 1.0 - rank / 100
            semantic.append(value)
        contextual = []
        for rank in range(1, 4):
            value = _result(f"context-{rank}", f"src/context-{rank}.hpp", f"c-{rank}")
            value["text"] = "context"
            value["title"] = f"Context{rank}"
            value["score"] = 0.8
            value["context_relation"] = "paired_source"
            value["context_source_rank"] = rank
            value["context_rank"] = rank
            contextual.append(value)

        with mock.patch.object(embeddings, "search_postgres", return_value=[]):
            with mock.patch.object(
                embeddings,
                "semantic_search",
                return_value=semantic,
            ):
                with mock.patch.object(
                    embeddings,
                    "_contextual_search",
                    return_value=contextual[:2],
                ):
                    results = embeddings.hybrid_search(
                        "postgresql://unused",
                        mock.Mock(),
                        query="contexto",
                        limit=10,
                        allowed_access={"lab"},
                    )

        promoted = [
            value for value in results if "context_relation" in value
        ]
        self.assertEqual(len(promoted), 2)
        self.assertTrue(all(value["path"].endswith(".hpp") for value in promoted))
        self.assertEqual(results[0]["chunk_id"], "semantic-1")
        self.assertEqual(results[1]["chunk_id"], "context-1")

    def test_interleave_keeps_adjacent_chunk_within_path_limit(self) -> None:
        baseline = [
            _result("particle-a", "src/model/particle.cpp", "a"),
            _result("particle-b", "src/model/particle.cpp", "b"),
            _result("generator", "src/common/generator.cpp", "c"),
            _result("types-method", "src/model/types.hpp", "d"),
            _result("particle-header", "src/model/particle.hpp", "e"),
            _result("types-preamble", "src/model/types.hpp", "f"),
        ]
        for rank, value in enumerate(baseline, start=1):
            value["rrf_score"] = 1.0 / (60 + rank)
        adjacent = _result("types-adjacent", "src/model/types.hpp", "g")
        adjacent["context_relation"] = "same_document"
        adjacent["context_source_rank"] = 4

        results = embeddings._interleave_context(
            baseline,
            [adjacent],
            limit=10,
            max_per_path=2,
            include_duplicate_content=False,
        )

        self.assertIn("types-adjacent", [value["chunk_id"] for value in results])
        self.assertNotIn("types-preamble", [value["chunk_id"] for value in results])

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
