from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mflab_knowledge.retrieval import RetrievalPolicy, load_retrieval_policy


class RetrievalPolicyTests(unittest.TestCase):
    def test_defaults_are_repository_agnostic_and_auditable(self) -> None:
        policy = RetrievalPolicy()
        fingerprint = policy.fingerprint()

        self.assertEqual(fingerprint["max_context_results"], 2)
        self.assertTrue(fingerprint["directory_require_root_document"])
        self.assertIn(".json", fingerprint["directory_extensions"])
        serialized = repr(fingerprint).casefold()
        self.assertNotIn("mfsim", serialized)
        self.assertNotIn("dpm", serialized)

    def test_loads_local_toml_and_rejects_unknown_options(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            valid = root / "retrieval.toml"
            valid.write_text(
                """schema_version = "0.1"
[context]
max_context_results = 3
directory_min_documents = 3
directory_extensions = [".json", ".case"]
""",
                encoding="utf-8",
            )
            policy = load_retrieval_policy(valid)
            self.assertEqual(policy.max_context_results, 3)
            self.assertEqual(policy.directory_min_documents, 3)
            self.assertEqual(policy.directory_extensions, (".json", ".case"))

            invalid = root / "invalid.toml"
            invalid.write_text(
                """schema_version = "0.1"
[context]
project_name = "specific-project"
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "opções desconhecidas"):
                load_retrieval_policy(invalid)

    def test_explicit_missing_policy_is_an_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "não encontrada"):
            load_retrieval_policy(Path("definitely-missing-retrieval.toml"))


if __name__ == "__main__":
    unittest.main()
