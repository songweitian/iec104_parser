"""
PCAP 文件导入器
支持从 Wireshark .pcap 文件中提取 IEC104 TCP 负载

两个入口：
- read_pcap()：逐包返回 TCP 负载（原始行为，调试/测试用）
- read_pcap_frames()：**按 TCP 流重组后**再按 104 长度域切帧（导入用）

为什么需要重组：抓包文件里一条 104 报文可能被拆进多个 TCP 段（大报文跨 MTU），
也可能因重传/乱序出现重复或倒序。逐包当"一条报文"处理会得到截断帧或错误帧，
而且只在特定网络条件下复现，最难排查。
"""

import struct
import os
from datetime import datetime
from typing import List, Tuple, Optional, NamedTuple

# pcapng 魔数（Wireshark 新版默认保存格式）。本导入器只支持传统 pcap，
# 识别出来是为了给出可操作的提示，而不是让用户对着"无效魔数"发呆。
_PCAPNG_MAGIC = 0x0A0D0D0A

# 单个数据包记录的长度上限（256KB）。损坏或被构造的 pcap 里 incl_len 是
# 任意 32 位值，不校验会直接 f.read(incl_len) 申请巨量内存 → 卡死/OOM。
_MAX_PACKET_LEN = 262144


class TcpSegment(NamedTuple):
    """一个 TCP 段（供流重组使用）"""
    src_ip: bytes
    dst_ip: bytes
    src_port: int
    dst_port: int
    seq: int
    payload: bytes
    ts_sec: int
    ts_usec: int
    direction: str

    @property
    def flow(self) -> tuple:
        return (self.src_ip, self.src_port, self.dst_ip, self.dst_port)


def _fmt_timestamp(ts_sec: int, ts_usec: int) -> str:
    """epoch → HH:MM:SS.mmm（**本地时间**）

    帧间隔分析按 HH:MM:SS 解析，故只输出时分秒。直接用 ts_sec % 86400 得到的是
    UTC 时分秒，与界面/设备时标（本地时间）相差一个时区。
    """
    try:
        local = datetime.fromtimestamp(ts_sec)
    except (OverflowError, OSError, ValueError):
        return "00:00:00.000"
    return (f"{local.hour:02d}:{local.minute:02d}:{local.second:02d}."
            f"{(ts_usec % 1000000) // 1000:03d}")


def _iter_packets(fp):
    """遍历 pcap 数据包，产出 (ts_sec, ts_usec, pkt_data, link_type)"""
    global_header = fp.read(24)
    if len(global_header) < 24:
        raise ValueError("PCAP 文件头不完整")

    magic = struct.unpack('<I', global_header[0:4])[0]
    if magic == 0xa1b2c3d4:
        big_endian, nanosecond = False, False
    elif magic == 0xd4c3b2a1:
        big_endian, nanosecond = True, False
    elif magic == 0xa1b23c4d:
        big_endian, nanosecond = False, True
    elif magic == 0x4d3cb2a1:
        big_endian, nanosecond = True, True
    elif magic == _PCAPNG_MAGIC:
        raise ValueError(
            "这是 pcapng 格式（Wireshark 新版默认保存格式），本工具暂不支持。\n"
            "请在 Wireshark 中选择「文件 → 另存为」，类型选 "
            "「Wireshark/tcpdump/... - pcap (*.pcap)」后重新导入。")
    else:
        raise ValueError(
            f"无效的 PCAP 魔数: 0x{magic:08X}（不是 pcap 文件，或已损坏）")

    link_type = struct.unpack('>I' if big_endian else '<I',
                              global_header[20:24])[0]

    packet_no = 0
    while True:
        pkt_header = fp.read(16)
        if len(pkt_header) < 16:
            break
        packet_no += 1

        if big_endian:
            ts_sec, ts_usec, incl_len, _orig_len = struct.unpack('>IIII', pkt_header)
        else:
            ts_sec, ts_usec, incl_len, _orig_len = struct.unpack('<IIII', pkt_header)

        if nanosecond:
            ts_usec = ts_usec // 1000

        # 长度合法性：0 或超过上限都说明文件损坏，继续读只会读到错位数据
        # 或一次性申请巨量内存
        if incl_len == 0 or incl_len > _MAX_PACKET_LEN:
            raise ValueError(
                f"PCAP 文件已损坏：第 {packet_no} 个数据包的记录长度为 "
                f"{incl_len} 字节（合法范围 1~{_MAX_PACKET_LEN}）。")

        pkt_data = fp.read(incl_len)
        if len(pkt_data) < incl_len:
            break

        yield ts_sec, ts_usec, pkt_data, link_type


def read_pcap(file_path: str) -> List[Tuple[bytes, str, str]]:
    """读取 PCAP 文件，返回**逐包**的 [(payload, timestamp_str, direction), ...]

    direction: 'RX' 或 'TX' (基于 TCP 端口判断)
    注意：这里不做流重组，需要"能直接解析的完整报文"请用 read_pcap_frames()。
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    results = []
    with open(file_path, 'rb') as f:
        for ts_sec, ts_usec, pkt_data, link_type in _iter_packets(f):
            payload, direction = _extract_tcp_payload(pkt_data, link_type)
            if payload:
                results.append((payload, _fmt_timestamp(ts_sec, ts_usec),
                                direction))
    return results


# Ethernet 帧类型
_ETH_TYPE_IPV4 = 0x0800
_ETH_TYPE_IPV6 = 0x86DD
# VLAN 标签帧类型（单/双/多级，0x88a8 为运营商骨干桥接）
_VLAN_ETH_TYPES = (0x8100, 0x88a8, 0x9100)
# IPv6 扩展头（next header 值），4/5/6 例外：
#  0=逐跳选项 43=路由 44=分片 60=目的选项 51=AH(可跳过, 不处理)
_IPV6_EXT_HEADERS = {0, 43, 44, 60}


def _extract_tcp_segment(pkt_data: bytes, link_type: int) -> Optional[TcpSegment]:
    """从数据包中提取 TCP 段（含序号与四元组，供流重组）"""
    base = _extract_tcp_payload_ex(pkt_data, link_type)
    if base is None:
        return None
    src_ip, dst_ip, sport, dport, seq, payload, direction = base
    return TcpSegment(src_ip, dst_ip, sport, dport, seq, payload,
                      0, 0, direction)


def _extract_tcp_payload(pkt_data: bytes,
                         link_type: int) -> Tuple[Optional[bytes], str]:
    """从数据包中提取 TCP 负载，支持 IPv4 / IPv6 与多级 VLAN。

    link_type: 1=Ethernet, 101=RAW IP（其余链路类型不支持，返回 None）
    """
    got = _extract_tcp_payload_ex(pkt_data, link_type)
    if got is None:
        return None, "RX"
    return got[5], got[6]


def _extract_tcp_payload_ex(pkt_data: bytes, link_type: int):
    """(src_ip, dst_ip, sport, dport, seq, payload, direction) 或 None"""
    direction = "RX"
    offset = 0
    src_ip = dst_ip = b""

    # ---------- 链路层 ----------
    if link_type == 1:
        if len(pkt_data) < 14:
            return None
        eth_type = struct.unpack('>H', pkt_data[12:14])[0]
        offset = 14
        # 逐层剥离 VLAN 标签（支持 QinQ 等多级）
        while eth_type in _VLAN_ETH_TYPES:
            if len(pkt_data) < offset + 4:
                return None
            eth_type = struct.unpack('>H', pkt_data[offset + 2:offset + 4])[0]
            offset += 4
    elif link_type == 101:
        # RAW IP：首字节高 4 位即 IP 版本
        if len(pkt_data) < 1:
            return None
        ver = pkt_data[0] >> 4
        eth_type = _ETH_TYPE_IPV4 if ver == 4 else _ETH_TYPE_IPV6 if ver == 6 else 0
    else:
        return None

    # ---------- 网络层 ----------
    if eth_type == _ETH_TYPE_IPV4:
        if len(pkt_data) < offset + 20:
            return None
        version_ihl = pkt_data[offset]
        if version_ihl >> 4 != 4:
            return None
        ihl = (version_ihl & 0x0F) * 4
        if ihl < 20:
            return None      # 畸形 IHL=0 会把 IP 头当 TCP 头解析
        protocol = pkt_data[offset + 9]
        total_length = struct.unpack('>H', pkt_data[offset + 2:offset + 4])[0]
        # 分片：MF(bit13) 置位或片偏移非 0 都说明本包不是完整数据报
        # （此前只看偏移量，首片 MF=1 会被当作完整 TCP 段上交，负载被截断）
        frag_field = struct.unpack('>H', pkt_data[offset + 6:offset + 8])[0]
        if frag_field & 0x3FFF:
            return None
        if protocol != 6:  # 非 TCP
            return None
        if total_length < ihl:
            return None
        src_ip = pkt_data[offset + 12:offset + 16]
        dst_ip = pkt_data[offset + 16:offset + 20]
        ip_end = offset + total_length
        tcp_start = offset + ihl
    elif eth_type == _ETH_TYPE_IPV6:
        if len(pkt_data) < offset + 40:
            return None
        if pkt_data[offset] >> 4 != 6:
            return None
        payload_len = struct.unpack('>H', pkt_data[offset + 4:offset + 6])[0]
        next_header = pkt_data[offset + 6]
        src_ip = pkt_data[offset + 8:offset + 24]
        dst_ip = pkt_data[offset + 24:offset + 40]
        ip_end = offset + 40 + payload_len
        tcp_start = offset + 40
        # 跳过 IPv6 扩展头直到 TCP（next header=6）
        while next_header != 6:
            if next_header not in _IPV6_EXT_HEADERS:
                return None
            if len(pkt_data) < tcp_start + 8:
                return None
            if next_header == 44:  # 分片头：非首片 / M=1 均无法当完整段处理
                frag_off = struct.unpack('>H', pkt_data[tcp_start + 2:tcp_start + 4])[0]
                if frag_off & 0xFFF9:      # M(bit0) | 片偏移(bit3-15)
                    return None
                ext_len = 8
            else:  # 逐跳/路由/目的选项：长度 = (len+1)*8
                ext_len = (pkt_data[tcp_start + 1] + 1) * 8
            next_header = pkt_data[tcp_start]
            tcp_start += ext_len
    else:
        return None

    # ---------- 传输层 ----------
    if len(pkt_data) < tcp_start + 20:
        return None
    tcp_header = pkt_data[tcp_start:tcp_start + 20]
    src_port = struct.unpack('>H', tcp_header[0:2])[0]
    dst_port = struct.unpack('>H', tcp_header[2:4])[0]
    seq = struct.unpack('>I', tcp_header[4:8])[0]
    tcp_offset = ((tcp_header[12] >> 4) & 0x0F) * 4
    if tcp_offset < 20:
        return None

    data_start = tcp_start + tcp_offset
    # snaplen 截断的包（incl_len < orig_len）里 IP 声明的总长会大于实际字节数：
    # 此前整包丢弃，用户只看到"导入结果为空"。改为按实际可用长度截取。
    ip_end = min(ip_end, len(pkt_data))
    if data_start > ip_end:
        return None
    payload = pkt_data[data_start:ip_end]

    if dst_port == 2404:
        direction = "TX"
    elif src_port == 2404:
        direction = "RX"
    else:
        direction = "TX"

    return src_ip, dst_ip, src_port, dst_port, seq, payload, direction


# ---------------------------------------------------------------------------
# TCP 流重组 + 按 104 长度域切帧
# ---------------------------------------------------------------------------

_SEQ_MOD = 1 << 32
_SEQ_HALF = 1 << 31


def _reassemble_flow(segments: List[TcpSegment]) -> bytes:
    """把同一方向的 TCP 段按序号拼成连续字节流（重传去重、乱序归位）"""
    if not segments:
        return b""
    # 以最早发送的段为基准：seq 最小的段（按 32 位有符号差比较，兼容回绕）
    base_seg = segments[0]
    base = base_seg.seq
    for seg in segments[1:]:
        delta = (seg.seq - base) & (_SEQ_MOD - 1)
        if delta > _SEQ_HALF:          # 比基准更早
            base = seg.seq

    end = 0
    placed = []
    for seg in segments:
        off = (seg.seq - base) & (_SEQ_MOD - 1)
        if off > _SEQ_HALF:            # 落在基准之前的异常段，丢弃
            continue
        placed.append((off, seg.payload))
        end = max(end, off + len(seg.payload))

    buf = bytearray(end)
    for off, payload in sorted(placed):     # 重叠部分后到的覆盖（重传内容相同）
        buf[off:off + len(payload)] = payload
    return bytes(buf)


def read_pcap_frames(file_path: str) -> List[Tuple[bytes, str, str]]:
    """读取 PCAP → **重组流 + 按 104 长度域切帧**，返回 [(frame_bytes, ts, dir)]

    逐包 path/payload 只作为切帧的原料：一条 104 报文跨多个 TCP 段时，逐包解析
    会得到残缺帧；这里先把同一方向（同四元组）的字节流拼回去，再按
    "68 + 长度域" 切出完整报文。
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    # 收集段（只保留 2404 端口或含 104 起始字节的流，避免把无关连接也算进来）
    flows: dict = {}
    order: List[tuple] = []
    with open(file_path, 'rb') as f:
        for ts_sec, ts_usec, pkt_data, link_type in _iter_packets(f):
            seg = _extract_tcp_segment(pkt_data, link_type)
            if seg is None or not seg.payload:
                continue
            if 2404 not in (seg.src_port, seg.dst_port) and seg.payload[0] != 0x68:
                continue
            seg = seg._replace(ts_sec=ts_sec, ts_usec=ts_usec)
            if seg.flow not in flows:
                flows[seg.flow] = []
                order.append(seg.flow)
            flows[seg.flow].append(seg)

    results: List[Tuple[bytes, str, str]] = []
    for flow_key in order:
        segs = flows[flow_key]
        stream = _reassemble_flow(segs)
        direction = segs[0].direction
        # 记录每个字节的时间戳：同一段内的字节用该段的时间
        ts_map = [None] * len(stream)
        base = segs[0].seq
        for seg in segs:
            off = (seg.seq - base) & (_SEQ_MOD - 1)
            if off > _SEQ_HALF:
                continue
            for i in range(off, min(off + len(seg.payload), len(ts_map))):
                ts_map[i] = (seg.ts_sec, seg.ts_usec)

        for start, frame in _split_104_frames(stream):
            ts_sec, ts_usec = ts_map[start] or (segs[0].ts_sec, segs[0].ts_usec)
            results.append((frame, _fmt_timestamp(ts_sec, ts_usec), direction))

    # 多路连接混抓时按时间排序（字符串 HH:MM:SS.mmm 可直接比较）
    results.sort(key=lambda item: item[1])
    return results


def _split_104_frames(stream: bytes):
    """从字节流里按 "68 + 长度域" 切出完整 APDU，产出 (起始偏移, 帧字节)"""
    i = 0
    n = len(stream)
    while i + 2 <= n:
        if stream[i] != 0x68:
            i += 1
            continue
        apdu_len = stream[i + 1]
        if not (4 <= apdu_len <= 253):
            i += 1
            continue
        end = i + 2 + apdu_len
        if end > n:
            break            # 流尾不完整：剩下的可能被 snaplen 截断
        yield i, stream[i:end]
        i = end


def pcap_to_hex_text(file_path: str) -> str:
    """将 PCAP 文件转换为十六进制文本格式（重组 + 切帧后逐条输出）"""
    frames = read_pcap_frames(file_path)
    lines = []
    for payload, timestamp, direction in frames:
        hex_str = payload.hex().upper()
        hex_formatted = ' '.join(hex_str[i:i+2]
                                 for i in range(0, len(hex_str), 2))
        lines.append(f"{timestamp} {direction}:")
        lines.append(hex_formatted)
        lines.append("")
    return '\n'.join(lines)
