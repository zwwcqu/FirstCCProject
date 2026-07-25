# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

工程图批阅系统 (Engineering Drawing Grading System) — a web app where teachers create drawing assignments, students submit PDF/image drawings, and an LLM vision model grades them via a three-stage pipeline:

1. **结构分析** (merged analysis) — single LLM call extracts geometric structure + quantitative data from the drawing image
2. **阶段一** (Phase 1) — visual comparison of student vs reference drawing structure using thumbnails (768px)
3. **阶段二** (Phase 2) — text-only comparison of quantitative JSON data (dimensions, tolerances, roughness)
4. **评分** — Total = √(Phase1 × Phase2), mapped to 9-level grades (A+≥90 → F<50)

## Commands

```bash
# Backend (Python 3.9 venv)
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
- **`config.py`** — DATA_DIR path, JSON/CVS read/write for settings and question index. **All parameters are configurable via settings.json — no hardcoded values.** Provides parameter helpers: `get_llm_params()`, `get_image_params()`, `get_grade_thresholds()`, `get_prompt_templates()`, `get_scoring_templates()`. Also reads `settings_debug.json` for debug/ops params.
- **`auth.py`** — Multi-teacher PBKDF2-SHA256 login (username+password from TeacherInfo CSV), student password system (default `cad123`, force change on first login), session management (in-memory + file). Student passwords stored per-class in `StudentAuth/{class}.json`.
- **`routers/teacher.py`** — All under `/api/teacher`, requires `_require_auth` (session cookie). Question CRUD with ownership enforcement (only creator can edit/delete), grades CSV viewer/editor, batch grading, roster class management, reference analysis, file/preview serving, supplement submission, **student password reset**, **teacher profile editing**.
- **`routers/student.py`** — Public `/api/student` endpoints. List questions (filtered by class), get detail, student login (name+ID+password), submit homework (`mode=test|submit`), three-step non-blocking flow (upload → analyze → grade) with status polling, result/analysis queries, file/preview serving. Rate limiting (50 req/min per IP). **Deadline enforcement on upload.**
- **`services/llm_service.py`** — OpenAI-compatible client with dual-model support (local LM Studio + cloud). All parameters from settings. Key functions: `analyze_merged()` (single-call structure+quantitative), `grade_phase1()` (thumbnails at 768px), `grade_phase2()` (simplified JSON comparison), `run_two_phase_grading()`. PDF→PNG conversion (pdf2image + poppler), image resize.
- **`services/question_service.py`** — Question CRUD with deadline, knowledge, teacher ownership, and class filtering.
- **`services/grade_service.py`** — CSV grade persistence with fcntl file locking. 18-column format.
- **`services/task_queue.py`** — Priority-based task queue with configurable concurrency.
- **`services/submit_status.py`** — In-memory status tracking for async submit progress.

### Frontend (`frontend/src/`)

- **`api.ts`** — All API calls with credential forwarding
- **`App.tsx`** — React Router: `/student`, `/teacher`, `/teacher/dashboard`, `/teacher/settings`
- **`pages/StudentPage.tsx`** — Student flow: login (name+ID+password) → select question (filtered by class, deadline display) → upload → analyze → grade. Password change on first login + settings button.
- **`pages/TeacherDashboard.tsx`** — Question CRUD with ownership (grey out others'), deadline input, class checkboxes, grades table with inline editing, batch grading, roster management, reference analysis, review modal with ownership enforcement.
- **`pages/TeacherLogin.tsx`** — Username+password login
- **`pages/SettingsPage.tsx`** — 7 tabs: Profile, Model Config, LLM Params, Image Processing, Grade Thresholds, Analysis Templates, Scoring Templates, System Management
- **`components/FloatingImageViewer.tsx`** — Draggable, resizable, zoomable image viewer

## Settings system

### `settings.json` (teacher-configurable via Settings page)
- `models[]` — LLM model configurations (name, api_base, api_key, model, concurrency)
- `llm_params` — temperature, max_tokens, enable_thinking, client_timeout
- `image_params` — analysis_max_size, analysis_dpi, phase1_max_size, phase1_jpeg_quality, analysis_jpeg_quality
- `grade_thresholds` — A+/A/B+/B/C+/C/D+/D score boundaries
- `prompt_templates` — 7 templates (structure analysis ×2, quantitative ×2, phase1/2 guides, phase2 correction hint)
- `scoring_templates` — Default scoring criteria for new questions

### `settings_debug.json` (server-side only)
- rate_limit, sessions, submit_status, task_queue, request_timeouts, photo_detection, pdf_validation_dpi, frontend, log_level

## Multi-teacher system

- Teachers stored in `data/TeacherInfo/教师名单.csv` (姓名, 用户名, 工号)
- Default password: `MechCAD` (PBKDF2 hashed in `data/TeacherAuth/teachers.json`)
- Template in `config/教师名单模版.csv` (admin only)
- Each question records `teacher` (creator) and `classes` (applicable class list)
- Only creator can edit/delete questions or modify grades
- All teachers can view and grade any question

## Student password system

- Default password: `cad123`, stored PBKDF2 hashed in `data/StudentAuth/{class}.json`
- First login forces password change (min 6 chars, old password verification, confirm new password)
- Teachers can reset student passwords to default via roster management
- Settings button (⚙) in student page for voluntary password change

## Question ownership & class filtering

- Questions have `teacher` (creator username) and `classes` (comma-separated) fields in `questions.json`
- Students only see questions matching their class
- Teachers see all questions but can only edit/delete their own
- Grade editing (both inline and review modal) restricted to question owner
- Frontend: non-owned questions shown greyed out with "只读" label

## Data layout

```
data/
  settings.json               — System settings (teacher-configurable)
  settings_debug.json          — Debug/ops parameters
  questions.json               — [{id, title, submission_type, teacher, classes, deadline}, ...]
  StudentInfo/
    _模版.csv                  — Template (header: 姓名,学号)
    {班级名}.csv               — Per-class roster
  StudentAuth/
    {班级名}.json              — Student password hashes
  TeacherInfo/
    教师名单.csv               — Teacher roster (姓名, 用户名, 工号)
  TeacherAuth/
    teachers.json              — Teacher password hashes
  .sessions/                   — Session token files
  {qid}/                       — Per-question directory
    题目内容.md                 — Question description
    阶段1评分标准.md            — Phase 1 grading criteria
    阶段2评分标准.md            — Phase 2 grading criteria
    补充知识.md                 — Supplementary knowledge for LLM (optional)
    题目图片.png                — Question illustration (optional)
    参考工程图.pdf              — Reference drawing (optional)
    参考图_结构分析.json         — Cached structure analysis
    参考图_量化分析.json         — Cached quantitative analysis
    成绩+{qid}.csv              — Grades CSV (18 columns)
    submissions.json            — Per-student submission records
    student/                    — Student submissions + analysis JSONs
  backup/                      — Soft-deleted questions

config/                        — Config templates (checked into repo)
  app.dirconfig.json            — Points to data/ directory
  settings.example.json         — Full settings template
  settings_debug.example.json   — Debug settings template
  结构分析模版.txt              — Reference structure analysis prompt
  结构分析_学生.txt             — Student structure analysis prompt
  量化分析模版.txt              — Reference quantitative analysis prompt
  量化分析_学生.txt             — Student quantitative analysis prompt
  评分模版1.md / 评分模版2.md   — Scoring template defaults
  二阶段修正提示词.txt           — Phase 2 correction hints
  学生名单模版.csv              — Student roster CSV template
  教师名单模版.csv              — Teacher roster CSV template
  DrawingForCheck.png          — Test reference drawing
```

## Key constraints

- Python 3.9+ — use `from __future__ import annotations`
- LLM: dual-model support — local LM Studio or cloud DashScope qwen model
- PDF handling requires `pdf2image` + poppler installed on the host
- No database — all storage is file-system based (JSON, CSV, Markdown, images/PDFs)
- fcntl file locking for concurrent grade CSV writes
- Student submissions auto-convert non-PNG images to PNG; PDFs get PNG preview
- Task queue: teacher priority (0) > batch (5) > student (10)
- Rate limiting on student submit endpoints: 50 req/min per IP
- All numeric parameters are configurable — no hardcoded magic numbers
