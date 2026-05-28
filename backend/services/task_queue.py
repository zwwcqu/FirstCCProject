"""LLM 任务队列 + 线程池。云端模型 3 并发，本地模型 1 并发。教师任务优先级高于学生。"""
from __future__ import annotations

import logging
import threading
import time
from queue import PriorityQueue

from config import read_settings

logger = logging.getLogger(__name__)

# 队列大小无限制
_queue: PriorityQueue = PriorityQueue()
_seq = 0
_seq_lock = threading.Lock()

_workers: list[threading.Thread] = []
_running = False
_lock = threading.Lock()


def _next_seq() -> int:
    global _seq
    with _seq_lock:
        _seq += 1
        return _seq


def _is_local_model(base_url: str) -> bool:
    """根据 API 地址判断是否为本地模型"""
    return any(x in base_url for x in ("127.0.0.1", "localhost", "192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.2", "172.30.", "172.31."))


def _detect_concurrency() -> int:
    """从当前激活模型配置读取并发数，上限 5"""
    settings = read_settings()
    models = settings.get("models", [])
    idx = settings.get("llm_active", 0)
    if models and 0 <= idx < len(models):
        configured = models[idx].get("concurrency", 1)
        if isinstance(configured, int) and configured >= 1:
            return min(configured, 5)
    return 1


def enqueue(priority: int, func, status_callback=None, *args, **kwargs):
    """
    投递任务到队列。
    priority: 0=教师（最高）, 10=学生分析, 10=学生评分
    status_callback: 可选的状态回调，接受 (status: str) 参数
    """
    seq = _next_seq()
    _queue.put((priority, seq, func, args, kwargs, status_callback))
    logger.debug(f"任务入队: priority={priority} seq={seq}")


def _worker():
    """工作线程：循环从队列取任务执行"""
    while _running:
        try:
            priority, seq, func, args, kwargs, callback = _queue.get(timeout=1)
        except Exception:
            continue  # timeout, 重新检查 _running
        try:
            logger.info(f"开始执行任务: priority={priority} seq={seq}")
            if callback:
                callback("running")
            func(*args, **kwargs)
            if callback:
                callback("done")
        except Exception as e:
            logger.error(f"任务执行失败: {e}")
            if callback:
                callback(f"error:{e}")
        finally:
            _queue.task_done()


def start():
    """启动线程池。在应用启动时调用"""
    global _running, _workers
    with _lock:
        if _running:
            return
        concurrency = _detect_concurrency()
        _running = True
        for i in range(concurrency):
            t = threading.Thread(target=_worker, name=f"llm-worker-{i}", daemon=True)
            t.start()
            _workers.append(t)
        logger.info(f"LLM 任务队列已启动，并发数: {concurrency}")


def stop():
    """停止线程池（应用关闭时调用）"""
    global _running
    with _lock:
        _running = False
    for t in _workers:
        t.join(timeout=5)
    _workers.clear()
    logger.info("LLM 任务队列已停止")


def get_queue_info() -> dict:
    """返回队列状态（供调试/监控）"""
    return {
        "queue_size": _queue.qsize(),
        "workers": len(_workers),
        "running": _running,
        "concurrency": _detect_concurrency(),
    }
