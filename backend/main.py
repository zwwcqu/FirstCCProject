"""
工程图批阅系统 — FastAPI 入口。

功能：
- 配置 CORS、挂载路由（teacher / student）
- 生产模式下托管前端 SPA（frontend/dist/）
- 启动时初始化数据目录（config._init_data_dir）
- 配置全局日志格式与级别
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from config import _init_data_dir
from routers import teacher, student

# ── 日志配置 ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：初始化数据目录 + 启动 LLM 任务队列"""
    logger.info("正在初始化数据目录…")
    _init_data_dir()
    from services.task_queue import start as start_queue
    start_queue()
    logger.info("应用启动完成")
    yield
    from services.task_queue import stop as stop_queue
    stop_queue()
    logger.info("应用关闭")


app = FastAPI(title="工程图批阅系统", lifespan=lifespan)

# ── 请求超时中间件 ─────────────────────────────────────────
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
import asyncio

def _get_request_timeouts() -> dict:
    """从 debug 配置读取各 API 端点的请求超时（秒）"""
    try:
        from config import read_settings_debug
        return read_settings_debug().get("request_timeouts", {})
    except Exception:
        return {}

_REQUEST_TIMEOUTS = {
    "/api/student/upload": _get_request_timeouts().get("upload", 120),
    "/api/teacher/questions": _get_request_timeouts().get("teacher_questions", 120),
    "/api/student/analyze": _get_request_timeouts().get("analyze", 10),
    "/api/student/grade": _get_request_timeouts().get("grade", 10),
}
_DEFAULT_TIMEOUT = _get_request_timeouts().get("default", 60)


class TimeoutMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        timeout = _DEFAULT_TIMEOUT
        for prefix, t in _REQUEST_TIMEOUTS.items():
            if request.url.path.startswith(prefix):
                timeout = t
                break
        try:
            return await asyncio.wait_for(call_next(request), timeout=timeout)
        except asyncio.TimeoutError:
            return JSONResponse({"detail": "请求超时，请重试"}, status_code=504)


app.add_middleware(TimeoutMiddleware)


# ── API 响应禁用缓存 ───────────────────────────────────────
class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


app.add_middleware(NoCacheMiddleware)

# CORS — 仅允许前端开发服务器和本地访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

app.include_router(teacher.router)
app.include_router(student.router)

# ── 前端 SPA 托管（仅生产模式下 dist/ 存在时生效）──────────
FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"

if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")


@app.get("/student")
@app.get("/student/")
async def serve_student_spa():
    """学生端 SPA 入口"""
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/teacher")
@app.get("/teacher/")
@app.get("/teacher/{rest:path}")
async def serve_teacher_spa():
    """教师端 SPA 入口"""
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/")
async def root():
    """根路径，直接返回前端首页"""
    return FileResponse(FRONTEND_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
