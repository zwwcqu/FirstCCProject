"""
DXF 工程图处理服务。

功能：
- DXF 文件解析：使用 ezdxf 提取结构化数据
- 预处理：MLINE → LINE, POLYLINE/LWPOLYLINE → LINE 线段
- 数据提取：图层（线型、颜色、线宽）、实体轮廓（线、圆、圆弧）、
  尺寸列表、hatch 数据、中心线数据、文本
- DXF 预览渲染：通过 matplotlib 渲染为 PNG

提取数据格式（独立于 PDF/图片流程的 LLM 分析格式）：
  entities: {lines, circles, arcs, hatches, centerlines}
  dimensions: [...]
  texts: [...]
  layers: {name: {color, linetype, lineweight}}
  entity_counts: {...}
  bounds: {min_x, max_x, min_y, max_y}
"""

from __future__ import annotations

import logging
import math
from io import BytesIO
from pathlib import Path

logger = logging.getLogger(__name__)

# ezdxf 是可选依赖，在首次使用时延迟导入
_ezdxf_available = False
_ezdxf_import_error: str | None = None

try:
    import ezdxf
    from ezdxf.entities import (
        Insert,  # noqa: F401 — 用于未来块参照展开
    )
    _ezdxf_available = True
except ImportError as e:
    _ezdxf_import_error = str(e)


def _check_ezdxf() -> None:
    """确保 ezdxf 已安装，否则抛明确错误"""
    if not _ezdxf_available:
        raise RuntimeError(
            f"ezdxf 未安装，无法处理 DXF 文件。请运行: pip install ezdxf\n"
            f"原始错误: {_ezdxf_import_error}"
        )


# ── DXF 预览渲染参数 ──────────────────────────────────────

def _get_dxf_params() -> dict:
    """读取 DXF 处理参数（优先 settings，缺失使用默认值）"""
    from config import read_settings
    defaults = {
        "preview_max_size": 768,       # 长边像素数（图纸 ISO/GB mm）
        "preview_bg": "#FFFFFF",
        "preview_fg": "#000000",
        "preview_linewidth_scale": 1.0,
    }
    stored = read_settings().get("dxf_params", {}) or {}
    return {**defaults, **{k: v for k, v in stored.items() if not k.startswith("_")}}


# ── 常量 ───────────────────────────────────────────────────

# 中心线图层关键词（不区分大小写）
_CENTERLINE_LAYER_KEYWORDS = [
    "中心线", "center", "centerline", "c-line", "轴线",
    "点划线", "点画线", " dash", "dot dash",
]

# 中心线线型关键词（不区分大小写）
_CENTERLINE_LINETYPE_KEYWORDS = [
    "center", "center2", "centerx2", "dashdot", "dash dot",
    "acad_iso08", "acad_iso10", "acad_iso12",  # AutoCAD ISO 中心线线型
]


def _is_centerline_layer(layer_name: str) -> bool:
    """判断图层名是否为中心线相关"""
    lower = layer_name.lower().strip()
    return any(kw in lower for kw in _CENTERLINE_LAYER_KEYWORDS)


def _is_centerline_linetype(linetype: str) -> bool:
    """判断线型是否为中心线类型"""
    lower = linetype.lower().strip()
    return any(kw in lower for kw in _CENTERLINE_LINETYPE_KEYWORDS)


# ── 预处理：分解多线和多段线 ──────────────────────────────

def _explode_polyline_2d(entity) -> list[dict]:
    """将 LWPOLYLINE / POLYLINE 分解为多段 LINE。
    对圆弧段（bulge）取中间点近似，使弧段由多段直线拟合。
    """
    segments: list[dict] = []
    layer = entity.dxf.layer
    linetype = entity.dxf.linetype or ""
    color = entity.dxf.color

    try:
        # OCS → WCS 变换
        ocs = entity.ocs()
    except Exception:
        ocs = None

    # 处理 LWPOLYLINE (有 .get_points("xyb") 返回 (x, y, bulge) 列表)
    bulge_points = None
    try:
        bulge_points = list(entity.get_points("xyb"))
    except Exception:
        pass

    if bulge_points and len(bulge_points) >= 2:
        # LWPOLYLINE with bulge support
        for i in range(len(bulge_points) - 1):
            x1, y1, bulge = bulge_points[i]
            x2, y2, b2 = bulge_points[i + 1]

            if ocs:
                w1 = ocs.to_wcs((x1, y1, 0))
                w2 = ocs.to_wcs((x2, y2, 0))
                p1 = (w1.x, w1.y)
                p2 = (w2.x, w2.y)
            else:
                p1 = (x1, y1)
                p2 = (x2, y2)

            bulge_val = bulge  # b2 is the next point's bulge, not used

            if abs(bulge_val) < 1e-10:
                # 直段
                segments.append({
                    "start": [round(p1[0], 4), round(p1[1], 4)],
                    "end": [round(p2[0], 4), round(p2[1], 4)],
                    "layer": layer, "linetype": linetype, "color": color,
                    "type": "line",
                })
            else:
                # 弧段 → 用多段直线近似
                chord_vec = (p2[0] - p1[0], p2[1] - p1[1])
                chord_len = math.hypot(chord_vec[0], chord_vec[1])
                if chord_len < 1e-6:
                    continue
                # 计算弧参数
                sagitta = chord_len * abs(bulge_val) / 2
                radius = (chord_len / 2) * (1 + bulge_val ** 2) / (2 * abs(bulge_val)) if abs(bulge_val) > 1e-10 else chord_len * 1000
                mid_chord = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
                # 圆心方向垂直于弦
                perp = (-chord_vec[1], chord_vec[0])
                perp_len = math.hypot(perp[0], perp[1])
                if perp_len < 1e-6:
                    continue
                perp_unit = (perp[0] / perp_len, perp[1] / perp_len)
                center_offset = sagitta * (1 if bulge_val > 0 else -1)
                center = (mid_chord[0] + perp_unit[0] * center_offset,
                          mid_chord[1] + perp_unit[1] * center_offset)

                angle1 = math.atan2(p1[1] - center[1], p1[0] - center[0])
                angle2 = math.atan2(p2[1] - center[1], p2[0] - center[0])
                # 标准化角度差
                if bulge_val > 0:
                    while angle2 <= angle1:
                        angle2 += 2 * math.pi
                else:
                    while angle2 >= angle1:
                        angle2 -= 2 * math.pi
                angle_span = abs(angle2 - angle1)
                # 每段约 5 度
                num_segments = max(2, int(angle_span / (5 * math.pi / 180)) + 1)
                prev_pt = p1
                for j in range(1, num_segments + 1):
                    t = j / num_segments
                    a = angle1 + (angle2 - angle1) * t
                    pt = (round(center[0] + radius * math.cos(a), 4),
                          round(center[1] + radius * math.sin(a), 4))
                    segments.append({
                        "start": [round(prev_pt[0], 4), round(prev_pt[1], 4)],
                        "end": [round(pt[0], 4), round(pt[1], 4)],
                        "layer": layer, "linetype": linetype, "color": color,
                        "type": "line",
                    })
                    prev_pt = pt
        return segments

    # POLYLINE (3D) 或简单 LWPOLYLINE 无 bulge
    points = None
    try:
        points = list(entity.points())
    except Exception:
        pass

    if points and len(points) >= 2:
        for i in range(len(points) - 1):
            p1 = points[i]
            p2 = points[i + 1]
            segments.append({
                "start": [round(p1[0], 4), round(p1[1], 4)],
                "end": [round(p2[0], 4), round(p2[1], 4)],
                "layer": layer, "linetype": linetype, "color": color,
                "type": "line",
            })
        return segments

    # 如果都失败，把 entity 作为单个整体保留（不考虑 bulge 的退化情况）
    return segments


def _explode_mline(entity) -> list[dict]:
    """将 MLINE 分解为多条 LINE（每根平行线一条 LINE 段）。
    如果 ezdxf 版本支持，使用 .get_line_segments() 获取各线段。
    """
    segments: list[dict] = []
    layer = entity.dxf.layer
    linetype = entity.dxf.linetype or ""
    color = entity.dxf.color

    try:
        # ezdxf MLINE 提供 .lines 属性或可通过遍历获取
        # 不同版本 API 略有差异，先尝试 .lines
        if hasattr(entity, 'lines'):
            for line in entity.lines:
                if hasattr(line, 'dxf'):
                    segments.append({
                        "start": [round(line.dxf.start[0], 4), round(line.dxf.start[1], 4)],
                        "end": [round(line.dxf.end[0], 4), round(line.dxf.end[1], 4)],
                        "layer": layer, "linetype": linetype, "color": color,
                        "type": "line",
                    })
                else:
                    # line 可能是 (start, end) 元组
                    s, e = line
                    segments.append({
                        "start": [round(s[0], 4), round(s[1], 4)],
                        "end": [round(e[0], 4), round(e[1], 4)],
                        "layer": layer, "linetype": linetype, "color": color,
                        "type": "line",
                    })
    except Exception:
        pass

    # 如果上述方式未产生线段，尝试遍历虚拟 entities
    if not segments:
        try:
            for virt_entity in entity.virtual_entities():
                if virt_entity.dxftype() == "LINE":
                    segs = _extract_line(virt_entity)
                    segments.extend(segs)
        except Exception:
            pass

    return segments


def _extract_line(entity) -> list[dict]:
    """提取 LINE 实体"""
    layer = entity.dxf.layer
    linetype = entity.dxf.linetype or ""
    color = entity.dxf.color
    return [{
        "start": [round(entity.dxf.start[0], 4), round(entity.dxf.start[1], 4)],
        "end": [round(entity.dxf.end[0], 4), round(entity.dxf.end[1], 4)],
        "layer": layer,
        "linetype": linetype,
        "color": color,
        "type": "line",
    }]


def _extract_circle(entity) -> list[dict]:
    """提取 CIRCLE 实体"""
    layer = entity.dxf.layer
    linetype = entity.dxf.linetype or ""
    color = entity.dxf.color
    return [{
        "center": [round(entity.dxf.center[0], 4), round(entity.dxf.center[1], 4)],
        "radius": round(entity.dxf.radius, 4),
        "layer": layer,
        "linetype": linetype,
        "color": color,
        "type": "circle",
    }]


def _extract_arc(entity) -> list[dict]:
    """提取 ARC 实体"""
    layer = entity.dxf.layer
    linetype = entity.dxf.linetype or ""
    color = entity.dxf.color
    return [{
        "center": [round(entity.dxf.center[0], 4), round(entity.dxf.center[1], 4)],
        "radius": round(entity.dxf.radius, 4),
        "start_angle": round(entity.dxf.start_angle, 4),
        "end_angle": round(entity.dxf.end_angle, 4),
        "layer": layer,
        "linetype": linetype,
        "color": color,
        "type": "arc",
    }]


def _clean_dimension_text(raw_text: str, measurement: float | None = None) -> str:
    """清理标注文字：去除 MText 格式代码，将 <> 替换为实际测量值。
    单独 <> 时返回纯测量值字符串；如 "3x<>" 且测量值为 5.0 则返回 "3x5.0"。
    """
    import re
    if not raw_text:
        return ""
    # 去除 MText 格式标记 {\\f...; ...}
    cleaned = re.sub(r'\\[fF][^;]*;', '', raw_text)
    cleaned = re.sub(r'[{}]', '', cleaned)
    cleaned = re.sub(r'\\P', ' ', cleaned)
    cleaned = cleaned.strip()
    # 替换 <> 为实际测量值
    if "<>" in cleaned and measurement is not None:
        meas_str = str(round(measurement, 2)) if measurement != int(measurement) else str(int(measurement))
        cleaned = cleaned.replace("<>", meas_str)
    elif cleaned == "<>" and measurement is not None:
        meas_str = str(round(measurement, 2)) if measurement != int(measurement) else str(int(measurement))
        cleaned = meas_str
    return cleaned


def _extract_dimension(entity) -> dict | None:
    """提取标注实体，支持线性/对齐/角度/直径/半径/坐标标注。

    使用 ezdxf 的 dimtype 标志位（低 4 位）判断标注类型，
    通过 entity.get_measurement() 获取测量值（由 ezdxf 从 defpoints 计算）。
    """
    try:
        layer = entity.dxf.layer
        color = entity.dxf.color

        # ── 标注类型：dimtype 低 4 位 ──────────────────────
        raw_dimtype = entity.dxf.dimtype
        base_type = raw_dimtype & 0x0F  # 位 0-3 = 标注类型
        _DIM_TYPE_NAMES = {
            0: "linear",       # Rotated / horizontal / vertical
            1: "aligned",
            2: "angular",      # 2-line angular
            3: "diameter",
            4: "radius",
            5: "angular_3pt",  # 3-point angular
            6: "ordinate",
        }
        dim_type_name = _DIM_TYPE_NAMES.get(base_type, f"unknown_{base_type}")

        # ── 测量值：ezdxf 从 defpoints 自动计算 ────────────
        measurement = None
        try:
            measurement = entity.get_measurement()
        except Exception:
            pass

        # ── 标注文字：去除 MText 格式 ──────────────────────
        raw_text = ""
        try:
            raw_text = entity.dxf.text or ""
        except Exception:
            pass
        text_override = _clean_dimension_text(raw_text, measurement)

        # ── 几何定义点 ─────────────────────────────────────
        defpoints: dict[str, list[float]] = {}
        for attr_name in ("defpoint", "defpoint2", "defpoint3", "defpoint4"):
            try:
                pt = getattr(entity.dxf, attr_name)
                # 跳过零向量（该 defpoint 未使用）
                if pt is not None and not (abs(pt[0]) < 0.0001 and abs(pt[1]) < 0.0001 and abs(pt[2]) < 0.0001):
                    defpoints[attr_name] = [round(pt[0], 4), round(pt[1], 4)]
            except Exception:
                pass

        # ── 文字位置 ───────────────────────────────────────
        text_position = None
        try:
            tmp = entity.dxf.text_midpoint
            if tmp is not None and not (abs(tmp[0]) < 0.0001 and abs(tmp[1]) < 0.0001 and abs(tmp[2]) < 0.0001):
                text_position = [round(tmp[0], 4), round(tmp[1], 4)]
        except Exception:
            pass

        result: dict = {
            "type": dim_type_name,
            "layer": layer,
            "color": color,
            "measurement": round(float(measurement), 4) if measurement is not None else None,
            "text": text_override,
            "defpoints": defpoints,
            "text_position": text_position,
        }

        # ── 直径/半径额外字段 ──────────────────────────────
        if base_type in (3, 4):
            try:
                ll = entity.dxf.leader_length
                if ll is not None:
                    result["leader_length"] = round(float(ll), 4)
            except Exception:
                pass

        # ── 坐标标注方向 ───────────────────────────────────
        if base_type == 6:
            try:
                octype = entity.dxf.ordinate_type
                result["ordinate_type"] = "x" if octype == 0 else "y"
            except Exception:
                pass

        return result
    except Exception as e:
        logger.debug(f"提取标注失败: {e}")
        return None


def _extract_text(entity) -> dict | None:
    """提取 TEXT / MTEXT 实体"""
    try:
        content = ""
        try:
            content = entity.dxf.text or ""
        except Exception:
            pass
        try:
            # 清理 MTEXT 格式化字符
            cleaned = str(content)
            # 去掉 MText 格式标记 {\f...;...}
            import re
            cleaned = re.sub(r'\\[fF][^;]*;', '', cleaned)
            cleaned = re.sub(r'\{|\}', '', cleaned)
            cleaned = re.sub(r'\\P', '\n', cleaned)
            cleaned = cleaned.strip()
        except Exception:
            cleaned = str(content)

        if not cleaned:
            return None

        # 获取插入点
        try:
            insert = entity.dxf.insert
            position = [round(insert[0], 4), round(insert[1], 4)]
        except Exception:
            position = [0, 0]

        height = 2.5
        try:
            height = round(float(entity.dxf.height), 2)
        except Exception:
            pass

        return {
            "content": cleaned,
            "position": position,
            "height": height,
            "layer": entity.dxf.layer,
            "color": entity.dxf.color,
            "entity_type": entity.dxftype(),  # "TEXT" or "MTEXT"
        }
    except Exception as e:
        logger.debug(f"提取文本失败: {e}")
        return None


def _extract_hatch(entity) -> dict | None:
    """提取 HATCH 实体（填充图案名称 + 边界路径）"""
    try:
        pattern_name = ""
        try:
            pattern_name = entity.dxf.pattern_name or ""
        except Exception:
            pass

        # 获取边界路径（简化：只提取路径中的关键点）
        boundary_points = []
        try:
            for path in entity.paths:
                if hasattr(path, 'vertices'):
                    pts = []
                    for v in path.vertices:
                        pts.append([round(v[0], 4), round(v[1], 4)])
                    if pts and len(pts) >= 3:
                        boundary_points.append(pts)
                elif hasattr(path, 'edges'):
                    for edge in path.edges:
                        if hasattr(edge, 'start') and hasattr(edge, 'end'):
                            boundary_points.append([
                                [round(edge.start[0], 4), round(edge.start[1], 4)],
                                [round(edge.end[0], 4), round(edge.end[1], 4)],
                            ])
        except Exception:
            pass

        return {
            "pattern": pattern_name or "SOLID",
            "layer": entity.dxf.layer,
            "color": entity.dxf.color,
            "boundary": boundary_points,
        }
    except Exception as e:
        logger.debug(f"提取 hatch 失败: {e}")
        return None


# ── 图层信息提取 ──────────────────────────────────────────

def _extract_layers(doc) -> dict:
    """提取所有图层信息：线型、颜色、线宽"""
    layers = {}
    for layer in doc.layers:
        name = layer.dxf.name
        info = {
            "color": layer.dxf.color,
            "linetype": str(layer.dxf.linetype or ""),
            "lineweight": _decode_lineweight(getattr(layer.dxf, 'lineweight', -1)),
        }
        # 如果图层为 OFF/FROZEN，标记
        if hasattr(layer, 'is_off') and layer.is_off():
            info["off"] = True
        if hasattr(layer, 'is_frozen') and layer.is_frozen():
            info["frozen"] = True
        layers[name] = info
    return layers


def _decode_lineweight(lw: int) -> float:
    """将 DXF 线宽枚举值解码为 mm"""
    # DXF 线宽枚举: -3=default, -2=byblock, -1=bylayer, 0=0.00mm,
    # 值 = mm * 100
    if lw <= 0:
        return 0.0
    return round(lw / 100.0, 2)


def _compute_bounds(all_entities: dict) -> dict:
    """计算整体包围盒"""
    xs, ys = [], []
    for line_data in all_entities.get("lines", []):
        for pt in (line_data["start"], line_data["end"]):
            xs.append(pt[0])
            ys.append(pt[1])
    for circle in all_entities.get("circles", []):
        cx, cy = circle["center"]
        r = circle["radius"]
        xs.extend([cx - r, cx + r])
        ys.extend([cy - r, cy + r])
    for arc in all_entities.get("arcs", []):
        cx, cy = arc["center"]
        r = arc["radius"]
        xs.extend([cx - r, cx + r])
        ys.extend([cy - r, cy + r])
    for cl in all_entities.get("centerlines", []):
        if "start" in cl:
            xs.extend([cl["start"][0], cl["end"][0]])
            ys.extend([cl["start"][1], cl["end"][1]])

    if not xs or not ys:
        return {"min_x": 0, "max_x": 1, "min_y": 0, "max_y": 1}

    return {
        "min_x": round(min(xs), 4),
        "max_x": round(max(xs), 4),
        "min_y": round(min(ys), 4),
        "max_y": round(max(ys), 4),
    }


# ── 核心提取函数 ──────────────────────────────────────────

def _build_extraction_result(
    lines: list[dict],
    circles: list[dict],
    arcs: list[dict],
    hatches: list[dict],
    centerlines: list[dict],
    dimensions: list[dict],
    texts: list[dict],
    layers: dict,
) -> dict:
    """组装最终提取结果"""
    all_lines = list(lines)
    all_circles = list(circles)
    all_arcs = list(arcs)

    entities = {
        "lines": all_lines,
        "circles": all_circles,
        "arcs": all_arcs,
        "hatches": hatches,
        "centerlines": centerlines,
    }

    result = {
        "entities": entities,
        "dimensions": dimensions,
        "texts": texts,
        "layers": layers,
        "entity_counts": {
            "lines": len(all_lines),
            "circles": len(all_circles),
            "arcs": len(all_arcs),
            "hatches": len(hatches),
            "centerlines": len(centerlines),
            "dimensions": len(dimensions),
            "texts": len(texts),
        },
        "bounds": _compute_bounds(entities),
    }
    return result


def extract_dxf(filepath: Path) -> dict:
    """
    解析 DXF 文件，提取全部结构化数据。

    返回 dict:
        entities: {lines, circles, arcs, hatches, centerlines}
        dimensions: [...]
        texts: [...]
        layers: {name: {color, linetype, lineweight}}
        entity_counts: {...}
        bounds: {min_x, max_x, min_y, max_y}
    """
    _check_ezdxf()
    logger.info(f"开始解析 DXF: {filepath}")

    doc = ezdxf.readfile(str(filepath))
    msp = doc.modelspace()

    # 提取图层信息
    layers = _extract_layers(doc)

    # 分类收集
    lines: list[dict] = []
    circles: list[dict] = []
    arcs: list[dict] = []
    hatches: list[dict] = []
    centerlines: list[dict] = []
    dimensions: list[dict] = []
    texts: list[dict] = []

    # 遍历所有 modelspace 实体
    for entity in msp:
        dxftype = entity.dxftype()

        if dxftype == "LINE":
            segs = _extract_line(entity)
            # 判断是否为中心线
            layer_name = entity.dxf.layer
            ltype = entity.dxf.linetype or ""
            if _is_centerline_layer(layer_name) or _is_centerline_linetype(ltype):
                centerlines.extend(segs)
            else:
                lines.extend(segs)

        elif dxftype == "CIRCLE":
            segs = _extract_circle(entity)
            layer_name = entity.dxf.layer
            ltype = entity.dxf.linetype or ""
            if _is_centerline_layer(layer_name) or _is_centerline_linetype(ltype):
                for s in segs:
                    s["type"] = "centerline_circle"
                centerlines.extend(segs)
            else:
                arcs_for_c = segs  # circles go to circles list
                circles.extend(segs)

        elif dxftype == "ARC":
            segs = _extract_arc(entity)
            layer_name = entity.dxf.layer
            ltype = entity.dxf.linetype or ""
            if _is_centerline_layer(layer_name) or _is_centerline_linetype(ltype):
                for s in segs:
                    s["type"] = "centerline_arc"
                centerlines.extend(segs)
            else:
                arcs.extend(segs)

        elif dxftype in ("LWPOLYLINE", "POLYLINE"):
            segs = _explode_polyline_2d(entity)
            layer_name = entity.dxf.layer
            ltype = entity.dxf.linetype or ""
            if _is_centerline_layer(layer_name) or _is_centerline_linetype(ltype):
                centerlines.extend(segs)
            else:
                lines.extend(segs)

        elif dxftype == "MLINE":
            segs = _explode_mline(entity)
            layer_name = entity.dxf.layer
            ltype = entity.dxf.linetype or ""
            if _is_centerline_layer(layer_name) or _is_centerline_linetype(ltype):
                centerlines.extend(segs)
            else:
                lines.extend(segs)

        elif dxftype == "DIMENSION":
            dim_data = _extract_dimension(entity)
            if dim_data:
                dimensions.append(dim_data)

        elif dxftype in ("TEXT", "MTEXT"):
            text_data = _extract_text(entity)
            if text_data:
                texts.append(text_data)

        elif dxftype == "HATCH":
            hatch_data = _extract_hatch(entity)
            if hatch_data:
                hatches.append(hatch_data)

        elif dxftype == "ELLIPSE":
            # 椭圆 → 提取为中心点和两轴半径（近似为 arcs 类别）
            try:
                center = entity.dxf.center
                major = entity.dxf.major_axis
                ratio = entity.dxf.ratio
                # 分解为多段 LINE 近似
                ellipse_segs = _explode_ellipse_to_lines(entity)
                layer_name = entity.dxf.layer
                ltype = entity.dxf.linetype or ""
                if _is_centerline_layer(layer_name) or _is_centerline_linetype(ltype):
                    centerlines.extend(ellipse_segs)
                else:
                    lines.extend(ellipse_segs)
            except Exception as e:
                logger.debug(f"提取椭圆失败: {e}")

        elif dxftype == "SPLINE":
            # 样条曲线 → 采样为 LINE 段
            try:
                spline_segs = _explode_spline_to_lines(entity)
                layer_name = entity.dxf.layer
                ltype = entity.dxf.linetype or ""
                if _is_centerline_layer(layer_name) or _is_centerline_linetype(ltype):
                    centerlines.extend(spline_segs)
                else:
                    lines.extend(spline_segs)
            except Exception as e:
                logger.debug(f"提取样条曲线失败: {e}")

        elif dxftype == "INSERT":
            # 块参照 → 展开块内实体
            try:
                block_segs = _explode_insert(entity, doc)
                for seg in block_segs:
                    if seg["type"] == "line":
                        layer_name = seg.get("layer", "")
                        ltype = seg.get("linetype", "")
                        if _is_centerline_layer(layer_name) or _is_centerline_linetype(ltype):
                            seg["type"] = "centerline_line"
                            centerlines.append(seg)
                        else:
                            lines.append(seg)
                    elif seg["type"] == "circle":
                        circles.append(seg)
                    elif seg["type"] == "arc":
                        arcs.append(seg)
            except Exception as e:
                logger.debug(f"展开块参照失败: {e}")

    result = _build_extraction_result(
        lines, circles, arcs, hatches, centerlines,
        dimensions, texts, layers,
    )
    logger.info(
        f"DXF 解析完成: {result['entity_counts']['lines']} 线, "
        f"{result['entity_counts']['circles']} 圆, "
        f"{result['entity_counts']['arcs']} 弧, "
        f"{result['entity_counts']['hatches']} 填充, "
        f"{result['entity_counts']['centerlines']} 中心线, "
        f"{result['entity_counts']['dimensions']} 标注, "
        f"{result['entity_counts']['texts']} 文本"
    )
    return result


# ── ELIPSE / SPLINE / INSERT 展开辅助 ─────────────────────

def _explode_ellipse_to_lines(entity) -> list[dict]:
    """将椭圆分解为多段 LINE（64 段近似）"""
    import math as _math
    layer = entity.dxf.layer
    linetype = entity.dxf.linetype or ""
    color = entity.dxf.color
    center = (entity.dxf.center[0], entity.dxf.center[1])
    major_vec = (entity.dxf.major_axis[0], entity.dxf.major_axis[1])
    ratio = entity.dxf.ratio
    start_param = entity.dxf.start_param if entity.dxf.hasattr("start_param") else 0.0
    end_param = entity.dxf.end_param if entity.dxf.hasattr("end_param") else 2 * _math.pi

    major_len = _math.hypot(major_vec[0], major_vec[1])
    if major_len < 1e-10:
        return []
    angle = _math.atan2(major_vec[1], major_vec[0])
    minor_len = major_len * ratio

    segments = []
    n = 64
    prev_pt = None
    for i in range(n + 1):
        t = start_param + (end_param - start_param) * i / n
        # 参数方程 (cos(t), sin(t)) 旋转+缩放
        cx = center[0] + major_len * _math.cos(t) * _math.cos(angle) - minor_len * _math.sin(t) * _math.sin(angle)
        cy = center[1] + major_len * _math.cos(t) * _math.sin(angle) + minor_len * _math.sin(t) * _math.cos(angle)
        pt = (round(cx, 4), round(cy, 4))
        if prev_pt is not None:
            segments.append({
                "start": [prev_pt[0], prev_pt[1]],
                "end": [pt[0], pt[1]],
                "layer": layer, "linetype": linetype, "color": color,
                "type": "line",
            })
        prev_pt = pt
    return segments


def _explode_spline_to_lines(entity) -> list[dict]:
    """将样条曲线通过采样展开为多段 LINE（128 点均匀采样）"""
    layer = entity.dxf.layer
    linetype = entity.dxf.linetype or ""
    color = entity.dxf.color

    try:
        spline = entity.spline()
    except Exception:
        return []

    segments = []
    n = 128
    prev_pt = None
    for i in range(n + 1):
        t = i / n
        try:
            pt = spline.point(t)
        except Exception:
            continue
        pt_rounded = (round(pt[0], 4), round(pt[1], 4))
        if prev_pt is not None:
            segments.append({
                "start": [prev_pt[0], prev_pt[1]],
                "end": [pt_rounded[0], pt_rounded[1]],
                "layer": layer, "linetype": linetype, "color": color,
                "type": "line",
            })
        prev_pt = pt_rounded
    return segments


def _explode_insert(entity, doc) -> list[dict]:
    """展开块参照 (INSERT)，将块内实体变换到 WCS。
    嵌套块最多展开深度 3，过深则跳过。
    """
    import math as _math

    block_name = entity.dxf.name
    try:
        block = doc.blocks.get(block_name)
    except Exception:
        return []

    # 变换矩阵参数
    insert_pt = entity.dxf.insert
    scale_x = entity.dxf.xscale if entity.dxf.hasattr("xscale") else 1.0
    scale_y = entity.dxf.yscale if entity.dxf.hasattr("yscale") else 1.0
    rotation = entity.dxf.rotation if entity.dxf.hasattr("rotation") else 0.0

    cos_a = _math.cos(_math.radians(rotation))
    sin_a = _math.sin(_math.radians(rotation))

    def _transform(pt):
        """将块内坐标变换到 WCS"""
        x = (pt[0] * cos_a - pt[1] * sin_a) * scale_x + insert_pt[0]
        y = (pt[0] * sin_a + pt[1] * cos_a) * scale_y + insert_pt[1]
        return (x, y)

    result: list[dict] = []
    for e in block:
        dxftype = e.dxftype()
        color = e.dxf.color
        # 块内实体颜色为 BYBLOCK (0) 时继承 INSERT 的颜色
        if color == 0:
            color = entity.dxf.color

        if dxftype == "LINE":
            s = _transform(e.dxf.start)
            e2 = _transform(e.dxf.end)
            result.append({
                "start": [round(s[0], 4), round(s[1], 4)],
                "end": [round(e2[0], 4), round(e2[1], 4)],
                "layer": e.dxf.layer, "linetype": e.dxf.linetype or "",
                "color": color, "type": "line",
            })
        elif dxftype == "CIRCLE":
            c = _transform(e.dxf.center)
            r = e.dxf.radius * max(abs(scale_x), abs(scale_y))
            result.append({
                "center": [round(c[0], 4), round(c[1], 4)],
                "radius": round(r, 4),
                "layer": e.dxf.layer, "linetype": e.dxf.linetype or "",
                "color": color, "type": "circle",
            })
        elif dxftype == "ARC":
            c = _transform(e.dxf.center)
            r = e.dxf.radius * max(abs(scale_x), abs(scale_y))
            result.append({
                "center": [round(c[0], 4), round(c[1], 4)],
                "radius": round(r, 4),
                "start_angle": round(e.dxf.start_angle + rotation, 4),
                "end_angle": round(e.dxf.end_angle + rotation, 4),
                "layer": e.dxf.layer, "linetype": e.dxf.linetype or "",
                "color": color, "type": "arc",
            })
        elif dxftype in ("LWPOLYLINE", "POLYLINE"):
            segs = _explode_polyline_2d(e)
            for seg in segs:
                s = _transform((seg["start"][0], seg["start"][1]))
                e2 = _transform((seg["end"][0], seg["end"][1]))
                seg["start"] = [round(s[0], 4), round(s[1], 4)]
                seg["end"] = [round(e2[0], 4), round(e2[1], 4)]
                seg["color"] = color
            result.extend(segs)
        elif dxftype in ("TEXT", "MTEXT"):
            pass  # 块内文字通常为标注文字，正文已在 main modelspace 提取

    # 仅返回几何实体（标注/文字已在 main modelspace 处理）
    return [x for x in result if isinstance(x, dict) and x.get("type") in ("line", "circle", "arc")]


# ── DXF 预览渲染 ──────────────────────────────────────────

def render_dxf_preview(
    filepath: Path,
    output_path: Path,
    *,
    skip_dimensions: bool = False,
) -> Path:
    """
    将 DXF 渲染为 PNG 预览图。

    图纸坐标为 mm（ISO/GB 标准），长边 = max_size px，短边等比例。
    正确处理 DXF 索引色：BYLAYER(256)/BYBLOCK(0) → 图层色 → ACI 色表 → RGB。

    Args:
        filepath: DXF 文件路径
        output_path: 输出 PNG 路径
        skip_dimensions: True 跳过标注实体（生成无尺寸版本）
    """
    _check_ezdxf()
    params = _get_dxf_params()
    max_size: int = params["preview_max_size"]
    bg: str = params["preview_bg"]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.readfile(str(filepath))
    msp = doc.modelspace()

    # ── 颜色修正（白底黑线）─────────────────────────────────
    # DXF 索引色 7 = 白/黑（AutoCAD 依背景自动切换），ezdxf 固定 #FFFFFF
    # → 白线白底不可见，改为黑色。
    # "文本层"（标注所在层，颜色 212 深紫 a500a5）与黑线区分度低
    # → 改为蓝色，方便教师查看尺寸。
    for layer in doc.layers:
        if layer.dxf.color == 7:
            layer.rgb = (0, 0, 0)
        if layer.dxf.name == "文本层":
            layer.rgb = (0, 102, 204)

    # ── 修复标注虚拟实体 BYBLOCK→白色 的 ezdxf 缺陷 ────────
    # DIMENSION 的匿名几何块中所有实体 color=0 (BYBLOCK)，但 ezdxf
    # draw_composite_entity 对 DIMENSION 不 push_state，导致 BYBLOCK
    # 无法继承父标注颜色 → 固定解析为 #ffffff（白底白线不可见）。
    # 修复：将块内实体的 color 改为 256 (BYLAYER)，使用图层色。
    for entity in msp:
        if entity.dxftype() == "DIMENSION":
            try:
                block = entity.get_geometry_block()
                for be in block:
                    if be.dxf.color == 0:
                        be.dxf.color = 256
            except Exception:
                pass

    # ── 图纸包围盒（mm）→ 像素尺寸 ───────────────────────────
    from ezdxf.bbox import extents, Cache
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import RenderContext, Frontend
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    try:
        bounds = extents(msp, cache=Cache())
        has_data = bounds.has_data
    except Exception:
        has_data = False

    if not has_data:
        px_w, px_h = 768, 576
        xmin, xmax, ymin, ymax = 0, 100, 0, 100
    else:
        w_mm = bounds.size.x   # 图纸宽度 mm
        h_mm = bounds.size.y   # 图纸高度 mm
        if w_mm >= h_mm:
            px_w = max_size
            px_h = max(1, int(max_size * h_mm / w_mm))
        else:
            px_h = max_size
            px_w = max(1, int(max_size * w_mm / h_mm))
        xmin, xmax = bounds.extmin.x, bounds.extmax.x
        ymin, ymax = bounds.extmin.y, bounds.extmax.y

    # ── 创建画布（100 DPI 内部基准，仅供 matplot​lib 使用）───
    fig = plt.figure(figsize=(px_w / 100, px_h / 100), dpi=100, facecolor=bg)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_aspect('equal')
    ax.axis('off')
    ax.set_facecolor(bg)

    # ── 渲染 ─────────────────────────────────────────────────
    ctx = RenderContext(doc)
    backend = MatplotlibBackend(ax, adjust_figure=False)
    frontend = Frontend(ctx, backend)

    if skip_dimensions:
        frontend.draw_layout(msp, finalize=True,
                             filter_func=lambda e: e.dxftype() != "DIMENSION")
    else:
        frontend.draw_layout(msp, finalize=True)

    fig.savefig(str(output_path), dpi=100, facecolor=bg, edgecolor='none')
    plt.close(fig)
    return output_path


# ── 统一 DXF 分析（教师参考图 + 学生作业共用）───────────────

def process_dxf(filepath: Path, output_dir: Path | None = None) -> dict:
    """
    解析 DXF 并渲染预览图。教师参考图和学生作业统一入口。

    Args:
        filepath: DXF 文件路径
        output_dir: 预览图输出目录（默认与 DXF 同目录）

    Returns:
        extract_dxf 的结构化数据 dict
    """
    data = extract_dxf(filepath)
    out_dir = output_dir or filepath.parent
    stem = filepath.stem
    render_dxf_preview(filepath, out_dir / f"{stem}.png")
    render_dxf_preview(filepath, out_dir / f"{stem}_无尺寸.png", skip_dimensions=True)
    return data


# ── 统一 DXF 评分入口（教师批量 + 学生提交共用）─────────────

def run_dxf_grade(
    student_dxf_path: Path,
    ref_data: dict,
    ref_dir: Path,
    phase1_criteria: str,
    phase2_criteria: str,
    *,
    stu_dxf_data: dict | None = None,
    knowledge: str = "",
) -> tuple[dict, dict]:
    """
    统一 DXF 评分入口——教师批量评分和学生提交评分都走这里。

    Args:
        student_dxf_path: 学生 DXF 文件路径
        ref_data: 参考图提取数据（extract_dxf 的输出）
        ref_dir: 参考图所在目录（用于查找 参考工程图.png / .dxf）
        phase1_criteria: 阶段一评分标准
        phase2_criteria: 阶段二评分标准
        stu_dxf_data: 学生 DXF 提取数据（如已提前提取则传入，否则自动提取）
        knowledge: 补充知识

    Returns:
        (stu_dxf_data, grade_result) — 学生 DXF 数据和 LLM 评分结果
    """
    from services.llm_service import grade_dxf

    # 1. 提取学生 DXF 数据（如尚未提取则统一 process_dxf）
    if stu_dxf_data is None:
        stu_dxf_data = process_dxf(student_dxf_path)

    # 3. 预览图：阶段一视觉对比用无尺寸图（纯几何，去掉标注干扰）
    ref_nodim = ref_dir / "参考工程图_无尺寸.png"
    ref_preview = ref_nodim if ref_nodim.exists() else (ref_dir / "参考工程图.png")
    if not ref_preview.exists():
        ref_preview = ref_dir / "参考工程图.dxf"
    if not ref_preview.exists():
        raise RuntimeError("参考 DXF 预览图不存在，请联系老师")

    stu_png_nodim = student_dxf_path.parent / f"{student_dxf_path.stem}_无尺寸.png"
    stu_preview = stu_png_nodim if stu_png_nodim.exists() else stu_png

    # 4. LLM 评分
    grade_result = grade_dxf(
        ref_data=ref_data,
        stu_data=stu_dxf_data,
        ref_preview_path=ref_preview,
        stu_preview_path=stu_preview,
        phase1_criteria=phase1_criteria,
        phase2_criteria=phase2_criteria,
        knowledge=knowledge,
    )

    return stu_dxf_data, grade_result
    return output_path
