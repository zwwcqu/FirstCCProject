"""
学生端 API 路由（前缀 /api/student）。

功能：
- 题目列表与详情查询（公开，无需登录）
- 作业提交与 LLM 批阅（两阶段评分）
- 成绩查询（按学号查历史成绩）
- 文件服务（学生提交文件下载 + 预览图生成）

提交模式：
- test：测试模式，不校验名单、不保存成绩
- submit：正式提交，需通过名单校验，成绩写入 CSV，覆盖旧记录
"""

import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse

from services.question_service import (
    list_questions,
    get_question,
    get_question_files,
    save_student_submission,
    get_student_submission_path,
    get_question_dir,
)
from services.llm_service import grade_submission
from services.grade_service import save_grade, save_result_json, get_student_grade

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/student", tags=["student"])

# ── 频率限制 ─────────────────────────────────────────────
_RATE_LIMIT: dict[str, list[float]] = {}       # IP → 最近请求时间戳列表
_RATE_WINDOW = 60                               # 窗口大小（秒）
_RATE_MAX = 10                                  # 窗口内最大请求数


def _check_rate_limit(request: Request) -> None:
    """按 IP 做滑动窗口限流，超限返回 429"""
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
    """获取所有题目列表（公开）"""
    return list_questions()


@router.get("/questions/{qid}")
async def get_question_detail(qid: str):
    """获取题目详情（含描述、附图、参考工程图信息）"""
    q = get_question(qid)
    if q is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    files = get_question_files(qid)
    q["files"] = files
    return q


# ── 作业提交与批阅 ──────────────────────────────────────

@router.post("/submit/{qid}")
async def submit(
    qid: str,
    request: Request,
    name: str = Form(...),
    student_id: str = Form(...),
    file: UploadFile = File(...),
    mode: str = Form("submit"),     # "test" | "submit"
):
    """学生提交作业并触发 LLM 批阅"""
    _check_rate_limit(request)

    q = get_question(qid)
    if q is None:
        raise HTTPException(status_code=404, detail="题目不存在")

    is_test = mode == "test"

    # 正式模式校验名单
    if not is_test:
        from services.question_service import check_roster
        ok, msg = check_roster(name, student_id)
        if not ok:
            raise HTTPException(status_code=403, detail=msg)

    # 正式模式检查是否覆盖已有成绩
    overwrite = False
    if not is_test:
        existing = get_student_grade(qid, student_id)
        if existing:
            overwrite = True

    # 保存学生上传的文件
    file_bytes = await file.read()
    student_path = save_student_submission(qid, student_id, name, file_bytes, file.filename or "submission.pdf")

    # 引用文件仅含"参考工程图.pdf"（用于 LLM 相似度对比）
    qdir = get_question_dir(qid)
    files = get_question_files(qid)
    ref_paths = []
    if files["reference_pdf"]:
        ref_paths.append(qdir / files["reference_pdf"])

    # 调用 LLM 批阅
    try:
        result = grade_submission(
            description=files["description"],
            phase1_criteria=files["phase1_criteria"],
            phase2_criteria=files["phase2_criteria"],
            reference_paths=ref_paths,
            student_submission_path=Path(student_path),
        )
    except Exception as e:
        logger.error(f"LLM 批阅失败: {e}")
        raise HTTPException(status_code=500, detail=f"批阅失败: {str(e)}")

    grade = result.get("grade", "N/A")
    logger.info(f"批阅完成: [{qid}] {name}({student_id}) mode={mode} → {grade}")

    # 正式模式写入成绩
    if not is_test:
        from services.question_service import find_student_class
        class_name = find_student_class(name, student_id)
        save_grade(qid, student_id, name, grade, result, class_name)
        save_result_json(qid, student_id, name, result)

    student_filename = Path(student_path).name
    ref_filenames = [files["reference_pdf"]] if files["reference_pdf"] else []

    return {
        "ok": True,
        "grade": grade,
        "result": result,
        "student_filename": student_filename,
        "ref_filenames": ref_filenames,
        "mode": mode,
        "overwrite": overwrite,
    }


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
    """将学生提交文件转为 JPEG 预览图（用于前端缩略展示）"""
    from services.question_service import get_student_dir
    from services.llm_service import image_to_base64
    import base64
    from io import BytesIO
    from fastapi.responses import Response

    sdir = get_student_dir(qid)
    filepath = sdir / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404)

    b64 = image_to_base64(filepath)
    img_bytes = base64.b64decode(b64)
    return Response(content=img_bytes, media_type="image/jpeg")
