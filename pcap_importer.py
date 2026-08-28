"""
PCAP 文件导入器
支持从 Wireshark .pcap 文件中提取 IEC104 TCP 负载
"""

import struct
import os
from typing import List, Tuple, Optional


def read_pcap(file_path: str) -> List[Tuple[bytes, str, str]]:
    """读取 PCAP 文件，返回 [(payload, timestamp_str, direction), ...]

    direction: 'RX' 或 'TX' (基于 TCP 端口判断)
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    results = []

    with open(file_path, 'rb') as f:
        global_header = f.read(24)
        if len(global_header) < 24:
            raise ValueError("PCAP 文件头不完整")

        magic = struct.unpack('<I', global_header[0:4])[0]
        if magic == 0xa1b2c3d4:
            big_endian = False
            nanosecond = False
        elif magic == 0xd4c3b2a1:
            big_endian = True
            nanosecond = False
        elif magic == 0xa1b23c4d:
            big_endian = False
            nanosecond = True
        elif magic == 0x4d3cb2a1:
            big_endian = True
            nanosecond = True
        else:
            raise ValueError(f"无效的 PCAP 魔数: 0x{magic:08X}")

        link_type = struct.unpack('>I' if big_endian else '<I', global_header[20:24])[0]

        while True:
            pkt_header = f.read(16)
            if len(pkt_header) < 16:
                break

            if big_endian:
                ts_sec, ts_usec, incl_len, orig_len = struct.unpack('>IIII', pkt_header)
            else:
                ts_sec, ts_usec, incl_len, orig_len = struct.unpack('<IIII', pkt_header)

            if nanosecond:
                ts_usec = ts_usec // 1000

            pkt_data = f.read(incl_len)
            if len(pkt_data) < incl_len:
                break

            payload, direction = _extract_tcp_payload(pkt_data, link_type)
            if payload and len(payload) > 0:
                # ts_sec 是自 epoch 的绝对秒数，须先折算到当天再取时分秒，
                # 否则跨日捕获会显示为 480000+ 小时（H2 修复）
                day_sec = ts_sec % 86400
                timestamp = f"{day_sec // 3600:02d}:{(day_sec % 3600) // 60:02d}:{day_sec % 60:02d}.{ts_usec // 1000:03d}"
                results.append((payload, timestamp, direction))

    return results


# Ethernet 帧类型
_ETH_TYPE_IPV4 = 0x0800
_ETH_TYPE_IPV6 = 0x86DD
# VLAN 标签帧类型（单/双/多级，0x88a8 为运营商骨干桥接）
_VLAN_ETH_TYPES = (0x8100, 0x88a8, 0x9100)
# IPv6 扩展头（next header 值），4/5/6 例外：
#  0=逐跳选项 43=路由 44=分片 60=目的选项 51=AH(可跳过, 不处理)
_IPV6_EXT_HEADERS = {0, 43, 44, 60}


def _extract_tcp_payload(pkt_data: bytes, link_type: int) -> Tuple[Optional[bytes], str]:
    """从数据包中提取 TCP 负载，支持 IPv4 / IPv6 与多级 VLAN。

    link_type: 1=Ethernet, 101=RAW IP（其余链路类型不支持，返回 None）
    """
    direction = "RX"
    offset = 0

    # ---------- 链路层 ----------
    if link_type == 1:
        if len(pkt_data) < 14:
            return None, direction
        eth_type = struct.unpack('>H', pkt_data[12:14])[0]
        offset = 14
        # 逐层剥离 VLAN 标签（支持 QinQ 等多级）
        while eth_type in _VLAN_ETH_TYPES:
            if len(pkt_data) < offset + 4:
                return None, direction
            eth_type = struct.unpack('>H', pkt_data[offset + 2:offset + 4])[0]
            offset += 4
    elif link_type == 101:
        # RAW IP：首字节高 4 位即 IP 版本
        if len(pkt_data) < 1:
            return None, direction
        ver = pkt_data[0] >> 4
        eth_type = _ETH_TYPE_IPV4 if ver == 4 else _ETH_TYPE_IPV6 if ver == 6 else 0
    else:
        return None, direction

    # ---------- 网络层 ----------
    if eth_type == _ETH_TYPE_IPV4:
        if len(pkt_data) < offset + 20:
            return None, direction
        version_ihl = pkt_data[offset]
        if version_ihl >> 4 != 4:
            return None, direction
        ihl = (version_ihl & 0x0F) * 4
        protocol = pkt_data[offset + 9]
        total_length = struct.unpack('>H', pkt_data[offset + 2:offset + 4])[0]
        # 分片非首片无法重组，跳过
        frag_field = struct.unpack('>H', pkt_data[offset + 6:offset + 8])[0]
        if frag_field & 0x1FFF:
            return None, direction
        if protocol != 6:  # 非 TCP
            return None, direction
        if total_length < ihl:
            return None, direction
        ip_end = offset + total_length
        tcp_start = offset + ihl
    elif eth_type == _ETH_TYPE_IPV6:
        if len(pkt_data) < offset + 40:
            return None, direction
        if pkt_data[offset] >> 4 != 6:
            return None, direction
        payload_len = struct.unpack('>H', pkt_data[offset + 4:offset + 6])[0]
        next_header = pkt_data[offset + 6]
        ip_end = offset + 40 + payload_len
        tcp_start = offset + 40
        # 跳过 IPv6 扩展头直到 TCP（next header=6）
        while next_header != 6:
            if next_header not in _IPV6_EXT_HEADERS:
                return None, direction
            if len(pkt_data) < tcp_start + 8:
                return None, direction
            if next_header == 44:  # 分片头：非首片无法重组
                frag_off = struct.unpack('>H', pkt_data[tcp_start + 2:tcp_start + 4])[0]
                if frag_off & 0xFFF8:
                    return None, direction
                ext_len = 8
            else:  # 逐跳/路由/目的选项：长度 = (len+1)*8
                ext_len = (pkt_data[tcp_start + 1] + 1) * 8
            next_header = pkt_data[tcp_start]
            tcp_start += ext_len
    else:
        return None, direction

    # ---------- 传输层 ----------
    if len(pkt_data) < tcp_start + 20:
        return None, direction
    tcp_header = pkt_data[tcp_start:tcp_start + 20]
    src_port = struct.unpack('>H', tcp_header[0:2])[0]
    dst_port = struct.unpack('>H', tcp_header[2:4])[0]
    tcp_offset = ((tcp_header[12] >> 4) & 0x0F) * 4
    if tcp_offset < 20:
        return None, direction

    data_start = tcp_start + tcp_offset
    if data_start > ip_end or ip_end > len(pkt_data):
        return None, direction
    payload = pkt_data[data_start:ip_end]

    if dst_port == 2404:
        direction = "TX"
    elif src_port == 2404:
        direction = "RX"
    else:
        direction = "TX"

    return payload, direction


def pcap_to_hex_text(file_path: str) -> str:
    """将 PCAP 文件转换为十六进制文本格式"""
    packets = read_pcap(file_path)
    lines = []

    for payload, timestamp, direction in packets:
        if payload[0:1] == b'\x68':
            hex_str = payload.hex().upper()
            hex_formatted = ' '.join(hex_str[i:i+2] for i in range(0, len(hex_str), 2))
            lines.append(f"{timestamp} {direction}:")
            lines.append(hex_formatted)
            lines.append("")

    return '\n'.join(lines)


def is_pcap_file(file_path: str) -> bool:
    """检查文件是否是 PCAP 格式"""
    try:
        with open(file_path, 'rb') as f:
            magic = f.read(4)
            return magic in (b'\xd4\xc3\xb2\xa1', b'\xa1\xb2\xc3\xd4',
                           b'\x4d\x3c\xb2\xa1', b'\xa1\xb2\x3c\x4d')
    except Exception:
        return False
