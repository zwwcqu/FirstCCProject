"""
教师端 API 路由（前缀 /api/teacher）。

功能：
- 登录/登出（HttpOnly Cookie + Session）
- 题目 CRUD（创建时支持上传附图 + 参考工程图，自动触发 LLM 分析）
- 参考图分析（手动触发 + 结果查询）
- 成绩查询（CSV 按题号返回）
- 系统设置（LLM 配置 + 密码修改）
- 学生名单管理（班级导入/查看/删除/模板下载）
- 文件服务（题目文件下载 + 预览图生成）

所有接口（除 login/文件服务外）均需登录校验 (_require_auth)。
"""

import json
import logging
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile, File, Form

from auth import verify_password, create_session, validate_session, destroy_session, change_password
from config import CONFIG_DIR, get_question_dir, read_settings, write_settings
from services.question_service import (
    list_questions,
    create_question,
    update_question,
    delete_question,
    save_question_image,
    save_reference_pdf,
    get_question_files,
    get_scoring_templates,
    save_reference_analysis,
    get_reference_analysis,
    get_submissions,
    get_submission_record,
    get_student_submission_path,
    get_student_analysis,
    get_student_dir,
    update_submission_record,
    sync_submissions_from_disk,
    _sanitize_filename_part,
)
from services.grade_service import read_all_grades, get_grades_csv_path, FIELDNAMES, save_grade, get_student_grade, save_result_json, remove_grade

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/teacher", tags=["teacher"])


def _require_auth(request: Request) -> None:
    """从 Cookie 中取 session token，校验登录状态"""
    token = request.cookies.get("session")
    if not token or not validate_session(token):
        raise HTTPException(status_code=401, detail="请先登录")


def _run_reference_analysis(qid: str) -> None:
    """
    教师参考图分析，通过任务队列以最高优先级执行。
    """
    from services.llm_service import analyze_structure, analyze_quantitative
    from services.task_queue import enqueue

    def _task():
        qdir = get_question_dir(qid)
        ref_pdf = qdir / "参考工程图.pdf"
        if not ref_pdf.exists():
            logger.warning(f"[{qid}] 参考工程图不存在，跳过分析")
            return

        kn = get_question_files(qid).get("knowledge", "")
        struct_tpl = CONFIG_DIR / "结构分析模版.txt"
        quant_tpl = CONFIG_DIR / "量化分析模版.txt"

        logger.info(f"[{qid}] 开始参考图结构分析…")
        structure = analyze_structure(ref_pdf, struct_tpl, knowledge=kn)
        logger.info(f"[{qid}] 参考图量化分析…")
        quantitative = analyze_quantitative(ref_pdf, quant_tpl, structure, knowledge=kn)

        save_reference_analysis(qid, {"structure": structure, "quantitative": quantitative})
        logger.info(f"[{qid}] 参考图分析完成并已保存")

    enqueue(0, _task,
            task_key=f"ref_analyze:{qid}",
            task_info={"type": "ref_analyze", "qid": qid})


# ── 登录 / 登出 ──────────────────────────────────────────

@router.post("/login")
async def login(response: Response, password: str = Form(...)):
    """教师登录，成功后设置 HttpOnly Cookie"""
    if not verify_password(password):
        raise HTTPException(status_code=403, detail="密码错误")
    token = create_session()
    response.set_cookie(
        key="session",
        value=token,
        max_age=14400,          # 4 小时
        httponly=True,          # JS 不可读，防 XSS
        samesite="lax",
        path="/",
    )
    logger.info("教师登录成功")
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request, response: Response):
    """教师登出，销毁 session + 清除 Cookie"""
    token = request.cookies.get("session")
    if token:
        destroy_session(token)
    response.delete_cookie(key="session", path="/")
    logger.info("教师已登出")
    return {"ok": True}


@router.get("/check")
async def check_login(request: Request):
    """检查登录状态，用于前端页面刷新时验证"""
    token = request.cookies.get("session")
    if token and validate_session(token):
        return {"ok": True}
    raise HTTPException(status_code=401)


# ── 题目管理 ─────────────────────────────────────────────

@router.get("/questions")
async def get_questions(request: Request):
    """获取所有题目列表（含文件信息）"""
    _require_auth(request)
    questions = list_questions()
    result = []
    for q in questions:
        files = get_question_files(q["id"])     # 附带描述/图片/参考图信息
        q["files"] = files
        result.append(q)
    return result


@router.post("/questions")
async def create_question_handler(
    request: Request,
    qid: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    phase1_criteria: str = Form(""),
    phase2_criteria: str = Form(""),
    knowledge: str = Form(""),
    image: Optional[UploadFile] = File(None),           # 题目附图（可选）
    reference_pdf: Optional[UploadFile] = File(None),   # 参考工程图（可选）
):
    """新增题目"""
    _require_auth(request)
    try:
        entry = create_question(qid, title, description, phase1_criteria, phase2_criteria, knowledge)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if image and image.filename:
        img_bytes = await image.read()
        save_question_image(qid, img_bytes, image.filename)
    if reference_pdf and reference_pdf.filename:
        pdf_bytes = await reference_pdf.read()
        save_reference_pdf(qid, pdf_bytes, reference_pdf.filename)
        _run_reference_analysis(qid)   # 后台异步分析参考图
    return entry


@router.put("/questions/{qid}")
async def update_question_handler(
    request: Request,
    qid: str,
    title: str = Form(...),
    description: str = Form(""),
    phase1_criteria: str = Form(""),
    phase2_criteria: str = Form(""),
    knowledge: str = Form(""),
    image: Optional[UploadFile] = File(None),
    reference_pdf: Optional[UploadFile] = File(None),
):
    """编辑已有题目"""
    _require_auth(request)
    entry = update_question(qid, title, description, phase1_criteria, phase2_criteria, knowledge)
    if entry is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    if image and image.filename:
        img_bytes = await image.read()
        save_question_image(qid, img_bytes, image.filename)
    if reference_pdf and reference_pdf.filename:
        pdf_bytes = await reference_pdf.read()
        save_reference_pdf(qid, pdf_bytes, reference_pdf.filename)
        _run_reference_analysis(qid)   # 参考图更新后重新分析
    return entry


@router.delete("/questions/{qid}")
async def delete_question_handler(request: Request, qid: str):
    """删除题目（数据移到 backup/ 目录）"""
    _require_auth(request)
    ok = delete_question(qid)
    if not ok:
        raise HTTPException(status_code=404, detail="题目不存在")
    return {"ok": True}


@router.get("/scoring-templates")
async def get_templates(request: Request):
    """获取评分模板内容，供新增/编辑题目时预填"""
    _require_auth(request)
    return get_scoring_templates()


# ── 参考图分析 ────────────────────────────────────────────

@router.post("/questions/{qid}/analyze")
async def trigger_analysis(request: Request, qid: str):
    """手动触发参考图分析（用于重分析已有参考图）"""
    _require_auth(request)
    qdir = get_question_dir(qid)
    ref_pdf = qdir / "参考工程图.pdf"
    if not ref_pdf.exists():
        raise HTTPException(status_code=400, detail="请先上传参考工程图 PDF")
    _run_reference_analysis(qid)
    return {"ok": True, "message": "分析已启动，请稍后查询结果"}


@router.get("/questions/{qid}/analysis")
async def get_analysis_result(request: Request, qid: str):
    """获取参考图的分析结果（结构 + 量化 JSON）"""
    _require_auth(request)
    analysis = get_reference_analysis(qid)
    if analysis is None:
        return {"ok": True, "ready": False, "analysis": None}
    return {"ok": True, "ready": True, "analysis": analysis}


# ── 成绩查询 ─────────────────────────────────────────────

@router.get("/grades/{qid}")
async def get_grades(request: Request, qid: str):
    """查看某题所有学生成绩，含未评分但已提交的学生"""
    _require_auth(request)
    # CSV 中已有成绩的学生
    graded_rows = read_all_grades(qid)
    graded_ids = {r.get("学号", "") for r in graded_rows}

    # submissions.json 中已提交但无成绩的学生
    submissions = get_submissions(qid)
    ungraded_rows: list[dict] = []
    for sid, rec in submissions.items():
        if sid not in graded_ids:
            student_path = get_student_submission_path(qid, sid, rec.get("name", ""))
            ungraded_rows.append({
                "班级": "",
                "姓名": rec.get("name", ""),
                "学号": sid,
                "提交时间": rec.get("submitted_at", ""),
                "成绩": "",
                "阶段1相似度": "",
                "阶段2评分": "",
                "总分": "",
                "相似度评价": "",
                "阶段2评语": "",
                "总评": "",
                "图样表达": "",
                "尺寸标注": "",
                "尺寸公差": "",
                "表面质量": "",
                "形位公差": "",
                "技术要求": "",
                "_status": rec.get("status", "uploaded"),
                "_filename": student_path.name if student_path else "",
                "_error": rec.get("error", ""),
            })

    # 给已评分行补上 _status、_filename、_error
    for row in graded_rows:
        sid = row.get("学号", "")
        name = row.get("姓名", "")
        rec = get_submission_record(qid, sid)
        row["_status"] = rec.get("status", "graded") if rec else "graded"
        row["_error"] = rec.get("error", "") if rec else ""
        # 有 record 用 record 中的名字查文件，否则用 CSV 中的名字查
        lookup_name = rec.get("name", "") if rec else name
        student_path = get_student_submission_path(qid, sid, lookup_name)
        row["_filename"] = student_path.name if student_path else ""

    all_rows = graded_rows + ungraded_rows
    return {"qid": qid, "grades": all_rows, "columns": FIELDNAMES}


@router.post("/grades/{qid}/batch-grade")
async def batch_grade(request: Request, qid: str):
    """批量评分：对选中的学生启动后台评分任务"""
    _require_auth(request)
    body = await request.json()
    student_ids: list[str] = body.get("student_ids", [])
    if not student_ids:
        raise HTTPException(status_code=400, detail="请选择至少一名学生")

    from services.llm_service import run_two_phase_grading, analyze_structure, analyze_quantitative
    from services.task_queue import enqueue

    ref_analysis = get_reference_analysis(qid)
    if ref_analysis is None:
        raise HTTPException(status_code=400, detail="参考图尚未完成分析，请先分析参考图")

    files = get_question_files(qid)
    phase1_criteria = files.get("phase1_criteria", "")
    phase2_criteria = files.get("phase2_criteria", "")
    knowledge = files.get("knowledge", "")
    qdir = get_question_dir(qid)
    ref_pdf = qdir / "参考工程图.pdf"
    struct_tpl = CONFIG_DIR / "结构分析_学生.txt"
    quant_tpl = CONFIG_DIR / "量化分析_学生.txt"

    def _grade_one(sid: str):
        from services.question_service import find_student_class, update_submission_record, save_student_analysis

        rec = get_submission_record(qid, sid)
        if not rec:
            return
        name = rec.get("name", "")
        student_path = get_student_submission_path(qid, sid, name)
        if student_path is None:
            return

        # 如果还没有分析，先跑分析
        stu_analysis = get_student_analysis(qid, sid, name)
        if stu_analysis is None:
            try:
                update_submission_record(qid, sid, name, student_path.stem, "analyzing")
                structure = analyze_structure(student_path, struct_tpl, knowledge=knowledge)
                quantitative = analyze_quantitative(student_path, quant_tpl, structure, knowledge=knowledge)
                stu_analysis = {"structure": structure, "quantitative": quantitative}
                save_student_analysis(qid, sid, name, stu_analysis)
                update_submission_record(qid, sid, name, student_path.stem, "analyzed")
            except Exception as e:
                logger.error(f"[{qid}] 自动分析失败 {name}({sid}): {e}")
                update_submission_record(qid, sid, name, student_path.stem,
                                         "analyze_failed", error=str(e))
                return

        # 标记评分中
        update_submission_record(qid, sid, name, student_path.stem, "grading")
        try:
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
            class_name = find_student_class(name, sid)
            save_grade(qid, sid, name, grade, result, class_name)
            save_result_json(qid, sid, name, result)
            update_submission_record(qid, sid, name, student_path.stem,
                                     "graded", grade=grade,
                                     total_score=str(result.get("total_score", "")))
            logger.info(f"[{qid}] 批量评分完成: {name}({sid}) → {grade}")
        except Exception as e:
            logger.error(f"[{qid}] 批量评分失败 {name}({sid}): {e}")
            update_submission_record(qid, sid, name, student_path.stem,
                                     "grade_failed", error=str(e))

    # 每个学生独立入队，实现真正的多 worker 并发（单学生内 3 步骤仍顺序执行）
    for sid in student_ids:
        rec = get_submission_record(qid, sid)
        name = rec.get("name", "") if rec else ""
        # 注意：lambda s=sid 捕获当前值，避免 Python 闭包延迟绑定陷阱
        enqueue(5, (lambda s: lambda: _grade_one(s))(sid),
                task_key=f"grade:{qid}:{sid}",
                task_info={"type": "grade", "qid": qid, "sid": sid, "name": name})


@router.post("/grades/{qid}/batch-clear")
async def batch_clear_grades(request: Request, qid: str):
    """批量清除评分：删除选中学生的成绩记录 + 结构/量化分析文件"""
    _require_auth(request)
    body = await request.json()
    student_ids: list[str] = body.get("student_ids", [])
    if not student_ids:
        raise HTTPException(status_code=400, detail="请选择至少一名学生")

    student_dir = get_student_dir(qid)
    cleared = 0
    for sid in student_ids:
        rec = get_submission_record(qid, sid)
        name = rec.get("name", "") if rec else ""
        if name:
            safe_name = _sanitize_filename_part(name)
            safe_id = _sanitize_filename_part(sid)
            stem = f"{safe_name}_{safe_id}"
            # 删除分析/结果 JSON 文件（保留 PDF/PNG 原文件）
            if student_dir.exists():
                for f in list(student_dir.iterdir()):
                    if f.suffix.lower() in (".pdf", ".png"):
                        continue
                    if f.stem == stem or f.stem.startswith(stem + "_"):
                        f.unlink()
                        logger.info(f"[{qid}] 已删除分析文件: {f.name}")
            # 删除成绩记录
            remove_grade(qid, sid)
            # 更新提交状态为 uploaded（尚未评分）
            update_submission_record(qid, sid, name, stem, "uploaded")
            cleared += 1

    return {"ok": True, "cleared": cleared}


@router.post("/grades/{qid}/supplement-submission")
async def supplement_submission(
    request: Request,
    qid: str,
    name: str = Form(...),
    student_id: str = Form(...),
    file: UploadFile = File(...),
):
    """教师补充提交学生作业，需校验名单"""
    _require_auth(request)

    from services.question_service import check_roster
    ok, msg = check_roster(name, student_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    file_bytes = await file.read()
    fname = file.filename or "submission.pdf"

    from services.question_service import submit_student_work
    saved_name = submit_student_work(qid, student_id, name, file_bytes, fname)
    logger.info(f"[{qid}] 教师补充提交: {name}({student_id}) → {saved_name}")
    return {"ok": True, "student_filename": saved_name}


@router.post("/grades/{qid}/refresh")
async def refresh_grades(request: Request, qid: str):
    """扫描磁盘文件，自动发现名单中学生的提交，同步返回最新成绩列表"""
    _require_auth(request)

    added = sync_submissions_from_disk(qid)

    # 复用成绩查询逻辑
    graded_rows = read_all_grades(qid)
    graded_ids = {r.get("学号", "") for r in graded_rows}

    submissions = get_submissions(qid)
    ungraded_rows: list[dict] = []
    for sid, rec in submissions.items():
        if sid not in graded_ids:
            student_path = get_student_submission_path(qid, sid, rec.get("name", ""))
            ungraded_rows.append({
                "班级": "",
                "姓名": rec.get("name", ""),
                "学号": sid,
                "提交时间": rec.get("submitted_at", ""),
                "成绩": "",
                "阶段1相似度": "",
                "阶段2评分": "",
                "总分": "",
                "相似度评价": "",
                "阶段2评语": "",
                "总评": "",
                "图样表达": "",
                "尺寸标注": "",
                "尺寸公差": "",
                "表面质量": "",
                "形位公差": "",
                "技术要求": "",
                "_status": rec.get("status", "uploaded"),
                "_filename": student_path.name if student_path else "",
                "_error": rec.get("error", ""),
            })

    for row in graded_rows:
        sid = row.get("学号", "")
        name = row.get("姓名", "")
        rec = get_submission_record(qid, sid)
        row["_status"] = rec.get("status", "graded") if rec else "graded"
        row["_error"] = rec.get("error", "") if rec else ""
        lookup_name = rec.get("name", "") if rec else name
        student_path = get_student_submission_path(qid, sid, lookup_name)
        row["_filename"] = student_path.name if student_path else ""

    all_rows = graded_rows + ungraded_rows
    return {"qid": qid, "grades": all_rows, "columns": FIELDNAMES, "added": added}


@router.put("/grades/{qid}/{student_id}")
async def edit_grade(request: Request, qid: str, student_id: str):
    """修改单个学生的成绩字段"""
    _require_auth(request)
    body = await request.json()
    row = get_student_grade(qid, student_id)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到该学生成绩")

    # 更新指定字段
    for key in body:
        if key in FIELDNAMES:
            row[key] = str(body[key]) if body[key] is not None else ""

    from services.question_service import find_student_class
    class_name = find_student_class(row.get("姓名", ""), student_id)
    # 通过 CSV 列名反向映射回 comments dict
    comments = {
        "phase1_similarity": row.get("阶段1相似度", ""),
        "phase2_criteria": row.get("阶段2评分", ""),
        "total_score": row.get("总分", ""),
        "phase1_comment": row.get("相似度评价", ""),
        "phase2_comment": row.get("阶段2评语", ""),
        "总评": row.get("总评", ""),
        "图样表达": row.get("图样表达", ""),
        "尺寸标注": row.get("尺寸标注", ""),
        "尺寸公差": row.get("尺寸公差", ""),
        "表面质量": row.get("表面质量", ""),
        "形位公差": row.get("形位公差", ""),
        "技术要求": row.get("技术要求", ""),
        "教师评语": row.get("教师评语", ""),
    }
    save_grade(qid, student_id, row.get("姓名", ""), row.get("成绩", ""),
               comments, class_name)
    return {"ok": True}


@router.get("/student-analysis/{qid}/{student_id}")
async def teacher_student_analysis(request: Request, qid: str, student_id: str, name: str = ""):
    """教师查看学生的图面分析结果（结构分析 + 量化分析）"""
    _require_auth(request)
    from services.question_service import get_student_analysis, get_submission_record

    # 尝试从提交记录中获取姓名
    if not name:
        rec = get_submission_record(qid, student_id)
        if rec:
            name = rec.get("name", "")
    if not name:
        raise HTTPException(status_code=400, detail="无法确定学生姓名")

    analysis = get_student_analysis(qid, student_id, name)
    if analysis is None:
        return {"ok": True, "ready": False, "analysis": None}
    return {"ok": True, "ready": True, "analysis": analysis}


@router.get("/student-preview/{qid}/{student_id}")
async def teacher_student_preview(request: Request, qid: str, student_id: str):
    """教师查看学生提交的工程图预览（优先 PNG，回退实时转换）"""
    _require_auth(request)
    from fastapi.responses import FileResponse

    rec = get_submission_record(qid, student_id)
    if not rec:
        raise HTTPException(status_code=404, detail="该学生未提交作业")
    name = rec.get("name", "")

    # 优先找预生成的 PNG
    from services.question_service import get_student_dir, _sanitize_filename_part
    sdir = get_student_dir(qid)
    safe_name = _sanitize_filename_part(name)
    safe_id = _sanitize_filename_part(student_id)
    png_path = sdir / f"{safe_name}_{safe_id}.png"
    if png_path.exists():
        return FileResponse(str(png_path), media_type="image/png")

    # 回退：实时转换
    student_path = get_student_submission_path(qid, student_id, name)
    if student_path is None:
        raise HTTPException(status_code=404, detail="提交文件不存在")
    from services.llm_service import image_to_base64
    import base64
    from fastapi.responses import Response
    b64 = image_to_base64(student_path)
    img_bytes = base64.b64decode(b64)
    return Response(content=img_bytes, media_type="image/jpeg")


# ── 系统设置 ─────────────────────────────────────────────

@router.get("/settings")
async def get_settings(request: Request):
    """获取当前 LLM 配置（API 地址 / 密钥 / 模型名），不返回密码"""
    _require_auth(request)
    settings = read_settings()
    return {
        "models": settings.get("models", []),
        "llm_active": settings.get("llm_active", 0),
    }


@router.put("/settings")
async def update_settings(request: Request):
    """更新系统设置，密码修改走 auth.change_password 做哈希"""
    _require_auth(request)
    body = await request.json()
    settings = read_settings()

    if "models" in body:
        models = body["models"]
        for m in models:
            m["concurrency"] = max(1, min(5, m.get("concurrency", 1)))
        settings["models"] = models
    if "llm_active" in body:
        settings["llm_active"] = int(body["llm_active"])

    # 密码修改走哈希流程
    if "teacher_password" in body and body["teacher_password"]:
        change_password(body["teacher_password"])
        write_settings(settings)   # 写入其他设置项
        logger.info("系统设置和密码已更新")
        return {"ok": True}

    write_settings(settings)
    logger.info("系统设置已更新")
    return {"ok": True}


@router.post("/settings/test")
async def test_llm_connection(request: Request):
    """测试大模型连接。body: {api_base, api_key, model}"""
    _require_auth(request)
    body = await request.json()
    base_url = (body.get("api_base") or "").strip()
    api_key = (body.get("api_key") or "").strip()
    model = (body.get("model") or "").strip()

    if not base_url:
        return {"ok": False, "message": "请先填写 API 地址"}

    try:
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=10)
        if model:
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=5,
            )
        else:
            models = client.models.list()
            if not models.data:
                return {"ok": False, "message": "连接成功但未找到可用模型"}
            model = models.data[0].id
        return {"ok": True, "message": f"连接成功，模型: {model}"}
    except Exception as e:
        return {"ok": False, "message": f"连接失败: {str(e)}"}


@router.post("/settings/change-password")
async def change_password_handler(request: Request):
    """修改密码：需验证当前密码正确后才允许修改"""
    _require_auth(request)
    body = await request.json()
    current = (body.get("current_password") or "").strip()
    new = (body.get("new_password") or "").strip()

    if not current:
        raise HTTPException(status_code=400, detail="请输入当前密码")
    if not new:
        raise HTTPException(status_code=400, detail="请输入新密码")

    if not verify_password(current):
        raise HTTPException(status_code=403, detail="当前密码错误")

    change_password(new)
    logger.info("教师密码已通过验证后修改")
    return {"ok": True}


@router.post("/settings/query-model")
async def query_current_model(request: Request):
    """查询当前激活模型的详细信息。读取 settings 中的当前配置，向 API 查询模型详情并验证可用性"""
    _require_auth(request)
    settings = read_settings()
    models = settings.get("models", [])
    idx = settings.get("llm_active", 0)
    if not models or idx >= len(models):
        return {"ok": False, "message": "没有激活的模型配置"}
    cfg = models[idx]
    base_url = (cfg.get("api_base") or "").strip()
    api_key = (cfg.get("api_key") or "").strip()
    model_id = (cfg.get("model") or "").strip()

    if not base_url:
        return {"ok": False, "message": "当前模型未配置 API 地址"}

    from openai import OpenAI
    client = OpenAI(base_url=base_url, api_key=api_key, timeout=10)

    model_info = None
    source = "unknown"

    # 尝试获取模型详情
    if model_id:
        try:
            info = client.models.retrieve(model_id)
            model_info = {"id": info.id, "owned_by": getattr(info, "owned_by", ""), "created": getattr(info, "created", None)}
            source = "retrieve"
        except Exception:
            pass

    # retrieve 失败则尝试从列表中匹配
    if model_info is None:
        try:
            all_models = client.models.list()
            for m in all_models.data:
                if model_id and m.id == model_id:
                    model_info = {"id": m.id, "owned_by": getattr(m, "owned_by", ""), "created": getattr(m, "created", None)}
                    source = "list"
                    break
            if model_info is None and all_models.data:
                source = "list"
                if not model_id:
                    model_id = all_models.data[0].id
                    model_info = {"id": model_id, "owned_by": "", "created": None}
        except Exception:
            pass

    # 验证可用性
    available = False
    test_error = ""
    if model_id:
        try:
            client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=5,
            )
            available = True
        except Exception as e:
            test_error = str(e)

    return {
        "ok": True,
        "model": model_id or "(自动检测)",
        "api_base": base_url,
        "model_info": model_info,
        "available": available,
        "test_error": test_error if not available else "",
        "source": source,
    }


@router.get("/settings/queue-status")
async def get_queue_status(request: Request):
    """查询 LLM 任务队列状态（活跃任务列表 + 去重信息）"""
    _require_auth(request)
    from services.task_queue import get_queue_info
    info = get_queue_info()
    return {"ok": True, **info}


@router.post("/settings/queue-clear")
async def clear_queue_handler(request: Request):
    """清空任务队列中所有等待中的任务（不影响正在执行的）"""
    _require_auth(request)
    from services.task_queue import clear_queue
    count = clear_queue()
    logger.info(f"教师手动清空队列，移除 {count} 个等待任务")
    return {"ok": True, "cleared": count}


@router.post("/settings/restart")
async def restart_service(request: Request):
    """重启后端服务（利用 uvicorn --reload 自动重启）"""
    _require_auth(request)
    import sys
    logger.info("收到重启指令，服务即将重启…")
    sys.exit(3)  # uvicorn reloader 检测到 exit code 3 会重启 worker


# ── 文件服务 ─────────────────────────────────────────────

@router.get("/files/{qid}/{filename}")
async def serve_question_file(qid: str, filename: str):
    """直接下载题目目录下的原始文件"""
    qdir = get_question_dir(qid)
    filepath = qdir / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404)
    from fastapi.responses import FileResponse
    return FileResponse(str(filepath))


@router.get("/preview/{qid}/{filename}")
async def serve_question_preview(qid: str, filename: str):
    """将题目 PDF/图片转为 JPEG 预览图（用于前端缩略展示）"""
    from services.llm_service import image_to_base64
    import base64
    from io import BytesIO
    from fastapi.responses import Response

    qdir = get_question_dir(qid)
    filepath = qdir / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404)

    b64 = image_to_base64(filepath)
    img_bytes = base64.b64decode(b64)
    return Response(content=img_bytes, media_type="image/jpeg")


# ── 学生名单管理（全局 StudentInfo 目录）─────────────────

@router.get("/roster/classes")
async def list_roster_classes(request: Request):
    """获取所有班级列表 [{class_name, count}]"""
    _require_auth(request)
    from services.question_service import list_classes
    classes = list_classes()
    return {"classes": classes}


@router.get("/roster/classes/{class_name}")
async def get_roster_class(request: Request, class_name: str):
    """查看某班学生详情 [{姓名, 学号}]"""
    _require_auth(request)
    from services.question_service import get_class_students
    students = get_class_students(class_name)
    return {"class_name": class_name, "students": students}


@router.post("/roster/classes")
async def create_roster_class(
    request: Request,
    class_name: str = Form(...),
    file: UploadFile = File(...),
):
    """上传 CSV 创建/覆盖班级名单，返回导入人数"""
    _require_auth(request)
    from services.question_service import create_class_roster
    csv_bytes = await file.read()
    count = create_class_roster(class_name, csv_bytes)
    return {"ok": True, "class_name": class_name, "count": count}


@router.delete("/roster/classes/{class_name}")
async def remove_roster_class(request: Request, class_name: str):
    """删除班级 CSV 文件"""
    _require_auth(request)
    from services.question_service import delete_class_roster
    ok = delete_class_roster(class_name)
    return {"ok": ok}


@router.get("/roster/template")
async def download_roster_template():
    """下载学生名单 CSV 模板（仅表头：姓名,学号）"""
    from fastapi.responses import FileResponse
    from services.question_service import ensure_template
    tmpl_path = ensure_template()
    return FileResponse(
        tmpl_path,
        media_type="text/csv",
        filename="学生名单模版.csv",
    )


@router.get("/roster/lookup")
async def lookup_student(request: Request, name: str = "", student_id: str = ""):
    """根据姓名+学号查询学生班级"""
    _require_auth(request)
    from services.question_service import find_student_class, check_roster
    ok, _ = check_roster(name.strip(), student_id.strip())
    if not ok:
        return {"found": False, "class": ""}
    class_name = find_student_class(name.strip(), student_id.strip())
    return {"found": True, "class": class_name}
