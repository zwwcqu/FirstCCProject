# 工程图批阅系统 — 安装与运行说明

## 环境要求

| 依赖 | 最低版本 | 用途 |
|---|---|---|
| **Python** | 3.9 | 后端运行时 |
| **Node.js** | 18+ | 前端构建与开发服务器 |
| **npm** | 随 Node.js | 包管理器 |
| **poppler** | — | PDF 转 PNG（`pdf2image` 的底层依赖） |

### 安装系统依赖

**macOS:**
```bash
brew install python@3.9 node poppler
```

**Ubuntu / Debian:**
```bash
sudo apt update
sudo apt install python3.9 python3.9-venv python3-pip nodejs npm poppler-utils
```

**Windows:**
- Python：从 [python.org](https://www.python.org/downloads/) 下载安装（3.9+）
- Node.js：从 [nodejs.org](https://nodejs.org/) 下载 LTS 版
- poppler：下载 [poppler for Windows](http://blog.alivate.com.au/poppler-windows/)，将 `bin/` 目录添加到系统 PATH

---

## 1. 获取代码

```bash
git clone <仓库地址> CadMark
cd CadMark
```

---

## 2. 后端安装

```bash
cd backend

# 创建虚拟环境
python3.9 -m venv venv

# 激活虚拟环境
source venv/bin/activate        # macOS / Linux
# 或
venv\Scripts\activate           # Windows

# 安装 Python 依赖
pip install -r requirements.txt

cd ..
```

---

## 3. 前端安装

```bash
cd frontend
npm install
cd ..
```

---

## 4. 配置数据目录

编辑 `config/app.dirconfig.json`，设置数据存储路径：

```json
{
  "data_dir": "~/CadMarkData"
}
```

> 路径支持 `~`（用户主目录）和相对路径。首次启动时，系统会自动创建该目录并初始化必要的文件。

---

## 5. 配置 LLM 模型

系统支持双模型（本地 + 云端）。首次启动后，在浏览器中打开系统设置页面进行配置：

```
http://localhost:5173/teacher/settings
```

填写内容：
- **模型 1**（云端，如阿里云百炼 DashScope）：API 地址、API Key、模型名称、并发数
- **模型 2**（本地，如 LM Studio）：API 地址（例如 `http://127.0.0.1:1234/v1`）、API Key（任意值即可）、并发数
- **激活模型**：选择使用哪个模型

或者直接编辑 `~/CadMarkData/settings.json`，参考模板 `config/settings.example.json`。

---

## 6. 启动运行

### 开发模式（推荐日常使用）

开两个终端：

**终端 1 — 后端（端口 8000）：**
```bash
cd backend
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

**终端 2 — 前端开发服务器（端口 5173）：**
```bash
cd frontend
npm run dev
```

然后访问 **http://localhost:5173** 即可。

> 前端开发服务器会自动将 `/api` 请求代理到后端 `localhost:8000`。

### 生产模式（单端口部署）

```bash
# 1. 构建前端
cd frontend
npm run build

# 2. 启动后端（自动托管前端静态文件）
cd ../backend
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000
```

访问 **http://localhost:8000**，前后端均由 FastAPI 托管。

---

## 7. 验证安装

1. 浏览器打开 `http://localhost:5173`（开发模式）或 `http://localhost:8000`（生产模式）
2. 应看到学生端页面，显示"暂无题目"（因为还没有创建题目）
3. 进入教师端：点击右上角进入 `/teacher`，使用默认密码 `MechCAD` 登录
4. 登录后即可创建题目、上传参考图、管理学生名单

---

## 常见问题

**Q: 启动后端报 `FileNotFoundError: 配置文件缺失: ...app.dirconfig.json`**
A: 确保 `config/app.dirconfig.json` 存在且 `data_dir` 路径有效。

**Q: PDF 上传后无法处理，日志报 `pdfinfo` / `pdftoppm` 找不到**
A: poppler 未安装或未加入 PATH。macOS 执行 `brew install poppler`，Linux 执行 `apt install poppler-utils`，Windows 需手动下载并配置 PATH。

**Q: 前端代理报 CORS 错误**
A: 确保后端已在 8000 端口启动，前端的 `vite.config.ts` 中已配置代理。

**Q: LLM 调用失败**
A: 检查系统设置中的 API 地址、Key 和模型名称是否正确，确认 LLM 服务是否在运行。
