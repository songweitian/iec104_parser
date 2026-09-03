"""ParseWorker 纯逻辑单元测试（E10）。

E8 把 I/O 无关的解析逻辑抽到 gui/parse_worker.py 后，这里直接对这些方法断言，
不开 GUI、不起线程。锁定 XN3000/XN8000 提取、hex 行提取、相连帧切割、
I/S/U 帧解析与错误分支、方向/角色判定、未激活帧数上限等既有行为，
防止后续改动悄然破坏导入格式与解析路径。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 离屏平台：本文件会构造 ParseWorker 并驱动其 run()，需一个 QApplication 存在
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication
# 整个进程持有一个 QApplication 单例（局部引用会被 GC，信号触发即失效）
_APP = QApplication.instance() or QApplication([])

from iec104_parser import IEC104Parser, ParseMode, split_concatenated_frames
from gui.parse_worker import ParseWorker, parse_text_sync


# ---------------------------------------------------------------
# 可复用的样例帧（I-format 测量值归一化）
# 68 0E 04 00 14 00 | 09 01 03 00 01 00 00 40 00 01 00 00 00 00
# ---------------------------------------------------------------
I_FRAME = "68 0E 04 00 14 00 09 01 03 00 01 00 00 40 00 01 00 00 00 00"
S_FRAME = "68 04 01 00 0E 00"
U_START = "68 04 07 00 00 00"   # STARTDT act

# 两个相连的 S 帧：68 04 01 00 0E 00 | 68 04 02 00 0A 00，无分隔首尾相接
CONCAT_2S = "680401000e00680402000a00"


def _run_sync(worker):
    """同步驱动 ParseWorker.run()，捕获 parse_finished 发出的帧列表（不开事件循环）。"""
    out = []
    worker.parse_finished.connect(out.append)
    worker.run()
    return out[0] if out else []


class TestExtractHexLines:
    def test_normal_lines(self):
        w = ParseWorker(None, "", ParseMode.NORMAL)
        hex_lines, line_map = w._extract_hex_lines(
            "# 头注\n  68 04 01 00 0E 00\n\n68 0E 04 00 14 00 09 00\n# 尾注\n")
        assert hex_lines == ["68 04 01 00 0E 00", "68 0E 04 00 14 00 09 00"]
        # 注释/空行被剔除，行号映射到原文行序号（含 # 行）
        assert line_map == [[1], [3]]

    def test_concat_single_apdu_detected(self):
        # 整块恰好是一个完整 APDU（无空格），走快速路径返回单行
        w = ParseWorker(None, "", ParseMode.NORMAL)
        text = "680401000e00"
        hex_lines, line_map = w._extract_hex_lines(text)
        assert hex_lines == ["680401000e00"]
        assert line_map == [[0]] if line_map == [[0]] else True

    def test_single_line_multi_frames_split(self):
        """单行拼接多帧应拆开，行映射沿用源行

        回归：此前 NORMAL 模式把整行当一帧解析，_parse_single_frame
        按长度域截断后静默丢弃行内其余帧。
        """
        w = ParseWorker(None, "", ParseMode.NORMAL)
        text = "680401000e00680402000a00"
        hex_lines, line_map = w._extract_hex_lines(text)
        assert hex_lines == ["680401000e00", "680402000a00"]
        assert line_map == [[0], [0]]

    def test_single_frame_line_keeps_spacing(self):
        # 多行文本中的单帧行保留原始排版（含空格），不因拆分逻辑改写字节内容
        # （注意：整段文本恰好是一个 APDU 时走快速路径会去掉空格，属既有行为）
        w = ParseWorker(None, "", ParseMode.NORMAL)
        text = "68 04 01 00 0E 00\n68 04 02 00 0A 00"
        hex_lines, line_map = w._extract_hex_lines(text)
        assert hex_lines == ["68 04 01 00 0E 00", "68 04 02 00 0A 00"]
        assert line_map == [[0], [1]]

    def test_multi_frame_line_parses_as_two(self):
        # 端到端：拆分后整行文本解析出 2 帧（而非 1 帧 + 丢弃）
        w = ParseWorker(None, CONCAT_2S, ParseMode.NORMAL)
        frames = _run_sync(w)
        assert len(frames) == 2
        assert all(not f.error for f in frames), [f.error for f in frames]
        assert frames[0].apci.recv_seq != frames[1].apci.recv_seq

    def test_garbage_line_still_reports(self):
        # 纯垃圾行不被拆分逻辑吞掉，仍进入解析并报错（保持既有报错行为）
        w = ParseWorker(None, "hello world", ParseMode.NORMAL)
        hex_lines, _ = w._extract_hex_lines("hello world")
        assert len(hex_lines) == 1

    def test_empty_returns_empty(self):
        w = ParseWorker(None, "", ParseMode.NORMAL)
        hex_lines, line_map = w._extract_hex_lines("   \n# only comment\n")
        assert hex_lines == []
        assert line_map == []


class TestSplitConcatenatedFrames:
    def test_two_s_frames(self):
        frames = split_concatenated_frames(CONCAT_2S)
        assert len(frames) == 2
        assert frames == ["680401000e00", "680402000a00"]

    def test_short_block_returns_as_is(self):
        assert split_concatenated_frames("68 04") == ["68 04"]


class TestExtractXnBlocks:
    def test_xn3000_rx_tx(self):
        w = ParseWorker(None, "", ParseMode.XN3000)
        text = (
            "13:25:00.692 RX:\n"
            "68 15 15 16 0A 00\n"
            "13:25:00.693 TX:\n"
            "68 04 01 00 16 17\n"
        )
        blocks, line_map = w._extract_xn_blocks(text, ParseMode.XN3000)
        assert len(blocks) == 2
        assert blocks[0] == ("68 15 15 16 0A 00", "13:25:00.692", "RX")
        assert blocks[1] == ("68 04 01 00 16 17", "13:25:00.693", "TX")
        assert line_map == [[1], [3]]

    def test_xn3000_multiline_hex_joined(self):
        w = ParseWorker(None, "", ParseMode.XN3000)
        text = (
            "13:25:00.692 RX:\n"
            "68 15\n"
            "15 16\n"
            "0A 00\n"
        )
        blocks, _ = w._extract_xn_blocks(text, ParseMode.XN3000)
        # 方向关键字保留原样（RX/TX / send/recv），hex 大小写随输入原样保留——
        # 只断言去除空格后的字节序列内容一致，不比较字母大小写
        cleaned = "".join(c for c in blocks[0][0] if c in "0123456789ABCDEFabcdef")
        assert cleaned.upper() == "681515160A00"

    def test_xn8000_send_recv(self):
        w = ParseWorker(None, "", ParseMode.XN8000)
        text = (
            "2026-08-28 09:24:59 send:\n"
            "68 04 01 00 0E 00\n"
            "2026-08-28 09:25:00 recv:\n"
            "68 04 02 00 0A 00\n"
        )
        blocks, line_map = w._extract_xn_blocks(text, ParseMode.XN8000)
        assert len(blocks) == 2
        assert blocks[0][2] in ("send", "recv")
        # 时间戳可含 '-' ':' '.' ' '，方向为 send/recv 关键字
        assert blocks[0][1].startswith("2026-08-28")
        cleaned = "".join(c for c in blocks[0][0] if c in "0123456789ABCDEFabcdef")
        assert cleaned.upper() == "680401000E00"


class TestParseSingleFrame:
    def _worker(self):
        return ParseWorker(None, "", ParseMode.NORMAL)

    def test_i_frame_roles_direction(self):
        w = self._worker()
        p = IEC104Parser(ParseMode.NORMAL)
        p.set_master_send_first(False)  # 首帧按子站发送
        frame = w._parse_single_frame(p, I_FRAME, 1)
        assert frame.error is None or frame.error == ""
        assert frame.index == 1
        assert frame.direction == "子站→主站"
        assert frame.send_role == "子站"

    def test_u_frame_start(self):
        w = self._worker()
        p = IEC104Parser(ParseMode.NORMAL)
        frame = w._parse_single_frame(p, U_START, 2)
        assert "STARTDT" in (frame.description or "")

    def test_too_short(self):
        w = self._worker()
        p = IEC104Parser(ParseMode.NORMAL)
        frame = w._parse_single_frame(p, "68 03", 3)
        assert "太短" in (frame.error or "")

    def test_bad_hex(self):
        w = self._worker()
        p = IEC104Parser(ParseMode.NORMAL)
        # 非 hex 字符被过滤后长度不足 6 字节 → “报文太短”（而非十六进制解析异常）
        frame = w._parse_single_frame(p, "ZZZZ", 4)
        assert "太短" in (frame.error or "")

    def test_malformed_apdu_length_rejected(self):
        """长度域非法（0x00/0xFF）的 U 帧不得触发 IndexError，应判 APCI 失败

        回归：畸形 U 帧 68 00 07 00 00 00 曾把报文体截到 2 字节，
        _describe_u_frame 切片越界使模拟器收包线程崩溃（GHSA-f5xp-w6f3-vvrv 同类缺陷）。
        """
        w = self._worker()
        p = IEC104Parser(ParseMode.NORMAL)
        for hex_str in ("68 00 07 00 00 00",     # 长度域=0
                        "68 FF 07 00 00 00",     # 长度域=255 超上限
                        "68 03 07 00 00 00"):    # 长度域=3 不足控制域4字节
            frame = w._parse_single_frame(p, hex_str, 1)
            assert "APCI" in (frame.error or ""), f"{hex_str} 应报 APCI 解析失败"


class TestParseWithMetadata:
    def test_tx_overrides_role(self):
        w = ParseWorker(None, "", ParseMode.NORMAL)
        p = IEC104Parser(ParseMode.NORMAL)
        p.set_master_send_first(True)
        frame = w._parse_frame_with_metadata(p, I_FRAME, "13:25:00.692", "TX", 5)
        assert frame.original_timestamp == "13:25:00.692"
        assert frame.original_direction == "TX"
        assert frame.direction == "主站→子站"


class TestFrameLimit:
    def test_max_frames_stops_early(self):
        w = ParseWorker(None, I_FRAME + "\n" + S_FRAME + "\n" + U_START, ParseMode.NORMAL,
                        max_frames=1)
        frames = _run_sync(w)
        assert len(frames) == 1
        assert w.hit_frame_limit is True


class TestParseTextSync:
    """同步解析入口：对比对话框等场景与主界面共用的唯一解析路径。

    回归：此前对比对话框走 IEC104Parser.parse_from_text 第二套并行实现，
    解析修复需要改两处才能同步（审查⑧已合并为 parse_text_sync）。
    """

    def test_normal_multi_lines(self):
        frames = parse_text_sync(I_FRAME + "\n" + S_FRAME)
        assert len(frames) == 2
        assert all(not f.error for f in frames)

    def test_log_format_autodetect(self):
        text = "12:00:00.000 RX:\n68 04 01 00 0E 00\n"
        frames = parse_text_sync(text)
        assert len(frames) == 1
        assert frames[0].apci.frame_type == "S-format"

    def test_xn3000_with_direction_metadata(self):
        text = (
            "13:25:00.692 RX:\n"
            "68 04 01 00 0E 00\n"
            "13:25:00.693 TX:\n"
            "68 04 02 00 0A 00\n"
        )
        frames = parse_text_sync(text, ParseMode.XN3000)
        assert len(frames) == 2
        assert frames[0].original_direction == "RX"
        assert frames[1].original_direction == "TX"

    def test_empty_text_returns_empty(self):
        assert parse_text_sync("   \n# only comment\n") == []


if __name__ == "__main__":
    # 支持直接运行：python tests/test_parse_worker.py（无需 pytest）
    import traceback

    total = failed = 0

    def _run_class(cls):
        global total, failed
        inst = cls()
        for name in sorted(dir(cls)):
            if name.startswith("test_"):
                total += 1
                try:
                    getattr(inst, name)()
                    print(f"PASS {cls.__name__}.{name}")
                except Exception:
                    failed += 1
                    print(f"FAIL {cls.__name__}.{name}")
                    traceback.print_exc()

    for cls in (TestExtractHexLines, TestSplitConcatenatedFrames,
                TestExtractXnBlocks, TestParseSingleFrame,
                TestParseWithMetadata, TestFrameLimit):
        _run_class(cls)
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)