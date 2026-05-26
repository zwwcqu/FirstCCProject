"""
题目管理服务。

功能：
- 题目 CRUD（索引维护 + 文件读写）
- 题目附图 / 参考工程图的上传
- 学生作业提交文件的存取
- 全局学生名单（StudentInfo）管理：班级的增删查
- 评分模板读取（新建题目时预填）
- 文件名安全清洗

数据布局（各题目目录）：
  data/{qid}/
    题目内容.md          题目文字描述
    阶段1评分标准.md      相位1相似度评分标准
    阶段2评分标准.md      相位2批改要求评分标准
    题目图片.png          题目附图（可选）
    参考工程图.pdf         参考工程图（可选）
    student/
      {姓名}_{学号}.pdf   学生提交的作业
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import shutil
from pathlib import Path

from config import (
    DATA_DIR,
    CONFIG_DIR,
    STUDENT_INFO_DIR,
    read_questions_index,
    write_questions_index,
    get_question_dir,
    get_student_dir,
)

logger = logging.getLogger(__name__)

# 模板文件名
TEMPLATE_NAME = "_模版.csv"
# 旧版名单名（保留兼容）
ROSTER_FILENAME = "学生名单.csv"


def _sanitize_filename_part(s: str) -> str:
    """剔除文件名中的路径分隔符和不可见控制字符，替换为下划线"""
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', s)


# ── 题目 CRUD ───────────────────────────────────────────

def list_questions() -> list[dict]:
    """返回所有题目列表 [{id, title}]"""
    return read_questions_index()


def get_question(qid: str) -> dict | None:
    """按题号查找题目，未找到返回 None"""
    questions = read_questions_index()
    for q in questions:
        if q["id"] == qid:
            return q
    return None


def create_question(qid: str, title: str, description: str, phase1_criteria: str, phase2_criteria: str) -> dict:
    """创建题目：写索引 + 创建目录 + 写内容文件"""
    if not qid.isdigit():
        raise ValueError("题号必须为非负整数")
    questions = read_questions_index()
    for q in questions:
        if q["id"] == qid:
            raise ValueError(f"题号 {qid} 已存在")

    qdir = get_question_dir(qid)
    qdir.mkdir(parents=True, exist_ok=True)
    get_student_dir(qid).mkdir(parents=True, exist_ok=True)

    # 写入各内容文件
    (qdir / "题目内容.md").write_text(description, encoding="utf-8")
    (qdir / "阶段1评分标准.md").write_text(phase1_criteria, encoding="utf-8")
    (qdir / "阶段2评分标准.md").write_text(phase2_criteria, encoding="utf-8")

    entry = {"id": qid, "title": title}
    questions.append(entry)
    write_questions_index(questions)
    logger.info(f"题目已创建: [{qid}] {title}")
    return entry


def update_question(qid: str, title: str, description: str, phase1_criteria: str, phase2_criteria: str) -> dict | None:
    """编辑题目：更新索引 + 覆盖内容文件"""
    questions = read_questions_index()
    found = None
    for q in questions:
        if q["id"] == qid:
            q["title"] = title
            found = q
            break
    if found is None:
        return None

    qdir = get_question_dir(qid)
    (qdir / "题目内容.md").write_text(description, encoding="utf-8")
    (qdir / "阶段1评分标准.md").write_text(phase1_criteria, encoding="utf-8")
    (qdir / "阶段2评分标准.md").write_text(phase2_criteria, encoding="utf-8")
    write_questions_index(questions)
    logger.info(f"题目已更新: [{qid}] {title}")
    return found


def delete_question(qid: str) -> bool:
    """删除题目：从索引移除 + 数据目录移到 backup/（非真删）"""
    questions = read_questions_index()
    new_list = [q for q in questions if q["id"] != qid]
    if len(new_list) == len(questions):
        return False
    write_questions_index(new_list)

    qdir = get_question_dir(qid)
    if qdir.exists():
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = DATA_DIR / "backup" / f"{qid}_{ts}"
        backup_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(qdir), str(backup_dir))
        logger.info(f"题目 [{qid}] 已备份到 {backup_dir}")
    return True


# ── 题目文件（附图 / 参考工程图）─────────────────────────

def save_question_image(qid: str, file_bytes: bytes, filename: str) -> str:
    """保存题目附图，固定命名为 题目图片.* """
    qdir = get_question_dir(qid)
    qdir.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix or ".png"
    path = qdir / f"题目图片{ext}"
    path.write_bytes(file_bytes)
    return str(path)


def save_reference_pdf(qid: str, file_bytes: bytes, filename: str) -> str:
    """保存参考工程图 PDF"""
    qdir = get_question_dir(qid)
    qdir.mkdir(parents=True, exist_ok=True)
    path = qdir / "参考工程图.pdf"
    path.write_bytes(file_bytes)
    return str(path)


def get_question_files(qid: str) -> dict:
    """读取题目的所有内容文件和附件信息"""
    qdir = get_question_dir(qid)
    # 默认空结果
    result = {
        "description": "",
        "phase1_criteria": "",
        "phase2_criteria": "",
        "images": [],
        "reference_pdf": None,
    }

    desc_file = qdir / "题目内容.md"
    if desc_file.exists():
        result["description"] = desc_file.read_text(encoding="utf-8")

    p1_file = qdir / "阶段1评分标准.md"
    if p1_file.exists():
        result["phase1_criteria"] = p1_file.read_text(encoding="utf-8")

    p2_file = qdir / "阶段2评分标准.md"
    if p2_file.exists():
        result["phase2_criteria"] = p2_file.read_text(encoding="utf-8")

    for f in sorted(qdir.iterdir()):
        if f.name.startswith("题目图片") and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            result["images"].append(f.name)
        if f.name == "参考工程图.pdf":
            result["reference_pdf"] = f.name

    return result


# ── 评分模板（新建题目时预填）────────────────────────────

def get_scoring_templates() -> dict:
    """从 config/ 目录读取两个评分模板文件，返回 {phase1, phase2}"""
    result = {"phase1": "", "phase2": ""}
    t1 = CONFIG_DIR / "评分模版1.md"
    t2 = CONFIG_DIR / "评分模版2.md"
    if t1.exists():
        result["phase1"] = t1.read_text(encoding="utf-8").strip()
    if t2.exists():
        result["phase2"] = t2.read_text(encoding="utf-8").strip()
    return result


# ── 学生提交文件存取 ────────────────────────────────────

def save_student_submission(qid: str, student_id: str, name: str, file_bytes: bytes, filename: str) -> str:
    """保存学生上传的工程图文件。文件名经安全清洗后保存"""
    student_dir = get_student_dir(qid)
    student_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix or ".pdf"
    safe_name = _sanitize_filename_part(name)
    safe_id = _sanitize_filename_part(student_id)
    path = student_dir / f"{safe_name}_{safe_id}{ext}"
    path.write_bytes(file_bytes)
    return str(path)


def get_student_submission_path(qid: str, student_id: str, name: str) -> Path | None:
    """查找学生已提交的文件路径（用于覆盖前检查），未找到返回 None"""
    student_dir = get_student_dir(qid)
    if not student_dir.exists():
        return None
    safe_name = _sanitize_filename_part(name)
    safe_id = _sanitize_filename_part(student_id)
    for f in student_dir.iterdir():
        if f.stem == f"{safe_name}_{safe_id}" and f.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg"):
            return f
    return None


# ── 全局学生名单（StudentInfo 目录）─────────────────────

def _ensure_student_info_dir() -> Path:
    """确保 StudentInfo 目录存在"""
    STUDENT_INFO_DIR.mkdir(parents=True, exist_ok=True)
    return STUDENT_INFO_DIR


def create_class_roster(class_name: str, csv_bytes: bytes) -> int:
    """新增/覆盖班级名单，返回导入人数。CSV 可含 班别/姓名/学号 列，只提取 姓名、学号"""
    _ensure_student_info_dir()
    content = csv_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    students = []
    for row in reader:
        name = row.get("姓名", "").strip()
        sid = row.get("学号", "").strip()
        if name and sid:
            students.append({"姓名": name, "学号": sid})

    path = STUDENT_INFO_DIR / f"{class_name}.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["姓名", "学号"])
        writer.writeheader()
        writer.writerows(students)
    logger.info(f"班级名单已导入: {class_name} ({len(students)}人)")
    return len(students)


def list_classes() -> list[dict]:
    """返回所有班级列表 [{class_name, count}]"""
    _ensure_student_info_dir()
    result = []
    for f in sorted(STUDENT_INFO_DIR.iterdir()):
        if f.suffix.lower() == ".csv" and f.stem != "_模版":
            rows = _read_csv(f)
            result.append({"class_name": f.stem, "count": len(rows)})
    return result


def get_class_students(class_name: str) -> list[dict]:
    """读取某班级学生列表 [{姓名, 学号}]"""
    path = STUDENT_INFO_DIR / f"{class_name}.csv"
    if not path.exists():
        return []
    return _read_csv(path)


def delete_class_roster(class_name: str) -> bool:
    """删除指定班级 CSV 文件"""
    path = STUDENT_INFO_DIR / f"{class_name}.csv"
    if path.exists():
        path.unlink()
        logger.info(f"班级已删除: {class_name}")
        return True
    return False


def ensure_template() -> str:
    """返回学生名单 CSV 模板路径（位于 config/ 目录）"""
    return str(CONFIG_DIR / "学生名单模版.csv")


def check_roster(name: str, student_id: str) -> tuple[bool, str]:
    """校验学生是否在任意班级名单中。无任何班级文件时放行。返回 (ok, message)"""
    _ensure_student_info_dir()
    csvs = [f for f in STUDENT_INFO_DIR.iterdir() if f.suffix.lower() == ".csv" and f.stem != "_模版"]
    if not csvs:
        return True, ""
    for f in csvs:
        for row in _read_csv(f):
            if row.get("姓名", "").strip() == name.strip() and row.get("学号", "").strip() == student_id.strip():
                return True, ""
    return False, "姓名或学号不在任何班级名单中，请联系老师。如为测试请选择测试模式。"


def find_student_class(name: str, student_id: str) -> str:
    """查找学生所属班级，返回班级名；未找到返回空字符串"""
    _ensure_student_info_dir()
    for f in STUDENT_INFO_DIR.iterdir():
        if f.suffix.lower() == ".csv" and f.stem != "_模版":
            for row in _read_csv(f):
                if row.get("姓名", "").strip() == name.strip() and row.get("学号", "").strip() == student_id.strip():
                    return f.stem
    return ""


def _read_csv(path: Path) -> list[dict]:
    """读取 CSV 文件，返回字典列表（内部工具）"""
    with open(path, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))
