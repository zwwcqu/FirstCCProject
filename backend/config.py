"""
全局配置与路径管理。

功能：
- 从 config/app.dirconfig.json 读取数据目录路径
- 提供 settings.json / questions.json / StudentInfo 的读写接口
- 题目目录和学生提交目录的路径计算
- 首次启动初始化数据目录（复制模板、创建空索引）
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 数据目录配置 ─────────────────────────────────────────
_APP_CONFIG_FILE = Path(__file__).parent.parent / "config" / "app.dirconfig.json"


def _read_app_config() -> dict:
    """读取 app.dirconfig.json 配置文件"""
    if not _APP_CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"配置文件缺失: {_APP_CONFIG_FILE}\n"
            f"请检查 config/ 目录，确保 app.dirconfig.json 存在且包含 data_dir 字段"
        )
    with open(_APP_CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_data_dir() -> Path:
    """从配置文件解析数据目录路径（展开 ~ 和相对路径）"""
    config = _read_app_config()
    raw = config.get("data_dir")
    if not raw:
        raise ValueError(
            f"app.dirconfig.json 中未配置 data_dir，"
            f"请设置数据目录路径（如 ~/CadMarkData）"
        )
    return Path(raw).expanduser().resolve()


# 全局数据目录路径
DATA_DIR = _get_data_dir()
CONFIG_DIR = _APP_CONFIG_FILE.parent               # config/ 目录（模板文件所在地）
SETTINGS_FILE = DATA_DIR / "settings.json"          # 系统设置（LLM配置、密码等）
QUESTIONS_FILE = DATA_DIR / "questions.json"        # 题目索引列表
STUDENT_INFO_DIR = DATA_DIR / "StudentInfo"         # 学生名单目录


def _init_data_dir() -> None:
    """首次启动时初始化数据目录：复制 settings 模版、创建空的 questions.json"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STUDENT_INFO_DIR.mkdir(parents=True, exist_ok=True)

    # 清理残留的临时文件（写入时崩溃可能导致）
    tmp_file = SETTINGS_FILE.with_suffix(".tmp")
    if tmp_file.exists():
        tmp_file.unlink()
        logger.info("已清理残留的 settings.tmp")

    if not SETTINGS_FILE.exists():
        example = CONFIG_DIR / "settings.example.json"
        if example.exists():
            import shutil
            shutil.copy(example, SETTINGS_FILE)
            logger.info(f"已从模板创建 settings.json")
        else:
            raise FileNotFoundError(
                f"缺少 settings 模版文件: {example}\n"
                f"且数据目录下 settings.json 也不存在，无法启动"
            )

    if not QUESTIONS_FILE.exists():
        write_questions_index([])
        logger.info("已创建空的 questions.json")


# ── Settings 读写 ────────────────────────────────────────

def read_settings() -> dict:
    """读取系统设置 JSON"""
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def write_settings(data: dict) -> None:
    """写入系统设置 JSON（原子写入：先写临时文件再替换，防止中断导致数据丢失）"""
    tmp_path = SETTINGS_FILE.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(SETTINGS_FILE)  # os.replace 在 Unix 上是原子操作


# ── 题目索引读写 ────────────────────────────────────────

def read_questions_index() -> list[dict]:
    """读取题目索引列表"""
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def write_questions_index(data: list[dict]) -> None:
    """写入题目索引列表"""
    with open(QUESTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── 路径工具 ────────────────────────────────────────────

def get_question_dir(qid: str) -> Path:
    """返回指定题号的数据目录路径"""
    return DATA_DIR / qid


def get_student_dir(qid: str) -> Path:
    """返回指定题目下学生提交目录路径"""
    return get_question_dir(qid) / "student"
