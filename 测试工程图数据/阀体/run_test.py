"""
阀体工程图批阅管线完整测试
=============================
运行完整的三阶段评分流程，保存所有中间 JSON，记录模型调用时间。
"""
import json
import sys
import time
import logging
from pathlib import Path

# 加 backend 到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

# 日志配置
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

from config import CONFIG_DIR, get_prompt_templates, get_template
from services.llm_service import (
    analyze_merged,
    analyze_and_grade,
    _simplify_quantitative,
    _compute_grade,
)

# ─── 目录和文件 ─────────────────────────────────────
TEST_DIR = Path(__file__).parent
REF_PDF = TEST_DIR / "阀体 参考答案.pdf"
STU_PDF = TEST_DIR / "阀体-作业1.pdf"

# 统一模板
TEMPLATE_TEXT = get_template("零件图识读模板.txt")

# 评分标准（简单版，因为没有创建正式题目）
PHASE1_CRITERIA = "对比学生图和参考图的结构相似度，从视图完整性、特征完整性、画图规范性三方面评估，给出0-100分。"
PHASE2_CRITERIA = "对比量化标注：1. 尺寸匹配率占40% 2. 公差匹配率占20% 3. 粗糙度匹配率占20% 4. 形位公差匹配率占10% 5. 技术要求相似度占10%。给出0-100分。"

results = {}
timings = {}


def save_json(filename: str, data: dict):
    """保存 JSON 到测试目录"""
    path = TEST_DIR / filename
    # 去掉内部字段
    clean = {k: v for k, v in data.items() if not k.startswith("_")}
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 已保存: {filename}")
    return path


def step(name: str):
    """开始一个步骤"""
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")
    return time.time()


def done(name: str, start: float):
    """记录步骤耗时"""
    elapsed = time.time() - start
    timings[name] = elapsed
    print(f"  ⏱ 耗时: {elapsed:.1f}s")
    return elapsed


# ═══════════════════════════════════════════════════════════
#  步骤 1: 参考图合并分析 (结构 + 量化)
# ═══════════════════════════════════════════════════════════
t0 = step("步骤 1a: 参考图工程图识读 (analyze_merged)")
ref_merged = analyze_merged(REF_PDF, TEMPLATE_TEXT)
done("1a. 参考图合并分析", t0)
save_json("01-参考图_分析.json", ref_merged)
results["ref_analysis"] = ref_merged
results["ref_model"] = ref_merged.get("_model", "")
results["ref_usage"] = ref_merged.get("_usage", {})

# ═══════════════════════════════════════════════════════════
#  步骤 2: 学生图合并分析 (结构 + 量化)
# ═══════════════════════════════════════════════════════════
t1 = step("步骤 1b: 学生图工程图识读 (analyze_merged)")
stu_merged = analyze_merged(STU_PDF, TEMPLATE_TEXT)
done("1b. 学生图合并分析", t1)
save_json("02-学生图_分析.json", stu_merged)
results["stu_analysis"] = stu_merged
results["stu_model"] = stu_merged.get("_model", "")
results["stu_usage"] = stu_merged.get("_usage", {})

# ═══════════════════════════════════════════════════════════
#  步骤 3: 阶段一评分（视觉对比）
# ═══════════════════════════════════════════════════════════
t2 = step("步骤 2: 阶段一评分 - 图形相似度 (grade_phase1)")
p1_result = grade_phase1(
    ref_analysis=results["ref_analysis"],
    stu_analysis=results["stu_analysis"],
    phase1_criteria=PHASE1_CRITERIA,
    ref_image_path=REF_PDF,
    stu_image_path=STU_PDF,
)
p1_time = done("2. 阶段一评分", t2)
save_json("03-阶段一_图形相似度评分.json", p1_result)
results["phase1"] = p1_result
results["p1_model"] = p1_result.get("_model", "")
results["p1_usage"] = p1_result.get("_usage", {})

# ═══════════════════════════════════════════════════════════
#  步骤 4: 阶段二评分（量化数据对比）
# ═══════════════════════════════════════════════════════════
t3 = step("步骤 3: 阶段二评分 - 量化标注 (grade_phase2)")

# 安全版精简函数（容错 LLM 不按格式返回字符串等异常）
def safe_simplify(data: dict) -> dict:
    try:
        return _simplify_quantitative(data)
    except Exception as e:
        print(f"  ⚠ _simplify_quantitative 异常: {e}，使用原始数据")
        return data

ref_simple = safe_simplify(results["ref_analysis"])
stu_simple = safe_simplify(results["stu_analysis"])
save_json("04-参考图_量化精简.json", ref_simple)
save_json("05-学生图_量化精简.json", stu_simple)

p2_result = grade_phase2(
    ref_analysis=results["ref_analysis"],
    stu_analysis=results["stu_analysis"],
    phase2_criteria=PHASE2_CRITERIA,
)
p2_time = done("3. 阶段二评分", t3)
save_json("06-阶段二_量化标注评分.json", p2_result)
results["phase2"] = p2_result
results["p2_model"] = p2_result.get("_model", "")
results["p2_usage"] = p2_result.get("_usage", {})

# ═══════════════════════════════════════════════════════════
#  步骤 5: 计算总分和等级
# ═══════════════════════════════════════════════════════════
p1_score = float(p1_result.get("phase1_similarity", 0))
p2_score = float(p2_result.get("phase2_criteria", 0))
total = round((p1_score * p2_score) ** 0.5, 1)
grade = _compute_grade(total)

results["final"] = {
    "phase1_similarity": p1_score,
    "phase2_criteria": p2_score,
    "total_score": total,
    "grade": grade,
}

# ═══════════════════════════════════════════════════════════
#  汇总报告
# ═══════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"  最终结果")
print(f"{'='*60}")
print(f"  阶段一（图形相似度）: {p1_score}")
print(f"  阶段二（量化标注）:   {p2_score}")
print(f"  总分 (几何平均):     {total}")
print(f"  等级:                {grade}")

# 汇总 timing
total_tokens = 0
total_time = 0
for name, elapsed in timings.items():
    total_time += elapsed
    print(f"  {name}: {elapsed:.1f}s")

print(f"\n  总耗时: {total_time:.1f}s")

# 汇总 token
all_usages = [
    ("参考图分析", results.get("ref_usage", {})),
    ("学生图分析", results.get("stu_usage", {})),
    ("阶段一评分", results.get("p1_usage", {})),
    ("阶段二评分", results.get("p2_usage", {})),
]
for label, u in all_usages:
    if u:
        print(f"  {label} tokens: prompt={u.get('prompt_tokens','?')} completion={u.get('completion_tokens','?')} total={u.get('total_tokens','?')}")
        total_tokens += u.get("total_tokens", 0) or 0

# 保存最终结果
save_json("07-最终评分汇总.json", results["final"])

# 保存完整汇总
summary = {
    "test_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    "reference_file": str(REF_PDF.name),
    "student_file": str(STU_PDF.name),
    "timings": timings,
    "total_time_s": round(total_time, 1),
    "llm_calls": 4,
    "models_used": {
        "reference_analysis": results.get("ref_model", ""),
        "student_analysis": results.get("stu_model", ""),
        "phase1": results.get("p1_model", ""),
        "phase2": results.get("p2_model", ""),
    },
    "token_usage": all_usages,
    "total_tokens": total_tokens,
    "scores": results["final"],
    "prompt_templates_used": {
        "structure_analysis": "settings.json (len={})".format(len(get_prompt_templates().get("structure_analysis", ""))),
        "structure_analysis_student": "settings.json (len={})".format(len(get_prompt_templates().get("structure_analysis_student", ""))),
        "quantitative_analysis": "settings.json (len={})".format(len(get_prompt_templates().get("quantitative_analysis", ""))),
        "quantitative_analysis_student": "settings.json (len={})".format(len(get_prompt_templates().get("quantitative_analysis_student", ""))),
    },
}
save_json("08-测试汇总.json", summary)

print(f"\n  ✓ 所有文件已保存到: {TEST_DIR}")
print(f"  ✓ 共 {len(timings)} 次 LLM 调用，总耗时 {total_time:.1f}s，总 token {total_tokens}")
