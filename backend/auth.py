"""
教师身份认证模块。

功能：
- 密码验证与修改（PBKDF2-SHA256 哈希存储，防时序攻击的常量比较）
- Session 管理（创建、校验、销毁，支持多 worker 文件共享）
- 首次启动自动将明文密码迁移为哈希

Session 存储：
- 内存优先（快速路径），文件后备（多 worker 共享）
- 文件位置：DATA_DIR/.sessions/{token}.json
- 超时时间：30 秒无操作断开
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from datetime import datetime, timedelta

from config import read_settings, write_settings, DATA_DIR

logger = logging.getLogger(__name__)

# ── 教师 Session 配置 ──────────────────────────────────────
_teacher_sessions: dict[str, datetime] = {}    # 内存缓存：token → 最后活跃时间
TEACHER_SESSION_TIMEOUT = timedelta(minutes=30)  # 30分钟无操作自动断开

# ── 学生 Session 配置 ──────────────────────────────────────
_student_sessions: dict[str, datetime] = {}    # 内存缓存：token → 最后活跃时间
STUDENT_SESSION_TIMEOUT = timedelta(minutes=1)  # 1分钟无操作自动断开

_SESSIONS_DIR = DATA_DIR / ".sessions"        # 持久化目录

# 过期文件清理间隔（秒），避免每次请求都扫描目录
_last_cleanup = 0.0
_CLEANUP_INTERVAL = 600  # 10 分钟

# ── 密码哈希参数 ─────────────────────────────────────────
_PBKDF2_ITERATIONS = 100_000                   # PBKDF2 迭代次数
_HASH_ALGORITHM = "sha256"                     # 哈希算法


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """使用 PBKDF2-SHA256 对密码做哈希。返回 (hash_hex, salt_hex)。"""
    if salt is None:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        _HASH_ALGORITHM,
        password.encode("utf-8"),
        salt.encode("utf-8"),
        _PBKDF2_ITERATIONS,
    )
    return key.hex(), salt


def _migrate_plaintext_password(plain: str) -> None:
    """将 settings.json 中的明文密码迁移为哈希格式"""
    salt = secrets.token_hex(16)
    hashed, salt = _hash_password(plain, salt)
    settings = read_settings()
    settings["teacher_password"] = hashed
    settings["password_salt"] = salt
    write_settings(settings)
    logger.info("已自动将明文密码迁移为 PBKDF2-SHA256 哈希存储")


def verify_password(password: str) -> bool:
    """校验教师密码。首次调用时自动迁移明文密码。"""
    settings = read_settings()
    stored = settings.get("teacher_password", "")
    salt = settings.get("password_salt", "")

    # 旧版明文密码 —— 自动迁移为哈希
    if not salt:
        if password == stored:
            _migrate_plaintext_password(stored)
            return True
        return False

    # 新版哈希密码 —— 常量时间比较防时序攻击
    hashed, _ = _hash_password(password, salt)
    return secrets.compare_digest(hashed, stored)


def change_password(new_password: str) -> None:
    """修改教师密码（PBKDF2-SHA256 哈希存储）"""
    salt = secrets.token_hex(16)
    hashed, salt = _hash_password(new_password, salt)
    settings = read_settings()
    settings["teacher_password"] = hashed
    settings["password_salt"] = salt
    write_settings(settings)
    logger.info("教师密码已更新")


# ── Session 文件持久化辅助 ──────────────────────────────

def _session_file(token: str):
    """返回 session token 对应的持久化文件路径"""
    return _SESSIONS_DIR / f"{token}.json"


def _cleanup_expired_sessions() -> None:
    """清理过期的 session 文件（限频调用）"""
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < _CLEANUP_INTERVAL:
        return
    _last_cleanup = now
    if not _SESSIONS_DIR.exists():
        return
    for f in _SESSIONS_DIR.iterdir():
        try:
            if f.suffix == ".json":
                data = json.loads(f.read_text(encoding="utf-8"))
                created = datetime.fromisoformat(data["created_at"])
                timeout = STUDENT_SESSION_TIMEOUT if data.get("type") == "student" else TEACHER_SESSION_TIMEOUT
                if datetime.now() - created > timeout:
                    f.unlink(missing_ok=True)
        except Exception:
            pass


# ── Session 公开接口 ─────────────────────────────────────

def create_session() -> str:
    """创建教师 session，写入内存和文件"""
    _cleanup_expired_sessions()
    token = secrets.token_hex(32)
    now = datetime.now()
    _teacher_sessions[token] = now

    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    _session_file(token).write_text(
        json.dumps({"created_at": now.isoformat(), "type": "teacher"}), encoding="utf-8"
    )
    logger.info(f"教师 Session 已创建: {token[:8]}…")
    return token


def validate_session(token: str) -> bool:
    """校验教师 session 是否有效"""
    return _validate(token, _teacher_sessions, TEACHER_SESSION_TIMEOUT)


def destroy_session(token: str) -> None:
    """销毁 session（内存 + 文件）"""
    _teacher_sessions.pop(token, None)
    _student_sessions.pop(token, None)
    _session_file(token).unlink(missing_ok=True)
    logger.info(f"Session 已销毁: {token[:8]}…")


def _validate(token: str, store: dict[str, datetime], timeout: timedelta) -> bool:
    """校验 session。内存优先，文件后备"""
    # 1. 内存命中
    if token in store:
        if datetime.now() - store[token] > timeout:
            del store[token]
            _session_file(token).unlink(missing_ok=True)
            return False
        store[token] = datetime.now()  # 续期
        return True

    # 2. 文件后备
    sf = _session_file(token)
    if not sf.exists():
        return False
    try:
        data = json.loads(sf.read_text(encoding="utf-8"))
        created = datetime.fromisoformat(data["created_at"])
    except Exception:
        return False

    if datetime.now() - created > timeout:
        sf.unlink(missing_ok=True)
        return False

    store[token] = datetime.now()
    return True


# ── 学生 Session ───────────────────────────────────────────

def create_student_session(name: str, student_id: str) -> str:
    """创建学生 session，关联姓名和学号"""
    _cleanup_expired_sessions()
    token = secrets.token_hex(32)
    now = datetime.now()
    _student_sessions[token] = now

    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    _session_file(token).write_text(
        json.dumps({"created_at": now.isoformat(), "type": "student", "name": name, "student_id": student_id}), encoding="utf-8"
    )
    logger.info(f"学生 Session 已创建: {name}({student_id}) → {token[:8]}…")
    return token


def validate_student_session(token: str) -> bool:
    """校验学生 session 是否有效"""
    return _validate(token, _student_sessions, STUDENT_SESSION_TIMEOUT)


def get_student_session(token: str) -> dict | None:
    """获取学生 session 关联的姓名和学号"""
    sf = _session_file(token)
    if not sf.exists():
        return None
    try:
        return json.loads(sf.read_text(encoding="utf-8"))
    except Exception:
        return None
