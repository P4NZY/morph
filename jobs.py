"""Tiny in-memory background job runner, so slow work can report progress."""

from __future__ import annotations

import shutil
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from converters import ConversionError

KEEP_SECONDS = 30 * 60


@dataclass
class Job:
    id: str
    status: str = "running"          # running | done | error
    progress: float = 0.0
    message: str = "Starting…"
    error: str | None = None
    result: bytes | None = None       # small results are kept in memory…
    result_path: Path | None = None   # …large ones (e.g. videos) stay on disk
    workdir: Path | None = None       # temporary folder, removed when the job is finished with
    filename: str | None = None
    mimetype: str = "application/octet-stream"
    meta: dict[str, Any] = field(default_factory=dict)
    updated: float = field(default_factory=time.time)

    def report(self, progress: float, message: str) -> None:
        self.progress = max(0.0, min(1.0, progress))
        self.message = message
        self.updated = time.time()

    def size(self) -> int | None:
        if self.result is not None:
            return len(self.result)
        if self.result_path is not None and self.result_path.exists():
            return self.result_path.stat().st_size
        return None

    def discard_files(self) -> None:
        if self.workdir is not None:
            shutil.rmtree(self.workdir, ignore_errors=True)

    def public(self) -> dict:
        return {
            "id": self.id, "status": self.status, "progress": self.progress,
            "message": self.message, "error": self.error, "filename": self.filename,
            "size": self.size(), **self.meta,
        }


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def start(work: Callable[[Job], None], workdir: Path | None = None) -> Job:
    _cleanup()
    job = Job(id=uuid.uuid4().hex, workdir=workdir)
    with _lock:
        _jobs[job.id] = job

    def run():
        try:
            work(job)
            job.status = "done"
            job.progress = 1.0
        except ConversionError as exc:
            job.status, job.error = "error", str(exc)
            job.discard_files()
        except Exception as exc:  # pragma: no cover - surfaced to the UI
            traceback.print_exc()
            job.status, job.error = "error", f"Something went wrong: {type(exc).__name__}: {exc}"
            job.discard_files()
        job.updated = time.time()

    threading.Thread(target=run, daemon=True).start()
    return job


def get(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def pop(job_id: str) -> Job | None:
    with _lock:
        return _jobs.pop(job_id, None)


def _cleanup() -> None:
    cutoff = time.time() - KEEP_SECONDS
    with _lock:
        expired = [j for j in _jobs.values() if j.status != "running" and j.updated < cutoff]
        for job in expired:
            del _jobs[job.id]
    for job in expired:
        job.discard_files()
