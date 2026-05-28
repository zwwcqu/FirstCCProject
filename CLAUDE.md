# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

工程图批阅系统 (Engineering Drawing Grading System) — a web app where teachers create drawing assignments, students submit PDF/image drawings, and an LLM vision model grades them via a three-stage pipeline:

1. **结构分析** (structure analysis) — LLM reads the drawing and extracts geometric features
2. **量化分析** (quantitative analysis) — LLM quantifies dimensions, tolerances, surface quality from the structure JSON
3. **两阶段评分** (two-phase grading):
   - Phase 1: visual comparison of student vs reference drawing structure (similarity 60–100%)
   - Phase 2: scoring against teacher criteria using quantitative analysis (0–100%)
   - Total = Phase1 × Phase2, mapped to 9-level grades (A+≥93.75 → F<50)

The teacher can also provide **补充知识** (supplementary knowledge) about the drawing (e.g., material, surface finish) which is injected into all LLM prompts that read images.

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
- **`auth.py`** — In-memory session tokens (4h timeout), password PBKDF2-SHA256 hashed in `data/settings.json`
- **`routers/teacher.py`** — All under `/api/teacher`, requires `_require_auth` (session cookie). Question CRUD with knowledge field, grades CSV viewer/editor, batch grading, roster class management, reference analysis, file/preview serving, supplement submission
- **`routers/student.py`** — Public `/api/student` endpoints. List questions, get detail, submit homework (`mode=test|submit`), three-step non-blocking flow (upload → analyze → grade) with status polling, result/analysis queries, file/preview serving. Rate limiting (50 req/min per IP)
- **`services/llm_service.py`** — OpenAI-compatible client with dual-model support (local LM Studio + cloud). All functions accept optional `knowledge` param. Key functions: `analyze_structure()`, `analyze_quantitative()`, `grade_phase1()`, `grade_phase2()`, `run_two_phase_grading()`. PDF→PNG conversion (pdf2image + poppler), image resize (max 1024px). JSON parsing with markdown code-block tolerance
- **`services/question_service.py`** — Question CRUD with knowledge support (`补充知识.md`), file management, global roster class management (`data/StudentInfo/`, one CSV per class), student submission record tracking (`submissions.json`)
- **`services/grade_service.py`** — CSV grade persistence (`data/{qid}/成绩+{qid}.csv`), overwrite by student ID. fcntl file locking for concurrent safety. Fields include 教师评语 (teacher comment) and dimension-level evaluations
- **`services/task_queue.py`** — Priority-based task queue with thread pool (17 workers for cloud, 1 for local). Teacher tasks (priority 1-9) preempt student tasks (priority 10+)
- **`services/submit_status.py`** — In-memory dictionary for tracking async submit progress (upload/analyze/grade steps with done/error states)

### Frontend (`frontend/src/`)

- **`api.ts`** — All API calls, `fetch` with `credentials: "include"`. Teacher functions require session cookie. Image URL helpers (`getQuestionFileUrl`, `getTeacherPreviewUrl`, `getStudentPreviewUrl`, `getTeacherStudentPreviewUrl`) with cache-busting `?t=` parameter
- **`App.tsx`** — React Router: `/student`, `/teacher`, `/teacher/dashboard`, `/teacher/settings`, fallback redirect to `/student`
- **`pages/StudentPage.tsx`** — Student flow: select question → check identity → upload file → analyze → grade. Test mode skips roster validation and grade saving. Compact header bar. Right-side floating image viewer (auto-opens on analysis/result). Teacher comment display in results
- **`pages/TeacherDashboard.tsx`** — Question CRUD with knowledge field, grades table (per-question modal with inline cell editing), batch grading with checkbox selection, roster management modal, reference analysis with expandable results, **review modal** (draggable, grade dropdown + two-phase scores + teacher comment textarea), supplement submission. Multiple `FloatingImageViewer` instances for reference drawing and student drawing preview
- **`pages/TeacherLogin.tsx`** — Password login, redirects to dashboard
- **`pages/SettingsPage.tsx`** — Configure LLM API base/model/key and teacher password
- **`components/FloatingImageViewer.tsx`** — Reusable floating image viewer. Features: draggable by title bar, resizable (bottom-right handle, min 240×220), zoom (scroll/pinch 0.5×–5×), pan when zoomed. Document-level mousemove for smooth drag/resize. Used by both StudentPage and TeacherDashboard
- **`vite.config.ts`** — Port 5173, proxies only `/api` to backend (NOT `/student` or `/teacher` — those are SPA routes)

## Data layout

```
data/
  settings.json               # teacher_password, llm_api_base, llm_api_key, llm_model
  questions.json              # [{id: "02", title: "轴零件"}, ...]
  {qid}/                      # Per-question directory
    题目内容.md                # Question description
    阶段1评分标准.md           # Phase 1 grading criteria
    阶段2评分标准.md           # Phase 2 grading criteria
    补充知识.md                # Supplementary knowledge for LLM (optional)
    题目图片.png               # Question illustration image (optional)
    参考工程图.pdf             # Reference drawing (optional)
    成绩+{qid}.csv             # Grades CSV (fields: 班级, 姓名, 学号, 提交时间, 成绩,
                              #   阶段1相似度, 阶段2评分, 总分, 相似度评价, 总评,
                              #   图样表达, 尺寸标注, 尺寸公差, 表面质量, 形位公差, 教师评语)
    student/                  # Student submissions + raw LLM JSON results
    submissions.json          # Per-student submission records
  StudentInfo/                # Global student roster
    _模版.csv                  # Template (header: 姓名,学号)
    {班级名}.csv               # Per-class roster

config/                       # Config templates (checked into repo)
  app.dirconfig.json          # Points to data/ directory
  settings.example.json       # Example settings
  结构分析模版.txt             # Structure analysis prompt template
  结构分析_学生.txt            # Student-specific variant
  量化分析模版.txt             # Quantitative analysis prompt template
  量化分析_学生.txt            # Student-specific variant
  评分模版1.md / 评分模版2.md  # Grading prompt templates
  二阶段修正提示词.txt          # Phase 2 correction hints
  学生名单模版.csv              # Roster CSV template
```

## Key constraints

- Python 3.9 — use `from __future__ import annotations` in service files for `dict | None` syntax, but use `Optional[X]` in FastAPI route parameters (runtime type evaluation)
- LLM: dual-model support — local LM Studio at `100.125.140.73:11234` (leave `llm_model` empty for auto-detection) or cloud DashScope qwen model
- PDF handling requires `pdf2image` + poppler installed on the host
- Backend serves frontend assets in production; dev uses separate Vite server with proxy
- No database — all storage is file-system based (JSON, CSV, Markdown, images/PDFs)
- fcntl file locking for concurrent grade CSV writes
- Student submissions auto-convert non-PNG images to PNG; PDFs get PNG preview generated
- Task queue: 17 cloud workers / 1 local worker, teacher priority < student priority
- Rate limiting on student submit endpoints: 50 req/min per IP
