"""
IEC104 帧生成器
根据用户参数生成符合标准的 IEC104 报文帧
"""

import struct
from typing import Optional


def generate_i_frame(
    type_id: int,
    cot: int,
    common_addr: int,
    ioa: int,
    value,
    send_seq: int = 0,
    vsq: int = 1,
    origin_addr: int = 0,
) -> str:
    """生成 I 格式帧"""
    # ASDU
    asdu = bytearray()
    asdu.append(type_id)
    asdu.append(vsq)
    asdu.append(cot & 0xFF)
    asdu.append((cot >> 8) & 0xFF if cot > 255 else origin_addr)
    asdu.append(common_addr & 0xFF)
    asdu.append((common_addr >> 8) & 0xFF)

    # IOA (3 bytes)
    asdu.append(ioa & 0xFF)
    asdu.append((ioa >> 8) & 0xFF)
    asdu.append((ioa >> 16) & 0xFF)

    # 值
    if type_id in (1, 2, 3, 4):  # 单点/双点信息
        asdu.append(int(value) & 0xFF)
    elif type_id in (9, 10):  # 归一化值
        val = int(value)
        asdu.append(val & 0xFF)
        asdu.append((val >> 8) & 0xFF)
        asdu.append(0)  # QDS
    elif type_id in (11, 12):  # 标度化值
        val = int(value)
        asdu.append(val & 0xFF)
        asdu.append((val >> 8) & 0xFF)
        asdu.append(0)  # QDS
    elif type_id in (13, 14):  # 短浮点
        float_bytes = struct.pack('<f', float(value))
        asdu.extend(float_bytes)
        asdu.append(0)  # QDS
    elif type_id in (15, 16):  # 累计量
        val = int(value)
        asdu.extend(struct.pack('<I', val))
        asdu.append(0)  # QDS
    elif type_id == 45:  # 单点命令
        asdu.append(int(value) & 0xFF)
    elif type_id == 46:  # 双点命令
        asdu.append(int(value) & 0xFF)
    elif type_id == 100:  # 总召唤
        asdu.append(int(value) & 0xFF)  # QOI
    elif type_id == 103:  # 时钟同步
        asdu.extend(_generate_cp56time2a())
    else:
        asdu.append(int(value) & 0xFF)

    # APCI
    apdu_len = len(asdu) + 4  # 4 control bytes
    send_seq_bits = (send_seq << 1) & 0xFF
    apci = bytes([0x68, apdu_len, send_seq_bits, 0x00, 0x00, 0x00])

    frame = apci + bytes(asdu)
    return ' '.join(f'{b:02X}' for b in frame)


def generate_s_frame(recv_seq: int) -> str:
    """生成 S 格式帧"""
    recv_seq_bits = (recv_seq << 1) & 0xFF
    frame = bytes([0x68, 0x04, 0x01, 0x00, recv_seq_bits, 0x00])
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


def _generate_cp56time2a() -> bytes:
    """生成当前时间的 CP56Time2a"""
    from datetime import datetime
    now = datetime.now()
    ms = now.second * 1000 + now.microsecond // 1000
    minute = now.minute | (now.hour << 6)
    day = now.day | (now.month << 5)
    year = now.year - 2000
    return struct.pack('<H', ms) + bytes([minute, day, year, 0, 0, 0, 0])


# TypeID 信息表
TYPE_INFO = {
    1: ("M_SP_NA_1", "单点信息"),
    3: ("M_DP_NA_1", "双点信息"),
    9: ("M_ME_NA_1", "测量值(归一化)"),
    11: ("M_ME_NB_1", "测量值(标度化)"),
    13: ("M_ME_NC_1", "测量值(短浮点)"),
    15: ("M_IT_NA_1", "累计量"),
    30: ("M_SP_TB_1", "单点信息(CP56Time2a)"),
    31: ("M_DP_TB_1", "双点信息(CP56Time2a)"),
    34: ("M_ME_TD_1", "归一化(CP56Time2a)"),
    35: ("M_ME_TE_1", "标度化(CP56Time2a)"),
    36: ("M_ME_TF_1", "短浮点(CP56Time2a)"),
    45: ("C_SC_NA_1", "单点命令"),
    46: ("C_DC_NA_1", "双点命令"),
    100: ("C_IC_NA_1", "总召唤命令"),
    103: ("C_CS_NA_1", "时钟同步命令"),
}
