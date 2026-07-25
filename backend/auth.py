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

from config import read_settings, write_settings, DATA_DIR, read_settings_debug

logger = logging.getLogger(__name__)

# ── Session 超时（从 debug 配置读取，提供内置默认值）─────
def _get_teacher_timeout() -> timedelta:
    minutes = read_settings_debug().get("sessions", {}).get("teacher_timeout_minutes", 30)
    return timedelta(minutes=minutes)

def _get_student_timeout() -> timedelta:
    minutes = read_settings_debug().get("sessions", {}).get("student_timeout_minutes", 1)
    return timedelta(minutes=minutes)

def _get_session_cleanup_interval() -> int:
    return read_settings_debug().get("sessions", {}).get("cleanup_interval_seconds", 600)

# ── 教师 Session 配置 ──────────────────────────────────────
_teacher_sessions: dict[str, datetime] = {}    # 内存缓存：token → 最后活跃时间

# ── 学生 Session 配置 ──────────────────────────────────────
_student_sessions: dict[str, datetime] = {}    # 内存缓存：token → 最后活跃时间

_SESSIONS_DIR = DATA_DIR / ".sessions"        # 持久化目录

# 过期文件清理间隔（秒），避免每次请求都扫描目录
_last_cleanup = 0.0

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


# ── 学生密码管理 ─────────────────────────────────────────

_STUDENT_DEFAULT_PASSWORD = "cad123"  # 学生初始默认密码
_STUDENT_AUTH_DIR = DATA_DIR / "StudentAuth"  # 学生密码文件目录


def _get_student_auth_path(class_name: str) -> Path:
    """返回某班级的学生密码文件路径"""
    return _STUDENT_AUTH_DIR / f"{class_name}.json"


def _read_student_auth(class_name: str) -> dict:
    """读取某班级的学生密码数据，返回 {student_id: {hash, salt, password_changed}}"""
    path = _get_student_auth_path(class_name)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_student_auth(class_name: str, data: dict) -> None:
    """写入某班级的学生密码数据"""
    _STUDENT_AUTH_DIR.mkdir(parents=True, exist_ok=True)
    path = _get_student_auth_path(class_name)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_default_password_hash() -> tuple[str, str]:
    """返回默认密码 cad123 的哈希和盐（用于首次登录校验）"""
    return _hash_password(_STUDENT_DEFAULT_PASSWORD)


def verify_student_password(class_name: str, student_id: str, password: str) -> tuple[bool, bool]:
    """
    校验学生密码。返回 (ok: bool, password_changed: bool)
    - 学生首次登录（auth 文件中无记录）：用默认密码 cad123 校验，成功后自动创建记录
    - 已有记录：正常哈希校验
    """
    auth_data = _read_student_auth(class_name)
    record = auth_data.get(student_id)

    if record is None:
        # 首次登录：校验默认密码
        if password == _STUDENT_DEFAULT_PASSWORD:
            # 自动创建记录（未修改密码状态）
            default_hash, default_salt = get_default_password_hash()
            auth_data[student_id] = {
                "password_hash": default_hash,
                "salt": default_salt,
                "password_changed": False,
            }
            _write_student_auth(class_name, auth_data)
            logger.info(f"学生 {student_id} 首次登录，密码待修改")
            return True, False
        return False, False

    # 已有记录：正常哈希校验
    stored_hash = record.get("password_hash", "")
    stored_salt = record.get("salt", "")
    if not stored_hash or not stored_salt:
        return False, False

    hashed, _ = _hash_password(password, stored_salt)
    if secrets.compare_digest(hashed, stored_hash):
        return True, record.get("password_changed", False)
    return False, False


def change_student_password(class_name: str, student_id: str, new_password: str) -> bool:
    """修改学生密码。返回是否成功"""
    auth_data = _read_student_auth(class_name)
    if student_id not in auth_data:
        # 如果没有记录，创建一个
        hashed, salt = _hash_password(new_password)
        auth_data[student_id] = {
            "password_hash": hashed,
            "salt": salt,
            "password_changed": True,
        }
    else:
        hashed, salt = _hash_password(new_password)
        auth_data[student_id]["password_hash"] = hashed
        auth_data[student_id]["salt"] = salt
        auth_data[student_id]["password_changed"] = True

    _write_student_auth(class_name, auth_data)
    logger.info(f"学生 {student_id} 密码已修改")
    return True


# ── Session 文件持久化辅助 ──────────────────────────────

def _session_file(token: str):
    """返回 session token 对应的持久化文件路径"""
    return _SESSIONS_DIR / f"{token}.json"


def _cleanup_expired_sessions() -> None:
    """清理过期的 session 文件（限频调用）"""
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < _get_session_cleanup_interval():
        return
    _last_cleanup = now
    if not _SESSIONS_DIR.exists():
        return
    for f in _SESSIONS_DIR.iterdir():
        try:
            if f.suffix == ".json":
                data = json.loads(f.read_text(encoding="utf-8"))
                created = datetime.fromisoformat(data["created_at"])
                timeout = _get_student_timeout() if data.get("type") == "student" else _get_teacher_timeout()
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
    return _validate(token, _teacher_sessions, _get_teacher_timeout())


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
    return _validate(token, _student_sessions, _get_student_timeout())


def get_student_session(token: str) -> dict | None:
    """获取学生 session 关联的姓名和学号"""
    sf = _session_file(token)
    if not sf.exists():
        return None
    try:
        return json.loads(sf.read_text(encoding="utf-8"))
    except Exception:
        return None
