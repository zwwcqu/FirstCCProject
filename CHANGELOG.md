# CHANGELOG

## 2026-05-27

### 安全加固

- **密码哈希存储** — 教师密码改为 PBKDF2-SHA256（10 万次迭代 + 随机 salt）哈希存储，首次验证明文密码时自动迁移，校验使用 `secrets.compare_digest` 常量时间比较防时序攻击
- **HttpOnly Cookie** — Session Cookie 改为后端 `Response.set_cookie()` 设置（`HttpOnly=True, SameSite=Lax`），前端不再通过 JS 写入 cookie，防止 XSS 泄露
- **提交频率限制** — 学生提交接口 `/api/student/submit/` 添加 IP 级滑动窗口限流（每分钟最多 10 次），超限返回 429
- **文件名安全清洗** — `_sanitize_filename_part()` 剔除路径分隔符和控制字符，防止非法字符导致文件写入失败或路径穿越

### 数据安全

- **CSV 文件锁** — `save_grade()` 使用 `fcntl.LOCK_EX` 排他锁保护读-改-写全过程，`read_all_grades()` 使用 `fcntl.LOCK_SH` 共享锁保证读到一致视图，消除并发写入丢数据风险

### 可观测性

- **结构化日志** — 全局配置日志格式 `时间 [级别] 模块: 消息`，所有模块使用 `logging.getLogger(__name__)`，覆盖登录、登出、题目 CRUD、LLM 批阅、成绩保存等关键操作

### 代码质量

- **全文件注释** — 8 个后端 Python 文件添加文件头注释（功能说明、数据布局）、函数 docstring、关键变量注释
- **公开接口规范化** — `_image_to_base64` 重命名为 `image_to_base64`，router 不再直接引用私有函数
- **成绩表格列顺序固定** — 后端 `/api/teacher/grades/` 返回 `columns` 字段（15 列固定顺序），前端不再依赖 `Object.keys()` 的不稳定顺序
- **教师设置页密码修改修复** — 密码修改走 `change_password()` 哈希流程，不再直接写明文
- **启动初始化延后** — `_init_data_dir()` 从 config.py 模块级调用改为 FastAPI lifespan 启动事件

### 前端

- **输入校验** — 学生提交页添加校验：姓名必填 ≤50 字符、学号必填 ≤30 字符、文件必选 ≤20MB

### Session

- **多 worker 文件持久化** — Session 存储改为内存优先 + 文件后备（`DATA_DIR/.sessions/`），支持多 uvicorn worker 共享 session，过期文件每 10 分钟自动清理
