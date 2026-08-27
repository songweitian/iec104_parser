"""
IEC60870-5-104 通信模拟器
支持主站/从站模式，TCP 通信 + APCI 状态机
"""

import socket
import struct
import threading
import time
import logging
import random
from enum import Enum, IntEnum
from dataclasses import dataclass, field
from typing import Optional, Callable, Dict, Any, List

from PyQt5.QtCore import QThread, pyqtSignal

from iec104_parser import hex_to_bytes, IEC104Parser
from frame_generator import generate_i_frame, generate_s_frame, generate_u_frame

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
    STOPPING = "停止中"


class APCIType(IntEnum):
    """APCI 帧类型标识 (控制域第1字节 bit0-bit1)"""
    I_FORMAT = 0x00  # bit0=0: I 格式
    S_FORMAT = 0x01  # bit0=1, bit1=0: S 格式
    U_FORMAT = 0x03  # bit0=1, bit1=1: U 格式


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
                          yk_count: int = 2, yt_count: int = 2) -> Dict[int, PointData]:
    """创建默认点位表"""
    points = {}
    for i in range(yx_count):
        ioa = DEFAULT_IOA_RANGES["YX"][0] + i
        points[ioa] = PointData(ioa=ioa, signal_type="YX", value=False)
    for i in range(yc_count):
        ioa = DEFAULT_IOA_RANGES["YC"][0] + i
        points[ioa] = PointData(ioa=ioa, signal_type="YC", value=0.0)
    for i in range(yk_count):
        ioa = DEFAULT_IOA_RANGES["YK"][0] + i
        points[ioa] = PointData(ioa=ioa, signal_type="YK", value=0)
    for i in range(yt_count):
        ioa = DEFAULT_IOA_RANGES["YT"][0] + i
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
        self._auto_send_interval = 1000  # ms

        # 接收计数器 (用于 W 窗口控制)
        self._rx_count_since_last_s = 0

        # 协议参数
        self.t1 = DEFAULT_T1
        self.t2 = DEFAULT_T2
        self.t3 = DEFAULT_T3
        self.k = DEFAULT_K
        self.w = DEFAULT_W

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
        """关闭所有 socket"""
        for sock in [self._client_socket, self._server_socket, self._socket]:
            if sock:
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
        self._server_socket.bind((self.host, self.port))
        self._server_socket.listen(1)

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
                self.log_message.emit(f"[客户端] 连接失败: {e}")
                if self._running:
                    time.sleep(3)
            finally:
                if self._client_socket:
                    try:
                        self._client_socket.close()
                    except Exception:
                        pass
                    self._client_socket = None

    def _handle_connection(self):
        """处理已建立的连接 - 接收循环 + 定时任务"""
        while self._running and self._client_socket:
            try:
                # 接收数据
                self._receive_and_process()
                # 处理定时任务
                self._handle_timers()
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
        """接收并处理数据"""
        try:
            data = self._client_socket.recv(4096)
            if not data:
                self.log_message.emit("[连接] 对端关闭连接")
                self._client_socket = None
                return

            self._last_activity_time = time.time()
            offset = 0
            while offset < len(data):
                if offset + 2 > len(data):
                    break
                if data[offset] != 0x68:
                    offset += 1
                    continue
                apdu_len = data[offset + 1]
                frame_len = apdu_len + 2
                if offset + frame_len > len(data):
                    break
                frame_data = data[offset:offset + frame_len]
                offset += frame_len
                self._process_received_frame(frame_data)

        except socket.timeout:
            pass

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
            self._handle_i_frame_received(frame)
        elif ftype == "S-format":
            rn = ((frame_data[5] & 0x7F) << 8 | (frame_data[4] & 0xFE)) >> 1
            self.log_message.emit(f"[收] S帧 RN={rn}")
            self._handle_s_frame_received_raw(rn)
        elif ftype == "U-format":
            desc = self._parser._describe_u_frame(frame_data[2:6])
            self.log_message.emit(f"[收] {desc}")
            self._handle_u_frame_received(frame_data)

    def _handle_i_frame_received(self, frame):
        """处理收到的 I 帧"""
        # 更新接收序号
        sn = frame.apci.send_seq
        if sn == self._recv_seq:
            self._recv_seq = (self._recv_seq + 1) % 32768
        else:
            self.log_message.emit(f"[警告] 序号不连续: 期望{self._recv_seq}, 收到{sn}")
            self._recv_seq = (sn + 1) % 32768

        self._rx_count_since_last_s += 1
        self._last_rx_time = time.time()
        self._pending_s_timer = True

        # 自动回复逻辑
        if self.role == SimulatorRole.SLAVE:
            self._auto_reply_slave(frame)
        elif self.role == SimulatorRole.MASTER:
            self._auto_reply_master(frame)

        # 达到 W 窗口大小，立即发送 S 帧
        if self._rx_count_since_last_s >= self.w:
            self.send_s_frame()

    def _handle_s_frame_received(self, frame):
        """处理收到的 S 帧 (通过 parser 解析)"""
        rn = frame.apci.recv_seq
        self.log_message.emit(f"[S帧] 确认序号 RN={rn}")
        self._last_rx_time = time.time()
        self._last_send_time = 0

    def _handle_s_frame_received_raw(self, rn: int):
        """处理收到的 S 帧 (直接使用序号)"""
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
                self.send_general_interrogation(common_addr=1)
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
        else:
            self.log_message.emit(f"[U帧] 未知功能码: 0x{ctrl:02X}")

        self._last_rx_time = time.time()
        self._last_activity_time = time.time()

    def _auto_reply_slave(self, frame):
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

        elif tid in (13, 14, 45, 46, 47, 48, 49, 50) and cot == 6:
            # 遥控/遥调/设点命令 → 更新点位 + 回复确认
            ioa = frame.asdu.information_elements[0].ioa if frame.asdu.information_elements else 0
            value = frame.asdu.information_elements[0].value if frame.asdu.information_elements else 0
            if ioa in self.points:
                self.points[ioa].value = value
                self.points[ioa].timestamp = time.strftime("%H:%M:%S")
                self.points[ioa].is_valid = True
                self.point_updated.emit(ioa, self.points[ioa])
            self.log_message.emit(f"[从站] 收到控制命令 TID={tid} IOA=0x{ioa:04X} 值={value}，回复确认")
            # 回复：同类型标识，COT=7（激活确认），IOA 使用原命令的 IOA
            self.send_i_frame(type_id=tid, cot=7, common_addr=ca, ioa=ioa, value=value)

    def _send_all_points(self, common_addr: int, cot: int = 20):
        """上送所有点位数据"""
        for ioa, pt in sorted(self.points.items()):
            if pt.signal_type == "YX":
                self.send_single_point(ioa=ioa, value=bool(pt.value),
                                       common_addr=common_addr, cot=cot)
            elif pt.signal_type == "YC":
                self.send_measured_float(ioa=ioa, value=float(pt.value),
                                         common_addr=common_addr, cot=cot)

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
        # 如果已连接且值有变化，自动上送
        if self._auto_send_enabled and self.is_connected and old_val != value:
            if pt.signal_type == "YX":
                self.send_single_point(ioa=ioa, value=bool(value), common_addr=1, cot=3)
            elif pt.signal_type == "YC":
                self.send_measured_float(ioa=ioa, value=float(value), common_addr=1, cot=3)
            self.log_message.emit(f"[从站] 变化上送 IOA=0x{ioa:04X} 值={value}")

    def _update_point_from_frame(self, frame):
        """从收到的帧更新点位数据（主站模式）"""
        if not frame.asdu:
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

    def _auto_reply_master(self, frame):
        """主站自动回复逻辑"""
        if not frame.asdu:
            return
        # 更新点位数据
        self._update_point_from_frame(frame)

    # ==================================================================
    # 帧发送
    # ==================================================================

    def _send_raw(self, hex_string: str):
        """发送原始十六进制帧"""
        if not self._client_socket:
            self.error_occurred.emit("未连接，无法发送")
            return False
        try:
            data = hex_to_bytes(hex_string)
            self._client_socket.sendall(data)
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
        """发送 S 帧确认"""
        hex_str = generate_s_frame(self._recv_seq)
        self._rx_count_since_last_s = 0
        self._pending_s_timer = False
        return self._send_raw(hex_str)

    def send_i_frame(self, type_id: int, cot: int, common_addr: int,
                     ioa: int, value, vsq: int = 1):
        """发送 I 帧"""
        if self._state != ConnectionState.STARTED:
            self.error_occurred.emit("未进入数据传输状态，请先发送 STARTDT")
            return False
        hex_str = generate_i_frame(
            type_id=type_id, cot=cot, common_addr=common_addr,
            ioa=ioa, value=value, send_seq=self._send_seq,
            recv_seq=self._recv_seq, vsq=vsq
        )
        result = self._send_raw(hex_str)
        if result:
            self._send_seq = (self._send_seq + 1) % 32768
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

        # t1: 发送 I 帧后等待确认超时
        if self._last_send_time > 0:
            if now - self._last_send_time >= self.t1:
                self.log_message.emit(f"[超时] t1={self.t1}s 发送确认超时")
                self._last_send_time = 0

        # t3: 空闲时发送 TESTFR
        if self._last_activity_time > 0:
            if now - self._last_activity_time >= self.t3:
                self.log_message.emit(f"[定时] t3={self.t3}s 空闲，发送 TESTFR")
                self.send_testfr()
                self._last_activity_time = now

    def _reset_sequence_numbers(self):
        """重置序列号"""
        self._send_seq = 0
        self._recv_seq = 0
        self._rx_count_since_last_s = 0
        self._last_send_time = 0
        self._last_rx_time = time.time()
        self._last_activity_time = time.time()
        self._pending_s_timer = False
