from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from mflab_knowledge.cli import ConsoleReporter, build_parser, main
from mflab_knowledge.service_runner import RunAlreadyActiveError


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
            reporter.section("repositório 1/2")

        content = output.getvalue()
        self.assertIn("\033[36m[mflab:INFO]\033[0m", content)
        self.assertIn("\033[1;32m[mflab:OK]\033[0m", content)
        self.assertIn("\033[1;33m[mflab:AVISO]\033[0m", content)
        self.assertIn("\033[1;31m[mflab:ERRO]\033[0m", content)
        self.assertIn("\033[1;35m[mflab:RESULTADO]\033[0m", content)
        self.assertIn("\033[1;36m[mflab:ETAPA]\033[0m", content)
        self.assertIn("=" * 72, content)

    def test_non_interactive_progress_identifies_current_context(self) -> None:
        output = io.StringIO()
        with redirect_stderr(output):
            reporter = ConsoleReporter(color="never")
            reporter.progress(5, 10, "feature/solver :: src/solver.cpp")

        self.assertIn("50% (5/10)", output.getvalue())
        self.assertIn("feature/solver :: src/solver.cpp", output.getvalue())

    def test_details_and_cache_require_verbose_mode(self) -> None:
        concise = io.StringIO()
        with redirect_stderr(concise):
            reporter = ConsoleReporter(color="never")
            reporter.log("configuração", "detail")
            reporter.log("reutilizado", "cache")
        self.assertEqual(concise.getvalue(), "")

        expanded = io.StringIO()
        with redirect_stderr(expanded):
            reporter = ConsoleReporter(color="never", verbose=True)
            reporter.log("configuração", "detail")
            reporter.log("reutilizado", "cache")
        self.assertIn("[mflab:DETALHE] configuração", expanded.getvalue())
        self.assertIn("[mflab:CACHE] reutilizado", expanded.getvalue())

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
                "--canonical-ref",
                "origin/trunk",
                "--fetch-timeout-seconds",
                "2400",
                "--color",
                "always",
            ]
        )
        sync_all = parser.parse_args(
            [
                "sync-all",
                "--config",
                "repositories.toml",
                "--repository",
                "solver-next",
                "--color",
                "never",
            ]
        )
        index_all = parser.parse_args(
            [
                "index-all",
                "--config",
                "repositories.toml",
                "--repository",
                "solver-next",
                "--offline",
                "--no-embeddings",
                "--batch-size",
                "8",
            ]
        )
        scheduled = parser.parse_args(
            [
                "run-scheduled",
                "--config",
                "repositories.toml",
                "--state-dir",
                "runtime/state",
                "--history-limit",
                "75",
                "--color",
                "never",
            ]
        )
        run_status = parser.parse_args(
            ["run-status", "--state-dir", "runtime/state"]
        )
        serve = parser.parse_args(
            [
                "serve",
                "--port",
                "9000",
                "--allow-access",
                "public",
                "--device",
                "cpu",
                "--generation-config",
                "local-generation.toml",
            ]
        )
        database = parser.parse_args(
            [
                "db-search",
                "--query",
                "DPMManager",
                "--color",
                "never",
            ]
        )
        hybrid = parser.parse_args(
            [
                "db-search",
                "--query",
                "partículas distribuídas",
                "--mode",
                "hybrid",
                "--retrieval-config",
                "retrieval.toml",
            ]
        )
        api_evaluate = parser.parse_args(
            [
                "api-evaluate",
                "--suite",
                "evaluations/answers.json",
                "--api-base-url",
                "http://127.0.0.1:9999",
                "--no-gpu-monitor",
            ]
        )
        self.assertEqual(inventory.color, "never")
        self.assertEqual(sync.color, "always")
        self.assertEqual(sync.canonical_ref, "origin/trunk")
        self.assertEqual(sync.profile, "generic")
        self.assertEqual(sync.fetch_timeout_seconds, 2400)
        self.assertEqual(sync_all.repository, ["solver-next"])
        self.assertFalse(sync_all.verbose)
        self.assertEqual(index_all.repository, ["solver-next"])
        self.assertTrue(index_all.offline)
        self.assertTrue(index_all.no_embeddings)
        self.assertEqual(index_all.batch_size, 8)
        self.assertEqual(scheduled.state_dir, Path("runtime/state"))
        self.assertEqual(scheduled.history_limit, 75)
        self.assertFalse(scheduled.offline)
        self.assertEqual(run_status.state_dir, Path("runtime/state"))
        self.assertEqual(serve.host, "127.0.0.1")
        self.assertEqual(serve.port, 9000)
        self.assertEqual(serve.allow_access, ["public"])
        self.assertEqual(serve.device, "cpu")
        self.assertEqual(
            serve.generation_config,
            Path("local-generation.toml"),
        )
        self.assertEqual(database.color, "never")
        self.assertEqual(hybrid.mode, "hybrid")
        self.assertEqual(hybrid.retrieval_config, Path("retrieval.toml"))
        self.assertEqual(
            api_evaluate.suite,
            Path("evaluations/answers.json"),
        )
        self.assertEqual(api_evaluate.api_base_url, "http://127.0.0.1:9999")
        self.assertTrue(api_evaluate.no_gpu_monitor)

    def test_single_repository_sync_requires_explicit_canonical_ref(self) -> None:
        parser = build_parser()
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(
                    [
                        "sync",
                        "--source",
                        ".",
                        "--project",
                        "test",
                    ]
                )

    def test_api_evaluate_returns_failure_when_a_case_fails(self) -> None:
        report = {
            "summary": {
                "cases": 2,
                "cases_passed": 1,
                "cases_failed": 1,
                "mean_citation_coverage": 0.5,
                "peak_gpu_memory_used_mib": 12000.0,
            }
        }
        with mock.patch(
            "mflab_knowledge.cli.evaluate_answer_suite",
            return_value=report,
        ) as evaluate:
            status = main(
                [
                    "api-evaluate",
                    "--suite",
                    "evaluations/answers.json",
                    "--color",
                    "never",
                ]
            )

        self.assertEqual(status, 1)
        self.assertEqual(
            evaluate.call_args.kwargs["suite_path"],
            Path("evaluations/answers.json"),
        )

    def test_scheduled_command_uses_default_lock_and_reports_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            output = io.StringIO()
            with mock.patch(
                "mflab_knowledge.cli.run_managed",
                return_value={
                    "status": "success",
                    "run_id": "run-1",
                    "failed": 0,
                    "warnings": 0,
                },
            ) as managed:
                with redirect_stdout(output):
                    status = main(
                        [
                            "run-scheduled",
                            "--state-dir",
                            str(state_dir),
                            "--color",
                            "never",
                        ]
                    )

            self.assertEqual(status, 0)
            self.assertIn('"status": "success"', output.getvalue())
            self.assertEqual(
                managed.call_args.kwargs["lock_file"],
                state_dir.resolve() / "index.lock",
            )
            self.assertTrue(
                managed.call_args.kwargs["metadata"]["refresh_remote"]
            )

    def test_scheduled_command_treats_active_run_as_safe_skip(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        with mock.patch(
            "mflab_knowledge.cli.run_managed",
            side_effect=RunAlreadyActiveError("ocupado"),
        ):
            with redirect_stdout(output), redirect_stderr(errors):
                status = main(["run-scheduled", "--color", "never"])

        self.assertEqual(status, 0)
        self.assertIn('"reason": "already_running"', output.getvalue())
        self.assertIn("ocupado", errors.getvalue())

    def test_serve_builds_loopback_service_settings(self) -> None:
        with mock.patch(
            "mflab_knowledge.cli.load_database_url",
            return_value="postgresql:///mflab_knowledge",
        ):
            with mock.patch("mflab_knowledge.cli.run_api") as run:
                status = main(
                    [
                        "serve",
                        "--port",
                        "9001",
                        "--allow-access",
                        "lab",
                        "--allow-access",
                        "project",
                        "--generation-config",
                        "runtime/generation.toml",
                        "--color",
                        "never",
                    ]
                )

        self.assertEqual(status, 0)
        settings = run.call_args.args[0]
        self.assertEqual(settings.allowed_access, frozenset({"lab", "project"}))
        self.assertEqual(
            settings.generation_config,
            Path("runtime/generation.toml"),
        )
        self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")
        self.assertEqual(run.call_args.kwargs["port"], 9001)


if __name__ == "__main__":
    unittest.main()
