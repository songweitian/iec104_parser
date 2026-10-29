"""协议正确性回归测试（本轮代码审核发现的问题）

覆盖：
- COT 第 2 字节是 ORG（源发方地址），不得并入传送原因
- 遥控/遥调 S/E 位语义：1=选择(预制)、0=执行；RCO(47/60) 的 S/E 在 bit6
- 步位置 VTI 为 7 位补码、累计量 BCR 为 32 位补码
- 帧生成器参数越界/不支持类型必须报错，而不是静默生成错帧
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from frame_generator import generate_i_frame
from iec104_parser import IEC104Parser


def _parse(hex_str: str):
    parser = IEC104Parser()
    return parser, parser.parse_hex(hex_str)


class TestCotOriginator:
    def test_originator_not_mixed_into_cot(self):
        """ORG≠0 时传送原因仍应是 data[2]，ORG 单独落在 asdu.org"""
        frame = _parse(generate_i_frame(9, 6, 1, 0x4001, 100, origin_addr=0x35))[1]
        assert frame.asdu.cot == 6
        assert frame.asdu.cot_cause == 6
        assert frame.asdu.org == 0x35

    def test_generator_writes_originator_byte(self):
        raw = bytes.fromhex(
            generate_i_frame(9, 6, 1, 0x4001, 100, origin_addr=0x7F).replace(" ", ""))
        assert raw[6 + 3] == 0x7F     # ASDU 偏移 3 = ORG


class TestSelectExecuteBit:
    def test_sco_select_is_bit7_set(self):
        frame = _parse(generate_i_frame(45, 6, 1, 0x6001, 1, select=True))[1]
        assert "选择" in frame.description
        frame = _parse(generate_i_frame(45, 6, 1, 0x6001, 1, select=False))[1]
        assert "执行" in frame.description

    def test_rco_se_bit6_and_step_direction(self):
        """RCO：bit0=1 表示降一步，S/E 在 bit6"""
        desc = _parse(generate_i_frame(47, 6, 1, 0x6201, 0x01, select=False))[1]
        assert "降" in desc.asdu.information_elements[0].value_desc
        assert "执行" in desc.asdu.information_elements[0].value_desc

        picked = _parse(generate_i_frame(47, 6, 1, 0x6201, 0x01, select=True))[1]
        elem = picked.asdu.information_elements[0]
        assert elem.value["se"] == 1
        assert "选择" in elem.value_desc
        assert "降" in elem.value_desc

    def test_setpoint_qos_se_display(self):
        elem = _parse(generate_i_frame(48, 6, 1, 0x6201, 100, select=True))[1] \
            .asdu.information_elements[0]
        assert "选择" in elem.value_desc
        elem = _parse(generate_i_frame(48, 6, 1, 0x6201, 100, select=False))[1] \
            .asdu.information_elements[0]
        assert "执行" in elem.value_desc

    def test_timestamped_command_se_offset(self):
        """带时标命令 61 的 S/E 位偏移同样要正确取到 QOS"""
        frame = _parse(generate_i_frame(61, 6, 1, 0x6201, 100, select=True))[1]
        assert "选择确认" in frame.description or "选择" in frame.description


class TestValueDecoding:
    def test_step_position_is_7bit_signed(self):
        """VTI 0x7F 是 -1（7 位补码），不是 +127"""
        hex_str = generate_i_frame(5, 3, 1, 0x4001, 0x7F)
        elem = _parse(hex_str)[1].asdu.information_elements[0]
        assert elem.value == -1

    def test_counter_is_signed_32bit(self):
        """累计量 BCR 为补码：0xFFFFFFFF 应显示 -1"""
        hex_str = generate_i_frame(15, 3, 1, 0x6401, -1)
        elem = _parse(hex_str)[1].asdu.information_elements[0]
        assert elem.value == -1


class TestLogFormatMetadata:
    """日志格式（HH:MM:SS.mmm RX/TX:）里的时间与收发方向不能丢

    此前 parse_log_format_blocks 把标记行整行丢弃，普通模式下时间列恒为
    "-"，方向只能靠交替猜测，S 帧/U 帧（无类型标识）经常标反。
    """

    LOG = ("17:01:02.612 RX:\n68 04 0B 00 00 00\n\n"
           "17:01:02.725 TX:\n68 04 43 00 00 00\n")

    def test_direction_and_timestamp_kept(self):
        from iec104_parser import parse_log_format_frames
        frames, line_map, ts, dirs = parse_log_format_frames(self.LOG)
        assert len(frames) == 2
        assert dirs == ["RX", "TX"]
        assert ts == ["17:01:02.612", "17:01:02.725"]
        assert len(line_map) == 2

    def test_worker_applies_log_direction(self):
        from gui.parse_worker import parse_text_sync
        frames = parse_text_sync(self.LOG)
        assert frames[0].direction == "子站→主站"
        assert frames[1].direction == "主站→子站"
        assert frames[0].original_timestamp == "17:01:02.612"


class TestFormatDetection:
    """输入格式判定：纯 hex dump 不得被误判成日志模式（否则 0 帧）"""

    def test_dot_dump_is_normal(self):
        from iec104_parser import ParseMode, detect_input_format
        assert detect_input_format("68 04 07 00 00 00 .") is ParseMode.NORMAL

    def test_dot_dump_parses_in_all_paths(self):
        from gui.parse_worker import parse_text_sync
        text = "68 04 07 00 00 00 .\n68 04 13 00 00 00 ."
        assert len(parse_text_sync(text)) == 2

    def test_xn_mode_falls_back_without_markers(self):
        """手动选了 XN3000 但文本没有收发标记行：兜底按普通十六进制解析"""
        from gui.parse_worker import parse_text_sync
        text = "68 04 07 00 00 00\n68 04 13 00 00 00"
        assert len(parse_text_sync(text)) == 2
        assert len(parse_text_sync(text, __import__("iec104_parser").ParseMode.XN3000)) == 2


class TestGeneratorValidation:
    def test_unsupported_type_rejected(self):
        with pytest.raises(ValueError):
            generate_i_frame(120, 6, 1, 0x4001, 0)   # 文件传输未实现

    def test_ioa_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            generate_i_frame(1, 3, 1, 0x1FFFFFF, 1)

    def test_cot_high_byte_rejected(self):
        with pytest.raises(ValueError):
            generate_i_frame(1, 0x106, 1, 0x4001, 1)

    def test_timestamped_types_have_cp24(self):
        """2/4/10/12/14/16 必须带 3 字节 CP24Time2a"""
        for tid in (2, 4, 10, 12, 14, 16):
            raw = bytes.fromhex(
                generate_i_frame(tid, 3, 1, 0x4001, 1.5 if tid == 14 else 1)
                      .replace(" ", ""))
            # 帧总长 = 2(68+len) + 4(ctrl) + 9(asdu头) + 值 + 3(cp24)
            value_len = {2: 1, 4: 1, 10: 3, 12: 3, 14: 5, 16: 5}[tid]
            assert len(raw) == 15 + value_len + 3, f"type {tid} 缺少 CP24Time2a"

    def test_apdu_len_never_exceeds_253(self):
        raw = bytes.fromhex(generate_i_frame(36, 3, 1, 0x4001, 1.5).replace(" ", ""))
        assert raw[1] <= 253 and raw[1] == len(raw) - 2
