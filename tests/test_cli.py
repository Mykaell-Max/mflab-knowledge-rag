from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stderr
from unittest import mock

from mflab_knowledge.cli import ConsoleReporter, build_parser


class _InteractiveBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class ConsoleReporterTests(unittest.TestCase):
    def test_force_color_marks_each_log_level(self) -> None:
        output = io.StringIO()
        with redirect_stderr(output):
            reporter = ConsoleReporter(color="always")
            reporter.log("etapa")
            reporter.success("feito")
            reporter.warning("atenção")
            reporter.error("falhou")
            reporter.result("resumo")

        content = output.getvalue()
        self.assertIn("\033[36m[mflab:INFO]\033[0m", content)
        self.assertIn("\033[1;32m[mflab:OK]\033[0m", content)
        self.assertIn("\033[1;33m[mflab:AVISO]\033[0m", content)
        self.assertIn("\033[1;31m[mflab:ERRO]\033[0m", content)
        self.assertIn("\033[1;35m[mflab:RESULTADO]\033[0m", content)

    def test_auto_color_respects_tty_and_no_color(self) -> None:
        interactive = _InteractiveBuffer()
        with redirect_stderr(interactive):
            with mock.patch.dict(os.environ, {}, clear=True):
                ConsoleReporter(color="auto").success("feito")
        self.assertIn("\033[1;32m", interactive.getvalue())

        disabled = _InteractiveBuffer()
        with redirect_stderr(disabled):
            with mock.patch.dict(os.environ, {"NO_COLOR": "1"}, clear=True):
                ConsoleReporter(color="auto").success("feito")
        self.assertNotIn("\033[", disabled.getvalue())

    def test_never_color_and_quiet_are_clean_for_automation(self) -> None:
        output = io.StringIO()
        with redirect_stderr(output):
            ConsoleReporter(color="never").error("falhou")
            ConsoleReporter(quiet=True, color="always").result("oculto")
            ConsoleReporter(quiet=True, color="never").error("erro visível")
        self.assertEqual(
            output.getvalue(),
            "[mflab:ERRO] falhou\n[mflab:ERRO] erro visível\n",
        )

    def test_commands_accept_color_policy(self) -> None:
        parser = build_parser()
        inventory = parser.parse_args(
            [
                "inventory",
                "--source",
                ".",
                "--project",
                "test",
                "--output",
                "out.yaml",
                "--color",
                "never",
            ]
        )
        sync = parser.parse_args(
            [
                "sync",
                "--source",
                ".",
                "--project",
                "test",
                "--color",
                "always",
            ]
        )
        self.assertEqual(inventory.color, "never")
        self.assertEqual(sync.color, "always")


if __name__ == "__main__":
    unittest.main()
