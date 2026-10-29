"""
IEC60870-5-104 通信模拟器
支持主站/从站模式，TCP 通信 + APCI 状态机
"""

import socket
import threading
import time
import logging
import struct
from enum import Enum, IntEnum
from dataclasses import dataclass
from typing import Optional, Dict, Any

from PyQt5.QtCore import QThread, pyqtSignal

from iec104_parser import hex_to_bytes, IEC104Parser
from frame_generator import generate_i_frame, generate_s_frame, generate_u_frame
from iec104_protocol import SBO_COMMAND_TYPE_IDS, se_bit, value_mask

logger = logging.getLogger(__name__)


# ============================================================================
# 协议常量
# ============================================================================
class SimulatorRole(Enum):
    MASTER = "master"
    SLAVE = "slave"


class ConnectionState(Enum):
    IDLE = "空闲"
    CONNECTING = "连接中"
    CONNECTED = "已连接"
    STARTED = "数据传输中"


class UFunc(IntEnum):
    """U 帧功能码 (控制域第1字节)"""
    STARTDT_ACT = 0x07
    STARTDT_CON = 0x0B
    STOPDT_ACT = 0x13
    STOPDT_CON = 0x23
    TESTFR_ACT = 0x43
    TESTFR_CON = 0x83


# IEC 104 协议默认参数
DEFAULT_T1 = 15.0   # 发送 I 帧后等待确认超时 (秒)
DEFAULT_T2 = 10.0   # 收到 I 帧后延迟发送 S 帧的时间 (秒)
DEFAULT_T3 = 20.0   # 空闲时发送 TESTFR 的间隔 (秒)
DEFAULT_K = 12      # 发送窗口大小 (最大未确认 I 帧数)
DEFAULT_W = 8       # 接收窗口大小 (发送 S 帧确认前可接收的最大 I 帧数)


def _friendly_connect_error(e: Exception) -> str:
    """把 socket 报错翻成用户能懂的原因（WinError 数字对用户无意义）"""
    s = str(e)
    if "10061" in s or isinstance(e, ConnectionRefusedError):
        return "对端拒绝连接（目标 IP:端口没有服务在监听，请检查 IP/端口或先启动从站）"
    if "10060" in s or isinstance(e, socket.timeout):
        return "连接超时（网络不通或防火墙拦截）"
    if "10049" in s:
        return "IP 地址无效（本机没有该网络地址）"
    if "11001" in s:
        return "主机名无法解析"
    return s


# ============================================================================
# 点位数据
# ============================================================================
@dataclass
class PointData:
    """四遥点位数据"""
    ioa: int                           # 信息对象地址
    signal_type: str = "YC"            # YX/YC/YK/YT
    value: Any = 0                     # 当前值
    quality: int = 0                   # 品质描述词 (0=有效)
    timestamp: str = ""                # 最后更新时间
    is_valid: bool = False             # 是否收到过有效数据 (主站用)
    description: str = ""              # 描述/别名


# IOA 地址范围默认映射
DEFAULT_IOA_RANGES = {
    "YX": (0x0001, 0x4000),    # 遥信
    "YC": (0x4001, 0x5000),    # 遥测
    "YK": (0x6001, 0x6100),    # 遥控
    "YT": (0x6201, 0x6400),    # 遥调
}


def create_default_points(yx_count: int = 4, yc_count: int = 8,
                          yk_count: int = 2, yt_count: int = 2,
                          ioa_bases: Optional[Dict[str, int]] = None) -> Dict[int, PointData]:
    """创建默认点位表

    ioa_bases: 各信号类型的 IOA 基址（对齐专业工具的可配置项），
        缺省用 DEFAULT_IOA_RANGES 起点。建议仍按 YX/YC/YK/YT 分区，
        否则对端按地址范围自动分类时会归错类。
    """
    bases = {
        "YX": DEFAULT_IOA_RANGES["YX"][0],
        "YC": DEFAULT_IOA_RANGES["YC"][0],
        "YK": DEFAULT_IOA_RANGES["YK"][0],
        "YT": DEFAULT_IOA_RANGES["YT"][0],
    }
    if ioa_bases:
        for k, v in ioa_bases.items():
            if k in bases and v:
                bases[k] = v
    points = {}
    for i in range(yx_count):
        ioa = bases["YX"] + i
        points[ioa] = PointData(ioa=ioa, signal_type="YX", value=False)
    for i in range(yc_count):
        ioa = bases["YC"] + i
        points[ioa] = PointData(ioa=ioa, signal_type="YC", value=0.0)
    for i in range(yk_count):
        ioa = bases["YK"] + i
        points[ioa] = PointData(ioa=ioa, signal_type="YK", value=0)
    for i in range(yt_count):
        ioa = bases["YT"] + i
        points[ioa] = PointData(ioa=ioa, signal_type="YT", value=0.0)
    return points


# ============================================================================
# IEC104 模拟器核心
# ============================================================================
class IEC104Simulator(QThread):
    """IEC104 通信模拟器 - 运行在独立线程"""

    # 信号
    frame_sent = pyqtSignal(str)           # 发送的帧 (hex)
    frame_received = pyqtSignal(str)       # 接收的帧 (hex)
    state_changed = pyqtSignal(str)        # 连接状态变化
    error_occurred = pyqtSignal(str)       # 错误信息
    log_message = pyqtSignal(str)          # 日志消息
    connection_established = pyqtSignal()  # 连接建立
    connection_lost = pyqtSignal()         # 连接断开
    point_updated = pyqtSignal(int, object)  # 点位更新 (ioa, PointData)
    control_ack = pyqtSignal(int, int, int)  # 控制命令确认 (ioa, cot_cause, tid)

    def __init__(self, role: SimulatorRole = SimulatorRole.MASTER, parent=None):
        super().__init__(parent)
        self.role = role
        self.host = "0.0.0.0"
        self.port = 2404
        self._state = ConnectionState.IDLE
        self._running = False
        self._socket: Optional[socket.socket] = None
        self._client_socket: Optional[socket.socket] = None
        self._server_socket: Optional[socket.socket] = None

        # 序列号
        self._send_seq = 0
        self._recv_seq = 0

        # 点位数据 {ioa: PointData}
        self.points: Dict[int, PointData] = {}
        self._auto_send_enabled = False

        # 接收计数器 (用于 W 窗口控制)
        self._rx_count_since_last_s = 0

        # TCP 流重组缓冲：recv 可能给出半包/粘包，跨包缓存直到凑出完整帧
        self._rx_buffer = bytearray()
        # 发送锁：GUI 线程(手动发送/定时上送)与工作线程(t3 TESTFR/t2 S帧)
        # 可能并发 sendall，无锁会交错写出损坏字节流。
        # RLock：send_i_frame 需要在同一调用栈内再进入 _send_raw，
        # 且"取序号→生成帧→发送→自增"必须整体原子，普通 Lock 会自死锁。
        self._send_lock = threading.RLock()

        # 发送窗口：已发出但尚未被确认的 I 帧序号（用于 K 窗口限制）
        self._unacknowledged_i = 0

        # SBO 预制状态：{ioa: (type_id, value)}，记录已选择待执行的遥控/遥调
        self._select_locks: Dict[int, tuple] = {}

        # 遥控/遥调 → 关联遥信/遥测点位映射（由 GUI 提供并同步）。
        # 遥控/遥调本质是写点位：执行结果写入关联点位并变化上送，
        # 不参与总召唤、也不靠控制回显帧传递状态。
        self.yk_links: Dict[int, int] = {}
        self.yt_links: Dict[int, int] = {}

        # 协议参数（GUI 协议参数对话框可配置，对齐 lib60870/QTester104 等
        # 专业工具暴露的可配置项）
        self.t1 = DEFAULT_T1
        self.t2 = DEFAULT_T2
        self.t3 = DEFAULT_T3
        self.k = DEFAULT_K
        self.w = DEFAULT_W
        self.common_addr = 1        # ASDU 公共地址（站地址）
        self.soe_enabled = False    # 遥信/遥测带 CP56Time2a 时标 (SOE)

        # 协议超时重连判据：t1（I帧确认超时）/ t3（TESTFR 连续无应答）置位后，
        # _handle_connection 退出内层循环，外层 while 重连。
        self._testfr_unacked = 0
        self._reconnect_requested = False

        # 总召唤响应队列：[(ioa, PointData)...]，末尾 (None, None) 为
        # ACTTERM 哨兵。按 K 窗口分段发送，避免一次发满后被丢弃。
        self._gi_queue: list = []
        self._gi_ca = 1
        self._gi_cot = 20

        # 定时器状态
        self._last_send_time = 0      # 最后发送 I 帧的时间
        self._last_rx_time = 0        # 最后收到帧的时间
        self._last_activity_time = 0  # 最后活动时间
        self._pending_s_timer = False # 是否有待发送的 S 帧

        # 自动行为
        self._auto_gi = True          # 主站收到 STARTDT_CON 后自动总召唤

        # 解析器 (用于解析收到的帧)
        self._parser = IEC104Parser()

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state in (ConnectionState.CONNECTED, ConnectionState.STARTED)

    @property
    def send_seq(self) -> int:
        return self._send_seq

    @property
    def recv_seq(self) -> int:
        return self._recv_seq

    def _set_state(self, new_state: ConnectionState):
        """更新连接状态"""
        if self._state != new_state:
            old = self._state
            self._state = new_state
            self.state_changed.emit(f"{old.value} → {new_state.value}")
            self.log_message.emit(f"[状态] {old.value} → {new_state.value}")

    # ==================================================================
    # 连接管理
    # ==================================================================

    def start_server(self, host: str = "0.0.0.0", port: int = 2404):
        """启动 TCP 服务端（从站模式）"""
        self.host = host
        self.port = port
        self.role = SimulatorRole.SLAVE
        self._running = True
        self.start()

    def connect_to(self, host: str = "127.0.0.1", port: int = 2404):
        """连接到服务端（主站模式）"""
        self.host = host
        self.port = port
        self.role = SimulatorRole.MASTER
        self._running = True
        self.start()

    def stop(self):
        """停止模拟器"""
        self._running = False
        self._close_sockets()
        self._set_state(ConnectionState.IDLE)

    def _close_sockets(self):
        """关闭所有 socket

        先 shutdown(SHUT_RDWR) 再 close，确保对端立即感知半关闭、避免
        TIME_WAIT 堆积（此前只 close，对端可能短暂认为连接仍可用）。
        """
        for sock in [self._client_socket, self._server_socket, self._socket]:
            if sock:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    sock.close()
                except Exception:
                    pass
        self._client_socket = None
        self._server_socket = None
        self._socket = None

    # ==================================================================
    # QThread 主循环
    # ==================================================================

    def run(self):
        """线程主循环"""
        try:
            if self.role == SimulatorRole.SLAVE:
                self._run_server()
            else:
                self._run_client()
        except Exception as e:
            self.error_occurred.emit(f"模拟器异常: {e}")
            logger.error("模拟器异常", exc_info=True)
        finally:
            self._close_sockets()
            self._set_state(ConnectionState.IDLE)
            self.connection_lost.emit()

    def _run_server(self):
        """服务端主循环"""
        self._set_state(ConnectionState.CONNECTING)
        self.log_message.emit(f"[服务端] 启动监听 {self.host}:{self.port}")

        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_socket.settimeout(1.0)
        try:
            self._server_socket.bind((self.host, self.port))
            self._server_socket.listen(1)
        except OSError as e:
            # 端口被占用等：给出可读原因并结束线程，而不是抛通用异常
            self.error_occurred.emit(
                f"[服务端] 监听 {self.host}:{self.port} 失败（端口可能已被占用）: {e}")
            return

        while self._running:
            self.log_message.emit("[服务端] 等待客户端连接...")
            try:
                client, addr = self._server_socket.accept()
                self._client_socket = client
                self._client_socket.settimeout(0.5)
                self.log_message.emit(f"[服务端] 客户端已连接: {addr}")
                self._set_state(ConnectionState.CONNECTED)
                self.connection_established.emit()
                self._reset_sequence_numbers()
                self._handle_connection()
            except socket.timeout:
                continue
            except OSError:
                break

    def _run_client(self):
        """客户端主循环"""
        while self._running:
            self._set_state(ConnectionState.CONNECTING)
            self.log_message.emit(f"[客户端] 连接到 {self.host}:{self.port}...")

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect((self.host, self.port))
                self._client_socket = sock
                self._client_socket.settimeout(0.5)
                self.log_message.emit("[客户端] 连接成功")
                self._set_state(ConnectionState.CONNECTED)
                self.connection_established.emit()
                self._reset_sequence_numbers()
                # 主站连接后自动发送 STARTDT_ACT 握手
                self.send_startdt()
                self._handle_connection()
            except (ConnectionRefusedError, socket.timeout, OSError) as e:
                self.error_occurred.emit(f"[客户端] 连接失败: {_friendly_connect_error(e)}")
                if self._running:
                    self.log_message.emit("[客户端] 3 秒后重试…（点击\"停止\"可中止）")
                    # 可中断等待：整段 time.sleep(3) 会让 stop() 后线程迟迟不退出
                    for _ in range(30):
                        if not self._running:
                            break
                        time.sleep(0.1)
            finally:
                if self._client_socket:
                    try:
                        self._client_socket.close()
                    except Exception:
                        pass
                    self._client_socket = None

    def _handle_connection(self):
        """处理已建立的连接 - 接收循环 + 定时任务"""
        self._reconnect_requested = False
        self._testfr_unacked = 0
        while self._running and self._client_socket:
            try:
                # 接收数据
                self._receive_and_process()
                # 处理定时任务（t1/t3 协议超时可能置位重连标志）
                self._handle_timers()
                if self._reconnect_requested:
                    self.log_message.emit("[连接] 协议超时，触发重连…")
                    break
            except ConnectionResetError:
                self.log_message.emit("[连接] 对端断开")
                break
            except ConnectionAbortedError:
                break
            except OSError:
                break

    # ==================================================================
    # 数据收发
    # ==================================================================

    def _receive_and_process(self):
        """接收并处理数据

        TCP 是字节流：recv 可能返回半包（帧不完整）或粘包（多帧一次到达），
        这里用跨包缓冲 _rx_buffer 重组——凑齐"0x68 + 长度域"声明的完整帧才上送，
        残留字节留待下一次 recv 拼接；起始字节非法时逐字节重同步。
        """
        # 局部引用 + None 检查：stop() 可能在 GUI 线程把 _client_socket 置 None，
        # 直接 self._client_socket.recv 存在"判空后属性被改"的竞态
        sock = self._client_socket
        if sock is None:
            return
        try:
            data = sock.recv(4096)
        except socket.timeout:
            return
        if not data:
            self.log_message.emit("[连接] 对端关闭连接")
            self._client_socket = None
            return

        self._last_activity_time = time.time()
        self._rx_buffer.extend(data)
        # 防御：对端异常刷垃圾字节时丢弃最旧数据，避免缓冲无限增长
        if len(self._rx_buffer) > 256 * 1024:
            del self._rx_buffer[:128 * 1024]

        offset = 0
        buf = self._rx_buffer
        while offset + 2 <= len(buf):
            if buf[offset] != 0x68:
                offset += 1          # 重同步：跳过非法字节找帧头
                continue
            apdu_len = buf[offset + 1]
            if not (4 <= apdu_len <= 253):
                offset += 1          # 长度域非法，视为流内垃圾，重同步
                continue
            frame_len = apdu_len + 2
            if offset + frame_len > len(buf):
                break                # 半包：留给下次 recv 拼接
            frame_data = bytes(buf[offset:offset + frame_len])
            offset += frame_len
            self._process_received_frame(frame_data)

        # 消费已处理字节，保留未凑齐的尾部
        if offset:
            del self._rx_buffer[:offset]

    def _process_received_frame(self, frame_data: bytes):
        """处理一个收到的完整帧"""
        hex_str = ' '.join(f'{b:02X}' for b in frame_data)
        self.frame_received.emit(hex_str)

        # 快速判断帧类型 (不依赖 parser)
        if len(frame_data) < 6:
            return
        ctrl1 = frame_data[2]
        U_FRAME_BYTES = {0x07, 0x0B, 0x13, 0x23, 0x43, 0x83}
        if ctrl1 in U_FRAME_BYTES:
            ftype = "U-format"
        elif (ctrl1 & 0x01) == 0:
            ftype = "I-format"        # bit0=0 → I帧
        else:
            ftype = "S-format"        # bit0=1 → S帧

        # 解析帧 (仅 I 帧需要完整 ASDU 解析)
        if ftype == "I-format":
            self._parser.clear()
            frame = self._parser.parse_hex(hex_str, source="recv")
            if frame.apci:
                desc = frame.description or ""
                self.log_message.emit(f"[收] {ftype} {desc[:60]}")
                self._handle_i_frame_received(frame, frame_data)
            else:
                # 此前无条件调用 _handle_i_frame_received，而后者首行即访问
                # frame.apci.send_seq —— 畸形帧（apci 为 None）直接抛
                # AttributeError 穿透到 run()，工作线程死亡、连接中断。
                self.log_message.emit(f"[警告] I帧解析失败，已忽略: {hex_str[:40]}")
        elif ftype == "S-format":
            rn = ((frame_data[5] & 0x7F) << 8 | (frame_data[4] & 0xFE)) >> 1
            self.log_message.emit(f"[收] S帧 RN={rn}")
            self._handle_s_frame_received_raw(rn)
        elif ftype == "U-format":
            desc = self._parser._describe_u_frame(frame_data[2:6])
            self.log_message.emit(f"[收] {desc}")
            self._handle_u_frame_received(frame_data)

    def _handle_i_frame_received(self, frame, raw=None):
        """处理收到的 I 帧"""
        # 更新接收序号
        sn = frame.apci.send_seq
        if sn == self._recv_seq:
            self._recv_seq = (self._recv_seq + 1) % 32768
        else:
            self.log_message.emit(f"[警告] 序号不连续: 期望{self._recv_seq}, 收到{sn}")
            self._recv_seq = (sn + 1) % 32768

        # I 帧捎带确认：对方用 N(R) 确认我方已发的 I 帧，据此释放 K 窗口。
        # 此前只有 S 帧会更新 _unacknowledged_i，捎带确认场景下窗口永不释放
        # → 发满 k 帧后所有后续 I 帧（含总召唤 ACTTERM）被静默丢弃。
        self._release_send_window(frame.apci.recv_seq)
        # 捎带确认同样确认了我方未决的发送，t1 计时应复位——否则已确认
        # 的发送在 15s 后仍被误判"确认超时"→ 无谓断连重连（总召唤数据
        # 可能半途被从站 _reset_sequence_numbers 清空）。
        self._last_send_time = 0

        self._rx_count_since_last_s += 1
        self._last_rx_time = time.time()
        self._pending_s_timer = True

        # 自动回复逻辑
        if self.role == SimulatorRole.SLAVE:
            self._auto_reply_slave(frame, raw)
        elif self.role == SimulatorRole.MASTER:
            self._auto_reply_master(frame)

        # 达到 W 窗口大小，立即发送 S 帧
        if self._rx_count_since_last_s >= self.w:
            self.send_s_frame()


    def _release_send_window(self, rn: int):
        """按对方 N(R) 缩减未确认发送窗口（K 窗口），S 帧与 I 帧捎带确认共用。

        rn 为对方期望的下一个序号，即已确认 [0, rn-1]；我方已发到
        _send_seq-1，故未确认数为 (_send_seq - rn) mod 32768。
        必须取模：序号回绕后 rn 会大于 _send_seq，不取模窗口永不复位。
        """
        if not (0 <= rn < 32768):
            return
        pending = (self._send_seq - rn) % 32768
        # 只收缩不膨胀：异常 rn（如对方重发旧确认）不应把窗口撑大
        if pending < self._unacknowledged_i:
            self._unacknowledged_i = pending

    def _handle_s_frame_received_raw(self, rn: int):
        """处理收到的 S 帧 (直接使用序号)

        对方确认到序号 rn-1 为止的 I 帧，据此缩减未确认窗口（K 窗口）。
        """
        self._release_send_window(rn)
        self._last_rx_time = time.time()
        self._last_send_time = 0

    def _handle_u_frame_received(self, frame_data: bytes):
        """处理收到的 U 帧"""
        ctrl = frame_data[2]
        if ctrl == UFunc.STARTDT_ACT:
            self.log_message.emit("[U帧] 收到 STARTDT_ACT, 回复 CON")
            self.send_u_frame("STARTDT_CON")
            self._set_state(ConnectionState.STARTED)
        elif ctrl == UFunc.STARTDT_CON:
            self.log_message.emit("[U帧] 收到 STARTDT_CON")
            self._set_state(ConnectionState.STARTED)
            # 主站自动发送总召唤
            if self.role == SimulatorRole.MASTER and self._auto_gi:
                self.log_message.emit("[主站] 自动发送总召唤")
                self.send_general_interrogation(common_addr=self.common_addr)
        elif ctrl == UFunc.STOPDT_ACT:
            self.log_message.emit("[U帧] 收到 STOPDT_ACT, 回复 CON")
            self.send_u_frame("STOPDT_CON")
            self._set_state(ConnectionState.CONNECTED)
        elif ctrl == UFunc.STOPDT_CON:
            self.log_message.emit("[U帧] 收到 STOPDT_CON")
            self._set_state(ConnectionState.CONNECTED)
        elif ctrl == UFunc.TESTFR_ACT:
            self.log_message.emit("[U帧] 收到 TESTFR_ACT, 回复 CON")
            self.send_u_frame("TESTFR_CON")
        elif ctrl == UFunc.TESTFR_CON:
            self.log_message.emit("[U帧] 收到 TESTFR_CON")
            # 对端确认了上一轮探活，清零无应答计数（t3 重连判据）
            self._testfr_unacked = 0
        else:
            self.log_message.emit(f"[U帧] 未知功能码: 0x{ctrl:02X}")

        self._last_rx_time = time.time()
        self._last_activity_time = time.time()

    def _auto_reply_slave(self, frame, raw=None):
        """从站自动回复逻辑"""
        if not frame.asdu:
            return
        tid = frame.asdu.type_id
        cot = frame.asdu.cot_cause
        ca = frame.asdu.common_address

        if tid == 100 and cot == 6:
            # 总召唤 → 回复确认 + 数据 + 终止
            self.log_message.emit("[从站] 收到总召唤，回复确认")
            self.send_i_frame(type_id=100, cot=7, common_addr=ca, ioa=0, value=20)
            # 上送所有点位数据
            self._send_all_points(ca, cot=20)
            # 发送终止
            self.send_i_frame(type_id=100, cot=10, common_addr=ca, ioa=0, value=20)
            self.log_message.emit("[从站] 总召唤数据上送完成")

        elif tid == 103 and cot == 6:
            # 时钟同步 → 回复确认（带当前时间）
            self.log_message.emit("[从站] 收到时钟同步，回复确认")
            self.send_i_frame(type_id=103, cot=7, common_addr=ca, ioa=0, value=0)

        elif tid in (13, 14, 45, 46, 47, 48, 49, 50, 61, 62, 63) and cot == 6:
            # 遥控/遥调/设点命令 → 按 SBO 预制流程处理
            self._handle_control_command(frame, tid, ca, raw)

    # 遥控/遥调命令是否要求 SBO 预制流程（定义见 iec104_protocol）
    _SBO_COMMAND_TIDS = SBO_COMMAND_TYPE_IDS

    def _extract_sco(self, frame, tid: int) -> int:
        """从解析结果还原控制命令的 SCO/设点字节（含 S/E 位）

        S/E 位含义遵循标准：1=选择(预制)、0=执行。
          - 45 C_SC:  {'scs':bit0, 'se':bit7}
          - 46 C_DC:  {'dcs':bit2-3, 'se':bit7}
          - 47 C_RC:  {'rca':bit0-5, 'se':bit6}（bit7 为 QL）
          - 48/49/50/61/62/63 设点：parser 只给值，S/E 位在 QOS 未单独解析，
            此处以 S/E=执行 还原（设点命令极少用 SBO，且此处仅用于校验）
        """
        if not frame.asdu or not frame.asdu.information_elements:
            return 0
        elem = frame.asdu.information_elements[0]
        val = elem.value
        if isinstance(val, dict):
            se = val.get("se", 0)
            if tid == 45:
                body = val.get("scs", 0) & 0x01
            elif tid == 46:
                body = (val.get("dcs", 0) & 0x03) << 2
            elif tid == 47:
                rca = val.get("rca", 0) & 0x3F
                if val.get("step_dir") == "降":
                    rca |= 0x01
                return (se << se_bit(tid)) | rca
            else:
                body = 0
            return (se << se_bit(tid)) | (body & value_mask(tid))
        if isinstance(val, int):
            return val & 0xFF
        return 0

    def _decode_command(self, raw: bytes, tid: int):
        """从原始帧字节精确还原控制命令的 S/E 位与命令值。

        _extract_sco 依赖解析结果、对短浮点设点(type 50)会丢失信息（浮点
        值被当 0、S/E 位无法取得），故改为直接按类型解析字节：
          - 45/46 (遥控/双点): 信息体 1 字节，bit7=S/E，低 7 位=命令值
          - 47 (步调):        信息体 1 字节，bit6=S/E，bit0-5=RCA
          - 48/49 (设点归一/标度):    信息体 2 字节值 + 1 字节 QOS(bit7=S/E)
          - 50/63 (设点短浮点):       信息体 4 字节 float + 1 字节 QOS(bit7=S/E)
          - 61/62 (设点带时标归一/标度): 2 字节值 + QOS + 7 字节时标
        ASDU 自偏移 6 起，IOA 占 3 字节，信息体自偏移 15 起。
        返回 (se, value)：se 为 1 表示选择/预制、0 表示执行；
        value 对浮点用 ('f', float) 标记以便精确比较。
        """
        if not raw or len(raw) < 16:
            return (None, None)
        info = raw[15:]                      # 信息体起始
        if tid in (45, 46):
            b = info[0]
            return ((b >> 7) & 1, b & 0x7F)
        if tid == 47:
            b = info[0]
            return ((b >> 6) & 1, b & 0x3F)
        if tid in (48, 49):
            val = info[0] | (info[1] << 8)
            return ((info[2] >> 7) & 1, val)
        if tid == 50:
            fv = struct.unpack('<f', bytes(info[0:4]))[0]
            return ((info[4] >> 7) & 1, ('f', fv))
        if tid in (61, 62):
            val = info[0] | (info[1] << 8)
            return ((info[2] >> 7) & 1, val)
        if tid == 63:
            fv = struct.unpack('<f', bytes(info[0:4]))[0]
            return ((info[4] >> 7) & 1, ('f', fv))
        return (None, None)

    @staticmethod
    def _cmd_value_equal(a, b) -> bool:
        """比较预制值与执行值（浮点按原始字节，容忍浮点误差）"""
        if isinstance(a, tuple) and isinstance(b, tuple) and a and b \
                and a[0] == 'f' and b[0] == 'f':
            return abs(a[1] - b[1]) < 1e-6
        return a == b

    def _handle_control_command(self, frame, tid: int, ca: int, raw=None):
        """按 SBO（select-before-operate）流程处理遥控/遥调/设点命令

        流程（IEC 104 §5.4.2.2，SBO 要求）：
          1. 收到 COT=6 + S/E=1(选择)  → 回 COT=7(选择确认)，记录预制锁
          2. 收到 COT=6 + S/E=0(执行)  → 校验预制锁，执行并回 COT=7(执行确认) + 变化上送
          3. 未预制就执行 / 预制后值不一致 / 预制后超时 → 回 COT=8(激活终止)

        直接操作（SBO 关闭场景下主站只发执行帧、无预制锁）按 direct operate
        处理：直接执行并回执行确认，便于测试工具互相联调。
        """
        if not frame.asdu or not frame.asdu.information_elements:
            return
        ioa = frame.asdu.information_elements[0].ioa
        # 优先按字节精确解码（type 50 浮点必须）；无原始字节时回退 _extract_sco
        if raw is not None:
            decoded = self._decode_command(raw, tid)
            if decoded[0] is not None:
                se, value = decoded
                raw_ok = True
            else:
                raw_ok = False
        else:
            raw_ok = False
        if not raw_ok:
            sco = self._extract_sco(frame, tid)
            # RCO（47/60）的 S/E 在 bit6，其余在 bit7（见 iec104_protocol）
            pos = se_bit(tid)
            se = (sco >> pos) & 1
            value = sco & value_mask(tid)
        # 浮点设点用 ('f', x) 标记，回确认帧时需还原为真实浮点
        send_value = value[1] if isinstance(value, tuple) and value \
            and value[0] == 'f' else value

        if se == 1:
            # ===== 选择/预制（S/E=1）=====
            self._select_locks[ioa] = (tid, value)
            self.log_message.emit(
                f"[从站] SBO选择 IOA=0x{ioa:04X} TID={tid}，回选择确认")
            self.send_i_frame(type_id=tid, cot=7, common_addr=ca, ioa=ioa,
                              value=send_value, select=True)
            return

        # ===== 执行 =====
        lock = self._select_locks.get(ioa)
        if lock is None:
            # 无预制锁：视为直接操作（SBO 关闭），直接执行并确认
            self.log_message.emit(
                f"[从站] 直接操作(无预制) IOA=0x{ioa:04X} TID={tid}，回执行确认")
            self._apply_control_to_point(ioa, tid, value, ca)
            self.send_i_frame(type_id=tid, cot=7, common_addr=ca, ioa=ioa,
                              value=send_value, select=False)
            return
        if lock[0] != tid or not self._cmd_value_equal(lock[1], value):
            # 预制值不一致 → 拒绝并清除预制锁（该选择已被尝试消耗）
            self._select_locks.pop(ioa, None)
            self.log_message.emit(
                f"[从站] 执行与预制不一致 IOA=0x{ioa:04X} TID={tid}，回激活终止")
            self.send_i_frame(type_id=tid, cot=8, common_addr=ca, ioa=ioa,
                              value=send_value, select=False)
            return

        # 校验通过，执行操作并清除预制锁
        self._select_locks.pop(ioa, None)
        self._apply_control_to_point(ioa, tid, value, ca)
        self.log_message.emit(f"[从站] SBO执行 IOA=0x{ioa:04X} TID={tid}，回执行确认")
        self.send_i_frame(type_id=tid, cot=7, common_addr=ca, ioa=ioa,
                          value=send_value, select=False)

    def _apply_control_to_point(self, ioa: int, tid: int, value, ca: int = 1):
        """把控制命令的作用写入点位，并同步写入关联遥信/遥测后变化上送

        遥控/遥调本质是写点位：遥控结果体现为关联遥信的合/分、遥调
        结果体现为关联遥测的新值。主站靠这些 YX/YC 数据帧（COT=3）
        刷新显示，而不是靠控制回显帧（那是命令回执，不是遥信）。
        """
        if ioa not in self.points:
            return
        pt = self.points[ioa]
        is_float = isinstance(value, tuple) and value and value[0] == 'f'
        if is_float:
            # type 50/63 短浮点设点：直接存浮点
            pt.value = value[1]
        elif tid == 46:
            # 双点命令 DCS 占 bit2-3：0=中间态 1=分 2=合 3=不确定。
            # 直接 bool(body) 会把 0x04(分) 也判成合
            pt.value = (value >> 2) & 0x03
        else:
            # 单点命令 SCS 占 bit0（0=分 1=合）
            pt.value = value & 0x7F
        pt.timestamp = time.strftime("%H:%M:%S")
        pt.is_valid = True
        self.point_updated.emit(ioa, pt)

        # 写入关联点位并变化上送（控制变位是典型 SOE 场景，带时标可选）
        links = (self.yk_links if pt.signal_type == "YK"
                 else self.yt_links if pt.signal_type == "YT" else {})
        link_ioa = links.get(ioa)
        if not link_ioa or link_ioa not in self.points:
            return
        lp = self.points[link_ioa]
        if is_float:
            lp.value = value[1]
        elif tid == 46:
            lp.value = (pt.value == 2)     # DCS=2 才是合
        else:
            lp.value = bool(value & 0x7F)
        lp.timestamp = pt.timestamp
        lp.is_valid = True
        self.point_updated.emit(link_ioa, lp)
        if self.is_connected:
            if lp.signal_type == "YX":
                if self.soe_enabled:
                    self.send_i_frame(type_id=30, cot=3, common_addr=ca,
                                      ioa=link_ioa, value=bool(lp.value))
                else:
                    self.send_single_point(ioa=link_ioa, value=bool(lp.value),
                                           common_addr=ca, cot=3)
            elif lp.signal_type == "YC":
                if self.soe_enabled:
                    self.send_i_frame(type_id=36, cot=3, common_addr=ca,
                                      ioa=link_ioa, value=float(lp.value))
                else:
                    self.send_measured_float(ioa=link_ioa, value=float(lp.value),
                                             common_addr=ca, cot=3)
            self.log_message.emit(
                f"[从站] 控制写入关联点位 IOA=0x{link_ioa:04X} "
                f"值={lp.value}，变化上送")

    def _send_all_points(self, common_addr: int, cot: int = 20):
        """上送所有点位数据（按 K 窗口分段，末尾发激活终止）

        此前在同一个同步调用栈里连续发送：发满 k=12 后其余点位被静默
        丢弃，随后的 ACTTERM 也发不出去 → 主站必然等到 15s 超时才收尾。
        改为入队 + 由收包循环（_handle_timers）分批刷新：每轮只发窗口
        允许的量，对端 S 帧到达后窗口释放再继续，最终 ACTTERM 必定发出。
        """
        queue = [(ioa, pt) for ioa, pt in sorted(self.points.items())
                 if pt.signal_type in ("YX", "YC")]
        # 哨兵：数据发完后发 ACTTERM
        queue.append((None, None))
        self._gi_queue = queue
        self._gi_ca = common_addr
        self._gi_cot = cot
        self._flush_gi_queue()

    def _flush_gi_queue(self):
        """按 K 窗口余量继续上送总召唤数据；队列清空后补发激活终止"""
        queue = self._gi_queue
        if not queue:
            return

        while queue and self._unacknowledged_i < self.k:
            ioa, pt = queue[0]

            if ioa is None:
                # ACTTERM：窗口满时留到下一轮，绝不能丢弃（否则主站
                # 收不到激活终止，只能等超时）
                if not self.send_i_frame(type_id=100, cot=10,
                                         common_addr=self._gi_ca,
                                         ioa=0, value=20):
                    return
                queue.pop(0)
                self.log_message.emit("[从站] 总召唤数据上送完成")
                continue

            if pt.signal_type == "YX":
                if self.soe_enabled:
                    ok = self.send_i_frame(type_id=30, cot=self._gi_cot,
                                           common_addr=self._gi_ca,
                                           ioa=ioa, value=bool(pt.value))
                else:
                    ok = self.send_single_point(ioa=ioa, value=bool(pt.value),
                                                common_addr=self._gi_ca,
                                                cot=self._gi_cot)
            else:
                if self.soe_enabled:
                    ok = self.send_i_frame(type_id=36, cot=self._gi_cot,
                                           common_addr=self._gi_ca,
                                           ioa=ioa, value=float(pt.value))
                else:
                    ok = self.send_measured_float(ioa=ioa, value=float(pt.value),
                                                  common_addr=self._gi_ca,
                                                  cot=self._gi_cot)
            if not ok:
                return      # 发送失败或窗口已满，留待下一轮
            queue.pop(0)

    def set_point_value(self, ioa: int, value: Any):
        """设置点位值（从站模式），自动上送变化数据"""
        if ioa not in self.points:
            return
        pt = self.points[ioa]
        old_val = pt.value
        pt.value = value
        pt.timestamp = time.strftime("%H:%M:%S")
        pt.is_valid = True
        self.point_updated.emit(ioa, pt)
        # 如果已连接且值有变化，自动上送（公共地址/带时标由协议参数决定）
        if self._auto_send_enabled and self.is_connected and old_val != value:
            if pt.signal_type == "YX":
                if self.soe_enabled:
                    self.send_i_frame(type_id=30, cot=3, common_addr=self.common_addr,
                                      ioa=ioa, value=bool(value))
                else:
                    self.send_single_point(ioa=ioa, value=bool(value),
                                           common_addr=self.common_addr, cot=3)
            elif pt.signal_type == "YC":
                if self.soe_enabled:
                    self.send_i_frame(type_id=36, cot=3, common_addr=self.common_addr,
                                      ioa=ioa, value=float(value))
                else:
                    self.send_measured_float(ioa=ioa, value=float(value),
                                             common_addr=self.common_addr, cot=3)
            self.log_message.emit(f"[从站] 变化上送 IOA=0x{ioa:04X} 值={value}")

    def _update_point_from_frame(self, frame):
        """从收到的帧更新点位数据（主站模式）"""
        if not frame.asdu:
            return
        # 控制命令回显（45/46/…/63 的 COT=7/8 确认帧）不是遥信数据：
        # 预制(选择)回显也会带着命令值到达，若照单全收，主站在"预制"
        # 阶段就会把显示值改成目标值——显示与从站真实状态不符。
        # 主站学值只认数据帧（总召唤 COT=20 / 变化上送 COT=3）。
        if frame.asdu.type_id in self._CONTROL_TIDS:
            return
        for elem in frame.asdu.information_elements:
            ioa = elem.ioa
            if ioa == 0:
                continue  # IOA=0 是命令/控制帧（总召唤/时钟同步等），不是数据点位
            if ioa not in self.points:
                # 自动创建新点位（使用英文名称：YX/YC/YK/YT）
                st = elem.signal_type.name if elem.signal_type else "YC"
                self.points[ioa] = PointData(ioa=ioa, signal_type=st)
            pt = self.points[ioa]
            pt.value = elem.value
            pt.is_valid = True
            pt.timestamp = time.strftime("%H:%M:%S")
            if elem.quality:
                pt.quality = elem.quality.raw
            self.point_updated.emit(ioa, pt)

    # 控制方向类型（C_ 开头命令帧）。注意 13/14 是监视方向的遥测短浮点
    # （M_ME_NC_1/TC_1），绝不能混入——否则主站会把从站上送的遥测当
    # 控制回显丢弃，遥测全解析不出来。
    _CONTROL_TIDS = {45, 46, 47, 48, 49, 50, 51, 58, 59, 60, 61, 62, 63, 64}

    def _auto_reply_master(self, frame):
        """主站自动回复逻辑"""
        if not frame.asdu:
            return
        # 识别从站返回的控制命令确认/终止（COT=7/8/9/10）
        tid = frame.asdu.type_id
        cot = frame.asdu.cot_cause
        if tid in self._CONTROL_TIDS and cot in (7, 8, 9, 10) \
                and frame.asdu.information_elements:
            ioa = frame.asdu.information_elements[0].ioa
            self.control_ack.emit(ioa, cot, tid)
        # 更新点位数据
        self._update_point_from_frame(frame)

    # ==================================================================
    # 帧发送
    # ==================================================================

    def _send_raw(self, hex_string: str):
        """发送原始十六进制帧

        加锁串行化 sendall：GUI 线程（手动发送/行内遥控）与工作线程
        （t2 S帧 / t3 TESTFR）可能并发调用，无锁时字节流会交错损坏。
        """
        sock = self._client_socket
        if sock is None:
            self.error_occurred.emit("未连接，无法发送")
            return False
        try:
            data = hex_to_bytes(hex_string)
            with self._send_lock:
                sock.sendall(data)
            self.frame_sent.emit(hex_string)
            self._last_activity_time = time.time()
            self.log_message.emit(f"[发] {hex_string[:60]}...")
            return True
        except Exception as e:
            self.error_occurred.emit(f"发送失败: {e}")
            return False

    def send_frame(self, hex_string: str):
        """发送用户手动输入的帧"""
        return self._send_raw(hex_string)

    def send_u_frame(self, u_type: str = "STARTDT_ACT"):
        """发送 U 帧"""
        hex_str = generate_u_frame(u_type)
        return self._send_raw(hex_str)

    def send_s_frame(self):
        """发送 S 帧确认

        仅在发送成功时才复位确认计数——失败时保留 _pending_s_timer，
        由 t2 定时器重发。此前无条件清零：S 帧丢失后对端将永远等不到
        确认，卡死在 K 窗口上（总召唤数据传一半即停摆），直至 t1 超时
        断连重连、_gi_queue 被清空，数据丢失。
        """
        hex_str = generate_s_frame(self._recv_seq)
        if self._send_raw(hex_str):
            self._rx_count_since_last_s = 0
            self._pending_s_timer = False
            return True
        return False

    def send_i_frame(self, type_id: int, cot: int, common_addr: int,
                     ioa: int, value, vsq: int = 1, select: bool = False):
        """发送 I 帧

        select: 遥控/遥调命令（45/46/47/48/49/50/61/62/63）的预制标志。
            为 True 表示"选择/预制"，为 False 表示"执行"。

        注意：K 窗口满时**不等待**直接返回 False。等待会自死锁——
        _auto_reply_slave 在收包循环的同步调用栈内发送，工作线程无法
        腾出手处理对端 S 帧，窗口永远不释放。需要可靠送达的批量帧
        （如总召唤响应）请走 _gi_queue 分段发送。
        """
        if self._state != ConnectionState.STARTED:
            self.error_occurred.emit("未进入数据传输状态，请先发送 STARTDT")
            return False

        # "取序号→生成帧→发送→自增" 必须整体原子：GUI 线程（手动发送/
        # 定时上送）与工作线程（t2 S帧/t3 TESTFR/总召唤响应）并发调用时，
        # 无保护会产出两条相同 SN 的 I 帧或丢失自增。
        with self._send_lock:
            if self._unacknowledged_i >= self.k:
                self.log_message.emit(
                    f"[K窗口] 未确认I帧数已达 {self._unacknowledged_i}/{self.k}，暂缓发送")
                return False

            hex_str = generate_i_frame(
                type_id=type_id, cot=cot, common_addr=common_addr,
                ioa=ioa, value=value, send_seq=self._send_seq,
                recv_seq=self._recv_seq, vsq=vsq, select=select
            )
            result = self._send_raw(hex_str)
            if result:
                self._send_seq = (self._send_seq + 1) % 32768
                self._unacknowledged_i += 1
                self._last_send_time = time.time()
            return result

    def send_startdt(self):
        """发送 STARTDT_ACT"""
        return self.send_u_frame("STARTDT_ACT")

    def send_stopdt(self):
        """发送 STOPDT_ACT"""
        return self.send_u_frame("STOPDT_ACT")

    def send_testfr(self):
        """发送 TESTFR_ACT"""
        return self.send_u_frame("TESTFR_ACT")

    def send_general_interrogation(self, common_addr: int = 1):
        """发送总召唤命令"""
        return self.send_i_frame(
            type_id=100, cot=6, common_addr=common_addr,
            ioa=0, value=20  # QOI=20 (全局)
        )

    def send_clock_sync(self, common_addr: int = 1):
        """发送时钟同步命令"""
        return self.send_i_frame(
            type_id=103, cot=6, common_addr=common_addr,
            ioa=0, value=0
        )

    def send_single_point(self, ioa: int, value: bool, common_addr: int = 1,
                          cot: int = 3):
        """发送单点信息"""
        return self.send_i_frame(
            type_id=1, cot=cot, common_addr=common_addr,
            ioa=ioa, value=int(value)
        )

    def send_measured_float(self, ioa: int, value: float, common_addr: int = 1,
                            cot: int = 3):
        """发送短浮点测量值"""
        return self.send_i_frame(
            type_id=13, cot=cot, common_addr=common_addr,
            ioa=ioa, value=value
        )

    # ==================================================================
    # 定时器管理
    # ==================================================================

    def _handle_timers(self):
        """处理协议定时器"""
        now = time.time()

        # t2: 收到 I 帧后延迟发送 S 帧
        if self._pending_s_timer and self._last_rx_time > 0:
            if now - self._last_rx_time >= self.t2:
                self.send_s_frame()

        # t1: 发送 I 帧后等待确认超时（协议级致命，应重连）
        if self._last_send_time > 0:
            if now - self._last_send_time >= self.t1:
                self.log_message.emit(f"[超时] t1={self.t1}s 发送确认超时，连接视为断开")
                self._last_send_time = 0
                self._reconnect_requested = True

        # t3: 空闲时发送 TESTFR 探活；连续 2 次无 CON 应答才判连接已死，
        # 单次无应答（网络抖动/丢包）不轻易断开。
        if self._last_activity_time > 0:
            if now - self._last_activity_time >= self.t3:
                if self._testfr_unacked >= 2:
                    self.log_message.emit(
                        f"[超时] t3={self.t3}s 连续 2 次 TESTFR 无应答，连接断开")
                    self._reconnect_requested = True
                else:
                    self.log_message.emit(f"[定时] t3={self.t3}s 空闲，发送 TESTFR")
                    self.send_testfr()
                    self._testfr_unacked += 1
                self._last_activity_time = now

        # 总召唤响应：窗口释放后继续上送剩余点位 / 补发激活终止
        if self._gi_queue:
            self._flush_gi_queue()

    def _reset_sequence_numbers(self):
        """重置序列号与连接态状态"""
        self._send_seq = 0
        self._recv_seq = 0
        self._rx_count_since_last_s = 0
        self._unacknowledged_i = 0
        self._select_locks.clear()
        self._gi_queue.clear()    # 上一连接未完成的总召唤上送不得带入新连接
        self._rx_buffer.clear()   # 新连接从干净字节流开始，旧半包不得混入
        self._last_send_time = 0
        self._last_rx_time = time.time()
        self._last_activity_time = time.time()
        self._pending_s_timer = False
        self._testfr_unacked = 0
        self._reconnect_requested = False
