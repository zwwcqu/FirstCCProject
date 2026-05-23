# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

工程图批阅系统 (Engineering Drawing Grading System) — a web app where teachers create drawing assignments, students submit PDF/image drawings, and an LLM vision model grades them. Two-phase grading: Phase 1 compares student drawing with reference (similarity 60–100%), Phase 2 scores against teacher criteria (0–100%). Total = Phase1 × Phase2, mapped to 9-level grades (A+≥93.75 → F<50).

## Commands

```bash
# Backend (Python 3.9 venv at backend/venv)
cd backend && ./venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Frontend dev server (proxies /api → localhost:8000)
cd frontend && npm run dev

# Frontend production build
cd frontend && npm run build
```

In production, the FastAPI server serves the built frontend SPA from `frontend/dist/`. CORS is wide open (`allow_origins=["*"]`) for LAN access.

## Architecture

```
Browser (React SPA)
  → /api/* → FastAPI backend (port 8000)
  → /student, /teacher/* → React Router (client-side), proxied by FastAPI in prod
```

### Backend (`backend/`)

- **`main.py`** — FastAPI app, mounts two routers, serves SPA fallback in production
- **`config.py`** — `DATA_DIR` path, JSON read/write for settings and question index. Path helpers: `get_question_dir(qid)` → `data/{qid}`, `get_student_dir(qid)` → `data/{qid}/student`
- **`auth.py`** — In-memory session tokens (4h timeout), password stored in `data/settings.json`
- **`routers/teacher.py`** — All under `/api/teacher`, requires `_require_auth` (session cookie). Question CRUD, grades CSV viewer, global roster class management, settings, file/preview serving
- **`routers/student.py`** — Public `/api/student` endpoints. List questions, get detail, submit homework (`mode=test|submit`), get results, file/preview serving
- **`services/llm_service.py`** — OpenAI-compatible client. Auto-detects model from LM Studio if not configured. `_image_to_base64(path)` handles PDF→image conversion (via pdf2image + poppler) and resize (max 1024px long side). `grade_submission()` builds a prompt labeling reference as `【参考工程图】` and student as `【学生提交的工程图】`, calls LLM, parses JSON, computes grade
- **`services/question_service.py`** — Question CRUD, file management, and **global roster class management** (under `data/StudentInfo/`, one CSV per class with headers `姓名,学号`)
- **`services/grade_service.py`** — CSV grade persistence (`data/{qid}/成绩+{qid}.csv`), overwrite by student ID

### Frontend (`frontend/src/`)

- **`api.ts`** — All API calls. Uses `fetch` with `credentials: "include"`. Teacher functions require session cookie (set on login). `getQuestionFileUrl`, `getTeacherPreviewUrl`, `getStudentPreviewUrl` construct direct URLs with optional cache-busting `?t=` parameter
- **`App.tsx`** — React Router: `/student`, `/teacher`, `/teacher/dashboard`, `/teacher/settings`, fallback redirect to `/student`
- **`pages/StudentPage.tsx`** — Student submission flow: select question → choose mode (test/submit) → upload file → see result. Test mode disables name/student-ID fields, uses placeholders, skips roster validation and grade saving. Submit mode enforces roster check and shows overwrite confirmation
- **`pages/TeacherDashboard.tsx`** — Question CRUD (modal form), grades table viewer (per-question modal), global roster management (independent "学生信息" modal: download template, add class by name + CSV, expand to view students, delete class)
- **`pages/TeacherLogin.tsx`** — Password login, redirects to dashboard
- **`pages/SettingsPage.tsx`** — Configure LLM API base/model/key and teacher password
- **`vite.config.ts`** — Port 5173, proxies only `/api` to backend (NOT `/student` or `/teacher` — those are SPA routes)

## Data layout

```
data/
  settings.json         # teacher_password, llm_api_base, llm_api_key, llm_model
  questions.json        # [{id: "02", title: "轴零件"}, ...]
  {qid}/                # Per-question directory
    题目内容.md          # Question description
    批改要求.md          # Grading requirements/rubrics
    题目图片.png         # Question image (optional)
    参考工程图.pdf       # Reference drawing (optional)
    成绩+{qid}.csv       # Grades CSV
    student/            # Student submissions + raw LLM JSON results
  StudentInfo/          # Global student roster
    _模版.csv            # Template (header only: 姓名,学号)
    {班级名}.csv         # Per-class roster
```

## Key constraints

- Python 3.9 — use `from __future__ import annotations` in service files for `dict | None` syntax, but use `Optional[X]` in FastAPI route parameters (runtime type evaluation)
- LM Studio LLM at `100.125.140.73:11234` — leave `llm_model` empty in settings for auto-detection
- PDF handling requires `pdf2image` + poppler installed on the host
- Backend serves frontend assets in production; dev uses separate Vite server with proxy
- No database — all storage is file-system based (JSON, CSV, Markdown, images/PDFs)
