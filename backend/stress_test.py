#!/usr/bin/env python3
"""
压力测试：模拟 20 个学生并发提交作业+评分。
检测：DXF 队列和 LLM 队列是否阻塞前台服务。
"""

import time, json, threading, shutil, urllib.request, urllib.parse
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

SRC_DXF = Path("/home/ubuntu/CadMarkData/260727-001/student/张三_2024001.dxf")
SRC_ANALYSIS = Path("/home/ubuntu/CadMarkData/260727-001/student/张三_2024001_分析.json")
QID = "260727-001"
BASE = "http://localhost:8000"
TOTAL = 20

results = {"analyze_ok": 0, "analyze_fail": 0, "grade_ok": 0, "grade_fail": 0}
lock = threading.Lock()
ping_data = []
start_time = None


def ts():
    return datetime.now().strftime("%H:%M:%S")


def ping():
    """测前台响应"""
    t0 = time.time()
    try:
        urllib.request.urlopen(f"{BASE}/api/student/questions", timeout=5)
        return time.time() - t0
    except Exception:
        return -1


def monitor(end_time: float):
    """后台持续 ping"""
    while time.time() < end_time:
        p = ping()
        with lock:
            ping_data.append(p)
        time.sleep(0.5)


def setup_student(i: int):
    """为虚拟学生创建文件（模拟提交）"""
    sid = f"STEST{i:04d}"
    name = "压力测试"
    sdir = Path(f"/home/ubuntu/CadMarkData/{QID}/student")
    sdir.mkdir(parents=True, exist_ok=True)
    # 复制 DXF（模拟学生上传）
    dst_dxf = sdir / f"{name}_{sid}.dxf"
    if not dst_dxf.exists():
        shutil.copy2(SRC_DXF, dst_dxf)
    return sid, name


def call_analyze(sid: str, name: str):
    """调用学生分析 API"""
    t0 = time.time()
    try:
        data = urllib.parse.urlencode({"name": name, "student_id": sid, "mode": "submit"}).encode()
        r = urllib.request.Request(f"{BASE}/api/student/analyze/{QID}/start", data=data, method="POST")
        resp = urllib.request.urlopen(r, timeout=10)
        d = json.loads(resp.read())
        if d.get("ok"):
            with lock:
                results["analyze_ok"] += 1
        else:
            with lock:
                results["analyze_fail"] += 1
        return time.time() - t0
    except Exception as e:
        with lock:
            results["analyze_fail"] += 1
        return str(e)


def call_grade(sid: str, name: str):
    """调用学生评分 API"""
    t0 = time.time()
    try:
        data = urllib.parse.urlencode({"name": name, "student_id": sid, "mode": "submit"}).encode()
        r = urllib.request.Request(f"{BASE}/api/student/grade/{QID}", data=data, method="POST")
        resp = urllib.request.urlopen(r, timeout=120)
        d = json.loads(resp.read())
        if d.get("ok"):
            with lock:
                results["grade_ok"] += 1
        else:
            with lock:
                results["grade_fail"] += 1
        return time.time() - t0
    except Exception as e:
        with lock:
            results["grade_fail"] += 1
        return str(e)


# ── 主流程 ──────────────────────────────────────────────────

print(f"{ts()} === 压力测试: {TOTAL} DXF分析 + {TOTAL} LLM评分 ===")
start_time = time.time()

# 1. 准备虚拟学生文件
print(f"\n{ts()} 准备 {TOTAL} 个虚拟学生文件...")
students = []
for i in range(TOTAL):
    students.append(setup_student(i))
print(f"  完成，{len(students)} 个学生")

# 2. 启动监控
mon = threading.Thread(target=monitor, args=(time.time() + 120,), daemon=True)
mon.start()

# 3. 并发触发分析（DXF 串行队列）
print(f"\n{ts()} --- 并发触发 {TOTAL} 个 DXF 分析 ---")
with ThreadPoolExecutor(max_workers=10) as ex:
    futs = [ex.submit(call_analyze, s[0], s[1]) for s in students]
    for i, f in enumerate(as_completed(futs)):
        elapsed = f.result()
        print(f"  [{i+1:2d}] 分析 {elapsed:.2f}s" if isinstance(elapsed, float) else f"  [{i+1:2d}] 分析错误: {elapsed}")

# 等 DXF 队列处理完
print(f"\n{ts()} 等待 DXF 分析完成...")
waited = 0
while waited < 60:
    time.sleep(2); waited += 2
    done = sum(1 for s in students if Path(f"/home/ubuntu/CadMarkData/{QID}/student/{s[1]}_{s[0]}_分析.json").exists())
    print(f"  {done}/{TOTAL} 完成")
    if done >= TOTAL:
        break

# 4. 并发触发评分（LLM 队列，并发 3）
print(f"\n{ts()} --- 并发触发 {TOTAL} 个 LLM 评分 ---")
with ThreadPoolExecutor(max_workers=10) as ex:
    futs = [ex.submit(call_grade, s[0], s[1]) for s in students]
    for i, f in enumerate(as_completed(futs)):
        elapsed = f.result()
        print(f"  [{i+1:2d}] 评分 {elapsed:.2f}s" if isinstance(elapsed, float) else f"  [{i+1:2d}] 评分错误: {elapsed}")

time.sleep(1)
total_time = time.time() - start_time

# 5. 清理
for s in students:
    sid, name = s
    for f in Path(f"/home/ubuntu/CadMarkData/{QID}/student").glob(f"{name}_{sid}*"):
        f.unlink(missing_ok=True)

# 6. 报告
pings = [p for p in ping_data if p is not None]
slow = [p for p in pings if p != -1 and p > 2]
timeout = [p for p in pings if p == -1]

print(f"\n{'='*60}")
print(f"{ts()} === 压力测试报告 ===")
print(f"总耗时: {total_time:.0f}s")
print(f"DXF 分析: {results['analyze_ok']}/{TOTAL} 成功")
print(f"LLM 评分: {results['grade_ok']}/{TOTAL} 成功")
print(f"\n前台响应（共 {len(pings)} 次 ping）:")
if pings:
    ok_pings = [p for p in pings if p != -1]
    print(f"  正常 (<2s): {len([p for p in ok_pings if p <= 2])} 次")
    print(f"  慢 (>2s):   {len(slow)} 次")
    print(f"  超时:      {len(timeout)} 次")
    print(f"  最快: {min(ok_pings):.3f}s  最慢: {max(ok_pings):.3f}s  平均: {sum(ok_pings)/len(ok_pings):.3f}s")
print(f"\n结论: {'⚠️ 有前台阻塞!' if (slow or timeout) else '✓ 前台无阻塞，服务正常'}")
