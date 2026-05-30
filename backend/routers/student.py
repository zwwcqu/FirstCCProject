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

from auth import create_student_session, validate_student_session, get_student_session
from config import CONFIG_DIR, get_question_dir as _get_question_dir
from services.question_service import (
    list_questions,
    get_question,
    get_question_files,
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


@router.post("/login")
async def student_login(request: Request):
    """学生登录：验证姓名+学号，返回 session token（1分钟超时）"""
    body = await request.json()
    name = (body.get("name") or "").strip()
    student_id = (body.get("student_id") or "").strip()
    if not name or not student_id:
        raise HTTPException(status_code=400, detail="姓名和学号不能为空")
    from services.question_service import check_roster, find_student_class
    ok, msg = check_roster(name, student_id)
    if not ok:
        raise HTTPException(status_code=401, detail=msg)
    token = create_student_session(name, student_id)
    class_name = find_student_class(name, student_id)
    return {"ok": True, "token": token, "class_name": class_name}


def _require_student_login(request: Request, expected_name: str = "", expected_sid: str = "") -> dict:
    """校验学生 session token，从 Cookie 或 Authorization header 中提取"""
    token = request.cookies.get("student_token") or ""
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token or not validate_student_session(token):
        raise HTTPException(status_code=401, detail="登录已过期，请重新输入姓名学号")
    info = get_student_session(token)
    if info is None:
        raise HTTPException(status_code=401, detail="登录已过期")
    return info


# ── 学生个人提交历史 ────────────────────────────────────

@router.get("/submissions")
async def get_my_submissions(name: str, student_id: str):
    """返回某学生所有题目的提交记录（含上传/分析/已评分各阶段）"""
    from services.question_service import get_submission_record as _get_record
    questions = list_questions()
    results: list[dict] = []
    for q in questions:
        qid = q["id"]
        # 先查成绩 CSV
        grade_row = get_student_grade(qid, student_id)
        if grade_row:
            results.append({
                "question_id": qid,
                "question_title": q["title"],
                "student_name": grade_row.get("姓名", name),
                "student_id": grade_row.get("学号", student_id),
                "grade": grade_row.get("成绩", ""),
                "total_score": grade_row.get("总分", ""),
                "status": "completed",
                "submitted_at": grade_row.get("提交时间", ""),
            })
        else:
            # 再查 submissions.json
            rec = _get_record(qid, student_id)
            if rec:
                results.append({
                    "question_id": qid,
                    "question_title": q["title"],
                    "student_name": rec.get("name", name),
                    "student_id": student_id,
                    "grade": rec.get("grade", ""),
                    "total_score": rec.get("total_score", ""),
                    "status": rec.get("status", "uploaded"),
                    "submitted_at": rec.get("submitted_at", ""),
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


# ── 第一步：上传作业（同步，不触发LLM）─────────────────

@router.post("/upload/{qid}")
async def upload_submission(
    qid: str,
    request: Request,
    name: str = Form(...),
    student_id: str = Form(...),
    file: UploadFile = File(...),
    mode: str = Form("submit"),
):
    """上传作业文件，保存并转换图片，立即返回预览文件名"""
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
    fname = file.filename or "submission.pdf"
    set_status(qid, name, student_id, "upload", "converting")

    if is_test:
        from services.llm_service import bytes_to_base64
        bytes_to_base64(file_bytes, fname)
        set_file_data(qid, name, student_id, file_bytes, fname)
        set_status(qid, name, student_id, "upload", "done", student_filename=fname)
        return {"ok": True, "student_filename": fname}
    else:
        from services.question_service import submit_student_work
        saved_name = submit_student_work(qid, student_id, name, file_bytes, fname)
        set_status(qid, name, student_id, "upload", "done", student_filename=saved_name)
        logger.info(f"[{qid}] 文件已保存: {name}({student_id}) → {saved_name}")
        return {"ok": True, "student_filename": saved_name}


# ── 第二步：开始分析（异步非阻塞）─────────────────────────

def _run_analyze(
    qid: str, name: str, student_id: str,
    file_bytes: bytes | None, filename: str,
    is_test: bool,
    struct_tpl: Path, quant_tpl: Path,
    knowledge: str = "",
):
    """后台线程：结构分析 → 量化分析 → 保存"""
    try:
        set_status(qid, name, student_id, "analyze", "analyzing")
        if is_test:
            if not file_bytes:
                raise RuntimeError("测试模式文件数据丢失，请重新上传")
            structure = analyze_structure_bytes(file_bytes, filename, struct_tpl, knowledge=knowledge)
            quantitative = analyze_quantitative_bytes(file_bytes, filename, quant_tpl, structure, knowledge=knowledge)
        else:
            student_path = get_student_submission_path(qid, student_id, name)
            if student_path is None:
                raise RuntimeError("学生提交文件不存在，请重新上传")
            structure = analyze_structure(student_path, struct_tpl, knowledge=knowledge)
            quantitative = analyze_quantitative(student_path, quant_tpl, structure, knowledge=knowledge)

        analysis = {"structure": structure, "quantitative": quantitative}
        if not is_test:
            save_student_analysis(qid, student_id, name, analysis)
            from services.question_service import update_submission_record
            update_submission_record(qid, student_id, name,
                                     Path(filename).stem, "analyzed")

        set_status(qid, name, student_id, "analyze", "done")
        logger.info(f"[{qid}] 图面分析完成: {name}({student_id})")
    except Exception as e:
        logger.error(f"[{qid}] 图面分析失败: {e}")
        set_status(qid, name, student_id, "analyze", "error", str(e))


@router.post("/analyze/{qid}/start")
async def start_analysis(
    qid: str,
    request: Request,
    name: str = Form(...),
    student_id: str = Form(...),
    mode: str = Form("submit"),
):
    """对已上传的作业启动 LLM 结构分析 + 量化分析"""
    _check_rate_limit(request)

    q = get_question(qid)
    if q is None:
        raise HTTPException(status_code=404, detail="题目不存在")

    is_test = mode == "test"

    file_bytes: bytes | None = None
    student_fn = ""

    if is_test:
        # 测试模式：依赖内存状态
        st = get_status(qid, name, student_id)
        if st["step"] != "upload" or st["status"] != "done":
            raise HTTPException(status_code=400, detail="请先上传作业文件")
        file_bytes, _ = get_file_data(qid, name, student_id)
        if not file_bytes:
            raise HTTPException(status_code=400, detail="测试模式文件数据丢失，请重新上传")
        student_fn = st.get("student_filename", "")
    else:
        # 正式模式：查 submissions.json + 磁盘文件
        from services.question_service import get_submission_record as _get_record
        rec = _get_record(qid, student_id)
        if not rec or rec.get("status") not in ("uploaded", "analyzed"):
            raise HTTPException(status_code=400, detail="请先上传作业文件")
        student_path = get_student_submission_path(qid, student_id, name)
        if student_path is None:
            raise HTTPException(status_code=400, detail="提交文件丢失，请重新上传")
        student_fn = student_path.name

    def _task():
        kn = get_question_files(qid).get("knowledge", "")
        _run_analyze(qid, name, student_id, file_bytes, student_fn, is_test,
                     CONFIG_DIR / "结构分析_学生.txt",
                     CONFIG_DIR / "量化分析_学生.txt",
                     knowledge=kn)

    enqueue(10, _task,
            task_key=f"analyze:{qid}:{student_id}",
            task_info={"type": "analyze", "qid": qid, "name": name, "student_id": student_id})
    return {"ok": True, "status": "processing"}


# ── 第二步：两阶段评分（异步非阻塞）───────────────────────

def _run_grade(
    qid: str, name: str, student_id: str,
    is_test: bool,
    stu_data: bytes | None, stu_filename: str,
):
    """后台线程：读取分析结果 → 阶段一 + 阶段二评分 → 保存"""
    set_status(qid, name, student_id, "grade", "processing")
    if not is_test:
        from services.question_service import update_submission_record
        update_submission_record(qid, student_id, name,
                                 Path(stu_filename).stem if stu_filename else "",
                                 "grading")
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
        knowledge = files.get("knowledge", "")

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
                stu_image_path=Path("."),
                stu_data=stu_data,
                stu_filename=stu_filename,
                knowledge=knowledge,
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
                knowledge=knowledge,
            )

        grade = result.get("grade", "N/A")

        if not is_test:
            from services.question_service import find_student_class, update_submission_record
            class_name = find_student_class(name, student_id)
            save_grade(qid, student_id, name, grade, result, class_name)
            save_result_json(qid, student_id, name, result)
            update_submission_record(qid, student_id, name,
                                     Path(stu_filename).stem if stu_filename else "",
                                     "graded", grade=grade, total_score=str(result.get("total_score", "")))

        set_status(qid, name, student_id, "grade", "done")
        logger.info(f"[{qid}] 评分完成: {name}({student_id}) → {grade}")
    except Exception as e:
        logger.error(f"[{qid}] 评分失败: {e}")
        set_status(qid, name, student_id, "grade", "error", str(e))
        if not is_test:
            from services.question_service import update_submission_record
            update_submission_record(qid, student_id, name,
                                     Path(stu_filename).stem if stu_filename else "",
                                     "grade_failed", error=str(e))


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
    else:
        from services.question_service import get_submission_record as _get_record
        rec = _get_record(qid, student_id)
        if not rec:
            raise HTTPException(status_code=400, detail="请先上传作业文件")
        if rec.get("status") not in ("analyzed", "graded"):
            raise HTTPException(status_code=400, detail="请先完成图面分析")
        student_path = get_student_submission_path(qid, student_id, name)
        if student_path is None:
            raise HTTPException(status_code=400, detail="提交文件丢失，请重新上传")
        stu_filename = student_path.name
        # 检查分析结果是否存在
        from services.question_service import get_student_analysis
        stu_analysis = get_student_analysis(qid, student_id, name)
        if stu_analysis is None:
            raise HTTPException(status_code=400, detail="分析结果丢失，请重新进行分析")

    def _task():
        _run_grade(qid, name, student_id, is_test, stu_data, stu_filename)

    enqueue(10, _task,
            task_key=f"grade:{qid}:{student_id}",
            task_info={"type": "grade", "qid": qid, "name": name, "student_id": student_id})

    return {"ok": True, "status": "processing"}


# ── 成绩查询 ─────────────────────────────────────────────

@router.get("/result/{qid}/{student_id}")
async def get_result(qid: str, student_id: str):
    """查询某学生在某题的历史成绩"""
    row = get_student_grade(qid, student_id)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到成绩")
    return row


@router.get("/submission-record/{qid}")
async def get_submission_record(qid: str, name: str, student_id: str):
    """获取学生在该题目的提交记录（含文件、分析状态、成绩）"""
    from services.question_service import get_submission_record as _get_record
    record = _get_record(qid, student_id)
    if record is None:
        raise HTTPException(status_code=404, detail="无提交记录")
    # 补充实际文件名（带后缀）
    student_path = get_student_submission_path(qid, student_id, name)
    resp = dict(record)
    resp["student_filename"] = student_path.name if student_path else ""
    return resp


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
    """返回学生提交文件的 PNG 预览图，优先取预生成的 PNG"""
    from services.question_service import get_student_dir
    from fastapi.responses import FileResponse

    sdir = get_student_dir(qid)
    # 优先找预生成的 PNG
    stem = Path(filename).stem
    png_path = sdir / f"{stem}.png"
    if png_path.exists():
        return FileResponse(str(png_path), media_type="image/png")

    # 回退：实时转换（兼容旧数据）
    filepath = sdir / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404)
    from services.llm_service import image_to_base64
    import base64
    from fastapi.responses import Response
    b64 = image_to_base64(filepath)
    img_bytes = base64.b64decode(b64)
    return Response(content=img_bytes, media_type="image/jpeg")
