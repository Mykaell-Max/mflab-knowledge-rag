from __future__ import annotations

import json
import os
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


class RunAlreadyActiveError(RuntimeError):
    """Raised when another unattended indexing run owns the process lock."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


class ProcessFileLock:
    """Cross-platform non-blocking process lock, backed by an OS file lock."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self._handle: object | None = None
        self._backend: str | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
        try:
            try:
                import fcntl
            except ImportError:
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                self._backend = "msvcrt"
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._backend = "fcntl"
        except (BlockingIOError, OSError) as exc:
            handle.close()
            raise RunAlreadyActiveError(
                f"já existe uma indexação ativa; trava: {self.path}"
            ) from exc

        self._handle = handle
        try:
            metadata = {
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "acquired_at": _utc_now(),
            }
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps(metadata, ensure_ascii=False).encode("utf-8"))
            handle.flush()
            try:
                os.fsync(handle.fileno())
                self.path.chmod(0o600)
            except OSError:
                pass
        except Exception:
            self.release()
            raise

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            if self._backend == "fcntl":
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif self._backend == "msvcrt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()
            self._handle = None
            self._backend = None

    def __enter__(self) -> ProcessFileLock:
        self.acquire()
        return self

    def __exit__(self, *_error: object) -> None:
        self.release()


class RunStateRecorder:
    """Persist safe, bounded state for an unattended indexing execution."""

    def __init__(
        self,
        state_dir: Path,
        *,
        history_limit: int = 50,
        progress_write_interval: float = 2.0,
    ) -> None:
        if history_limit < 1 or history_limit > 1000:
            raise ValueError("history_limit deve estar entre 1 e 1000")
        self.state_dir = state_dir.expanduser().resolve()
        self.history_dir = self.state_dir / "runs"
        self.last_run_path = self.state_dir / "last-run.json"
        self.history_limit = history_limit
        self.progress_write_interval = max(0.0, progress_write_interval)
        self.state: dict[str, object] = {}
        self._started_monotonic = 0.0
        self._last_progress_write = 0.0
        self._progress_started_monotonic = 0.0
        self._progress_origin = 0
        self._progress_total: int | None = None
        self._progress_previous = 0

    def start(self, metadata: dict[str, object]) -> dict[str, object]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self._archive_interrupted_run()
        now = _utc_now()
        self._started_monotonic = time.monotonic()
        self.state = {
            "schema_version": "0.1",
            "run_id": (
                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                + "-"
                + uuid.uuid4().hex[:8]
            ),
            "status": "running",
            "started_at": now,
            "updated_at": now,
            "finished_at": None,
            "duration_seconds": None,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "metadata": metadata,
            "stage": "starting",
            "progress": None,
            "last_event": None,
            "result": None,
            "error": None,
        }
        self._persist()
        return dict(self.state)

    def log(self, message: str, level: str = "info") -> None:
        if not self.state:
            return
        now = _utc_now()
        if level == "section":
            self.state["stage"] = message
            self.state["progress"] = None
            self._progress_total = None
        self.state["last_event"] = {
            "at": now,
            "level": level,
            "message": message,
        }
        self.state["updated_at"] = now
        self._persist()

    def progress(self, current: int, total: int, context: str) -> None:
        if not self.state:
            return
        now_monotonic = time.monotonic()
        if (
            self._progress_total != total
            or current < self._progress_previous
        ):
            self._progress_started_monotonic = now_monotonic
            self._progress_origin = current
            self._progress_total = total
        self._progress_previous = current
        if (
            current < total
            and now_monotonic - self._last_progress_write
            < self.progress_write_interval
        ):
            return
        self._last_progress_write = now_monotonic
        percent = 100.0 if total == 0 else round(current * 100 / total, 2)
        elapsed = max(0.0, now_monotonic - self._progress_started_monotonic)
        processed = current - self._progress_origin
        rate = processed / elapsed if elapsed > 0 and processed > 0 else None
        eta_seconds = (
            max(0.0, (total - current) / rate)
            if rate is not None and rate > 0 and total >= current
            else None
        )
        self.state["progress"] = {
            "current": current,
            "total": total,
            "percent": percent,
            "context": context,
            "rate_per_second": round(rate, 3) if rate is not None else None,
            "eta_seconds": round(eta_seconds, 1) if eta_seconds is not None else None,
        }
        self.state["updated_at"] = _utc_now()
        self._persist()

    def finish(
        self,
        status: str,
        *,
        result: dict[str, object] | None = None,
        error: str | None = None,
    ) -> dict[str, object]:
        if status not in {"success", "warning", "failed"}:
            raise ValueError(f"status final inválido: {status}")
        now = _utc_now()
        self.state["status"] = status
        self.state["updated_at"] = now
        self.state["finished_at"] = now
        self.state["duration_seconds"] = round(
            max(0.0, time.monotonic() - self._started_monotonic),
            3,
        )
        self.state["result"] = result
        self.state["error"] = error
        self._persist()
        self._archive(self.state)
        self._prune_history()
        return dict(self.state)

    def _persist(self) -> None:
        _atomic_write_json(self.last_run_path, self.state)

    def _archive(self, state: dict[str, object]) -> None:
        run_id = str(state.get("run_id", "unknown"))
        _atomic_write_json(self.history_dir / f"{run_id}.json", state)

    def _archive_interrupted_run(self) -> None:
        try:
            previous = json.loads(self.last_run_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return
        if not isinstance(previous, dict) or previous.get("status") != "running":
            return
        now = _utc_now()
        previous["status"] = "failed"
        previous["updated_at"] = now
        previous["finished_at"] = now
        previous["error"] = "execução anterior terminou sem estado final"
        self._archive(previous)

    def _prune_history(self) -> None:
        entries = sorted(
            self.history_dir.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for obsolete in entries[self.history_limit :]:
            try:
                obsolete.unlink()
            except OSError:
                pass


def managed_run_status(result: dict[str, object]) -> str:
    if int(result.get("failed", 0)):
        return "failed"
    if int(result.get("warnings", 0)):
        return "warning"
    return "success"


def read_last_run(state_dir: Path) -> dict[str, object]:
    path = state_dir.expanduser().resolve() / "last-run.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"nenhuma execução registrada em {path.parent}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"estado operacional inválido: {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != "0.1":
        raise ValueError(f"estado operacional incompatível: {path}")
    return value


def run_managed(
    *,
    state_dir: Path,
    lock_file: Path,
    metadata: dict[str, object],
    action: Callable[[RunStateRecorder], dict[str, object]],
    history_limit: int = 50,
) -> dict[str, object]:
    """Execute an index action with process exclusion and persistent state."""

    with ProcessFileLock(lock_file):
        recorder = RunStateRecorder(state_dir, history_limit=history_limit)
        recorder.start(metadata)
        try:
            result = action(recorder)
            status = managed_run_status(result)
            recorder.finish(status, result=result)
            return {**result, "status": status, "run_id": recorder.state["run_id"]}
        except Exception as exc:
            try:
                recorder.finish("failed", error=str(exc))
            except Exception:
                pass
            raise
