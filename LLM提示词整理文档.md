# 工程图批阅系统 — LLM 提示词（Prompt）整理文档

## 一、整体流程概览

```
学生提交工程图
    │
    ▼
┌─────────────────────────────────────────────────────┐
│ 步骤 1: 合并分析 (analyze_merged)                      │
│   单次 LLM 调用 = 结构分析 + 量化分析                   │
│   输入: 工程图图片 + 合并 Prompt                       │
│   输出: { structure: {...}, quantitative: {...} }     │
└─────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────┐
│ 步骤 2: 阶段一评分 (grade_phase1)                      │
│   视觉对比：学生图 vs 参考图的结构特征                   │
│   输入: 结构分析 JSON ×2 + 缩略图(768px) ×2            │
│   输出: { phase1_similarity, phase1_comment }        │
└─────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────┐
│ 步骤 3: 阶段二评分 (grade_phase2)                      │
│   纯文本对比：学生量化数据 vs 参考量化数据               │
│   输入: 精简后的量化 JSON ×2（不含图片）                │
│   输出: { phase2_criteria, 各维度评语 }               │
└─────────────────────────────────────────────────────┘
    │
    ▼
  总分 = √(phase1_similarity × phase2_criteria)
  等级: A+ ≥ 90 → F < 50（9 档）
```

**所有 Prompt 的位置**：
| 层级 | 位置 | 说明 |
|------|------|------|
| 文件模板 | `config/*.txt`, `config/*.md` | 出厂默认，从文件读取 |
| Settings 模板 | `config/settings.example.json` → `prompt_templates` | 教师可在设置页面覆盖 |
| 代码组装 | `backend/services/llm_service.py` | 运行时将模板填充变量并拼接 |
| 评分标准 | 每道题目录下的 `.md` 文件 | 教师为每道题单独编写 |

**覆盖优先级**：`settings.json 中的 prompt_templates` > `config/ 目录下的模板文件`

---

## 二、步骤 1：合并分析（结构分析 + 量化分析）

### 2.1 参考图 — 结构分析模板

- **文件**: `config/结构分析模版.txt`
- **Settings key**: `prompt_templates.structure_analysis`
- **角色**: 机械制图专家
- **输入**: 参考工程图图片
- **输出 JSON 字段**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `title_block` | object | 标题栏：`part_name`, `drawing_number`, `material`, `scale` |
| `views[]` | array | 视图列表：`name`(主视图/俯视图/…), `type`(front/top/section_full/…), `description` |
| `features[]` | array | 结构特征：`id`(F01..), `type`(通孔/盲孔/键槽/倒角/…), `count`, `location`, `notes` |
| `overall_shape` | object | 整体形状：`type`(轴类/盘类/箱体/…), `has_material_label`, `material_text`, `symmetry`, `approx_dimensions` |
| `technical_notes` | string | 技术要求原文提取 |

**特征类型枚举**: 通孔、盲孔、沉头孔、螺纹孔、销孔、光孔、槽、键槽、凸台、加强筋、倒角、圆角、退刀槽、越程槽、齿轮、花键、螺纹、锥面、扁位、其他

### 2.2 学生图 — 结构分析模板

- **文件**: `config/结构分析_学生.txt`
- **Settings key**: `prompt_templates.structure_analysis_student`
- **角色**: 机械制图专家
- **与参考图的差异**:
  - 强调"学生作业可能不完整、有标注错误或特征遗漏，请如实分析"
  - 标题栏字段允许为 `null`（未标注时）
  - `views[].description` 增加"如该视图表达不规范，请指出问题"
  - 不要凭空编造数据
- **输出格式**: 与参考图结构分析完全一致

### 2.3 参考图 — 量化分析模板

- **文件**: `config/量化分析模版.txt`
- **Settings key**: `prompt_templates.quantitative_analysis`
- **角色**: 机械检测工程师
- **输入**: 上一轮结构分析的 JSON（通过 `__STRUCTURE_JSON__` 占位符注入）+ 原图
- **关键要求**:
  1. 数值精度保持原图格式（72 → 整数72, 72.0 → 72.0, 72.00 → 72.00）
  2. 公差识别规则：H7/h6 等 → `tolerance: "H7"`；+0.018/+0.008 → `tolerance: "+0.018/+0.008"`；±0.02 → `tolerance: "±0.02"`；无标注 → `"未注公差"`
  3. `feature_ref` 必须引用结构分析中的 feature id
  4. 同一尺寸不重复列入多个数组
- **输出 JSON 字段**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `dimensions[]` | array | 尺寸标注：`id`, `type`(外径/内径/长度/…), `value`, `unit`, `tolerance`, `feature_ref`, `description` |
| `surface_roughness[]` | array | 表面粗糙度：`feature_ref`, `value`(Ra 3.2格式), `location` |
| `geometric_tolerances[]` | array | 形位公差：`id`, `type`(14种), `value`, `unit`, `ref_feature`, `datum`(基准), `description` |
| `thread_specs[]` | array | 螺纹规格：`id`, `type`(公制粗牙/细牙/梯形/管螺纹/锥管), `spec`, `feature_ref`, `notes` |
| `general_notes` | object | 通用说明：`scale`, `general_tolerance`, `heat_treatment`, `surface_treatment`, `unspecified_rounds`, `unspecified_chamfers` |
| `技术要求` | string | 技术要求原文逐条罗列 |

**形位公差类型枚举**: 直线度、平面度、圆度、圆柱度、线轮廓度、面轮廓度、平行度、垂直度、倾斜度、同轴度、对称度、位置度、圆跳动、全跳动

### 2.4 学生图 — 量化分析模板

- **文件**: `config/量化分析_学生.txt`
- **Settings key**: `prompt_templates.quantitative_analysis_student`
- **角色**: 机械检测工程师
- **与参考图的差异**:
  - "学生图中缺失的项目，对应数组保持为空 []。不要凭空编造数据"
  - 仅提取图中实际标注的内容
  - `completeness_notes` 字段：总结图中明显缺失或异常之处
  - `description` 和 `notes` 中可指出可疑之处
  - `技术要求` 字段说明"学生可能漏写或写错，如实提取"
- **输出格式**: 与参考图量化分析一致，额外增加 `completeness_notes` 字段

### 2.5 合并分析的 Prompt 组装逻辑

代码位置: `backend/services/llm_service.py` → `_build_merged_prompt()`

```
你是机械制图与检测专家。请一次性完成以下两项任务：

## 任务一：结构分析
[结构分析模板全文]

## 任务二：量化分析（基于你在任务一中输出的结构特征）
[量化分析模板全文，__STRUCTURE_JSON__ 替换为："请直接使用你在任务一中输出的结构分析 JSON 作为本任务的输入依据"]

请将两项结果合并为一个 JSON，不要用 markdown 代码块包裹：
{
  "structure": { ... 任务一的完整 JSON 输出 ... },
  "quantitative": { ... 任务二的完整 JSON 输出 ... }
}
```

如果题目有补充知识（`补充知识.md`），会在 Prompt 最前面加上：
```
【补充知识】
{knowledge 内容}
```

---

## 三、步骤 2：阶段一评分 — 图形相似度

### 3.1 阶段一引导语

- **Settings key**: `prompt_templates.phase1_guide`
- **默认值**: `"你是一位工程图批阅老师。请对比学生图和参考图的结构特征，评估图形相似度和画图质量。"`

### 3.2 阶段一 Prompt 组装

代码位置: `grade_phase1()`

```
{phase1_guide}

【参考工程图结构分析】
{ref_struct JSON}

【学生工程图结构分析】
{stu_struct JSON}

【评分标准】
{phase1_criteria}  ← 来自题目的 阶段1评分标准.md

请严格按以下 JSON 格式输出：
{
  "phase1_similarity": 85,
  "phase1_comment": "与参考图相比的相似度评价，指出学生图在结构完整性和画图规范性方面的表现"
}
```

**图片输入**（多模态消息）：
- `【参考工程图】`: 参考图缩略图（默认 768px 长边，JPEG 质量 55）
- `【学生提交的工程图】`: 学生图缩略图（同上参数）

### 3.3 评分标准默认值

- **Settings key**: `scoring_templates.phase1`
- **文件默认**: `config/评分模版1.md`
- **默认内容**:
  > 较为宽松比较相似情况.
  > 和参考图形整体比较, 很相似给100%, 一般相似给90%, 不怎么相似给80%, 有点点相似给60%, 绝不相似给0%.

---

## 四、步骤 3：阶段二评分 — 量化标注对比

### 4.1 阶段二修正提示词

- **Settings key**: `prompt_templates.phase2_correction_hint`
- **文件回退**: `config/二阶段修正提示词.txt`
- **出厂默认内容**:
  > 【重要提示】
  >
  > 分5项比较,以参考为基准,计算学生图匹配百分比.
  > 1. 尺寸相似率: (匹配的尺寸数量)/(参考尺寸数量)
  > 2. 尺寸公差相似率:(匹配尺寸公差数量)/(参考图尺寸公差数量)
  > 3. 粗糙度相似率:(匹配的粗糙度数量/(参考图粗糙度数量)
  > 4. 形位公差相似率:(匹配的形位公差数量/(参考图形位公差数量)
  > 5. 技术要求 大致计算文本意思相似度.
  > 具体占分比例,后面给出.

### 4.2 阶段二引导语

- **Settings key**: `prompt_templates.phase2_guide`
- **默认值**: `"你是一位机械检测工程师。请逐项对比两份量化分析数据，评估学生标注的完整性和正确性。"`

### 4.3 量化数据精简

阶段二不需要图片，但为了减少 token 消耗，`_simplify_quantitative()` 会先精简量化 JSON：

| 原字段 | 精简后保留 |
|--------|-----------|
| `dimensions[]` | `尺寸数量` + 每条保留 `{数值, 公差}` |
| `surface_roughness[]` | `粗糙度数量` + 每条保留 `{数值}` |
| `geometric_tolerances[]` | `形位公差项数` + 每条保留 `{类型, 数值}` |
| `技术要求` | 保留原文 |
| `thread_specs[]` | 保留 |

### 4.4 阶段二 Prompt 组装

代码位置: `grade_phase2()`

```
{phase2_correction_hint}

{phase2_guide}

【参考图量化数据】
{ref_simplified JSON}

【学生图量化数据】
{stu_simplified JSON}

【评分标准】
{phase2_criteria}  ← 来自题目的 阶段2评分标准.md

请严格按以下 JSON 格式输出：
{
  "phase2_criteria": 85,
  "图样表达": "评价图样表达是否清晰规范",
  "尺寸标注": "评价尺寸标注是否齐全、正确",
  "尺寸公差": "评价公差标注是否规范",
  "表面质量": "评价粗糙度等表面质量标注",
  "形位公差": "评价形位公差标注情况",
  "技术要求": "评价技术要求文本的完整性和相似度",
  "phase2_comment": "按批改要求的综合评价",
  "总评": "综合两阶段的整体评价"
}
```

### 4.5 评分标准默认值

- **Settings key**: `scoring_templates.phase2`
- **文件默认**: `config/评分模版2.md`
- **默认内容**:
  > 1. 尺寸相似率占总分40%
  > 2. 尺寸公差相似率占总分20%
  > 3. 粗糙度相似率占总分20%
  > 4. 形位公差相似率占总分10%
  > 5. 技术要求相似度占总分10%

---

## 五、评分计算

### 5.1 总分公式

```
总分 = √(phase1_similarity × phase2_criteria)
```

### 5.2 等级映射（9 档）

| 等级 | 最低分 | 等级 | 最低分 |
|------|--------|------|--------|
| A+ | 90 | C+ | 68.75 |
| A | 85 | C | 62.5 |
| B+ | 80 | D+ | 56.25 |
| B | 75 | D | 50 |
| | | F | < 50 |

阈值可在 Settings → 等级阈值 页面调节。

---

## 六、所有 Prompt 模板的配置汇总

### 6.1 `prompt_templates`（settings.json）

| Key | 用途 | 默认来源 | 使用阶段 |
|-----|------|----------|----------|
| `structure_analysis` | 参考图结构分析 | `config/结构分析模版.txt` | 步骤1 合并分析 |
| `structure_analysis_student` | 学生图结构分析 | `config/结构分析_学生.txt` | 步骤1 合并分析 |
| `quantitative_analysis` | 参考图量化分析 | `config/量化分析模版.txt` | 步骤1 合并分析 |
| `quantitative_analysis_student` | 学生图量化分析 | `config/量化分析_学生.txt` | 步骤1 合并分析 |
| `phase1_guide` | 阶段一评分引导语 | 代码内置默认 | 步骤2 阶段一 |
| `phase2_guide` | 阶段二评分引导语 | 代码内置默认 | 步骤3 阶段二 |
| `phase2_correction_hint` | 阶段二修正提示词 | `config/二阶段修正提示词.txt` | 步骤3 阶段二 |

### 6.2 `scoring_templates`（settings.json）

| Key | 用途 | 默认来源 |
|-----|------|----------|
| `phase1` | 新建题目时预填的阶段一评分标准 | `config/评分模版1.md` |
| `phase2` | 新建题目时预填的阶段二评分标准 | `config/评分模版2.md` |

### 6.3 每道题独立的评分标准文件

| 文件 | 用途 | 使用阶段 |
|------|------|----------|
| `{qid}/阶段1评分标准.md` | 该题的阶段一评分标准 | 步骤2 |
| `{qid}/阶段2评分标准.md` | 该题的阶段二评分标准 | 步骤3 |
| `{qid}/补充知识.md` | 给 LLM 的补充知识（可选） | 步骤1-3 |

---

## 七、前端管理界面

设置页面 (`SettingsPage.tsx`) 中两个标签页管理提示词：

### 7.1 "工程图分析模板" 标签页

可编辑 4 个分析模板：
- `structure_analysis` — 结构分析（参考图）
- `structure_analysis_student` — 结构分析（学生图）
- `quantitative_analysis` — 量化分析（参考图）
- `quantitative_analysis_student` — 量化分析（学生图）

### 7.2 "评分模板" 标签页

可编辑 5 个模板：
- `phase1_guide` — 阶段一评分引导语
- `phase2_guide` — 阶段二评分引导语
- `phase2_correction_hint` — 阶段二修正提示词
- `phase1`（scoring_templates）— 阶段一默认评分标准
- `phase2`（scoring_templates）— 阶段二默认评分标准

---

## 八、图像处理参数（影响 Prompt 中间接效果）

| 参数 | 默认值 | 影响 |
|------|--------|------|
| `analysis_max_size` | 3508px | 结构/量化分析用图长边最大像素 |
| `analysis_dpi` | 150 | PDF 渲染分辨率 |
| `analysis_jpeg_quality` | 85 | 分析用图 JPEG 质量 |
| `phase1_max_size` | 768px | 阶段一缩略图长边最大像素 |
| `phase1_jpeg_quality` | 55 | 阶段一缩略图 JPEG 质量 |

## 九、LLM 调用参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `temperature` | 0.1 | 生成随机性，越低越确定 |
| `max_tokens` | 4096 | 单次调用最大输出 token |
| `enable_thinking` | false | 是否启用思考模式 |
| `client_timeout` | 120s | API 请求超时 |

---

## 十、关键设计决策

1. **合并分析 vs 分步分析**：系统采用合并分析（单次 LLM 调用完成结构+量化），减少调用次数和延迟。

2. **阶段一用缩略图**：阶段一只需要判断整体结构相似度，使用 768px 缩略图 + 低 JPEG 质量（55），大幅降低 token 消耗。

3. **阶段二不传图片**：阶段二仅对比量化数据（尺寸、公差、粗糙度等），是纯文本对比，不需要图片。量化数据在发送前经过 `_simplify_quantitative()` 精简。

4. **补充知识注入**：题目的 `补充知识.md` 会在所有分析步骤（结构、量化、阶段一、阶段二）的最前面注入，帮助 LLM 理解特定题目的专业知识。

5. **模板覆盖优先级**：settings.json → config/ 文件。教师既可以通过前端 UI 修改模板（推荐），也可以直接替换 config/ 下的文件。

6. **JSON 输出容错**：所有 LLM 输出都有多层 JSON 解析容错：
   - 去除 markdown 代码块包裹
   - 修复尾随逗号
   - 修复缺失逗号
   - 回退到 json5 宽松解析
   - JSON 解析失败自动重试一次
