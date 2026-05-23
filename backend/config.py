from __future__ import annotations

import json
import os
from pathlib import Path

_APP_CONFIG_FILE = Path(__file__).parent.parent / "config" / "app.dirconfig.json"


def _read_app_config() -> dict:
    if not _APP_CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"配置文件缺失: {_APP_CONFIG_FILE}\n"
            f"请检查 config/ 目录，确保 app.dirconfig.json 存在且包含 data_dir 字段"
        )
    with open(_APP_CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_data_dir() -> Path:
    config = _read_app_config()
    raw = config.get("data_dir")
    if not raw:
        raise ValueError(
            f"app.dirconfig.json 中未配置 data_dir，"
            f"请设置数据目录路径（如 ~/CadMarkData）"
        )
    return Path(raw).expanduser().resolve()


DATA_DIR = _get_data_dir()
CONFIG_DIR = _APP_CONFIG_FILE.parent
SETTINGS_FILE = DATA_DIR / "settings.json"
QUESTIONS_FILE = DATA_DIR / "questions.json"
STUDENT_INFO_DIR = DATA_DIR / "StudentInfo"


def _init_data_dir() -> None:
    """首次启动时初始化数据目录：复制 settings 模版、创建空的 questions.json"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STUDENT_INFO_DIR.mkdir(parents=True, exist_ok=True)

    if not SETTINGS_FILE.exists():
        example = CONFIG_DIR / "settings.example.json"
        if example.exists():
            import shutil
            shutil.copy(example, SETTINGS_FILE)
        else:
            raise FileNotFoundError(
                f"缺少 settings 模版文件: {example}\n"
                f"且数据目录下 settings.json 也不存在，无法启动"
            )

    if not QUESTIONS_FILE.exists():
        write_questions_index([])


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


_init_data_dir()
