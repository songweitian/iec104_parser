"""
IEC60870-5-104 (2002) 协议报文解析器

支持：
- APCI 帧解析（I帧、S帧、U帧）
- ASDU 解析（类型标识、VSQ、传送原因、公共地址、信息对象地址等）
- 主站/从站角色识别
- CP56Time2a / CP24Time2a 时标解析
- 信号类型分类（遥信/遥测/遥控/遥调/遥脉）
- 多种类型标识的数据解析
"""

import struct
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import IntEnum
from typing import List, Optional, Tuple, Dict, Any

logger = logging.getLogger(__name__)


# ============================================================================
# 类型标识 (Type Identification)
# ============================================================================
class TypeID(IntEnum):
    """IEC 104 类型标识"""
    # 监视方向 (Monitor direction) - 过程信息
    M_SP_NA_1   = 1    # 单点信息
    M_SP_TA_1   = 2    # 带时标的单点信息
    M_DP_NA_1   = 3    # 双点信息
    M_DP_TA_1   = 4    # 带时标的双点信息
    M_ST_NA_1   = 5    # 步位置信息
    M_ST_TA_1   = 6    # 带时标的步位置信息
    M_BO_NA_1   = 7    # 32比特串
    M_BO_TA_1   = 8    # 带时标的32比特串
    M_ME_NA_1   = 9    # 测量值，归一化值
    M_ME_TA_1   = 10   # 带时标的测量值，归一化值
    M_ME_NB_1   = 11   # 测量值，标度化值
    M_ME_TB_1   = 12   # 带时标的测量值，标度化值
    M_ME_NC_1   = 13   # 测量值，短浮点数
    M_ME_TC_1   = 14   # 带时标的测量值，短浮点数
    M_IT_NA_1   = 15   # 累计量
    M_IT_TA_1   = 16   # 带时标的累计量
    M_EP_TA_1   = 17   # 带时标的继电保护设备事件
    M_EP_TB_1   = 18   # 带时标的继电保护设备成组启动事件
    M_EP_TC_1   = 19   # 带时标的继电保护设备成组输出电路信息
    M_PS_NA_1   = 20   # 带变位检出的成组单点信息
    M_ME_ND_1   = 21   # 测量值，不带品质描述词的归一化值
    M_SP_TB_1   = 30   # 带CP56Time2a时标的单点信息
    M_DP_TB_1   = 31   # 带CP56Time2a时标的双点信息
    M_ST_TB_1   = 32   # 带CP56Time2a时标的步位置信息
    M_BO_TB_1   = 33   # 带CP56Time2a时标的32比特串
    M_ME_TD_1   = 34   # 带CP56Time2a时标的测量值，归一化值
    M_ME_TE_1   = 35   # 带CP56Time2a时标的测量值，标度化值
    M_ME_TF_1   = 36   # 带CP56Time2a时标的测量值，短浮点数
    M_IT_TB_1   = 37   # 带CP56Time2a时标的累计量
    M_EP_TD_1   = 38   # 带CP56Time2a时标的继电保护设备事件
    M_EP_TE_1   = 39   # 带CP56Time2a时标的继电保护设备成组启动事件
    M_EP_TF_1   = 40   # 带CP56Time2a时标的继电保护设备成组输出电路信息

    # 控制方向 (Control direction) - 过程信息
    C_SC_NA_1   = 45   # 单点命令
    C_DC_NA_1   = 46   # 双点命令
    C_RC_NA_1   = 47   # 调节步命令
    C_SE_NA_1   = 48   # 设定值命令，归一化值
    C_SE_NB_1   = 49   # 设定值命令，标度化值
    C_SE_NC_1   = 50   # 设定值命令，短浮点数
    C_BO_NA_1   = 51   # 32比特串命令
    C_SC_TA_1   = 58   # 带CP56Time2a时标的单点命令
    C_DC_TA_1   = 59   # 带CP56Time2a时标的双点命令
    C_RC_TA_1   = 60   # 带CP56Time2a时标的调节步命令
    C_SE_TA_1   = 61   # 带CP56Time2a时标的设定值命令，归一化值
    C_SE_TB_1   = 62   # 带CP56Time2a时标的设定值命令，标度化值
    C_SE_TC_1   = 63   # 带CP56Time2a时标的设定值命令，短浮点数
    C_BO_TA_1   = 64   # 带CP56Time2a时标的32比特串命令

    # 监视方向 - 系统信息
    M_EI_NA_1   = 70   # 初始化结束

    # 控制方向 - 系统信息
    C_IC_NA_1   = 100  # 总召唤命令
    C_CI_NA_1   = 101  # 计数量召唤命令
    C_RD_NA_1   = 102  # 读命令
    C_CS_NA_1   = 103  # 时钟同步命令
    C_TS_NA_1   = 104  # 测试命令
    C_RP_NA_1   = 105  # 复位进程命令
    C_CD_NA_1   = 106  # 延迟获取命令
    C_TS_TA_1   = 107  # 带CP56Time2a时标的测试命令

    @classmethod
    def get_name(cls, type_id: int) -> str:
        try:
            return cls(type_id).name
        except ValueError:
            return f"未知类型({type_id})"

    @classmethod
    def get_direction(cls, type_id: int) -> str:
        """判断类型标识的方向"""
        if 1 <= type_id <= 44 or type_id == 70:
            return "监视方向"
        elif 45 <= type_id <= 69 or 100 <= type_id <= 107:
            return "控制方向"
        elif 128 <= type_id <= 255:
            return "自定义范围"
        return "未知方向"

    @classmethod
    def get_description(cls, type_id: int) -> str:
        descriptions = {
            1: "单点信息",
            2: "带时标的单点信息",
            3: "双点信息",
            4: "带时标的双点信息",
            5: "步位置信息",
            6: "带时标的步位置信息",
            7: "32比特串",
            8: "带时标的32比特串",
            9: "测量值(归一化)",
            10: "带时标的测量值(归一化)",
            11: "测量值(标度化)",
            12: "带时标的测量值(标度化)",
            13: "测量值(短浮点)",
            14: "带时标的测量值(短浮点)",
            15: "累计量",
            16: "带时标的累计量",
            17: "带时标的继电保护事件",
            18: "带时标的继电保护成组启动",
            19: "带时标的继电保护成组输出",
            20: "成组单点信息",
            21: "测量值(归一化,无品质)",
            30: "单点信息(CP56Time2a)",
            31: "双点信息(CP56Time2a)",
            32: "步位置信息(CP56Time2a)",
            33: "32比特串(CP56Time2a)",
            34: "测量值(归一化,CP56Time2a)",
            35: "测量值(标度化,CP56Time2a)",
            36: "测量值(短浮点,CP56Time2a)",
            37: "累计量(CP56Time2a)",
            38: "继电保护事件(CP56Time2a)",
            39: "继电保护成组启动(CP56Time2a)",
            40: "继电保护成组输出(CP56Time2a)",
            45: "单点命令",
            46: "双点命令",
            47: "调节步命令",
            48: "设定值命令(归一化)",
            49: "设定值命令(标度化)",
            50: "设定值命令(短浮点)",
            51: "32比特串命令",
            58: "单点命令(CP56Time2a)",
            59: "双点命令(CP56Time2a)",
            60: "调节步命令(CP56Time2a)",
            61: "设定值命令(归一化,CP56Time2a)",
            62: "设定值命令(标度化,CP56Time2a)",
            63: "设定值命令(短浮点,CP56Time2a)",
            64: "32比特串命令(CP56Time2a)",
            70: "初始化结束",
            100: "总召唤命令",
            101: "计数量召唤命令",
            102: "读命令",
            103: "时钟同步命令",
            104: "测试命令",
            105: "复位进程命令",
            106: "延迟获取命令",
            107: "测试命令(CP56Time2a)",
        }
        return descriptions.get(type_id, "未知")


# ============================================================================
# 信号类型 (Signal Type by IOA address range)
# ============================================================================
class SignalType(IntEnum):
    """根据IOA地址范围判定的信号类型"""
    YX = 1    # 遥信 (状态量)
    YC = 2    # 遥测 (模拟量)
    YK = 3    # 遥控 (开关控制)
    YT = 4    # 遥调 (调节控制)
    YM = 5    # 遥脉 (累计量/电能)
    OTHER = 0 # 其他/系统

    @classmethod
    def from_ioa(cls, ioa: int) -> "SignalType":
        """根据IOA地址判定信号类型"""
        if ioa < 0x010000:
            return cls.YX
        elif ioa < 0x020000:
            return cls.YC
        elif ioa < 0x030000:
            return cls.YK
        elif ioa < 0x040000:
            return cls.YT
        elif ioa < 0x050000:
            return cls.YM
        return cls.OTHER

    @classmethod
    def from_type_id(cls, type_id: int) -> "SignalType":
        """根据类型标识推断信号类型"""
        if type_id in (1, 2, 3, 4, 20, 30, 31):
            return cls.YX
        elif type_id in (5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 21,
                         32, 33, 34, 35, 36):
            return cls.YC
        elif type_id in (15, 16, 37):
            return cls.YM
        elif type_id in (45, 46, 58, 59):
            return cls.YK
        elif type_id in (47, 48, 49, 50, 60, 61, 62, 63, 64):
            return cls.YT
        return cls.OTHER

    @property
    def label(self) -> str:
        return {
            SignalType.YX: "遥信",
            SignalType.YC: "遥测",
            SignalType.YK: "遥控",
            SignalType.YT: "遥调",
            SignalType.YM: "遥脉",
            SignalType.OTHER: "其他",
        }[self]

    @property
    def color_hex(self) -> str:
        return {
            SignalType.YX: "#2196F3",
            SignalType.YC: "#4CAF50",
            SignalType.YK: "#F44336",
            SignalType.YT: "#FF9800",
            SignalType.YM: "#9C27B0",
            SignalType.OTHER: "#9E9E9E",
        }[self]


# ============================================================================
# 传送原因 (Cause of Transmission)
# ============================================================================
class COT(IntEnum):
    """传送原因"""
    PERIODIC = 1           # 周期/循环
    BACKGROUND = 2         # 背景扫描
    SPONTANEOUS = 3        # 突发/自发
    INITIALIZED = 4        # 初始化
    REQUEST = 5            # 请求/被请求
    ACTIVATION = 6         # 激活
    ACTIVATION_CON = 7     # 激活确认
    DEACTIVATION = 8       # 停止激活
    DEACTIVATION_CON = 9   # 停止激活确认
    ACTIVATION_TERM = 10   # 激活终止
    RETURN_INFO_REMOTE = 11  # 远方操作引起的返送信息
    RETURN_INFO_LOCAL = 12  # 当地操作引起的返送信息
    FILE_TRANSFER = 13     # 文件传送
    INTERROGATED_STATION = 20  # 响应站召唤(总召唤)
    INTERROGATED_GROUP1 = 21   # 响应第1组召唤
    INTERROGATED_GROUP2 = 22   # 响应第2组召唤
    INTERROGATED_GROUP3 = 23   # 响应第3组召唤
    INTERROGATED_GROUP4 = 24   # 响应第4组召唤
    INTERROGATED_GROUP5 = 25   # 响应第5组召唤
    INTERROGATED_GROUP6 = 26   # 响应第6组召唤
    INTERROGATED_GROUP7 = 27   # 响应第7组召唤
    INTERROGATED_GROUP8 = 28   # 响应第8组召唤
    INTERROGATED_GROUP9 = 29   # 响应第9组召唤
    INTERROGATED_GROUP10 = 30  # 响应第10组召唤
    INTERROGATED_GROUP11 = 31  # 响应第11组召唤
    INTERROGATED_GROUP12 = 32  # 响应第12组召唤
    INTERROGATED_GROUP13 = 33  # 响应第13组召唤
    INTERROGATED_GROUP14 = 34  # 响应第14组召唤
    INTERROGATED_GROUP15 = 35  # 响应第15组召唤
    INTERROGATED_GROUP16 = 36  # 响应第16组召唤
    COUNTER_GENERAL = 37   # 响应计数量召唤
    COUNTER_GROUP1 = 38    # 响应第1组计数量召唤
    COUNTER_GROUP2 = 39    # 响应第2组计数量召唤
    COUNTER_GROUP3 = 40    # 响应第3组计数量召唤
    COUNTER_GROUP4 = 41    # 响应第4组计数量召唤
    UNKNOWN_TYPE = 44      # 未知类型标识
    UNKNOWN_COT = 45       # 未知传送原因
    UNKNOWN_ASDU = 46      # 未知ASDU地址
    UNKNOWN_IOA = 47       # 未知信息对象地址

    @classmethod
    def get_description(cls, cot: int) -> str:
        """获取传送原因描述（纯描述，无标签前缀）"""
        test_bit = (cot >> 7) & 1
        pn_bit = (cot >> 6) & 1
        cause = cot & 0x3F

        descs = {
            1: "周期/循环", 2: "背景扫描", 3: "突发",
            4: "初始化", 5: "请求/被请求", 6: "激活",
            7: "激活确认", 8: "停止激活", 9: "停止激活确认",
            10: "激活终止", 11: "远方返送", 12: "当地返送",
            13: "文件传送", 20: "总召唤响应",
            21: "响应组1召唤", 22: "响应组2召唤", 23: "响应组3召唤",
            24: "响应组4召唤", 25: "响应组5召唤", 26: "响应组6召唤",
            27: "响应组7召唤", 28: "响应组8召唤", 29: "响应组9召唤",
            30: "响应组10召唤", 31: "响应组11召唤", 32: "响应组12召唤",
            33: "响应组13召唤", 34: "响应组14召唤", 35: "响应组15召唤",
            36: "响应组16召唤", 37: "计数量召唤响应",
            38: "响应组1计数量", 39: "响应组2计数量", 40: "响应组3计数量",
            41: "响应组4计数量", 44: "未知类型标识",
            45: "未知传送原因", 46: "未知ASDU地址", 47: "未知信息对象地址",
        }

        desc = descs.get(cause, f"未知({cause})")
        flags = []
        if test_bit:
            flags.append("试验")
        if pn_bit:
            flags.append("否定确认")

        if flags:
            desc += f" [{', '.join(flags)}]"
        return desc


# ============================================================================
# 品质描述词 (Quality Descriptor)
# ============================================================================
class QualityDescriptor:
    """品质描述词解析

    SIQ/DIQ 结构:
      bit0-3:  信息值 (SPI状态/DPI状态)
      bit4:    被闭锁 (Blocked)
      bit5:    被取代 (Substituted)
      bit6:    非当前值 (Not topical)
      bit7:    无效 (Invalid)

    测量值品质结构:
      bit0:    溢出 (Overflow)
      bit1:    累计时间无效
      bit4-7:  同SIQ
    """
    def __init__(self, byte_val: int, is_siq_diq: bool = False):
        self.raw = byte_val
        if is_siq_diq:
            # SIQ/DIQ: quality bits are 4-7, info value is 0-3
            self.overflow = False
            self.elapsed_time_invalid = False
            self.blocked = bool(byte_val & 0x10)
            self.substituted = bool(byte_val & 0x20)
            self.not_topical = bool(byte_val & 0x40)
            self.invalid = bool(byte_val & 0x80)
        else:
            # 测量值/累计量等: 品质描述词占完整1字节 (bit0-7)
            self.overflow = bool(byte_val & 0x01)
            self.elapsed_time_invalid = bool(byte_val & 0x02)
            self.blocked = bool(byte_val & 0x10)
            self.substituted = bool(byte_val & 0x20)
            self.not_topical = bool(byte_val & 0x40)
            self.invalid = bool(byte_val & 0x80)

    def __str__(self):
        if self.invalid:
            return "无效"
        parts = []
        if self.not_topical:
            parts.append("非当前值")
        if self.substituted:
            parts.append("被取代")
        if self.blocked:
            parts.append("被闭锁")
        if self.overflow:
            parts.append("溢出")
        if self.elapsed_time_invalid:
            parts.append("时间无效")
        return "有效" if not parts else f"有效[{', '.join(parts)}]"


# ============================================================================
# 数据类
# ============================================================================
@dataclass
class APCIHeader:
    """APCI 控制头"""
    start_byte: int          # 0x68
    apdu_length: int         # APDU长度(不含起始字节和长度字节)
    send_seq: int = 0        # 发送序号 (I帧)
    recv_seq: int = 0        # 接收序号
    frame_type: str = ""     # I-format / S-format / U-format

    @property
    def total_length(self) -> int:
        return self.apdu_length + 2  # 总帧长 = APDU长度 + 起始字节(1) + 长度字节(1)


@dataclass
class InfoElement:
    """信息元素 - 单个信息对象"""
    ioa: int = 0                    # 信息对象地址
    value: Any = None               # 解析后的值
    raw_value: bytes = b""          # 原始值字节
    value_desc: str = ""            # 值描述
    quality: Optional[QualityDescriptor] = None  # 品质描述词
    timestamp: Optional[datetime] = None         # 时标 (CP56Time2a/CP24Time2a)
    siq: int = 0                    # 单点信息
    diq: int = 0                    # 双点信息
    signal_type: SignalType = SignalType.OTHER   # 信号类型


@dataclass
class ASDU:
    """ASDU 应用服务数据单元"""
    type_id: int                    # 类型标识
    vsq: int                        # 可变结构限定词
    number_of_objects: int          # 信息对象数目
    is_sequence: bool = False       # 是否为顺序地址
    cot: int = 0                    # 传送原因
    cot_cause: int = 0              # 原因(低6位)
    cot_test: bool = False          # 试验位
    cot_pn: bool = False            # 否定确认位
    org: int = 0                    # 发方地址(源地址)
    common_address: int = 0         # 公共地址(ASDU地址)
    pad: int = 0                    # 0补齐字段
    information_elements: List[InfoElement] = field(default_factory=list)
    raw_data: bytes = b""           # ASDU原始数据
    signal_type: SignalType = SignalType.OTHER  # 信号类型


@dataclass
class IEC104Frame:
    """完整的 IEC104 报文帧"""
    index: int = 0                           # 报文序号
    raw_hex: str = ""                        # 原始十六进制字符串
    apci: Optional[APCIHeader] = None         # APCI头
    asdu: Optional[ASDU] = None              # ASDU (I帧时有效)
    direction: str = ""                       # "主站→子站" / "子站→主站" (发送方向)
    send_role: str = ""                       # "主站" / "子站" (发送方角色)
    recv_role: str = ""                       # "子站" / "主站" (接收方角色)
    description: str = ""                     # 报文描述
    error: str = ""                           # 错误信息
    source: str = ""                          # 来源
    signal_type: SignalType = SignalType.OTHER  # 信号类型
    signal_label: str = ""                    # 信号类型标签 (遥信/遥测/...)
    control_desc: str = ""                    # 遥控/遥调的详细说明


# ============================================================================
# CP56Time2a / CP24Time2a 时标解析
# ============================================================================
def parse_cp56time2a(data: bytes) -> datetime:
    """解析CP56Time2a格式时标 (7字节)
    Byte 0-1: 毫秒 (0-59999)
    Byte 2: 分钟 (bit0-5)
    Byte 3: 小时 (bit0-4)
    Byte 4: 日 (bit0-4) + 星期 (bit5-7)
    Byte 5: 月 (bit0-3)
    Byte 6: 年 (bit0-6) + 夏时制 (bit7)
    """
    if len(data) < 7:
        return datetime(2000, 1, 1)

    ms = data[0] + ((data[1] & 0xFF) << 8)
    minute = data[2] & 0x3F
    hour = data[3] & 0x1F
    day = data[4] & 0x1F
    month = data[5] & 0x0F
    year = (data[6] & 0x7F) + 2000

    month = max(1, min(12, month))
    day = max(1, min(28, day))
    hour = min(23, hour)
    minute = min(59, minute)

    try:
        return datetime(year, month, day, hour, minute, 0, ms * 1000)
    except ValueError:
        return datetime(2000, 1, 1)


def parse_cp24time2a(data: bytes) -> datetime:
    """解析CP24Time2a格式时标 (3字节) - 用于 type_id=2/4
    Byte 0-1: 毫秒 (0-59999)
    Byte 2: 分钟 (bit0-5)
    注意: CP24Time2a 不含日期信息，需要外部提供基准日期
    """
    if len(data) < 3:
        return datetime(2000, 1, 1)

    ms = data[0] + ((data[1] & 0xFF) << 8)
    minute = data[2] & 0x3F

    minute = min(59, minute)

    # 无日期信息，使用基准日期 + 时分
    try:
        return datetime(2000, 1, 1, 0, minute, 0, ms * 1000)
    except ValueError:
        return datetime(2000, 1, 1)


# ============================================================================
# IEC104 帧解析器
# ============================================================================

# TypeID 集合：用于方向判定
_CONTROL_TYPE_IDS = set(range(45, 65)) | set(range(100, 108))
_MONITOR_TYPE_IDS = set(range(1, 41)) | {70}


class IEC104Parser:
    """IEC60870-5-104 协议报文解析器"""

    START_SEND_FROM_MASTER = True

    def __init__(self):
        self.frames: List[IEC104Frame] = []
        self.last_master_send = IEC104Parser.START_SEND_FROM_MASTER

    def set_master_send_first(self, master_first: bool):
        """设置第一个报文是否由主站发出"""
        self.START_SEND_FROM_MASTER = master_first
        self.last_master_send = master_first

    def parse_hex(self, hex_string: str, source: str = "") -> IEC104Frame:
        """解析单个十六进制报文"""
        frame = IEC104Frame()
        frame.index = len(self.frames) + 1
        frame.raw_hex = hex_string.strip()
        frame.source = source

        try:
            raw_bytes = self._hex_to_bytes(frame.raw_hex)
        except ValueError as e:
            frame.error = f"十六进制解析错误: {e}"
            self.frames.append(frame)
            return frame

        if len(raw_bytes) < 6:
            frame.error = "报文太短 (最少6字节)"
            self.frames.append(frame)
            return frame

        frame.apci = self._parse_apci(raw_bytes)
        if frame.apci is None:
            frame.error = "APCI解析失败"
            self.frames.append(frame)
            return frame

        apci = frame.apci

        if len(raw_bytes) < apci.total_length:
            frame.error = f"报文不完整 (需要{apci.total_length}字节, 实际{len(raw_bytes)}字节)"
            self.frames.append(frame)
            return frame

        raw_bytes = raw_bytes[:apci.total_length]

        # 先判断发送方向 (交替)
        if self.last_master_send:
            frame.send_role = "子站"
            frame.recv_role = "主站"
            frame.direction = "子站→主站"
        else:
            frame.send_role = "主站"
            frame.recv_role = "子站"
            frame.direction = "主站→子站"
        self.last_master_send = not self.last_master_send

        # 如果是I帧，解析ASDU
        if apci.frame_type == "I-format":
            try:
                frame.asdu = self._parse_asdu(raw_bytes[6:], apci)
                self._build_description(frame)
            except Exception as e:
                frame.error = f"ASDU解析异常: {e}"
                logger.warning("ASDU解析异常: %s", e, exc_info=True)
        elif apci.frame_type == "S-format":
            frame.description = f"S帧 (确认帧, 接收序号RN={apci.recv_seq})"
        elif apci.frame_type == "U-format":
            frame.description = self._describe_u_frame(raw_bytes[2:6])

        # 根据 ASDU 内容修正方向
        if frame.asdu:
            tid = frame.asdu.type_id
            if tid in _CONTROL_TYPE_IDS:
                frame.direction = "主站→子站"
                frame.send_role = "主站"
                frame.recv_role = "子站"
            elif tid in _MONITOR_TYPE_IDS:
                frame.direction = "子站→主站"
                frame.send_role = "子站"
                frame.recv_role = "主站"

            frame.signal_type = frame.asdu.signal_type
            frame.signal_label = frame.asdu.signal_type.label

        self.frames.append(frame)
        return frame

    def parse_multiple(self, hex_strings: List[str], source: str = "") -> List[IEC104Frame]:
        """批量解析多条报文"""
        frames = []
        self.last_master_send = self.START_SEND_FROM_MASTER

        for hs in hex_strings:
            hs = hs.strip()
            if not hs:
                continue

            frame = IEC104Frame()
            frame.index = len(frames) + 1
            frame.raw_hex = hs
            frame.source = source

            try:
                raw_bytes = self._hex_to_bytes(frame.raw_hex)
            except ValueError as e:
                frame.error = f"十六进制解析错误: {e}"
                frames.append(frame)
                continue

            if len(raw_bytes) < 6:
                frame.error = "报文太短"
                frames.append(frame)
                continue

            frame.apci = self._parse_apci(raw_bytes)
            if frame.apci is None:
                frame.error = "APCI解析失败"
                frames.append(frame)
                continue

            if len(raw_bytes) < frame.apci.total_length:
                frame.error = f"报文不完整"
                frames.append(frame)
                continue

            raw_bytes = raw_bytes[:frame.apci.total_length]

            if frame.apci.frame_type == "I-format":
                try:
                    frame.asdu = self._parse_asdu(raw_bytes[6:], frame.apci)
                    self._build_description(frame)

                    tid = frame.asdu.type_id
                    if tid in _CONTROL_TYPE_IDS:
                        frame.direction = "主站→子站"
                        frame.send_role = "主站"
                        frame.recv_role = "子站"
                    elif tid in _MONITOR_TYPE_IDS:
                        frame.direction = "子站→主站"
                        frame.send_role = "子站"
                        frame.recv_role = "主站"
                    else:
                        # 对S/U以外的未知I帧，使用交替判断
                        if self.last_master_send:
                            frame.direction = "子站→主站"
                            frame.send_role = "子站"
                            frame.recv_role = "主站"
                        else:
                            frame.direction = "主站→子站"
                            frame.send_role = "主站"
                            frame.recv_role = "子站"

                    self.last_master_send = (frame.send_role == "主站")
                    frame.signal_type = frame.asdu.signal_type
                    frame.signal_label = frame.asdu.signal_type.label

                except Exception as e:
                    frame.error = f"ASDU解析异常: {e}"
                    logger.warning("ASDU解析异常: %s", e, exc_info=True)
                    # ASDU解析异常时也需要翻转方向状态
                    self.last_master_send = not self.last_master_send
            elif frame.apci.frame_type == "S-format":
                frame.description = f"S帧 (确认帧, RN={frame.apci.recv_seq})"
                # S帧方向根据交替模式判断
                if self.last_master_send:
                    frame.direction = "子站→主站"
                    frame.send_role = "子站"
                    frame.recv_role = "主站"
                else:
                    frame.direction = "主站→子站"
                    frame.send_role = "主站"
                    frame.recv_role = "子站"
                self.last_master_send = not self.last_master_send
            elif frame.apci.frame_type == "U-format":
                frame.description = self._describe_u_frame(raw_bytes[2:6])
                # U帧方向根据交替模式判断
                if self.last_master_send:
                    frame.direction = "子站→主站"
                    frame.send_role = "子站"
                    frame.recv_role = "主站"
                else:
                    frame.direction = "主站→子站"
                    frame.send_role = "主站"
                    frame.recv_role = "子站"
                self.last_master_send = not self.last_master_send

            frames.append(frame)

        self.frames = frames
        return frames

    def clear(self):
        """清除已解析的报文"""
        self.frames.clear()

    def get_statistics(self) -> Dict[str, Any]:
        """获取解析统计"""
        total = len(self.frames)
        valid = sum(1 for f in self.frames if not f.error)
        i_frames = sum(1 for f in self.frames if f.apci and f.apci.frame_type == "I-format")
        s_frames = sum(1 for f in self.frames if f.apci and f.apci.frame_type == "S-format")
        u_frames = sum(1 for f in self.frames if f.apci and f.apci.frame_type == "U-format")
        errors = sum(1 for f in self.frames if f.error)

        master_to_slave = sum(1 for f in self.frames if f.direction == "主站→子站")
        slave_to_master = sum(1 for f in self.frames if f.direction == "子站→主站")

        return {
            "total": total,
            "valid": valid,
            "invalid": total - valid,
            "i_frames": i_frames,
            "s_frames": s_frames,
            "u_frames": u_frames,
            "errors": errors,
            "master_send": master_to_slave,
            "slave_send": slave_to_master,
        }

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _hex_to_bytes(self, hex_string: str) -> bytes:
        """十六进制字符串 → 字节"""
        cleaned = hex_string.replace(" ", "").replace("\n", "").replace("\r", "")
        cleaned = cleaned.replace("0x", "").replace("0X", "")
        cleaned = cleaned.replace(",", "").replace("[", "").replace("]", "")
        cleaned = ''.join(c for c in cleaned if c in '0123456789ABCDEFabcdef')
        if len(cleaned) % 2 != 0:
            raise ValueError("十六进制字符串长度为奇数")
        return bytes.fromhex(cleaned)

    def _parse_apci(self, raw: bytes) -> Optional[APCIHeader]:
        """解析APCI控制头"""
        if raw[0] != 0x68:
            return None

        apdu_len = raw[1]
        ctrl_bytes = raw[2:6]
        ctrl1 = ctrl_bytes[0]
        ctrl2 = ctrl_bytes[1]
        ctrl3 = ctrl_bytes[2]
        ctrl4 = ctrl_bytes[3]

        header = APCIHeader(start_byte=0x68, apdu_length=apdu_len)

        if (ctrl1 & 0x01) == 0:
            header.frame_type = "I-format"
            header.send_seq = ((ctrl2 & 0x7F) << 8) | ctrl1
            header.send_seq = header.send_seq >> 1
            header.recv_seq = ((ctrl4 & 0x7F) << 8) | ctrl3
            header.recv_seq = header.recv_seq >> 1
        elif (ctrl1 & 0x03) == 0x01:
            header.frame_type = "S-format"
            header.recv_seq = ((ctrl4 & 0x7F) << 8) | ctrl3
            header.recv_seq = header.recv_seq >> 1
        elif (ctrl1 & 0x03) == 0x03:
            header.frame_type = "U-format"

        return header

    def _describe_u_frame(self, ctrl: bytes) -> str:
        """描述U帧功能"""
        func = ctrl[0]
        descs = {
            0x07: "U帧: STARTDT 激活 (启动数据传输)",
            0x13: "U帧: STARTDT 确认 (启动数据传输确认)",
            0x23: "U帧: STOPDT 激活 (停止数据传输)",
            0x33: "U帧: STOPDT 确认 (停止数据传输确认)",
            0x43: "U帧: TESTFR 激活 (测试帧)",
            0x73: "U帧: TESTFR 确认 (测试帧确认)",
        }
        for key, desc in descs.items():
            if func == key:
                return desc
        return f"U帧 (控制域: 0x{func:02X} 0x{ctrl[1]:02X} 0x{ctrl[2]:02X} 0x{ctrl[3]:02X})"

    def _parse_asdu(self, data: bytes, apci: APCIHeader) -> ASDU:
        """解析ASDU"""
        type_id = data[0]
        asdu = ASDU(
            type_id=type_id,
            vsq=data[1],
            number_of_objects=data[1] & 0x7F,
            is_sequence=bool(data[1] >> 7),
            raw_data=data,
            signal_type=SignalType.from_type_id(type_id),
        )

        # 传送原因 (2字节, 低字节在前)
        cot = data[2] + (data[3] << 8)
        asdu.cot = cot
        asdu.cot_cause = cot & 0x3F
        asdu.cot_test = bool((cot >> 7) & 1)
        asdu.cot_pn = bool((cot >> 6) & 1)

        # 源发方地址(ORG)
        asdu.org = data[4]

        # 公共地址 (2字节, 低字节在前)
        asdu.common_address = data[5] + (data[6] << 8)

        # 信息元素
        elem_data = data[7:]
        offset = 0
        prev_ioa = 0

        for i in range(asdu.number_of_objects):
            elem = InfoElement()

            if offset + 3 > len(elem_data):
                break

            if asdu.is_sequence and i > 0:
                prev_ioa += 1
                elem.ioa = prev_ioa
            else:
                elem.ioa = elem_data[offset] + (elem_data[offset + 1] << 8) \
                    + (elem_data[offset + 2] << 16)
                offset += 3
                prev_ioa = elem.ioa

            # 根据 IOA 设置信号类型
            elem.signal_type = SignalType.from_ioa(elem.ioa)

            # 根据类型ID解析元素数据
            consumed = self._parse_element_value(asdu.type_id, elem_data[offset:], elem)
            offset += consumed

            asdu.information_elements.append(elem)

        # 0补齐对齐
        if len(data) % 2 == 1:
            asdu.pad = data[-1]

        return asdu

    def _parse_element_value(self, type_id: int, data: bytes, elem: InfoElement) -> int:
        """解析信息元素的值，返回消耗的字节数"""
        consumed = 0

        try:
            if type_id == 1:  # M_SP_NA_1 单点信息 (遥信)
                if len(data) >= 1:
                    elem.siq = data[0]
                    elem.quality = QualityDescriptor(data[0], is_siq_diq=True)
                    elem.value = bool(data[0] & 0x01)
                    val_dec = "合" if elem.value else "分"
                    elem.value_desc = f"SPI={val_dec} | DEC={int(elem.value)} HEX=0x{data[0]:02X}"
                    consumed = 1

            elif type_id == 2:  # M_SP_TA_1 带CP24Time2a的单点信息 (遥信)
                if len(data) >= 1:
                    elem.siq = data[0]
                    elem.quality = QualityDescriptor(data[0], is_siq_diq=True)
                    elem.value = bool(data[0] & 0x01)
                    val_dec = "合" if elem.value else "分"
                    elem.value_desc = f"SPI={val_dec} | DEC={int(elem.value)} HEX=0x{data[0]:02X}"
                    consumed = 1
                    if len(data) >= 4:
                        elem.timestamp = parse_cp24time2a(data[1:4])
                        consumed = 4
                    else:
                        consumed = 1

            elif type_id == 3:  # M_DP_NA_1 双点信息 (遥信)
                if len(data) >= 1:
                    elem.diq = data[0]
                    elem.quality = QualityDescriptor(data[0], is_siq_diq=True)
                    dpi = (data[0] >> 2) & 0x03
                    dpi_map = {0: "中间态/不确定", 1: "分", 2: "合", 3: "中间态"}
                    elem.value = dpi
                    elem.value_desc = f"DPI={dpi_map.get(dpi, '?')} | DEC={dpi} HEX=0x{data[0]:02X}"
                    consumed = 1

            elif type_id == 4:  # M_DP_TA_1 带CP24Time2a的双点信息 (遥信)
                if len(data) >= 1:
                    elem.diq = data[0]
                    elem.quality = QualityDescriptor(data[0], is_siq_diq=True)
                    dpi = (data[0] >> 2) & 0x03
                    dpi_map = {0: "中间态/不确定", 1: "分", 2: "合", 3: "中间态"}
                    elem.value = dpi
                    elem.value_desc = f"DPI={dpi_map.get(dpi, '?')} | DEC={dpi} HEX=0x{data[0]:02X}"
                    consumed = 1
                    if len(data) >= 4:
                        elem.timestamp = parse_cp24time2a(data[1:4])
                        consumed = 4
                    else:
                        consumed = 1

            elif type_id == 9:  # M_ME_NA_1 测量值(归一化) (遥测)
                if len(data) >= 2:
                    raw_val = data[0] + (data[1] << 8)
                    signed_val = self._to_signed(raw_val, 16)
                    normalized = raw_val / 32767.0
                    elem.value = raw_val
                    elem.value_desc = (
                        f"归一化={normalized:.6f} | DEC={raw_val}(有符号:{signed_val}) HEX=0x{raw_val:04X}"
                    )
                    consumed = 2
                    if len(data) >= 3:
                        elem.quality = QualityDescriptor(data[2])
                        consumed = 3

            elif type_id == 10:  # M_ME_TA_1 带时标的测量值(归一化) (遥测)
                if len(data) >= 2:
                    raw_val = data[0] + (data[1] << 8)
                    signed_val = self._to_signed(raw_val, 16)
                    normalized = raw_val / 32767.0
                    elem.value = raw_val
                    elem.value_desc = (
                        f"归一化={normalized:.6f} | DEC={raw_val}(有符号:{signed_val}) HEX=0x{raw_val:04X}"
                    )
                    consumed = 2
                    if len(data) >= 3:
                        elem.quality = QualityDescriptor(data[2])
                        consumed = 3
                        if len(data) >= 6:
                            elem.timestamp = parse_cp24time2a(data[3:6])
                            consumed = 6

            elif type_id == 11:  # M_ME_NB_1 测量值(标度化) (遥测)
                if len(data) >= 2:
                    signed_val = self._to_signed(data[0] + (data[1] << 8), 16)
                    elem.value = signed_val
                    elem.value_desc = f"标度化={signed_val} | DEC={signed_val} HEX=0x{signed_val & 0xFFFF:04X}"
                    consumed = 2
                    if len(data) >= 3:
                        elem.quality = QualityDescriptor(data[2])
                        consumed = 3

            elif type_id == 13:  # M_ME_NC_1 测量值(短浮点) (遥测)
                if len(data) >= 4:
                    raw = struct.unpack('<f', data[0:4])[0]
                    raw_int = struct.unpack('<I', data[0:4])[0]
                    elem.value = round(raw, 4)
                    elem.value_desc = f"浮点={elem.value} | DEC={raw_int} HEX=0x{raw_int:08X}"
                    consumed = 4
                    if len(data) >= 5:
                        elem.quality = QualityDescriptor(data[4])
                        consumed = 5

            elif type_id == 15:  # M_IT_NA_1 累计量 (遥脉)
                if len(data) >= 4:
                    elem.value = data[0] + (data[1] << 8) + (data[2] << 16) + (data[3] << 24)
                    elem.value_desc = f"累计量={elem.value} | DEC={elem.value} HEX=0x{elem.value:08X}"
                    consumed = 4
                    if len(data) >= 5:
                        elem.quality = QualityDescriptor(data[4])
                        consumed = 5

            elif type_id == 20:  # M_PS_NA_1 成组单点信息 (遥信)
                if len(data) >= 1:
                    elem.siq = data[0]
                    elem.quality = QualityDescriptor(data[0], is_siq_diq=True)
                    elem.value = bool(data[0] & 0x01)
                    elem.value_desc = f"SIQ=0x{data[0]:02X}"
                    consumed = 1

            elif type_id == 21:  # M_ME_ND_1 测量值(归一化,无品质) (遥测)
                if len(data) >= 2:
                    raw_val = data[0] + (data[1] << 8)
                    normalized = raw_val / 32767.0
                    elem.value = raw_val
                    elem.value_desc = f"归一化={normalized:.6f} | DEC={raw_val} HEX=0x{raw_val:04X}"
                    consumed = 2

            elif type_id in (30, 31, 32, 33):
                consumed = self._parse_with_timestamp(type_id, data, elem)

            elif type_id in (34, 35, 36, 37, 38, 39, 40):
                consumed = self._parse_measured_with_timestamp(type_id, data, elem)

            elif type_id == 70:  # M_EI_NA_1 初始化结束
                if len(data) >= 1:
                    elem.value = data[0]
                    elem.value_desc = f"COI=0x{data[0]:02X} | DEC={data[0]} HEX=0x{data[0]:02X}"
                    consumed = 1

            elif type_id in (100, 101, 102, 103, 104, 105, 106, 107):
                consumed = self._parse_control_system(type_id, data, elem)

            elif type_id in (45, 46, 47, 48, 49, 50, 51):
                consumed = self._parse_control_command(type_id, data, elem)

            elif type_id in (58, 59, 60, 61, 62, 63, 64):
                consumed = self._parse_control_command_timestamp(type_id, data, elem)

            else:
                remaining = len(data)
                if remaining > 0:
                    elem.raw_value = data[:min(remaining, 8)]
                    elem.value_desc = f"原始字节: {elem.raw_value.hex().upper()}"
                    consumed = min(remaining, 8)
        except Exception:
            consumed = min(len(data), 4)
            logger.warning("解析元素值异常 type_id=%d", type_id, exc_info=True)

        return consumed

    def _parse_with_timestamp(self, type_id: int, data: bytes, elem: InfoElement) -> int:
        """解析带CP56Time2a时标的基本信息"""
        if len(data) < 11:
            return len(data)

        elem.quality = QualityDescriptor(data[0])
        if type_id == 30:  # 单点 (遥信)
            elem.value = bool(data[0] & 0x01)
            val_dec = "合" if elem.value else "分"
            elem.value_desc = f"SPI={val_dec} | DEC={int(elem.value)} HEX=0x{data[0]:02X}"
        elif type_id == 31:  # 双点 (遥信)
            dpi = (data[0] >> 2) & 0x03
            elem.value = dpi
            dpi_map = {0: "不确定", 1: "分", 2: "合", 3: "中间态"}
            elem.value_desc = f"DPI={dpi_map.get(dpi, f'{dpi}')} | DEC={dpi} HEX=0x{data[0]:02X}"
        elif type_id == 32:  # 步位置 (遥测)
            elem.value = data[0]
            elem.value_desc = f"步位置={data[0]} | DEC={data[0]} HEX=0x{data[0]:02X}"
        elif type_id == 33:  # 32比特串 (遥测)
            elem.value = struct.unpack('<I', data[0:4])[0]
            elem.value_desc = f"32比特串={elem.value} | DEC={elem.value} HEX=0x{elem.value:08X}"

        elem.timestamp = parse_cp56time2a(data[4:11])
        return 11

    def _parse_measured_with_timestamp(self, type_id: int, data: bytes,
                                        elem: InfoElement) -> int:
        """解析带CP56Time2a时标的测量值"""
        offset = 0
        try:
            if type_id == 34:  # 归一化值 (遥测)
                if len(data) >= 3:
                    raw_val = data[0] + (data[1] << 8)
                    signed_val = self._to_signed(raw_val, 16)
                    normalized = raw_val / 32767.0
                    elem.value = raw_val
                    elem.value_desc = (
                        f"归一化={normalized:.6f} | DEC={raw_val}(有符号:{signed_val}) HEX=0x{raw_val:04X}"
                    )
                    elem.quality = QualityDescriptor(data[2])
                    offset = 3
            elif type_id == 35:  # 标度化值 (遥测)
                if len(data) >= 3:
                    signed_val = self._to_signed(data[0] + (data[1] << 8), 16)
                    elem.value = signed_val
                    elem.value_desc = f"标度化={signed_val} | DEC={signed_val} HEX=0x{signed_val & 0xFFFF:04X}"
                    elem.quality = QualityDescriptor(data[2])
                    offset = 3
            elif type_id == 36:  # 短浮点数 (遥测)
                if len(data) >= 5:
                    raw = struct.unpack('<f', data[0:4])[0]
                    raw_int = struct.unpack('<I', data[0:4])[0]
                    elem.value = round(raw, 4)
                    elem.value_desc = f"浮点={elem.value} | DEC={raw_int} HEX=0x{raw_int:08X}"
                    elem.quality = QualityDescriptor(data[4])
                    offset = 5
            elif type_id == 37:  # 累计量 (遥脉)
                if len(data) >= 5:
                    elem.value = struct.unpack('<I', data[0:4])[0]
                    elem.value_desc = f"累计量={elem.value} | DEC={elem.value} HEX=0x{elem.value:08X}"
                    elem.quality = QualityDescriptor(data[4])
                    offset = 5

            # CP56Time2a (7字节)
            if len(data) >= offset + 7:
                elem.timestamp = parse_cp56time2a(data[offset:offset + 7])
                offset += 7
        except Exception:
            pass
        return offset

    def _parse_control_command(self, type_id: int, data: bytes,
                                elem: InfoElement) -> int:
        """解析控制方向命令 (遥控/遥调)"""
        if len(data) < 1:
            return 0

        # 确定是遥控还是遥调
        is_remote_control = type_id in (45, 46, 58, 59)    # 遥控
        is_remote_adjust = type_id in (47, 48, 49, 50, 60, 61, 62, 63, 64)  # 遥调
        ctrl_type = "遥控" if is_remote_control else ("遥调" if is_remote_adjust else "控制")

        sco = data[0]
        consumed = 1

        if type_id == 45:  # C_SC_NA_1 单点命令 (遥控)
            se = (sco >> 7) & 1
            scs = sco & 0x01
            val_dec = "合" if scs else "分"
            sel_exec = "执行" if se else "选择"
            elem.value = {"scs": scs, "se": se}
            elem.value_desc = (
                f"[{ctrl_type}] 单点={val_dec} | 操作={sel_exec} "
                f"| DEC={sco} HEX=0x{sco:02X}"
            )

        elif type_id == 46:  # C_DC_NA_1 双点命令 (遥控)
            se = (sco >> 7) & 1
            dcs = (sco >> 2) & 0x03
            dcs_map = {0: "不允许", 1: "分", 2: "合", 3: "不允许"}
            sel_exec = "执行" if se else "选择"
            elem.value = {"dcs": dcs, "se": se}
            elem.value_desc = (
                f"[{ctrl_type}] 双点={dcs_map.get(dcs, str(dcs))} | 操作={sel_exec} "
                f"| DEC={sco} HEX=0x{sco:02X}"
            )

        elif type_id == 47:  # C_RC_NA_1 调节步命令 (遥调)
            se = (sco >> 7) & 1
            step_down = (sco >> 0) & 0x01
            rca = (sco >> 1) & 0x3F
            sel_exec = "执行" if se else "选择"
            direction = "升" if step_down else "降"
            elem.value = {"rca": rca, "step_dir": direction, "se": se}
            elem.value_desc = (
                f"[{ctrl_type}] 步方向={direction} | 操作={sel_exec} "
                f"| DEC={sco} HEX=0x{sco:02X}"
            )

        elif type_id == 48:  # C_SE_NA_1 设定值(归一化) (遥调)
            if len(data) >= 3:
                raw_val = data[0] + (data[1] << 8)
                signed_val = self._to_signed(raw_val, 16)
                normalized = raw_val / 32767.0
                qdp = data[2]
                elem.value = {"raw": raw_val, "normalized": normalized}
                elem.value_desc = (
                    f"[{ctrl_type}] 归一化设定值={normalized:.6f} "
                    f"| DEC={raw_val}(有符号:{signed_val}) HEX=0x{raw_val:04X} "
                    f"| QDS=0x{qdp:02X}"
                )
                consumed = 3
            else:
                elem.value_desc = f"[{ctrl_type}] 数据不足 | DEC={sco} HEX=0x{sco:02X}"

        elif type_id == 49:  # C_SE_NB_1 设定值(标度化) (遥调)
            if len(data) >= 3:
                signed_val = self._to_signed(data[0] + (data[1] << 8), 16)
                qdp = data[2]
                elem.value = signed_val
                elem.value_desc = (
                    f"[{ctrl_type}] 标度化设定值={signed_val} "
                    f"| DEC={signed_val} HEX=0x{signed_val & 0xFFFF:04X} "
                    f"| QDS=0x{qdp:02X}"
                )
                consumed = 3
            else:
                elem.value_desc = f"[{ctrl_type}] 数据不足 | DEC={sco} HEX=0x{sco:02X}"

        elif type_id == 50:  # C_SE_NC_1 设定值(短浮点) (遥调)
            if len(data) >= 5:
                raw = struct.unpack('<f', data[0:4])[0]
                raw_int = struct.unpack('<I', data[0:4])[0]
                qdp = data[4]
                elem.value = round(raw, 4)
                elem.value_desc = (
                    f"[{ctrl_type}] 浮点设定值={elem.value} "
                    f"| DEC={raw_int} HEX=0x{raw_int:08X} "
                    f"| QDS=0x{qdp:02X}"
                )
                consumed = 5
            else:
                elem.value_desc = f"[{ctrl_type}] 数据不足 | DEC={sco} HEX=0x{sco:02X}"

        elif type_id == 51:  # C_BO_NA_1 32比特串命令 (遥调)
            if len(data) >= 5:
                val = struct.unpack('<I', data[0:4])[0]
                qdp = data[4]
                elem.value = val
                elem.value_desc = (
                    f"[{ctrl_type}] 32比特串=0x{val:08X} "
                    f"| DEC={val} HEX=0x{val:08X} "
                    f"| QDS=0x{qdp:02X}"
                )
                consumed = 5
            else:
                elem.value_desc = f"[{ctrl_type}] 数据不足 | DEC={sco} HEX=0x{sco:02X}"
        else:
            elem.value = sco
            elem.value_desc = f"[{ctrl_type}] 控制=0x{sco:02X} | DEC={sco} HEX=0x{sco:02X}"

        return consumed

    def _parse_control_command_timestamp(self, type_id: int, data: bytes,
                                          elem: InfoElement) -> int:
        """解析带时标的控制命令"""
        consumed = self._parse_control_command(type_id, data, elem)
        if len(data) >= consumed + 7:
            elem.timestamp = parse_cp56time2a(data[consumed:consumed + 7])
            consumed += 7
        return consumed

    def _parse_control_system(self, type_id: int, data: bytes,
                               elem: InfoElement) -> int:
        """解析控制方向系统命令"""
        if type_id == 100:  # C_IC_NA_1 总召唤
            if len(data) >= 1:
                elem.value = data[0]
                elem.value_desc = f"QOI=0x{data[0]:02X} | DEC={data[0]} HEX=0x{data[0]:02X}"
                return 1
        elif type_id == 103:  # C_CS_NA_1 时钟同步
            if len(data) >= 7:
                elem.timestamp = parse_cp56time2a(data[:7])
                elem.value = elem.timestamp
                elem.value_desc = f"时钟同步: {elem.timestamp}"
                return 7
        elif type_id in (101, 102, 104, 105, 106):
            elem.value = data[0] if len(data) >= 1 else 0
            elem.value_desc = f"命令参数=0x{elem.value:02X} | DEC={elem.value} HEX=0x{elem.value:02X}"
            return 1 if len(data) >= 1 else 0
        return len(data)

    def _build_description(self, frame: IEC104Frame):
        """构建报文描述"""
        if not frame.asdu:
            return

        a = frame.asdu
        parts = []
        parts.append(f"{a.signal_type.label}")
        parts.append(f"{TypeID.get_description(a.type_id)}")
        parts.append(f"VSQ=0x{a.vsq:02X}({a.number_of_objects}个对象{'顺序' if a.is_sequence else '离散'})")
        parts.append(COT.get_description(a.cot))
        parts.append(f"ORG={a.org}, 公共地址={a.common_address}")

        frame.description = "、".join(parts)

    @staticmethod
    def _to_signed(val: int, bits: int) -> int:
        """将无符号整数转换为有符号"""
        if val & (1 << (bits - 1)):
            return val - (1 << bits)
        return val
