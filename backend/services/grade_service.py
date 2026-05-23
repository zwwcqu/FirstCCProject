from __future__ import annotations

import json
import csv
import os
from pathlib import Path

from config import get_question_dir


def get_grades_csv_path(qid: str) -> Path:
    return get_question_dir(qid) / f"成绩+{qid}.csv"


def read_all_grades(qid: str) -> list[dict]:
    csv_path = get_grades_csv_path(qid)
    if not csv_path.exists():
        return []
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def save_grade(qid: str, student_id: str, name: str, grade: str, comments: dict, class_name: str = "") -> None:
    csv_path = get_grades_csv_path(qid)
    rows = read_all_grades(qid)

    from datetime import datetime
    submit_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    new_row = {
        "班级": class_name,
        "姓名": name,
        "学号": student_id,
        "提交时间": submit_time,
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

    found = False
    for i, row in enumerate(rows):
        if row.get("学号") == student_id:
            rows[i] = new_row
            found = True
            break

    if not found:
        rows.append(new_row)

    fieldnames = ["班级", "姓名", "学号", "提交时间", "成绩", "阶段1相似度", "阶段2评分", "总分", "相似度评价", "总评", "图样表达", "尺寸标注", "尺寸公差", "表面质量", "形位公差"]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def get_student_grade(qid: str, student_id: str) -> dict | None:
    rows = read_all_grades(qid)
    for row in rows:
        if row.get("学号") == student_id:
            return row
    return None


def save_result_json(qid: str, student_id: str, name: str, result: dict) -> None:
    from config import get_student_dir
    student_dir = get_student_dir(qid)
    student_dir.mkdir(parents=True, exist_ok=True)
    json_path = student_dir / f"{name}_{student_id}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
