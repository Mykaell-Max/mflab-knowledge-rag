from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mflab_knowledge.credentials import GitCredentials
from mflab_knowledge import repository
from mflab_knowledge.repository import (
    list_repository_branches,
    prepare_remote_repository_mirror,
    prepare_repository_mirror,
    prepare_repository_snapshot,
    resolve_remote_default_branch,
)


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@unittest.skipUnless(shutil.which("git"), "Git não está disponível")
class RepositorySnapshotTests(unittest.TestCase):
    @unittest.skipIf(
        os.name == "nt",
        "Git for Windows não pode iniciar upload-pack no sandbox local",
    )
    def test_prepares_remote_only_mirror_and_reuses_it_offline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            remote = root / "remote.git"
            cache = root / "cache"
            source.mkdir()
            subprocess.run(
                ["git", "init", "--bare", str(remote)],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "init", "-b", "trunk", str(source)],
                check=True,
                capture_output=True,
                text=True,
            )
            _git(source, "config", "user.name", "Remote Test")
            _git(source, "config", "user.email", "remote@example.invalid")
            (source / "README.md").write_text("remote\n", encoding="utf-8")
            _git(source, "add", "README.md")
            _git(source, "commit", "-m", "initial")
            _git(source, "remote", "add", "origin", str(remote))
            _git(source, "push", "origin", "trunk")
            subprocess.run(
                [
                    "git",
                    "--git-dir",
                    str(remote),
                    "symbolic-ref",
                    "HEAD",
                    "refs/heads/trunk",
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            online = prepare_remote_repository_mirror(
                remote.as_uri(),
                project="Remote Project",
                cache_dir=cache,
            )
            branches = list_repository_branches(online, scope="remote")
            online_default = resolve_remote_default_branch(
                online,
                refresh_remote=True,
            )
            offline = prepare_remote_repository_mirror(
                remote.as_uri(),
                project="Remote Project",
                cache_dir=cache,
                refresh_remote=False,
            )
            offline_default = resolve_remote_default_branch(
                offline,
                refresh_remote=False,
            )

            self.assertEqual([branch.name for branch in branches], ["trunk"])
            self.assertEqual(online.mirror_path, offline.mirror_path)
            self.assertEqual(online.remote_url, remote.as_uri())
            self.assertEqual(online_default, "trunk")
            self.assertEqual(offline_default, "trunk")

    def test_remote_only_offline_requires_an_existing_mirror(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(ValueError, "ainda não existe"):
                prepare_remote_repository_mirror(
                    "https://gitlab.example.invalid/group/repo.git",
                    project="Missing",
                    cache_dir=Path(temporary_directory),
                    refresh_remote=False,
                )

    def test_remote_only_rejects_credentials_inside_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaisesRegex(ValueError, "não pode conter credenciais"):
                prepare_remote_repository_mirror(
                    "https://user:token@gitlab.example.invalid/group/repo.git",
                    project="Unsafe",
                    cache_dir=Path(temporary_directory),
                )

    def test_remote_credentials_do_not_enter_git_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            mirror = Path(temporary_directory) / "mirror.git"
            mirror.mkdir()
            credentials = GitCredentials("mborges", "secret-token-value")
            captured: dict[str, object] = {}

            def fake_run(command: list[str], **kwargs: object) -> str:
                captured["command"] = command
                captured["env"] = kwargs.get("env")
                return ""

            with mock.patch.object(repository, "_run", side_effect=fake_run):
                repository._refresh_mirror_from_remote(
                    mirror,
                    "https://gitlab.example.invalid/group/project.git",
                    credentials,
                )

            command = captured["command"]
            environment = captured["env"]
            self.assertIsInstance(command, list)
            self.assertIsInstance(environment, dict)
            self.assertNotIn(credentials.token, " ".join(command))
            self.assertEqual(environment["MFLAB_ASKPASS_TOKEN"], credentials.token)
            self.assertFalse(any(mirror.parent.glob(".mflab-askpass-*")))

    def test_materializes_requested_branch_without_touching_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            cache = root / "cache"
            source.mkdir()

            subprocess.run(
                ["git", "init", "-b", "master", str(source)],
                check=True,
                capture_output=True,
                text=True,
            )
            _git(source, "config", "user.name", "Inventory Test")
            _git(source, "config", "user.email", "inventory@example.invalid")
            (source / "solver.cpp").write_text("master\n", encoding="utf-8")
            _git(source, "add", "solver.cpp")
            _git(source, "commit", "-m", "master")

            _git(source, "switch", "-c", "diagnostic/dpm")
            (source / "solver.cpp").write_text("diagnostic\n", encoding="utf-8")
            _git(source, "commit", "-am", "diagnostic")
            (source / "local-output.h5").write_bytes(b"not committed")

            before_head = _git(source, "rev-parse", "HEAD")
            before_branch = _git(source, "branch", "--show-current")
            before_status = _git(source, "status", "--short")

            snapshot = prepare_repository_snapshot(
                source,
                project="MFSim-NG",
                cache_dir=cache,
                ref="master",
            )

            self.assertEqual(snapshot.branch, "master")
            self.assertEqual(
                (snapshot.path / "solver.cpp").read_text(encoding="utf-8"),
                "master\n",
            )
            self.assertFalse((snapshot.path / "local-output.h5").exists())
            self.assertEqual(_git(source, "rev-parse", "HEAD"), before_head)
            self.assertEqual(_git(source, "branch", "--show-current"), before_branch)
            self.assertEqual(_git(source, "status", "--short"), before_status)

    def test_reuses_snapshot_for_same_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            cache = root / "cache"
            source.mkdir()
            subprocess.run(
                ["git", "init", "-b", "master", str(source)],
                check=True,
                capture_output=True,
                text=True,
            )
            _git(source, "config", "user.name", "Inventory Test")
            _git(source, "config", "user.email", "inventory@example.invalid")
            (source / "README.md").write_text("test\n", encoding="utf-8")
            _git(source, "add", "README.md")
            _git(source, "commit", "-m", "initial")

            first = prepare_repository_snapshot(
                source, project="MFSim-NG", cache_dir=cache, ref="master"
            )
            second = prepare_repository_snapshot(
                source, project="MFSim-NG", cache_dir=cache, ref="master"
            )

            self.assertEqual(first.path, second.path)
            self.assertEqual(first.commit_sha, second.commit_sha)

    @unittest.skipIf(
        os.name == "nt",
        "Git for Windows não pode iniciar upload-pack no sandbox local",
    )
    def test_refreshes_remote_without_writing_source_refs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            remote = root / "remote.git"
            cache = root / "cache"
            source.mkdir()
            subprocess.run(
                ["git", "init", "--bare", str(remote)],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "init", "-b", "master", str(source)],
                check=True,
                capture_output=True,
                text=True,
            )
            _git(source, "config", "user.name", "Inventory Test")
            _git(source, "config", "user.email", "inventory@example.invalid")
            _git(source, "remote", "add", "origin", str(remote))
            (source / "README.md").write_text("master\n", encoding="utf-8")
            _git(source, "add", "README.md")
            _git(source, "commit", "-m", "master")
            _git(source, "push", "-u", "origin", "master")

            _git(source, "switch", "-c", "lab/work")
            (source / "README.md").write_text("work\n", encoding="utf-8")
            _git(source, "commit", "-am", "work")
            _git(source, "push", "origin", "lab/work")
            _git(source, "switch", "master")
            _git(source, "branch", "-D", "lab/work")
            _git(source, "update-ref", "-d", "refs/remotes/origin/lab/work")

            before_refs = _git(source, "for-each-ref", "--format=%(refname)")
            mirror = prepare_repository_mirror(
                source,
                project="MFSim-NG",
                cache_dir=cache,
                refresh_remote=True,
            )
            branches = list_repository_branches(mirror, scope="remote")

            self.assertEqual(
                [branch.name for branch in branches],
                ["lab/work", "master"],
            )
            self.assertEqual(
                _git(source, "for-each-ref", "--format=%(refname)"),
                before_refs,
            )


if __name__ == "__main__":
    unittest.main()
