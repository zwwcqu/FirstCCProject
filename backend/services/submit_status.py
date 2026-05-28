"""提交状态追踪服务。内存字典 + 简单超时清理。"""
from __future__ import annotations

import threading
import time
import logging

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_status: dict[str, dict] = {}  # key = "{qid}:{name}:{student_id}"


def _make_key(qid: str, name: str, student_id: str) -> str:
    return f"{qid}:{name}:{student_id}"


def set_status(qid: str, name: str, student_id: str, step: str, status: str, error_message: str = "", student_filename: str = ""):
    with _lock:
        key = _make_key(qid, name, student_id)
        existing = _status.get(key, {})
        _status[key] = {
            **existing,
            "step": step,
            "status": status,
            "error_message": error_message,
            "student_filename": student_filename or existing.get("student_filename", ""),
            "ts": time.time(),
        }


def set_file_data(qid: str, name: str, student_id: str, data: bytes, filename: str):
    """暂存文件 bytes（测试模式用，供 analyze→grade 两步间传递）"""
    with _lock:
        key = _make_key(qid, name, student_id)
        existing = _status.get(key, {})
        _status[key] = {**existing, "file_data": data, "file_filename": filename, "ts": time.time()}


def get_file_data(qid: str, name: str, student_id: str) -> tuple[bytes | None, str]:
    """取出暂存的文件 bytes 和文件名"""
    with _lock:
        s = _status.get(_make_key(qid, name, student_id))
        if s is None:
            return None, ""
        return s.get("file_data"), s.get("file_filename", "")


def get_status(qid: str, name: str, student_id: str) -> dict:
    with _lock:
        s = _status.get(_make_key(qid, name, student_id))
        if s is None:
            return {"step": "", "status": "unknown", "student_filename": ""}
        return {
            "step": s["step"],
            "status": s["status"],
            "error_message": s.get("error_message", ""),
            "student_filename": s.get("student_filename", ""),
        }


CLEANUP_INTERVAL = 300  # 5分钟清理一次过期记录


def _cleanup():
    """清理超过 30 分钟的旧状态记录"""
    now = time.time()
    with _lock:
        stale = [k for k, v in _status.items() if now - v["ts"] > 1800]
        for k in stale:
            del _status[k]
    if stale:
        logger.debug(f"清理了 {len(stale)} 条过期提交状态")


def _start_cleanup_timer():
    t = threading.Timer(CLEANUP_INTERVAL, _start_cleanup_timer)
    t.daemon = True
    t.start()
    _cleanup()


_start_cleanup_timer()
