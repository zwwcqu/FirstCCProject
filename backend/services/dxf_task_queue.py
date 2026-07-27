"""
DXF 处理任务队列 — 串行队列（concurrency=1），独立于 LLM 队列。
教师参考图和学生作业的 DXF 提取/渲染统一走这里，避免阻塞 FastAPI 请求线程。
"""

from __future__ import annotations

import logging
import queue
import threading

logger = logging.getLogger(__name__)

_queue: queue.Queue = queue.Queue()
_worker: threading.Thread | None = None
_running = False

# 任务追踪（用于去重 + 状态查询）
_active_keys: set[str] = set()
_lock = threading.Lock()


def enqueue(func, *, task_key: str = ""):
    """投递任务到 DXF 队列。相同 task_key 不重复入队。"""
    if task_key:
        with _lock:
            if task_key in _active_keys:
                logger.info(f"DXF 任务已在队列中: {task_key}")
                return
            _active_keys.add(task_key)
    _queue.put((func, task_key))


def _worker_loop():
    while _running:
        try:
            func, task_key = _queue.get(timeout=1)
        except queue.Empty:
            continue
        try:
            logger.info(f"DXF 任务开始: {task_key}")
            func()
        except Exception as e:
            logger.error(f"DXF 任务失败 [{task_key}]: {e}")
        finally:
            if task_key:
                with _lock:
                    _active_keys.discard(task_key)
            _queue.task_done()


def start():
    global _worker, _running
    _running = True
    _worker = threading.Thread(target=_worker_loop, name="dxf-worker", daemon=True)
    _worker.start()
    logger.info("DXF 串行队列已启动（concurrency=1）")


def stop():
    global _running
    _running = False
    if _worker:
        _worker.join(timeout=5)
    logger.info("DXF 串行队列已停止")


def get_queue_info() -> dict:
    with _lock:
        return {
            "queue_size": _queue.qsize(),
            "running": _running,
            "active_tasks": list(_active_keys),
        }
