"""frame_generator 帧生成器单元测试

锁定 generate_i_frame / generate_s_frame / generate_u_frame 的
帧结构（APCI 长度、控制域、ASDU 布局），并回归 M5 修复：
48/49/50（不带时标设点命令）不得附加 CP56Time2a，
61/62/63（带时标设点命令）必须附加 7 字节 CP56Time2a。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from frame_generator import (
    generate_i_frame, generate_s_frame, generate_u_frame,
    TYPE_INFO,
)


def _frame_bytes(hex_str: str) -> bytes:
    return bytes.fromhex(hex_str.replace(" ", ""))


class TestFrameStructure:
    def test_i_frame_starts_with_68(self):
        f = _frame_bytes(generate_i_frame(9, 3, 1, 0x4001, 100))
        assert f[0] == 0x68
        assert f[1] == len(f) - 2  # APDU 长度 = 帧总长 - 2

    def test_i_frame_apci_control_bytes(self):
        f = _frame_bytes(generate_i_frame(9, 3, 1, 0x4001, 100,
                                          send_seq=3, recv_seq=5))
        # I 帧控制域: SN=(3<<1)=0x06, RN=(5<<1)=0x0A
        assert f[2] == 0x06
        assert f[3] == 0x00
        assert f[4] == 0x0A
        assert f[5] == 0x00

    def test_s_frame(self):
        f = _frame_bytes(generate_s_frame(recv_seq=7))
        assert f[0] == 0x68
        assert f[1] == 0x04          # S 帧固定长度
        assert f[2] == 0x01          # S 帧类型标识
        assert f[4] == 0x0E          # RN=(7<<1)=14
        assert len(f) == 6

    def test_u_frame_known_types(self):
        expected = {
            "STARTDT_ACT": 0x07,
            "STARTDT_CON": 0x0B,
            "STOPDT_ACT": 0x13,
            "STOPDT_CON": 0x23,
            "TESTFR_ACT": 0x43,
            "TESTFR_CON": 0x83,
        }
        for name, byte in expected.items():
            f = _frame_bytes(generate_u_frame(name))
            assert f[2] == byte, name
            assert f[3] == 0 and f[4] == 0 and f[5] == 0
            assert len(f) == 6

    def test_u_frame_default_unknown(self):
        f = _frame_bytes(generate_u_frame("UNKNOWN"))
        assert f[2] == 0x07  # 回退 STARTDT_ACT


class TestIValueLayout:
    """各类型 ID 的 ASDU 布局检查（ASDU 起始于帧偏移 6）"""

    def _asdu(self, type_id, value):
        f = _frame_bytes(generate_i_frame(type_id, 3, 1, 0x4001, value))
        return f[6:], f

    def test_single_point(self):
        asdu, _ = self._asdu(1, 1)
        assert asdu[0] == 1 and asdu[1] == 1  # type_id + VSQ
        # ASDU 头 9 字节: type+VSQ+COT2+CA2+IOA3
        assert asdu[6] == 0x01 and asdu[7] == 0x40  # IOA=0x4001 小端
        assert asdu[9] == 0x01                      # 值(第10字节)

    def test_normalized_value(self):
        asdu, _ = self._asdu(9, 100)
        # 归一化: 值2字节(NVA) + QDS，位于 ASDU 偏移 9..11
        assert asdu[9] == 100 and asdu[10] == 0
        assert asdu[11] == 0  # QDS

    def test_short_float(self):
        import struct
        asdu, _ = self._asdu(13, 1.5)
        val_bytes = struct.unpack('<f', bytes(asdu[9:13]))[0]
        assert abs(val_bytes - 1.5) < 1e-6
        assert asdu[13] == 0  # QDS

    def test_cumulative(self):
        asdu, _ = self._asdu(15, 0x01020304)
        assert bytes(asdu[9:13]) == b'\x04\x03\x02\x01'  # 小端
        assert asdu[13] == 0  # QDS


class TestM5TimedFix:
    """M5 回归：时标附加行为正确"""

    def test_no_timestamp_for_se_na(self):
        """48/49/50 不带时标：48/49=12字节ASDU，50(短浮点4字节)=14字节ASDU"""
        for tid in (48, 49):
            f = _frame_bytes(generate_i_frame(tid, 6, 1, 0x4001, 100))
            assert f[1] == 0x10, f"type {tid} 不应带时标 (APDU_LEN={f[1]:#x})"
            assert len(f) == 2 + 0x10
        f = _frame_bytes(generate_i_frame(50, 6, 1, 0x4001, 1.5))
        assert f[1] == 0x12, f"type 50 短浮点(4字节) 不应带时标 (APDU_LEN={f[1]:#x})"
        assert len(f) == 2 + 0x12

    def test_timestamp_for_se_ta(self):
        """61/62/63 带 7 字节时标：61/62=19字节ASDU，63(短浮点)=21字节ASDU"""
        for tid in (61, 62):
            f = _frame_bytes(generate_i_frame(tid, 6, 1, 0x4001, 100))
            assert f[1] == 0x17, f"type {tid} 应带时标 (APDU_LEN={f[1]:#x})"
            assert len(f) == 2 + 0x17
        f = _frame_bytes(generate_i_frame(63, 6, 1, 0x4001, 1.5))
        assert f[1] == 0x19, f"type 63 应带时标 (APDU_LEN={f[1]:#x})"
        assert len(f) == 2 + 0x19

    def test_clock_sync_has_timestamp(self):
        """103 时钟同步带 CP56Time2a：ASDU = 9 + 7 = 16，APDU_LEN = 0x14"""
        f = _frame_bytes(generate_i_frame(103, 6, 1, 0, 0))
        assert f[1] == 0x14
        assert len(f) == 2 + 0x14

    def test_total_call(self):
        """100 总召唤 QOI：ASDU = 9 + 1 = 10，APDU_LEN = 0x0E"""
        f = _frame_bytes(generate_i_frame(100, 6, 1, 0, 20))
        assert f[6] == 100
        assert f[15] == 20           # QOI = frame偏移15 (6+9)
        assert f[1] == 0x0E

    def test_timed_measurement_types(self):
        """带时标测量值 30/31/34/35/36 必须附加 7 字节 CP56Time2a"""
        # ASDU = 9(头) + 值 + [QDS] + 7(时标)
        expected = {30: 9 + 1 + 7, 31: 9 + 1 + 7, 34: 9 + 3 + 7,
                    35: 9 + 3 + 7, 36: 9 + 5 + 7}
        for tid, asdu_len in expected.items():
            value = 1.5 if tid == 36 else 100
            f = _frame_bytes(generate_i_frame(tid, 3, 1, 0x4001, value))
            assert f[1] == asdu_len + 4, \
                f"type {tid} APDU_LEN={f[1]:#x}, 期望 {asdu_len+4:#x}"
            assert len(f) == 2 + asdu_len + 4

    def test_apdu_len_matches_frame(self):
        """任意生成帧：APDU 长度必须与实际长度一致（防止布局漂移）"""
        for tid, _ in TYPE_INFO.items():
            if tid == 103:
                value = 0
            elif tid in (13, 36, 50, 63):
                value = 1.5
            elif tid in (15,):
                value = 1000
            else:
                value = 100
            f = _frame_bytes(generate_i_frame(tid, 3, 1, 0x4001, value))
            assert f[1] == len(f) - 2, \
                f"type {tid} APDU_LEN={f[1]:#x} != 实际 {len(f)-2:#x}"
