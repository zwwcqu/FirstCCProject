# 工程图批阅系统 — 部署指引

## 环境要求

- Ubuntu 20.04+（或其他 Linux 发行版）
- Python 3.9+
- Node.js 18+
- poppler-utils（PDF 处理依赖）

## 安装步骤

### 1. 安装系统依赖

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv nodejs npm poppler-utils
```

### 2. 克隆项目

```bash
git clone <仓库地址>
cd FirstCCProject
```

### 3. 配置后端

```bash
# 创建 Python 虚拟环境（位于 backend/venv/，已在 .gitignore 中，不会上传）
cd backend
python3 -m venv venv

# 安装 Python 依赖
./venv/bin/pip install -r requirements.txt

# 配置数据目录（缺省 ~/CadMarkData，可修改）
cp app_config.example.json app_config.json
# 如需修改数据目录，编辑 app_config.json 中的 data_dir 字段
cd ..
```

### 4. 构建前端

```bash
cd frontend
npm install           # 安装依赖到 frontend/node_modules/（已在 .gitignore 中）
npm run build         # 构建产物输出到 frontend/dist/
cd ..
```

### 5. 启动服务

```bash
cd backend
./venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

启动后访问 `http://<服务器IP>:8000`。

## 目录结构（部署后）

```
~/FirstCCProject/          # 项目根目录
  backend/
    venv/                  # Python 虚拟环境（本地创建，不入库）
    app_config.json        # 数据目录配置（本地创建，不入库）
    main.py                # FastAPI 入口
    ...
  frontend/
    node_modules/          # npm 依赖（本地安装，不入库）
    dist/                  # 前端构建产物
    ...
~/CadMarkData/             # 业务数据（缺省位置，不入库）
  settings.json            # 教师密码、LLM 配置
  questions.json           # 题目列表
  StudentInfo/             # 学生名单
  {题号}/                   # 各题目目录
```

## 首次使用

1. 浏览器访问 `http://<服务器IP>:8000`
2. 进入教师端 `/teacher`，用默认密码 `MechCAD` 登录
3. 进入设置页，配置 LLM（API 地址、密钥、模型）
4. 添加题目和学生名单后即可使用

## 常用操作

### 后台运行（推荐生产环境）

```bash
cd ~/FirstCCProject/backend
nohup ./venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000 > /tmp/cadmark.log 2>&1 &
```

### 配置 Nginx 反向代理

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 修改数据目录

编辑 `backend/app_config.json`，修改 `data_dir` 后重启服务：

```json
{
  "data_dir": "/data/cadmark"
}
```

### 更新部署

```bash
cd ~/FirstCCProject
git pull
cd backend && ./venv/bin/pip install -r requirements.txt
cd ../frontend && npm install && npm run build
# 重启服务
```

## 注意事项

- `app_config.json` 和 `~/CadMarkData/` 不会提交到 Git，部署时需手动创建/配置
- `venv/` 和 `node_modules/` 是平台相关的，换系统必须重建
- PDF 批阅功能依赖 `poppler-utils`，缺少时提交 PDF 会报错
- LLM 服务地址需要在教师后台手动配置，建议放内网
- 生产环境建议搭配 Nginx 反向代理，不要直接暴露 uvicorn 到公网
