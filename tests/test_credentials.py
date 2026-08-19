from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mflab_knowledge.credentials import (
    GitCredentials,
    ensure_api_key,
    load_admin_password,
    load_api_key,
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

    def test_api_key_is_generated_once_and_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_file = Path(temporary_directory) / ".env"
            env_file.write_text(
                "MFLAB_GIT_USERNAME=max\nMFLAB_GIT_READ_TOKEN=token\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"MFLAB_API_KEY": ""}, clear=False):
                self.assertTrue(ensure_api_key(env_file))
                first = load_api_key(env_file)
                self.assertFalse(ensure_api_key(env_file))
                second = load_api_key(env_file)

            self.assertIsNotNone(first)
            self.assertGreaterEqual(len(first or ""), 32)
            self.assertEqual(first, second)
            self.assertEqual(
                env_file.read_text(encoding="utf-8").count("MFLAB_API_KEY="),
                1,
            )

    def test_api_key_rejects_short_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_file = Path(temporary_directory) / ".env"
            env_file.write_text("MFLAB_API_KEY=short\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"MFLAB_API_KEY": ""}, clear=False):
                with self.assertRaisesRegex(ValueError, "32 caracteres"):
                    load_api_key(env_file)

    def test_api_key_initializer_writes_file_even_if_process_has_a_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_file = Path(temporary_directory) / ".env"
            env_file.write_text("MFLAB_API_KEY=\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {"MFLAB_API_KEY": "environment-key-that-is-long-enough-123456"},
                clear=False,
            ):
                self.assertTrue(ensure_api_key(env_file))

            file_key = env_file.read_text(encoding="utf-8").split("=", 1)[1].strip()
            self.assertGreaterEqual(len(file_key), 32)
            self.assertNotEqual(file_key, os.environ.get("MFLAB_API_KEY"))

    def test_admin_password_is_loaded_without_entering_process_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_file = Path(temporary_directory) / ".env"
            env_file.write_text(
                "MFLAB_ADMIN_PASSWORD='senha-local-segura'\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {"MFLAB_ADMIN_PASSWORD": ""},
                clear=False,
            ):
                self.assertEqual(
                    load_admin_password(env_file),
                    "senha-local-segura",
                )
                self.assertEqual(os.environ["MFLAB_ADMIN_PASSWORD"], "")

    def test_admin_password_rejects_short_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_file = Path(temporary_directory) / ".env"
            env_file.write_text(
                "MFLAB_ADMIN_PASSWORD=curta\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {"MFLAB_ADMIN_PASSWORD": ""},
                clear=False,
            ):
                with self.assertRaisesRegex(ValueError, "12 caracteres"):
                    load_admin_password(env_file)


if __name__ == "__main__":
    unittest.main()
