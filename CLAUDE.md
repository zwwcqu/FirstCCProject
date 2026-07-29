# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

工程图批阅系统 (Engineering Drawing Grading System) — a web app where teachers create drawing assignments, students submit drawings, and an LLM vision model grades them.

## 提交类型

系统支持三种提交类型（`submission_type`）：`pdf`、`image`、`dxf`。每个题目只能选一种。

### PDF / 图片流程（LLM 视觉识读）

1. **参考图分析** (teacher, once per question) — single LLM call extracts all drawing info from the reference
2. **合并评分** (per student) — single LLM call does: drawing analysis + phase 1 (visual comparison) + phase 2 (quantitative comparison)
3. **评分** — Total = √(Phase1 × Phase2), mapped to 9-level grades (A+≥90 → F<50)

### DXF 流程（ezdxf 提取 + LLM 评分）

1. **参考图提取** (teacher, once per question) — `process_dxf()` 统一入口：ezdxf 提取 + 渲染两张预览图（含尺寸 + 无尺寸），走 **DXF 串行队列** `dxf_task_queue`（concurrency=1，避免渲染抢 CPU）
2. **学生图提取** (per student) — 同上 `process_dxf()`，走 DXF 串行队列
3. **DXF 评分** — 单次 LLM 调用，发送**无尺寸预览图**（纯几何，Phase 1 视觉对比）+ 结构化数据（Phase 2 量化对比）。走 LLM 任务队列 `task_queue`
4. **评分** — Total = √(Phase1 × Phase2), mapped to 9-level grades (A+≥90 → F<50)

### 服务池架构

| 队列 | 文件 | 并发 | 用途 |
|------|------|:--:|------|
| LLM 队列 | `task_queue.py` | 3（可配） | PDF/图片 LLM 分析 + 所有评分 |
| DXF 队列 | `dxf_task_queue.py` | 1（串行） | 教师/学生 DXF 提取+渲染 |

LLM 调用在线程中同步阻塞等待——不阻塞 FastAPI 事件循环。调大 models[].concurrency 即可增加 LLM 并发数（上限由 settings_debug.json → task_queue.max_concurrency 控制）。

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

## 修改前端的注意事项

修改前端 TSX/TS 文件后必须运行 `npm run build`（或 `npx vite build`）确认构建成功，**不能只依赖 `npx tsc --noEmit`**——它不会发现 JSX 结构错误（如未闭合标签、多余闭合），这些错误会导致生产构建失败，用户打开页面空白。

修改后端 Python 函数后，**必须启动服务器并用 curl 测试受影响的 API 端点**，不能只做 `py_compile` 或 import 检查——变量重命名、函数签名变更等会导致运行时 NameError / TypeError，编译检查发现不了。涉及核心流程（提交作业、评分、文件命名）的修改尤其需要端到端测试。

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
- **`services/llm_service.py`** — OpenAI-compatible client with dual-model support (local LM Studio + cloud). Key functions: `analyze_merged()` (reference analysis, 1 call), `analyze_and_grade()` (student analysis + grading, 1 combined call), `grade_dxf()` (DXF scoring with images + structured data), `grade_combined()` (scoring only, no analysis). PDF→PNG conversion, image resize.
- **`services/dxf_service.py`** — DXF file processing via ezdxf. Key functions:
  - `extract_dxf()` — parses entities (LINE/CIRCLE/ARC/LWPOLYLINE/MLINE/SPLINE/ELLIPSE/INSERT→exploded), dimensions (linear/angular/diameter/radius/ordinate via `dimtype & 0x0F` + `get_measurement()`), texts, layers, hatches, centerlines.
  - `render_dxf_preview()` — matplotlib backend, mm 坐标，长边 768px。修复了 DXF 颜色 7→黑（白底可见）、BYBLOCK→BYLAYER（标注可见）、`adjust_figure=False`（画布不变形）。
  - `process_dxf()` — 统一入口：提取数据 + 渲染含尺寸/无尺寸两张预览图。
  - `run_dxf_grade()` — 统一评分入口：无尺寸图做 Phase 1 视觉对比，结构化数据做 Phase 2 量化对比。
- **`services/dxf_task_queue.py`** — DXF 处理串行队列，concurrency=1，独立于 LLM 队列。
- **`services/task_queue.py`** — LLM 任务队列。Priority: teacher(0) > batch(5) > student(10)。Worker 数 = min(model.concurrency, max_concurrency)。去重（同 key 不重复入队）。
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
- `dxf_params` — preview_dpi, preview_max_size, preview_bg, preview_fg, preview_linewidth_scale
- `grade_thresholds` — A+/A/B+/B/C+/C/D+/D score boundaries
- `prompt_templates` — `grading_guide` (scoring guidance for LLM)
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

- Questions have `teacher` (creator username), `classes` (comma-separated), and `visible_to_others` (0=仅限本人, 1=其他教师可见) fields in `questions.json`
- Question IDs auto-generated as `YYMMDD-NNN` (e.g. `260727-001`), no manual input needed
- Students only see questions matching their class
- Teachers: non-owned questions hidden if `visible_to_others=0`; visible but read-only if `=1`
- Grade editing restricted to question owner
- Frontend: non-owned questions shown greyed out with "只读" label

## DXF rendering quirks

- **Color 7 (white/black)**: ezdxf maps to `#FFFFFF`; fixed by setting `layer.rgb = (0,0,0)` before rendering (white bg → black lines)
- **BYBLOCK (color=0)**: ezdxf doesn't push state for DIMENSION, so BYBLOCK entities resolve to `#FFFFFF` (invisible). Fixed by changing block entity color `0 → 256` (BYLAYER)
- **Dimension layer (文本层, color=212)**: dark purple `#a500a5` blends with black lines; set to bright blue `(0,102,204)` for visibility
- **MatplotlibBackend**: `adjust_figure=False` to keep canvas size; `fig.add_axes([0,0,1,1])` for no margins
- Coordinates are ISO/GB mm. Long side scaled to 768px.

## Data layout

```
data/
  settings.json               — System settings (teacher-configurable)
  settings_debug.json          — Debug/ops parameters
  questions.json               — [{id, title, submission_type, teacher, classes, deadline}, ...]
  templates/                   — Global template work copies (copied from config/ on init)
    零件图识读模板.txt
    装配图识读模板.txt
    平面图识读模板.txt
    组合体三视图识读模板.txt
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
    识读模板.txt                — Per-question analysis template (copied from global; PDF/image only)
    题目图片.png                — Question illustration (optional)
    参考工程图.pdf              — Reference drawing (PDF, optional)
    参考工程图.dxf              — Reference drawing (DXF, optional)
    参考工程图.png              — Reference DXF rendered preview (auto-generated)
    参考图_分析.json             — Cached reference analysis (LLM for PDF/image; ezdxf for DXF)
    成绩+{qid}.csv              — Grades CSV (18 columns)
    submissions.json            — Per-student submission records
    student/                    — Student submissions + analysis JSONs
      {name}_{id}.dxf           — Student DXF submission
      {name}_{id}.pdf           — Student PDF submission
      {name}_{id}.png           — Student submission preview (auto-generated)
      {name}_{id}_分析.json      — Student analysis (LLM or ezdxf)
      {name}_{id}_评分.json      — Student grading result
  backup/                      — Soft-deleted questions

config/                        — Config templates (checked into repo)
  app.dirconfig.json            — Points to data/ directory
  settings.example.json         — Full settings template
  settings_debug.example.json   — Debug settings template
  零件图识读模板.txt            — Part drawing analysis template (8 data categories)
  装配图识读模板.txt            — Assembly drawing analysis template
  平面图识读模板.txt            — 2D flat/beginner drawing template
  组合体三视图识读模板.txt       — Combined 3-view drawing template
  评分模版1.md / 评分模版2.md   — Scoring template defaults
  DrawingForCheck.png          — Test reference drawing
  学生名单模版.csv              — Student roster CSV template
  教师名单模版.csv              — Teacher roster CSV template
```

## Key constraints

- Python 3.9+ — use `from __future__ import annotations`
- LLM: dual-model support — local LM Studio or cloud DashScope qwen model
- PDF handling requires `pdf2image` + poppler installed on the host
- DXF handling requires `ezdxf` + `matplotlib` (`render_dxf_preview`)
- **API Key 安全**: 后端脱敏返回 `sk-c****dwyy`（首尾各 4 字符）。测试接口后端查真实 Key。前端无显示/隐藏按钮
- No database — all storage is file-system based (JSON, CSV, Markdown, images/PDFs/DXFs)
- fcntl file locking for concurrent grade CSV writes
- **PDF/image grading**: single `analyze_and_grade()` call combines analysis + phase1 + phase2
- **DXF grading**: `process_dxf()` (ezdxf, no LLM) → `grade_dxf()` (LLM: 无尺寸预览图 + structured data)
- **DXF Phase 1**: uses 无尺寸 preview (pure geometry) for visual comparison, not 有尺寸 version
- Student image sent twice: large (3508px) for analysis, thumbnail (768px) for comparison
- Reference image cached as `参考图_分析.json`, reused across all students
- DXF `参考图_分析.json` format: `{entities, dimensions, texts, layers, entity_counts, bounds}`
- Frontend auto-detects DXF data by presence of `entities` + `entity_counts` fields
- Two task queues: LLM queue (concurrency configurable) + DXF queue (serial, concurrency=1)
- Task queue priority: teacher (0) > batch (5) > student (10)
- Rate limiting on student submit endpoints: 50 req/min per IP
- Student analysis/grading status: now checks **actual disk files** instead of `submissions.json` status field
