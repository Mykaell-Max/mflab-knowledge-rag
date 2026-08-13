from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from mflab_knowledge.sync import sync_repository_branches


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


@unittest.skipUnless(shutil.which("git"), "Git não está disponível")
class SyncTests(unittest.TestCase):
    def test_syncs_remote_branches_and_renders_hierarchy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            cache = root / "cache"
            output = root / "inventory"
            source.mkdir()
            subprocess.run(
                ["git", "init", "-b", "master", str(source)],
                check=True,
                capture_output=True,
                text=True,
            )
            _git(source, "config", "user.name", "Sync Test")
            _git(source, "config", "user.email", "sync@example.invalid")

            (source / "solver.cpp").write_text("master\n", encoding="utf-8")
            _git(source, "add", "solver.cpp")
            _git(source, "commit", "-m", "master")
            master_commit = _git(source, "rev-parse", "HEAD")

            _git(source, "switch", "-c", "diagnostic/dpm")
            (source / "solver.cpp").write_text("diagnostic\n", encoding="utf-8")
            _git(source, "commit", "-am", "diagnostic")
            diagnostic_commit = _git(source, "rev-parse", "HEAD")
            _git(source, "branch", "feature/alias", diagnostic_commit)
            _git(source, "switch", "master")

            _git(source, "update-ref", "refs/remotes/origin/master", master_commit)
            _git(
                source,
                "update-ref",
                "refs/remotes/origin/diagnostic/dpm",
                diagnostic_commit,
            )
            _git(
                source,
                "update-ref",
                "refs/remotes/origin/feature/alias",
                diagnostic_commit,
            )
            (source / "local-output.h5").write_bytes(b"not committed")
            stale_catalog = output / "branches" / "deleted.generated.yaml"
            stale_catalog.parent.mkdir(parents=True)
            stale_catalog.write_text("stale\n", encoding="utf-8")

            before_head = _git(source, "rev-parse", "HEAD")
            before_status = _git(source, "status", "--short")
            result = sync_repository_branches(
                source=source,
                project="MFSim-NG",
                canonical_ref="origin/master",
                branch_scope="remote",
                access_class="lab",
                profile="generic",
                cache_dir=cache,
                output_dir=output,
            )

            self.assertEqual(result["branches"], 3)
            self.assertEqual(result["unique_commits"], 2)
            self.assertEqual(result["errors"], 0)
            self.assertEqual(_git(source, "rev-parse", "HEAD"), before_head)
            self.assertEqual(_git(source, "status", "--short"), before_status)
            self.assertFalse(stale_catalog.exists())

            tree = (output / "branches.generated.txt").read_text(encoding="utf-8")
            self.assertIn("★ master", tree)
            self.assertIn("diagnostic\n", tree)
            self.assertIn("└── dpm", tree)
            self.assertIn("feature", tree)
            self.assertIn("commit compartilhado", tree)
            self.assertIn("↑1 ↓0", tree)

            master_catalog = (output / "branches" / "master.generated.yaml").read_text(
                encoding="utf-8"
            )
            diagnostic_catalog = (
                output / "branches" / "diagnostic" / "dpm.generated.yaml"
            ).read_text(encoding="utf-8")
            alias_catalog = (
                output / "branches" / "feature" / "alias.generated.yaml"
            ).read_text(encoding="utf-8")
            self.assertIn('canonical: true', master_catalog)
            self.assertIn('branch: "diagnostic/dpm"', diagnostic_catalog)
            self.assertIn('branch: "feature/alias"', alias_catalog)
            self.assertNotIn("local-output.h5", master_catalog)

            manifest = (output / "manifest.generated.yaml").read_text(
                encoding="utf-8"
            )
            self.assertIn('canonical_branch: "master"', manifest)
            self.assertIn("unique_commits: 2", manifest)
            self.assertIn('relation: "ahead"', manifest)
            self.assertIn("ahead: 1", manifest)


if __name__ == "__main__":
    unittest.main()
