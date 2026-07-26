"""
阀体批阅管线 — 正确版测试
==========================
使用 201 题目的实际评分标准、补充知识、统一模板。
参考图使用已有缓存，不再重复分析。
"""
import json
import sys
import time
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

from services.llm_service import (
    analyze_merged,
    grade_phase1,
    grade_phase2,
    _simplify_quantitative,
    _compute_grade,
)
from config import get_template

TEST_DIR = Path(__file__).parent
DATA_201 = Path.home() / "CadMarkData" / "201"

# ─── 201 题目的实际文件 ───────────────────────────────
REF_PDF = DATA_201 / "参考工程图.pdf"
STU_PDF = TEST_DIR / "阀体-作业1.pdf"
KNOWLEDGE = (DATA_201 / "补充知识.md").read_text(encoding="utf-8").strip()
PHASE1_CRITERIA = (DATA_201 / "阶段1评分标准.md").read_text(encoding="utf-8").strip()
PHASE2_CRITERIA = (DATA_201 / "阶段2评分标准.md").read_text(encoding="utf-8").strip()

# ─── 使用统一模板 ─────────────────────────────────────
TEMPLATE_TEXT = get_template("零件图识读模板.txt")

timings = {}


def save_json(filename, data):
    clean = {k: v for k, v in data.items() if not k.startswith("_")}
    path = TEST_DIR / filename
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 已保存: {filename}")
    return path


def save_json_raw(filename, data):
    path = TEST_DIR / filename
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ 已保存（含原始字段）: {filename}")
    return path


# ═══════════════════════════════════════════════════════════
#  步骤 1: 学生图工程图识读
# ═══════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"  步骤 1: 学生图工程图识读 (统一模板 + 补充知识)")
print(f"{'='*60}")

t0 = time.time()
stu_analysis = analyze_merged(STU_PDF, TEMPLATE_TEXT, knowledge=KNOWLEDGE)
elapsed = time.time() - t0
timings["1. 学生图识读"] = elapsed
print(f"  ⏱ 耗时: {elapsed:.1f}s")

save_json_raw("01-学生图_分析.json", stu_analysis)
概述 = stu_analysis.get("工程图概述", "")
几何特征 = stu_analysis.get("几何特征", [])
尺寸 = stu_analysis.get("尺寸", [])
print(f"  概述: {len(概述)} 字")
print(f"  几何特征: {len(几何特征)} 个")
print(f"  尺寸: {len(尺寸)} 条")

# 加载缓存的参考图分析
REF_CACHE = DATA_201 / "参考图_分析.json"
if REF_CACHE.exists():
    ref_analysis = json.loads(REF_CACHE.read_text(encoding="utf-8"))
    print(f"\n  参考图缓存: 概述={len(ref_analysis.get('工程图概述',''))}字, 几何特征={len(ref_analysis.get('几何特征',[]))}个, 尺寸={len(ref_analysis.get('尺寸',[]))}条")
else:
    # 兼容旧格式缓存
    old_struct = DATA_201 / "参考图_结构分析.json"
    old_quant = DATA_201 / "参考图_量化分析.json"
    if old_struct.exists() and old_quant.exists():
        ref_analysis = {
            "structure": json.loads(old_struct.read_text(encoding="utf-8")),
            "quantitative": json.loads(old_quant.read_text(encoding="utf-8")),
        }
        print(f"\n  参考图缓存(旧格式): 已加载")
    else:
        print(f"\n  ⚠ 参考图缓存不存在，将重新分析...")
        ref_analysis = analyze_merged(REF_PDF, TEMPLATE_TEXT, knowledge=KNOWLEDGE)
        save_json_raw("参考图_分析.json", ref_analysis)

# ═══════════════════════════════════════════════════════════
#  步骤 2: 阶段一评分
# ═══════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"  步骤 2: 阶段一评分")
print(f"{'='*60}")
t1 = time.time()
p1_result = grade_phase1(
    ref_analysis=ref_analysis,
    stu_analysis=stu_analysis,
    phase1_criteria=PHASE1_CRITERIA,
    ref_image_path=REF_PDF,
    stu_image_path=STU_PDF,
    knowledge=KNOWLEDGE,
)
elapsed = time.time() - t1
timings["2. 阶段一评分"] = elapsed
print(f"  ⏱ 耗时: {elapsed:.1f}s")
save_json_raw("02-阶段一_图形相似度评分.json", p1_result)

# ═══════════════════════════════════════════════════════════
#  步骤 3: 阶段二评分
# ═══════════════════════════════════════════════════════════
print(f"\n{'='*60}")
print(f"  步骤 3: 阶段二评分")
print(f"{'='*60}")

ref_simple = _simplify_quantitative(ref_analysis)
stu_simple = _simplify_quantitative(stu_analysis)
save_json("03-参考图_量化精简.json", ref_simple)
save_json("04-学生图_量化精简.json", stu_simple)

t2 = time.time()
p2_result = grade_phase2(
    ref_analysis=ref_analysis,
    stu_analysis=stu_analysis,
    phase2_criteria=PHASE2_CRITERIA,
    knowledge=KNOWLEDGE,
)
elapsed = time.time() - t2
timings["3. 阶段二评分"] = elapsed
print(f"  ⏱ 耗时: {elapsed:.1f}s")
save_json_raw("05-阶段二_量化标注评分.json", p2_result)

# ═══════════════════════════════════════════════════════════
#  步骤 4: 计算总分
# ═══════════════════════════════════════════════════════════
p1_score = float(p1_result.get("phase1_similarity", 0))
p2_score = float(p2_result.get("phase2_criteria", 0))
total = round((p1_score * p2_score) ** 0.5, 1)
grade = _compute_grade(total)

print(f"\n{'='*60}")
print(f"  最终结果")
print(f"{'='*60}")
print(f"  阶段一（图形相似度）: {p1_score}")
print(f"  阶段二（量化标注）:   {p2_score}")
print(f"  总分 (几何平均):     {total}")
print(f"  等级:                {grade}")

# ═══════════════════════════════════════════════════════════
#  汇总
# ═══════════════════════════════════════════════════════════
total_time = sum(timings.values())
print(f"\n  耗时汇总:")
for name, t in timings.items():
    print(f"    {name}: {t:.1f}s")
print(f"    总耗时: {total_time:.1f}s")

summary = {
    "test_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    "question": "201 - 阀体零件图",
    "templates_used": {
        "analysis": "零件图识读模板.txt（统一模板）",
        "phase1_criteria": "201/阶段1评分标准.md",
        "phase2_criteria": "201/阶段2评分标准.md",
        "knowledge": "201/补充知识.md",
    },
    "scores": {"phase1_similarity": p1_score, "phase2_criteria": p2_score, "total_score": total, "grade": grade},
    "timings": timings,
    "total_time_s": round(total_time, 1),
}
save_json("06-测试汇总.json", summary)

print(f"\n  ✓ 完成。所有文件保存在: {TEST_DIR}")
