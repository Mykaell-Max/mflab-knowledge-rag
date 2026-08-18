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

    def test_api_systemd_assets_are_loopback_only_and_generic(self) -> None:
        root = Path(__file__).resolve().parents[1]
        service = (
            root / "deploy/systemd/mflab-knowledge-api.service.in"
        ).read_text(encoding="utf-8")
        installer = (root / "scripts/install-api-systemd.sh").read_text(
            encoding="utf-8"
        )
        replacements = {
            "@PROJECT_DIR@": "/srv/mflab-knowledge-rag",
            "@SERVICE_USER@": "knowledge",
            "@SERVICE_GROUP@": "knowledge",
            "@PYTHON@": "/srv/mflab-knowledge-rag/.venv/bin/python",
            "@ENV_FILE@": "/srv/mflab-knowledge-rag/.env",
            "@STATE_DIR@": "/srv/mflab-knowledge-rag/state",
            "@PORT@": "8765",
        }
        rendered = service
        for placeholder, value in replacements.items():
            rendered = rendered.replace(placeholder, value)

        self.assertNotIn("@", rendered)
        self.assertIn("--host 127.0.0.1", rendered)
        self.assertIn("Restart=on-failure", rendered)
        self.assertIn("ConditionPathExists=@PYTHON@", service)
        self.assertNotIn('ConditionPathExists="', service)
        self.assertIn("systemd-analyze verify", installer)
        self.assertIn("/health", installer)
        for forbidden in ("/home/max", "mfsim-ng", "mfsim-cmake"):
            self.assertNotIn(forbidden, service.casefold())
            self.assertNotIn(forbidden, installer.casefold())

    def test_llm_systemd_assets_are_loopback_only_and_generic(self) -> None:
        root = Path(__file__).resolve().parents[1]
        service = (
            root / "deploy/systemd/mflab-knowledge-llm.service.in"
        ).read_text(encoding="utf-8")
        installer = (root / "scripts/install-llm-systemd.sh").read_text(
            encoding="utf-8"
        )
        replacements = {
            "@PROJECT_DIR@": "/srv/mflab-knowledge-rag",
            "@SERVICE_USER@": "knowledge",
            "@SERVICE_GROUP@": "knowledge",
            "@VLLM_PYTHON@": "/opt/mflab-vllm/bin/python",
            "@MODEL_PATH@": "/var/lib/mflab/models/model",
            "@SERVED_MODEL@": "local-model",
            "@PORT@": "8000",
            "@MAX_MODEL_LEN@": "8192",
            "@GPU_MEMORY_UTILIZATION@": "0.75",
            "@MAX_NUM_SEQS@": "2",
            "@CHAT_TEMPLATE_KWARGS@": "{}",
        }
        rendered = service
        for placeholder, value in replacements.items():
            rendered = rendered.replace(placeholder, value)

        self.assertNotIn("@", rendered)
        self.assertIn("--host 127.0.0.1", rendered)
        self.assertIn("VLLM_USE_FLASHINFER_SAMPLER=0", service)
        self.assertIn("--served-model-name", service)
        self.assertIn("--default-chat-template-kwargs", service)
        self.assertIn("--model-path", installer)
        self.assertIn("systemd-analyze verify", installer)
        self.assertIn("/health", installer)
        for forbidden in (
            "/home/max",
            "mfsim-ng",
            "mfsim-cmake",
            "qwen",
            "huggingface",
        ):
            self.assertNotIn(forbidden, service.casefold())
            self.assertNotIn(forbidden, installer.casefold())


if __name__ == "__main__":
    unittest.main()
