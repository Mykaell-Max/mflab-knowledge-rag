from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mflab_knowledge.index_pipeline import index_all_repositories
from mflab_knowledge.repository_config import RepositoryCatalog, RepositoryDefinition


def _repository(identifier: str, root: Path) -> RepositoryDefinition:
    return RepositoryDefinition(
        id=identifier,
        enabled=True,
        project=identifier.upper(),
        source=root / identifier,
        canonical_ref="origin/trunk",
        branch_scope="remote",
        access_class="lab",
        profile="generic",
    )


class IndexPipelineTests(unittest.TestCase):
    def test_indexes_successes_isolates_failures_and_never_records_database_url(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = _repository("first", root)
            second = _repository("second", root)
            catalog = RepositoryCatalog(
                path=root / "repositories.toml",
                config_hash="sha256:catalog",
                cache_root=root / "cache",
                inventory_root=root / "inventory",
                normalized_root=root / "normalized",
                repositories=(first, second),
            )
            synchronization = {
                "manifest": str(root / "inventory/manifest.generated.yaml"),
                "repositories": [
                    {
                        "id": "first",
                        "project": "FIRST",
                        "status": "success",
                        "manifest": str(root / "inventory/first/manifest.generated.yaml"),
                        "manifest_json": str(
                            root / "inventory/first/manifest.generated.json"
                        ),
                        "branches": 3,
                    },
                    {
                        "id": "second",
                        "project": "SECOND",
                        "status": "failed",
                        "error": "remote indisponível",
                    },
                ],
            }
            normalization = {
                "documents": str(root / "normalized/first/documents.jsonl"),
                "chunks": str(root / "normalized/first/chunks.jsonl"),
                "unique_documents": 10,
                "chunks_count": 25,
                "documents_parsed": 10,
                "documents_reused": 0,
                "errors": 0,
            }
            database = {
                "repository_id": "first-stable",
                "project": "FIRST",
                "documents": 10,
                "chunks": 25,
                "reused": False,
            }
            embeddings = {
                "model": "model",
                "revision": "revision",
                "profile": "profile",
                "dimensions": 1024,
                "device": "cpu",
                "embedded": 25,
                "reused": 5,
                "total": 30,
            }
            database_url = "postgresql://operator:very-secret@localhost/knowledge"

            with mock.patch(
                "mflab_knowledge.index_pipeline.initialize_vector_database",
                return_value={"vector_initialized": True},
            ):
                with mock.patch(
                    "mflab_knowledge.index_pipeline.sync_all_repositories",
                    return_value=synchronization,
                ):
                    with mock.patch(
                        "mflab_knowledge.index_pipeline.normalize_manifest",
                        return_value=normalization,
                    ) as normalize:
                        with mock.patch(
                            "mflab_knowledge.index_pipeline.load_corpus",
                            return_value=database,
                        ) as load:
                            with mock.patch(
                                "mflab_knowledge.index_pipeline.embed_database",
                                return_value=embeddings,
                            ) as embed:
                                result = index_all_repositories(
                                    catalog=catalog,
                                    database_url=database_url,
                                    refresh_remote=False,
                                )

            self.assertEqual(result["succeeded"], 1)
            self.assertEqual(result["failed"], 1)
            self.assertEqual(result["loaded_repositories"], 1)
            self.assertEqual(result["embeddings_built"], 25)
            normalize.assert_called_once()
            self.assertEqual(
                normalize.call_args.kwargs["manifest_path"].suffix,
                ".json",
            )
            load.assert_called_once()
            embed.assert_called_once()
            self.assertEqual(
                embed.call_args.kwargs["repository_ids"],
                {"first-stable"},
            )
            manifest = Path(str(result["manifest_json"])).read_text(encoding="utf-8")
            self.assertIn('"credentials_recorded": false', manifest)
            self.assertIn("remote indisponível", manifest)
            self.assertNotIn(database_url, manifest)
            self.assertNotIn("very-secret", manifest)

    def test_can_skip_embeddings_without_loading_vector_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = RepositoryCatalog(
                path=root / "repositories.toml",
                config_hash="sha256:catalog",
                cache_root=root / "cache",
                inventory_root=root / "inventory",
                normalized_root=root / "normalized",
                repositories=(_repository("first", root),),
            )
            synchronization = {
                "manifest": str(root / "inventory/manifest.generated.yaml"),
                "repositories": [
                    {
                        "id": "first",
                        "project": "FIRST",
                        "status": "success",
                        "manifest": str(root / "inventory/first/manifest.generated.yaml"),
                        "manifest_json": str(
                            root / "inventory/first/manifest.generated.json"
                        ),
                    }
                ],
            }
            normalization = {
                "documents": str(root / "documents.jsonl"),
                "chunks": str(root / "chunks.jsonl"),
                "errors": 0,
            }
            database = {
                "repository_id": "first-stable",
                "documents": 1,
                "chunks": 2,
                "reused": True,
            }
            with mock.patch(
                "mflab_knowledge.index_pipeline.initialize_database"
            ) as initialize:
                with mock.patch(
                    "mflab_knowledge.index_pipeline.initialize_vector_database"
                ) as initialize_vector:
                    with mock.patch(
                        "mflab_knowledge.index_pipeline.sync_all_repositories",
                        return_value=synchronization,
                    ):
                        with mock.patch(
                            "mflab_knowledge.index_pipeline.normalize_manifest",
                            return_value=normalization,
                        ):
                            with mock.patch(
                                "mflab_knowledge.index_pipeline.load_corpus",
                                return_value=database,
                            ):
                                with mock.patch(
                                    "mflab_knowledge.index_pipeline.embed_database"
                                ) as embed:
                                    result = index_all_repositories(
                                        catalog=catalog,
                                        database_url="postgresql://localhost/test",
                                        include_embeddings=False,
                                    )

            initialize.assert_called_once()
            initialize_vector.assert_not_called()
            embed.assert_not_called()
            self.assertEqual(result["embedding_status"], "skipped")
            self.assertEqual(result["failed"], 0)


if __name__ == "__main__":
    unittest.main()
