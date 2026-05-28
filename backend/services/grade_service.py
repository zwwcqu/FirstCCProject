"""
成绩管理服务。

功能：
- 成绩 CSV 文件的读写（成绩+{题号}.csv）
- 按学号覆盖写入（同一学生再次提交则更新）
- 学生提交的原始 LLM JSON 结果存档
- fcntl 文件锁保证并发写入安全

CSV 列顺序（FIELDNAMES）：
  班级 → 姓名 → 学号 → 提交时间 → 成绩 →
  阶段1相似度 → 阶段2评分 → 总分 →
  相似度评价 → 总评 → 图样表达 → 尺寸标注 →
  尺寸公差 → 表面质量 → 形位公差
"""

from __future__ import annotations

import csv
import fcntl
import json
import logging
import os
from pathlib import Path

from config import get_question_dir, get_student_dir

logger = logging.getLogger(__name__)

# 成绩 CSV 固定列顺序，前后端保持一致
FIELDNAMES = [
    "班级", "姓名", "学号", "提交时间", "成绩",
    "阶段1相似度", "阶段2评分", "总分",
    "相似度评价", "总评",
    "图样表达", "尺寸标注", "尺寸公差", "表面质量", "形位公差",
]


def get_grades_csv_path(qid: str) -> Path:
    """返回题目对应的成绩 CSV 路径"""
    return get_question_dir(qid) / f"成绩+{qid}.csv"


def read_all_grades(qid: str) -> list[dict]:
    """读取某题全部成绩列表（加共享锁，与写操作互斥）"""
    csv_path = get_grades_csv_path(qid)
    if not csv_path.exists():
        return []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)     # 共享锁
        try:
            reader = csv.DictReader(f)
            return list(reader)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def save_grade(qid: str, student_id: str, name: str, grade: str, comments: dict, class_name: str = "") -> None:
    """保存/覆盖一条成绩记录。使用排他锁保护读-改-写全过程"""
    csv_path = get_grades_csv_path(qid)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    from datetime import datetime
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 组织写入行
    new_row = {
        "班级": class_name,
        "姓名": name,
        "学号": student_id,
        "提交时间": now_str,
        "成绩": grade,
        "阶段1相似度": str(comments.get("phase1_similarity", "")),
        "阶段2评分": str(comments.get("phase2_criteria", "")),
        "总分": str(comments.get("total_score", "")),
        "相似度评价": comments.get("phase1_comment", ""),
        "总评": comments.get("总评", ""),
        "图样表达": comments.get("图样表达", ""),
        "尺寸标注": comments.get("尺寸标注", ""),
        "尺寸公差": comments.get("尺寸公差", ""),
        "表面质量": comments.get("表面质量", ""),
        "形位公差": comments.get("形位公差", ""),
    }

    # 确保文件存在
    if not csv_path.exists():
        csv_path.write_text("", encoding="utf-8-sig")

    # 排他锁保护读 → 改 → 写
    with open(csv_path, "r+", encoding="utf-8-sig", newline="") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            reader = csv.DictReader(f)
            rows = list(reader)

            # 按学号查找并更新，未找到则追加
            found = False
            for i, row in enumerate(rows):
                if row.get("学号") == student_id:
                    rows[i] = new_row
                    found = True
                    break
            if not found:
                rows.append(new_row)

            f.seek(0)
            f.truncate()
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    logger.info(f"成绩已保存: [{qid}] {name}({student_id}) → {grade}")


def get_student_grade(qid: str, student_id: str) -> dict | None:
    """查询某学生在某题的成绩记录，无则返回 None"""
    rows = read_all_grades(qid)
    for row in rows:
        if row.get("学号") == student_id:
            return row
    return None


def remove_grade(qid: str, student_id: str) -> None:
    """从成绩 CSV 中删除指定学生的记录"""
    csv_path = get_grades_csv_path(qid)
    if not csv_path.exists():
        return
    import fcntl
    with open(csv_path, "r+", encoding="utf-8-sig", newline="") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            reader = csv.DictReader(f)
            rows = [row for row in reader if row.get("学号") != student_id]
            f.seek(0)
            f.truncate()
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def save_result_json(qid: str, student_id: str, name: str, result: dict) -> None:
    """将 LLM 原始批阅结果保存为 JSON（用于调试和归档）"""
    from services.question_service import _sanitize_filename_part

    student_dir = get_student_dir(qid)
    student_dir.mkdir(parents=True, exist_ok=True)

    safe_name = _sanitize_filename_part(name)
    safe_id = _sanitize_filename_part(student_id)
    json_path = student_dir / f"{safe_name}_{safe_id}.json"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
