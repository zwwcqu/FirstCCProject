"""
LLM 批阅服务。

功能：
- 对接 OpenAI 兼容 API（LM Studio / 云端模型）
- 自动检测模型（未配置时从 LM Studio 获取）
- PDF/图片转 Base64（PDF 通过 pdf2image + poppler 渲染为 JPEG）
- 构建两阶段评分 Prompt（相似度 → 批改要求 → 总分）
- 解析 LLM 返回的 JSON，计算最终等级（A+ ~ F 共 9 档）

评分公式：
  总分 = √(阶段1分数 × 阶段2分数)
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

from config import read_settings, get_llm_params, get_image_params, get_grade_thresholds, get_prompt_templates, get_scoring_templates

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
    llm = get_llm_params()
    return OpenAI(
        base_url=cfg["api_base"],
        api_key=cfg["api_key"],
        timeout=llm.get("client_timeout", 120),
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

def _image_param(key: str) -> int:
    """读取图像参数（默认值由 config.get_image_params() 提供）"""
    return get_image_params()[key]


def _analysis_max_size() -> int:
    return _image_param("analysis_max_size")


def _phase1_max_size() -> int:
    return _image_param("phase1_max_size")


def _phase1_jpeg_quality() -> int:
    return _image_param("phase1_jpeg_quality")


def _analysis_jpeg_quality() -> int:
    return _image_param("analysis_jpeg_quality")


def _analysis_dpi() -> int:
    return _image_param("analysis_dpi")


def _resize_image(img: Image.Image, max_size: int | None = None) -> Image.Image:
    """若图像长边超过 max_size，等比缩放。max_size=None 时使用 analysis_max_size"""
    if max_size is None:
        max_size = _analysis_max_size()
    w, h = img.size
    longest = max(w, h)
    if longest <= max_size:
        return img
    ratio = max_size / longest
    new_w, new_h = int(w * ratio), int(h * ratio)
    return img.resize((new_w, new_h), Image.LANCZOS)


def image_to_base64(path: Path, max_size: int | None = None,
                    quality: int | None = None) -> str:
    """将 PDF 或图片文件转为 JPEG 的 Base64 字符串。PDF 取首页渲染。
    max_size: 长边最大像素数（None=使用 analysis_max_size）；quality: JPEG 质量（None=使用 analysis_jpeg_quality）"""
    if max_size is None:
        max_size = _analysis_max_size()
    if quality is None:
        quality = _analysis_jpeg_quality()
    dpi = _analysis_dpi()
    if path.suffix.lower() == ".pdf":
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(str(path), first_page=1, last_page=1, dpi=dpi)
            img = _resize_image(images[0], max_size)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            return base64.b64encode(buf.getvalue()).decode()
        except ImportError:
            raise RuntimeError("pdf2image 未安装，无法处理 PDF 工程图。请安装 poppler 和 pdf2image。")
    elif path.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        img = Image.open(path)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")        # RGBA/调色板转为 RGB，避免 JPEG 保存报错
        img = _resize_image(img, max_size)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return base64.b64encode(buf.getvalue()).decode()
    else:
        raise ValueError(f"不支持的文件格式: {path.suffix}")


def save_as_png(input_path: Path, output_path: Path) -> Path:
    """将 PDF/图片转换为 PNG 并保存到 output_path，返回输出路径"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dpi = _analysis_dpi()
    if input_path.suffix.lower() == ".pdf":
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(str(input_path), first_page=1, last_page=1, dpi=dpi)
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


def bytes_to_base64(data: bytes, filename: str, max_size: int | None = None,
                    quality: int | None = None) -> str:
    """将内存中的 PDF/图片 bytes 直接转为 JPEG Base64，不落盘。测试模式使用。
    max_size: 长边最大像素数（None=使用 analysis_max_size）；quality: JPEG 质量（None=使用 analysis_jpeg_quality）"""
    if max_size is None:
        max_size = _analysis_max_size()
    if quality is None:
        quality = _analysis_jpeg_quality()
    dpi = _analysis_dpi()
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        try:
            from pdf2image import convert_from_bytes
            images = convert_from_bytes(data, first_page=1, last_page=1, dpi=dpi)
            img = _resize_image(images[0], max_size)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            return base64.b64encode(buf.getvalue()).decode()
        except ImportError:
            raise RuntimeError("pdf2image 未安装，无法处理 PDF 工程图。请安装 poppler 和 pdf2image。")
    elif ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        img = Image.open(BytesIO(data))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img = _resize_image(img, max_size)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return base64.b64encode(buf.getvalue()).decode()
    else:
        raise ValueError(f"不支持的文件格式: {ext}")


# ── 拍照图判别（本地图像特征，不调大模型）────────────────

def _get_photo_detection_config() -> dict:
    """读取拍照检测参数，优先 settings_debug.json，缺失时使用内置默认值"""
    from config import read_settings_debug
    defaults = {
        "enabled": False, "render_dpi": 120, "sample_size": 200,
        "color_threshold": 18, "white_threshold": 250,
        "color_rate_max": 0.05, "white_rate_min": 0.75,
        "aspect_ratio_min": 1.39, "aspect_ratio_max": 1.43,
    }
    cfg = read_settings_debug().get("photo_detection", {})
    return {**defaults, **cfg}


def check_if_photo(image_path: Path) -> tuple[bool, str]:
    """
    检测图片是否为手机/相机拍摄的实物照片（非正版工程图）。
    基于 EXIF 信息、四角采样、宽高比、色彩分布等特征综合判断。
    支持 PDF（取首页渲染）和图片格式。
    参数从 settings_debug.json → photo_detection 读取。
    返回 (is_photo: bool, reason: str)
    """
    pd_cfg = _get_photo_detection_config()
    if not pd_cfg.get("enabled", False):
        return False, ""

    render_dpi = pd_cfg.get("render_dpi", 120)
    sample_size = pd_cfg.get("sample_size", 200)
    color_threshold = pd_cfg.get("color_threshold", 18)
    white_threshold = pd_cfg.get("white_threshold", 250)
    color_rate_max = pd_cfg.get("color_rate_max", 0.05)
    white_rate_min = pd_cfg.get("white_rate_min", 0.75)
    aspect_min = pd_cfg.get("aspect_ratio_min", 1.39)
    aspect_max = pd_cfg.get("aspect_ratio_max", 1.43)

    # PDF 先转图片
    if image_path.suffix.lower() == ".pdf":
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(str(image_path), first_page=1, last_page=1, dpi=render_dpi)
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
    if not (aspect_min < ratio < aspect_max):
        return True, f"宽高比异常（{ratio:.3f}），标准工程图应为A4/A3纸张比例（{aspect_min}~{aspect_max}）"

    # 3. 色彩分布检测：缩略图采样统计
    small = img.convert("RGB").resize((sample_size, sample_size))
    colored = 0
    pure_white = 0
    total_pixels = sample_size * sample_size

    for px in small.getdata():
        r, g, b = px[0], px[1], px[2]
        gray = (r + g + b) / 3
        max_diff = max(abs(r - g), abs(g - b), abs(r - b))

        if max_diff > color_threshold:
            colored += 1
        if gray > white_threshold:
            pure_white += 1

    color_rate = colored / total_pixels

    if color_rate > color_rate_max:
        return True, f"检测到彩色噪点（{color_rate:.1%}），疑似拍照或截图。"
    if pure_white / total_pixels < white_rate_min:
        return True, f"白色背景比例偏低（纯白仅 {pure_white/total_pixels:.1%}），疑似截图或扫描件。标准工程图纯白背景应在{white_rate_min:.0%}以上。"

    return False, ""


# ── LLM 调用 + JSON 解析（自动重试）─────────────────────

def _call_and_parse(client, model, messages, parse_fn, temperature=None, max_tokens=None, max_retries=None):
    """调用 LLM → 解析 JSON。JSON 解析失败时自动重试一次。
    temperature/max_tokens/max_retries: None=使用 settings 默认值"""
    llm = get_llm_params()
    if temperature is None:
        temperature = llm.get("temperature", 0.1)
    if max_tokens is None:
        max_tokens = llm.get("max_tokens", 4096)
    if max_retries is None:
        max_retries = 1
    enable_thinking = llm.get("enable_thinking", False)
    logger.info(f"[LLM-CALL] 即将调用模型: {model}, max_tokens={max_tokens}")
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "extra_body": {"enable_thinking": enable_thinking},
    }
    last_error = None
    last_usage = None
    for attempt in range(max_retries + 1):
        response = client.chat.completions.create(**kwargs)
        raw_text = response.choices[0].message.content or ""
        # 捕获 token 用量（重试时使用最后一次成功调用的数据）
        if hasattr(response, 'usage') and response.usage is not None:
            last_usage = {
                "prompt_tokens": getattr(response.usage, 'prompt_tokens', 0) or 0,
                "completion_tokens": getattr(response.usage, 'completion_tokens', 0) or 0,
                "total_tokens": getattr(response.usage, 'total_tokens', 0) or 0,
            }
        try:
            result = parse_fn(raw_text)
            result["_model"] = model
            if last_usage:
                result["_usage"] = last_usage
            return result
        except ValueError as e:
            last_error = e
            if attempt < max_retries:
                logger.warning(f"JSON 解析失败（第{attempt+1}次），重试: {e}")
    raise last_error  # type: ignore


# ── 元数据清洗 ───────────────────────────────────────────

def _strip_meta(d: dict) -> dict:
    """去掉 dict 中以下划线开头的内部字段（_model, _usage 等），避免泄露到 LLM prompt"""
    return {k: v for k, v in d.items() if not k.startswith("_")}


# ── 元数据清洗 ───────────────────────────────────────────

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


# ── 等级计算 ─────────────────────────────────────────────

def _compute_grade(total: float) -> str:
    """按总分映射到九档等级，低于最低阈值为 F"""
    thresholds = get_grade_thresholds()
    for threshold, grade in thresholds:
        if total >= threshold:
            return grade
    return "F"


# ── 核心批阅流程 ────────────────────────────────────────

# grade_submission() has been removed. Use analyze_and_grade() instead.


# ── 合并分析（单次调用完成全部提取）───────────────────────

def analyze_merged(image_path: Path, template_text: str, *,
                   knowledge: str = "") -> dict:
    """
    单次 LLM 调用完成工程图识读。
    template_text: 识读模板的完整文本（来自 data/{qid}/识读模板.txt）
    返回 dict 包含 LLM 输出的全部字段，以及 _model、_usage 元数据。
    """
    client = _build_client()
    model = _get_model()

    prompt_text = template_text
    if knowledge:
        prompt_text = f"【补充知识】\n{knowledge}\n\n{prompt_text}"
    b64 = image_to_base64(image_path)

    content: list[dict] = [
        {"type": "text", "text": prompt_text},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]

    logger.info(f"工程图识读中（模型: {model}）…")
    result = _call_and_parse(client, model,
        [{"role": "user", "content": content}],
        _parse_json_response)
    logger.info("工程图识读完成")

    # 校验：工程图概述不能为空
    overview = result.get("工程图概述", "")
    if not overview or len(str(overview)) < 10:
        logger.warning("工程图概述为空或过短（<10字符），模型可能未能正确识读图片")

    result["_model"] = result.get("_model", model)
    result["_usage"] = result.get("_usage", {})
    return result


def analyze_merged_bytes(data: bytes, filename: str, template_text: str, *,
                          knowledge: str = "") -> dict:
    """合并分析（bytes 版本，测试模式使用）。template_text 为识读模板文本。"""
    client = _build_client()
    model = _get_model()

    prompt_text = template_text
    if knowledge:
        prompt_text = f"【补充知识】\n{knowledge}\n\n{prompt_text}"
    b64 = bytes_to_base64(data, filename)

    content: list[dict] = [
        {"type": "text", "text": prompt_text},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ]

    logger.info(f"工程图识读中（模型: {model}）…")
    result = _call_and_parse(client, model,
        [{"role": "user", "content": content}],
        _parse_json_response)
    logger.info("工程图识读完成")
    result["_model"] = result.get("_model", model)
    result["_usage"] = result.get("_usage", {})
    return result


# ── 两阶段评分（新版：基于分析结果）───────────────────────

def grade_phase1(
    ref_analysis: dict,
    stu_analysis: dict,
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
    对比两份工程图识读结果的概述和几何特征，结合缩略图做视觉判断。
    ref_analysis/stu_analysis: analyze_merged 的返回结果（新格式）。
    """
    client = _build_client()
    model = _get_model()

    kn_block = f"【补充知识】\n{knowledge}\n\n" if knowledge else ""
    templates = get_prompt_templates()
    phase1_guide = templates.get("grading_guide", "你是一位工程图批阅老师。请对比学生图和参考图，从结构完整性和标注准确性两方面综合评分。")

    # 提取阶段一相关的字段：概述、几何特征、各视图信息（如存在）
    def _p1_fields(analysis: dict) -> dict:
        fields = {}
        for key in ("工程图概述", "基本信息", "几何特征"):
            if key in analysis:
                fields[key] = analysis[key]
        if "各视图信息" in analysis:
            fields["各视图信息"] = analysis["各视图信息"]
        if "零件清单" in analysis:
            fields["零件清单"] = analysis["零件清单"]
        return fields

    ref_p1 = _p1_fields(ref_analysis)
    stu_p1 = _p1_fields(stu_analysis)

    prompt_text = f"""{kn_block}{phase1_guide}

【参考工程图分析】
{json.dumps(_strip_meta(ref_p1), ensure_ascii=False, indent=2)}

【学生工程图分析】
{json.dumps(_strip_meta(stu_p1), ensure_ascii=False, indent=2)}

【评分标准】
{phase1_criteria}

请严格按以下 JSON 格式输出，不要包含其他文字：
{{
  "phase1_similarity": 85,
  "phase1_comment": "与参考图相比的相似度评价，指出学生图在结构完整性和画图规范性方面的表现"
}}"""

    p1_size = _phase1_max_size()
    p1_qual = _phase1_jpeg_quality()
    ref_b64 = image_to_base64(ref_image_path, max_size=p1_size, quality=p1_qual)
    if stu_data:
        stu_b64 = bytes_to_base64(stu_data, stu_filename, max_size=p1_size, quality=p1_qual)
    else:
        stu_b64 = image_to_base64(stu_image_path, max_size=p1_size, quality=p1_qual)

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
    """精简量化分析 JSON，从新格式中提取评分所需字段，减少 token 消耗。容错 LLM 返回的非标准格式。"""
    simplified: dict = {}

    def _safe_items(arr, key_map: dict):
        """安全提取数组项，容错字符串/非dict元素"""
        result = []
        for item in (arr or []):
            if isinstance(item, str):
                result.append({list(key_map.values())[0]: item} if key_map else {"value": item})
            elif isinstance(item, dict):
                result.append({vk: item.get(vk, "") for vk in key_map.values()})
        return result

    # 尺寸标注
    dims = data.get("尺寸", []) or []
    if dims:
        simplified["尺寸数量"] = len(dims)
        simplified["尺寸标注"] = _safe_items(dims, {"类别": "类别", "数值": "数值"})

    # 表面粗糙度
    roughness = data.get("表面粗糙度", []) or []
    if roughness:
        simplified["粗糙度数量"] = len(roughness)
        simplified["表面粗糙度"] = _safe_items(roughness, {"类别": "类别", "数值": "数值"})

    # 几何公差
    geos = data.get("几何公差", []) or []
    if geos:
        simplified["几何公差项数"] = len(geos)
        simplified["几何公差"] = _safe_items(geos, {"类别": "类别", "数值": "数值", "基准": "基准"})

    # 尺寸公差
    dim_tols = data.get("尺寸公差", []) or []
    if dim_tols:
        simplified["尺寸公差项数"] = len(dim_tols)
        simplified["尺寸公差"] = _safe_items(dim_tols, {"公称尺寸": "公称尺寸", "公差": "公差"})

    # 技术要求
    if data.get("技术要求"):
        simplified["技术要求"] = str(data["技术要求"])

    # 装配图特有字段
    if data.get("配合关系"):
        simplified["配合关系数量"] = len(data["配合关系"])
        simplified["配合关系"] = data["配合关系"]
    if data.get("外形尺寸"):
        simplified["外形尺寸"] = data["外形尺寸"]
    if data.get("零件清单"):
        simplified["零件总数"] = len(data["零件清单"])

    return simplified


def grade_phase2(
    ref_analysis: dict,
    stu_analysis: dict,
    phase2_criteria: str,
    *,
    knowledge: str = "",
) -> dict:
    """
    阶段二：量化标注评分（纯文本对比，无需图片）。
    先精简量化 JSON，再发送评分。
    ref_analysis/stu_analysis: analyze_merged 的返回结果（新格式）。
    """
    client = _build_client()
    model = _get_model()

    templates = get_prompt_templates()
    grading_guide = templates.get("grading_guide", "你是一位工程图批阅老师。请对比学生图和参考图，从结构完整性和标注准确性两方面综合评分。")

    ref_simple = _simplify_quantitative(ref_analysis)
    stu_simple = _simplify_quantitative(stu_analysis)

    kn_block = f"【补充知识】\n{knowledge}\n\n" if knowledge else ""
    prompt_text = f"""{kn_block}{grading_guide}

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


# ── 合并评分（单次 LLM 调用完成识读+两阶段评分）────────────

def analyze_and_grade(
    image_path: Path,
    template_text: str,
    ref_analysis: dict,
    phase1_criteria: str,
    phase2_criteria: str,
    ref_image_path: Path,
    *,
    knowledge: str = "",
) -> dict:
    """
    单次 LLM 调用完成：工程图识读 + 阶段一评分（视觉对比）+ 阶段二评分（量化对比）。
    返回包含所有分析字段、两个阶段评分、总分和等级的完整 dict。
    """
    client = _build_client()
    model = _get_model()
    templates = get_prompt_templates()

    # ── 阶段一相关字段 ──
    def _p1_fields(a: dict) -> dict:
        fields = {}
        for key in ("工程图概述", "基本信息", "几何特征"):
            if key in a:
                fields[key] = a[key]
        if "各视图信息" in a:
            fields["各视图信息"] = a["各视图信息"]
        if "零件清单" in a:
            fields["零件清单"] = a["零件清单"]
        return fields

    ref_simple = _simplify_quantitative(ref_analysis)

    grading_guide = templates.get("grading_guide",
        "你是一位工程图批阅老师。请对比学生图和参考图，从结构完整性和标注准确性两方面综合评分。")

    kn_block = f"【补充知识】\n{knowledge}\n\n" if knowledge else ""

    # ── 组装合并 prompt ──
    prompt_text = f"""{kn_block}你是一位机械制图检测专家兼工程图批阅老师。请一次性完成以下三项任务。

## 任务一：工程图识读（使用大图提取细节）
{template_text}

## 任务二：评分（使用缩略图对比结构）
{grading_guide}

【参考工程图分析（作为评分基准）】
{json.dumps(_strip_meta(_p1_fields(ref_analysis)), ensure_ascii=False, indent=2)}

【参考图量化数据】
{json.dumps(ref_simple, ensure_ascii=False, indent=2)}

【评分标准】
阶段一（图形相似度）：
{phase1_criteria}

阶段二（量化标注）：
{phase2_criteria}

---

请将三项任务的结果合并为一个 JSON 输出，不要用 markdown 代码块包裹：

{{
  "工程图概述": "...",
  "基本信息": {{ "零件名称": "...", "材料": "...", "比例": "..." }},
  "几何特征": [...],
  "尺寸": [...],
  "几何公差": [...],
  "表面粗糙度": [...],
  "尺寸公差": [...],
  "技术要求": "...",
  "phase1_similarity": 85,
  "phase1_comment": "...",
  "phase2_criteria": 80,
  "图样表达": "...",
  "尺寸标注": "...",
  "尺寸公差": "...",
  "表面质量": "...",
  "形位公差": "...",
  "phase2_comment": "...",
  "总评": "..."
}}"""

    # ── 图片 ──
    # 学生图传两次：大图用于识读（任务一），缩略图用于对比（任务二，与参考图同参数）
    stu_large = image_to_base64(image_path, max_size=_analysis_max_size(), quality=_analysis_jpeg_quality())
    stu_thumb = image_to_base64(image_path, max_size=_phase1_max_size(), quality=_phase1_jpeg_quality())
    ref_thumb = image_to_base64(ref_image_path, max_size=_phase1_max_size(), quality=_phase1_jpeg_quality())

    content: list[dict] = [
        {"type": "text", "text": prompt_text},
        {"type": "text", "text": "\n【学生工程图 — 用于识读（大图）】："},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{stu_large}"}},
        {"type": "text", "text": "\n【学生工程图对比 — 缩略图】："},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{stu_thumb}"}},
        {"type": "text", "text": "\n【参考工程图 — 缩略图】："},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{ref_thumb}"}},
    ]

    logger.info(f"合并分析+评分中（模型: {model}）…")
    result = _call_and_parse(client, model,
        [{"role": "user", "content": content}],
        _parse_json_response)
    logger.info("合并分析+评分完成")

    # ── 计算总分 ──
    p1 = float(result.get("phase1_similarity", 0))
    p2 = float(result.get("phase2_criteria", 0))
    total = round((p1 * p2) ** 0.5, 1)
    grade = _compute_grade(total)

    result["phase1_similarity"] = p1
    result["phase2_criteria"] = p2
    result["total_score"] = total
    result["grade"] = grade
    result["_model"] = result.get("_model", model)
    return result


def grade_combined(
    stu_analysis: dict,
    ref_analysis: dict,
    phase1_criteria: str,
    phase2_criteria: str,
    ref_image_path: Path,
    stu_image_path: Path,
    *,
    knowledge: str = "",
) -> dict:
    """
    已有分析结果的前提下，单次 LLM 调用完成阶段一+阶段二评分（不做识读）。
    返回 dict 含 phase1_similarity, phase2_criteria, total_score, grade 及各维评语。
    """
    client = _build_client()
    model = _get_model()
    templates = get_prompt_templates()
    grading_guide = templates.get("grading_guide",
        "你是一位工程图批阅老师。请对比学生图和参考图，从结构完整性和标注准确性两方面综合评分。")

    def _p1_fields(a: dict) -> dict:
        fields = {}
        for key in ("工程图概述", "基本信息", "几何特征"):
            if key in a:
                fields[key] = a[key]
        if "各视图信息" in a:
            fields["各视图信息"] = a["各视图信息"]
        if "零件清单" in a:
            fields["零件清单"] = a["零件清单"]
        return fields

    ref_simple = _simplify_quantitative(ref_analysis)

    kn_block = f"【补充知识】\n{knowledge}\n\n" if knowledge else ""

    prompt_text = f"""{kn_block}{grading_guide}

【参考工程图分析】
{json.dumps(_strip_meta(_p1_fields(ref_analysis)), ensure_ascii=False, indent=2)}

【学生工程图分析】
{json.dumps(_strip_meta(_p1_fields(stu_analysis)), ensure_ascii=False, indent=2)}

【参考图量化数据】
{json.dumps(ref_simple, ensure_ascii=False, indent=2)}

【学生图量化数据】
{json.dumps(_simplify_quantitative(stu_analysis), ensure_ascii=False, indent=2)}

【评分标准】
阶段一（图形相似度）：
{phase1_criteria}

阶段二（量化标注）：
{phase2_criteria}

请严格按以下 JSON 格式输出，不要包含其他文字：
{{
  "phase1_similarity": 85,
  "phase1_comment": "...",
  "phase2_criteria": 80,
  "图样表达": "...",
  "尺寸标注": "...",
  "尺寸公差": "...",
  "表面质量": "...",
  "形位公差": "...",
  "技术要求": "...",
  "phase2_comment": "...",
  "总评": "..."
}}"""

    p1_size = _phase1_max_size()
    p1_qual = _phase1_jpeg_quality()
    stu_thumb = image_to_base64(stu_image_path, max_size=p1_size, quality=p1_qual)
    ref_thumb = image_to_base64(ref_image_path, max_size=p1_size, quality=p1_qual)

    content: list[dict] = [
        {"type": "text", "text": prompt_text},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{stu_thumb}"}},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{ref_thumb}"}},
    ]

    logger.info(f"合并评分中（模型: {model}）…")
    result = _call_and_parse(client, model,
        [{"role": "user", "content": content}],
        _parse_json_response)
    logger.info("合并评分完成")

    # 将分析数据合并进结果（保证 downstream 能看到完整数据）
    for key in ("工程图概述", "基本信息", "几何特征", "尺寸", "几何公差",
                "表面粗糙度", "尺寸公差", "技术要求",
                "各视图信息", "零件清单", "配合关系", "外形尺寸", "装配技术要求"):
        if key in stu_analysis and key not in result:
            result[key] = stu_analysis[key]

    p1 = float(result.get("phase1_similarity", 0))
    p2 = float(result.get("phase2_criteria", 0))
    total = round((p1 * p2) ** 0.5, 1)
    grade = _compute_grade(total)

    result["phase1_similarity"] = p1
    result["phase2_criteria"] = p2
    result["total_score"] = total
    result["grade"] = grade
    result["_model"] = result.get("_model", model)
    return result


def run_two_phase_grading(
    ref_analysis: dict,
    stu_analysis: dict,
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
    ref_analysis/stu_analysis: analyze_merged 的返回结果（新格式）。
    submit 模式传入 stu_image_path；test 模式传入 stu_data + stu_filename。
    """
    p1 = grade_phase1(ref_analysis, stu_analysis, phase1_criteria, ref_image_path,
                      stu_image_path, stu_data=stu_data, stu_filename=stu_filename,
                      knowledge=knowledge)
    p2 = grade_phase2(ref_analysis, stu_analysis, phase2_criteria, knowledge=knowledge)

    # 提取元数据后再合并，避免 p2 覆盖 p1 的 _model 和 _usage
    p1_usage = p1.pop("_usage", None)
    p1_model = p1.pop("_model", None)
    p2_usage = p2.pop("_usage", None)
    p2_model = p2.pop("_model", None)

    merged = {**p1, **p2}

    # 模型名称（两阶段通常一致，取任意一个）
    model = p1_model or p2_model
    if model:
        merged["_model"] = model

    # 按阶段存储用量（前端可按阶段展示）
    if p1_usage:
        merged["_phase1_usage"] = p1_usage
    if p2_usage:
        merged["_phase2_usage"] = p2_usage

    # 合计用量
    if p1_usage and p2_usage:
        merged["_usage"] = {
            "prompt_tokens": p1_usage["prompt_tokens"] + p2_usage["prompt_tokens"],
            "completion_tokens": p1_usage["completion_tokens"] + p2_usage["completion_tokens"],
            "total_tokens": p1_usage["total_tokens"] + p2_usage["total_tokens"],
        }
    elif p1_usage:
        merged["_usage"] = p1_usage
    elif p2_usage:
        merged["_usage"] = p2_usage

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
