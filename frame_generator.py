"""
IEC104 帧生成器
根据用户参数生成符合标准的 IEC104 报文帧

本模块只做"单一对象（VSQ=1，离散地址）"的帧生成——这正是调试/联调场景
最常用的形态。需要多对象或顺序地址时请显式调用方自行扩展。
"""

import struct
from datetime import datetime
from typing import Optional

MAX_IOA = 0xFFFFFF
MAX_COMMON_ADDR = 0xFFFF
MAX_SEQ = 32767

# S/E 位（选择/执行）所在字节与位：SCO/DCO/QOS 在 bit7，RCO 在 bit6
_SE_BITPOS = {47: 6, 60: 6}

# 支持生成的控制命令类型（select 参数只对它们生效）
_COMMAND_TYPES = frozenset(
    {45, 46, 47, 48, 49, 50, 51, 58, 59, 60, 61, 62, 63, 64})


def generate_i_frame(
    type_id: int,
    cot: int,
    common_addr: int,
    ioa: int,
    value,
    send_seq: int = 0,
    recv_seq: int = 0,
    vsq: int = 1,
    origin_addr: int = 0,
    select: bool = False,
    timestamp: Optional[datetime] = None,
) -> str:
    """生成 I 格式帧。

    select: 仅对遥控/遥调命令（45/46/47/48/49/50/51/58-64）生效。
        为 True 时置 S/E=1（选择/预制）；为 False 时置 S/E=0（执行）。
        符合 IEC 60870-5-101 §7.2.6：S/E 位 1=选择、0=执行。
        其它类型忽略该参数。

    timestamp: CP56Time2a/CP24Time2a 时标所用的时间，缺省取当前本地时间。

    参数越界或类型不支持时抛 ValueError——静默生成含义错误的帧比报错更危险。
    """
    _check_range("type_id", type_id, 1, 255)
    _check_range("cot", cot, 0, 255)
    _check_range("origin_addr", origin_addr, 0, 255)
    _check_range("common_addr", common_addr, 0, MAX_COMMON_ADDR)
    _check_range("ioa", ioa, 0, MAX_IOA)
    _check_range("send_seq", send_seq, 0, MAX_SEQ)
    _check_range("recv_seq", recv_seq, 0, MAX_SEQ)
    _check_range("vsq", vsq, 1, 127)

    asdu = bytearray()
    asdu.append(type_id & 0xFF)
    asdu.append(vsq & 0x7F)
    # COT 域 2 字节 = [原因+P/N+T, ORG]。此前在 cot>255 时把高字节当 ORG
    # 写进去、又丢掉调用方给的 origin_addr，帧结构随参数静默变化。
    asdu.append(cot & 0xFF)
    asdu.append(origin_addr & 0xFF)
    asdu.append(common_addr & 0xFF)
    asdu.append((common_addr >> 8) & 0xFF)

    # IOA (3 bytes, 低字节在前)
    asdu.append(ioa & 0xFF)
    asdu.append((ioa >> 8) & 0xFF)
    asdu.append((ioa >> 16) & 0xFF)

    asdu.extend(_encode_value(type_id, value, select, timestamp))

    # APCI - 控制域 4 字节
    apdu_len = len(asdu) + 4
    if apdu_len > 253:
        raise ValueError(
            f"APDU 长度 {apdu_len} 超过上限 253 字节（ASDU 过长）")
    ctrl1 = ((send_seq & 0x7F) << 1) & 0xFF  # SN 低7位, bit0=0(I帧)
    ctrl2 = (send_seq >> 7) & 0xFF            # SN 高8位
    ctrl3 = ((recv_seq & 0x7F) << 1) & 0xFF  # RN 低7位
    ctrl4 = (recv_seq >> 7) & 0xFF            # RN 高8位
    apci = bytes([0x68, apdu_len, ctrl1, ctrl2, ctrl3, ctrl4])

    frame = apci + bytes(asdu)
    return ' '.join(f'{b:02X}' for b in frame)


def _check_range(name: str, val, lo: int, hi: int):
    if not isinstance(val, int) or not (lo <= val <= hi):
        raise ValueError(f"参数 {name}={val!r} 超出合法范围 {lo}~{hi}")


def _int16(value) -> bytes:
    """16 位有符号数（NVA/SVA 均为补码）"""
    return struct.pack('<h', max(-32768, min(32767, int(value))))


def _uint32(value) -> bytes:
    """32 位数据（M_BO / C_BO 的比特串按无符号处理）"""
    return struct.pack('<I', int(value) & 0xFFFFFFFF)


def _int32(value) -> bytes:
    """32 位累计量（BCR 为补码）"""
    return struct.pack('<i', max(-2147483648, min(2147483647, int(value))))


def _float32(value) -> bytes:
    return struct.pack('<f', float(value))


def _se_byte(value, type_id: int, select: bool) -> int:
    """把 S/E 位写进命令字节（保留调用方给出的其余位）"""
    pos = _SE_BITPOS.get(type_id, 7)
    byte = int(value) & 0xFF
    byte &= ~(1 << pos) & 0xFF
    if select:
        byte |= (1 << pos)
    return byte


def _encode_value(type_id: int, value, select: bool,
                  timestamp: Optional[datetime]) -> bytes:
    """按类型标识编码信息体（值 + 品质 + 时标）"""
    ts = timestamp or datetime.now()

    if type_id in (1, 3):            # M_SP_NA_1 / M_DP_NA_1
        return bytes([int(value) & 0xFF])
    if type_id in (2, 4):            # M_SP_TA_1 / M_DP_TA_1 + CP24Time2a
        return bytes([int(value) & 0xFF]) + _cp24time2a(ts)

    if type_id in (5, 6):            # M_ST_NA_1 / M_ST_TA_1: VTI + QDS
        body = bytes([int(value) & 0x7F, 0])
        return body + (_cp24time2a(ts) if type_id == 6 else b'')

    if type_id in (7, 8):            # M_BO_NA_1 / M_BO_TA_1: 比特串 + QDS
        body = _uint32(value) + bytes([0])
        return body + (_cp24time2a(ts) if type_id == 8 else b'')

    if type_id in (9, 10, 11, 12):   # 归一化/标度化测量值 + QDS
        body = _int16(value) + bytes([0])
        return body + (_cp24time2a(ts) if type_id in (10, 12) else b'')

    if type_id in (13, 14):          # 短浮点 + QDS
        body = _float32(value) + bytes([0])
        return body + (_cp24time2a(ts) if type_id == 14 else b'')

    if type_id in (15, 16):          # 累计量 + QDS
        body = _int32(value) + bytes([0])
        return body + (_cp24time2a(ts) if type_id == 16 else b'')

    if type_id == 21:                # M_ME_ND_1: 归一化值，无品质描述词
        return _int16(value)

    if type_id in (30, 31):          # 带 CP56Time2a 的单点/双点
        return bytes([int(value) & 0xFF]) + _cp56time2a(ts)
    if type_id == 32:                # M_ST_TB_1
        return bytes([int(value) & 0x7F, 0]) + _cp56time2a(ts)
    if type_id == 33:                # M_BO_TB_1
        return _uint32(value) + bytes([0]) + _cp56time2a(ts)
    if type_id in (34, 35):          # M_ME_TD_1 / M_ME_TE_1
        return _int16(value) + bytes([0]) + _cp56time2a(ts)
    if type_id == 36:                # M_ME_TF_1
        return _float32(value) + bytes([0]) + _cp56time2a(ts)
    if type_id == 37:                # M_IT_TB_1
        return _int32(value) + bytes([0]) + _cp56time2a(ts)

    if type_id in (45, 46, 58, 59):  # SCO / DCO（S/E 在 bit7）
        return bytes([_se_byte(value, type_id, select)])
    if type_id in (47, 60):          # RCO（S/E 在 bit6，bit7=QL）
        return bytes([_se_byte(value, type_id, select)])
    if type_id in (48, 49):          # 设点(归一化/标度化) + QOS
        return _int16(value) + bytes([_se_byte(0, type_id, select)])
    if type_id == 50:                # 设点(短浮点) + QOS
        return _float32(value) + bytes([_se_byte(0, type_id, select)])
    if type_id == 51:                # 32 比特串命令 + QOS
        return _uint32(value) + bytes([_se_byte(0, type_id, select)])
    if type_id in (61, 62):          # 带时标设点(归一化/标度化)
        return (_int16(value) + bytes([_se_byte(0, type_id, select)])
                + _cp56time2a(ts))
    if type_id == 63:                # 带时标设点(短浮点)
        return (_float32(value) + bytes([_se_byte(0, type_id, select)])
                + _cp56time2a(ts))
    if type_id == 64:                # 带时标 32 比特串命令
        return (_uint32(value) + bytes([_se_byte(0, type_id, select)])
                + _cp56time2a(ts))
    if type_id == 60:                # C_RC_TA_1 带时标的调节步命令
        return bytes([_se_byte(value, type_id, select)]) + _cp56time2a(ts)

    if type_id == 70:                # M_EI_NA_1 初始化结束: COI
        return bytes([int(value) & 0xFF])
    if type_id in (100, 101, 105):   # 总召唤 QOI / 计数量召唤 QCC / 复位进程 QRP
        return bytes([int(value) & 0xFF])
    if type_id == 102:               # C_RD_NA_1 读命令：无信息体
        return b''
    if type_id == 103:               # 时钟同步：CP56Time2a
        return _cp56time2a(ts)
    if type_id == 104:               # 测试命令：固定 2 字节 0
        return b'\x00\x00'
    if type_id == 106:               # 延迟获取：CP16Time2a
        return struct.pack('<H', int(value) & 0xFFFF)
    if type_id == 107:               # 带时标测试命令：2 字节测试值 + CP56
        return struct.pack('<H', int(value) & 0xFFFF) + _cp56time2a(ts)
    if type_id in (108, 109):        # 参数(归一化/标度化): QPM + 2 字节值
        return bytes([0]) + _int16(value)
    if type_id == 110:               # 参数(短浮点): QPM + float
        return bytes([0]) + _float32(value)

    raise ValueError(
        f"帧生成器不支持 TypeID={type_id}（文件传输 120-127 等暂不支持），"
        f"已拒绝生成，避免产出结构错误的帧")


def generate_s_frame(recv_seq: int) -> str:
    """生成 S 格式帧"""
    _check_range("recv_seq", recv_seq, 0, MAX_SEQ)
    ctrl3 = ((recv_seq & 0x7F) << 1) & 0xFF  # RN 低7位, bit0=1(S帧)
    ctrl4 = (recv_seq >> 7) & 0xFF            # RN 高8位
    frame = bytes([0x68, 0x04, 0x01, 0x00, ctrl3, ctrl4])
    return ' '.join(f'{b:02X}' for b in frame)


def generate_u_frame(u_type: str = "STARTDT_ACT") -> str:
    """生成 U 格式帧"""
    u_types = {
        "STARTDT_ACT": 0x07,
        "STARTDT_CON": 0x0B,
        "STOPDT_ACT": 0x13,
        "STOPDT_CON": 0x23,
        "TESTFR_ACT": 0x43,
        "TESTFR_CON": 0x83,
    }
    u_byte = u_types.get(u_type, 0x07)
    frame = bytes([0x68, 0x04, u_byte, 0x00, 0x00, 0x00])
    return ' '.join(f'{b:02X}' for b in frame)


def _cp56time2a(now: Optional[datetime] = None) -> bytes:
    """生成 CP56Time2a (7字节)

    Byte 0-1: 毫秒 (0-59999，含秒)
    Byte 2:   分钟 (bit0-5) + IV(bit7)
    Byte 3:   小时 (bit0-4)
    Byte 4:   日 (bit0-4) + 星期 (bit5-7)
    Byte 5:   月 (bit0-3)
    Byte 6:   年 (bit0-6) + 夏时制 (bit7)

    时标按 IEC 60870-5-101 使用**本地时间**（SU 位表示夏令时），
    与工程现场设备的惯例一致。
    """
    now = now or datetime.now()
    ms = now.second * 1000 + now.microsecond // 1000
    return struct.pack('<H', ms) + bytes([
        now.minute & 0x3F,          # Byte 2: 分钟
        now.hour & 0x1F,            # Byte 3: 小时
        now.day & 0x1F,             # Byte 4: 日
        now.month & 0x0F,           # Byte 5: 月
        (now.year - 2000) & 0x7F,   # Byte 6: 年
    ])


def _cp24time2a(now: Optional[datetime] = None) -> bytes:
    """生成 CP24Time2a (3字节)：毫秒(2) + 分钟(1)，不含小时/日期"""
    now = now or datetime.now()
    ms = now.second * 1000 + now.microsecond // 1000
    return struct.pack('<H', ms) + bytes([now.minute & 0x3F])


# TypeID 信息表（帧生成器可选类型：只列生成器真正支持且常用的类型）
TYPE_INFO = {
    1: ("M_SP_NA_1", "单点信息"),
    2: ("M_SP_TA_1", "单点信息(CP24Time2a)"),
    3: ("M_DP_NA_1", "双点信息"),
    4: ("M_DP_TA_1", "双点信息(CP24Time2a)"),
    5: ("M_ST_NA_1", "步位置信息"),
    7: ("M_BO_NA_1", "32比特串"),
    9: ("M_ME_NA_1", "测量值(归一化)"),
    10: ("M_ME_TA_1", "归一化(CP24Time2a)"),
    11: ("M_ME_NB_1", "测量值(标度化)"),
    12: ("M_ME_TB_1", "标度化(CP24Time2a)"),
    13: ("M_ME_NC_1", "测量值(短浮点)"),
    14: ("M_ME_TC_1", "短浮点(CP24Time2a)"),
    15: ("M_IT_NA_1", "累计量"),
    16: ("M_IT_TA_1", "累计量(CP24Time2a)"),
    21: ("M_ME_ND_1", "归一化(无品质)"),
    30: ("M_SP_TB_1", "单点信息(CP56Time2a)"),
    31: ("M_DP_TB_1", "双点信息(CP56Time2a)"),
    32: ("M_ST_TB_1", "步位置(CP56Time2a)"),
    33: ("M_BO_TB_1", "32比特串(CP56Time2a)"),
    34: ("M_ME_TD_1", "归一化(CP56Time2a)"),
    35: ("M_ME_TE_1", "标度化(CP56Time2a)"),
    36: ("M_ME_TF_1", "短浮点(CP56Time2a)"),
    37: ("M_IT_TB_1", "累计量(CP56Time2a)"),
    45: ("C_SC_NA_1", "单点命令"),
    46: ("C_DC_NA_1", "双点命令"),
    47: ("C_RC_NA_1", "调节步命令"),
    48: ("C_SE_NA_1", "设点命令(归一化)"),
    49: ("C_SE_NB_1", "设点命令(标度化)"),
    50: ("C_SE_NC_1", "设点命令(短浮点)"),
    51: ("C_BO_NA_1", "32比特串命令"),
    58: ("C_SC_TA_1", "单点命令(CP56Time2a)"),
    59: ("C_DC_TA_1", "双点命令(CP56Time2a)"),
    60: ("C_RC_TA_1", "调节步命令(CP56Time2a)"),
    61: ("C_SE_TA_1", "设点命令(归一化,带时标)"),
    62: ("C_SE_TB_1", "设点命令(标度化,带时标)"),
    63: ("C_SE_TC_1", "设点命令(短浮点,带时标)"),
    64: ("C_BO_TA_1", "32比特串命令(CP56Time2a)"),
    100: ("C_IC_NA_1", "总召唤命令"),
    103: ("C_CS_NA_1", "时钟同步命令"),
}

# 需要"选择/执行"参数的类型（供 GUI 决定是否显示 S/E 选项）
SELECTABLE_TYPES = tuple(sorted(_COMMAND_TYPES))
