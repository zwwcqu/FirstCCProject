# Agent Handoff — 工程图批阅系统

## 这是什么

一个 Web 应用：教师创建工程图绘制作业 → 学生上传 PDF/图片 → LLM 视觉模型三阶段自动批阅打分。

技术栈：**FastAPI (Python 3.9) + React 19 (TypeScript) + Tailwind CSS 4**

## 快速跑起来

```bash
# 后端（端口 8000）
cd backend && source venv/bin/activate && uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 前端（端口 5173，/api 代理到 8000）
cd frontend && npm run dev
```

首次启动需要 `config/app.dirconfig.json` 指向有效的数据目录（默认 `~/CadMarkData`），系统会自动初始化。

教师端默认密码：`MechCAD`

## 代码地图

```
backend/
  main.py                     ← FastAPI 入口，挂载两个 router
  config.py                   ← DATA_DIR 路径、settings/questions JSON 读写
  auth.py                     ← 会话管理（内存 token，4h 过期）
  routers/
    teacher.py                ← /api/teacher/* 全部接口（需登录）
    student.py                ← /api/student/* 公开接口（限流 50/min）
  services/
    llm_service.py            ← 核心：三阶段 LLM 调用（结构→量化→评分）
    question_service.py       ← 题目 CRUD、文件管理、学生名单
    grade_service.py          ← CSV 成绩读写（fcntl 文件锁）
    task_queue.py             ← 优先级任务队列（17 云 / 1 本地 worker）
    submit_status.py          ← 异步提交状态追踪（内存 dict）

frontend/src/
  App.tsx                     ← React Router：/student /teacher /teacher/dashboard /teacher/settings
  api.ts                      ← 所有 API 调用 + 图片 URL 工具函数
  pages/
    StudentPage.tsx           ← 学生端：选题→身份验证→上传→分析→评分
    TeacherDashboard.tsx      ← 教师端主面板：题目管理、成绩表、批量评分、参考图分析
    TeacherLogin.tsx          ← 登录页
    SettingsPage.tsx          ← LLM 配置页面
  components/
    FloatingImageViewer.tsx   ← 可拖拽/缩放/调整大小的浮动图片查看器

config/                       ← 模板与配置文件（checkin 到 git）
  结构分析模版.txt             ← LLM 提示词模板
  量化分析模版.txt
  评分模版1.md / 评分模版2.md
  二阶段修正提示词.txt
  学生名单模版.csv
  settings.example.json
```

## 核心流程：三阶段评分流水线

学生提交一张工程图 → 得到 9 级评分（A+ ~ F）：

```
PDF/图片上传
  ↓
【阶段1：结构分析】
  LLM 阅读学生图纸 → 输出 JSON（几何特征列表）
  ↓
【阶段2：量化分析】
  LLM 基于阶段1的 JSON → 输出量化数据（尺寸、公差、表面质量）
  ↓
【阶段3：两阶段评分】
  Phase 1：学生图 vs 参考图 结构相似度（60–100%）
  Phase 2：对照教师评分标准 + 量化数据打分（0–100%）
  总分 = Phase1 × Phase2 → 映射 A+ ≥93.75 → ... → F <50
```

关键点：
- 每阶段 LLM 调用都可注入教师的「补充知识」（材质、表面处理等）
- `test` 模式跳过分值写入和学生身份校验，`submit` 模式走完整流程
- 异步执行：学生提交后立刻返回，通过轮询 `/api/student/status?task_id=xxx` 获取进度

## LLM 双模型机制

`llm_service.py` 中维护两个 OpenAI 兼容客户端：
- **模型 0（通常是云端）**：高并发（17 worker），处理学生提交
- **模型 1（通常是本地 LM Studio）**：低并发（1 worker），处理教师端分析
- 手动切换：`settings.json` 中的 `llm_active` 字段，或设置页面切换

模型配置字段：
```json
{
  "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "api_key": "sk-xxx",
  "model": "qwen3.6-plus",
  "concurrency": 3
}
```
- `model` 为空字符串时：自动从 `/v1/models` 获取第一个模型（适用于 LM Studio 本地）
- `api_key` 为 `"lm-studio"` 时：不发送 Authorization header

## 数据存储（无数据库）

所有数据都是文件系统：

```
~/CadMarkData/
  settings.json               ← LLM 配置、教师密码
  questions.json              ← [{id: "02", title: "轴零件"}, ...]
  {qid}/
    题目内容.md                ← 题目描述（Markdown）
    阶段1评分标准.md           ← 评分标准
    阶段2评分标准.md
    补充知识.md                ← 可选，注入所有 LLM 提示词
    题目图片.png               ← 可选
    参考工程图.pdf             ← 参考图
    成绩+{qid}.csv             ← 成绩表
    student/                   ← 学生提交
      {学号}_{姓名}/           ← 每次提交放在学号子目录下
  StudentInfo/
    {班级名}.csv               ← 学生名单（表头：姓名,学号）
```

**重要**：CSV 写入使用 `fcntl.flock` 文件锁，并发安全。JSON 读写没有锁（写入频率低，竞争风险可接受）。

## 开发约束与坑

### Python 3.9 兼容性
- 服务文件中用 `from __future__ import annotations` 启用 `dict | None` 语法
- **FastAPI 路由参数**中不能用 `dict | None`，必须用 `Optional[dict]`（路由在运行时求值类型，`__future__` 只影响编译期）
- 类型提示导入：`from typing import Optional, List, Dict`

### PDF 处理
- `pdf2image` 依赖系统安装的 poppler（`pdfinfo`、`pdftoppm` 命令行工具）
- 图片上传后统一 resize 到 max 1024px（`llm_service.py` 中的 `_resize_image_if_needed`）

### 前端注意事项
- 前端路由（`/student`、`/teacher/*`）是 SPA 客户端路由，**不能代理到后端**
- `vite.config.ts` 只代理 `/api` 到后端
- 生产模式下 FastAPI 托管前端 SPA：对非 `/api` 路径返回 `frontend/dist/index.html`
- 图片 URL 需要加 `?t=timestamp` 防止浏览器缓存（文件更新后仍显示旧图）

### 前端热更新
- `npm run dev` 启动 Vite 开发服务器
- 修改前端代码自动热更新，无需手动刷新

### LLM JSON 解析容错
- LLM 返回的 JSON 可能被 markdown 代码块包裹（\`\`\`json ... \`\`\`）
- `llm_service.py` 中有 `_parse_json_response` 做容错提取
- 评分阶段的 JSON 输出结构复杂，修改提示词时务必同步更新解析逻辑

### 端口与 CORS
- 后端 CORS 全开（`allow_origins=["*"]`），方便局域网其他设备访问
- 开发模式下前端 Vite 在 5173，代理 `/api` → 后端 8000

## 常见修改任务指引

### 修改评分标准 / 提示词
编辑 `config/` 下的模板文件，然后修改 `backend/services/llm_service.py` 中的对应函数（提示词读取 + JSON 解析）。

### 增加新的成绩字段
1. `grade_service.py`：修改 CSV header 和写入逻辑
2. `routers/teacher.py`：修改成绩读写接口的字段处理
3. `frontend/src/pages/TeacherDashboard.tsx`：修改成绩表格列和编辑逻辑

### 调整 LLM 模型配置
1. `config/settings.example.json`：修改默认模板
2. `backend/routers/teacher.py`：修改 settings 读写接口（如有结构变化）
3. `backend/services/llm_service.py`：修改 `get_llm_client()` / `get_llm_model()` 逻辑

### 调试学生提交流程
1. 学生端：打开浏览器 DevTools Network 面板观察 API 调用
2. 后端日志：uvicorn 控制台输出 + `submit_status` 的状态追踪
3. 中间结果：检查 `data/{qid}/student/{学号}/` 下的原始 LLM JSON

## 环境差异

当前开发机：
- macOS (Apple Silicon)，Homebrew 管理依赖
- LM Studio 本地模型运行在内网 `100.125.140.73:11234`
- 生产环境可能完全依赖云端 API（阿里云 DashScope）

其他机器部署时注意：
- poppler 路径可能不同
- `config/app.dirconfig.json` 中的 `data_dir` 需要指向实际数据目录
- 如有已存在的 `CadMarkData` 目录，直接拷贝即可保留所有题目和成绩数据
