from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mflab_knowledge.service_runner import (
    ProcessFileLock,
    RunAlreadyActiveError,
    RunStateRecorder,
    read_last_run,
    run_managed,
)


class ServiceRunnerTests(unittest.TestCase):
    def test_process_lock_rejects_concurrent_owner_and_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "state/index.lock"
            first = ProcessFileLock(lock_path)
            second = ProcessFileLock(lock_path)
            first.acquire()
            try:
                with self.assertRaises(RunAlreadyActiveError):
                    second.acquire()
            finally:
                first.release()

            second.acquire()
            second.release()

    def test_recorder_persists_progress_result_and_bounded_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            recorder = RunStateRecorder(
                state_dir,
                history_limit=1,
                progress_write_interval=0,
            )
            first = recorder.start({"config_file": "/safe/repositories.toml"})
            recorder.log("EMBEDDINGS INCREMENTAIS", "section")
            recorder.progress(25, 100, "repository :: src/file.cpp")
            finished = recorder.finish(
                "success",
                result={"failed": 0, "warnings": 0, "embeddings_built": 25},
            )

            stored = read_last_run(state_dir)
            self.assertEqual(stored["run_id"], first["run_id"])
            self.assertEqual(stored["status"], "success")
            self.assertEqual(stored["stage"], "EMBEDDINGS INCREMENTAIS")
            self.assertEqual(stored["progress"]["percent"], 25.0)
            self.assertEqual(finished["result"]["embeddings_built"], 25)
            self.assertEqual(len(list((state_dir / "runs").glob("*.json"))), 1)

            second = RunStateRecorder(state_dir, history_limit=1)
            second.start({"config_file": "/safe/repositories.toml"})
            second.finish("warning", result={"failed": 0, "warnings": 1})
            history = list((state_dir / "runs").glob("*.json"))
            self.assertEqual(len(history), 1)
            history_value = json.loads(history[0].read_text(encoding="utf-8"))
            self.assertEqual(history_value["status"], "warning")

    def test_new_run_archives_unfinished_previous_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary) / "state"
            first = RunStateRecorder(state_dir)
            first_state = first.start({"command": "run-scheduled"})

            second = RunStateRecorder(state_dir)
            second.start({"command": "run-scheduled"})

            previous_path = state_dir / "runs" / f"{first_state['run_id']}.json"
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
            self.assertEqual(previous["status"], "failed")
            self.assertIn("sem estado final", previous["error"])

    def test_managed_run_records_success_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            success = run_managed(
                state_dir=root / "success",
                lock_file=root / "success.lock",
                metadata={"command": "test"},
                action=lambda recorder: {
                    "failed": 0,
                    "warnings": 0,
                    "progress_recorded": bool(recorder.state),
                },
            )
            self.assertEqual(success["status"], "success")
            self.assertTrue(success["progress_recorded"])

            def fail(_recorder: RunStateRecorder) -> dict[str, object]:
                raise ValueError("falha segura")

            with self.assertRaisesRegex(ValueError, "falha segura"):
                run_managed(
                    state_dir=root / "failure",
                    lock_file=root / "failure.lock",
                    metadata={"command": "test"},
                    action=fail,
                )
            self.assertEqual(read_last_run(root / "failure")["status"], "failed")

    def test_read_last_run_reports_missing_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "nenhuma execução"):
                read_last_run(Path(temporary))


if __name__ == "__main__":
    unittest.main()
