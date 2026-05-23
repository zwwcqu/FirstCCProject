from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from routers import teacher, student

app = FastAPI(title="工程图批阅系统")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(teacher.router)
app.include_router(student.router)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"

if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")


@app.get("/student")
@app.get("/student/")
async def serve_student_spa():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/teacher")
@app.get("/teacher/")
@app.get("/teacher/{rest:path}")
async def serve_teacher_spa():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/")
async def root():
    return FileResponse(FRONTEND_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
