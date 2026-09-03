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

    def test_cp56_iv_bit_set(self):
        """分钟字节 bit7=IV 必须保留为 invalid 标记，而不是被 0x3F 掩码丢弃"""
        data = struct.pack('<H', 1234) + bytes([0x80 | 30, 10, 15, 6, 23])
        data += bytes([0, 0, 0])
        result = parse_cp56time2a(data)
        assert result.minute == 30          # IV 位不参与数值
        assert getattr(result, "invalid", False) is True

    def test_cp56_iv_bit_clear(self):
        data = struct.pack('<H', 1234) + bytes([30, 10, 15, 6, 23])
        data += bytes([0, 0, 0])
        result = parse_cp56time2a(data)
        assert result.minute == 30
        assert getattr(result, "invalid", False) is False

    def test_cp24_iv_bit_set(self):
        data = struct.pack('<H', 500) + bytes([0x80 | 45])
        result = parse_cp24time2a(data)
        assert result.minute == 45
        assert getattr(result, "invalid", False) is True

    def test_cp24_iv_bit_clear(self):
        data = struct.pack('<H', 500) + bytes([45])
        result = parse_cp24time2a(data)
        assert result.minute == 45
        assert getattr(result, "invalid", False) is False

    def test_iv_annotated_in_frame(self):
        """IV=1 的时标在报文解析时应在描述中显式标注，避免误信"""
        parser = IEC104Parser()
        # type 30 (M_SP_TB_1): SIQ(1) + CP56(7)，分钟字节带 IV=0x80
        cp56 = bytes([0x00, 0x00, 0x9E, 0x0A, 0x0F, 0x06, 0x17])
        hex_str = f"68 15 00 00 00 00 1E 01 03 00 01 00 01 40 00 01 {' '.join(f'{b:02X}' for b in cp56)}"
        frame = parser.parse_hex(hex_str)
        elem = frame.asdu.information_elements[0]
        assert getattr(elem.timestamp, "invalid", False) is True
        assert "[时标无效:IV=1]" in elem.value_desc


class TestNormalizedSigned:
    """归一化值(NVA)是有符号数：0x8001..0x7FFF 映射 -1..+1。

    回归：此前按无符号解析，-32768 会被显示成 +1.00003。
    """

    def _type9(self, lo: str, hi: str) -> InfoElement:
        parser = IEC104Parser()
        frame = parser.parse_hex(f"68 10 00 00 00 00 09 01 03 00 01 00 01 40 00 {lo} {hi} 00")
        return frame.asdu.information_elements[0]

    def test_type9_negative_full_scale(self):
        elem = self._type9("FF", "FF")   # 0xFFFF → -1
        assert elem.value == -1
        assert "DEC=-1" in elem.value_desc

    def test_type9_negative_min(self):
        elem = self._type9("00", "80")   # 0x8000 → -32768，归一化约 -1.00003
        assert elem.value == -32768
        assert "归一化=-1.000031" in elem.value_desc
        assert "HEX=0x8000" in elem.value_desc

    def test_type9_positive_unchanged(self):
        elem = self._type9("00", "02")   # 0x0200 = 512，正值不受影响
        assert elem.value == 512

    def test_type21_signed(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 0F 00 00 00 00 15 01 03 00 01 00 01 40 00 FF FF")
        elem = frame.asdu.information_elements[0]
        assert elem.value == -1
        assert "归一化=-0.000031" in elem.value_desc

    def test_type34_signed(self):
        parser = IEC104Parser()
        # type 34: NVA(2) + QDS(1) + CP56(7)
        cp56 = "00 00 1E 0A 0F 06 17"
        frame = parser.parse_hex(f"68 17 00 00 00 00 22 01 03 00 01 00 01 40 00 FF FF 00 {cp56}")
        elem = frame.asdu.information_elements[0]
        assert elem.value == -1
        assert "DEC=-1" in elem.value_desc

    def test_type48_setpoint_signed(self):
        parser = IEC104Parser()
        # C_SE_NA_1: NVA(2) + QDS(1)，0x8000 → -32768
        frame = parser.parse_hex("68 10 00 00 00 00 30 01 06 00 01 00 01 60 00 00 80 00")
        elem = frame.asdu.information_elements[0]
        assert elem.value["raw"] == 0x8000
        assert elem.value["normalized"] == pytest.approx(-32768 / 32767.0)
        assert "归一化设定值=-1.000031" in elem.value_desc
        assert "DEC=-32768" in elem.value_desc


class TestProtectionEventTypes:
    """保护事件类型 38/39/40 对象长度回归：标准为 11/12/12 字节。

    回归：此前类型 38 按 12 字节、39/40 按 24 字节（错误的 4 字节时标跳过），
    单对象帧解析不到时标，多对象帧步长错位导致后续对象全部错乱。
    结构：38 = SPI+QDS+CP16(2)+CP56(7)=11；39/40 再加 SOD，=12。
    """
    CP56 = "00 00 1E 0A 0F 06 17"   # 2023-06-15 10:30，无 IV

    def test_type38_event(self):
        parser = IEC104Parser()
        # SPI(1)+QDS(1)+CP16=0x1234+CP56(7) = 11 字节
        frame = parser.parse_hex(
            "68 18 00 00 00 00 26 01 03 00 01 00 01 40 00 "
            "01 00 34 12 " + self.CP56)
        assert frame.asdu is not None
        elem = frame.asdu.information_elements[0]
        assert elem.value == 1
        assert elem.quality is not None
        assert "CP16时延=4660" in elem.value_desc     # 0x1234
        assert elem.timestamp.year == 2023
        assert elem.timestamp.minute == 30

    def test_type38_two_objects_stride(self):
        """VSQ=2：若步长不是 11 字节，第二个对象会错位"""
        parser = IEC104Parser()
        obj = "01 00 34 12 " + self.CP56
        frame = parser.parse_hex(
            "68 26 00 00 00 00 26 02 03 00 01 00 "
            "01 40 00 " + obj + " 02 40 00 " + obj)
        assert frame.asdu is not None
        elems = frame.asdu.information_elements
        assert len(elems) == 2
        for e in elems:
            assert e.value == 1
            assert e.timestamp.year == 2023, "时标应取到对象内偏移 4..11"

    def test_type39_startup_event(self):
        parser = IEC104Parser()
        # SPE(1)+QDS(1)+CP16(2)+SOD(1)+CP56(7) = 12 字节
        frame = parser.parse_hex(
            "68 19 00 00 00 00 27 01 03 00 01 00 01 40 00 "
            "05 00 00 00 01 " + self.CP56)
        assert frame.asdu is not None
        elem = frame.asdu.information_elements[0]
        assert elem.value == 5
        assert "SPE=0x05" in elem.value_desc
        assert "SOD=0x01" in elem.value_desc
        assert elem.timestamp.year == 2023

    def test_type40_output_circuit(self):
        parser = IEC104Parser()
        # OCI(1)+QDS(1)+CP16(2)+SOD(1)+CP56(7) = 12 字节
        frame = parser.parse_hex(
            "68 19 00 00 00 00 28 01 03 00 01 00 01 40 00 "
            "03 00 00 00 01 " + self.CP56)
        assert frame.asdu is not None
        elem = frame.asdu.information_elements[0]
        assert elem.value == 3
        assert "OCI=0x03" in elem.value_desc
        assert elem.timestamp.year == 2023


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
        from gui.parse_worker import parse_text_sync
        text = "# comment\n68 04 01 00 0E 00"
        frames = parse_text_sync(text)
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
