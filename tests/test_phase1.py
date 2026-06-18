"""Phase 1 回归测试 - Bug修复和重构验证"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from iec104_parser import (
    IEC104Parser, ParseMode, hex_to_bytes, is_log_format,
    split_concatenated_frames, parse_cp24time2a, parse_cp56time2a
)
from datetime import datetime


class TestHexToBytes:
    def test_basic_hex(self):
        assert hex_to_bytes("68 04 01 00 0E 00") == b'\x68\x04\x01\x00\x0E\x00'

    def test_no_spaces(self):
        assert hex_to_bytes("680401000E00") == b'\x68\x04\x01\x00\x0E\x00'

    def test_dot_separator(self):
        assert hex_to_bytes("68.04.01.00.0E.00") == b'\x68\x04\x01\x00\x0E\x00'

    def test_0x_prefix(self):
        assert hex_to_bytes("0x68 0x04") == b'\x68\x04'

    def test_odd_length_raises(self):
        try:
            hex_to_bytes("68 0")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass

    def test_newlines(self):
        assert hex_to_bytes("68 04\n01 00") == b'\x68\x04\x01\x00'


class TestIsLogFormat:
    def test_rx_tx_format(self):
        assert is_log_format("13:25:00.692 RX:") is True

    def test_dot_separator_format(self):
        assert is_log_format("68 04 .01 00") is True

    def test_plain_hex(self):
        assert is_log_format("68 04 01 00 0E 00") is False

    def test_xn8000_format(self):
        assert is_log_format("17- 9:24:59.947 send:") is False


class TestSplitConcatenatedFrames:
    def test_single_frame(self):
        result = split_concatenated_frames("680401000E00")
        assert len(result) == 1
        assert result[0] == "680401000E00"

    def test_two_frames(self):
        block = "680401000E00" + "680E04001400090103000100014000460200"
        result = split_concatenated_frames(block)
        assert len(result) == 2

    def test_with_spaces(self):
        block = "68 04 01 00 0E 00 68 0E 04 00 14 00 09 01 03 00 01 00 01 40 00 46 02 00"
        result = split_concatenated_frames(block)
        assert len(result) == 2

    def test_empty(self):
        result = split_concatenated_frames("")
        assert result == []


class TestCP24Time2a:
    def test_basic(self):
        data = bytes([0x58, 0x00, 0x1E])
        result = parse_cp24time2a(data)
        assert result.minute == 30
        assert result.second == 0

    def test_with_hour(self):
        data = bytes([0x58, 0x00, 0x1E])
        result = parse_cp24time2a(data, hour=15)
        assert result.hour == 15
        assert result.minute == 30

    def test_short_data(self):
        result = parse_cp24time2a(b'\x00\x00')
        assert result == datetime(2000, 1, 1)

    def test_minute_overflow(self):
        data = bytes([0x00, 0x00, 0x7F])
        result = parse_cp24time2a(data)
        assert result.minute == 59


class TestParserBasic:
    def test_s_frame(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 04 01 00 0E 00")
        assert frame.apci is not None
        assert frame.apci.frame_type == "S-format"

    def test_u_frame(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 04 07 00 00 00")
        assert frame.apci is not None
        assert frame.apci.frame_type == "U-format"

    def test_parse_mode(self):
        parser = IEC104Parser(mode=ParseMode.XN3000)
        assert parser.mode == ParseMode.XN3000

    def test_set_master_send_first_no_duplicate(self):
        parser = IEC104Parser()
        parser.set_master_send_first(False)
        assert parser.master_send_first is False
        assert parser.last_master_send is False


class TestXNFormats:
    def test_xn3000_parse(self):
        test_file = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                  "XN3000测试报文.txt")
        if os.path.exists(test_file):
            with open(test_file, "r", encoding="utf-8") as f:
                text = f.read()
            from iec104_parser import parse_log_format_blocks
            frames, lines = parse_log_format_blocks(text)
            assert len(frames) > 0

    def test_xn8000_parse(self):
        test_file = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                  "XN8000测试报文.txt")
        if os.path.exists(test_file):
            with open(test_file, "r", encoding="utf-8") as f:
                text = f.read()
            from iec104_parser import parse_log_format_blocks
            frames, lines = parse_log_format_blocks(text)
            assert len(frames) > 0
