from __future__ import annotations

import json
import os
from pathlib import Path

_APP_CONFIG_FILE = Path(__file__).parent / "app_config.json"


def _read_app_config() -> dict:
    if _APP_CONFIG_FILE.exists():
        with open(_APP_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _get_data_dir() -> Path:
    config = _read_app_config()
    raw = config.get("data_dir", "~/CadMarkData")
    return Path(raw).expanduser().resolve()


DATA_DIR = _get_data_dir()
SETTINGS_FILE = DATA_DIR / "settings.json"
QUESTIONS_FILE = DATA_DIR / "questions.json"
STUDENT_INFO_DIR = DATA_DIR / "StudentInfo"


def read_settings() -> dict:
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def write_settings(data: dict) -> None:
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_questions_index() -> list[dict]:
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def write_questions_index(data: list[dict]) -> None:
    with open(QUESTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_question_dir(qid: str) -> Path:
    return DATA_DIR / qid


def get_student_dir(qid: str) -> Path:
    return get_question_dir(qid) / "student"
