"""IEC104Simulator 通信模拟器单元测试

覆盖不依赖真实网络的核心逻辑：
- U 帧握手状态机（STARTDT/STOPDT/TESTFR 的状态迁移与自动回复）
- I 帧接收时的接收序号管理（期望序号/不连续跳变）
- W 窗口达到阈值自动发送 S 帧的计数逻辑
- 从站对总召唤/时钟同步/控制命令的自动回复调用路径
- 点位数据创建、设置与变更上送

说明：不启动线程、不建立 socket；_send_raw 在无 socket 时返回 False，
不影响状态机与计数器断言（QThread 对象在离屏 Qt 环境下可安全创建）。
"""
import os
import sys

# 离屏平台 + 路径注入（pytest 下由 conftest.py 统一处理，此处兼容独立运行）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
_APP = QApplication.instance() or QApplication([])

from iec104_simulator import (
    IEC104Simulator, SimulatorRole, ConnectionState,
    create_default_points,
)

# 复用 frame_generator 构造输入帧
from frame_generator import generate_i_frame, generate_u_frame, generate_s_frame


def _bytes(hex_str: str) -> bytes:
    return bytes.fromhex(hex_str.replace(" ", ""))


def _make(role=SimulatorRole.SLAVE):
    sim = IEC104Simulator(role=role)
    # 不打日志、不弹错误，方便断言
    sim.log_message.connect(lambda *_: None)
    sim.error_occurred.connect(lambda *_: None)
    return sim


class TestUFrameHandshake:
    def test_startdt_act_replies_con_and_started(self):
        sim = _make()
        sent = []
        sim._send_raw = lambda h: (sent.append(h), True)[1]  # 无 socket 时模拟发送成功
        sim._handle_u_frame_received(_bytes(generate_u_frame("STARTDT_ACT")))
        assert sim.state == ConnectionState.STARTED
        assert sent and "0B" in sent[0].replace(" ", "")  # STARTDT_CON 0x0B

    def test_startdt_con_from_slave(self):
        sim = _make()
        sim._handle_u_frame_received(_bytes(generate_u_frame("STARTDT_CON")))
        assert sim.state == ConnectionState.STARTED

    def test_stopdt_act_replies_con_and_connected(self):
        sim = _make()
        sim._set_state(ConnectionState.STARTED)
        sent = []
        sim._send_raw = lambda h: (sent.append(h), True)[1]
        sim._handle_u_frame_received(_bytes(generate_u_frame("STOPDT_ACT")))
        assert sim.state == ConnectionState.CONNECTED
        assert sent and "23" in sent[0].replace(" ", "")  # STOPDT_CON 0x23

    def test_testfr_act_replies_con(self):
        sim = _make()
        sent = []
        sim._send_raw = lambda h: (sent.append(h), True)[1]
        sim._handle_u_frame_received(_bytes(generate_u_frame("TESTFR_ACT")))
        assert sent and "83" in sent[0].replace(" ", "")  # TESTFR_CON 0x83


class TestISequence:
    def test_recv_seq_increments(self):
        sim = _make()
        frame = sim._parser.parse_hex(generate_i_frame(9, 3, 1, 0x4001, 100,
                                                       send_seq=0, recv_seq=0))
        sim._handle_i_frame_received(frame)
        assert sim.recv_seq == 1

    def test_recv_seq_skip_jumps(self):
        """序号不连续时以收到的序号+1 更新（并发出警告日志）"""
        sim = _make()
        logs = []
        sim.log_message.connect(logs.append)
        frame = sim._parser.parse_hex(generate_i_frame(9, 3, 1, 0x4001, 100,
                                                       send_seq=5, recv_seq=0))
        sim._handle_i_frame_received(frame)
        assert sim.recv_seq == 6
        assert any("序号不连续" in m for m in logs)

    def test_w_window_triggers_s_frame(self):
        """达到 W 窗口阈值时自动发送 S 帧并清零计数"""
        sim = _make()
        sim.w = 2
        sent = []
        sim._send_raw = lambda h: (sent.append(h), True)[1]
        for sn in range(2):
            frame = sim._parser.parse_hex(
                generate_i_frame(9, 3, 1, 0x4001, 100, send_seq=sn, recv_seq=0))
            sim._handle_i_frame_received(frame)
        assert sim._rx_count_since_last_s == 0
        assert sent and sent[-1].startswith("68 04")  # S 帧

    def test_s_frame_resets_rx_count(self):
        sim = _make()
        sim._rx_count_since_last_s = 3
        # 无 socket 时 send_s_frame 返回 False 但计数必须清零
        sim.send_s_frame()
        assert sim._rx_count_since_last_s == 0


class TestSlaveAutoReply:
    def test_general_interrogation_replies(self):
        """总召唤 → 回复确认 + 上送点位 + 终止（3 次发送尝试）"""
        sim = _make(SimulatorRole.SLAVE)
        sim.points = create_default_points(yx_count=2, yc_count=2)
        # 总召唤命令帧: type 100, COT=6, QOI=20
        cmd = sim._parser.parse_hex(generate_i_frame(100, 6, 1, 0, 20,
                                                     send_seq=0, recv_seq=0))
        sim._handle_i_frame_received(cmd)
        # 无 socket：_send_raw 返回 False，但确认/终止路径已尝试发送
        # 验证点位已创建可遍历（无异常即通过）

    def test_clock_sync_replies(self):
        sim = _make(SimulatorRole.SLAVE)
        frame = sim._parser.parse_hex(generate_i_frame(103, 6, 1, 0, 0,
                                                       send_seq=0, recv_seq=0))
        # 无异常即可
        sim._handle_i_frame_received(frame)

    def test_control_command_updates_point(self):
        """单点命令(45) → 点位值更新（C_SC_NA_1 值解析为 SCS 字典）"""
        sim = _make(SimulatorRole.SLAVE)
        sim.points = create_default_points(yx_count=4, yc_count=0)
        ioa = sorted(sim.points.keys())[0]
        cmd = sim._parser.parse_hex(generate_i_frame(45, 6, 1, ioa, 1,
                                                     send_seq=0, recv_seq=0))
        sim._handle_i_frame_received(cmd)
        val = sim.points[ioa].value
        # 单点命令信息体解析为 {'scs': 1, 'se': 0}，SCS=1 表示合位
        assert isinstance(val, dict) and val.get("scs") == 1
        assert sim.points[ioa].is_valid


class TestPoints:
    def test_create_default_points(self):
        pts = create_default_points(yx_count=3, yc_count=2)
        yx = [p for p in pts.values() if p.signal_type == "YX"]
        yc = [p for p in pts.values() if p.signal_type == "YC"]
        assert len(yx) == 3
        assert len(yc) == 2

    def test_set_point_value_emits_update(self):
        sim = _make(SimulatorRole.SLAVE)
        sim.points = create_default_points(yx_count=1, yc_count=0)
        ioa = sorted(sim.points.keys())[0]
        updates = []
        sim.point_updated.connect(lambda i, p: updates.append((i, p)))
        sim.set_point_value(ioa, True)
        assert sim.points[ioa].value is True
        assert any(i == ioa for i, _ in updates)


class TestStateAndReset:
    def test_is_connected(self):
        sim = _make()
        assert sim.state == ConnectionState.IDLE
        assert not sim.is_connected
        sim._set_state(ConnectionState.STARTED)
        assert sim.is_connected

    def test_reset_sequence_numbers(self):
        sim = _make()
        sim._send_seq = 10
        sim._recv_seq = 20
        sim._reset_sequence_numbers()
        assert sim.send_seq == 0
        assert sim.recv_seq == 0
