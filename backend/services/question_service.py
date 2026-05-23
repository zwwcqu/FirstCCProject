from __future__ import annotations

import csv
import io
import json
import shutil
from pathlib import Path

from config import (
    DATA_DIR,
    CONFIG_DIR,
    read_questions_index,
    write_questions_index,
    get_question_dir,
    get_student_dir,
)


def list_questions() -> list[dict]:
    return read_questions_index()


def get_question(qid: str) -> dict | None:
    questions = read_questions_index()
    for q in questions:
        if q["id"] == qid:
            return q
    return None


def create_question(qid: str, title: str, description: str, phase1_criteria: str, phase2_criteria: str) -> dict:
    if not qid.isdigit():
        raise ValueError("题号必须为非负整数")
    questions = read_questions_index()
    for q in questions:
        if q["id"] == qid:
            raise ValueError(f"题号 {qid} 已存在")
    qdir = get_question_dir(qid)
    qdir.mkdir(parents=True, exist_ok=True)
    get_student_dir(qid).mkdir(parents=True, exist_ok=True)

    (qdir / "题目内容.md").write_text(description, encoding="utf-8")
    (qdir / "阶段1评分标准.md").write_text(phase1_criteria, encoding="utf-8")
    (qdir / "阶段2评分标准.md").write_text(phase2_criteria, encoding="utf-8")

    entry = {"id": qid, "title": title}
    questions.append(entry)
    write_questions_index(questions)
    return entry


def update_question(qid: str, title: str, description: str, phase1_criteria: str, phase2_criteria: str) -> dict | None:
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
    return found


def delete_question(qid: str) -> bool:
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
    return True


def save_question_image(qid: str, file_bytes: bytes, filename: str) -> str:
    qdir = get_question_dir(qid)
    qdir.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix or ".png"
    path = qdir / f"题目图片{ext}"
    path.write_bytes(file_bytes)
    return str(path)


def save_reference_pdf(qid: str, file_bytes: bytes, filename: str) -> str:
    qdir = get_question_dir(qid)
    qdir.mkdir(parents=True, exist_ok=True)
    path = qdir / "参考工程图.pdf"
    path.write_bytes(file_bytes)
    return str(path)


def get_question_files(qid: str) -> dict:
    qdir = get_question_dir(qid)
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


def get_scoring_templates() -> dict:
    """Read global scoring templates from data directory for pre-filling new questions."""
    result = {"phase1": "", "phase2": ""}
    t1 = CONFIG_DIR / "评分模版1.md"
    t2 = CONFIG_DIR / "评分模版2.md"
    if t1.exists():
        result["phase1"] = t1.read_text(encoding="utf-8").strip()
    if t2.exists():
        result["phase2"] = t2.read_text(encoding="utf-8").strip()
    return result


def save_student_submission(qid: str, student_id: str, name: str, file_bytes: bytes, filename: str) -> str:
    student_dir = get_student_dir(qid)
    student_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix or ".pdf"
    path = student_dir / f"{name}_{student_id}{ext}"
    path.write_bytes(file_bytes)
    return str(path)


def get_student_submission_path(qid: str, student_id: str, name: str) -> Path | None:
    student_dir = get_student_dir(qid)
    if not student_dir.exists():
        return None
    for f in student_dir.iterdir():
        if f.stem == f"{name}_{student_id}" and f.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg"):
            return f
    return None


# --- roster (学生名单) --- 全局 StudentInfo 目录

ROSTER_FILENAME = "学生名单.csv"  # deprecated, 保留以兼容旧数据

from config import STUDENT_INFO_DIR

TEMPLATE_NAME = "_模版.csv"


def _ensure_student_info_dir() -> Path:
    STUDENT_INFO_DIR.mkdir(parents=True, exist_ok=True)
    return STUDENT_INFO_DIR


def create_class_roster(class_name: str, csv_bytes: bytes) -> int:
    """新增/覆盖班级名单，返回人数。CSV 可含 班别/姓名/学号，只保留姓名+学号写入。"""
    _ensure_student_info_dir()
    content = csv_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    students = []
    for row in reader:
        name = row.get("姓名", "").strip()
        sid = row.get("学号", "").strip()
        if name and sid:
            students.append({"姓名": name, "学号": sid})
    # 写入 StudentInfo/<class_name>.csv
    path = STUDENT_INFO_DIR / f"{class_name}.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["姓名", "学号"])
        writer.writeheader()
        writer.writerows(students)
    return len(students)


def list_classes() -> list[dict]:
    """返回班级列表 [{class_name, count}]"""
    _ensure_student_info_dir()
    result = []
    for f in sorted(STUDENT_INFO_DIR.iterdir()):
        if f.suffix.lower() == ".csv" and f.stem != "_模版":
            rows = _read_csv(f)
            result.append({"class_name": f.stem, "count": len(rows)})
    return result


def get_class_students(class_name: str) -> list[dict]:
    """读取某班学生列表 [{姓名, 学号}]"""
    path = STUDENT_INFO_DIR / f"{class_name}.csv"
    if not path.exists():
        return []
    return _read_csv(path)


def delete_class_roster(class_name: str) -> bool:
    """删除班级 CSV"""
    path = STUDENT_INFO_DIR / f"{class_name}.csv"
    if path.exists():
        path.unlink()
        return True
    return False


def ensure_template() -> str:
    """返回学生名单模版路径（位于 config/ 目录）"""
    return str(CONFIG_DIR / "学生名单模版.csv")


def check_roster(name: str, student_id: str) -> tuple[bool, str]:
    """校验学生是否在任意班级名单中。返回 (ok, message)。无任何班级文件则放行。"""
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
    """在学生名单中查找所属班级，返回班级名；未找到返回空字符串"""
    _ensure_student_info_dir()
    for f in STUDENT_INFO_DIR.iterdir():
        if f.suffix.lower() == ".csv" and f.stem != "_模版":
            for row in _read_csv(f):
                if row.get("姓名", "").strip() == name.strip() and row.get("学号", "").strip() == student_id.strip():
                    return f.stem
    return ""


def _read_csv(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))
