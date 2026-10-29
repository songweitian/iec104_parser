"""后台解析工作线程（从 gui/main_window.py 拆分）。

把不依赖 MainWindow 的“非 UI 逻辑”独立成模块，便于：
- 缩小主窗口文件的维护面（E8 拆分）；
- 让解析逻辑可直接被单元测试（E10），无需拉起整个 GUI。
导出名与拆分前一致（main_window 以 `from gui.parse_worker import ...` 重新导出），
因此 `from gui.main_window import ParseWorker` 等既有引用不受影响。
"""
import logging
import re

from PyQt5.QtCore import QThread, pyqtSignal

from iec104_parser import (
    IEC104Parser, ParseMode,
    is_log_format, split_concatenated_frames, parse_log_format_frames,
    clean_hex_text,
)

logger = logging.getLogger(__name__)


def parse_text_sync(raw_text: str, parse_mode: ParseMode = ParseMode.NORMAL) -> list:
    """同步解析文本，返回帧列表。

    供对比等不需要后台线程的场景复用——与主界面解析走同一条 ParseWorker
    路径（此前对比对话框走 IEC104Parser.parse_from_text，是第二套并行实现，
    解析修复需要改两处才能同步）。

    注意：run() 内异常走 parse_error 信号。此前该信号无人连接，调用方
    （对比对话框）拿到空列表却无从判断"确实无报文"还是"解析炸了"。
    """
    worker = ParseWorker(None, raw_text, parse_mode)
    try:
        return worker.parse()
    except Exception as e:      # 同步场景没有信号通道，这里兜住并记录
        logger.warning("同步解析失败: %s", e)
        return []


class ParseWorker(QThread):
    """后台解析工作线程 - 避免UI阻塞"""
    progress_updated = pyqtSignal(int, int)  # 当前进度, 总数
    parse_finished = pyqtSignal(list)         # 解析完成的帧列表
    parse_error = pyqtSignal(str)             # 错误信息

    def __init__(self, parser: IEC104Parser, raw_text: str, parse_mode: ParseMode,
                 max_frames: int = 0):
        super().__init__()
        self.parser = parser
        self.raw_text = raw_text
        self.parse_mode = parse_mode
        self._is_cancelled = False
        # 帧数上限：0 表示不限（未激活时传 1，每次仅解析第 1 条）
        self.max_frames = max_frames
        # 因达到帧数上限而提前停止（供主线程读取后提示用户）
        self.hit_frame_limit = False
        # 帧 → 输入行号映射。必须在 __init__ 就存在并在所有提前返回路径上
        # 赋值：此前只在 run() 末尾赋值，帧数上限/取消两条 early-return
        # 会让主线程拿不到映射，未激活场景下"输入行"列与 F2 定位恒失效。
        self._frame_input_lines = []
        # 被清洗掉的可疑字符（"第N行: x"）。此前这些字符被静默丢弃，
        # 用户只看到"解析出来的报文不对"，无从判断是输入抄错还是格式问题。
        self.hex_warnings = []

    def cancel(self):
        """请求取消解析"""
        self._is_cancelled = True

    def run(self):
        """在后台线程中执行解析（只负责发信号，解析逻辑见 parse()）"""
        try:
            frames = self.parse(progress_cb=self.progress_updated.emit,
                                should_cancel=lambda: self._is_cancelled)
            self.parse_finished.emit(frames)
        except Exception as e:
            import traceback
            self.parse_error.emit(f"解析异常: {str(e)}\n{traceback.format_exc()}")

    def parse(self, progress_cb=None, should_cancel=None) -> list:
        """同步解析，返回帧列表（不依赖线程与信号，异常向调用方抛出）

        后台线程、对比对话框、单元测试共用这一条解析路径——此前同步场景
        直接调用未 start() 的 run()，语义上是"把线程对象当函数用"。
        """
        cancel = should_cancel if should_cancel is not None \
            else (lambda: self._is_cancelled)

        def notify(cur: int, tot: int):
            if progress_cb is not None:
                progress_cb(cur, tot)

        # 创建独立的解析器副本，避免与界面线程互相干扰
        worker_parser = IEC104Parser(self.parse_mode)
        worker_parser.set_master_send_first(IEC104Parser.DEFAULT_MASTER_FIRST)

        frames = []
        if self.parse_mode == ParseMode.NORMAL:
            # 普通模式：按行解析
            (hex_lines, line_map,
             timestamps, directions) = self._extract_hex_lines_ex(self.raw_text)
            total = len(hex_lines)
            self._frame_input_lines = line_map

            if total == 0:
                return frames

            worker_parser.frames = []
            worker_parser.last_master_send = worker_parser.master_send_first

            for i, hex_line in enumerate(hex_lines):
                if cancel():
                    return frames

                hex_line = hex_line.strip()
                if not hex_line:
                    continue

                ts = timestamps[i] if i < len(timestamps) else ""
                direction = directions[i] if i < len(directions) else ""
                if ts or direction:
                    # 日志里带时间和收发标记：方向/时间以日志为准，
                    # 不再依赖解析器的交替猜测
                    frame = self._parse_frame_with_metadata(
                        worker_parser, hex_line, ts, direction, len(frames) + 1)
                else:
                    frame = self._parse_single_frame(
                        worker_parser, hex_line, len(frames) + 1)
                frames.append(frame)

                # 未激活帧数上限：解析满即停
                if self.max_frames and len(frames) >= self.max_frames:
                    self.hit_frame_limit = True
                    return frames

                # 每解析100帧更新一次进度
                if (i + 1) % 100 == 0 or i + 1 == total:
                    notify(i + 1, total)

            # 保存输入行映射
            self._frame_input_lines = line_map
        else:
            # XN3000/XN8000模式
            blocks, block_line_map = self._extract_xn_blocks(self.raw_text, self.parse_mode)
            if not blocks:
                # 没有识别到任何收发标记行（例如用户手选/误判成日志模式的
                # 纯十六进制或带点 dump）：回退按普通十六进制提取，
                # 否则会出现"点了解析没反应"（0 帧且无任何提示）。
                blocks, block_line_map = self._fallback_plain_blocks(self.raw_text)
            total = len(blocks)

            if total == 0:
                return frames

            worker_parser.frames = []
            worker_parser.last_master_send = worker_parser.master_send_first
            xn_line_map = []
            # 同一个 list 对象边增边用：提前返回时主线程也能拿到部分映射
            self._frame_input_lines = xn_line_map

            for i, (hex_text, timestamp, direction) in enumerate(blocks):
                if cancel():
                    return frames

                src_lines = block_line_map[i] if i < len(block_line_map) else []
                split_frames = split_concatenated_frames(hex_text)
                for frame_hex in split_frames:
                    if cancel():
                        return frames
                    frame = self._parse_frame_with_metadata(
                        worker_parser, frame_hex, timestamp, direction, len(frames) + 1
                    )
                    frames.append(frame)
                    xn_line_map.append(list(src_lines))

                    # 未激活帧数上限：解析满即停
                    if self.max_frames and len(frames) >= self.max_frames:
                        self.hit_frame_limit = True
                        return frames

                if (i + 1) % 100 == 0 or i + 1 == total:
                    notify(i + 1, total)

            # 保存输入行映射
            self._frame_input_lines = xn_line_map

        return frames

    def _fallback_plain_blocks(self, raw_text: str):
        """日志模式下的兜底：按普通十六进制逐行提取，返回 (blocks, line_map)"""
        hex_lines, line_map, stamps, dirs = self._extract_hex_lines_ex(raw_text)
        blocks = [(h, stamps[i] if i < len(stamps) else "",
                   dirs[i] if i < len(dirs) else "")
                  for i, h in enumerate(hex_lines)]
        return blocks, line_map

    def _extract_hex_lines(self, raw_text: str):
        """提取十六进制行（简化版）"""
        hex_lines, line_map, _, _ = self._extract_hex_lines_ex(raw_text)
        return hex_lines, line_map

    def _extract_hex_lines_ex(self, raw_text: str):
        """提取十六进制行 + 每帧的时间戳/方向

        返回 (hex_lines, line_map, timestamps, directions)：
        日志格式里每行的 "13:25:00.692 RX: ..." 前缀现在会随帧一起保留，
        否则时间列恒为 "-"、方向只能靠交替猜测（S/U 帧常标反）。
        """
        if is_log_format(raw_text):
            return parse_log_format_frames(raw_text)

        cleaned = ''.join(c for c in raw_text if c in '0123456789ABCDEFabcdef')
        if cleaned and len(cleaned) % 2 == 0 and cleaned[:2].lower() == '68':
            try:
                raw_bytes = bytes.fromhex(cleaned)
                if len(raw_bytes) >= 6 and raw_bytes[0] == 0x68:
                    apdu_len = raw_bytes[1]
                    if apdu_len + 2 == len(raw_bytes):
                        all_lines = list(range(len(raw_text.strip().split('\n'))))
                        return [cleaned], [all_lines], [""], [""]
            except ValueError:
                pass

        all_lines = raw_text.strip().split('\n')
        hex_lines = []
        line_map = []
        for i, line in enumerate(all_lines):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            _cleaned, dropped = clean_hex_text(line)
            if dropped:
                chars = "".join(c for _, c in dropped)
                self.hex_warnings.append(f"第 {i + 1} 行: 忽略 {chars!r}")
            # 单行多帧（无分隔拼接，如 680401000e00680402000a00）：拆成单帧序列，
            # 否则 _parse_single_frame 只取第一个 APDU，静默丢弃其余帧。
            # 仅在确实拆出多帧时替换原行，单帧行保留原始空格排版。
            split = split_concatenated_frames(line)
            if len(split) > 1:
                for frame_hex in split:
                    hex_lines.append(frame_hex)
                    line_map.append([i])
            else:
                hex_lines.append(line)
                line_map.append([i])
        return hex_lines, line_map, [""] * len(hex_lines), [""] * len(hex_lines)

    def _extract_xn_blocks(self, text: str, parse_mode: ParseMode):
        """提取XN格式的数据块，返回 (blocks, line_map)"""
        lines = text.strip().split('\n')
        blocks = []
        line_map = []
        current_timestamp = ""
        current_direction = ""
        current_hex_parts = []
        current_line_nums = []

        for line_idx, line in enumerate(lines):
            line = line.strip()

            # 时间戳/方向标记行，冒号后可带同行十六进制（如 "13:25:00.692 RX: 68 04 ..."）。
            # 此前要求标记独占一行，同行格式的日志整块被静默丢弃（0 帧解析）。
            if parse_mode == ParseMode.XN3000:
                ts_match = re.match(r'^(\d{2}:\d{2}:\d{2}\.\d+)\s+(RX|TX):\s*(.*)$', line)
            else:
                ts_match = re.match(r'^([\d\- :\.]+)\s+(send|recv):\s*(.*)$', line)

            if ts_match:
                if current_hex_parts:
                    blocks.append((''.join(current_hex_parts), current_timestamp, current_direction))
                    line_map.append(list(current_line_nums))
                    current_hex_parts = []
                    current_line_nums = []
                current_timestamp = ts_match.group(1)
                current_direction = ts_match.group(2)
                same_line = ts_match.group(3).strip()
                if same_line:
                    cleaned = same_line.replace('.', '').strip()
                    if cleaned:
                        current_hex_parts.append(cleaned)
                        current_line_nums.append(line_idx)
                continue

            if current_timestamp:
                cleaned = line.replace('.', '').strip()
                if cleaned:
                    current_hex_parts.append(cleaned)
                    current_line_nums.append(line_idx)

        if current_hex_parts:
            blocks.append((''.join(current_hex_parts), current_timestamp, current_direction))
            line_map.append(list(current_line_nums))

        return blocks, line_map

    def _parse_single_frame(self, parser, hex_string: str, index: int):
        """解析单个帧

        直接委托 parser.parse_hex——此前这里是 parse_hex 的手工副本，缺少
        CP56→CP24 小时学习（_last_known_hour）更新，且不完整帧的处理行为
        与 parse_hex 不一致；两套逻辑并存导致修复需要改两处（审查遗留）。
        """
        return parser.parse_hex(hex_string)

    def _parse_frame_with_metadata(self, parser, hex_string: str, timestamp: str, direction: str, index: int):
        """解析帧并附加元数据"""
        frame = self._parse_single_frame(parser, hex_string, index)
        frame.original_timestamp = timestamp
        frame.original_direction = direction

        if direction.upper() in ('TX', 'SEND'):
            frame.send_role = "主站"
            frame.recv_role = "子站"
            frame.direction = "主站→子站"
            parser.last_master_send = True
        elif direction.upper() in ('RX', 'RECV'):
            frame.send_role = "子站"
            frame.recv_role = "主站"
            frame.direction = "子站→主站"
            parser.last_master_send = False

        return frame