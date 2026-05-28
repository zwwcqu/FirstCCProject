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

_REQUEST_TIMEOUTS = {
    "/api/student/upload": 120,     # 上传 + PNG 转换
    "/api/teacher/questions": 120,  # 题目创建（含上传）
    "/api/student/analyze": 10,     # 分析入队，应该很快
    "/api/student/grade": 10,       # 评分入队，应该很快
}
_DEFAULT_TIMEOUT = 60


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

# CORS 开放给内网使用
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
