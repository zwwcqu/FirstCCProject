import hashlib
import secrets
from datetime import datetime, timedelta

from config import read_settings, write_settings

_sessions: dict[str, datetime] = {}
SESSION_TIMEOUT = timedelta(hours=4)


def verify_password(password: str) -> bool:
    settings = read_settings()
    return password == settings.get("teacher_password", "MechCAD")


def change_password(new_password: str) -> None:
    settings = read_settings()
    settings["teacher_password"] = new_password
    write_settings(settings)


def create_session() -> str:
    token = secrets.token_hex(32)
    _sessions[token] = datetime.now()
    return token


def validate_session(token: str) -> bool:
    if token not in _sessions:
        return False
    if datetime.now() - _sessions[token] > SESSION_TIMEOUT:
        del _sessions[token]
        return False
    _sessions[token] = datetime.now()
    return True


def destroy_session(token: str) -> None:
    _sessions.pop(token, None)
