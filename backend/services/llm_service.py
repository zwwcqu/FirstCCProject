from __future__ import annotations

import base64
import json
import re
from io import BytesIO
from pathlib import Path

from openai import OpenAI
from PIL import Image

from config import read_settings


_cached_model: str | None = None


def _build_client() -> OpenAI:
    settings = read_settings()
    return OpenAI(
        base_url=settings["llm_api_base"],
        api_key=settings["llm_api_key"],
        timeout=120,
    )


def _get_model() -> str:
    global _cached_model
    settings = read_settings()
    configured = settings.get("llm_model", "").strip()
    if configured:
        _cached_model = configured
        return _cached_model
    # 未指定模型时，每次都从 LM Studio 获取当前缺省模型（不缓存，因为远端可能会自动导入新模型）
    client = _build_client()
    models = client.models.list()
    if models.data:
        return models.data[0].id
    if _cached_model:
        return _cached_model
    raise RuntimeError("无法获取模型列表，请检查大模型服务是否正常运行")


MAX_IMAGE_SIZE = 1600


def _resize_image(img: Image.Image) -> Image.Image:
    w, h = img.size
    longest = max(w, h)
    if longest <= MAX_IMAGE_SIZE:
        return img
    ratio = MAX_IMAGE_SIZE / longest
    new_w, new_h = int(w * ratio), int(h * ratio)
    return img.resize((new_w, new_h), Image.LANCZOS)


def _image_to_base64(path: Path) -> str:
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
            img = img.convert("RGB")
        img = _resize_image(img)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode()
    else:
        raise ValueError(f"不支持的文件格式: {path.suffix}")


def _build_prompt(description: str, phase1_criteria: str, phase2_criteria: str) -> str:
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


def _parse_llm_response(text: str) -> dict:
    text = text.strip()
    # try to find JSON block
    match = re.search(r'\{[\s\S]*"phase1_similarity"[\s\S]*\}', text, re.DOTALL)
    if match:
        text = match.group(0)
    # try to fix trailing commas
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*]', ']', text)
    return json.loads(text)


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
    for threshold, grade in GRADE_THRESHOLDS:
        if total >= threshold:
            return grade
    return "F"


def grade_submission(
    description: str,
    phase1_criteria: str,
    phase2_criteria: str,
    reference_paths: list[Path],
    student_submission_path: Path,
) -> dict:
    client = _build_client()
    model = _get_model()

    prompt_text = _build_prompt(description, phase1_criteria, phase2_criteria)

    content: list[dict] = [{"type": "text", "text": prompt_text}]

    # add reference images (with label)
    if reference_paths:
        content.append({"type": "text", "text": "\n【参考工程图】："})
        for ref_path in reference_paths:
            if ref_path.exists():
                b64 = _image_to_base64(ref_path)
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                })

    # add student submission (with label)
    student_b64 = _image_to_base64(student_submission_path)
    content.append({"type": "text", "text": "\n【学生提交的工程图】："})
    content.append({
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{student_b64}"},
    })

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        temperature=0.1,
        max_tokens=4096,
    )

    raw_text = response.choices[0].message.content or ""
    result = _parse_llm_response(raw_text)

    # compute final grade from two phases
    p1 = float(result.get("phase1_similarity", 0))
    p2 = float(result.get("phase2_criteria", 0))
    total = round(p1 * p2 / 100, 1)
    grade = _compute_grade(total)

    result["grade"] = grade
    result["phase1_similarity"] = p1
    result["phase2_criteria"] = p2
    result["total_score"] = total
    return result
