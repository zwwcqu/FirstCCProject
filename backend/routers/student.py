"""
学生端 API 路由（前缀 /api/student）。

功能：
- 题目列表与详情查询
- 身份校验（名单验证）
- 学生个人提交历史查询（跨题目）
- 作业提交 → 图面分析 → 两阶段评分（后台线程，非阻塞）
- 提交状态轮询
- 成绩查询（按学号查历史成绩）
- 文件服务（学生提交文件下载 + 预览图生成）

提交模式：
- test：测试模式，不校验名单、不落盘文件、不保存成绩
- submit：正式提交，需通过名单校验，保存文件和成绩
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse

from config import CONFIG_DIR, get_question_dir as _get_question_dir
from services.question_service import (
    list_questions,
    get_question,
    get_question_files,
    save_student_submission,
    get_student_submission_path,
    get_question_dir,
    get_reference_analysis,
    save_student_analysis,
    get_student_analysis,
)
from services.llm_service import (
    analyze_structure,
    analyze_quantitative,
    analyze_structure_bytes,
    analyze_quantitative_bytes,
    run_two_phase_grading,
)
from services.grade_service import save_grade, save_result_json, get_student_grade, read_all_grades
from services.submit_status import set_status, get_status, set_file_data, get_file_data
from services.task_queue import enqueue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/student", tags=["student"])

# ── 频率限制 ─────────────────────────────────────────────
_RATE_LIMIT: dict[str, list[float]] = {}
_RATE_WINDOW = 60
_RATE_MAX = 50


def _check_rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    timestamps = _RATE_LIMIT.get(ip, [])
    timestamps = [t for t in timestamps if now - t < _RATE_WINDOW]
    if len(timestamps) >= _RATE_MAX:
        logger.warning(f"IP {ip} 提交过于频繁，已拒绝")
        raise HTTPException(status_code=429, detail="提交过于频繁，请稍后再试")
    timestamps.append(now)
    _RATE_LIMIT[ip] = timestamps


# ── 题目相关 ─────────────────────────────────────────────

@router.get("/questions")
async def get_questions():
    """获取所有题目列表"""
    return list_questions()


@router.get("/questions/{qid}")
async def get_question_detail(qid: str):
    """获取题目详情（含描述、附图、参考工程图）"""
    q = get_question(qid)
    if q is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    files = get_question_files(qid)
    q["files"] = files
    return q


# ── 身份校验 ─────────────────────────────────────────────

@router.post("/check")
async def check_identity(request: Request):
    """验证姓名+学号是否在名单中"""
    body = await request.json()
    name = (body.get("name") or "").strip()
    student_id = (body.get("student_id") or "").strip()
    if not name or not student_id:
        raise HTTPException(status_code=400, detail="姓名和学号不能为空")
    from services.question_service import check_roster, find_student_class
    ok, msg = check_roster(name, student_id)
    if not ok:
        return {"ok": False, "message": msg}
    class_name = find_student_class(name, student_id)
    return {"ok": True, "message": "", "class_name": class_name}


# ── 学生个人提交历史 ────────────────────────────────────

@router.get("/submissions")
async def get_my_submissions(name: str, student_id: str):
    """返回某学生所有题目的提交记录"""
    questions = list_questions()
    results: list[dict] = []
    for q in questions:
        qid = q["id"]
        row = get_student_grade(qid, student_id)
        if row:
            results.append({
                "question_id": qid,
                "question_title": q["title"],
                "student_name": row.get("姓名", name),
                "student_id": row.get("学号", student_id),
                "grade": row.get("成绩", ""),
                "total_score": row.get("总分", ""),
                "status": "completed",
                "submitted_at": row.get("提交时间", ""),
            })
    return {"submissions": results}


# ── 提交状态轮询 ────────────────────────────────────────

@router.get("/status/{qid}")
async def poll_status(qid: str, name: str, student_id: str):
    """查询异步提交的处理状态"""
    s = get_status(qid, name, student_id)
    return {"ok": True, **s}


# ── 分析结果查询 ────────────────────────────────────────

@router.get("/analysis/{qid}")
async def get_analysis_result(qid: str, name: str, student_id: str):
    """查询学生的图面分析结果"""
    analysis = get_student_analysis(qid, student_id, name)
    if analysis is None:
        raise HTTPException(status_code=404, detail="分析结果不存在，请先完成图面分析")
    return {"ok": True, "analysis": analysis}


# ── 第一步：图面分析（异步非阻塞）─────────────────────────

def _run_analyze(
    qid: str, name: str, student_id: str,
    file_bytes: bytes, filename: str,
    is_test: bool,
    struct_tpl: Path, quant_tpl: Path,
):
    """后台线程：文件转换 → 结构分析 → 量化分析 → 保存"""
    try:
        # 阶段1：文件转换
        set_status(qid, name, student_id, "analyze", "converting")
        if is_test:
            from services.llm_service import bytes_to_base64
            bytes_to_base64(file_bytes, filename)  # 预热转换，确保不失败
            set_status(qid, name, student_id, "analyze", "submitted")
        else:
            student_path = save_student_submission(qid, student_id, name, file_bytes, filename)
            set_status(qid, name, student_id, "analyze", "submitted", student_filename=Path(student_path).name)

        if is_test:
            set_file_data(qid, name, student_id, file_bytes, filename)
            set_status(qid, name, student_id, "analyze", "analyzing")
            structure = analyze_structure_bytes(file_bytes, filename, struct_tpl)
            quantitative = analyze_quantitative_bytes(file_bytes, filename, quant_tpl, structure)
        else:
            student_path_obj = Path(student_path)
            set_status(qid, name, student_id, "analyze", "analyzing")
            structure = analyze_structure(student_path_obj, struct_tpl)
            quantitative = analyze_quantitative(student_path_obj, quant_tpl, structure)

        analysis = {"structure": structure, "quantitative": quantitative}
        if not is_test:
            save_student_analysis(qid, student_id, name, analysis)

        set_status(qid, name, student_id, "analyze", "done")
        logger.info(f"[{qid}] 图面分析完成: {name}({student_id})")
    except Exception as e:
        logger.error(f"[{qid}] 图面分析失败: {e}")
        set_status(qid, name, student_id, "analyze", "error", str(e))


@router.post("/analyze/{qid}")
async def analyze_submission(
    qid: str,
    request: Request,
    name: str = Form(...),
    student_id: str = Form(...),
    file: UploadFile = File(...),
    mode: str = Form("submit"),
):
    """上传作业 → 后台执行图面分析 → 立即返回"""
    _check_rate_limit(request)

    q = get_question(qid)
    if q is None:
        raise HTTPException(status_code=404, detail="题目不存在")

    is_test = mode == "test"

    if not is_test:
        from services.question_service import check_roster
        ok, msg = check_roster(name, student_id)
        if not ok:
            raise HTTPException(status_code=403, detail=msg)

    file_bytes = await file.read()

    def _task():
        _run_analyze(qid, name, student_id, file_bytes, file.filename or "submission.pdf", is_test,
                     CONFIG_DIR / "结构分析_学生.txt", CONFIG_DIR / "量化分析_学生.txt")

    enqueue(10, _task)  # priority=10 学生

    return {"ok": True, "status": "processing"}


# ── 第二步：两阶段评分（异步非阻塞）───────────────────────

def _run_grade(
    qid: str, name: str, student_id: str,
    is_test: bool,
    stu_data: bytes | None, stu_filename: str,
):
    """后台线程：读取分析结果 → 阶段一 + 阶段二评分 → 保存"""
    set_status(qid, name, student_id, "grade", "processing")
    try:
        ref_analysis = get_reference_analysis(qid)
        if ref_analysis is None:
            raise RuntimeError("参考图尚未完成分析，请联系老师")

        stu_analysis = get_student_analysis(qid, student_id, name)
        if stu_analysis is None:
            raise RuntimeError("请先完成图面分析再提交评分")

        files = get_question_files(qid)
        phase1_criteria = files.get("phase1_criteria", "")
        phase2_criteria = files.get("phase2_criteria", "")

        qdir = _get_question_dir(qid)
        ref_pdf = qdir / "参考工程图.pdf"
        if not ref_pdf.exists():
            raise RuntimeError("参考工程图不存在，请联系老师")

        if is_test:
            if not stu_data:
                raise RuntimeError("测试模式文件数据丢失，请重新上传分析")
            result = run_two_phase_grading(
                ref_struct=ref_analysis["structure"],
                ref_quant=ref_analysis["quantitative"],
                stu_struct=stu_analysis["structure"],
                stu_quant=stu_analysis["quantitative"],
                phase1_criteria=phase1_criteria,
                phase2_criteria=phase2_criteria,
                ref_image_path=ref_pdf,
                stu_image_path=Path("."),  # 占位，不会用到
                stu_data=stu_data,
                stu_filename=stu_filename,
            )
        else:
            student_path = get_student_submission_path(qid, student_id, name)
            if student_path is None:
                raise RuntimeError("学生提交文件不存在，请重新上传")
            result = run_two_phase_grading(
                ref_struct=ref_analysis["structure"],
                ref_quant=ref_analysis["quantitative"],
                stu_struct=stu_analysis["structure"],
                stu_quant=stu_analysis["quantitative"],
                phase1_criteria=phase1_criteria,
                phase2_criteria=phase2_criteria,
                ref_image_path=ref_pdf,
                stu_image_path=student_path,
            )

        grade = result.get("grade", "N/A")

        if not is_test:
            from services.question_service import find_student_class
            class_name = find_student_class(name, student_id)
            save_grade(qid, student_id, name, grade, result, class_name)
            save_result_json(qid, student_id, name, result)

        set_status(qid, name, student_id, "grade", "done")
        logger.info(f"[{qid}] 评分完成: {name}({student_id}) → {grade}")
    except Exception as e:
        logger.error(f"[{qid}] 评分失败: {e}")
        set_status(qid, name, student_id, "grade", "error", str(e))


@router.post("/grade/{qid}")
async def grade_submission_handler(
    qid: str,
    request: Request,
    name: str = Form(...),
    student_id: str = Form(...),
    mode: str = Form("submit"),
):
    """提交评分 → 后台执行两阶段评分 → 立即返回"""
    _check_rate_limit(request)

    q = get_question(qid)
    if q is None:
        raise HTTPException(status_code=404, detail="题目不存在")

    is_test = mode == "test"

    stu_data: bytes | None = None
    stu_filename = ""
    if is_test:
        stu_data, stu_filename = get_file_data(qid, name, student_id)

    def _task():
        _run_grade(qid, name, student_id, is_test, stu_data, stu_filename)

    enqueue(10, _task)  # priority=10 学生

    return {"ok": True, "status": "processing"}


# ── 成绩查询 ─────────────────────────────────────────────

@router.get("/result/{qid}/{student_id}")
async def get_result(qid: str, student_id: str):
    """查询某学生在某题的历史成绩"""
    row = get_student_grade(qid, student_id)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到成绩")
    return row


# ── 文件服务 ─────────────────────────────────────────────

@router.get("/files/{qid}/{filename}")
async def serve_student_file(qid: str, filename: str):
    """直接下载学生提交的原始文件"""
    from services.question_service import get_student_dir
    sdir = get_student_dir(qid)
    filepath = sdir / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(str(filepath))


@router.get("/preview/{qid}/{filename}")
async def serve_student_preview(qid: str, filename: str):
    """将学生提交文件转为 JPEG 预览图"""
    from services.question_service import get_student_dir
    from services.llm_service import image_to_base64
    import base64
    from fastapi.responses import Response

    sdir = get_student_dir(qid)
    filepath = sdir / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404)

    b64 = image_to_base64(filepath)
    img_bytes = base64.b64decode(b64)
    return Response(content=img_bytes, media_type="image/jpeg")
