from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from mflab_knowledge.normalize import normalize_manifest, search_chunks


def _hash(content: str) -> str:
    return f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False),
        encoding="utf-8",
    )


class NormalizeTests(unittest.TestCase):
    def test_deduplicates_branches_reuses_parsing_and_searches_with_acl(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            inventory = root / "inventory"
            cache = root / "cache"
            output = root / "normalized"
            second_output = root / "normalized-second"
            master_snapshot = root / "snapshots" / "master"
            feature_snapshot = root / "snapshots" / "feature"
            master_snapshot.mkdir(parents=True)
            feature_snapshot.mkdir(parents=True)

            readme = "# MFSim-NG\n\nDocumentação compartilhada.\n"
            solver = (
                "int DPMManager::advance(int step) {\n"
                "  return step + 1;\n"
                "}\n"
            )
            restricted = "# Decisão interna\n\nARCH_SECRET_TOKEN de arquitetura.\n"
            (master_snapshot / "README.md").write_bytes(readme.encode("utf-8"))
            (feature_snapshot / "README.md").write_bytes(readme.encode("utf-8"))
            (feature_snapshot / "solver.cpp").write_bytes(solver.encode("utf-8"))
            (feature_snapshot / "internal.md").write_bytes(
                restricted.encode("utf-8")
            )

            def catalog(snapshot: Path, items: list[dict[str, object]]) -> dict[str, object]:
                return {
                    "source": {
                        "snapshot_root": str(snapshot),
                        "access_class": "lab",
                    },
                    "indexable": items,
                }

            readme_item = {
                "path": "README.md",
                "format": "markdown",
                "size_bytes": len(readme.encode("utf-8")),
                "content_hash": _hash(readme),
                "access_class": "lab",
            }
            _write_json(
                inventory / "branches" / "master.generated.json",
                catalog(master_snapshot, [readme_item]),
            )
            _write_json(
                inventory / "branches" / "feature.generated.json",
                catalog(
                    feature_snapshot,
                    [
                        readme_item,
                        {
                            "path": "solver.cpp",
                            "format": "cpp",
                            "size_bytes": len(solver.encode("utf-8")),
                            "content_hash": _hash(solver),
                            "access_class": "lab",
                        },
                        {
                            "path": "internal.md",
                            "format": "markdown",
                            "size_bytes": len(restricted.encode("utf-8")),
                            "content_hash": _hash(restricted),
                            "access_class": "restricted",
                        },
                    ],
                ),
            )
            manifest = {
                "project": "MFSim-NG",
                "remote_url": "https://gitlab.example/mfsim-ng.git",
                "branches": [
                    {
                        "name": "master",
                        "commit_sha": "a" * 40,
                        "canonical": True,
                        "requested_ref": "origin/master",
                        "catalog_json": "branches/master.generated.json",
                    },
                    {
                        "name": "feature/dpm",
                        "commit_sha": "b" * 40,
                        "canonical": False,
                        "requested_ref": "origin/feature/dpm",
                        "catalog_json": "branches/feature.generated.json",
                    },
                ],
            }
            manifest_path = inventory / "manifest.generated.json"
            _write_json(manifest_path, manifest)

            first = normalize_manifest(
                manifest_path=manifest_path,
                output_dir=output,
                cache_dir=cache,
            )
            self.assertEqual(first["input_occurrences"], 4)
            self.assertEqual(first["unique_documents"], 3)
            self.assertEqual(first["documents_parsed"], 3)
            self.assertEqual(first["documents_reused"], 0)
            self.assertEqual(first["errors"], 0)

            documents = [
                json.loads(line)
                for line in (output / "documents.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            readme_document = next(
                document for document in documents if document["path"] == "README.md"
            )
            self.assertEqual(len(readme_document["occurrences"]), 2)

            second = normalize_manifest(
                manifest_path=manifest_path,
                output_dir=second_output,
                cache_dir=cache,
            )
            self.assertEqual(second["documents_parsed"], 0)
            self.assertEqual(second["documents_reused"], 3)

            chunks_path = output / "chunks.jsonl"
            dpm_results = search_chunks(
                chunks_path=chunks_path,
                query="DPMManager",
                branch="feature/dpm",
                allowed_access={"lab"},
            )
            self.assertEqual(len(dpm_results), 1)
            self.assertIn("solver.cpp:L1-L3", dpm_results[0]["citation"])
            self.assertEqual(
                search_chunks(
                    chunks_path=chunks_path,
                    query="DPMManager",
                    branch="master",
                    allowed_access={"lab"},
                ),
                [],
            )
            self.assertEqual(
                search_chunks(
                    chunks_path=chunks_path,
                    query="ARCH_SECRET_TOKEN",
                    allowed_access={"public", "lab"},
                ),
                [],
            )
            self.assertEqual(
                search_chunks(
                    chunks_path=chunks_path,
                    query="ARCH_SECRET_TOKEN",
                ),
                [],
            )
            restricted_results = search_chunks(
                chunks_path=chunks_path,
                query="ARCH_SECRET_TOKEN",
                allowed_access={"restricted"},
            )
            self.assertEqual(len(restricted_results), 1)
            self.assertEqual(restricted_results[0]["access_class"], "restricted")
            with self.assertRaisesRegex(ValueError, "filtro de acesso"):
                search_chunks(
                    chunks_path=chunks_path,
                    query="qualquer coisa",
                    allowed_access={"pending"},
                )


if __name__ == "__main__":
    unittest.main()
