from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from mflab_knowledge.generation import (
    GenerationNotConfiguredError,
    GenerationUnavailableError,
)


class AskJobs:
    """Bounded in-memory jobs for real investigation progress in the web UI."""

    lifetime_seconds = 15 * 60
    maximum_jobs = 8

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, object]] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="mflab-ask",
        )

    def _cleanup(self, now: float) -> None:
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if now - float(job.get("updated_at", now)) > self.lifetime_seconds
        ]
        for job_id in expired:
            self._jobs.pop(job_id, None)

    def submit(
        self,
        runner: Callable[[Callable[[dict[str, object]], None]], dict[str, object]],
    ) -> str:
        now = time.monotonic()
        with self._lock:
            self._cleanup(now)
            active = sum(
                job.get("status") in {"queued", "running"}
                for job in self._jobs.values()
            )
            if active >= self.maximum_jobs:
                raise RuntimeError("fila de perguntas temporariamente cheia")
            job_id = secrets.token_urlsafe(24)
            self._jobs[job_id] = {
                "status": "queued",
                "steps": [],
                "created_at": now,
                "updated_at": now,
            }

        def execute() -> None:
            started = time.monotonic()
            with self._lock:
                job = self._jobs.get(job_id)
                if job is None:
                    return
                job["status"] = "running"
                job["updated_at"] = started

            def progress(event: dict[str, object]) -> None:
                with self._lock:
                    job = self._jobs.get(job_id)
                    if job is None:
                        return
                    steps = job["steps"]
                    assert isinstance(steps, list)
                    steps.append(
                        {
                            **event,
                            "sequence": len(steps) + 1,
                            "elapsed_seconds": round(time.monotonic() - started, 3),
                        }
                    )
                    job["updated_at"] = time.monotonic()

            try:
                result = runner(progress)
            except GenerationNotConfiguredError as exc:
                error = str(exc)
            except GenerationUnavailableError as exc:
                error = str(exc)
            except ValueError as exc:
                error = str(exc)
            except Exception:
                error = "falha interna durante a geração da resposta"
            else:
                with self._lock:
                    job = self._jobs.get(job_id)
                    if job is not None:
                        job["status"] = "completed"
                        job["result"] = result
                        job["updated_at"] = time.monotonic()
                return
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job["status"] = "failed"
                    job["error"] = error
                    job["updated_at"] = time.monotonic()

        self._executor.submit(execute)
        return job_id

    def get(self, job_id: str) -> dict[str, object] | None:
        now = time.monotonic()
        with self._lock:
            self._cleanup(now)
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return {
                key: (
                    [dict(item) for item in value]
                    if key == "steps" and isinstance(value, list)
                    else value
                )
                for key, value in job.items()
                if key not in {"created_at", "updated_at"}
            }

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
