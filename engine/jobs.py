# -*- coding: utf-8 -*-
"""后台任务管理（批量模拟）。

沙盒里模拟可能跑几十秒到几分钟，因此用后台线程 + 轮询进度的方式，
前端每 300ms 拉一次 ``/api/jobs/<id>``。

同时最多允许 ``config.MAX_PARALLEL_JOBS`` 个任务真正运行（信号量），
其余排队，避免多个任务同时抢 CPU 让“计算耗时”这项指标失真。
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

import config

_LOCK = threading.Lock()
_SEM = threading.Semaphore(config.MAX_PARALLEL_JOBS)
JOBS: Dict[str, "Job"] = {}
MAX_KEEP = 20


class Job:
    def __init__(self, kind: str, meta: Dict[str, Any]) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.meta = meta
        self.status = "queued"  # queued | running | done | error | cancelled
        self.created = time.time()
        self.started: Optional[float] = None
        self.finished: Optional[float] = None
        self.progress: Dict[str, Any] = {"done": 0, "total": 0, "message": "排队中"}
        self.result: Optional[Any] = None
        self.error: Optional[str] = None
        self.cancel_event = threading.Event()

    # ------------------------------------------------------------------ 序列化
    def snapshot(self, include_result: bool = False) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "meta": self.meta,
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            "progress": self.progress,
            "error": self.error,
            "elapsedSec": round((self.finished or time.time()) - (self.started or self.created), 3),
        }
        if include_result:
            data["result"] = self.result
        return data

    def cancel(self) -> None:
        self.cancel_event.set()
        if self.status == "queued":
            self.status = "cancelled"


def _gc_locked() -> None:
    if len(JOBS) <= MAX_KEEP:
        return
    ordered = sorted(JOBS.values(), key=lambda j: j.created)
    for job in ordered[: len(JOBS) - MAX_KEEP]:
        if job.status in ("done", "error", "cancelled"):
            JOBS.pop(job.id, None)


def submit(kind: str, meta: Dict[str, Any], runner: Callable[[Job], Any]) -> Job:
    """提交后台任务，立即返回 Job 句柄。"""
    job = Job(kind, meta)
    with _LOCK:
        JOBS[job.id] = job
        _gc_locked()

    def _target() -> None:
        _SEM.acquire()
        try:
            if job.cancel_event.is_set():
                job.status = "cancelled"
                job.progress = {**job.progress, "message": "已取消"}
                return
            job.status = "running"
            job.started = time.time()
            job.result = runner(job)
            if job.cancel_event.is_set():
                job.status = "cancelled"
                job.progress = {**job.progress, "message": "已取消"}
            else:
                job.status = "done"
                job.progress = {**job.progress, "message": "完成"}
        except Exception as exc:  # noqa: BLE001
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.progress = {**job.progress, "message": f"出错：{job.error}"}
        finally:
            job.finished = time.time()
            _SEM.release()

    threading.Thread(target=_target, name=f"job-{job.id}", daemon=True).start()
    return job


def get(job_id: str) -> Optional[Job]:
    return JOBS.get(job_id)


def list_jobs(limit: int = 20) -> List[Dict[str, Any]]:
    ordered = sorted(JOBS.values(), key=lambda j: j.created, reverse=True)
    return [j.snapshot() for j in ordered[:limit]]


def cancel(job_id: str) -> bool:
    job = JOBS.get(job_id)
    if not job:
        return False
    job.cancel()
    return True
