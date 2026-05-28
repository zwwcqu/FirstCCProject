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


def _get_active_config() -> dict:
    """获取当前激活的模型配置"""
    settings = read_settings()
    models = settings.get("models", [])
    idx = settings.get("llm_active", 0)
    if models and 0 <= idx < len(models):
        return models[idx]
    # 兼容旧格式
    return {
        "name": "默认",
        "api_base": settings.get("llm_api_base", ""),
        "api_key": settings.get("llm_api_key", ""),
        "model": settings.get("llm_model", ""),
        "concurrency": 1,
    }


def _build_client() -> OpenAI:
    """按当前激活模型配置创建 OpenAI 客户端"""
    cfg = _get_active_config()
    return OpenAI(
        base_url=cfg["api_base"],
        api_key=cfg["api_key"],
        timeout=120,
    )


def _get_model() -> str:
    """获取当前激活的模型名称"""
    global _cached_model
    configured = _get_active_config().get("model", "").strip()
    if configured:
        _cached_model = configured
        return _cached_model

    client = _build_client()
    models = client.models.list()
    if models.data:
        logger.info(f"自动检测到模型: {models.data[0].id}")
        return models.data[0].id
    if _cached_model:
        return _cached_model
    raise RuntimeError("无法获取模型列表，请检查大模型服务是否正常运行")


# ── 图像处理 ─────────────────────────────────────────────
MAX_IMAGE_SIZE = 3508                     # 长边最大像素数，超过则等比缩放


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


def save_as_png(input_path: Path, output_path: Path) -> Path:
    """将 PDF/图片转换为 PNG 并保存到 output_path，返回输出路径"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if input_path.suffix.lower() == ".pdf":
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(str(input_path), first_page=1, last_page=1, dpi=150)
            img = _resize_image(images[0])
            img.save(str(output_path), format="PNG")
            return output_path
        except ImportError:
            raise RuntimeError("pdf2image 未安装，无法处理 PDF 工程图")
    elif input_path.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        img = Image.open(input_path)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img = _resize_image(img)
        img.save(str(output_path), format="PNG")
        return output_path
    else:
        raise ValueError(f"不支持的文件格式: {input_path.suffix}")


def bytes_to_base64(data: bytes, filename: str) -> str:
    """将内存中的 PDF/图片 bytes 直接转为 JPEG Base64，不落盘。测试模式使用"""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        try:
            from pdf2image import convert_from_bytes
            images = convert_from_bytes(data, first_page=1, last_page=1, dpi=150)
            img = _resize_image(images[0])
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode()
        except ImportError:
            raise RuntimeError("pdf2image 未安装，无法处理 PDF 工程图。请安装 poppler 和 pdf2image。")
    elif ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        img = Image.open(BytesIO(data))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img = _resize_image(img)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode()
    else:
        raise ValueError(f"不支持的文件格式: {ext}")


# ── LLM 调用 + JSON 解析（自动重试）─────────────────────

def _call_and_parse(client, model, messages, parse_fn, temperature=0.1, max_tokens=4096, max_retries=1):
    """调用 LLM → 解析 JSON。JSON 解析失败时自动重试一次"""
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "extra_body": {"enable_thinking": False},
    }
    last_error = None
    for attempt in range(max_retries + 1):
        response = client.chat.completions.create(**kwargs)
        raw_text = response.choices[0].message.content or ""
        try:
            return parse_fn(raw_text)
        except ValueError as e:
            last_error = e
            if attempt < max_retries:
                logger.warning(f"JSON 解析失败（第{attempt+1}次），重试: {e}")
    raise last_error  # type: ignore


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

def _repair_json_text(text: str) -> str:
    """修复 LLM 常见的 JSON 格式错误"""
    # 去掉 markdown 代码块
    text = re.sub(r'^```(?:json)?\s*', '', text.strip())
    text = re.sub(r'\s*```$', '', text)
    # 提取最外层 {...}
    match = re.search(r'\{[\s\S]*\}', text, re.DOTALL)
    if match:
        text = match.group(0)
    # 修复尾随逗号
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*]', ']', text)
    # 修复行末缺少逗号: "xxx"\n  "yyy" → "xxx",\n  "yyy"
    text = re.sub(r'"\s*\n\s*"', '",\n  "', text)
    # 修复值后缺少逗号: }\n  "key" → },\n  "key"
    text = re.sub(r'}\s*\n\s*"', '},\n  "', text)
    # 修复数字/布尔后缺少逗号
    text = re.sub(r'(\d+|true|false|null)\s*\n\s*"', r'\1,\n  "', text)
    return text


def _parse_json_response(text: str) -> dict:
    """从 LLM 输出中提取 JSON，容错常见格式错误（尾随逗号/缺逗号/markdown）"""
    repaired = _repair_json_text(text)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    # json.loads 失败，尝试 json5 宽松解析
    try:
        import json5
        return json5.loads(repaired)
    except Exception:
        pass
    # 仍失败则抛出带上下文的错误
    raise ValueError(f"JSON 解析失败，原文前200字符: {repaired[:200]}")


def _parse_llm_response(text: str) -> dict:
    """从 LLM 输出中提取含 phase1_similarity 的 JSON 对象，容错解析"""
    text = text.strip()
    match = re.search(r'\{[\s\S]*"phase1_similarity"[\s\S]*\}', text, re.DOTALL)
    if match:
        text = match.group(0)
    repaired = _repair_json_text(text)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    try:
        import json5
        return json5.loads(repaired)
    except Exception:
        pass
    raise ValueError(f"评分 JSON 解析失败，原文前200字符: {repaired[:200]}")


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
    result = _call_and_parse(client, model,
        [{"role": "user", "content": content}],
        _parse_llm_response)

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
    result = _call_and_parse(client, model,
        [{"role": "user", "content": content}],
        _parse_json_response)
    logger.info("结构分析完成")
    return result


def analyze_structure_bytes(data: bytes, filename: str, template_path: Path) -> dict:
    """结构分析（bytes 版本，不读磁盘）。测试模式使用"""
    client = _build_client()
    model = _get_model()
    prompt_text = template_path.read_text(encoding="utf-8")
    b64 = bytes_to_base64(data, filename)

    content: list[dict] = [
        {"type": "text", "text": prompt_text},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]

    logger.info(f"结构分析中（模型: {model}）…")
    return _call_and_parse(client, model,
        [{"role": "user", "content": content}],
        _parse_json_response)


def analyze_quantitative_bytes(data: bytes, filename: str, template_path: Path, structure_json: dict) -> dict:
    """量化分析（bytes 版本，不读磁盘）。测试模式使用"""
    client = _build_client()
    model = _get_model()
    template_text = template_path.read_text(encoding="utf-8")
    prompt_text = template_text.replace("__STRUCTURE_JSON__", json.dumps(structure_json, ensure_ascii=False, indent=2))
    b64 = bytes_to_base64(data, filename)

    content: list[dict] = [
        {"type": "text", "text": prompt_text},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]

    logger.info(f"量化分析中（模型: {model}）…")
    return _call_and_parse(client, model,
        [{"role": "user", "content": content}],
        _parse_json_response)


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
    result = _call_and_parse(client, model,
        [{"role": "user", "content": content}],
        _parse_json_response)
    logger.info("量化分析完成")
    return result


# ── 两阶段评分（新版：基于分析结果）───────────────────────

def grade_phase1(
    ref_struct: dict,
    stu_struct: dict,
    phase1_criteria: str,
    ref_image_path: Path,
    stu_image_path: Path,
    *,
    stu_data: bytes | None = None,
    stu_filename: str = "",
) -> dict:
    """
    阶段一：结构相似度评分（视觉对比）。
    submit 模式用 stu_image_path；test 模式传 stu_data + stu_filename（不读磁盘）。
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
    if stu_data:
        stu_b64 = bytes_to_base64(stu_data, stu_filename)
    else:
        stu_b64 = image_to_base64(stu_image_path)

    content: list[dict] = [
        {"type": "text", "text": prompt_text},
        {"type": "text", "text": "\n【参考工程图】："},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{ref_b64}"}},
        {"type": "text", "text": "\n【学生提交的工程图】："},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{stu_b64}"}},
    ]

    logger.info(f"阶段一评分中（模型: {model}）…")
    result = _call_and_parse(client, model,
        [{"role": "user", "content": content}],
        _parse_json_response)
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
    result = _call_and_parse(client, model,
        [{"role": "user", "content": prompt_text}],
        _parse_json_response)
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
    *,
    stu_data: bytes | None = None,
    stu_filename: str = "",
) -> dict:
    """
    执行完整的两阶段评分流程。
    submit 模式传入 stu_image_path；test 模式传入 stu_data + stu_filename。
    """
    p1 = grade_phase1(ref_struct, stu_struct, phase1_criteria, ref_image_path,
                      stu_image_path, stu_data=stu_data, stu_filename=stu_filename)
    p2 = grade_phase2(ref_quant, stu_quant, phase2_criteria)

    merged = {**p1, **p2}

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
