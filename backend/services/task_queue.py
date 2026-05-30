"""LLM 任务队列 + 线程池。支持任务去重（同 key 不重复入队）+ 任务信息追踪 + 队列清空。"""
from __future__ import annotations

import logging
import threading
import time
from queue import PriorityQueue

from config import read_settings

logger = logging.getLogger(__name__)

_queue: PriorityQueue = PriorityQueue()
_seq = 0
_seq_lock = threading.Lock()

_workers: list[threading.Thread] = []
_running = False
_lock = threading.Lock()

# ── 任务追踪 & 去重 ────────────────────────────────────────
_active_keys: set[str] = set()       # 活跃任务 key（含排队中 + 执行中）
_active_items: list[dict] = []        # 活跃任务信息列表，_status: "queued" | "running"
_keys_lock = threading.Lock()


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


def enqueue(priority: int, func, status_callback=None, *,
            task_key: str = "", task_info: dict | None = None):
    """
    投递任务到队列。

    priority: 0=教师（最高）, 5=教师批量, 10=学生
    task_key: 去重键，相同 key 的任务不会重复入队（留空则不去重）
    task_info: 任务元数据，用于查询队列状态（如 {"type":"analyze","qid":"02","name":"张三","student_id":"123"}）
    status_callback: 可选的状态回调，接受 (status: str) 参数
    """
    # 去重检查
    if task_key:
        with _keys_lock:
            if task_key in _active_keys:
                logger.info(f"任务已在队列中，跳过重复提交: {task_key}")
                return
            _active_keys.add(task_key)
            item = {"_key": task_key, "_priority": priority, "_time": time.time(), "_status": "queued"}
            if task_info:
                item.update(task_info)
            _active_items.append(item)

    seq = _next_seq()
    _queue.put((priority, seq, func, task_key, status_callback))
    if task_key:
        logger.info(f"任务入队: {task_key} priority={priority}")


def _worker():
    """工作线程：循环从队列取任务执行"""
    while _running:
        try:
            priority, seq, func, task_key, callback = _queue.get(timeout=1)
        except Exception:
            continue  # timeout, 重新检查 _running

        # 标记为执行中
        if task_key:
            with _keys_lock:
                for item in _active_items:
                    if item.get("_key") == task_key:
                        item["_status"] = "running"
                        break

        try:
            logger.info(f"开始执行任务: priority={priority} seq={seq} key={task_key}")
            if callback:
                callback("running")
            func()
            if callback:
                callback("done")
        except Exception as e:
            logger.error(f"任务执行失败: {e}")
            if callback:
                callback(f"error:{e}")
        finally:
            # 清理去重 key
            if task_key:
                with _keys_lock:
                    _active_keys.discard(task_key)
                    for i, item in enumerate(_active_items):
                        if item.get("_key") == task_key:
                            _active_items.pop(i)
                            break
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
    """返回队列状态（含活跃任务列表，区分排队/执行中）"""
    with _keys_lock:
        items = list(_active_items)
    running = sum(1 for it in items if it.get("_status") == "running")
    queued = sum(1 for it in items if it.get("_status") == "queued")
    return {
        "queue_size": _queue.qsize(),
        "workers": len(_workers),
        "running": _running,
        "concurrency": _detect_concurrency(),
        "total": len(items),
        "running_count": running,
        "queued_count": queued,
        "items": items,
    }


def clear_queue() -> int:
    """清空队列中所有等待中的任务（不影响正在执行的任务）。返回清除数量"""
    cleared = 0

    # 收集需要保留的 task_key（正在执行中的）
    with _keys_lock:
        running_keys = {item["_key"] for item in _active_items if item.get("_status") == "running"}

    # 从 PriorityQueue 中取出所有排队任务，但保留 running 任务需要重新入队
    drained: list = []
    while not _queue.empty():
        try:
            drained.append(_queue.get_nowait())
        except Exception:
            break

    for item in drained:
        priority, seq, func, task_key, callback = item
        if task_key and task_key in running_keys:
            # 正在执行的任务已经不在队列中（被 get 取走了），这里理论上不会遇到
            _queue.put(item)
        else:
            _queue.task_done()
            cleared += 1
            if task_key:
                with _keys_lock:
                    _active_keys.discard(task_key)
                    for i, it in enumerate(_active_items):
                        if it.get("_key") == task_key:
                            _active_items.pop(i)
                            break

    # 清理 _active_items 中状态为 queued 的条目
    with _keys_lock:
        removed = []
        for item in _active_items[:]:
            if item.get("_status") == "queued":
                _active_keys.discard(item.get("_key", ""))
                _active_items.remove(item)
                removed.append(item.get("_key", ""))

    cleared += len(removed)
    if cleared:
        logger.info(f"队列已清空，移除 {cleared} 个等待任务: {removed}")
    return cleared
