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
  等级阈值：A+≥90, A≥85, B+≥80, B≥75, C+≥68.75,
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


# ── 拍照图判别（本地图像特征，不调大模型）────────────────

def check_if_photo(image_path: Path) -> tuple[bool, str]:
    """
    检测图片是否为手机/相机拍摄的实物照片（非正版工程图）。
    基于 EXIF 信息、四角采样、宽高比、色彩分布等特征综合判断。
    支持 PDF（取首页渲染）和图片格式。
    返回 (is_photo: bool, reason: str)
    """
    # PDF 先转图片（用较高 DPI 保留线条细节）
    if image_path.suffix.lower() == ".pdf":
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(str(image_path), first_page=1, last_page=1, dpi=120)
            if not images:
                return False, ""
            img = images[0]
        except ImportError:
            return False, ""
    else:
        img = Image.open(image_path)
    w, h = img.size

    # 1. EXIF 检测：有相机品牌/型号则强判为拍照
    exif = img.getexif()
    if exif:
        make = exif.get(0x010F, "")  # Make
        model = exif.get(0x0110, "")  # Model
        software = exif.get(0x0131, "")  # Software
        if make or model:
            return True, f"检测到相机信息: {make} {model}".strip()
        # 常见手机修图软件也视为拍照
        photo_software = ["snapseed", "lightroom", "meitu", "美图", "vsco", "picsart"]
        if any(s in software.lower() for s in photo_software):
            return True, f"检测到修图软件: {software}"

    # 2. 宽高比检测：工程图应符合标准纸张比例
    ratio = w / h if w > h else h / w
    if not (1.39 < ratio < 1.43):
        return True, f"宽高比异常（{ratio:.3f}），标准工程图应为A4/A3纸张比例（1.39~1.43）"

    # 3. 色彩分布检测：缩略图采样统计
    small = img.convert("RGB").resize((200, 200))
    colored = 0
    pure_white = 0
    total_small = 200 * 200

    for px in small.getdata():
        r, g, b = px[0], px[1], px[2]
        gray = (r + g + b) / 3
        max_diff = max(abs(r - g), abs(g - b), abs(r - b))

        if max_diff > 18:
            colored += 1
        if gray > 250:
            pure_white += 1

    color_rate = colored / total_small

    if color_rate > 0.05:
        return True, f"检测到彩色噪点（{color_rate:.1%}），疑似拍照或截图。"
    if pure_white / total_small < 0.75:
        return True, f"白色背景比例偏低（纯白仅 {pure_white/total_small:.1%}），疑似截图或扫描件。标准工程图纯白背景应在75%以上。"

    return False, ""


def _pixel_similarity(img1: Image.Image, img2: Image.Image, threshold: int = 20) -> float:
    """两图逐像素相似度 (0-100)。threshold 为单通道容差"""
    p1 = list(img1.getdata())
    p2 = list(img2.getdata())
    diff = sum(1 for i in range(len(p1))
               if abs(p1[i][0] - p2[i][0]) > threshold
               or abs(p1[i][1] - p2[i][1]) > threshold
               or abs(p1[i][2] - p2[i][2]) > threshold)
    return (1 - diff / len(p1)) * 100


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
A+≥90, A≥85, B+≥80, B≥75, C+≥68.75, C≥62.5, D+≥56.25, D≥50, F<50

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
    (90, "A+"),
    (85, "A"),
    (80, "B+"),
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

def analyze_structure(image_path: Path, template_path: Path, knowledge: str = "") -> dict:
    """
    对工程图进行结构分析。
    发送图片 + 结构分析模版 → LLM → 返回结构特征 JSON。
    返回 dict 包含 title_block, views, features, overall_shape, technical_notes 等。
    """
    client = _build_client()
    model = _get_model()
    prompt_text = template_path.read_text(encoding="utf-8")
    if knowledge:
        prompt_text = f"【补充知识】\n{knowledge}\n\n{prompt_text}"
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


def analyze_structure_bytes(data: bytes, filename: str, template_path: Path, knowledge: str = "") -> dict:
    """结构分析（bytes 版本，不读磁盘）。测试模式使用"""
    client = _build_client()
    model = _get_model()
    prompt_text = template_path.read_text(encoding="utf-8")
    if knowledge:
        prompt_text = f"【补充知识】\n{knowledge}\n\n{prompt_text}"
    b64 = bytes_to_base64(data, filename)

    content: list[dict] = [
        {"type": "text", "text": prompt_text},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]

    logger.info(f"结构分析中（模型: {model}）…")
    return _call_and_parse(client, model,
        [{"role": "user", "content": content}],
        _parse_json_response)


def analyze_quantitative_bytes(data: bytes, filename: str, template_path: Path, structure_json: dict, knowledge: str = "") -> dict:
    """量化分析（bytes 版本，不读磁盘）。测试模式使用"""
    client = _build_client()
    model = _get_model()
    template_text = template_path.read_text(encoding="utf-8")
    prompt_text = template_text.replace("__STRUCTURE_JSON__", json.dumps(structure_json, ensure_ascii=False, indent=2))
    if knowledge:
        prompt_text = f"【补充知识】\n{knowledge}\n\n{prompt_text}"
    b64 = bytes_to_base64(data, filename)

    content: list[dict] = [
        {"type": "text", "text": prompt_text},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]

    logger.info(f"量化分析中（模型: {model}）…")
    return _call_and_parse(client, model,
        [{"role": "user", "content": content}],
        _parse_json_response)


def analyze_quantitative(image_path: Path, template_path: Path, structure_json: dict, knowledge: str = "") -> dict:
    """
    对工程图进行量化分析（依赖结构分析结果）。
    将 __STRUCTURE_JSON__ 替换为实际结构 JSON → 发送图片 + 填充模版 → LLM → 返回量化 JSON。
    返回 dict 包含 dimensions, surface_roughness, geometric_tolerances, thread_specs 等。
    """
    client = _build_client()
    model = _get_model()
    template_text = template_path.read_text(encoding="utf-8")
    prompt_text = template_text.replace("__STRUCTURE_JSON__", json.dumps(structure_json, ensure_ascii=False, indent=2))
    if knowledge:
        prompt_text = f"【补充知识】\n{knowledge}\n\n{prompt_text}"
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
    knowledge: str = "",
) -> dict:
    """
    阶段一：结构相似度评分（视觉对比）。
    submit 模式用 stu_image_path；test 模式传 stu_data + stu_filename（不读磁盘）。
    """
    client = _build_client()
    model = _get_model()

    kn_block = f"【补充知识】\n{knowledge}\n\n" if knowledge else ""
    prompt_text = f"""{kn_block}你是一位工程图批阅老师。请对比学生图和参考图的结构特征，评估图形相似度和画图质量。

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


def _simplify_quantitative(data: dict) -> dict:
    """精简量化分析 JSON，仅保留评分所需的关键字段，减少 token 消耗。"""
    simplified: dict = {}

    # 尺寸标注：仅保留 count + 每条数值/公差
    dims = data.get("dimensions", [])
    simplified["尺寸数量"] = len(dims)
    simplified["尺寸标注"] = [
        {"数值": d.get("value"), "公差": d.get("tolerance")}
        for d in dims
    ]

    # 表面粗糙度：仅保留 count + 每条数值
    roughness = data.get("surface_roughness", [])
    simplified["粗糙度数量"] = len(roughness)
    simplified["表面粗糙度"] = [{"数值": r.get("value")} for r in roughness]

    # 形位公差：仅保留 count + 每条类型/数值/基准
    geos = data.get("geometric_tolerances", [])
    simplified["形位公差项数"] = len(geos)
    simplified["形位公差"] = [
        {"类型": g.get("type"), "数值": g.get("value")}
        for g in geos
    ]

    if data.get("技术要求"):
        simplified["技术要求"] = data["技术要求"]
    if "thread_specs" in data and data["thread_specs"]:
        simplified["螺纹规格"] = data["thread_specs"]

    return simplified


def grade_phase2(
    ref_quant: dict,
    stu_quant: dict,
    phase2_criteria: str,
    *,
    knowledge: str = "",
) -> dict:
    """
    阶段二：量化标注评分（纯文本对比，无需图片）。
    先精简量化 JSON（去掉 id/feature_ref/description/location 等冗余字段），再发送评分。
    返回 dict 包含 phase2_criteria, 图样表达, 尺寸标注, 尺寸公差, 表面质量, 形位公差, phase2_comment, 总评
    """
    client = _build_client()
    model = _get_model()

    from config import CONFIG_DIR
    hint_path = CONFIG_DIR / "二阶段修正提示词.txt"
    phase2_hint = hint_path.read_text(encoding="utf-8") if hint_path.exists() else ""

    ref_simple = _simplify_quantitative(ref_quant)
    stu_simple = _simplify_quantitative(stu_quant)

    kn_block = f"【补充知识】\n{knowledge}\n\n" if knowledge else ""
    prompt_text = f"""{kn_block}{phase2_hint}

你是一位机械检测工程师。请逐项对比两份量化分析数据，评估学生标注的完整性和正确性。

【参考图量化数据】
{json.dumps(ref_simple, ensure_ascii=False, indent=2)}

【学生图量化数据】
{json.dumps(stu_simple, ensure_ascii=False, indent=2)}

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
  "技术要求": "评价技术要求文本的完整性和相似度",
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
    knowledge: str = "",
) -> dict:
    """
    执行完整的两阶段评分流程。
    submit 模式传入 stu_image_path；test 模式传入 stu_data + stu_filename。
    """
    p1 = grade_phase1(ref_struct, stu_struct, phase1_criteria, ref_image_path,
                      stu_image_path, stu_data=stu_data, stu_filename=stu_filename,
                      knowledge=knowledge)
    p2 = grade_phase2(ref_quant, stu_quant, phase2_criteria, knowledge=knowledge)

    merged = {**p1, **p2}

    p1_score = float(merged.get("phase1_similarity", 0))
    p2_score = float(merged.get("phase2_criteria", 0))
    total = round((p1_score * p2_score) ** 0.5, 1)
    grade = _compute_grade(total)

    merged["grade"] = grade
    merged["phase1_similarity"] = p1_score
    merged["phase2_criteria"] = p2_score
    merged["total_score"] = total

    logger.info(f"两阶段评分完成 → 总分 {total}% 等级 {grade}")
    return merged
