"""Phase 3: 协议解析器全面测试"""
import sys
import os
import struct
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from iec104_parser import (
    IEC104Parser, ParseMode, TypeID, SignalType,
    hex_to_bytes, is_log_format, split_concatenated_frames,
    parse_cp24time2a, parse_cp56time2a,
    APCIHeader, ASDU, InfoElement, QualityDescriptor
)
from datetime import datetime


class TestAPCIParsing:
    """APCI 帧解析测试"""

    def test_i_frame_detection(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 0E 00 00 00 00 09 01 03 00 01 00 01 40 00 46 02 00")
        assert frame.apci is not None
        assert frame.apci.frame_type == "I-format"

    def test_s_frame_detection(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 04 01 00 0E 00")
        assert frame.apci is not None
        assert frame.apci.frame_type == "S-format"
        assert frame.apci.recv_seq == 7

    def test_u_frame_detection(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 04 07 00 00 00")
        assert frame.apci is not None
        assert frame.apci.frame_type == "U-format"

    def test_invalid_start_byte(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("00 04 01 00 0E 00")
        assert frame.error is not None

    def test_frame_too_short(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 04")
        assert frame.error is not None


class TestASDUParsing:
    """ASDU 头部解析测试"""

    def test_type_id_parsing(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 0E 00 00 00 00 09 01 03 00 01 00 01 40 00 46 02 00")
        assert frame.asdu is not None
        assert frame.asdu.type_id == 9

    def test_cot_parsing(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 0E 00 00 00 00 09 01 03 00 01 00 01 40 00 46 02 00")
        assert frame.asdu is not None
        assert frame.asdu.cot == 3

    def test_common_address_parsing(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 0E 00 00 00 00 09 01 03 00 01 00 01 40 00 46 02 00")
        assert frame.asdu is not None
        assert frame.asdu.common_address == 1

    def test_ioa_parsing(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 0E 00 00 00 00 09 01 03 00 01 00 01 40 00 46 02 00")
        assert frame.asdu is not None
        assert len(frame.asdu.information_elements) == 1
        assert frame.asdu.information_elements[0].ioa == 0x4001


class TestTypeIDParsing:
    """各类型标识的值解析测试"""

    def test_type1_single_point(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 0E 00 00 00 00 01 01 03 00 01 00 01 40 00 01")
        assert frame.asdu is not None
        elem = frame.asdu.information_elements[0]
        assert elem.value == True

    def test_type3_double_point(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 0E 00 00 00 00 03 01 03 00 01 00 01 40 00 08")
        assert frame.asdu is not None
        elem = frame.asdu.information_elements[0]
        assert elem.value == 2

    def test_type9_normalized(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 10 00 00 00 00 09 01 03 00 01 00 01 40 00 00 02 00")
        assert frame.asdu is not None
        elem = frame.asdu.information_elements[0]
        assert elem.value == 0x0200

    def test_type11_scaled(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 10 00 00 00 00 0B 01 03 00 01 00 01 40 00 00 01 00")
        assert frame.asdu is not None
        elem = frame.asdu.information_elements[0]
        assert elem.value == 256

    def test_type13_short_float(self):
        parser = IEC104Parser()
        float_bytes = struct.pack('<f', 1.0)
        hex_str = f"68 11 00 00 00 00 0D 01 03 00 01 00 01 40 00 {' '.join(f'{b:02X}' for b in float_bytes)}"
        frame = parser.parse_hex(hex_str)
        assert frame.asdu is not None
        elem = frame.asdu.information_elements[0]
        assert abs(elem.value - 1.0) < 0.001

    def test_type15_counter(self):
        parser = IEC104Parser()
        counter_bytes = struct.pack('<I', 1000)
        hex_str = f"68 11 00 00 00 00 0F 01 03 00 01 00 01 40 00 {' '.join(f'{b:02X}' for b in counter_bytes)}"
        frame = parser.parse_hex(hex_str)
        assert frame.asdu is not None
        elem = frame.asdu.information_elements[0]
        assert elem.value == 1000

    def test_type45_single_command(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 0E 00 00 00 00 2D 01 03 00 01 00 01 60 00 01")
        assert frame.asdu is not None
        elem = frame.asdu.information_elements[0]
        assert elem.value == {"scs": 1, "se": 0}

    def test_type70_init_termination(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 0E 00 00 00 00 46 01 03 00 01 00 00 00 00 00")
        assert frame.asdu is not None
        assert frame.asdu.type_id == 70

    def test_type100_interrogation(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 0E 00 00 00 00 64 01 06 00 01 00 00 00 00 14")
        assert frame.asdu is not None
        assert frame.asdu.type_id == 100
        elem = frame.asdu.information_elements[0]
        assert elem.value == 20

    def test_type103_clock_sync(self):
        parser = IEC104Parser()
        cp56 = bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        hex_str = f"68 14 00 00 00 00 67 01 06 00 01 00 00 00 {' '.join(f'{b:02X}' for b in cp56)} 00"
        frame = parser.parse_hex(hex_str)
        assert frame.asdu is not None
        assert frame.asdu.type_id == 103


class TestCP56Time2a:
    """CP56Time2a 时标解析测试"""

    def test_basic_time(self):
        ms = 123
        minute = 30
        hour = 10
        day = 15
        month = 6
        year = 23
        data = struct.pack('<H', ms) + bytes([minute, hour, day, month, year])
        data += bytes([0, 0, 0])
        result = parse_cp56time2a(data)
        assert result.year == 2023
        assert result.month == 6
        assert result.day == 15
        assert result.hour == 10
        assert result.minute == 30
        assert result.microsecond == 123000

    def test_short_data(self):
        result = parse_cp56time2a(b'\x00\x00\x00')
        assert result == datetime(2000, 1, 1)

    def test_invalid_data(self):
        result = parse_cp56time2a(b'')
        assert result == datetime(2000, 1, 1)


class TestDirectionDetection:
    """主站/子站方向判定测试"""

    def test_monitor_direction(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 0E 00 00 00 00 09 01 03 00 01 00 01 40 00 46 02 00")
        assert frame.direction == "子站→主站"
        assert frame.send_role == "子站"

    def test_control_direction(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 0E 00 00 00 00 64 01 06 00 01 00 00 00 00 14")
        assert frame.direction == "主站→子站"
        assert frame.send_role == "主站"

    def test_alternating_direction(self):
        parser = IEC104Parser()
        parser.set_master_send_first(True)
        frame1 = parser.parse_hex("68 0E 00 00 00 00 FF 01 03 00 01 00 01 40 00 46 02 00")
        frame2 = parser.parse_hex("68 0E 00 00 00 00 FF 01 03 00 01 00 01 40 00 46 02 00")
        assert frame1.direction == "子站→主站"
        assert frame2.direction == "主站→子站"


class TestInputFormats:
    """输入格式处理测试"""

    def test_hex_with_spaces(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 04 01 00 0E 00")
        assert frame.apci is not None

    def test_hex_without_spaces(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("680401000E00")
        assert frame.apci is not None

    def test_hex_with_dot_separator(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68.04.01.00.0E.00")
        assert frame.apci is not None

    def test_comment_lines_ignored(self):
        parser = IEC104Parser()
        text = "# comment\n68 04 01 00 0E 00"
        frames = parser.parse_from_text(text)
        assert len(frames) == 1


class TestSequenceNumber:
    """序列号连续性测试"""

    def test_s_frame_sequence(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 04 01 00 0E 00")
        assert frame.apci.recv_seq == 7

    def test_i_frame_send_sequence(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 0E 00 00 00 00 09 01 03 00 01 00 01 40 00 46 02 00")
        assert frame.apci.send_seq == 0


class TestFileTransfer:
    """文件传输 TypeID 测试"""

    def test_type120_file_ready(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 0E 00 00 00 00 78 01 03 00 01 00 00 00 01 02 03 04")
        assert frame.asdu is not None
        assert frame.asdu.type_id == 120

    def test_type126_file_directory(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 0E 00 00 00 00 7E 01 03 00 01 00 00 00 01 02 03 04")
        assert frame.asdu is not None
        assert frame.asdu.type_id == 126


class TestParameterCommands:
    """参数命令 TypeID 测试"""

    def test_type108_parameter_normalized(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 0E 00 00 00 00 6C 01 03 00 01 00 00 00 40 00 02")
        assert frame.asdu is not None
        assert frame.asdu.type_id == 108

    def test_type110_parameter_float(self):
        parser = IEC104Parser()
        float_bytes = struct.pack('<f', 1.0)
        hex_str = f"68 0E 00 00 00 00 6E 01 03 00 01 00 00 00 80 {' '.join(f'{b:02X}' for b in float_bytes)}"
        frame = parser.parse_hex(hex_str)
        assert frame.asdu is not None
        assert frame.asdu.type_id == 110
