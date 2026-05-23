import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form

from auth import verify_password, create_session, validate_session, destroy_session, change_password
from config import get_question_dir, read_settings, write_settings
from services.question_service import (
    list_questions,
    create_question,
    update_question,
    delete_question,
    save_question_image,
    save_reference_pdf,
    get_question_files,
    get_scoring_templates,
)
from services.grade_service import read_all_grades, get_grades_csv_path

router = APIRouter(prefix="/api/teacher", tags=["teacher"])


def _require_auth(request: Request) -> None:
    token = request.cookies.get("session")
    if not token or not validate_session(token):
        raise HTTPException(status_code=401, detail="请先登录")


@router.post("/login")
async def login(password: str = Form(...)):
    if not verify_password(password):
        raise HTTPException(status_code=403, detail="密码错误")
    token = create_session()
    return {"ok": True, "token": token}


@router.post("/logout")
async def logout(request: Request):
    token = request.cookies.get("session")
    if token:
        destroy_session(token)
    return {"ok": True}


@router.get("/check")
async def check_login(request: Request):
    token = request.cookies.get("session")
    if token and validate_session(token):
        return {"ok": True}
    raise HTTPException(status_code=401)


@router.get("/questions")
async def get_questions(request: Request):
    _require_auth(request)
    questions = list_questions()
    # add file info
    result = []
    for q in questions:
        files = get_question_files(q["id"])
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
    image: Optional[UploadFile] = File(None),
    reference_pdf: Optional[UploadFile] = File(None),
):
    _require_auth(request)
    try:
        entry = create_question(qid, title, description, phase1_criteria, phase2_criteria)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if image and image.filename:
        img_bytes = await image.read()
        save_question_image(qid, img_bytes, image.filename)
    if reference_pdf and reference_pdf.filename:
        pdf_bytes = await reference_pdf.read()
        save_reference_pdf(qid, pdf_bytes, reference_pdf.filename)
    return entry


@router.put("/questions/{qid}")
async def update_question_handler(
    request: Request,
    qid: str,
    title: str = Form(...),
    description: str = Form(""),
    phase1_criteria: str = Form(""),
    phase2_criteria: str = Form(""),
    image: Optional[UploadFile] = File(None),
    reference_pdf: Optional[UploadFile] = File(None),
):
    _require_auth(request)
    entry = update_question(qid, title, description, phase1_criteria, phase2_criteria)
    if entry is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    if image and image.filename:
        img_bytes = await image.read()
        save_question_image(qid, img_bytes, image.filename)
    if reference_pdf and reference_pdf.filename:
        pdf_bytes = await reference_pdf.read()
        save_reference_pdf(qid, pdf_bytes, reference_pdf.filename)
    return entry


@router.delete("/questions/{qid}")
async def delete_question_handler(request: Request, qid: str):
    _require_auth(request)
    ok = delete_question(qid)
    if not ok:
        raise HTTPException(status_code=404, detail="题目不存在")
    return {"ok": True}


@router.get("/scoring-templates")
async def get_templates(request: Request):
    """获取评分模版内容，供新增/编辑题目时预填"""
    _require_auth(request)
    return get_scoring_templates()


@router.get("/grades/{qid}")
async def get_grades(request: Request, qid: str):
    _require_auth(request)
    rows = read_all_grades(qid)
    return {"qid": qid, "grades": rows}


@router.get("/settings")
async def get_settings(request: Request):
    _require_auth(request)
    settings = read_settings()
    return {"llm_api_base": settings.get("llm_api_base", ""),
            "llm_model": settings.get("llm_model", ""),
            "llm_api_key": settings.get("llm_api_key", "")}


@router.put("/settings")
async def update_settings(request: Request):
    _require_auth(request)
    body = await request.json()
    settings = read_settings()
    if "llm_api_base" in body:
        settings["llm_api_base"] = body["llm_api_base"]
    if "llm_api_key" in body:
        settings["llm_api_key"] = body["llm_api_key"]
    if "llm_model" in body:
        settings["llm_model"] = body["llm_model"]
    if "teacher_password" in body:
        settings["teacher_password"] = body["teacher_password"]
    write_settings(settings)
    return {"ok": True}


@router.get("/files/{qid}/{filename}")
async def serve_question_file(qid: str, filename: str):
    qdir = get_question_dir(qid)
    filepath = qdir / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404)
    from fastapi.responses import FileResponse
    return FileResponse(str(filepath))


@router.get("/preview/{qid}/{filename}")
async def serve_question_preview(qid: str, filename: str):
    """将题目文件转为 JPEG 预览图"""
    from services.llm_service import _image_to_base64
    import base64
    from io import BytesIO
    from fastapi.responses import Response

    qdir = get_question_dir(qid)
    filepath = qdir / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404)

    b64 = _image_to_base64(filepath)
    img_bytes = base64.b64decode(b64)
    return Response(content=img_bytes, media_type="image/jpeg")


# --- 学生名单管理（全局 StudentInfo 目录） ---

@router.get("/roster/classes")
async def list_roster_classes(request: Request):
    _require_auth(request)
    from services.question_service import list_classes
    classes = list_classes()
    return {"classes": classes}


@router.get("/roster/classes/{class_name}")
async def get_roster_class(request: Request, class_name: str):
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
    _require_auth(request)
    from services.question_service import create_class_roster
    csv_bytes = await file.read()
    count = create_class_roster(class_name, csv_bytes)
    return {"ok": True, "class_name": class_name, "count": count}


@router.delete("/roster/classes/{class_name}")
async def remove_roster_class(request: Request, class_name: str):
    _require_auth(request)
    from services.question_service import delete_class_roster
    ok = delete_class_roster(class_name)
    return {"ok": ok}


@router.get("/roster/template")
async def download_roster_template():
    """下载学生名单 CSV 模版（仅表头）"""
    from fastapi.responses import FileResponse
    from services.question_service import ensure_template
    tmpl_path = ensure_template()
    return FileResponse(
        tmpl_path,
        media_type="text/csv",
        filename="学生名单模版.csv",
    )
