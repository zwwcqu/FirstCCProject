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
import os
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile, File, Form

from auth import verify_password, create_session, validate_session, destroy_session, change_password, MIN_PASSWORD_LENGTH, TEACHER_COOKIE
from config import CONFIG_DIR, PDF_MAGIC, get_question_dir, read_settings, write_settings, read_questions_index
from services.question_service import (
    list_questions,
    get_question,
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
    clear_student_data,
    _sanitize_filename_part,
)
from services.grade_service import read_all_grades, get_grades_csv_path, FIELDNAMES, save_grade, get_student_grade, remove_grade

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/teacher", tags=["teacher"])


def _require_auth(request: Request) -> None:
    """从 Cookie 中取 session token，校验登录状态"""
    token = request.cookies.get(TEACHER_COOKIE)
    if not token or not validate_session(token):
        raise HTTPException(status_code=401, detail="请先登录")


def _get_teacher_username(request: Request) -> str:
    """从 session 中提取当前教师的用户名"""
    token = request.cookies.get(TEACHER_COOKIE) or ""
    if token:
        sf = __import__("config").DATA_DIR / ".sessions" / f"{token}.json"
        if sf.exists():
            data = json.loads(sf.read_text(encoding="utf-8"))
            return data.get("teacher_username", "")
    return ""


def _check_question_ownership(request: Request, qid: str) -> None:
    """检查当前教师是否拥有该题目，不拥有则抛出 403"""
    teacher = _get_teacher_username(request)
    if not teacher:
        return  # 旧版 session 无用户名，放行
    questions = read_questions_index()
    for q in questions:
        if q["id"] == qid:
            owner = q.get("teacher", "")
            if owner and owner != teacher:
                raise HTTPException(status_code=403, detail=f"题目 [{qid}] 由 {owner} 创建，您无权修改")


def _run_reference_analysis(qid: str) -> None:
    """
    教师参考图分析，通过任务队列以最高优先级执行。
    根据题目的 submission_type 选择处理方式：
    - dxf: ezdxf 提取结构化数据 + 渲染预览图（无需 LLM）
    - pdf/image: LLM 视觉识读
    """
    from config import get_question_template

    q = get_question(qid)
    sub_type = q.get("submission_type", "pdf") if q else "pdf"

    if sub_type == "dxf":
        _run_reference_dxf_analysis(qid)
        return

    # PDF/图片流程：使用 LLM
    from services.llm_service import analyze_merged
    from services.task_queue import enqueue, PRIORITY_TEACHER

    # 立即删除旧分析文件，防止查询接口返回旧数据（新分析完成前返回"未就绪"）
    qdir = get_question_dir(qid)
    old_struct = qdir / "参考图_结构分析.json"
    old_quant = qdir / "参考图_量化分析.json"
    for f in (old_struct, old_quant):
        try:
            f.unlink(missing_ok=True)
        except Exception:
            pass

    def _task():
        qdir2 = get_question_dir(qid)
        ref_pdf = qdir2 / "参考工程图.pdf"
        if not ref_pdf.exists():
            logger.warning(f"[{qid}] 参考工程图不存在，跳过分析")
            return

        kn = get_question_files(qid).get("knowledge", "")
        template_text = get_question_template(qid)

        logger.info(f"[{qid}] 开始参考图工程图识读…")
        analysis = analyze_merged(ref_pdf, template_text, knowledge=kn)
        # 校验：工程图概述不能为空
        overview = analysis.get("工程图概述", "")
        if not overview or len(str(overview)) < 10:
            logger.error(f"[{qid}] 参考图识读结果为空（工程图概述过短），请检查模型是否支持图像识别")
            return

        save_reference_analysis(qid, analysis)
        logger.info(f"[{qid}] 参考图分析完成并已保存")

    enqueue(PRIORITY_TEACHER, _task,
            task_key=f"ref_analyze:{qid}",
            task_info={"type": "ref_analyze", "qid": qid})


def _run_reference_dxf_analysis(qid: str) -> None:
    """DXF 参考图分析：ezdxf 提取 + 渲染预览（同步完成，无需 LLM）"""
    from services.dxf_service import extract_dxf, render_dxf_preview

    qdir = get_question_dir(qid)
    ref_dxf = qdir / "参考工程图.dxf"
    if not ref_dxf.exists():
        logger.warning(f"[{qid}] 参考 DXF 不存在，跳过分析")
        return

    # 清理旧分析文件
    for old_name in ("参考图_分析.json", "参考图_结构分析.json", "参考图_量化分析.json"):
        old_path = qdir / old_name
        try:
            old_path.unlink(missing_ok=True)
        except Exception:
            pass

    try:
        # 提取 DXF 结构化数据
        logger.info(f"[{qid}] 开始 DXF 数据提取…")
        dxf_data = extract_dxf(ref_dxf)
        save_reference_analysis(qid, dxf_data)
        logger.info(f"[{qid}] DXF 参考图分析完成")

        # 渲染预览图
        preview_path = qdir / "参考工程图.png"
        render_dxf_preview(ref_dxf, preview_path)
        logger.info(f"[{qid}] DXF 预览图已生成")
    except Exception as e:
        logger.error(f"[{qid}] DXF 参考图分析失败: {e}")


# ── 登录 / 登出 ──────────────────────────────────────────

@router.post("/login")
async def login(response: Response, username: str = Form(...), password: str = Form(...)):
    """教师登录（用户名+密码）"""
    if not username.strip():
        raise HTTPException(status_code=400, detail="请输入用户名")
    from auth import verify_teacher_password
    ok, info = verify_teacher_password(username.strip(), password)
    if not ok:
        raise HTTPException(status_code=403, detail="用户名或密码错误")
    token = create_session()
    # 将教师信息存入 session 文件
    _SESSIONS_DIR = __import__("config").DATA_DIR / ".sessions"
    sf = _SESSIONS_DIR / f"{token}.json"
    data = json.loads(sf.read_text(encoding="utf-8"))
    data["teacher_name"] = info["姓名"]
    data["teacher_username"] = username.strip()
    sf.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    response.set_cookie(key=TEACHER_COOKIE, value=token, max_age=14400, httponly=True, samesite="lax", path="/")
    logger.info(f"教师登录: {info['姓名']} ({username.strip()})")
    return {"ok": True, "name": info["姓名"], "username": username.strip(), "password_changed": info.get("password_changed", False)}


@router.post("/logout")
async def logout(request: Request, response: Response):
    """教师登出，销毁 session + 清除 Cookie"""
    token = request.cookies.get(TEACHER_COOKIE)
    if token:
        destroy_session(token)
    response.delete_cookie(key=TEACHER_COOKIE, path="/")
    logger.info("教师已登出")
    return {"ok": True}


@router.get("/check")
async def check_login(request: Request):
    """检查登录状态，用于前端页面刷新时验证"""
    token = request.cookies.get(TEACHER_COOKIE)
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
    submission_type: str = Form("pdf"),                  # 学生提交文件类型：pdf / image
    classes: str = Form(""),                             # 适用班别（逗号分隔）
    deadline: str = Form(""),                            # 提交截止时间 ISO 格式
    template_type: str = Form("零件图识读模板.txt"),      # 识读模板类型
    template_content: str = Form(""),                    # 自定义模板内容（非空则覆盖）
):
    """新增题目"""
    _require_auth(request)
    teacher = _get_teacher_username(request)
    try:
        entry = create_question(qid, title, description, phase1_criteria, phase2_criteria,
                                knowledge, submission_type, teacher=teacher, classes=classes,
                                deadline=deadline, template_type=template_type,
                                template_content=template_content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if image and image.filename:
        img_bytes = await image.read()
        save_question_image(qid, img_bytes, image.filename)
    if reference_pdf and reference_pdf.filename:
        ref_bytes = await reference_pdf.read()
        ref_ext = Path(reference_pdf.filename).suffix.lower()
        if ref_ext == ".dxf":
            from services.question_service import save_reference_dxf
            save_reference_dxf(qid, ref_bytes, reference_pdf.filename)
        else:
            save_reference_pdf(qid, ref_bytes, reference_pdf.filename)
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
    submission_type: str = Form("pdf"),                  # 学生提交文件类型：pdf / image
    classes: str = Form(""),                             # 适用班别（逗号分隔）
    deadline: str = Form(""),                            # 提交截止时间 ISO 格式
    template_content: str = Form(""),                    # 自定义模板内容（非空则保存）
):
    """编辑已有题目"""
    _require_auth(request)
    teacher = _get_teacher_username(request)
    try:
        entry = update_question(qid, title, description, phase1_criteria, phase2_criteria,
                                knowledge, submission_type, teacher=teacher, classes=classes,
                                deadline=deadline)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    if entry is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    # 如果提交了模板内容，同步保存
    if template_content.strip():
        from config import save_question_template
        save_question_template(qid, template_content)
    if image and image.filename:
        img_bytes = await image.read()
        save_question_image(qid, img_bytes, image.filename)
    if reference_pdf and reference_pdf.filename:
        ref_bytes = await reference_pdf.read()
        ref_ext = Path(reference_pdf.filename).suffix.lower()
        if ref_ext == ".dxf":
            from services.question_service import save_reference_dxf
            save_reference_dxf(qid, ref_bytes, reference_pdf.filename)
        else:
            save_reference_pdf(qid, ref_bytes, reference_pdf.filename)
        _run_reference_analysis(qid)   # 参考图更新后重新分析
    return entry


@router.delete("/questions/{qid}")
async def delete_question_handler(request: Request, qid: str):
    """删除题目（数据移到 backup/ 目录）。仅创建者可删除"""
    _require_auth(request)
    teacher = _get_teacher_username(request)
    # 检查所有权
    questions = read_questions_index()
    for q in questions:
        if q["id"] == qid and teacher and q.get("teacher", "") and q["teacher"] != teacher:
            raise HTTPException(status_code=403, detail=f"题目 [{qid}] 由 {q['teacher']} 创建，您无权删除")
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
    ref_dxf = qdir / "参考工程图.dxf"
    if not ref_pdf.exists() and not ref_dxf.exists():
        raise HTTPException(status_code=400, detail="请先上传参考工程图（PDF 或 DXF）")
    # 诊断日志：记录当前激活模型
    from services.llm_service import _get_active_config
    active = _get_active_config()
    logger.info(f"[诊断] 触发重分析 qid={qid}，当前激活模型={active.get('name')}({active.get('model')}) llm_active={read_settings().get('llm_active')}")
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

def _get_derived_status(qid: str, sid: str, name: str, rec: dict | None) -> str:
    """根据文件存在状态推导学生提交状态。文件存在优先于 submissions.json"""
    student_dir = get_student_dir(qid)
    safe_name = _sanitize_filename_part(name or (rec.get("name", "") if rec else sid))
    safe_id = _sanitize_filename_part(sid)
    if (student_dir / f"{safe_name}_{safe_id}_评分.json").exists():
        return "graded"
    if (student_dir / f"{safe_name}_{safe_id}_分析.json").exists():
        return "analyzed"
    if rec and rec.get("status") == "rejected":
        return "rejected"
    if rec and rec.get("filename"):
        return "uploaded"
    return ""


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
                "班级": rec.get("class_name", ""),
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
                "_status": _get_derived_status(qid, sid, rec.get("name", "") if rec else "", rec),
                "_filename": student_path.name if student_path else "",
                "_error": rec.get("error", ""),
            })

    # 给已评分行补上 _status、_filename、_error，班级空时从记录补
    for row in graded_rows:
        sid = row.get("学号", "")
        name = row.get("姓名", "")
        rec = get_submission_record(qid, sid)
        row["_status"] = _get_derived_status(qid, sid, name, rec)
        row["_error"] = rec.get("error", "") if rec else ""
        if not row.get("班级", "") and rec:
            row["班级"] = rec.get("class_name", "")
        # 有 record 用 record 中的名字查文件，否则用 CSV 中的名字查
        lookup_name = rec.get("name", "") if rec else name
        student_path = get_student_submission_path(qid, sid, lookup_name)
        row["_filename"] = student_path.name if student_path else ""

        # 读取评分结果 JSON，附加模型和 token 用量
        import json as _json
        student_dir = get_student_dir(qid)
        safe_name_json = _sanitize_filename_part(name or (rec.get("name", "") if rec else ""))
        safe_id_json = _sanitize_filename_part(sid)
        result_path = student_dir / f"{safe_name_json}_{safe_id_json}.json"
        if result_path.exists():
            try:
                rj = _json.loads(result_path.read_text(encoding="utf-8"))
                row["_model"] = rj.get("_model", "")
                row["_usage"] = rj.get("_usage", {})
                row["_phase1_usage"] = rj.get("_phase1_usage", {})
                row["_phase2_usage"] = rj.get("_phase2_usage", {})
            except Exception:
                pass  # JSON 损坏或格式异常，跳过

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

    from services.llm_service import analyze_and_grade, grade_combined, grade_dxf
    from services.task_queue import enqueue, PRIORITY_BATCH
    from config import get_question_template

    # 先同步磁盘文件到 submissions.json（补充提交的学生可能不在记录中）
    from services.question_service import sync_submissions_from_disk
    sync_submissions_from_disk(qid)

    ref_analysis = get_reference_analysis(qid)
    if ref_analysis is None:
        raise HTTPException(status_code=400, detail="参考图尚未完成分析，请先分析参考图")

    files = get_question_files(qid)
    phase1_criteria = files.get("phase1_criteria", "")
    phase2_criteria = files.get("phase2_criteria", "")
    knowledge = files.get("knowledge", "")
    qdir = get_question_dir(qid)
    ref_pdf = qdir / "参考工程图.pdf"
    ref_dxf = qdir / "参考工程图.dxf"
    ref_png = qdir / "参考工程图.png"
    template_text = get_question_template(qid)

    # 判断是否为 DXF 题目
    q_info = get_question(qid)
    is_dxf = q_info.get("submission_type") == "dxf" if q_info else False

    def _grade_one(sid: str):
        from services.question_service import find_student_class, update_submission_record, save_student_analysis, reject_if_fake

        rec = get_submission_record(qid, sid)
        if not rec:
            return
        name = rec.get("name", "")
        student_path = get_student_submission_path(qid, sid, name)
        if student_path is None:
            return

        # 虚假作业判别
        if reject_if_fake(qid, sid, name, student_path, student_path.stem):
            return

        update_submission_record(qid, sid, name, student_path.stem, "analyzing" if not is_dxf else "grading")
        try:
            if is_dxf:
                # DXF 流程：统一入口
                from services.dxf_service import run_dxf_grade
                stu_dxf_data, result = run_dxf_grade(
                    student_dxf_path=student_path,
                    ref_data=ref_analysis,
                    ref_dir=qdir,
                    phase1_criteria=phase1_criteria,
                    phase2_criteria=phase2_criteria,
                    knowledge=knowledge,
                )
                save_student_analysis(qid, sid, name, stu_dxf_data)
            else:
                # PDF/图片流程（现有逻辑）
                stu_analysis = get_student_analysis(qid, sid, name)

                if stu_analysis is not None:
                    result = grade_combined(
                        stu_analysis=stu_analysis,
                        ref_analysis=ref_analysis,
                        phase1_criteria=phase1_criteria,
                        phase2_criteria=phase2_criteria,
                        ref_image_path=ref_pdf,
                        stu_image_path=student_path,
                        knowledge=knowledge,
                    )
                else:
                    result = analyze_and_grade(
                        image_path=student_path,
                        template_text=template_text,
                        ref_analysis=ref_analysis,
                        phase1_criteria=phase1_criteria,
                        phase2_criteria=phase2_criteria,
                        ref_image_path=ref_pdf,
                        knowledge=knowledge,
                    )
                # 识读数据（analyze_and_grade 路径才有新分析）
                if "工程图概述" in result and not stu_analysis:
                    save_student_analysis(qid, sid, name, result)

            # 评分数据
            from services.question_service import save_student_grade
            save_student_grade(qid, sid, name, result)
            grade = result.get("grade", "N/A")
            class_name = find_student_class(name, sid)
            import hashlib
            file_sha256 = hashlib.sha256(student_path.read_bytes()).hexdigest()
            save_grade(qid, sid, name, grade, result, class_name, file_sha256=file_sha256)
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
        enqueue(PRIORITY_BATCH, (lambda s: lambda: _grade_one(s))(sid),
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


@router.post("/grades/{qid}/reject/{student_id}")
async def reject_submission(request: Request, qid: str, student_id: str):
    """打回学生提交，允许重新提交"""
    _require_auth(request)
    rec = get_submission_record(qid, student_id)
    if not rec:
        raise HTTPException(status_code=404, detail="未找到提交记录")
    name = rec.get("name", student_id)
    stem = rec.get("filename", "")
    # 删除学生所有提交文件和分析数据
    clear_student_data(qid, student_id, name)
    update_submission_record(qid, student_id, name, stem, "rejected")
    logger.info(f"[{qid}] 已打回并清空数据: {name}({student_id})")
    return {"ok": True}


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

    # 校验文件类型（根据题目 submission_type）
    ext = Path(fname).suffix.lower()
    q_info = get_question(qid)
    sub_type = q_info.get("submission_type", "pdf") if q_info else "pdf"

    if sub_type == "dxf":
        if ext != ".dxf":
            raise HTTPException(status_code=400, detail="本题要求提交 DXF 文件")
    else:
        if not file_bytes.startswith(PDF_MAGIC) and ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
            raise HTTPException(status_code=400, detail="仅支持 PDF 或图片格式文件")

    from services.question_service import submit_student_work
    saved_name = submit_student_work(qid, student_id, name, file_bytes, fname)
    logger.info(f"[{qid}] 教师补充提交: {name}({student_id}) → {saved_name}")
    return {"ok": True, "student_filename": saved_name}


def _check_plagiarism(qid: str) -> None:
    """扫描成绩 CSV，SHA-256 相同者：最早提交为原创，其余标记作弊"""
    graded = read_all_grades(qid)
    if not graded:
        return
    sha_map: dict[str, list[dict]] = {}
    for row in graded:
        sha = (row.get("文件SHA256") or "").strip()
        if sha:
            sha_map.setdefault(sha, []).append(row)
    for sha, rows in sha_map.items():
        if len(rows) <= 1:
            continue
        rows.sort(key=lambda r: r.get("提交时间", ""))
        for i, row in enumerate(rows):
            if i == 0:
                continue
            if row.get("作弊") == "是":
                continue  # 已标记，跳过
            row["作弊"] = "是"
            row["总分"] = "0"
            row["成绩"] = "F"
            row["教师评语"] = "作弊，怀疑复制他人文件。"
            from services.grade_service import save_grade as _sg
            _sg(qid, row.get("学号", ""), row.get("姓名", ""),
                "F", {"总评": "", "教师评语": "作弊，怀疑复制他人文件。"},
                row.get("班级", ""), file_sha256=sha)


@router.post("/grades/{qid}/refresh")
async def refresh_grades(request: Request, qid: str):
    """扫描磁盘文件，自动发现名单中学生的提交，同步返回最新成绩列表"""
    _require_auth(request)

    added = sync_submissions_from_disk(qid)

    # 刷新前先做 SHA-256 作弊检测
    _check_plagiarism(qid)

    # 复用成绩查询逻辑
    graded_rows = read_all_grades(qid)
    graded_ids = {r.get("学号", "") for r in graded_rows}

    submissions = get_submissions(qid)
    ungraded_rows: list[dict] = []
    for sid, rec in submissions.items():
        if sid not in graded_ids:
            student_path = get_student_submission_path(qid, sid, rec.get("name", ""))
            ungraded_rows.append({
                "班级": rec.get("class_name", ""),
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
                "_status": _get_derived_status(qid, sid, rec.get("name", "") if rec else "", rec),
                "_filename": student_path.name if student_path else "",
                "_error": rec.get("error", ""),
            })

    for row in graded_rows:
        sid = row.get("学号", "")
        name = row.get("姓名", "")
        rec = get_submission_record(qid, sid)
        row["_status"] = _get_derived_status(qid, sid, name, rec)
        row["_error"] = rec.get("error", "") if rec else ""
        if not row.get("班级", "") and rec:
            row["班级"] = rec.get("class_name", "")
        lookup_name = rec.get("name", "") if rec else name
        student_path = get_student_submission_path(qid, sid, lookup_name)
        row["_filename"] = student_path.name if student_path else ""

        # 读取评分结果 JSON，附加模型和 token 用量
        import json as _json
        student_dir = get_student_dir(qid)
        safe_name_json = _sanitize_filename_part(name or (rec.get("name", "") if rec else ""))
        safe_id_json = _sanitize_filename_part(sid)
        result_path = student_dir / f"{safe_name_json}_{safe_id_json}.json"
        if result_path.exists():
            try:
                rj = _json.loads(result_path.read_text(encoding="utf-8"))
                row["_model"] = rj.get("_model", "")
                row["_usage"] = rj.get("_usage", {})
                row["_phase1_usage"] = rj.get("_phase1_usage", {})
                row["_phase2_usage"] = rj.get("_phase2_usage", {})
            except Exception:
                pass  # JSON 损坏或格式异常，跳过

    all_rows = graded_rows + ungraded_rows
    return {"qid": qid, "grades": all_rows, "columns": FIELDNAMES, "added": added}


@router.put("/grades/{qid}/{student_id}")
async def edit_grade(request: Request, qid: str, student_id: str):
    """修改单个学生的成绩字段。仅题目创建者可修改"""
    _require_auth(request)
    _check_question_ownership(request, qid)
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
    from services.question_service import get_student_analysis, get_submission_record, get_student_dir, _sanitize_filename_part

    # 尝试从提交记录中获取姓名
    if not name:
        rec = get_submission_record(qid, student_id)
        if rec:
            name = rec.get("name", "")

    # 回退：扫描 student 目录，从分析文件名中提取姓名
    if not name:
        sdir = get_student_dir(qid)
        safe_id = _sanitize_filename_part(student_id)
        if sdir.exists():
            for f in sdir.iterdir():
                if f.suffix.lower() == ".json" and f"_{safe_id}" in f.stem:
                    # 文件名格式：{name}_{id}.json 或 {name}_{id}_结构分析.json 等
                    idx = f.stem.find(f"_{safe_id}")
                    if idx > 0:
                        name = f.stem[:idx]
                        break

    if not name:
        raise HTTPException(status_code=400, detail="无法确定学生姓名")

    analysis = get_student_analysis(qid, student_id, name)
    if analysis is None:
        return {"ok": True, "ready": False, "analysis": None}
    return {"ok": True, "ready": True, "analysis": analysis}


@router.get("/student-preview/{qid}/{student_id}")
async def teacher_student_preview(request: Request, qid: str, student_id: str):
    """教师查看学生提交的工程图预览（优先 PNG，DXF 渲染，回退实时转换）"""
    _require_auth(request)
    from fastapi.responses import FileResponse, Response
    from services.question_service import get_student_dir, _sanitize_filename_part
    import base64

    sdir = get_student_dir(qid)
    safe_id = _sanitize_filename_part(student_id)

    # 先尝试从提交记录获取姓名，构造精确文件名
    rec = get_submission_record(qid, student_id)
    if rec:
        name = rec.get("name", "")
        safe_name = _sanitize_filename_part(name)
        png_path = sdir / f"{safe_name}_{safe_id}.png"
        if png_path.exists():
            return FileResponse(str(png_path), media_type="image/png")
        student_path = get_student_submission_path(qid, student_id, name)
        if student_path is not None:
            if student_path.suffix.lower() == ".dxf":
                # DXF 实时渲染 PNG
                from services.dxf_service import render_dxf_preview
                png_path = student_path.with_suffix(".png")
                if not png_path.exists():
                    render_dxf_preview(student_path, png_path)
                return FileResponse(str(png_path), media_type="image/png")
            from services.llm_service import image_to_base64
            b64 = image_to_base64(student_path)
            img_bytes = base64.b64decode(b64)
            return Response(content=img_bytes, media_type="image/jpeg")

    # 回退：没有提交记录，直接在 student 目录搜索匹配学号的文件
    if sdir.exists():
        # 优先 PNG
        for f in sorted(sdir.iterdir()):
            if f.suffix.lower() == ".png" and f.stem.endswith(f"_{safe_id}"):
                return FileResponse(str(f), media_type="image/png")
        # 再试 DXF
        for f in sorted(sdir.iterdir()):
            if f.suffix.lower() == ".dxf" and f.stem.endswith(f"_{safe_id}"):
                from services.dxf_service import render_dxf_preview
                png_path = f.with_suffix(".png")
                if not png_path.exists():
                    render_dxf_preview(f, png_path)
                return FileResponse(str(png_path), media_type="image/png")
        # 再试 PDF
        for f in sorted(sdir.iterdir()):
            if f.suffix.lower() == ".pdf" and f.stem.endswith(f"_{safe_id}"):
                from services.llm_service import image_to_base64
                b64 = image_to_base64(f)
                img_bytes = base64.b64decode(b64)
                return Response(content=img_bytes, media_type="image/jpeg")

    if rec:
        raise HTTPException(status_code=404, detail="提交文件不存在")
    raise HTTPException(status_code=404, detail="该学生未提交作业")


# ── 教师个人信息 ───────────────────────────────────────────

@router.get("/profile")
async def get_profile(request: Request):
    """获取当前登录教师的信息"""
    _require_auth(request)
    token = request.cookies.get(TEACHER_COOKIE) or ""
    if token:
        sf = __import__("config").DATA_DIR / ".sessions" / f"{token}.json"
        if sf.exists():
            data = json.loads(sf.read_text(encoding="utf-8"))
            username = data.get("teacher_username", "")
            name = data.get("teacher_name", "")
            if username:
                from auth import _read_teacher_auth
                auth = _read_teacher_auth()
                record = auth.get(username, {})
                return {
                    "姓名": record.get("姓名", name),
                    "用户名": username,
                    "工号": record.get("工号", ""),
                    "password_changed": record.get("password_changed", True),
                }
    return {"姓名": "", "用户名": "", "工号": "", "password_changed": True}


@router.put("/profile")
async def update_profile(request: Request):
    """修改教师姓名和用户名"""
    _require_auth(request)
    body = await request.json()
    new_name = (body.get("name") or "").strip()
    new_username = (body.get("username") or "").strip()
    if not new_name or not new_username:
        raise HTTPException(status_code=400, detail="姓名和用户名不能为空")
    token = request.cookies.get(TEACHER_COOKIE) or ""
    if token:
        sf = __import__("config").DATA_DIR / ".sessions" / f"{token}.json"
        if sf.exists():
            data = json.loads(sf.read_text(encoding="utf-8"))
            old_username = data.get("teacher_username", "")
            if old_username:
                from auth import update_teacher_profile
                ok, msg = update_teacher_profile(old_username, new_name, new_username)
                if not ok:
                    raise HTTPException(status_code=400, detail=msg)
                # 更新 session 中的信息
                data["teacher_name"] = new_name
                data["teacher_username"] = new_username
                sf.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return {"ok": True}
    raise HTTPException(status_code=400, detail="请使用多教师账号登录")


@router.post("/profile/change-password")
async def teacher_change_password(request: Request):
    """教师修改登录密码（需验证旧密码）"""
    _require_auth(request)
    body = await request.json()
    old_password = (body.get("old_password") or "").strip()
    new_password = (body.get("new_password") or "").strip()
    if not old_password or not new_password:
        raise HTTPException(status_code=400, detail="请填写旧密码和新密码")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"密码至少{MIN_PASSWORD_LENGTH}位")
    if old_password == new_password:
        raise HTTPException(status_code=400, detail="新密码不能与旧密码相同")
    token = request.cookies.get(TEACHER_COOKIE) or ""
    if token:
        sf = __import__("config").DATA_DIR / ".sessions" / f"{token}.json"
        if sf.exists():
            data = json.loads(sf.read_text(encoding="utf-8"))
            username = data.get("teacher_username", "")
            if username:
                from auth import change_teacher_password
                ok, msg = change_teacher_password(username, old_password, new_password)
                if not ok:
                    raise HTTPException(status_code=400, detail=msg)
                return {"ok": True}
    raise HTTPException(status_code=400, detail="请使用多教师账号登录")


# ── 系统设置 ─────────────────────────────────────────────

@router.get("/settings")
async def get_settings(request: Request):
    """获取完整的教师可配置设置，缺失字段用默认值补齐"""
    _require_auth(request)
    from config import get_llm_params, get_image_params, get_grade_thresholds, get_prompt_templates, get_scoring_templates
    settings = read_settings()
    # 等级阈值转为前端需要的 {等级: 分数} 格式
    raw_thresholds = settings.get("grade_thresholds", {})
    if not raw_thresholds:
        thresholds_list = get_grade_thresholds()
        raw_thresholds = {name: score for score, name in thresholds_list}
    return {
        "models": settings.get("models", []),
        "llm_active": settings.get("llm_active", 0),
        "llm_params": get_llm_params(),
        "image_params": get_image_params(),
        "grade_thresholds": raw_thresholds,
        "prompt_templates": get_prompt_templates(),
        "scoring_templates": get_scoring_templates(),
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

    # 新增可配置区块（仅更新 body 中包含的字段）
    for section in ("llm_params", "image_params", "grade_thresholds",
                    "prompt_templates", "scoring_templates"):
        if section in body and isinstance(body[section], dict):
            settings[section] = {**settings.get(section, {}), **body[section]}

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


@router.post("/settings/test-vision")
async def test_vision_capability(request: Request):
    """测试大模型读图能力。body: {api_base, api_key, model}。使用 config/DrawingForCheck.png 作为测试图"""
    _require_auth(request)
    body = await request.json()
    base_url = (body.get("api_base") or "").strip()
    api_key = (body.get("api_key") or "").strip()
    model = (body.get("model") or "").strip()

    if not base_url:
        return {"ok": False, "message": "请先填写 API 地址"}

    # 读取测试图
    test_image_path = Path(__file__).parent.parent.parent / "config" / "DrawingForCheck.png"
    if not test_image_path.exists():
        return {"ok": False, "message": f"测试图不存在: {test_image_path}"}

    try:
        from services.llm_service import image_to_base64
        b64 = image_to_base64(test_image_path)
    except Exception as e:
        return {"ok": False, "message": f"读取测试图失败: {str(e)}"}

    # 发请求给模型，用最简单的二选一问题验证读图能力
    try:
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=60)
        response = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "这是不是一个机械工程图，请回答是或者不是，不添加任何说明，只需回答是，不是两种选择。",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ],
            }],
            max_tokens=1024,
        )
        # 推理模型（如 DeepSeek-R1、mimo-v2.5）把思考放在 reasoning_content，
        # 最终回答在 content。优先取 content，为空时回退到 reasoning_content
        msg = response.choices[0].message
        reply = (msg.content or "").strip()
        if not reply:
            reasoning = getattr(msg, "reasoning_content", None) or ""
            reply = reasoning.strip()

        # 判断：回复包含"是"且不包含"不是"即为通过
        passed = "是" in reply and "不是" not in reply

        return {
            "ok": True,
            "passed": passed,
            "message": "读图通过" if passed else "读图未通过",
            "reply": reply[:200],
            "hint": None if passed else "模型未能识别图片为机械工程图，可能不支持图像识别（vision）能力。建议使用 qwen-vl 或 gpt-4o 等多模态模型。",
        }
    except Exception as e:
        return {"ok": False, "message": f"请求失败: {str(e)}", "passed": False}


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

    # 读图能力测试
    vision_ok = False
    vision_reply = ""
    vision_hint = ""
    if available and model_id:
        test_image_path = Path(__file__).parent.parent.parent / "config" / "DrawingForCheck.png"
        if test_image_path.exists():
            try:
                from services.llm_service import image_to_base64
                import base64 as _base64
                b64 = image_to_base64(test_image_path)
                vr = client.chat.completions.create(
                    model=model_id,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "这是不是一个机械工程图，请回答是或者不是，不添加任何说明，只需回答是，不是两种选择。"},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        ],
                    }],
                    max_tokens=1024,
                    timeout=60,
                )
                # 推理模型（如 DeepSeek-R1、mimo-v2.5）把思考放在 reasoning_content，
                # 最终回答在 content。优先取 content，为空时回退到 reasoning_content
                msg = vr.choices[0].message
                reply = (msg.content or "").strip()
                if not reply:
                    reasoning = getattr(msg, "reasoning_content", None) or ""
                    reply = reasoning.strip()
                vision_reply = reply[:200]
                vision_ok = "是" in reply and "不是" not in reply
                if not vision_ok:
                    vision_hint = "模型未能识别图片为机械工程图，可能不支持图像识别（vision）能力"
            except Exception as ve:
                vision_hint = f"读图测试失败: {str(ve)}"

    return {
        "ok": True,
        "model": model_id or "(自动检测)",
        "api_base": base_url,
        "model_info": model_info,
        "available": available,
        "test_error": test_error if not available else "",
        "source": source,
        "vision_ok": vision_ok,
        "vision_reply": vision_reply,
        "vision_hint": vision_hint,
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

def _check_path_safe(base: Path, target: Path) -> Path:
    """确保 target 在 base 目录内，防止路径穿越攻击"""
    resolved = target.resolve()
    if not str(resolved).startswith(str(base.resolve()) + os.sep):
        raise HTTPException(status_code=404, detail="文件未找到")
    return resolved


@router.get("/files/{qid}/{filename}")
async def serve_question_file(qid: str, filename: str, request: Request):
    """直接下载题目目录下的原始文件（参考工程图需登录）"""
    # 参考工程图不允许未登录访问（防止学生复制答案）
    if "参考" in filename or "reference" in filename.lower():
        _require_auth(request)
    qdir = get_question_dir(qid).resolve()
    filepath = _check_path_safe(qdir, Path(qdir / filename))
    if not filepath.is_file():
        raise HTTPException(status_code=404)
    from fastapi.responses import FileResponse
    return FileResponse(str(filepath))


@router.get("/preview/{qid}/{filename}")
async def serve_question_preview(qid: str, filename: str, request: Request):
    """将题目 PDF/图片/DXF 转为 JPEG/PNG 预览图（用于前端缩略展示，参考工程图需登录）"""
    # 参考工程图不允许未登录访问（防止学生复制答案）
    if "参考" in filename or "reference" in filename.lower():
        _require_auth(request)
    from services.llm_service import image_to_base64
    import base64
    from fastapi.responses import Response, FileResponse

    qdir = get_question_dir(qid).resolve()
    filepath = _check_path_safe(qdir, Path(qdir / filename))
    if not filepath.is_file():
        raise HTTPException(status_code=404)

    # DXF 优先返回预渲染的 PNG
    if filepath.suffix.lower() == ".dxf":
        png_path = filepath.with_suffix(".png")
        if png_path.exists():
            return FileResponse(str(png_path), media_type="image/png")
        # 实时渲染
        from services.dxf_service import render_dxf_preview
        render_dxf_preview(filepath, png_path)
        return FileResponse(str(png_path), media_type="image/png")

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


@router.post("/roster/reset-password")
async def reset_student_pwd(request: Request):
    """教师重置学生密码为默认值 cad123"""
    _require_auth(request)
    body = await request.json()
    class_name = (body.get("class_name") or "").strip()
    student_id = (body.get("student_id") or "").strip()
    if not class_name or not student_id:
        raise HTTPException(status_code=400, detail="请提供班级和学生学号")
    from auth import reset_student_password
    reset_student_password(class_name, student_id)
    return {"ok": True}


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


# ── 模板管理 API ─────────────────────────────────────────

@router.get("/templates")
async def get_templates(request: Request):
    """列出所有全局模板的名称和内容"""
    _require_auth(request)
    from config import list_templates
    return {"templates": list_templates()}


@router.put("/templates/{template_name}")
async def update_template(request: Request, template_name: str, body: dict):
    """保存单个全局模板"""
    _require_auth(request)
    from config import save_template
    content = body.get("content", "")
    try:
        save_template(template_name, content)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/questions/{qid}/template")
async def get_q_template(request: Request, qid: str):
    """获取题目的识读模板内容"""
    _require_auth(request)
    from config import get_question_template
    content = get_question_template(qid)
    return {"content": content}


@router.put("/questions/{qid}/template")
async def update_q_template(request: Request, qid: str, body: dict):
    """修改题目的识读模板"""
    _require_auth(request)
    from config import save_question_template
    content = body.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="模板内容不能为空")
    save_question_template(qid, content)
    return {"ok": True}


@router.post("/questions/{qid}/template")
async def select_q_template(request: Request, qid: str, body: dict):
    """选择/更换题目的模板类型（从 data/templates/ 复制到题目目录）"""
    _require_auth(request)
    from config import get_template, save_question_template, TEMPLATE_NAMES
    ttype = body.get("type", "")
    if ttype not in TEMPLATE_NAMES:
        raise HTTPException(status_code=400,
                           detail=f"未知模板类型: {ttype}，可选: {TEMPLATE_NAMES}")
    content = get_template(ttype)
    save_question_template(qid, content)
    return {"ok": True, "content": content}
