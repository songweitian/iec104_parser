"""pcap_importer 健壮性测试：pcapng 识别、损坏文件长度上限、基本解析"""
import os
import struct
import tempfile

import pcap_importer as P


def _write(raw: bytes) -> str:
    path = os.path.join(tempfile.mkdtemp(), "cap.pcap")
    with open(path, "wb") as f:
        f.write(raw)
    return path


def _pcap_header(link_type=1) -> bytes:
    return struct.pack("<IHHiIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, link_type)


def _pcap_record(incl_len: int, data: bytes) -> bytes:
    return struct.pack("<IIII", 0, 0, incl_len, incl_len) + data


def test_pcapng_rejected_with_actionable_message():
    """Wireshark 新版默认 pcapng 格式应给出可操作的提示，而不是‘无效魔数’"""
    path = _write(b"\x0a\x0d\x0d\x0a" + b"\x00" * 20)
    try:
        P.read_pcap(path)
        raise AssertionError("expected ValueError for pcapng")
    except ValueError as e:
        assert "pcapng" in str(e)
        assert "pcap" in str(e)  # 提示另存为 pcap


def test_oversized_incl_len_rejected():
    """记录长度超过上限（文件损坏/构造）必须显式报错，不能 read(巨量) 卡死"""
    path = _write(_pcap_header() + _pcap_record(999999, b""))
    try:
        P.read_pcap(path)
        raise AssertionError("expected ValueError for oversized incl_len")
    except ValueError as e:
        assert "损坏" in str(e)


def test_zero_incl_len_rejected():
    path = _write(_pcap_header() + _pcap_record(0, b""))
    try:
        P.read_pcap(path)
        raise AssertionError("expected ValueError for zero incl_len")
    except ValueError as e:
        assert "损坏" in str(e)


def test_truncated_packet_breaks_cleanly():
    """最后一包数据不足 incl_len：应安全中断而非抛错"""
    rec = _pcap_record(100, b"\x68\x04")  # 声明 100 字节，只给 2
    assert P.read_pcap(_write(_pcap_header() + rec)) == []


def test_empty_pcap_ok():
    assert P.read_pcap(_write(_pcap_header())) == []


# ---------------------------------------------------------------------------
# 分层构造（Ethernet + IPv4/IPv6 + TCP），用于分片/截断/时间戳回归
# ---------------------------------------------------------------------------

def _tcp(payload: bytes, src_port=2404, dst_port=50000) -> bytes:
    return (struct.pack(">HHIIBBHHH", src_port, dst_port, 1, 0,
                        5 << 4, 0x18, 8192, 0, 0) + payload)


def _ipv4(tcp: bytes, frag: int = 0, total_len=None) -> bytes:
    tl = total_len if total_len is not None else 20 + len(tcp)
    return struct.pack(">BBHHHBBH4s4s", 0x45, 0, tl, 1, frag, 64, 6, 0,
                       b"\x01\x02\x03\x04", b"\x05\x06\x07\x08") + tcp


def _eth(ip: bytes, eth_type=0x0800) -> bytes:
    return (b"\x00" * 6 + b"\x11" * 6 + struct.pack(">H", eth_type) + ip)


IEC_PAYLOAD = bytes.fromhex("680407000000")


def test_fragment_with_mf_flag_is_skipped():
    """IPv4 首片（offset=0 但 MF=1）不能当完整 TCP 段上交——否则负载被截断"""
    ip = _ipv4(_tcp(IEC_PAYLOAD), frag=0x2000)
    payload, _ = P._extract_tcp_payload(_eth(ip), 1)
    assert payload is None


def test_snaplen_truncated_packet_keeps_payload():
    """snaplen 截断（IP 总长 > 实际字节）时应保留已有负载，而不是整包丢弃"""
    ip = _ipv4(_tcp(IEC_PAYLOAD), total_len=20 + 20 + len(IEC_PAYLOAD) + 100)
    payload, direction = P._extract_tcp_payload(_eth(ip), 1)
    assert payload == IEC_PAYLOAD
    assert direction == "RX"


def test_invalid_ihl_rejected():
    """IHL < 5 视为畸形，不得把 IP 头当 TCP 头解析"""
    ip = _ipv4(_tcp(IEC_PAYLOAD))
    ip = bytes([0x40 | 0]) + ip[1:]      # ihl=0
    assert P._extract_tcp_payload(_eth(ip), 1)[0] is None


# ---------------------------------------------------------------------------
# TCP 流重组（真实抓包里一条 104 报文可能跨多个 TCP 段，也可能重传/乱序）
# ---------------------------------------------------------------------------

def _tcp_seq(payload: bytes, seq: int, src_port=2404, dst_port=50000) -> bytes:
    return (struct.pack(">HHIIBBHHH", src_port, dst_port, seq, 0,
                        5 << 4, 0x18, 8192, 0, 0) + payload)


def _record(payload: bytes, seq: int, ts_sec=1_700_000_000, ts_usec=0) -> bytes:
    pkt = _eth(_ipv4(_tcp_seq(payload, seq)))
    return (struct.pack("<IIII", ts_sec, ts_usec, len(pkt), len(pkt)) + pkt)


# 16 字节 I 帧：68 0E | ctrl | 01 01 03 00 01 00 01 00 00 40
ONE_FRAME = bytes.fromhex("680E0000000001010300010001000040")


def test_reassembles_frame_split_across_segments():
    """一条 104 报文拆成两个 TCP 段：逐包看都是残帧，重组后必须是完整帧"""
    frame = ONE_FRAME
    part1, part2 = frame[:5], frame[5:]
    raw = (_pcap_header() + _record(part1, seq=1000)
           + _record(part2, seq=1000 + len(part1)))
    frames = P.read_pcap_frames(_write(raw))
    assert len(frames) == 1
    assert frames[0][0] == frame

    # 逐包接口仍是"各自一段"：证明重组确实起了作用
    assert len(P.read_pcap(_write(raw))) == 2


def test_reassembly_handles_retransmit_and_reorder():
    """重传（重复段）+ 乱序：结果仍是一条完整帧，且不重复"""
    frame = ONE_FRAME
    part1, part2 = frame[:6], frame[6:]
    raw = (_pcap_header()
           + _record(part2, seq=1000 + len(part1))      # 后段先到
           + _record(part1, seq=1000)                   # 前段后到
           + _record(part1, seq=1000))                  # 重传
    frames = P.read_pcap_frames(_write(raw))
    assert len(frames) == 1
    assert frames[0][0] == frame


def test_multiple_frames_in_one_segment_are_split():
    """一个 TCP 段里塞了 3 条报文：必须切出 3 条，而不是当 1 条"""
    frames = [bytes.fromhex("68040B000000"),
              bytes.fromhex("680407000000"),
              bytes.fromhex("680443000000")]
    stream = b"".join(frames)
    raw = _pcap_header() + _record(stream, seq=1)
    got = P.read_pcap_frames(_write(raw))
    assert [g[0] for g in got] == frames


def test_reassembly_falls_back_on_truncated_tail():
    """流尾被 snaplen 截断：已完整的帧照常输出，残帧不抛异常"""
    frame = ONE_FRAME
    raw = _pcap_header() + _record(frame + b"\x68\x0e\x00", seq=1)
    got = P.read_pcap_frames(_write(raw))
    assert len(got) == 1 and got[0][0] == frame


def test_timestamp_uses_local_time():
    """时间戳必须按本地时间折算（此前用 ts_sec % 86400，差一个时区）"""
    import datetime as dt
    ts_sec = 1_700_000_000
    path = _write(_pcap_header()
                  + struct.pack("<IIII", ts_sec, 250_000, len(_eth(_ipv4(_tcp(IEC_PAYLOAD)))),
                                len(_eth(_ipv4(_tcp(IEC_PAYLOAD)))))
                  + _eth(_ipv4(_tcp(IEC_PAYLOAD))))
    payload, timestamp, _ = P.read_pcap(path)[0]
    local = dt.datetime.fromtimestamp(ts_sec)
    assert timestamp == f"{local.hour:02d}:{local.minute:02d}:{local.second:02d}.250"
