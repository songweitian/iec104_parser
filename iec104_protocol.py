"""IEC 60870-5-104 协议公共常量（唯一事实来源）

解析器（`iec104_parser`）与模拟器（`iec104_simulator`）此前各写一份类型集合、
S/E 位位置、命令信息体偏移。历史上"遥控 S/E 位写反"就是三处实现各改一遍
才能对齐的典型；任何新增类型/改位序都必须只改这里。
"""

# ---------------------------------------------------------------------------
# 类型标识分组（用于方向判定：控制方向=主站→子站，监视方向=子站→主站）
# ---------------------------------------------------------------------------
CONTROL_TYPE_IDS = frozenset(range(45, 65)) | frozenset(range(100, 111)) \
    | frozenset(range(120, 128))
MONITOR_TYPE_IDS = frozenset(range(1, 41)) | {70}

# ---------------------------------------------------------------------------
# S/E（选择/执行）位：1 = 选择(预制)，0 = 执行
# ---------------------------------------------------------------------------
SE_SELECT = 1
SE_EXECUTE = 0
SE_BIT = 7                 # SCO/DCO/QOS 系命令：S/E 在 bit7
RCO_SE_BIT = 6             # RCO（调节步 47/60）：S/E 在 bit6，bit7 是 QL
RCO_TYPE_IDS = frozenset({47, 60})
SCO_VALUE_MASK = 0x7F
RCO_VALUE_MASK = 0x3F

# 控制命令帧里 S/E 所在字节相对 **ASDU 起始** 的偏移：
#   45-47/58-60：IOA(3) + 1 字节命令        → 9
#   48/49/61/62：2 字节值 + QOS             → 11
#   50/51/63/64：4 字节值 + QOS             → 13
SE_BYTE_OFFSETS = {
    45: 9, 46: 9, 47: 9, 58: 9, 59: 9, 60: 9,
    48: 11, 49: 11, 61: 11, 62: 11,
    50: 13, 51: 13, 63: 13, 64: 13,
}

# 需要 SBO（先选择再执行）的命令类型（模拟器从站用）
SBO_COMMAND_TYPE_IDS = frozenset({45, 46, 47, 48, 49, 50, 61, 62, 63})


def se_bit(type_id: int) -> int:
    """该类型标识的 S/E 位在命令字节中的位置（缺省 bit7）"""
    return RCO_SE_BIT if type_id in RCO_TYPE_IDS else SE_BIT


def value_mask(type_id: int) -> int:
    """该类型标识命令字节里的命令值掩码（去掉 S/E 位后）"""
    return RCO_VALUE_MASK if type_id in RCO_TYPE_IDS else SCO_VALUE_MASK


def is_control_type(type_id: int) -> bool:
    return type_id in CONTROL_TYPE_IDS


def is_monitor_type(type_id: int) -> bool:
    return type_id in MONITOR_TYPE_IDS
