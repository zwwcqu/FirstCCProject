# CHANGELOG

## 2026-05-30 ~ 2026-05-31 — 教师端系统设置与任务队列优化

### 模型配置增强（SettingsPage）

- **添加/删除模型** — 支持自由增删改大模型配置（可为 0 个），首模型自动激活，删当前模型自动切到第一个
- **API Key 掩码** — 默认只显示后 5 位（`••••••••f5c3`），聚焦或点「显示」查看完整，失焦自动掩码
- **查询当前模型** — 新增 `POST /api/teacher/settings/query-model` 端点，调 API 获取模型详情（ID/提供方/创建时间）、验证可用性
- **启用即保存** — 点「启用」自动保存到后端 settings.json，无需额外操作
- **原子写入** — settings.json 改为先写 `.tmp` 再 `os.replace` 原子替换，防进程中断损坏配置
- **并发数改后提示重启** — 当前模型并发数修改后保存时弹窗确认，自动重启服务

### 密码页独立（PasswordPage）

- 密码修改从 SettingsPage 拆分为独立页面 `/teacher/settings/password`
- 新增 `POST /api/teacher/settings/change-password` — 必须验证当前密码正确后才允许修改
- 两个页面 header 选项卡导航：[模型配置] [修改密码]

### 任务队列增强（task_queue）

- **任务去重** — `enqueue()` 新增 `task_key` 参数，相同 key 不会重复入队
  - 学生分析 `analyze:{qid}:{sid}` / 学生评分 `grade:{qid}:{sid}`
  - 教师参考图分析 `ref_analyze:{qid}` / 批量评分 `batch_grade:{qid}`
- **队列状态查询** — `GET /api/teacher/settings/queue-status` 返回活跃任务列表
- **执行中/排队区分** — `_status: "queued" | "running"`，UI 绿点闪烁 vs 灰点
- **清空队列** — `POST /api/teacher/settings/queue-clear`，仅清除排队任务，不影响执行中的
- 系统管理区域每 5 秒自动刷新队列状态

### 查看作业弹窗（TeacherDashboard）

- **参考图分析 → 弹窗** — 分析结果从题目列表下方内联展开改为居中弹窗，浮动图 zIndex 升至 75 避免被遮挡
- **学生图面分析** — 查看作业弹窗新增学生工程图的结构分析 + 量化分析（在评分内容上方），新增 `GET /api/teacher/student-analysis/{qid}/{sid}` 端点
- **打印功能** — 参考图分析弹窗、查看作业弹窗均新增「打印」按钮，自动展开折叠详情，使用克隆+body 直挂策略支持完整多页打印

### 文件改动

| 后端 | 前端 |
|------|------|
| `auth.py` — Session 30min，学生 session，自动迁移 | `App.tsx` — 新增 `/teacher/settings/password` 路由 |
| `config.py` — 原子写入 + .tmp 清理 | `api.ts` — queryCurrentModel / changePassword / getQueueStatus / clearQueue / getStudentAnalysis |
| `routers/student.py` — enqueue 传入 task_key + task_info | `SettingsPage.tsx` — 增删模型、Key 掩码、队列状态、启用即保存、选项卡导航 |
| `routers/teacher.py` — query-model / change-password / queue-status / queue-clear / student-analysis 5 个新端点 | `PasswordPage.tsx` — **新建**，双密码验证 |
| `services/task_queue.py` — 去重 / 状态追踪 / 队列清空 / 执行排队区分 | `TeacherDashboard.tsx` — 分析弹窗、学生分析、打印、层级修复 |


## 2026-05-27（二）— 评分流水线重构：预分析 + 分步评分 + UI 改造

### 架构变更：从一次调用到四步流水线

旧架构：学生提交 → LLM 一次调用（评分 prompt + 参考图 + 学生图） → 出分

新架构：
```
教师上传参考图 → LLM 结构分析 → LLM 量化分析 → 缓存 JSON
学生上传作业   → LLM 结构分析 → LLM 量化分析 → 展示结果
                                               ↓
                                    学生确认 → 阶段一（结构JSON+图片）→ 阶段二（量化JSON 纯文本）→ 总分
```

### llm_service.py — 新增 6 个函数

- `analyze_structure(image_path, template)` — 图片 + 结构模板 → LLM → 结构特征 JSON（title_block、views、features 等）
- `analyze_quantitative(image_path, template, struct_json)` — 替换 `__STRUCTURE_JSON__` 占位符，图片 + 填充模板 → 量化 JSON（dimensions、公差、粗糙度、螺纹等）
- `grade_phase1(ref_struct, stu_struct, criteria, ref_img, stu_img)` — 结构 JSON + 图片对比 → 相似度评分（视觉调用）
- `grade_phase2(ref_quant, stu_quant, criteria)` — 量化 JSON 纯文本对比 → 五维评分（无图片）
- `run_two_phase_grading(...)` — 串联阶段一+阶段二 → 计算总分（P1×P2/100）+ 九档等级
- `_parse_json_response(text)` — 通用 JSON 解析器，容错 markdown 代码块和尾随逗号

### question_service.py — 分析结果存取

- `save_reference_analysis(qid, analysis)` / `get_reference_analysis(qid)` — 参考图分析缓存（`参考图_结构分析.json` + `_量化分析.json`）
- `save_student_analysis(qid, sid, name, analysis)` / `get_student_analysis(qid, sid, name)` — 学生图分析缓存

### 教师端 API

- `POST /api/teacher/questions/{qid}/analyze` — 手动触发参考图重分析（含"重分析"按钮）
- `GET /api/teacher/questions/{qid}/analysis` — 查询分析结果（`{ready, analysis}`）
- 创建/更新题目时上传参考图 → 后台线程自动触发分析（不阻塞 HTTP 响应）

### 学生端 API

- `POST /api/student/analyze/{qid}` — 上传文件 + 结构分析 + 量化分析 → 返回分析结果
- `POST /api/student/grade/{qid}` — 读取缓存的分析 JSON → 两阶段评分 → 写入成绩
- `POST /api/student/submit/{qid}` 保留兼容（旧版一次性调用）

### 配置文件

- `config/结构分析模版.txt` — 参考图结构分析 Prompt（含 title_block、20+ 特征类型）
- `config/量化分析模版.txt` — 参考图量化分析 Prompt（`__STRUCTURE_JSON__` 占位符，4 种公差格式）
- `config/结构分析_学生.txt` — 学生版结构分析（强调如实提取，无则留空）
- `config/量化分析_学生.txt` — 学生版量化分析（含 `completeness_notes`，要求指出问题）
- `config/二阶段修正提示词.txt` — 阶段二匹配规则：按尺寸数值/描述匹配而非 ID

### 前端 — 教师端

- 题目列表新增"参考图分析"列：分析中/查看+重分析/分析
- 分析结果用结构化表格展示（标题栏、形状、视图、特征、尺寸、公差、粗糙度、螺纹），顶部展示参考工程图
- 上传参考图后自动轮询（3 秒间隔），分析完成自动显示
- 页面加载时自动拉取已有分析结果

### 前端 — 学生端

- 提交流程改为两步：① 上传并分析（显示进度）→ ② 提交评分
- 分析完成后展示：参考图 vs 学生图左右对比 + 结构化分析表格（与教师端一致）
- 评分结果不再重复显示图形对比（分析区已展示）
- 测试模式修复：分析/评分共用同一 `submitKey`，避免 `Date.now()` 不一致

### 修复

- 测试模式 `save_student_analysis` 跳过问题（`if not is_test` 改为始终保存）

## 2026-05-27（一）

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
