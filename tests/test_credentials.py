from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mflab_knowledge.credentials import (
    GitCredentials,
    load_database_url,
    load_git_credentials,
)


class CredentialTests(unittest.TestCase):
    def test_first_use_creates_private_empty_env_and_stops(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_file = Path(temporary_directory) / ".env"
            with mock.patch.dict(
                os.environ,
                {"MFLAB_GIT_USERNAME": "", "MFLAB_GIT_READ_TOKEN": ""},
            ):
                with self.assertRaisesRegex(ValueError, "arquivo de credenciais criado"):
                    load_git_credentials(env_file)

            content = env_file.read_text(encoding="utf-8")
            self.assertIn("MFLAB_GIT_USERNAME=", content)
            self.assertIn("MFLAB_GIT_READ_TOKEN=", content)
            self.assertNotIn("token-value", content)
            if os.name != "nt":
                self.assertEqual(env_file.stat().st_mode & 0o777, 0o600)

    def test_loads_credentials_without_exporting_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_file = Path(temporary_directory) / ".env"
            env_file.write_text(
                "MFLAB_GIT_USERNAME=mborges\n"
                "MFLAB_GIT_READ_TOKEN='token-value'\n",
                encoding="utf-8",
            )
            if os.name != "nt":
                env_file.chmod(0o644)
            with mock.patch.dict(
                os.environ,
                {"MFLAB_GIT_USERNAME": "", "MFLAB_GIT_READ_TOKEN": ""},
            ):
                credentials = load_git_credentials(env_file)
                self.assertEqual(
                    credentials,
                    GitCredentials(username="mborges", token="token-value"),
                )
                self.assertEqual(os.environ["MFLAB_GIT_READ_TOKEN"], "")
            if os.name != "nt":
                self.assertEqual(env_file.stat().st_mode & 0o777, 0o600)

    def test_rejects_incomplete_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_file = Path(temporary_directory) / ".env"
            env_file.write_text(
                "MFLAB_GIT_USERNAME=mborges\nMFLAB_GIT_READ_TOKEN=\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {"MFLAB_GIT_USERNAME": "", "MFLAB_GIT_READ_TOKEN": ""},
            ):
                with self.assertRaisesRegex(ValueError, "MFLAB_GIT_READ_TOKEN"):
                    load_git_credentials(env_file)

    def test_database_url_uses_environment_without_exposing_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_file = Path(temporary_directory) / ".env"
            secret_url = "postgresql://mflab:secret@127.0.0.1/mflab"
            with mock.patch.dict(
                os.environ,
                {"MFLAB_DATABASE_URL": secret_url},
                clear=False,
            ):
                self.assertEqual(load_database_url(env_file), secret_url)
            self.assertFalse(env_file.exists())

    def test_database_url_placeholder_is_added_to_existing_env(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_file = Path(temporary_directory) / ".env"
            env_file.write_text(
                "MFLAB_GIT_USERNAME=max\nMFLAB_GIT_READ_TOKEN=token\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {"MFLAB_DATABASE_URL": ""},
                clear=False,
            ):
                with self.assertRaisesRegex(ValueError, "MFLAB_DATABASE_URL"):
                    load_database_url(env_file)
            content = env_file.read_text(encoding="utf-8")
            self.assertEqual(content.count("MFLAB_DATABASE_URL="), 1)
            self.assertNotIn("postgresql://", content)


if __name__ == "__main__":
    unittest.main()
