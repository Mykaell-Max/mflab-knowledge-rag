from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mflab_knowledge.repository import (
    list_repository_branches,
    prepare_repository_mirror,
    prepare_repository_snapshot,
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
