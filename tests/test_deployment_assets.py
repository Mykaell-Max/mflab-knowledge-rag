from __future__ import annotations

import unittest
from pathlib import Path


class DeploymentAssetTests(unittest.TestCase):
    def test_systemd_templates_are_generic_and_render_all_placeholders(self) -> None:
        root = Path(__file__).resolve().parents[1]
        service = (
            root / "deploy/systemd/mflab-knowledge-index.service.in"
        ).read_text(encoding="utf-8")
        timer = (
            root / "deploy/systemd/mflab-knowledge-index.timer.in"
        ).read_text(encoding="utf-8")
        installer = (root / "scripts/install-systemd.sh").read_text(
            encoding="utf-8"
        )

        replacements = {
            "@PROJECT_DIR@": "/srv/mflab-knowledge-rag",
            "@SERVICE_USER@": "knowledge",
            "@SERVICE_GROUP@": "knowledge",
            "@PYTHON@": "/srv/mflab-knowledge-rag/.venv/bin/python",
            "@CONFIG_FILE@": "/srv/mflab-knowledge-rag/repositories.toml",
            "@ENV_FILE@": "/srv/mflab-knowledge-rag/.env",
            "@STATE_DIR@": "/srv/mflab-knowledge-rag/state",
            "@BATCH_SIZE@": "4",
            "@INTERVAL@": "5min",
        }
        rendered = service + timer
        for placeholder, value in replacements.items():
            rendered = rendered.replace(placeholder, value)

        self.assertNotIn("@", rendered)
        self.assertIn("run-scheduled", rendered)
        self.assertIn("OnUnitInactiveSec=5min", rendered)
        self.assertIn("ConditionPathExists=@CONFIG_FILE@", service)
        self.assertIn("WorkingDirectory=@PROJECT_DIR@", service)
        self.assertNotIn('ConditionPathExists="', service)
        self.assertIn("systemd-analyze verify", installer)
        for forbidden in ("/home/max", "mfsim-ng", "mfsim-cmake"):
            self.assertNotIn(forbidden, service.casefold())
            self.assertNotIn(forbidden, timer.casefold())
            self.assertNotIn(forbidden, installer.casefold())


if __name__ == "__main__":
    unittest.main()
