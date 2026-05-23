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

router = APIRouter(prefix="/api/student", tags=["student"])


@router.get("/questions")
async def get_questions():
    return list_questions()


@router.get("/questions/{qid}")
async def get_question_detail(qid: str):
    q = get_question(qid)
    if q is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    files = get_question_files(qid)
    q["files"] = files
    return q


@router.post("/submit/{qid}")
async def submit(
    qid: str,
    name: str = Form(...),
    student_id: str = Form(...),
    file: UploadFile = File(...),
    mode: str = Form("submit"),
):
    q = get_question(qid)
    if q is None:
        raise HTTPException(status_code=404, detail="题目不存在")

    is_test = mode == "test"

    # roster check (only in submit mode)
    if not is_test:
        from services.question_service import check_roster
        ok, msg = check_roster(name, student_id)
        if not ok:
            raise HTTPException(status_code=403, detail=msg)

    # check overwrite (only in submit mode)
    overwrite = False
    if not is_test:
        existing = get_student_grade(qid, student_id)
        if existing:
            overwrite = True

    file_bytes = await file.read()
    student_path = save_student_submission(qid, student_id, name, file_bytes, file.filename or "submission.pdf")

    # build reference paths (仅参考工程图，不含题目附图)
    qdir = get_question_dir(qid)
    files = get_question_files(qid)
    ref_paths = []
    if files["reference_pdf"]:
        ref_paths.append(qdir / files["reference_pdf"])

    # call LLM
    try:
        result = grade_submission(
            description=files["description"],
            phase1_criteria=files["phase1_criteria"],
            phase2_criteria=files["phase2_criteria"],
            reference_paths=ref_paths,
            student_submission_path=Path(student_path),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批阅失败: {str(e)}")

    grade = result.get("grade", "N/A")

    # save only in submit mode
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


@router.get("/result/{qid}/{student_id}")
async def get_result(qid: str, student_id: str):
    row = get_student_grade(qid, student_id)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到成绩")
    return row


@router.get("/files/{qid}/{filename}")
async def serve_student_file(qid: str, filename: str):
    from services.question_service import get_student_dir
    sdir = get_student_dir(qid)
    filepath = sdir / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(str(filepath))


@router.get("/preview/{qid}/{filename}")
async def serve_student_preview(qid: str, filename: str):
    """将学生提交的文件转为 JPEG 预览图"""
    from services.question_service import get_student_dir
    from services.llm_service import _image_to_base64
    import base64
    from io import BytesIO
    from fastapi.responses import Response

    sdir = get_student_dir(qid)
    filepath = sdir / filename
    if not filepath.exists() or not filepath.is_file():
        raise HTTPException(status_code=404)

    b64 = _image_to_base64(filepath)
    img_bytes = base64.b64decode(b64)
    return Response(content=img_bytes, media_type="image/jpeg")
