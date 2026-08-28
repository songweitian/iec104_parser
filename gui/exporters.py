"""报文导出模块（从 gui/main_window.py 拆分，M4）。

把 CSV/JSON/TXT 三种导出中"不依赖 MainWindow 的数据组装逻辑"提取为纯函数：
- 输入：全部帧列表 + 需要导出的帧下标（过滤后的可见行）
- 输出：CSV 文本 / JSON 数据对象 / TXT 文本

GUI 侧只负责文件对话框、写盘与状态提示，便于本模块被单元测试（E10 风格）。
"""
import csv
import io
from datetime import datetime
from typing import List, Optional

from iec104_parser import TypeID, COT, IEC104Frame


# ============================================================================
# 公共字段
# ============================================================================

def _element_timestamp(elem) -> str:
    """信息元素时标 → 可读字符串"""
    if elem.timestamp:
        return elem.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return ""


# ============================================================================
# CSV 导出
# ============================================================================

CSV_HEADER = [
    "序号", "原始时间戳", "方向", "信号类型", "帧类型", "类型标识", "类型描述",
    "传送原因", "传送原因描述", "公共地址", "对象数",
    "信息对象地址", "值", "时标", "错误",
]


def _frame_to_csv_rows(frame: IEC104Frame) -> List[List[str]]:
    """单帧 → 0..N 行 CSV 数据（每个信息对象一行）"""
    if frame.asdu and frame.asdu.information_elements:
        rows = []
        for elem in frame.asdu.information_elements:
            rows.append([
                frame.index, frame.original_timestamp or "", frame.direction,
                frame.signal_label,
                frame.apci.frame_type if frame.apci else "",
                frame.asdu.type_id,
                TypeID.get_description(frame.asdu.type_id),
                frame.asdu.cot_cause,
                COT.get_description(frame.asdu.cot),
                frame.asdu.common_address,
                len(frame.asdu.information_elements),
                f"0x{elem.ioa:06X}",
                elem.value_desc, _element_timestamp(elem), frame.error,
            ])
        return rows
    return [[
        frame.index, frame.original_timestamp or "",
        frame.direction if frame.direction else "",
        "", frame.apci.frame_type if frame.apci else "",
        "", "", "", "", "", "",
        "", frame.description if frame.description else "",
        "", frame.error,
    ]]


def export_csv(frames: List[IEC104Frame],
               indices: Optional[List[int]] = None) -> str:
    """生成 CSV 文本（含 UTF-8 BOM）。

    indices: 要导出的帧下标（过滤后的可见行）；None 表示全部帧。
    """
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_HEADER)
    for idx in (indices if indices is not None else range(len(frames))):
        if 0 <= idx < len(frames):
            writer.writerows(_frame_to_csv_rows(frames[idx]))
    return out.getvalue()


# ============================================================================
# JSON 导出
# ============================================================================

def _frame_to_json_entry(frame: IEC104Frame) -> dict:
    """单帧 → JSON 条目"""
    entry = {
        "index": frame.index,
        "raw_hex": frame.raw_hex,
        "direction": frame.direction,
        "send_role": frame.send_role,
        "recv_role": frame.recv_role,
        "signal_type": frame.signal_label,
        "description": frame.description,
        "error": frame.error,
        "apci": None,
        "asdu": None,
    }
    if frame.apci:
        entry["apci"] = {
            "frame_type": frame.apci.frame_type,
            "send_seq": frame.apci.send_seq,
            "recv_seq": frame.apci.recv_seq,
            "apdu_length": frame.apci.apdu_length,
        }
    if frame.asdu:
        entry["asdu"] = {
            "type_id": frame.asdu.type_id,
            "type_name": TypeID.get_name(frame.asdu.type_id),
            "type_desc": TypeID.get_description(frame.asdu.type_id),
            "signal_type": frame.asdu.signal_type.label,
            "vsq": frame.asdu.vsq,
            "object_count": frame.asdu.number_of_objects,
            "is_sequence": frame.asdu.is_sequence,
            "cot": frame.asdu.cot,
            "cot_cause": frame.asdu.cot_cause,
            "cot_desc": COT.get_description(frame.asdu.cot),
            "cot_test": frame.asdu.cot_test,
            "cot_pn": frame.asdu.cot_pn,
            "org": frame.asdu.org,
            "common_address": frame.asdu.common_address,
            "elements": [],
        }
        for elem in frame.asdu.information_elements:
            entry["asdu"]["elements"].append({
                "ioa": f"0x{elem.ioa:06X}",
                "signal_type": elem.signal_type.label,
                "value": str(elem.value),
                "value_desc": elem.value_desc,
                "quality": str(elem.quality) if elem.quality else None,
                "timestamp": elem.timestamp.isoformat() if elem.timestamp else None,
            })
    return entry


def export_json(frames: List[IEC104Frame],
                indices: Optional[List[int]] = None) -> List[dict]:
    """生成 JSON 数据对象列表（调用方负责 json.dump）"""
    data = []
    for idx in (indices if indices is not None else range(len(frames))):
        if 0 <= idx < len(frames):
            data.append(_frame_to_json_entry(frames[idx]))
    return data


# ============================================================================
# TXT 导出
# ============================================================================

def export_txt(frames: List[IEC104Frame],
               indices: Optional[List[int]] = None,
               export_time: Optional[datetime] = None) -> str:
    """生成人类可读的 TXT 报告文本"""
    export_time = export_time or datetime.now()
    lines = []
    lines.append("=" * 80)
    lines.append("IEC60870-5-104 报文解析报告")
    lines.append(f"解析时间: {export_time.strftime('%Y-%m-%d %H:%M:%S')}")
    sel = indices if indices is not None else list(range(len(frames)))
    lines.append(f"报文总数: {len(sel)}/{len(frames)}")
    lines.append("=" * 80)
    lines.append("")

    for idx in sel:
        if not (0 <= idx < len(frames)):
            continue
        frame = frames[idx]
        lines.append(f"--- 报文 #{frame.index} ---")
        lines.append(f"方向: {frame.direction}")
        if frame.signal_label:
            lines.append(f"信号类型: {frame.signal_label}")
        lines.append(f"原始数据: {frame.raw_hex}")
        if frame.error:
            lines.append(f"错误: {frame.error}")
        elif frame.description:
            lines.append(f"描述: {frame.description}")
        if frame.apci:
            lines.append(f"帧类型: {frame.apci.frame_type}")
            lines.append(f"发送序号SN: {frame.apci.send_seq}")
            lines.append(f"接收序号RN: {frame.apci.recv_seq}")
        if frame.asdu:
            lines.append(f"类型标识: {frame.asdu.type_id} "
                         f"({TypeID.get_description(frame.asdu.type_id)})")
            lines.append(f"传送原因: {COT.get_description(frame.asdu.cot)}")
            lines.append(f"公共地址: {frame.asdu.common_address}")
            lines.append(f"信息对象数: {frame.asdu.number_of_objects}")
            for elem in frame.asdu.information_elements:
                lines.append(f"  IOA=0x{elem.ioa:06X} [{elem.signal_type.label}]: "
                             f"{elem.value_desc}")
                if elem.quality:
                    lines.append(f"    品质: {elem.quality}")
                if elem.timestamp:
                    lines.append(f"    时标: {elem.timestamp}")
        lines.append("")
    return "\n".join(lines)
