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
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile, File, Form
from fastapi.responses import FileResponse

from auth import create_student_session, validate_student_session, get_student_session, MIN_PASSWORD_LENGTH, STUDENT_COOKIE, _get_student_timeout
from config import CONFIG_DIR, PDF_MAGIC, get_question_dir as _get_question_dir
from services.question_service import (
    list_questions,
    get_question,
    get_question_files,
    get_student_submission_path,
    get_question_dir,
    get_reference_analysis,
    save_student_analysis,
    get_student_analysis,
    get_student_grade_json,
    reject_if_fake,
    get_student_dir,
    _sanitize_filename_part,
)
from services.llm_service import (
    analyze_merged,
    analyze_merged_bytes,
    analyze_and_grade,
    grade_combined,
)
from services.grade_service import save_grade, get_student_grade, read_all_grades
from services.submit_status import set_status, get_status, set_file_data, get_file_data
from services.task_queue import enqueue, PRIORITY_STUDENT

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/student", tags=["student"])

# ── 频率限制 ─────────────────────────────────────────────
_RATE_LIMIT: dict[str, list[float]] = {}


def _get_rate_limit() -> tuple[int, int]:
    """从 debug 配置读取频率限制（window_seconds, max_requests）"""
    try:
        from config import read_settings_debug
        rl = read_settings_debug().get("rate_limit", {})
        return rl.get("window_seconds", 60), rl.get("max_requests", 50)
    except Exception:
        return 60, 50


def _check_rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    window, max_req = _get_rate_limit()
    timestamps = _RATE_LIMIT.get(ip, [])
    timestamps = [t for t in timestamps if now - t < window]
    if len(timestamps) >= max_req:
        logger.warning(f"IP {ip} 提交过于频繁，已拒绝")
        raise HTTPException(status_code=429, detail="提交过于频繁，请稍后再试")
    timestamps.append(now)
    _RATE_LIMIT[ip] = timestamps


# ── 题目相关 ─────────────────────────────────────────────

@router.get("/questions")
async def get_questions(class_name: str = ""):
    """获取题目列表。指定 class_name 时仅返回该班别相关的题目"""
    all_qs = list_questions()
    if not class_name:
        return all_qs
    # 过滤：题目的 classes 字段包含该班别，或 classes 为空（未限制）
    result = []
    for q in all_qs:
        # 向后兼容：旧题目没有 required_frames 字段
        if "required_frames" not in q:
            q["required_frames"] = []
        q_classes = (q.get("classes") or "").strip()
        if not q_classes or class_name in q_classes:
            result.append(q)
    return result


@router.get("/questions/{qid}")
async def get_question_detail(qid: str):
    """获取题目详情（含描述、附图、参考工程图）"""
    q = get_question(qid)
    if q is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    files = get_question_files(qid)
    files.pop("reference_pdf", None)  # 不向学生暴露参考工程图，防止被复制
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
async def student_login(request: Request, response: Response):
    """学生登录：验证姓名+学号+密码，返回 session token + password_changed 标志"""
    body = await request.json()
    name = (body.get("name") or "").strip()
    student_id = (body.get("student_id") or "").strip()
    password = (body.get("password") or "").strip()
    if not name or not student_id or not password:
        raise HTTPException(status_code=400, detail="姓名、学号和密码不能为空")
    from services.question_service import check_roster, find_student_class
    from auth import verify_student_password
    ok, msg = check_roster(name, student_id)
    if not ok:
        raise HTTPException(status_code=401, detail=msg)
    class_name = find_student_class(name, student_id)
    # 校验密码
    pwd_ok, pwd_changed = verify_student_password(class_name, student_id, password)
    if not pwd_ok:
        raise HTTPException(status_code=401, detail="密码错误")
    token = create_student_session(name, student_id)
    # 设置 HttpOnly cookie（刷新页面不丢失登录）
    timeout_sec = int(_get_student_timeout().total_seconds())
    response.set_cookie(key=STUDENT_COOKIE, value=token,
                        max_age=timeout_sec, httponly=True,
                        samesite="lax", path="/")
    return {"ok": True, "token": token, "class_name": class_name, "password_changed": pwd_changed}


@router.post("/change-password")
async def student_change_password(request: Request):
    """学生修改密码（需验证旧密码）"""
    body = await request.json()
    name = (body.get("name") or "").strip()
    student_id = (body.get("student_id") or "").strip()
    class_name = (body.get("class_name") or "").strip()
    old_password = (body.get("old_password") or "").strip()
    new_password = (body.get("new_password") or "").strip()
    if not all([name, student_id, class_name, old_password, new_password]):
        raise HTTPException(status_code=400, detail="参数不完整")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"密码至少{MIN_PASSWORD_LENGTH}位")
    if old_password == new_password:
        raise HTTPException(status_code=400, detail="新密码不能与旧密码相同")
    from services.question_service import check_roster
    ok, _ = check_roster(name, student_id)
    if not ok:
        raise HTTPException(status_code=403, detail="身份校验失败")
    from auth import change_student_password
    success, msg = change_student_password(class_name, student_id, old_password, new_password)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True}


def _require_student_login(request: Request, expected_name: str = "", expected_sid: str = "") -> dict:
    """校验学生 session token，从 Cookie 或 Authorization header 中提取"""
    token = request.cookies.get(STUDENT_COOKIE) or ""
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
    # 兼容旧数据：缺失重叠率时自动补算
    if analysis.get("entities") and analysis.get("views"):
        from services.dxf_service import ensure_overlap_ratios
        analysis = ensure_overlap_ratios(analysis)
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

    # 检查提交截止时间
    deadline = q.get("deadline", "").strip()
    if deadline and mode == "submit":
        from datetime import datetime as _dt
        try:
            dl = _dt.fromisoformat(deadline)
            if _dt.now() > dl:
                raise HTTPException(status_code=400, detail=f"提交已截止（{deadline}），无法再提交作业")
        except ValueError:
            pass

    is_test = mode == "test"

    if not is_test:
        from services.question_service import check_roster, get_submission_record as _get_rec
        ok, msg = check_roster(name, student_id)
        if not ok:
            raise HTTPException(status_code=403, detail=msg)
        rec = _get_rec(qid, student_id)
        if rec and rec.get("status") == "graded":
            raise HTTPException(status_code=400, detail="作业已提交，无法修改。请等待教师打回")

    file_bytes = await file.read()
    fname = file.filename or "submission.pdf"

    # 根据题目设置的提交类型校验文件格式
    sub_type = q.get("submission_type", "pdf")  # 缺省 pdf
    ext = Path(fname).suffix.lower()
    if sub_type == "dxf":
        if ext != ".dxf":
            raise HTTPException(status_code=400, detail="本题要求提交 DXF 文件，请上传 .dxf 格式文件")
    elif sub_type == "pdf":
        if not file_bytes.startswith(PDF_MAGIC):
            raise HTTPException(status_code=400, detail="本题要求提交 PDF 文件，请上传真实的 PDF 文件")
    elif sub_type == "image":
        if file_bytes.startswith(PDF_MAGIC):
            raise HTTPException(status_code=400, detail="本题要求提交图片文件，不支持 PDF 格式")
    else:
        # 未知类型，保持 PDF 校验
        if not file_bytes.startswith(PDF_MAGIC):
            raise HTTPException(status_code=400, detail="仅支持 PDF 格式文件，请上传真实的 PDF 文件")

    set_status(qid, name, student_id, "upload", "converting")

    if is_test:
        from services.llm_service import bytes_to_base64
        bytes_to_base64(file_bytes, fname)
        # 清理旧的分析和评分文件
        from services.question_service import get_student_dir, _sanitize_filename_part
        student_dir = get_student_dir(qid)
        safe_name = _sanitize_filename_part(name)
        safe_id = _sanitize_filename_part(student_id)
        stem = f"{safe_name}_{safe_id}"
        if student_dir.exists():
            for old in [f"{stem}_分析.json", f"{stem}_评分.json", f"{stem}.json"]:
                (student_dir / old).unlink(missing_ok=True)

        if not is_test:
            from services.question_service import update_submission_record
            update_submission_record(qid, student_id, name, Path(fname).stem, "uploaded")
        set_file_data(qid, name, student_id, file_bytes, fname)
        set_status(qid, name, student_id, "upload", "done", student_filename=fname)
        return {"ok": True, "student_filename": fname}
    else:
        from services.question_service import submit_student_work, update_submission_record
        saved_name = submit_student_work(qid, student_id, name, file_bytes, fname)
        update_submission_record(qid, student_id, name, Path(saved_name).stem, "uploaded")
        set_status(qid, name, student_id, "upload", "done", student_filename=saved_name)
        logger.info(f"[{qid}] 文件已保存: {name}({student_id}) → {saved_name}")
        return {"ok": True, "student_filename": saved_name}


# ── 第二步：开始分析（异步非阻塞）─────────────────────────

def _run_analyze(
    qid: str, name: str, student_id: str,
    file_bytes: bytes | None, filename: str,
    is_test: bool,
    template_text: str,
    knowledge: str = "",
):
    """后台线程：工程图识读 → 保存（PDF/图片用 LLM，DXF 用 ezdxf 提取）"""
    try:
        set_status(qid, name, student_id, "analyze", "analyzing")

        # 判断是否为 DXF 题目
        q = get_question(qid)
        is_dxf = q.get("submission_type") == "dxf" if q else False

        if is_dxf:
            # DXF 流程：使用统一 process_dxf 提取数据 + 渲染预览
            if is_test:
                raise RuntimeError("DXF 文件暂不支持测试模式，请使用正式提交")
            student_path = get_student_submission_path(qid, student_id, name)
            if student_path is None:
                raise RuntimeError("学生提交文件不存在，请重新上传")
            from services.dxf_service import process_dxf
            analysis = process_dxf(student_path)
            if not analysis.get("entity_counts"):
                raise RuntimeError("DXF 数据提取结果为空，文件可能不包含有效实体")
        else:
            # PDF/图片流程：LLM 视觉识读
            if is_test:
                if not file_bytes:
                    raise RuntimeError("测试模式文件数据丢失，请重新上传")
                analysis = analyze_merged_bytes(file_bytes, filename, template_text, knowledge=knowledge)
            else:
                student_path = get_student_submission_path(qid, student_id, name)
                if student_path is None:
                    raise RuntimeError("学生提交文件不存在，请重新上传")

                # 虚假作业判别
                if reject_if_fake(qid, student_id, name, student_path,
                                  Path(filename).stem if not is_test else ""):
                    set_status(qid, name, student_id, "analyze", "done")
                    return

                analysis = analyze_merged(student_path, template_text, knowledge=knowledge)

            # 校验：工程图概述为空或过短表示 LLM 未真正读图
            overview = analysis.get("工程图概述", "")
            if not overview or len(str(overview)) < 10:
                raise RuntimeError("工程图识读结果为空（概述过短），模型可能未能正确识读图片，请稍后重试")

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

    # 判断是否为 DXF 题目
    q = get_question(qid)
    is_dxf = q.get("submission_type") == "dxf" if q else False

    if is_test:
        st = get_status(qid, name, student_id)
        if st["step"] != "upload" or st["status"] != "done":
            raise HTTPException(status_code=400, detail="请先上传作业文件")
        file_bytes, _ = get_file_data(qid, name, student_id)
        if not file_bytes:
            raise HTTPException(status_code=400, detail="测试模式文件数据丢失，请重新上传")
        student_fn = st.get("student_filename", "")
    else:
        student_path = get_student_submission_path(qid, student_id, name)
        if student_path is None:
            raise HTTPException(status_code=400, detail="没有找到作业文件，请先上传")
        student_fn = student_path.name

    if is_dxf and not is_test:
        # DXF 分析走独立串行队列（无需 LLM）
        from services.dxf_task_queue import enqueue as dxf_enqueue

        def _dxf_task():
            _run_analyze(qid, name, student_id, None, student_fn, False, "", knowledge="")

        set_status(qid, name, student_id, "analyze", "queued")
        dxf_enqueue(_dxf_task, task_key=f"dxf_analyze:{qid}:{student_id}")
        return {"ok": True, "status": "processing"}

    def _task():
        kn = get_question_files(qid).get("knowledge", "")
        from config import get_question_template
        template_text = get_question_template(qid)
        _run_analyze(qid, name, student_id, file_bytes, student_fn, is_test,
                     template_text,
                     knowledge=kn)

    # 在任务入队前立即更新状态，避免前端轮询读到上一步的旧状态
    set_status(qid, name, student_id, "analyze", "queued")
    enqueue(PRIORITY_STUDENT, _task,
            task_key=f"analyze:{qid}:{student_id}",
            task_info={"type": "analyze", "qid": qid, "name": name, "student_id": student_id})
    return {"ok": True, "status": "processing"}


# ── 第二步：两阶段评分（异步非阻塞）───────────────────────

def _run_grade(
    qid: str, name: str, student_id: str,
    is_test: bool,
    stu_data: bytes | None, stu_filename: str,
):
    """后台线程：评分。DXF 用 grade_dxf，PDF/图片用现有流程。"""
    set_status(qid, name, student_id, "grade", "processing")
    from services.question_service import update_submission_record

    if not is_test:
        update_submission_record(qid, student_id, name,
                                 Path(stu_filename).stem if stu_filename else "",
                                 "grading")
    try:
        ref_analysis = get_reference_analysis(qid)
        if ref_analysis is None:
            raise RuntimeError("参考图尚未完成分析，请联系老师")

        files = get_question_files(qid)
        phase1_criteria = files.get("phase1_criteria", "")
        phase2_criteria = files.get("phase2_criteria", "")
        knowledge = files.get("knowledge", "")

        qdir = _get_question_dir(qid)

        # 判断是否为 DXF 题目
        q = get_question(qid)
        is_dxf = q.get("submission_type") == "dxf" if q else False

        if is_dxf:
            # DXF 流程：统一入口
            if is_test:
                raise RuntimeError("DXF 文件暂不支持测试模式")
            stu_path = get_student_submission_path(qid, student_id, name)
            if stu_path is None:
                raise RuntimeError("学生提交文件不存在，请重新上传")
            if stu_path.suffix.lower() != ".dxf":
                raise RuntimeError(
                    f"提交文件不是 DXF（实际为 {stu_path.suffix}），请重新上传 DXF 文件")

            stu_analysis = get_student_analysis(qid, student_id, name)
            if stu_analysis is None:
                raise RuntimeError("DXF 数据尚未提取，请先完成分析步骤")

            from services.dxf_service import run_dxf_grade
            required_frames_dxf = q.get("required_frames", [])
            _, result = run_dxf_grade(
                student_dxf_path=stu_path,
                ref_data=ref_analysis,
                ref_dir=qdir,
                phase1_criteria=phase1_criteria,
                phase2_criteria=phase2_criteria,
                stu_dxf_data=stu_analysis,
                knowledge=knowledge,
                required_frames=required_frames_dxf or None,
            )

        else:
            # PDF/图片流程（现有逻辑）
            ref_pdf = qdir / "参考工程图.pdf"
            if not ref_pdf.exists():
                raise RuntimeError("参考工程图不存在，请联系老师")

            from config import get_question_template
            template_text = get_question_template(qid)

            stu_analysis = get_student_analysis(qid, student_id, name)

            if is_test:
                if not stu_data:
                    raise RuntimeError("测试模式文件数据丢失，请重新上传")
                import tempfile
                ext = Path(stu_filename).suffix or ".png"
                with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                    tmp.write(stu_data)
                    tmp_path = Path(tmp.name)
                stu_path = tmp_path
            else:
                stu_path = get_student_submission_path(qid, student_id, name)
                if stu_path is None:
                    raise RuntimeError("学生提交文件不存在，请重新上传")
                # 虚假作业判别
                if reject_if_fake(qid, student_id, name, stu_path,
                                  Path(stu_filename).stem if stu_filename else ""):
                    set_status(qid, name, student_id, "grade", "done")
                    return

            try:
                if stu_analysis is not None:
                    # 已有分析 → 只做评分
                    logger.info(f"[{qid}] 已有分析结果，直接评分")
                    result = grade_combined(
                        stu_analysis=stu_analysis,
                        ref_analysis=ref_analysis,
                        phase1_criteria=phase1_criteria,
                        phase2_criteria=phase2_criteria,
                        ref_image_path=ref_pdf,
                        stu_image_path=stu_path,
                        knowledge=knowledge,
                    )
                else:
                    # 无分析 → 识读+评分一起做
                    result = analyze_and_grade(
                        image_path=stu_path,
                        template_text=template_text,
                        ref_analysis=ref_analysis,
                        phase1_criteria=phase1_criteria,
                        phase2_criteria=phase2_criteria,
                        ref_image_path=ref_pdf,
                        knowledge=knowledge,
                    )
            finally:
                if is_test:
                    try:
                        stu_path.unlink(missing_ok=True)
                    except Exception:
                        pass

            # 校验（仅 PDF/图片流程）
            overview = result.get("工程图概述", "")
            if not overview or len(str(overview)) < 10:
                raise RuntimeError("工程图识读结果为空，模型可能未能正确识读图片")

        grade = result.get("grade", "N/A")
        if not is_test:
            from services.question_service import find_student_class, save_student_grade
            # 计算提交文件的 SHA-256
            import hashlib
            file_sha256 = hashlib.sha256(stu_path.read_bytes()).hexdigest() if not is_test else ""
            # 识读数据存 _分析.json（非 DXF 新分析才有，grade_combined 路径无；DXF 已在 analyze 阶段保存）
            if not is_dxf and "工程图概述" in result and not stu_analysis:
                save_student_analysis(qid, student_id, name, result)
            # 评分数据存 _评分.json
            save_student_grade(qid, student_id, name, result)
            # CSV
            class_name = find_student_class(name, student_id)
            save_grade(qid, student_id, name, grade, result, class_name, file_sha256=file_sha256)
            # 标记为已提交，加锁
            update_submission_record(qid, student_id, name,
                                     Path(stu_filename).stem,
                                     "graded", grade=grade,
                                     total_score=str(result.get("total_score", "")))

        set_status(qid, name, student_id, "grade", "done")
        logger.info(f"[{qid}] 评分完成: {name}({student_id}) → {grade}")
    except Exception as e:
        import traceback
        logger.error(f"[{qid}] 评分失败: {e}\n{traceback.format_exc()}")
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
        # 检查作业文件是否存在
        student_path = get_student_submission_path(qid, student_id, name)
        if student_path is None:
            raise HTTPException(status_code=400, detail="没有找到作业文件，请先上传")
        # 检查是否已完成分析（需要分析 JSON 文件）
        analysis = get_student_analysis(qid, student_id, name)
        if analysis is None:
            raise HTTPException(status_code=400, detail="请先完成预览分析")
        # 检查是否已提交过
        existing_grade = get_student_grade_json(qid, student_id, name)
        if existing_grade is not None:
            raise HTTPException(status_code=400, detail="作业已提交，请等待教师批阅")
        stu_filename = student_path.name

    def _task():
        _run_grade(qid, name, student_id, is_test, stu_data, stu_filename)

    # 在任务入队前立即更新状态，避免前端轮询读到上一步的旧状态
    set_status(qid, name, student_id, "grade", "queued")
    enqueue(PRIORITY_STUDENT, _task,
            task_key=f"grade:{qid}:{student_id}",
            task_info={"type": "grade", "qid": qid, "name": name, "student_id": student_id})

    return {"ok": True, "status": "processing"}


# ── 成绩查询 ─────────────────────────────────────────────

@router.get("/result/{qid}/{student_id}")
async def get_result(qid: str, student_id: str):
    """查询某学生在某题的历史成绩（CSV + 评分 JSON 合并）"""
    row = get_student_grade(qid, student_id)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到成绩")
    # 合并评分 JSON 中的额外字段（重合度、重叠率等）
    try:
        name = row.get("姓名", "")
        grade_json = get_student_grade_json(qid, student_id, name)
        if grade_json:
            for k in ("view_overlap_ratios", "view_coincidence", "_model", "_usage"):
                if k in grade_json:
                    row[k] = grade_json[k]
    except Exception:
        pass
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

def _check_path_safe(base: Path, target: Path) -> Path:
    """确保 target 在 base 目录内，防止路径穿越攻击"""
    resolved = target.resolve()
    if not str(resolved).startswith(str(base.resolve()) + os.sep):
        raise HTTPException(status_code=404, detail="文件未找到")
    return resolved


@router.get("/files/{qid}/{filename}")
async def serve_student_file(qid: str, filename: str):
    """直接下载学生提交的原始文件"""
    from services.question_service import get_student_dir
    sdir = get_student_dir(qid).resolve()
    filepath = _check_path_safe(sdir, Path(sdir / filename))
    if not filepath.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(str(filepath))


@router.get("/preview/{qid}/{filename}")
async def serve_student_preview(qid: str, filename: str):
    """返回学生提交文件的 PNG 预览图，优先取预生成的 PNG"""
    from services.question_service import get_student_dir
    from fastapi.responses import FileResponse

    sdir = get_student_dir(qid).resolve()
    # 优先找预生成的 PNG（stem 已剥离路径，安全）
    stem = Path(filename).stem
    png_path = _check_path_safe(sdir, Path(sdir / f"{stem}.png"))
    if png_path.is_file():
        return FileResponse(str(png_path), media_type="image/png")

    # 回退：实时转换（兼容旧数据）
    filepath = _check_path_safe(sdir, Path(sdir / filename))
    if not filepath.is_file():
        raise HTTPException(status_code=404)
    from services.llm_service import image_to_base64
    import base64
    from fastapi.responses import Response
    b64 = image_to_base64(filepath)
    img_bytes = base64.b64decode(b64)
    return Response(content=img_bytes, media_type="image/jpeg")
