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
import time

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
from frame_generator import generate_i_frame, generate_u_frame


def _bytes(hex_str: str) -> bytes:
    return bytes.fromhex(hex_str.replace(" ", ""))


def _make(role=SimulatorRole.SLAVE):
    sim = IEC104Simulator(role=role)
    # 不打日志、不弹错误，方便断言
    sim.log_message.connect(lambda *_: None)
    sim.error_occurred.connect(lambda *_: None)
    return sim


class TestControlCommandDecoding:
    """控制命令类型集合与 DCS 解码回归（曾因 13/14 误入控制集合导致遥测全丢）"""

    def test_control_tids_exclude_measurement_types(self):
        # 13/14 是监视方向的遥测短浮点（M_ME_NC_1/TC_1），不是控制命令。
        # 混入后主站会把从站上送的遥测当控制回显全部丢弃 → 遥测解析不出来
        assert 13 not in IEC104Simulator._CONTROL_TIDS
        assert 14 not in IEC104Simulator._CONTROL_TIDS
        for tid in (45, 46, 47, 48, 49, 50, 61, 62, 63):
            assert tid in IEC104Simulator._CONTROL_TIDS

    def test_master_learns_yc_from_gi_response(self):
        sim = _make(SimulatorRole.MASTER)
        sim.points = create_default_points(yx_count=1, yc_count=1)
        hex_str = generate_i_frame(type_id=13, cot=20, common_addr=1,
                                   ioa=0x4001, value=185.5,
                                   send_seq=0, recv_seq=0)
        frame = sim._parser.parse_hex(hex_str, source="recv")
        sim._auto_reply_master(frame)
        pt = sim.points[0x4001]
        assert pt.is_valid, "总召唤响应的遥测必须更新点位"
        assert pt.value == 185.5

    def test_double_command_dcs_off_not_mapped_to_on(self):
        # DCS 占 bit2-3：0x04=分(DCS=1)、0x08=合(DCS=2)。
        # 直接 bool(0x04) 会把"分"误判为"合"
        sim = _make(SimulatorRole.SLAVE)
        sim.points = create_default_points(yx_count=1, yc_count=0, yk_count=1)
        sim.yk_links = {0x6001: 0x0001}
        sim._set_state(ConnectionState.STARTED)
        sent = []
        sim._send_raw = lambda h: (sent.append(h), True)[1]
        for dcs_raw, expect_pt, expect_yx in ((1 << 2, 1, False), (2 << 2, 2, True)):
            raw = _bytes(generate_i_frame(type_id=46, cot=6, common_addr=1,
                                          ioa=0x6001, value=dcs_raw, select=False))
            hex_str = ' '.join(f'{b:02X}' for b in raw)
            frame = sim._parser.parse_hex(hex_str, source="recv")
            sim._handle_control_command(frame, 46, 1, raw)
            assert sim.points[0x6001].value == expect_pt
            assert sim.points[0x0001].value is expect_yx


class TestProtocolParams:
    """协议参数（公共地址/IOA基址/SOE）对报文行为的影响"""

    def test_create_default_points_custom_bases(self):
        pts = create_default_points(
            2, 1, 1, 1,
            ioa_bases={"YX": 0x0100, "YC": 0x0200, "YK": 0x0300, "YT": 0x0400})
        for ioa in (0x0100, 0x0101, 0x0200, 0x0300, 0x0400):
            assert ioa in pts
        assert pts[0x0100].signal_type == "YX"
        assert pts[0x0200].signal_type == "YC"
        # 基址缺省时回到默认起点
        pts2 = create_default_points(1, 0, 0, 0)
        assert 0x0001 in pts2

    def _capturing_sim(self, role=SimulatorRole.SLAVE):
        sim = _make(role)
        sent = []
        sim._send_raw = lambda h: (sent.append(h), True)[1]
        sim._set_state(ConnectionState.STARTED)
        sim._auto_send_enabled = True
        return sim, sent

    def test_auto_upload_uses_configured_common_addr(self):
        sim, sent = self._capturing_sim()
        sim.points = create_default_points(yx_count=1, yc_count=0)
        sim.common_addr = 7
        sim.set_point_value(0x0001, True)
        assert sent, "变化上送应有帧发出"
        raw = _bytes(sent[0])
        # ASDU 公共地址位于 6(APCI)+4(类型/VSQ/COT) 之后：低字节在前
        assert raw[10] == 7 and raw[11] == 0

    def test_soe_upload_uses_timestamped_types(self):
        sim, sent = self._capturing_sim()
        sim.points = create_default_points(yx_count=1, yc_count=1)
        sim.soe_enabled = True
        sim.set_point_value(0x0001, True)
        sim.set_point_value(0x4001, 5.0)
        types = [_bytes(h)[6] for h in sent]
        assert 30 in types, "SOE 遥信应变址为 M_SP_TB_1 (type 30)"
        assert 36 in types, "SOE 遥测应变址为 M_ME_TF_1 (type 36)"

    def test_soe_disabled_keeps_plain_types(self):
        sim, sent = self._capturing_sim()
        sim.points = create_default_points(yx_count=1, yc_count=0)
        sim.soe_enabled = False
        sim.set_point_value(0x0001, True)
        assert _bytes(sent[0])[6] == 1


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

    def test_s_frame_failure_keeps_rx_count(self):
        sim = _make()
        sim._rx_count_since_last_s = 3
        sim._pending_s_timer = True
        # 无 socket 时 send_s_frame 返回 False；确认计数不得清零——否则
        # 丢失的 S 帧永不重发，对端卡死在 K 窗口（t2 定时器负责重试）
        sim.send_s_frame()
        assert sim._rx_count_since_last_s == 3
        assert sim._pending_s_timer is True

    def test_s_frame_success_resets_rx_count(self):
        sim = _make()
        sim._client_socket = FakeSocket()
        sim._rx_count_since_last_s = 3
        sim._pending_s_timer = True
        sim.send_s_frame()
        assert sim._rx_count_since_last_s == 0
        assert sim._pending_s_timer is False


class FakeSocket:
    """记录 sendall 内容，用于验证从站确实产生了回复帧（替代无 socket 的
    假绿断言）。"""
    def __init__(self):
        self.sent = []
    def sendall(self, data: bytes):
        self.sent.append(data.hex().upper())


class TestSlaveAutoReply:
    def test_general_interrogation_replies(self):
        """总召唤 → 回复确认 + 上送点位 + 终止（验证实际产生了回复帧）"""
        sim = _make(SimulatorRole.SLAVE)
        sim._set_state(ConnectionState.STARTED)
        sim.points = create_default_points(yx_count=2, yc_count=2)
        sim._client_socket = FakeSocket()
        # 总召唤命令帧: type 100, COT=6, QOI=20
        cmd = sim._parser.parse_hex(generate_i_frame(100, 6, 1, 0, 20,
                                                     send_seq=0, recv_seq=0))
        sim._handle_i_frame_received(cmd)
        # 此前只是"不抛异常"，现在验证确实发送了（确认 + ≥1 点位 + 终止）
        assert len(sim._client_socket.sent) >= 3
        # 首帧为总召唤激活确认（type 100, COT=7）：APCI 6 字节后第 7 字节为 type_id
        assert int(sim._client_socket.sent[0][12:14], 16) == 100

    def test_clock_sync_replies(self):
        """时钟同步 → 回复确认（type 103, COT=7）"""
        sim = _make(SimulatorRole.SLAVE)
        sim._set_state(ConnectionState.STARTED)
        sim._client_socket = FakeSocket()
        frame = sim._parser.parse_hex(generate_i_frame(103, 6, 1, 0, 0,
                                                       send_seq=0, recv_seq=0))
        sim._handle_i_frame_received(frame)
        assert len(sim._client_socket.sent) >= 1
        assert int(sim._client_socket.sent[0][12:14], 16) == 103


class TestProtocolTimers:
    """t1/t3 协议超时 → 置位重连标志（由外层连接循环执行重连）"""

    def test_t1_timeout_requests_reconnect(self):
        sim = _make(SimulatorRole.MASTER)
        sim._set_state(ConnectionState.STARTED)
        sim._client_socket = FakeSocket()
        sim._last_send_time = time.time() - sim.t1 - 1   # 已超时
        sim._handle_timers()
        assert sim._reconnect_requested is True

    def test_t3_first_unacked_increments_counter(self):
        sim = _make(SimulatorRole.MASTER)
        sim._set_state(ConnectionState.STARTED)
        sim._client_socket = FakeSocket()
        sim._last_activity_time = time.time() - sim.t3 - 1
        sim._testfr_unacked = 0
        sim._handle_timers()
        # 首次无应答：发出 TESTFR_ACT，计数 +1，尚未判死
        assert sim._testfr_unacked == 1
        assert len(sim._client_socket.sent) >= 1
        assert sim._reconnect_requested is False

    def test_t3_two_unacked_triggers_reconnect(self):
        sim = _make(SimulatorRole.MASTER)
        sim._set_state(ConnectionState.STARTED)
        sim._client_socket = FakeSocket()
        sim._last_activity_time = time.time() - sim.t3 - 1
        sim._testfr_unacked = 2   # 已连续 2 次无应答
        sim._handle_timers()
        assert sim._reconnect_requested is True

    def test_testfr_con_resets_unacked(self):
        sim = _make(SimulatorRole.MASTER)
        sim._set_state(ConnectionState.STARTED)
        sim._testfr_unacked = 2
        sim._handle_u_frame_received(_bytes(generate_u_frame("TESTFR_CON")))
        assert sim._testfr_unacked == 0

class TestSBOSelectBeforeOperate:
    """SBO 预制流程：选择 → 执行；未预制拒绝；值不一致拒绝"""

    def _receive(self, sim, tid, ioa, value, select):
        """构造并下发一条控制命令帧（同时透传原始字节，浮点设点依赖它解码）"""
        hex_str = generate_i_frame(tid, 6, 1, ioa, value,
                                   send_seq=0, recv_seq=0, select=select)
        raw = bytes.fromhex(hex_str.replace(" ", ""))
        frame = sim._parser.parse_hex(hex_str)
        sim._handle_i_frame_received(frame, raw)

    def _sent_cots(self, sim, tid):
        """统计发送帧中指定 type_id 的 COT 集合"""
        cots = []
        for hx in sim._sent:
            b = bytes.fromhex(hx.replace(" ", ""))
            if b[6] == tid:
                # COT 在 ASDU 偏移 2（type/vsq 之后），即帧偏移 8
                cots.append(b[8] & 0x3F)
        return cots

    def _make_slave(self):
        sim = _make(SimulatorRole.SLAVE)
        sim.points = create_default_points(yx_count=4, yc_count=0)
        # 从站需处于数据传输状态才能回发确认帧
        sim._set_state(ConnectionState.STARTED)
        sim._sent = []
        sim._send_raw = lambda h: (sim._sent.append(h), True)[1]
        return sim

    def test_full_sbo_flow(self):
        """选择→确认→执行→确认→点位生效"""
        sim = self._make_slave()
        ioa = sorted(sim.points.keys())[0]
        # 选择(合)
        self._receive(sim, 45, ioa, 1, select=True)
        assert self._sent_cots(sim, 45) == [7], "选择应回 COT=7(选择确认)"
        assert ioa in sim._select_locks
        # 执行(合)
        sim._sent.clear()
        self._receive(sim, 45, ioa, 1, select=False)
        assert self._sent_cots(sim, 45) == [7], "执行应回 COT=7(执行确认)"
        assert ioa not in sim._select_locks, "执行后应清除预制锁"
        assert sim.points[ioa].value & 0xFF == 1  # 合

    def test_execute_without_select_is_direct_operate(self):
        """未预制直接执行（SBO 关闭场景）→ 按直接操作处理，回 COT=7 并生效

        此前的旧实现会把任何无预制锁的执行一律拒绝(COT=8)，导致主站在
        "关闭预制"模式下点击合/分/设定无响应。模拟器作为测试工具应接受直接操作。
        """
        sim = self._make_slave()
        ioa = sorted(sim.points.keys())[0]
        self._receive(sim, 45, ioa, 1, select=False)
        assert self._sent_cots(sim, 45) == [7], "无预制锁的执行应回 COT=7(直接操作确认)"
        assert ioa not in sim._select_locks
        assert sim.points[ioa].value & 0xFF == 1  # 直接操作同样生效（合）

    def test_mismatched_value_rejected(self):
        """预制值(合)与执行值(分)不一致 → 拒绝"""
        sim = self._make_slave()
        ioa = sorted(sim.points.keys())[0]
        self._receive(sim, 45, ioa, 1, select=True)  # 预制"合"
        sim._sent.clear()
        self._receive(sim, 45, ioa, 0, select=False)  # 执行"分"
        assert self._sent_cots(sim, 45) == [8], "值不一致应回 COT=8"
        assert ioa not in sim._select_locks

    def test_yt_float_sbo_flow(self):
        """遥调(type 50 短浮点) SBO：选择→确认→执行→确认→浮点值生效"""
        sim = _make(SimulatorRole.SLAVE)
        sim.points = create_default_points(yx_count=0, yc_count=0,
                                           yk_count=0, yt_count=2)
        sim._set_state(ConnectionState.STARTED)
        sim._sent = []
        sim._send_raw = lambda h: (sim._sent.append(h), True)[1]
        ioa = sorted(sim.points.keys())[0]
        # 选择(浮点 12.5)
        self._receive(sim, 50, ioa, 12.5, select=True)
        assert self._sent_cots(sim, 50) == [7], "遥调选择应回 COT=7(选择确认)"
        assert ioa in sim._select_locks
        # 执行(同一浮点)
        sim._sent.clear()
        self._receive(sim, 50, ioa, 12.5, select=False)
        assert self._sent_cots(sim, 50) == [7], "遥调执行应回 COT=7(执行确认)"
        assert ioa not in sim._select_locks
        assert abs(sim.points[ioa].value - 12.5) < 1e-6  # 浮点值正确落地

    def test_yt_float_mismatched_rejected(self):
        """遥调预制值(12.5)与执行值(99.0)不一致 → 拒绝"""
        sim = _make(SimulatorRole.SLAVE)
        sim.points = create_default_points(yx_count=0, yc_count=0,
                                           yk_count=0, yt_count=2)
        sim._set_state(ConnectionState.STARTED)
        sim._sent = []
        sim._send_raw = lambda h: (sim._sent.append(h), True)[1]
        ioa = sorted(sim.points.keys())[0]
        self._receive(sim, 50, ioa, 12.5, select=True)  # 预制 12.5
        sim._sent.clear()
        self._receive(sim, 50, ioa, 99.0, select=False)  # 执行 99.0
        assert self._sent_cots(sim, 50) == [8], "遥调值不一致应回 COT=8"
        assert ioa not in sim._select_locks


class TestControlAckSignal:
    """主站收到从站控制确认帧时发出 control_ack 信号（GUI 依赖此点亮按钮）"""

    def _make_master(self):
        sim = _make(SimulatorRole.MASTER)
        sim._set_state(ConnectionState.STARTED)
        sim.points = create_default_points(yx_count=2, yc_count=0)
        sim._sent = []
        sim._send_raw = lambda h: (sim._sent.append(h), True)[1]
        return sim

    def test_select_confirm_emits_ack(self):
        """从站回 COT=7(选择确认) → 主站发出 (ioa, 7, 45)"""
        sim = self._make_master()
        acks = []
        sim.control_ack.connect(lambda i, c, t: acks.append((i, c, t)))
        ioa = sorted(sim.points.keys())[0]
        # 从站选择确认帧: type 45, COT=7, S/E=0
        hex_str = generate_i_frame(45, 7, 1, ioa, 0x01,
                                   send_seq=0, recv_seq=0, select=True)
        sim._handle_i_frame_received(sim._parser.parse_hex(hex_str))
        assert acks == [(ioa, 7, 45)]

    def test_activation_termination_emits_ack(self):
        """从站回 COT=8(激活终止) → 主站发出 (ioa, 8, 45)"""
        sim = self._make_master()
        acks = []
        sim.control_ack.connect(lambda i, c, t: acks.append((i, c, t)))
        ioa = sorted(sim.points.keys())[0]
        hex_str = generate_i_frame(45, 8, 1, ioa, 0x81,
                                   send_seq=0, recv_seq=0, select=False)
        sim._handle_i_frame_received(sim._parser.parse_hex(hex_str))
        assert acks == [(ioa, 8, 45)]

    def test_data_frame_no_ack(self):
        """普通测量数据帧不应触发 control_ack"""
        sim = self._make_master()
        acks = []
        sim.control_ack.connect(lambda i, c, t: acks.append((i, c, t)))
        hex_str = generate_i_frame(9, 3, 1, 0x4001, 100,
                                   send_seq=0, recv_seq=0)
        sim._handle_i_frame_received(sim._parser.parse_hex(hex_str))
        assert acks == []


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
