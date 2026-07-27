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

# 答题图框线宽（mm），系统常数，非教师可配置
FRAME_LINEWIDTH = 1.0
# DXF 线宽存储单位：1/100 mm，1.0mm = 100
FRAME_LINEWEIGHT_DXF = int(FRAME_LINEWIDTH * 100)


def arc_bbox(cx: float, cy: float, radius: float,
             start_angle: float, end_angle: float) -> tuple[float, float, float, float]:
    """
    计算圆弧的精确包围盒。
    取起止端点 + 四个象限点中落在弧上的点。
    角度单位为度（DXF 标准）。
    """
    a1, a2 = start_angle, end_angle

    # 归一化：让 a2 > a1（跨越 0° 时 +360）
    if a1 > a2:
        a2 += 360

    pts = []

    # 起止端点
    rad1 = math.radians(a1)
    rad2 = math.radians(a2)
    pts.append((cx + radius * math.cos(rad1), cy + radius * math.sin(rad1)))
    pts.append((cx + radius * math.cos(rad2), cy + radius * math.sin(rad2)))

    # 四个象限角度 0, 90, 180, 270
    for quad_angle in [0, 90, 180, 270]:
        # 直接判断 + 跨越 0° 时的 +360 判断，稳妥处理 wrap-around
        if (a1 <= quad_angle <= a2) or (a1 <= quad_angle + 360 <= a2):
            rad = math.radians(quad_angle)
            pts.append((cx + radius * math.cos(rad), cy + radius * math.sin(rad)))

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def circle_bbox(cx: float, cy: float, radius: float) -> tuple[float, float, float, float]:
    """计算圆的包围盒"""
    return cx - radius, cy - radius, cx + radius, cy + radius

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
    source_type = entity.dxftype()
    layer = entity.dxf.layer
    linetype = entity.dxf.linetype or ""
    color = entity.dxf.color
    lineweight = _entity_lineweight(entity)

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
                    "lineweight": lineweight,
                        "source_type": source_type,
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
                        "lineweight": lineweight,
                        "source_type": source_type,
                    })
                    prev_pt = pt

        # 闭合多段线：首尾相连（最后一点→第一点）
        if len(bulge_points) >= 3:
            try:
                is_closed = entity.closed
            except Exception:
                is_closed = False
            if is_closed:
                # 最后一点→第一点
                x_last, y_last = bulge_points[-1][0], bulge_points[-1][1]
                x_first, y_first = bulge_points[0][0], bulge_points[0][1]
                if ocs:
                    w_last = ocs.to_wcs((x_last, y_last, 0))
                    w_first = ocs.to_wcs((x_first, y_first, 0))
                    p_last = (w_last.x, w_last.y)
                    p_first = (w_first.x, w_first.y)
                else:
                    p_last = (x_last, y_last)
                    p_first = (x_first, y_first)
                segments.append({
                    "start": [round(p_last[0], 4), round(p_last[1], 4)],
                    "end": [round(p_first[0], 4), round(p_first[1], 4)],
                    "layer": layer, "linetype": linetype, "color": color,
                    "type": "line",
                    "lineweight": lineweight,
                        "source_type": source_type,
                })
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
                "lineweight": lineweight,
                        "source_type": source_type,
            })

        # 闭合多段线：首尾相连
        if len(points) >= 3:
            try:
                is_closed = entity.closed
            except Exception:
                is_closed = False
            if is_closed:
                p_last = points[-1]
                p_first = points[0]
                segments.append({
                    "start": [round(p_last[0], 4), round(p_last[1], 4)],
                    "end": [round(p_first[0], 4), round(p_first[1], 4)],
                    "layer": layer, "linetype": linetype, "color": color,
                    "type": "line",
                    "lineweight": lineweight,
                        "source_type": source_type,
                })
        return segments

    # 如果都失败，把 entity 作为单个整体保留（不考虑 bulge 的退化情况）
    return segments


def _explode_mline(entity) -> list[dict]:
    """将 MLINE 分解为多条 LINE（每根平行线一条 LINE 段）。
    如果 ezdxf 版本支持，使用 .get_line_segments() 获取各线段。
    """
    segments: list[dict] = []
    source_type = entity.dxftype()
    layer = entity.dxf.layer
    linetype = entity.dxf.linetype or ""
    color = entity.dxf.color
    lineweight = _entity_lineweight(entity)

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
                        "lineweight": lineweight,
                        "source_type": source_type,
                    })
                else:
                    # line 可能是 (start, end) 元组
                    s, e = line
                    segments.append({
                        "start": [round(s[0], 4), round(s[1], 4)],
                        "end": [round(e[0], 4), round(e[1], 4)],
                        "layer": layer, "linetype": linetype, "color": color,
                        "type": "line",
                        "lineweight": lineweight,
                        "source_type": source_type,
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


def _entity_lineweight(entity) -> int:
    """获取实体的 DXF 线宽值（1/100 mm）。BYLAYER(-1) 时尝试从图层属性推断。"""
    try:
        lw = entity.dxf.lineweight
        if lw is None or lw < 0:  # BYLAYER / BYBLOCK / DEFAULT
            # 从图层获取
            try:
                lw = entity.doc.layers.get(entity.dxf.layer).dxf.lineweight
            except Exception:
                lw = -1
        if lw is None or lw < 0:
            return 0
        return int(lw)
    except Exception:
        return 0


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
        "lineweight": _entity_lineweight(entity),
        "type": "line",
        "source_type": "LINE",
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
        "lineweight": _entity_lineweight(entity),
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
        "lineweight": _entity_lineweight(entity),
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
    for el in all_entities.get("ellipses", []):
        cx, cy = el["center"]
        ma = el["major_axis"]
        major_len = math.hypot(ma[0], ma[1])
        minor_len = major_len * el["ratio"]
        r = max(major_len, minor_len)
        xs.extend([cx - r, cx + r])
        ys.extend([cy - r, cy + r])
    for sp in all_entities.get("splines", []):
        for cp in sp.get("control_points", []):
            xs.append(cp[0])
            ys.append(cp[1])
    for pt in all_entities.get("points", []):
        pos = pt.get("position")
        if pos:
            xs.append(pos[0]); ys.append(pos[1])
    for ld in all_entities.get("leaders", []):
        for v in ld.get("vertices", []):
            xs.append(v[0]); ys.append(v[1])
    for tol in all_entities.get("tolerances", []):
        pos = tol.get("position")
        if pos:
            xs.append(pos[0]); ys.append(pos[1])
    for sd in all_entities.get("solids", []):
        for c in sd.get("corners", []):
            xs.append(c[0]); ys.append(c[1])

    if not xs or not ys:
        return {"min_x": 0, "max_x": 1, "min_y": 0, "max_y": 1}

    return {
        "min_x": round(min(xs), 4),
        "max_x": round(max(xs), 4),
        "min_y": round(min(ys), 4),
        "max_y": round(max(ys), 4),
    }


# ── 图框检测与实体分类 ─────────────────────────────────────

# ACI 颜色索引 → 视图名称映射
FRAME_COLOR_MAP: dict[int, str] = {
    1: "主视图",
    2: "俯视图",
    3: "左视图",
    5: "其他视图1",
    6: "其他视图2",
}


def _detect_frames(all_lines: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    从 LINE 实体中检测图框。

    检测条件：lineweight == FRAME_LINEWEIGHT_DXF (100) 且 color 在 FRAME_COLOR_MAP 中。
    返回 (frames, non_frame_lines)，其中 frames 为检测到的图框列表：
      [{name, color, bbox: {min_x, min_y, max_x, max_y}}]
    non_frame_lines 为排除了图框线条后的剩余 LINE。
    """
    frame_lines: dict[int, list[dict]] = {c: [] for c in FRAME_COLOR_MAP}
    normal_lines: list[dict] = []

    for ln in all_lines:
        lw = ln.get("lineweight", 0)
        color = ln.get("color", 0)
        if lw == FRAME_LINEWEIGHT_DXF and color in FRAME_COLOR_MAP:
            frame_lines[color].append(ln)
        else:
            normal_lines.append(ln)

    frames = []
    for color, color_lines in frame_lines.items():
        if len(color_lines) < 4:
            # 不足 4 条线，不足以构成矩形图框 → 归为普通线
            normal_lines.extend(color_lines)
            continue

        # 收集所有端点坐标，计算包围盒
        xs, ys = [], []
        for ln in color_lines:
            xs.append(ln["start"][0])
            xs.append(ln["end"][0])
            ys.append(ln["start"][1])
            ys.append(ln["end"][1])

        frames.append({
            "name": FRAME_COLOR_MAP[color],
            "color": color,
            "bbox": {
                "min_x": round(min(xs), 4),
                "min_y": round(min(ys), 4),
                "max_x": round(max(xs), 4),
                "max_y": round(max(ys), 4),
            },
        })

    return frames, normal_lines


def _point_in_bbox(px: float, py: float, bbox: dict) -> bool:
    """判断点是否在图框包围盒内"""
    return (bbox["min_x"] - 1e-6 <= px <= bbox["max_x"] + 1e-6
            and bbox["min_y"] - 1e-6 <= py <= bbox["max_y"] + 1e-6)


def _line_in_frame(line: dict, bbox: dict) -> bool:
    """LINE 两端点都在框内 → 属于该图框"""
    return (_point_in_bbox(line["start"][0], line["start"][1], bbox)
            and _point_in_bbox(line["end"][0], line["end"][1], bbox))


def _entity_bbox_in_frame(entity_bbox: tuple[float, float, float, float],
                          frame_bbox: dict) -> bool:
    """实体的包围盒完全在图框包围盒内"""
    return (frame_bbox["min_x"] - 1e-6 <= entity_bbox[0]
            and frame_bbox["min_y"] - 1e-6 <= entity_bbox[1]
            and entity_bbox[2] <= frame_bbox["max_x"] + 1e-6
            and entity_bbox[3] <= frame_bbox["max_y"] + 1e-6)


def _dimension_in_frame(dim: dict, bbox: dict) -> bool:
    """标注的任一 defpoint（几何定义点）在框内 → 属于该图框"""
    defpoints = dim.get("defpoints", {})
    for key in ("defpoint", "defpoint2", "defpoint3", "defpoint4"):
        pt = defpoints.get(key)
        if pt and _point_in_bbox(pt[0], pt[1], bbox):
            return True
    # 如果 defpoints 为空，回退到 text_position
    tp = dim.get("text_position")
    if tp:
        return _point_in_bbox(tp[0], tp[1], bbox)
    return False


def _centerline_in_frame(cl: dict, bbox: dict) -> bool:
    """中心线两端都在框内 → 属于该图框"""
    if "start" in cl and "end" in cl:
        return (_point_in_bbox(cl["start"][0], cl["start"][1], bbox)
                and _point_in_bbox(cl["end"][0], cl["end"][1], bbox))
    return False


def _hatch_in_frame(hatch: dict, bbox: dict) -> bool:
    """HATCH 的第一个边界点在框内 → 属于该图框"""
    boundary = hatch.get("boundary", [])
    if not boundary:
        return False
    first = boundary[0]
    if first and len(first) > 0:
        pt = first[0]
        return _point_in_bbox(pt[0], pt[1], bbox)
    return False


def _text_in_frame(text: dict, bbox: dict) -> bool:
    """TEXT 位置在框内 → 属于该图框"""
    pos = text.get("position")
    if pos:
        return _point_in_bbox(pos[0], pos[1], bbox)
    return False


def _point_in_frame(pt: dict, bbox: dict) -> bool:
    """POINT 位置在框内 → 属于"""
    pos = pt.get("position")
    if pos:
        return _point_in_bbox(pos[0], pos[1], bbox)
    return False


def _leader_in_frame(ld: dict, bbox: dict) -> bool:
    """LEADER 任一顶点在框内 → 属于"""
    for v in ld.get("vertices", []):
        if _point_in_bbox(v[0], v[1], bbox):
            return True
    return False


def _tolerance_in_frame(tol: dict, bbox: dict) -> bool:
    """TOLERANCE 位置点在框内 → 属于"""
    pos = tol.get("position")
    if pos:
        return _point_in_bbox(pos[0], pos[1], bbox)
    return False


def _solid_in_frame(sd: dict, bbox: dict) -> bool:
    """SOLID 任一顶点在框内 → 属于"""
    for c in sd.get("corners", []):
        if _point_in_bbox(c[0], c[1], bbox):
            return True
    return False


def _ellipse_in_frame(el: dict, bbox: dict) -> bool:
    """椭圆中心在框内 → 属于该图框"""
    return _point_in_bbox(el["center"][0], el["center"][1], bbox)


def _spline_in_frame(sp: dict, bbox: dict) -> bool:
    """样条所有控制点在框内 → 属于该图框"""
    for cp in sp.get("control_points", []):
        if not _point_in_bbox(cp[0], cp[1], bbox):
            return False
    return True if sp.get("control_points") else False


def _classify_entities_by_frames(
    lines: list[dict],
    circles: list[dict],
    arcs: list[dict],
    frames: list[dict],
    ellipses: list[dict] | None = None,
    splines: list[dict] | None = None,
    points: list[dict] | None = None,
    leaders: list[dict] | None = None,
    tolerances: list[dict] | None = None,
    solids: list[dict] | None = None,
    hatches: list[dict] | None = None,
    dimensions: list[dict] | None = None,
    texts: list[dict] | None = None,
    centerlines: list[dict] | None = None,
) -> tuple[dict[str, dict], list[dict]]:
    """
    将实体按图框分类。

    返回 (views, frame_list):
      views: { "全视图数据集": {lines, circles, arcs, hatches, dimensions, texts, centerlines},
                "主视图": {lines, circles, arcs, ...}, ... }
      frame_list: 检测到的图框列表（无图框时为空）
    """
    hatches = hatches or []
    dimensions = dimensions or []
    texts = texts or []
    centerlines = centerlines or []
    ellipses = ellipses or []
    splines = splines or []
    points = points or []
    leaders = leaders or []
    tolerances = tolerances or []
    solids = solids or []

    # ── 1. 按图框归类 ──
    view_data: dict[str, dict] = {}
    for frame in frames:
        name = frame["name"]
        bbox = frame["bbox"]
        view_data[name] = {
            "lines": [ln for ln in lines if _line_in_frame(ln, bbox)],
            "circles": [c for c in circles
                        if _entity_bbox_in_frame(
                            circle_bbox(c["center"][0], c["center"][1], c["radius"]),
                            bbox)],
            "arcs": [a for a in arcs
                     if _entity_bbox_in_frame(
                         arc_bbox(a["center"][0], a["center"][1],
                                  a["radius"], a["start_angle"], a["end_angle"]),
                         bbox)],
            "ellipses": [el for el in ellipses if _ellipse_in_frame(el, bbox)],
            "splines": [sp for sp in splines if _spline_in_frame(sp, bbox)],
            "points": [pt for pt in points if _point_in_frame(pt, bbox)],
            "leaders": [ld for ld in leaders if _leader_in_frame(ld, bbox)],
            "tolerances": [tol for tol in tolerances if _tolerance_in_frame(tol, bbox)],
            "solids": [sd for sd in solids if _solid_in_frame(sd, bbox)],
            "hatches": [h for h in hatches if _hatch_in_frame(h, bbox)],
            "dimensions": [d for d in dimensions if _dimension_in_frame(d, bbox)],
            "texts": [t for t in texts if _text_in_frame(t, bbox)],
            "centerlines": [cl for cl in centerlines if _centerline_in_frame(cl, bbox)],
        }

    # ── 2. 全视图数据集 = 所有非图框线实体 ──
    view_data["全视图数据集"] = {
        "lines": lines,
        "circles": circles,
        "arcs": arcs,
        "ellipses": ellipses,
        "splines": splines,
        "points": points,
        "leaders": leaders,
        "tolerances": tolerances,
        "solids": solids,
        "hatches": hatches,
        "dimensions": dimensions,
        "texts": texts,
        "centerlines": centerlines,
    }

    return view_data, frames


# ── 核心提取函数 ──────────────────────────────────────────

def _build_extraction_result(
    lines: list[dict],
    circles: list[dict],
    arcs: list[dict],
    ellipses: list[dict] | None = None,
    splines: list[dict] | None = None,
    points: list[dict] | None = None,
    leaders: list[dict] | None = None,
    tolerances: list[dict] | None = None,
    solids: list[dict] | None = None,
    hatches: list[dict] | None = None,
    centerlines: list[dict] | None = None,
    dimensions: list[dict] | None = None,
    texts: list[dict] | None = None,
    layers: dict | None = None,
    frames: list[dict] | None = None,
    views: dict[str, dict] | None = None,
) -> dict:
    """组装最终提取结果"""
    ellipses = ellipses or []
    splines = splines or []
    points = points or []
    leaders = leaders or []
    tolerances = tolerances or []
    solids = solids or []
    hatches = hatches or []
    centerlines = centerlines or []
    dimensions = dimensions or []
    texts = texts or []
    layers = layers or {}

    entities = {
        "lines": list(lines),
        "circles": list(circles),
        "arcs": list(arcs),
        "ellipses": ellipses,
        "splines": splines,
        "points": points,
        "leaders": leaders,
        "tolerances": tolerances,
        "solids": solids,
        "hatches": hatches,
        "centerlines": centerlines,
    }

    result = {
        "entities": entities,
        "dimensions": dimensions,
        "texts": texts,
        "layers": layers,
        "entity_counts": {
            "lines": len(lines),
            "circles": len(circles),
            "arcs": len(arcs),
            "ellipses": len(ellipses),
            "splines": len(splines),
            "points": len(points),
            "leaders": len(leaders),
            "tolerances": len(tolerances),
            "solids": len(solids),
            "hatches": len(hatches),
            "centerlines": len(centerlines),
            "dimensions": len(dimensions),
            "texts": len(texts),
        },
        "bounds": _compute_bounds(entities),
    }
    if frames:
        result["frames"] = frames
    if views:
        result["views"] = views
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
    ellipses: list[dict] = []
    splines: list[dict] = []
    points: list[dict] = []
    leaders: list[dict] = []
    tolerances: list[dict] = []
    solids: list[dict] = []
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
            # 椭圆 → 保留参数，不炸为 LINE
            try:
                ellipses.append({
                    "center": [round(entity.dxf.center[0], 4), round(entity.dxf.center[1], 4)],
                    "major_axis": [round(entity.dxf.major_axis[0], 4), round(entity.dxf.major_axis[1], 4)],
                    "ratio": round(entity.dxf.ratio, 4),
                    "start_param": round(entity.dxf.start_param, 4),
                    "end_param": round(entity.dxf.end_param, 4),
                    "layer": entity.dxf.layer,
                    "linetype": entity.dxf.linetype or "",
                    "color": entity.dxf.color,
                    "lineweight": _entity_lineweight(entity),
                    "type": "ellipse",
                })
            except Exception as e:
                logger.debug(f"提取椭圆失败: {e}")

        elif dxftype == "SPLINE":
            # 样条曲线 → 保留控制点，不炸为 LINE
            try:
                cp = [[round(p[0],4), round(p[1],4)]
                      for p in entity.control_points]
                splines.append({
                    "control_points": cp,
                    "degree": int(entity.dxf.degree) if entity.dxf.hasattr("degree") else 3,
                    "layer": entity.dxf.layer,
                    "linetype": entity.dxf.linetype or "",
                    "color": entity.dxf.color,
                    "lineweight": _entity_lineweight(entity),
                    "type": "spline",
                })
            except Exception as e:
                logger.debug(f"提取样条曲线失败: {e}")

        elif dxftype == "POINT":
            try:
                loc = entity.dxf.location
                points.append({
                    "position": [round(loc[0], 4), round(loc[1], 4)],
                    "layer": entity.dxf.layer,
                    "color": entity.dxf.color,
                    "lineweight": _entity_lineweight(entity),
                    "type": "point",
                })
            except Exception as e:
                logger.debug(f"提取点失败: {e}")

        elif dxftype == "LEADER":
            try:
                verts = [[round(v[0],4), round(v[1],4)] for v in entity.vertices]
                leaders.append({
                    "vertices": verts,
                    "layer": entity.dxf.layer,
                    "color": entity.dxf.color,
                    "type": "leader",
                })
            except Exception as e:
                logger.debug(f"提取引线失败: {e}")

        elif dxftype == "TOLERANCE":
            try:
                tolerances.append({
                    "position": [round(entity.dxf.insert[0],4), round(entity.dxf.insert[1],4)],
                    "content": str(entity.dxf.content or ""),
                    "layer": entity.dxf.layer,
                    "color": entity.dxf.color,
                    "lineweight": _entity_lineweight(entity),
                    "type": "tolerance",
                })
            except Exception as e:
                logger.debug(f"提取形位公差失败: {e}")

        elif dxftype == "SOLID":
            try:
                corners = []
                for attr in ("vtx0", "vtx1", "vtx2", "vtx3"):
                    v = getattr(entity.dxf, attr, None)
                    if v is not None:
                        corners.append([round(v[0],4), round(v[1],4)])
                solids.append({
                    "corners": corners,
                    "layer": entity.dxf.layer,
                    "color": entity.dxf.color,
                    "type": "solid",
                })
            except Exception as e:
                logger.debug(f"提取SOLID失败: {e}")

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

    # ── 图框检测与实体分类（不改变 lines/circles/arcs 原始列表） ──
    frames_detected, non_frame_lines = _detect_frames(lines)
    if frames_detected:
        views_sorted, _ = _classify_entities_by_frames(
            non_frame_lines, circles, arcs, frames_detected,
            ellipses=ellipses, splines=splines,
            points=points, leaders=leaders,
            tolerances=tolerances, solids=solids,
            hatches=hatches, dimensions=dimensions,
            texts=texts, centerlines=centerlines)
    else:
        views_sorted = {"全视图数据集": {
            "lines": lines, "circles": circles, "arcs": arcs,
            "ellipses": ellipses, "splines": splines,
            "points": points, "leaders": leaders,
            "tolerances": tolerances, "solids": solids,
            "hatches": hatches, "dimensions": dimensions,
            "texts": texts, "centerlines": centerlines,
        }}
        frames_detected = []

    result = _build_extraction_result(
        lines, circles, arcs,
        ellipses=ellipses, splines=splines,
        points=points, leaders=leaders,
        tolerances=tolerances, solids=solids,
        hatches=hatches, centerlines=centerlines,
        dimensions=dimensions, texts=texts, layers=layers,
        frames=frames_detected,
        views=views_sorted,
    )
    logger.info(
        f"DXF 解析完成: "
        f"{result['entity_counts']['lines']}线 "
        f"{result['entity_counts']['circles']}圆 "
        f"{result['entity_counts']['arcs']}弧 "
        f"{result['entity_counts']['ellipses']}椭圆 "
        f"{result['entity_counts']['splines']}样条 "
        f"{result['entity_counts']['points']}点 "
        f"{result['entity_counts']['leaders']}引线 "
        f"{result['entity_counts']['tolerances']}公差 "
        f"{result['entity_counts']['solids']}填充面 "
        f"{result['entity_counts']['hatches']}填充 "
        f"{result['entity_counts']['centerlines']}中心线 "
        f"{result['entity_counts']['dimensions']}标注 "
        f"{result['entity_counts']['texts']}文本"
    )
    if frames_detected:
        logger.info(f"  检测到 {len(frames_detected)} 个图框: "
                     f"{[f['name'] for f in frames_detected]}")
    return result


# ── ELIPSE / SPLINE / INSERT 展开辅助 ─────────────────────

def _explode_ellipse_to_lines(entity) -> list[dict]:
    """将椭圆分解为多段 LINE（64 段近似）"""
    import math as _math
    source_type = entity.dxftype()
    layer = entity.dxf.layer
    linetype = entity.dxf.linetype or ""
    color = entity.dxf.color
    lineweight = _entity_lineweight(entity)
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
                "lineweight": lineweight,
                        "source_type": source_type,
            })
        prev_pt = pt
    return segments


def _explode_spline_to_lines(entity) -> list[dict]:
    """将样条曲线通过采样展开为多段 LINE（128 点均匀采样）"""
    source_type = entity.dxftype()
    layer = entity.dxf.layer
    linetype = entity.dxf.linetype or ""
    color = entity.dxf.color
    lineweight = _entity_lineweight(entity)

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
                "lineweight": lineweight,
                        "source_type": source_type,
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


# ── DXF 预处理：MLINE / POLYLINE / LWPOLYLINE → LINE ─────

def preprocess_dxf(input_path: Path, output_path: Path) -> None:
    """
    预处理 DXF 文件：仅将 MLINE / POLYLINE / LWPOLYLINE 爆炸为 LINE 实体，
    其他实体原样保留。爆炸后的 LINE 继承原实体的图层、颜色、线型、线宽，
    并附加 XDATA source_type 标识来源。

    输出文件与原 DXF 同目录，文件名为 {原文件名_stem}_processed.dxf
    """
    import ezdxf
    _check_ezdxf()

    doc = ezdxf.readfile(str(input_path))
    msp = doc.modelspace()

    # 注册 XDATA 的 appid
    APPID = "DXF_PREP"
    if APPID not in doc.appids:
        doc.appids.new(APPID)

    # 收集需要替换的实体（先收集后修改，避免迭代中修改）
    to_replace: list[tuple[object, str, list[dict]]] = []  # (entity, dxftype, segments)

    for entity in msp:
        dxftype = entity.dxftype()
        if dxftype not in ("LWPOLYLINE", "POLYLINE", "MLINE"):
            continue
        try:
            if dxftype == "MLINE":
                segs = _explode_mline(entity)
            else:
                segs = _explode_polyline_2d(entity)
        except Exception as e:
            logger.debug(f"爆炸 {dxftype} 失败: {e}")
            continue
        if segs:
            to_replace.append((entity, dxftype, segs))

    # 替换
    for entity, dxftype, segs in to_replace:
        try:
            msp.delete_entity(entity)
        except Exception:
            pass
        for seg in segs:
            new_line = msp.add_line(
                start=(seg["start"][0], seg["start"][1]),
                end=(seg["end"][0], seg["end"][1]),
            )
            new_line.dxf.layer = seg.get("layer", "0")
            new_line.dxf.color = seg.get("color", 256)
            ltype = seg.get("linetype", "")
            if ltype and ltype.upper() != "BYLAYER":
                try:
                    new_line.dxf.linetype = ltype
                except Exception:
                    pass
            lw = seg.get("lineweight", 0)
            if lw > 0:
                try:
                    new_line.dxf.lineweight = lw
                except Exception:
                    pass
            # 附加 XDATA：记录来源类型
            try:
                source = seg.get("source_type", dxftype)
                new_line.add_xdata(APPID, [
                    (1001, APPID),
                    (1000, f"source_type={source}"),
                ])
            except Exception:
                pass

    doc.saveas(str(output_path))
    logger.info(f"预处理 DXF 已保存: {output_path}  "
                 f"(替换 {len(to_replace)} 个实体: "
                 f"{', '.join(f'{t[1]}' for t in to_replace)})")


# ── 重叠线清理 ─────────────────────────────────────────

_TOL = 1e-8

_SOLID_LTYPES = {"", "bylayer", "continuous", "solid"}
_DASHED_LTYPE_KWS = ["dash", "dotted", "hidden", "dashdot",
                     "acad_iso02", "acad_iso04", "acad_iso06",
                     "acad_iso07", "acad_iso09", "acad_iso11"]
_CENTER_LTYPE_KWS = ["center", "center2", "centerx2",
                     "acad_iso08", "acad_iso10", "acad_iso12"]


def _resolve_entity_linetype(entity: dict, layers: dict) -> str:
    """解析实体实际线型：BYLAYER 时查图层"""
    lt = (entity.get("linetype") or "").strip()
    if lt.upper() == "BYLAYER" or not lt:
        layer_name = entity.get("layer", "0")
        layer_info = layers.get(layer_name, {})
        lt = str(layer_info.get("linetype", "Continuous"))
    return lt


def _linetype_category(linetype: str) -> str:
    """返回线型分类: 'solid', 'dashed', 'centerline', 'other'"""
    lower = linetype.lower().strip()
    if not lower or lower in _SOLID_LTYPES:
        return "solid"
    if any(kw in lower for kw in _DASHED_LTYPE_KWS):
        return "dashed"
    if any(kw in lower for kw in _CENTER_LTYPE_KWS):
        return "centerline"
    return "other"


def _normalize_arc(arc: dict) -> dict:
    """统一圆弧为逆时针、start < end；跨越 0° 时 end += 360"""
    a = dict(arc)
    s, e = a["start_angle"], a["end_angle"]
    if s > e:
        e += 360
    a["start_angle"] = s
    a["end_angle"] = e
    return a


def _parallel(p1, p2, q1, q2) -> bool:
    """向量 (p1→p2) 与 (q1→q2) 是否平行（相对容差）"""
    dx1, dy1 = p2[0] - p1[0], p2[1] - p1[1]
    dx2, dy2 = q2[0] - q1[0], q2[1] - q1[1]
    cross = abs(dx1 * dy2 - dy1 * dx2)
    len1 = math.hypot(dx1, dy1)
    len2 = math.hypot(dx2, dy2)
    # 容差取两向量长度的 1e-4 倍，最低 1e-6
    tol = max(1e-6, 1e-4 * max(len1, len2))
    return cross < tol


def _collinear(l1: dict, l2: dict) -> bool:
    """两条 LINE 是否共线"""
    a, b = l1["start"], l1["end"]
    c, d = l2["start"], l2["end"]
    if not _parallel(a, b, c, d):
        return False
    # a→c 也与方向平行
    return _parallel(a, b, a, c)


def _project_t(pt, ref_start, ref_end) -> float:
    """返回 pt 在参考线上的投影参数 t"""
    dx = ref_end[0] - ref_start[0]
    dy = ref_end[1] - ref_start[1]
    if abs(dx) < _TOL and abs(dy) < _TOL:
        return 0.0
    return ((pt[0] - ref_start[0]) * dx + (pt[1] - ref_start[1]) * dy) / (dx * dx + dy * dy)


def _merge_lines_batch(batch: list[dict]) -> list[dict]:
    """合并一批共线线段。取最远两点作为新线段。"""
    if not batch:
        return []
    ref = batch[0]
    rs, re = ref["start"], ref["end"]
    ts = [_project_t(l["start"], rs, re) for l in batch]
    te = [_project_t(l["end"], rs, re) for l in batch]
    all_t = ts + te
    i_min = all_t.index(min(all_t))
    i_max = all_t.index(max(all_t))
    pts = [l["start"] for l in batch] + [l["end"] for l in batch]
    merged = dict(batch[0])
    merged["start"] = pts[i_min]
    merged["end"] = pts[i_max]
    return [merged]


def _overlap_interval(t1: float, t2: float, u1: float, u2: float) -> tuple[float, float] | None:
    """返回两个投影区间的重叠段，无重叠返回 None"""
    lo = max(min(t1, t2), min(u1, u2))
    hi = min(max(t1, t2), max(u1, u2))
    if hi - lo >= -_TOL:
        return lo, hi
    return None


def _clean_lines_by_category(lines: list[dict]) -> list[dict]:
    """合并某类线型中所有共线重叠/相接线段"""
    if not lines:
        return []
    remaining = list(lines)
    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(remaining):
            j = i + 1
            merged_any = False
            while j < len(remaining):
                if _collinear(remaining[i], remaining[j]):
                    ri, rj = remaining[i], remaining[j]
                    rs, re = ri["start"], ri["end"]
                    ti = [_project_t(ri["start"], rs, re), _project_t(ri["end"], rs, re)]
                    tj = [_project_t(rj["start"], rs, re), _project_t(rj["end"], rs, re)]
                    if _overlap_interval(ti[0], ti[1], tj[0], tj[1]) is not None:
                        merged = _merge_lines_batch([remaining.pop(j), remaining.pop(i)])[0]
                        remaining.append(merged)
                        changed = True
                        merged_any = True
                        break
                j += 1
            if not merged_any:
                i += 1
    return remaining


def _dedup_circles(circles: list[dict]) -> list[dict]:
    """同圆心同半径的圆只保留一个"""
    seen = set()
    result = []
    for c in circles:
        key = (round(c["center"][0], 4), round(c["center"][1], 4), round(c["radius"], 4))
        if key not in seen:
            seen.add(key)
            result.append(c)
    return result


def _arcs_same_base(a1: dict, a2: dict) -> bool:
    """两个圆弧是否同圆心同半径"""
    return (abs(a1["center"][0] - a2["center"][0]) < _TOL and
            abs(a1["center"][1] - a2["center"][1]) < _TOL and
            abs(a1["radius"] - a2["radius"]) < _TOL)


def _merge_arcs_batch(batch: list[dict]) -> list[dict]:
    """合并同圆心同半径且有角度重叠的圆弧"""
    if not batch:
        return []
    norm = [_normalize_arc(a) for a in batch]
    starts = [a["start_angle"] for a in norm]
    ends = [a["end_angle"] for a in norm]
    # 合并角度范围
    s_min, e_max = min(starts), max(ends)
    merged = dict(batch[0])
    merged["start_angle"] = s_min
    merged["end_angle"] = e_max
    # 如果范围超过 360，归一化
    if e_max - s_min > 360:
        merged["end_angle"] = s_min + 360
    return [merged]


def _clean_arcs_by_category(arcs: list[dict]) -> list[dict]:
    """合并同圆心同半径且有角度重叠的圆弧，迭代到稳定"""
    if not arcs:
        return []
    remaining = list(arcs)
    changed = True
    while changed:
        changed = False
        i = 0
        while i < len(remaining):
            j = i + 1
            merged_any = False
            while j < len(remaining):
                if _arcs_same_base(remaining[i], remaining[j]):
                    ni = _normalize_arc(remaining[i])
                    nj = _normalize_arc(remaining[j])
                    if _overlap_interval(ni["start_angle"], ni["end_angle"],
                                         nj["start_angle"], nj["end_angle"]) is not None:
                        merged = _merge_arcs_batch([remaining.pop(j), remaining.pop(i)])[0]
                        remaining.append(merged)
                        changed = True
                        merged_any = True
                        break
                j += 1
            if not merged_any:
                i += 1
    return remaining


def _arc_covered_by_circle(arc: dict, circle: dict) -> bool:
    """圆弧是否被同圆心同半径的圆完全覆盖"""
    return _arcs_same_base(arc, circle)


def _cut_lines_overlap(dashed: dict, solid: dict) -> list[dict] | None:
    """
    实线覆盖虚线的重叠部分，虚线被切除。
    返回切除后剩余的虚线线段列表。
    - 虚线被完全覆盖 → []（删除）
    - 虚线部分覆盖 → 1-2 条新虚线
    - 无重叠 → [dashed]（不变）
    如果两条线不共线或无重叠，返回 None。
    """
    if not _collinear(dashed, solid):
        return None

    rs, re = dashed["start"], dashed["end"]
    ti1, ti2 = _project_t(dashed["start"], rs, re), _project_t(dashed["end"], rs, re)
    tj1, tj2 = _project_t(solid["start"], rs, re), _project_t(solid["end"], rs, re)
    d_min, d_max = min(ti1, ti2), max(ti1, ti2)
    s_min, s_max = min(tj1, tj2), max(tj1, tj2)

    ov = _overlap_interval(d_min, d_max, s_min, s_max)
    if ov is None:
        return None  # 无重叠

    ol, oh = ov
    # 切除 [ol, oh]，剩余 [d_min, ol] 和 [oh, d_max]
    result = []
    if ol - d_min > _TOL:  # 左侧剩余
        result.append(_make_line_from_projection(dashed, d_min, ol, rs, re))
    if d_max - oh > _TOL:  # 右侧剩余
        result.append(_make_line_from_projection(dashed, oh, d_max, rs, re))
    return result


def _make_line_from_projection(
    template: dict, t1: float, t2: float, ref_start, ref_end
) -> dict:
    """根据投影参数创建新线段。自动处理 t1/t2 顺序，确保长度的正方向。"""
    dx = ref_end[0] - ref_start[0]
    dy = ref_end[1] - ref_start[1]
    length = dx * dx + dy * dy
    if length < _TOL:
        return dict(template)
    p1 = [ref_start[0] + t1 * dx, ref_start[1] + t1 * dy]
    p2 = [ref_start[0] + t2 * dx, ref_start[1] + t2 * dy]
    new_line = dict(template)
    # 保持线段方向与模板一致（模板 start→end 方向不变）
    new_line["start"] = [round(p1[0], 4), round(p1[1], 4)]
    new_line["end"] = [round(p2[0], 4), round(p2[1], 4)]
    return new_line


def clean_overlapping_entities(
    lines: list[dict],
    circles: list[dict],
    arcs: list[dict],
    layers: dict,
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    清理重叠实体。

    流程：
      1. 按线型分类（实线/虚线/中心线）
      2. 各类内部分别合并重叠线段、圆、圆弧
      3. 虚线与实线重叠：切除虚线的重叠部分
      4. 迭代直到无变化

    Args:
        lines: 线段列表
        circles: 圆列表
        arcs: 圆弧列表
        layers: 图层信息 {name: {linetype, ...}}

    Returns:
        (cleaned_lines, cleaned_circles, cleaned_arcs)
    """
    # ── 1. 按线型分类 ──
    by_cat: dict[str, dict[str, list]] = {
        "solid": {"lines": [], "circles": [], "arcs": []},
        "dashed": {"lines": [], "circles": [], "arcs": []},
        "centerline": {"lines": [], "circles": [], "arcs": []},
    }

    def _categorize_entity(entity, entity_type, store):
        lt = _resolve_entity_linetype(entity, layers)
        cat = _linetype_category(lt)
        if cat in by_cat:
            store[cat][entity_type].append(entity)
        else:
            # 'other' → 归入 solid 作为兜底
            store["solid"][entity_type].append(entity)

    for ln in lines:
        _categorize_entity(ln, "lines", by_cat)
    for c in circles:
        _categorize_entity(c, "circles", by_cat)
    for a in arcs:
        _categorize_entity(a, "arcs", by_cat)

    # ── 迭代处理 ──
    changed = True
    max_iter = 20
    iteration = 0
    while changed and iteration < max_iter:
        iteration += 1
        changed = False

        # 2. 各类内部处理
        for cat in ("solid", "dashed", "centerline"):
            g = by_cat[cat]
            before_l = len(g["lines"])
            g["lines"] = _clean_lines_by_category(g["lines"])
            if len(g["lines"]) != before_l:
                changed = True

            before_c = len(g["circles"])
            g["circles"] = _dedup_circles(g["circles"])
            if len(g["circles"]) != before_c:
                changed = True

            before_a = len(g["arcs"])
            g["arcs"] = _clean_arcs_by_category(g["arcs"])
            if len(g["arcs"]) != before_a:
                changed = True

            # 圆弧被同圆心同半径的圆覆盖 → 删除圆弧
            new_arcs = []
            for a in g["arcs"]:
                covered = any(_arc_covered_by_circle(a, circ) for circ in g["circles"])
                if not covered:
                    new_arcs.append(a)
                else:
                    changed = True
            g["arcs"] = new_arcs

        # 3. 虚线与实线重叠处理（逐条累进切割）
        dashed_lines = by_cat["dashed"]["lines"]
        solid_lines = by_cat["solid"]["lines"]
        new_dashed = []
        for dl in dashed_lines:
            remaining_segs = [dl]
            for sl in solid_lines:
                next_segs = []
                for seg in remaining_segs:
                    result = _cut_lines_overlap(seg, sl)
                    if result is None:
                        next_segs.append(seg)
                    else:
                        next_segs.extend(result)
                remaining_segs = next_segs
            if len(remaining_segs) != 1 or (
                remaining_segs[0]['start'] != dl['start'] or
                remaining_segs[0]['end'] != dl['end']
            ):
                new_dashed.extend(remaining_segs)
                changed = True
            else:
                new_dashed.append(dl)
        by_cat["dashed"]["lines"] = new_dashed

        # 4. 虚线圆被实线圆覆盖 → 删除虚线圆
        solid_circle_keys = {
            (round(c['center'][0], 4), round(c['center'][1], 4), round(c['radius'], 4))
            for c in by_cat['solid']['circles']
        }
        new_dashed_circles = []
        for dc in by_cat['dashed']['circles']:
            key = (round(dc['center'][0], 4), round(dc['center'][1], 4), round(dc['radius'], 4))
            if key in solid_circle_keys:
                changed = True  # 虚线圆被实线圆覆盖，丢弃
            else:
                new_dashed_circles.append(dc)
        by_cat['dashed']['circles'] = new_dashed_circles

        # 5. 虚线圆弧被实线圆弧/实线圆覆盖
        dashed_arcs = by_cat["dashed"]["arcs"]
        solid_arcs = by_cat["solid"]["arcs"]
        solid_circles = by_cat["solid"]["circles"]
        new_dashed_arcs = []
        for da in dashed_arcs:
            covered = False
            for sa in solid_arcs:
                if _arcs_same_base(da, sa):
                    nda = _normalize_arc(da)
                    nsa = _normalize_arc(sa)
                    if _overlap_interval(nda["start_angle"], nda["end_angle"],
                                         nsa["start_angle"], nsa["end_angle"]) is not None:
                        covered = True
                        break
            if not covered:
                for sc in solid_circles:
                    if _arc_covered_by_circle(da, sc):
                        covered = True
                        break
            if not covered:
                new_dashed_arcs.append(da)
            else:
                changed = True
        by_cat["dashed"]["arcs"] = new_dashed_arcs

    # ── 合并结果 ──
    out_lines = by_cat["solid"]["lines"] + by_cat["dashed"]["lines"] + by_cat["centerline"]["lines"]
    out_circles = by_cat["solid"]["circles"] + by_cat["dashed"]["circles"] + by_cat["centerline"]["circles"]
    out_arcs = by_cat["solid"]["arcs"] + by_cat["dashed"]["arcs"] + by_cat["centerline"]["arcs"]

    return out_lines, out_circles, out_arcs


# ── 统一 DXF 分析（教师参考图 + 学生作业共用）───────────────

def process_dxf(filepath: Path, output_dir: Path | None = None) -> dict:
    """
    解析 DXF 并渲染预览图。教师参考图和学生作业统一入口。

    流程：
      1. 预处理：MLINE / POLYLINE / LWPOLYLINE → LINE（保存为 *_processed.dxf）
      2. 从预处理后的 DXF 提取结构化数据
      3. 渲染预览图（含尺寸 + 无尺寸）

    Args:
        filepath: DXF 文件路径
        output_dir: 预览图输出目录（默认与 DXF 同目录）

    Returns:
        extract_dxf 的结构化数据 dict
    """
    out_dir = output_dir or filepath.parent
    stem = filepath.stem

    # 1. 预处理 → 保存 _processed.dxf
    processed_path = out_dir / f"{stem}_processed.dxf"
    if not processed_path.exists():
        try:
            preprocess_dxf(filepath, processed_path)
        except Exception as e:
            logger.warning(f"DXF 预处理失败，使用原始文件: {e}")
            processed_path = filepath
    else:
        # 已存在则直接使用
        pass

    # 2. 从预处理后的 DXF 提取结构化数据
    data = extract_dxf(processed_path)

    # 3. 渲染预览图（含尺寸 + 无尺寸）
    render_dxf_preview(processed_path, out_dir / f"{stem}.png")
    render_dxf_preview(processed_path, out_dir / f"{stem}_无尺寸.png", skip_dimensions=True)

    logger.info(f"DXF 处理完成: {filepath.name} → 预处理 {processed_path.name}")
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
