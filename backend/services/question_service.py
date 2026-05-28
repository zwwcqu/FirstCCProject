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


def create_question(qid: str, title: str, description: str,
                    phase1_criteria: str, phase2_criteria: str,
                    knowledge: str = "") -> dict:
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

    (qdir / "题目内容.md").write_text(description, encoding="utf-8")
    (qdir / "阶段1评分标准.md").write_text(phase1_criteria, encoding="utf-8")
    (qdir / "阶段2评分标准.md").write_text(phase2_criteria, encoding="utf-8")
    (qdir / "补充知识.md").write_text(knowledge, encoding="utf-8")

    entry = {"id": qid, "title": title}
    questions.append(entry)
    write_questions_index(questions)
    logger.info(f"题目已创建: [{qid}] {title}")
    return entry


def update_question(qid: str, title: str, description: str,
                    phase1_criteria: str, phase2_criteria: str,
                    knowledge: str = "") -> dict | None:
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
    (qdir / "补充知识.md").write_text(knowledge, encoding="utf-8")
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
        "knowledge": "",
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

    kn_file = qdir / "补充知识.md"
    if kn_file.exists():
        result["knowledge"] = kn_file.read_text(encoding="utf-8")

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
    """保存学生上传的工程图文件。非 PDF/PNG 图片统一转为 PNG。保存前校验文件格式有效。"""
    from io import BytesIO

    student_dir = get_student_dir(qid)
    student_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix.lower()
    safe_name = _sanitize_filename_part(name)
    safe_id = _sanitize_filename_part(student_id)

    if ext == ".pdf":
        # 校验 PDF 有效（至少能渲染首页）
        try:
            from pdf2image import convert_from_bytes
            images = convert_from_bytes(file_bytes, first_page=1, last_page=1, dpi=72)
            if not images:
                raise ValueError("PDF 无法渲染，可能已损坏")
        except Exception as e:
            raise ValueError(f"PDF 文件无效: {e}")

    elif ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        # 校验图片可被 PIL 打开
        try:
            from PIL import Image
            img = Image.open(BytesIO(file_bytes))
            img.verify()  # 只校验结构，不加载像素数据
        except Exception as e:
            raise ValueError(f"图片文件无效: {e}")

        # 非 PNG 格式统一转为 PNG
        if ext != ".png":
            img = Image.open(BytesIO(file_bytes))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            buf = BytesIO()
            img.save(buf, format="PNG")
            file_bytes = buf.getvalue()
            ext = ".png"
    else:
        raise ValueError(f"不支持的文件格式: {ext}，请上传 PDF 或图片文件")

    path = student_dir / f"{safe_name}_{safe_id}{ext}"
    path.write_bytes(file_bytes)
    return str(path)


def submit_student_work(qid: str, student_id: str, name: str,
                        file_bytes: bytes, filename: str) -> str:
    """学生作业提交全流程：清除旧数据 → 保存文件 → PDF 生成 PNG 预览 → 写记录。返回保存的文件名。"""
    stem = clear_student_data(qid, student_id, name)
    student_path = save_student_submission(qid, student_id, name, file_bytes, filename)
    saved_name = Path(student_path).name
    file_stem = Path(saved_name).stem

    if Path(saved_name).suffix.lower() == ".pdf":
        from services.llm_service import save_as_png
        save_as_png(Path(student_path), Path(student_path).with_suffix(".png"))

    update_submission_record(qid, student_id, name, file_stem, "uploaded")
    return saved_name


def get_student_submission_path(qid: str, student_id: str, name: str) -> Path | None:
    """查找学生已提交的文件路径（支持 PDF/PNG），未找到返回 None"""
    student_dir = get_student_dir(qid)
    if not student_dir.exists():
        return None
    safe_name = _sanitize_filename_part(name)
    safe_id = _sanitize_filename_part(student_id)
    for f in student_dir.iterdir():
        if f.stem == f"{safe_name}_{safe_id}" and f.suffix.lower() in (".pdf", ".png"):
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


# ── 分析结果存取（参考图 / 学生图的结构+量化 JSON）─────

def save_reference_analysis(qid: str, analysis: dict) -> None:
    """
    保存参考图的分析结果。
    analysis 应包含 structure 和 quantitative 两个 key。
    写入 data/{qid}/参考图_结构分析.json 和 参考图_量化分析.json
    """
    qdir = get_question_dir(qid)
    qdir.mkdir(parents=True, exist_ok=True)
    struct_path = qdir / "参考图_结构分析.json"
    quant_path = qdir / "参考图_量化分析.json"
    struct_path.write_text(json.dumps(analysis.get("structure", {}), ensure_ascii=False, indent=2), encoding="utf-8")
    quant_path.write_text(json.dumps(analysis.get("quantitative", {}), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"参考图分析结果已保存: [{qid}]")


def get_reference_analysis(qid: str) -> dict | None:
    """
    读取参考图的分析结果。
    返回 {"structure": ..., "quantitative": ...} 或 None（分析文件不存在时）
    """
    qdir = get_question_dir(qid)
    struct_path = qdir / "参考图_结构分析.json"
    quant_path = qdir / "参考图_量化分析.json"
    if not struct_path.exists() or not quant_path.exists():
        return None
    return {
        "structure": json.loads(struct_path.read_text(encoding="utf-8")),
        "quantitative": json.loads(quant_path.read_text(encoding="utf-8")),
    }


# ── 提交记录（submissions.json）─────────────────────────

def _get_submissions_path(qid: str) -> Path:
    return get_question_dir(qid) / "submissions.json"


def get_submissions(qid: str) -> dict:
    """读取题目的所有提交记录"""
    path = _get_submissions_path(qid)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {}


def _save_submissions(qid: str, data: dict) -> None:
    """保存提交记录"""
    path = _get_submissions_path(qid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_submission_record(qid: str, student_id: str) -> dict | None:
    """获取单个学生的提交记录"""
    submissions = get_submissions(qid)
    return submissions.get(student_id)


def update_submission_record(qid: str, student_id: str, name: str,
                              filename_stem: str, status: str, **extra) -> None:
    """更新/新增一条提交记录；filename_stem 不含后缀"""
    submissions = get_submissions(qid)
    record = submissions.get(student_id, {})
    record["name"] = name
    record["filename"] = filename_stem
    record["status"] = status
    from datetime import datetime
    record["submitted_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    record.update(extra)
    submissions[student_id] = record
    _save_submissions(qid, submissions)


def clear_student_data(qid: str, student_id: str, name: str) -> str:
    """
    清除学生的旧提交数据（重新提交时调用）：
    - 删除旧上传文件
    - 删除分析 JSON
    - 删除结果 JSON
    - 从成绩 CSV 中移除
    - 从 submissions.json 中移除
    返回安全文件名前缀（name_id）
    """
    import glob as _glob
    student_dir = get_student_dir(qid)
    safe_name = _sanitize_filename_part(name)
    safe_id = _sanitize_filename_part(student_id)
    stem = f"{safe_name}_{safe_id}"

    if student_dir.exists():
        for f in student_dir.iterdir():
            if f.stem == stem or f.stem.startswith(stem + "_"):
                f.unlink()
                logger.info(f"已删除旧文件: {f}")

    # 清除成绩 CSV
    from services.grade_service import remove_grade
    remove_grade(qid, student_id)

    # 清除提交记录
    submissions = get_submissions(qid)
    submissions.pop(student_id, None)
    _save_submissions(qid, submissions)

    return stem


def save_student_analysis(qid: str, student_id: str, name: str, analysis: dict) -> None:
    """
    保存学生图的分析结果。
    写入 data/{qid}/student/{姓名}_{学号}_结构分析.json 和 _量化分析.json
    """
    student_dir = get_student_dir(qid)
    student_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_filename_part(name)
    safe_id = _sanitize_filename_part(student_id)
    struct_path = student_dir / f"{safe_name}_{safe_id}_结构分析.json"
    quant_path = student_dir / f"{safe_name}_{safe_id}_量化分析.json"
    struct_path.write_text(json.dumps(analysis.get("structure", {}), ensure_ascii=False, indent=2), encoding="utf-8")
    quant_path.write_text(json.dumps(analysis.get("quantitative", {}), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"学生图分析结果已保存: [{qid}] {name}({student_id})")


def get_student_analysis(qid: str, student_id: str, name: str) -> dict | None:
    """
    读取学生图的分析结果。
    返回 {"structure": ..., "quantitative": ...} 或 None
    """
    student_dir = get_student_dir(qid)
    safe_name = _sanitize_filename_part(name)
    safe_id = _sanitize_filename_part(student_id)
    struct_path = student_dir / f"{safe_name}_{safe_id}_结构分析.json"
    quant_path = student_dir / f"{safe_name}_{safe_id}_量化分析.json"
    if not struct_path.exists() or not quant_path.exists():
        return None
    return {
        "structure": json.loads(struct_path.read_text(encoding="utf-8")),
        "quantitative": json.loads(quant_path.read_text(encoding="utf-8")),
    }


def _read_csv(path: Path) -> list[dict]:
    """读取 CSV 文件，返回字典列表（内部工具）"""
    with open(path, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def get_all_roster_students() -> list[dict]:
    """返回所有班级名单中的学生 [{姓名, 学号, 班级}]"""
    _ensure_student_info_dir()
    students: list[dict] = []
    for f in STUDENT_INFO_DIR.iterdir():
        if f.suffix.lower() == ".csv" and f.stem != "_模版":
            for row in _read_csv(f):
                name = row.get("姓名", "").strip()
                sid = row.get("学号", "").strip()
                if name and sid:
                    students.append({"姓名": name, "学号": sid, "班级": f.stem})
    return students


def _validate_pdf_file(path: Path) -> bool:
    """校验 PDF 文件是否可渲染。返回 True 表示有效。"""
    if path.stat().st_size == 0:
        return False
    try:
        from pdf2image import convert_from_path
        images = convert_from_path(str(path), first_page=1, last_page=1, dpi=72)
        return len(images) > 0
    except Exception:
        return False


def sync_submissions_from_disk(qid: str) -> int:
    """扫描 student 目录：清理损坏文件、将 PDF/非PNG 图片转为 3508px PNG、自动注册提交。返回新增数量。"""
    student_dir = get_student_dir(qid)
    if not student_dir.exists():
        return 0

    roster_students = get_all_roster_students()
    roster_map: dict[str, dict] = {}
    for s in roster_students:
        safe_name = _sanitize_filename_part(s["姓名"])
        safe_id = _sanitize_filename_part(s["学号"])
        roster_map[f"{safe_name}_{safe_id}"] = s

    submissions = get_submissions(qid)
    added = 0
    valid_exts = (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
    corrupt_stems: set[str] = set()
    submission_dirty = False

    for f in sorted(student_dir.iterdir()):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        # 跳过分析/结果 JSON（_结构分析, _量化分析 等）
        if ext == ".json" or f.stem.endswith("_结构分析") or f.stem.endswith("_量化分析"):
            continue
        if ext not in valid_exts:
            continue

        stem = f.stem
        student = roster_map.get(stem)
        if student is None:
            continue

        sid = student["学号"]
        name = student["姓名"]

        # 校验 PDF 有效性（损坏/空文件 → 标记清理）
        if ext == ".pdf" and not _validate_pdf_file(f):
            logger.warning(f"[{qid}] 损坏的 PDF，将清除: {f.name}")
            corrupt_stems.add(stem)
            continue

        # 非 PNG 格式 → 转换为 3508px PNG，保留原始文件
        if ext != ".png":
            png_path = f.with_suffix(".png")
            if not png_path.exists():
                try:
                    from services.llm_service import save_as_png
                    save_as_png(f, png_path)
                    logger.info(f"[{qid}] 转换为 PNG: {f.name} → {png_path.name}")
                except Exception as e:
                    logger.error(f"[{qid}] PNG 转换失败: {f.name}: {e}")

        # 自动注册到 submissions
        if sid not in submissions:
            submissions[sid] = {
                "name": name,
                "filename": stem,
                "status": "uploaded",
                "submitted_at": "",
            }
            added += 1
            submission_dirty = True
            logger.info(f"[{qid}] 自动发现提交: {name}({sid}) ← {f.name}")

    # 清理损坏文件（PDF + 分析 JSON + submissions 记录）
    for stem in corrupt_stems:
        for cf in list(student_dir.iterdir()):
            if cf.stem == stem or cf.stem.startswith(stem + "_"):
                cf.unlink()
                logger.info(f"[{qid}] 已删除损坏相关文件: {cf.name}")
        for sid, rec in list(submissions.items()):
            safe_name = _sanitize_filename_part(rec.get("name", ""))
            safe_id = _sanitize_filename_part(sid)
            if f"{safe_name}_{safe_id}" == stem:
                submissions.pop(sid, None)
                submission_dirty = True
                logger.info(f"[{qid}] 已从记录移除损坏提交: {rec.get('name')}({sid})")
                # 同时清理成绩 CSV
                try:
                    from services.grade_service import remove_grade
                    remove_grade(qid, sid)
                except Exception:
                    pass
                break

    if submission_dirty or corrupt_stems:
        _save_submissions(qid, submissions)
    return added
