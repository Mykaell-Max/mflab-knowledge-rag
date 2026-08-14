from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mflab_knowledge.multi_sync import sync_all_repositories
from mflab_knowledge.repository_config import RepositoryCatalog, RepositoryDefinition


def _repository(identifier: str, source: Path, canonical_ref: str) -> RepositoryDefinition:
    return RepositoryDefinition(
        id=identifier,
        enabled=True,
        project=identifier.upper(),
        source=source,
        canonical_ref=canonical_ref,
        branch_scope="remote",
        access_class="lab",
        profile="generic",
    )


def _remote_repository(identifier: str, canonical_ref: str) -> RepositoryDefinition:
    return RepositoryDefinition(
        id=identifier,
        enabled=True,
        project=identifier.upper(),
        source=None,
        canonical_ref=canonical_ref,
        branch_scope="remote",
        access_class="lab",
        profile="generic",
        remote_url=f"https://gitlab.example.invalid/group/{identifier}.git",
    )


class MultiSyncTests(unittest.TestCase):
    def test_continues_after_repository_failure_and_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = _repository("first", root / "first", "origin/trunk")
            second = _remote_repository("second", "origin/release/current")
            catalog = RepositoryCatalog(
                path=root / "repositories.toml",
                config_hash="sha256:config",
                cache_root=root / "cache",
                inventory_root=root / "inventory",
                normalized_root=root / "data",
                repositories=(first, second),
            )
            success = {
                "output_dir": str(root / "inventory/first"),
                "manifest": str(root / "inventory/first/manifest.generated.yaml"),
                "manifest_json": str(root / "inventory/first/manifest.generated.json"),
                "tree": str(root / "inventory/first/branches.generated.txt"),
                "branches": 4,
                "branches_discovered": 7,
                "branches_filtered": 3,
                "unique_commits": 3,
                "inventories_built": 2,
                "inventories_reused": 1,
                "errors": 0,
            }
            messages: list[tuple[str, str]] = []

            with mock.patch(
                "mflab_knowledge.multi_sync.sync_repository_branches",
                side_effect=[success, ValueError("branch canônica ausente")],
            ) as synchronize:
                result = sync_all_repositories(
                    catalog=catalog,
                    refresh_remote=False,
                    log=lambda message, level="info": messages.append((message, level)),
                )

            self.assertEqual(synchronize.call_count, 2)
            self.assertEqual(synchronize.call_args_list[0].kwargs["canonical_ref"], "origin/trunk")
            self.assertEqual(
                synchronize.call_args_list[1].kwargs["canonical_ref"],
                "origin/release/current",
            )
            self.assertIsNone(synchronize.call_args_list[1].kwargs["source"])
            self.assertEqual(
                synchronize.call_args_list[1].kwargs["remote_url"],
                "https://gitlab.example.invalid/group/second.git",
            )
            self.assertEqual(result["succeeded"], 1)
            self.assertEqual(result["failed"], 1)
            self.assertEqual(result["branches"], 4)
            self.assertTrue(Path(str(result["manifest_json"])).is_file())
            manifest = Path(str(result["manifest_json"])).read_text(encoding="utf-8")
            self.assertIn('"status": "success"', manifest)
            self.assertIn('"status": "failed"', manifest)
            self.assertIn("branch canônica ausente", manifest)
            self.assertIn('"source_kind": "remote_url"', manifest)
            self.assertTrue(any("[second]" in message for message, _ in messages))

    def test_repository_filter_rejects_unknown_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = RepositoryCatalog(
                path=root / "repositories.toml",
                config_hash="sha256:config",
                cache_root=root / "cache",
                inventory_root=root / "inventory",
                normalized_root=root / "data",
                repositories=(_repository("known", root / "known", "origin/main"),),
            )
            with self.assertRaisesRegex(ValueError, "desconhecidos"):
                sync_all_repositories(
                    catalog=catalog,
                    refresh_remote=False,
                    repository_ids={"missing"},
                )


if __name__ == "__main__":
    unittest.main()
