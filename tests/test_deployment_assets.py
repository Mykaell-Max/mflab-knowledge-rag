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
            "@DEVICE@": "cpu",
            "@INTERVAL@": "5min",
        }
        rendered = service + timer
        for placeholder, value in replacements.items():
            rendered = rendered.replace(placeholder, value)

        self.assertNotIn("@", rendered)
        self.assertIn("run-scheduled", rendered)
        self.assertIn("OnUnitInactiveSec=5min", rendered)
        self.assertIn("--device cpu", rendered)
        self.assertIn('DEVICE="cpu"', installer)
        self.assertIn("ConditionPathExists=@CONFIG_FILE@", service)
        self.assertIn("WorkingDirectory=@PROJECT_DIR@", service)
        self.assertNotIn('ConditionPathExists="', service)
        self.assertIn("systemd-analyze verify", installer)
        for forbidden in ("/home/max", "mfsim-ng", "mfsim-cmake"):
            self.assertNotIn(forbidden, service.casefold())
            self.assertNotIn(forbidden, timer.casefold())
            self.assertNotIn(forbidden, installer.casefold())

    def test_api_systemd_assets_support_authenticated_lan_and_are_generic(self) -> None:
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
            "@HOST@": "127.0.0.1",
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
        self.assertIn("--host", installer)
        self.assertIn("api-key-init", installer)
        for forbidden in ("/home/max", "mfsim-ng", "mfsim-cmake"):
            self.assertNotIn(forbidden, service.casefold())
            self.assertNotIn(forbidden, installer.casefold())

    def test_browser_interface_is_packaged_without_repository_hardcoding(self) -> None:
        root = Path(__file__).resolve().parents[1]
        html = (root / "src/mflab_knowledge/web/index.html").read_text(
            encoding="utf-8"
        )
        css = (root / "src/mflab_knowledge/web/app.css").read_text(
            encoding="utf-8"
        )
        javascript = (root / "src/mflab_knowledge/web/app.js").read_text(
            encoding="utf-8"
        )
        logo = (root / "src/mflab_knowledge/web/mflab-logo.svg").read_text(
            encoding="utf-8"
        )

        self.assertIn("MFLab Knowledge", html)
        self.assertIn('src="/ui/app.js"', html)
        self.assertIn("Universidade Federal de Uberlândia", html)
        self.assertIn('src="/ui/mflab-logo.svg"', html)
        self.assertIn('viewBox="0 0 64 50"', logo)
        self.assertNotIn("eyebrow", html.casefold())
        self.assertNotIn("uma única superfície", html.casefold())
        self.assertIn("Perguntar à base do MFLab", html)
        self.assertIn("Administração", html)
        self.assertNotIn("Operacional", html)
        self.assertNotIn("sessionStorage", javascript)
        self.assertIn('api("/ui-api/repositories")', javascript)
        self.assertIn('api("/ui-api/admin/status")', javascript)
        self.assertIn("credentials: \"same-origin\"", javascript)
        self.assertIn("function renderMarkdown", javascript)
        self.assertIn("function highlightCode", javascript)
        self.assertIn("function languageForResult", javascript)
        self.assertIn("function createCodeBlock", javascript)
        self.assertIn("document.createTextNode", javascript)
        self.assertIn("source-${sourceId}", javascript)
        self.assertNotIn("innerHTML", javascript)
        self.assertIn(".inline-citation", css)
        self.assertIn(".code-block", css)
        self.assertIn(".syntax-comment", css)
        self.assertIn(".syntax-keyword", css)
        self.assertIn(".syntax-function", css)
        self.assertIn(".result-card[id]:target", css)
        self.assertIn("--accent", css)
        for forbidden in ("mfsim-ng", "mfsim-cmake", "/home/max"):
            self.assertNotIn(forbidden, html.casefold())
            self.assertNotIn(forbidden, css.casefold())
            self.assertNotIn(forbidden, javascript.casefold())

    def test_private_administration_uses_server_side_session_controls(self) -> None:
        root = Path(__file__).resolve().parents[1]
        transport = (root / "src/mflab_knowledge/api_http.py").read_text(
            encoding="utf-8"
        )
        javascript = (root / "src/mflab_knowledge/web/app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('"/ui-api/search"', transport)
        self.assertIn('"/ui-api/ask"', transport)
        self.assertIn('"/ui-api/admin/status"', transport)
        self.assertIn("httponly=True", transport)
        self.assertIn('samesite="strict"', transport)
        self.assertIn("maximum_failures = 5", transport)
        self.assertIn('{"public", "lab"}', transport)
        self.assertNotIn("MFLAB_ADMIN_PASSWORD", javascript)
        self.assertNotIn("localStorage", javascript)
        self.assertNotIn("sessionStorage", javascript)

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
        self.assertIn('realpath -s "$VLLM_PYTHON"', installer)
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
