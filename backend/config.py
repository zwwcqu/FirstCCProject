"""
全局配置与路径管理。

功能：
- 从 config/app.dirconfig.json 读取数据目录路径
- 提供 settings.json / questions.json / StudentInfo 的读写接口
- 题目目录和学生提交目录的路径计算
- 首次启动初始化数据目录（复制模板、创建空索引）
- 提供各业务参数的默认值和读取函数，避免代码中硬编码具体数值
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
SETTINGS_DEBUG_FILE = DATA_DIR / "settings_debug.json"  # 调试/运维参数
QUESTIONS_FILE = DATA_DIR / "questions.json"        # 题目索引列表
STUDENT_INFO_DIR = DATA_DIR / "StudentInfo"         # 学生名单目录
TEMPLATES_DATA_DIR = DATA_DIR / "templates"         # 全局模板工作副本目录

# 4 种工程图识读模板文件名
TEMPLATE_NAMES = [
    "零件图识读模板.txt",
    "装配图识读模板.txt",
    "平面图识读模板.txt",
    "组合体三视图识读模板.txt",
]

# 文件校验常量
PDF_MAGIC = b"%PDF"  # PDF 文件头魔数


def _copy_template_csv(template_name: str, subdir: str, target_name: str) -> None:
    """复制 config/ 下的 CSV 模板到数据目录对应子目录（如果目标不存在）"""
    src = CONFIG_DIR / template_name
    dest_dir = DATA_DIR / subdir
    dest = dest_dir / target_name
    if src.exists() and not dest.exists():
        dest_dir.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy(src, dest)
        logger.info(f"已复制模板: {template_name} → {subdir}/{target_name}")


def _init_data_dir() -> None:
    """首次启动时初始化数据目录：创建子目录、复制模板、初始化配置文件"""
    import shutil

    # ── 1. 创建所有必要的子目录 ──
    for d in [
        DATA_DIR,
        STUDENT_INFO_DIR,
        TEMPLATES_DATA_DIR,
        DATA_DIR / "TeacherInfo",
        DATA_DIR / "TeacherAuth",
        DATA_DIR / "StudentAuth",
        DATA_DIR / ".sessions",
        DATA_DIR / "backup",
    ]:
        d.mkdir(parents=True, exist_ok=True)

    # ── 2. 清理残留临时文件 ──
    tmp_file = SETTINGS_FILE.with_suffix(".tmp")
    if tmp_file.exists():
        tmp_file.unlink()
        logger.info("已清理残留的 settings.tmp")

    # ── 3. settings.json ──
    if not SETTINGS_FILE.exists():
        example = CONFIG_DIR / "settings.example.json"
        if example.exists():
            shutil.copy(example, SETTINGS_FILE)
            logger.info("已从模板创建 settings.json")
        else:
            raise FileNotFoundError(
                f"缺少 settings 模版文件: {example}\n"
                f"且数据目录下 settings.json 也不存在，无法启动"
            )
    else:
        _migrate_settings()

    # ── 4. settings_debug.json ──
    if not SETTINGS_DEBUG_FILE.exists():
        example_debug = CONFIG_DIR / "settings_debug.example.json"
        if example_debug.exists():
            shutil.copy(example_debug, SETTINGS_DEBUG_FILE)
            logger.info("已从模板创建 settings_debug.json")
        else:
            logger.warning("缺少调试配置模版，将使用内置默认值")

    # ── 5. questions.json ──
    if not QUESTIONS_FILE.exists():
        write_questions_index([])
        logger.info("已创建空的 questions.json")

    # ── 6. CSV 模板 → 数据目录 ──
    _copy_template_csv("教师名单模版.csv", "TeacherInfo", "教师名单.csv")
    _copy_template_csv("学生名单模版.csv", "StudentInfo", "_模版.csv")

    # ── 7. 识读模板 → data/templates/ ──
    for name in TEMPLATE_NAMES:
        dest = TEMPLATES_DATA_DIR / name
        if not dest.exists():
            src = CONFIG_DIR / name
            if src.exists():
                shutil.copy(src, dest)
                logger.info(f"已复制模板: {name}")
            else:
                logger.warning(f"模板文件缺失: {name}（config/ 目录中不存在）")


def _migrate_settings() -> None:
    """自动补齐 settings.json 中缺失的区块（从 example 模板合并，不覆盖已有值）"""
    example = CONFIG_DIR / "settings.example.json"
    if not example.exists():
        return
    try:
        example_data = json.loads(example.read_text(encoding="utf-8"))
    except Exception:
        return

    migrate_keys = ["llm_params", "image_params", "grade_thresholds",
                    "prompt_templates", "scoring_templates"]

    current = read_settings()
    changed = False
    for key in migrate_keys:
        if key in example_data and key not in current:
            current[key] = {k: v for k, v in example_data[key].items() if not k.startswith("_")}
            changed = True
            logger.info(f"settings.json 自动补齐缺失区块: {key}")

    if changed:
        write_settings(current)


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


# ── Debug Settings 读写 ─────────────────────────────────

def read_settings_debug() -> dict:
    """读取调试/运维参数 JSON，文件缺失时返回内置默认值"""
    if SETTINGS_DEBUG_FILE.exists():
        try:
            with open(SETTINGS_DEBUG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"settings_debug.json 解析失败，使用内置默认值: {e}")
    return _settings_debug_defaults()


def write_settings_debug(data: dict) -> None:
    """写入调试参数 JSON（原子写入）"""
    tmp_path = SETTINGS_DEBUG_FILE.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(SETTINGS_DEBUG_FILE)


def _settings_debug_defaults() -> dict:
    """settings_debug.json 的内置默认值（文件缺失时的兜底）"""
    return {
        "rate_limit": {"window_seconds": 60, "max_requests": 50},
        "sessions": {"teacher_timeout_minutes": 30, "student_timeout_minutes": 1, "cleanup_interval_seconds": 600},
        "submit_status": {"expire_seconds": 1800, "cleanup_interval_seconds": 300},
        "task_queue": {"max_concurrency": 5, "worker_poll_timeout": 1, "shutdown_timeout": 5},
        "request_timeouts": {"upload": 120, "teacher_questions": 120, "analyze": 10, "grade": 10, "default": 60},
        "photo_detection": {"enabled": False, "render_dpi": 120, "sample_size": 200, "color_threshold": 18,
                           "white_threshold": 250, "color_rate_max": 0.05, "white_rate_min": 0.75,
                           "aspect_ratio_min": 1.39, "aspect_ratio_max": 1.43},
        "pdf_validation_dpi": 72,
        "frontend": {"max_file_mb": 20, "poll_interval_ms": 2000, "poll_timeout_ms": 120000},
        "log_level": "INFO",
    }


# ── 业务参数读取（均提供默认值，settings.json 缺失字段也不报错）─

def get_llm_params() -> dict:
    """读取 LLM 调用参数"""
    defaults = {"temperature": 0.1, "max_tokens": 4096, "enable_thinking": False, "client_timeout": 120}
    return {**defaults, **read_settings().get("llm_params", {})}


def get_image_params() -> dict:
    """读取图像处理参数"""
    defaults = {
        "analysis_max_size": 3508,
        "analysis_dpi": 150,
        "phase1_max_size": 768,
        "phase1_jpeg_quality": 55,
        "analysis_jpeg_quality": 85,
    }
    return {**defaults, **read_settings().get("image_params", {})}


def _clean_settings(data: dict) -> dict:
    """过滤 settings.json 中以 _ 开头的注释字段"""
    return {k: v for k, v in data.items() if not k.startswith("_")}


def get_grade_thresholds() -> list[tuple[float, str]]:
    """
    读取评分等级阈值，返回从高到低排序的 (分数, 等级) 列表。
    settings.json 中为 {"A+": 90, "A": 85, ...} 格式。
    """
    defaults = {"A+": 90, "A": 85, "B+": 80, "B": 75, "C+": 68.75, "C": 62.5, "D+": 56.25, "D": 50}
    thresholds = _clean_settings(read_settings().get("grade_thresholds", {}) or {})
    merged = {**defaults, **thresholds}
    # 按分数降序排列
    sorted_items = sorted(merged.items(), key=lambda x: float(x[1]), reverse=True)
    return [(float(v), k) for k, v in sorted_items]


def get_prompt_templates() -> dict:
    """读取所有 LLM 提示词模板（评分引导语部分，分析模板已改为文件管理）"""
    defaults = {
        "grading_guide": "你是一位工程图批阅老师。请对比学生图和参考图，从结构完整性和标注准确性两方面综合评分。",
    }
    stored = _clean_settings(read_settings().get("prompt_templates", {}) or {})
    relevant = {k: v for k, v in stored.items() if k in defaults}
    return {**defaults, **relevant}


def get_scoring_templates() -> dict:
    """读取评分模板默认值（新建题目时预填）"""
    defaults = {
        "phase1": "较为宽松比较相似情况. 和参考图形整体比较, 很相似给100%, 一般相似给90%, 不怎么相似给80%, 有点点相似给60%, 绝不相似给0%.",
        "phase2": "1. 尺寸相似率占总分40%\n2. 尺寸公差相似率占总分20%\n3. 粗糙度相似率占总分20%\n4. 形位公差相似率占总分10%\n5. 技术要求相似度占总分10%",
    }
    stored = _clean_settings(read_settings().get("scoring_templates", {}) or {})
    return {**defaults, **stored}


def get_debug_param(section: str, key: str, default=None):
    """读取调试/运维参数中的单个值"""
    debug = read_settings_debug()
    return debug.get(section, {}).get(key, default)


# ── 模板读取 ───────────────────────────────────────────

def get_template(template_name: str) -> str:
    """读取全局模板内容：data/templates/ → config/ 回退"""
    path = TEMPLATES_DATA_DIR / template_name
    if path.exists():
        return path.read_text(encoding="utf-8")
    fallback = CONFIG_DIR / template_name
    if fallback.exists():
        return fallback.read_text(encoding="utf-8")
    raise FileNotFoundError(f"找不到模板文件: {template_name}")


def get_question_template(qid: str) -> str:
    """读取题目的识读模板：data/{qid}/识读模板.txt → 默认零件图模板"""
    path = DATA_DIR / qid / "识读模板.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    # 不存在则回退到全局零件图模板
    return get_template("零件图识读模板.txt")


def list_templates() -> dict[str, str]:
    """列出所有全局模板的名称和内容"""
    return {name: get_template(name) for name in TEMPLATE_NAMES}


def save_template(template_name: str, content: str) -> None:
    """保存全局模板到 data/templates/"""
    if template_name not in TEMPLATE_NAMES:
        raise ValueError(f"未知的模板名称: {template_name}")
    TEMPLATES_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (TEMPLATES_DATA_DIR / template_name).write_text(content, encoding="utf-8")


def save_question_template(qid: str, content: str) -> None:
    """保存题目的识读模板到 data/{qid}/识读模板.txt"""
    qdir = DATA_DIR / qid
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "识读模板.txt").write_text(content, encoding="utf-8")


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
