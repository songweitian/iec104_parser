"""gui/exporters.py 单元测试（M4 拆分回归）。

拆分前 CSV/JSON/TXT 导出逻辑内联在 MainWindow 中、难以独立验证；
拆分后 export_csv/export_json/export_txt 为纯函数，此处直接断言其输出结构，
防止后续改动破坏导出格式。
"""
import os
import sys
import json
import io
import csv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from iec104_parser import ParseMode
from gui.parse_worker import parse_text_sync
from gui.exporters import export_csv, export_json, export_txt, CSV_HEADER


def _parse_frames():
    """构造 2 条 I 帧（测量值归一化）"""
    text = (
        "68 0E 04 00 14 00 09 01 03 00 01 00 01 40 00 46 02 00\n"
        "68 0E 04 00 14 00 09 01 03 00 01 00 02 40 00 47 02 00\n"
    )
    return parse_text_sync(text, ParseMode.NORMAL)


class TestExportCsv:
    def test_header_row(self):
        frames = _parse_frames()
        out = export_csv(frames)
        reader = csv.reader(io.StringIO(out))
        header = next(reader)
        assert header == CSV_HEADER
        # 每帧至少一行数据
        rows = list(reader)
        assert len(rows) >= 2

    def test_values_present(self):
        frames = _parse_frames()
        out = export_csv(frames)
        assert "0x004001" in out       # IOA 0x4001
        assert "0x004002" in out       # IOA 0x4002
        assert "子站→主站" in out      # 方向

    def test_indices_filter(self):
        frames = _parse_frames()
        out = export_csv(frames, indices=[0])
        reader = csv.reader(io.StringIO(out))
        next(reader)  # header
        rows = list(reader)
        assert len(rows) == 1
        assert "0x004001" in rows[0]
        assert not any("0x004002" in r for r in rows)

    def test_empty_frames(self):
        out = export_csv([])
        reader = csv.reader(io.StringIO(out))
        assert next(reader) == CSV_HEADER
        assert list(reader) == []


class TestExportJson:
    def test_structure(self):
        frames = _parse_frames()
        data = export_json(frames)
        assert len(data) == 2
        entry = data[0]
        assert entry["index"] == 1
        assert entry["apci"]["frame_type"] == "I-format"
        assert entry["asdu"]["type_id"] == 9
        assert entry["asdu"]["elements"][0]["ioa"] == "0x004001"

    def test_serializable(self):
        frames = _parse_frames()
        # 必须可被 json.dump 序列化
        json.dumps(export_json(frames), ensure_ascii=False)

    def test_indices_filter(self):
        frames = _parse_frames()
        data = export_json(frames, indices=[1])
        assert len(data) == 1
        assert data[0]["index"] == 2


class TestExportTxt:
    def test_report_header(self):
        frames = _parse_frames()
        out = export_txt(frames)
        assert "IEC60870-5-104 报文解析报告" in out
        assert "报文总数: 2/2" in out

    def test_frame_content(self):
        frames = _parse_frames()
        out = export_txt(frames)
        assert "--- 报文 #1 ---" in out
        assert "0x004001" in out
        assert "测量值" in out  # 类型描述

    def test_indices_filter(self):
        frames = _parse_frames()
        out = export_txt(frames, indices=[0])
        assert "报文总数: 1/2" in out
        assert "0x004001" in out
        assert "0x004002" not in out

    def test_empty_frames(self):
        out = export_txt([])
        assert "报文总数: 0/0" in out
