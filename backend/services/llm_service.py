"""
LLM 批阅服务。

功能：
- 对接 OpenAI 兼容 API（LM Studio / 云端模型）
- 自动检测模型（未配置时从 LM Studio 获取）
- PDF/图片转 Base64（PDF 通过 pdf2image + poppler 渲染为 JPEG）
- 构建两阶段评分 Prompt（相似度 → 批改要求 → 总分）
- 解析 LLM 返回的 JSON，计算最终等级（A+ ~ F 共 9 档）

评分公式：
  总分 = 阶段1分数 × 阶段2分数 / 100
  等级阈值：A+≥93.75, A≥87.5, B+≥81.25, B≥75, C+≥68.75,
            C≥62.5, D+≥56.25, D≥50, F<50
"""

from __future__ import annotations

import base64
import json
import logging
import re
from io import BytesIO
from pathlib import Path

from openai import OpenAI
from PIL import Image

from config import read_settings

logger = logging.getLogger(__name__)

# ── 模型相关 ─────────────────────────────────────────────
_cached_model: str | None = None         # 用户明确指定后的模型缓存


def _build_client() -> OpenAI:
    """按当前 settings 创建 OpenAI 客户端"""
    settings = read_settings()
    return OpenAI(
        base_url=settings["llm_api_base"],
        api_key=settings["llm_api_key"],
        timeout=120,
    )


def _get_model() -> str:
    """获取模型名称。已配置则直接用，否则从 LM Studio 自动检测"""
    global _cached_model
    settings = read_settings()
    configured = settings.get("llm_model", "").strip()
    if configured:
        _cached_model = configured
        return _cached_model

    # LM Studio 自动检测（每次查询，不缓存——远端可能自动切换模型）
    client = _build_client()
    models = client.models.list()
    if models.data:
        logger.info(f"自动检测到模型: {models.data[0].id}")
        return models.data[0].id
    if _cached_model:
        return _cached_model
    raise RuntimeError("无法获取模型列表，请检查大模型服务是否正常运行")


# ── 图像处理 ─────────────────────────────────────────────
MAX_IMAGE_SIZE = 1600                     # 长边最大像素数，超过则等比缩放


def _resize_image(img: Image.Image) -> Image.Image:
    """若图像长边超过 MAX_IMAGE_SIZE，等比缩放"""
    w, h = img.size
    longest = max(w, h)
    if longest <= MAX_IMAGE_SIZE:
        return img
    ratio = MAX_IMAGE_SIZE / longest
    new_w, new_h = int(w * ratio), int(h * ratio)
    return img.resize((new_w, new_h), Image.LANCZOS)


def image_to_base64(path: Path) -> str:
    """将 PDF 或图片文件转为 JPEG 的 Base64 字符串。PDF 取首页渲染"""
    if path.suffix.lower() == ".pdf":
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(str(path), first_page=1, last_page=1, dpi=150)
            img = _resize_image(images[0])
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode()
        except ImportError:
            raise RuntimeError("pdf2image 未安装，无法处理 PDF 工程图。请安装 poppler 和 pdf2image。")
    elif path.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        img = Image.open(path)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")        # RGBA/调色板转为 RGB，避免 JPEG 保存报错
        img = _resize_image(img)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode()
    else:
        raise ValueError(f"不支持的文件格式: {path.suffix}")


# ── Prompt 构建 ──────────────────────────────────────────

def _build_prompt(description: str, phase1_criteria: str, phase2_criteria: str) -> str:
    """组装两阶段评分 Prompt 文本"""
    return f"""你是一位工程图批阅老师。请根据以下信息，分两阶段批阅学生提交的工程图。

【题目】{description}

## 评分流程（两阶段）

### 第一阶段：图形相似度评分
{phase1_criteria}

### 第二阶段：按批改要求评分
{phase2_criteria}

### 第三阶段：计算总分
总分 = 第一阶段分数 × 第二阶段分数
根据总分映射到等级（100~50 线性分布为 A+~D 八档，50以下为F）：
A+≥93.75, A≥87.5, B+≥81.25, B≥75, C+≥68.75, C≥62.5, D+≥56.25, D≥50, F<50

请严格按以下 JSON 格式输出，不要包含其他文字：
{{
  "phase1_similarity": 85,
  "phase1_comment": "与参考图相比的相似度评价",
  "phase2_criteria": 80,
  "图样表达": "评价...",
  "尺寸标注": "评价...",
  "尺寸公差": "评价...",
  "表面质量": "评价...",
  "形位公差": "评价...",
  "phase2_comment": "按批改要求的综合评价",
  "总评": "综合两阶段的整体评价"
}}"""


# ── LLM 输出解析 ────────────────────────────────────────

def _parse_json_response(text: str) -> dict:
    """从 LLM 原始文本中提取 JSON 对象（通用版），容错尾随逗号和 markdown 代码块"""
    text = text.strip()
    # 去掉可能的 markdown 代码块包裹
    if text.startswith("```"):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
    # 尝试匹配最外层的 { ... }
    match = re.search(r'\{[\s\S]*\}', text, re.DOTALL)
    if match:
        text = match.group(0)
    # 修复 JSON 尾随逗号（LLM 常见错误）
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*]', ']', text)
    return json.loads(text)


def _parse_llm_response(text: str) -> dict:
    """从 LLM 原始文本中提取含 phase1_similarity 的 JSON 对象，容错尾随逗号"""
    text = text.strip()
    match = re.search(r'\{[\s\S]*"phase1_similarity"[\s\S]*\}', text, re.DOTALL)
    if match:
        text = match.group(0)
    # 修复 JSON 尾随逗号（LLM 常见错误）
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*]', ']', text)
    return json.loads(text)


# ── 等级计算 ─────────────────────────────────────────────

# 分数阈值 → 等级（从高到低排列，匹配第一个命中的）
GRADE_THRESHOLDS = [
    (93.75, "A+"),
    (87.5, "A"),
    (81.25, "B+"),
    (75, "B"),
    (68.75, "C+"),
    (62.5, "C"),
    (56.25, "D+"),
    (50, "D"),
]


def _compute_grade(total: float) -> str:
    """按总分映射到九档等级，低于 50 为 F"""
    for threshold, grade in GRADE_THRESHOLDS:
        if total >= threshold:
            return grade
    return "F"


# ── 核心批阅流程 ────────────────────────────────────────

def grade_submission(
    description: str,
    phase1_criteria: str,
    phase2_criteria: str,
    reference_paths: list[Path],
    student_submission_path: Path,
) -> dict:
    """
    两阶段批阅主流程。
    返回 dict 包含 phase1_similarity, phase2_criteria, total_score, grade 及各维度评语。
    """
    client = _build_client()
    model = _get_model()
    prompt_text = _build_prompt(description, phase1_criteria, phase2_criteria)

    # 组装多模态消息：[文本 Prompt] + [参考图（可选）] + [学生作业]
    content: list[dict] = [{"type": "text", "text": prompt_text}]

    if reference_paths:
        content.append({"type": "text", "text": "\n【参考工程图】："})
        for ref_path in reference_paths:
            if ref_path.exists():
                b64 = image_to_base64(ref_path)
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                })

    student_b64 = image_to_base64(student_submission_path)
    content.append({"type": "text", "text": "\n【学生提交的工程图】："})
    content.append({
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{student_b64}"},
    })

    logger.info(f"正在调用 LLM 批阅（模型: {model}）…")
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        temperature=0.1,
        max_tokens=4096,
    )

    raw_text = response.choices[0].message.content or ""
    result = _parse_llm_response(raw_text)

    # 计算总分和等级
    p1 = float(result.get("phase1_similarity", 0))
    p2 = float(result.get("phase2_criteria", 0))
    total = round(p1 * p2 / 100, 1)
    grade = _compute_grade(total)

    result["grade"] = grade
    result["phase1_similarity"] = p1
    result["phase2_criteria"] = p2
    result["total_score"] = total

    logger.info(f"LLM 批阅完成 → 总分 {total}% 等级 {grade}")
    return result


# ── 工程图预分析（参考图 / 学生图）────────────────────────

def analyze_structure(image_path: Path, template_path: Path) -> dict:
    """
    对工程图进行结构分析。
    发送图片 + 结构分析模版 → LLM → 返回结构特征 JSON。
    返回 dict 包含 title_block, views, features, overall_shape, technical_notes 等。
    """
    client = _build_client()
    model = _get_model()
    prompt_text = template_path.read_text(encoding="utf-8")
    b64 = image_to_base64(image_path)

    content: list[dict] = [
        {"type": "text", "text": prompt_text},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]

    logger.info(f"结构分析中（模型: {model}）…")
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        temperature=0.1,
        max_tokens=4096,
    )
    raw_text = response.choices[0].message.content or ""
    result = _parse_json_response(raw_text)
    logger.info("结构分析完成")
    return result


def analyze_quantitative(image_path: Path, template_path: Path, structure_json: dict) -> dict:
    """
    对工程图进行量化分析（依赖结构分析结果）。
    将 __STRUCTURE_JSON__ 替换为实际结构 JSON → 发送图片 + 填充模版 → LLM → 返回量化 JSON。
    返回 dict 包含 dimensions, surface_roughness, geometric_tolerances, thread_specs 等。
    """
    client = _build_client()
    model = _get_model()
    template_text = template_path.read_text(encoding="utf-8")
    # 替换占位符为上一轮结构分析的实际 JSON
    prompt_text = template_text.replace("__STRUCTURE_JSON__", json.dumps(structure_json, ensure_ascii=False, indent=2))
    b64 = image_to_base64(image_path)

    content: list[dict] = [
        {"type": "text", "text": prompt_text},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]

    logger.info(f"量化分析中（模型: {model}）…")
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        temperature=0.1,
        max_tokens=4096,
    )
    raw_text = response.choices[0].message.content or ""
    result = _parse_json_response(raw_text)
    logger.info("量化分析完成")
    return result


# ── 两阶段评分（新版：基于分析结果）───────────────────────

def grade_phase1(
    ref_struct: dict,
    stu_struct: dict,
    phase1_criteria: str,
    ref_image_path: Path,
    stu_image_path: Path,
) -> dict:
    """
    阶段一：结构相似度评分（视觉对比）。
    Prompt = 主观对比指令 + 两份结构 JSON + 老师阶段一评分标准
    Content = Prompt 文本 + 参考图 + 学生图
    返回 {"phase1_similarity": int, "phase1_comment": str}
    """
    client = _build_client()
    model = _get_model()

    prompt_text = f"""你是一位工程图批阅老师。请对比学生图和参考图的结构特征，评估图形相似度和画图质量。

【参考工程图结构分析】
{json.dumps(ref_struct, ensure_ascii=False, indent=2)}

【学生工程图结构分析】
{json.dumps(stu_struct, ensure_ascii=False, indent=2)}

【评分标准】
{phase1_criteria}

请严格按以下 JSON 格式输出，不要包含其他文字：
{{
  "phase1_similarity": 85,
  "phase1_comment": "与参考图相比的相似度评价，指出学生图在结构完整性和画图规范性方面的表现"
}}"""

    ref_b64 = image_to_base64(ref_image_path)
    stu_b64 = image_to_base64(stu_image_path)

    content: list[dict] = [
        {"type": "text", "text": prompt_text},
        {"type": "text", "text": "\n【参考工程图】："},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{ref_b64}"}},
        {"type": "text", "text": "\n【学生提交的工程图】："},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{stu_b64}"}},
    ]

    logger.info(f"阶段一评分中（模型: {model}）…")
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        temperature=0.1,
        max_tokens=4096,
    )
    raw_text = response.choices[0].message.content or ""
    result = _parse_json_response(raw_text)
    logger.info(f"阶段一评分完成 → 相似度 {result.get('phase1_similarity', '?')}")
    return result


def grade_phase2(
    ref_quant: dict,
    stu_quant: dict,
    phase2_criteria: str,
) -> dict:
    """
    阶段二：量化标注评分（纯文本对比，无需图片）。
    Prompt = 量化对比指令 + 两份量化 JSON + 老师阶段二评分标准
    Content = 纯文本
    返回 dict 包含 phase2_criteria, 图样表达, 尺寸标注, 尺寸公差, 表面质量, 形位公差, phase2_comment, 总评
    """
    client = _build_client()
    model = _get_model()

    # 读取阶段二修正提示词（匹配规则：按数值而非ID）
    from config import CONFIG_DIR
    hint_path = CONFIG_DIR / "二阶段修正提示词.txt"
    phase2_hint = hint_path.read_text(encoding="utf-8") if hint_path.exists() else ""

    prompt_text = f"""{phase2_hint}

你是一位机械检测工程师。请逐项对比两份量化分析数据，评估学生标注的完整性和正确性。

【参考图量化数据】
{json.dumps(ref_quant, ensure_ascii=False, indent=2)}

【学生图量化数据】
{json.dumps(stu_quant, ensure_ascii=False, indent=2)}

【评分标准】
{phase2_criteria}

请严格按以下 JSON 格式输出，不要包含其他文字：
{{
  "phase2_criteria": 85,
  "图样表达": "评价图样表达是否清晰规范",
  "尺寸标注": "评价尺寸标注是否齐全、正确",
  "尺寸公差": "评价公差标注是否规范",
  "表面质量": "评价粗糙度等表面质量标注",
  "形位公差": "评价形位公差标注情况",
  "phase2_comment": "按批改要求的综合评价",
  "总评": "综合两阶段的整体评价"
}}"""

    logger.info(f"阶段二评分中（模型: {model}）…")
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt_text}],
        temperature=0.1,
        max_tokens=4096,
    )
    raw_text = response.choices[0].message.content or ""
    result = _parse_json_response(raw_text)
    logger.info(f"阶段二评分完成 → 评分 {result.get('phase2_criteria', '?')}")
    return result


def run_two_phase_grading(
    ref_struct: dict,
    ref_quant: dict,
    stu_struct: dict,
    stu_quant: dict,
    phase1_criteria: str,
    phase2_criteria: str,
    ref_image_path: Path,
    stu_image_path: Path,
) -> dict:
    """
    执行完整的两阶段评分流程：
    阶段一（视觉 + 结构 JSON） → 阶段二（纯文本量化对比） → 总分 + 等级。
    返回完整评分结果 dict，包含 grade, total_score, 各维度评语等。
    """
    p1 = grade_phase1(ref_struct, stu_struct, phase1_criteria, ref_image_path, stu_image_path)
    p2 = grade_phase2(ref_quant, stu_quant, phase2_criteria)

    # 合并两阶段结果
    merged = {**p1, **p2}

    # 计算总分和等级
    p1_score = float(merged.get("phase1_similarity", 0))
    p2_score = float(merged.get("phase2_criteria", 0))
    total = round(p1_score * p2_score / 100, 1)
    grade = _compute_grade(total)

    merged["grade"] = grade
    merged["phase1_similarity"] = p1_score
    merged["phase2_criteria"] = p2_score
    merged["total_score"] = total

    logger.info(f"两阶段评分完成 → 总分 {total}% 等级 {grade}")
    return merged
