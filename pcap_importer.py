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
                timestamp = f"{ts_sec // 3600:02d}:{(ts_sec % 3600) // 60:02d}:{ts_sec % 60:02d}.{ts_usec // 1000:03d}"
                results.append((payload, timestamp, direction))

    return results


def _extract_tcp_payload(pkt_data: bytes, link_type: int) -> Tuple[Optional[bytes], str]:
    """从数据包中提取 TCP 负载"""
    offset = 0
    direction = "RX"

    if link_type == 1:
        if len(pkt_data) < 14:
            return None, direction
        eth_type = struct.unpack('>H', pkt_data[12:14])[0]
        offset = 14

        if eth_type == 0x8100:
            if len(pkt_data) < 18:
                return None, direction
            eth_type = struct.unpack('>H', pkt_data[16:18])[0]
            offset = 18

        if eth_type != 0x0800:
            return None, direction
    elif link_type == 101:
        pass
    else:
        return None, direction

    if len(pkt_data) < offset + 20:
        return None, direction

    ip_header = pkt_data[offset:offset + 20]
    version_ihl = ip_header[0]
    ihl = (version_ihl & 0x0F) * 4
    protocol = ip_header[9]
    total_length = struct.unpack('>H', ip_header[2:4])[0]

    if protocol != 6:
        return None, direction

    offset += ihl

    if len(pkt_data) < offset + 20:
        return None, direction

    tcp_header = pkt_data[offset:offset + 20]
    src_port = struct.unpack('>H', tcp_header[0:2])[0]
    dst_port = struct.unpack('>H', tcp_header[2:4])[0]
    tcp_offset = ((tcp_header[12] >> 4) & 0x0F) * 4

    offset += tcp_offset

    payload = pkt_data[offset:offset + total_length - ihl - tcp_offset]

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
