"""后台解析工作线程（从 gui/main_window.py 拆分）。

把不依赖 MainWindow 的“非 UI 逻辑”独立成模块，便于：
- 缩小主窗口文件的维护面（E8 拆分）；
- 让解析逻辑可直接被单元测试（E10），无需拉起整个 GUI。
导出名与拆分前一致（main_window 以 `from gui.parse_worker import ...` 重新导出），
因此 `from gui.main_window import ParseWorker` 等既有引用不受影响。
"""
import re

from PyQt5.QtCore import QThread, pyqtSignal

from iec104_parser import (
    IEC104Parser, ParseMode,
    is_log_format, split_concatenated_frames, parse_log_format_blocks,
)


def parse_text_sync(raw_text: str, parse_mode: ParseMode = ParseMode.NORMAL) -> list:
    """同步解析文本，返回帧列表。

    供对比等不需要后台线程的场景复用——与主界面解析走同一条 ParseWorker
    路径（此前对比对话框走 IEC104Parser.parse_from_text，是第二套并行实现，
    解析修复需要改两处才能同步）。
    """
    worker = ParseWorker(None, raw_text, parse_mode)
    out = []
    worker.parse_finished.connect(out.append)
    worker.run()
    return out[0] if out else []


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

    def cancel(self):
        """请求取消解析"""
        self._is_cancelled = True

    def run(self):
        """在后台线程中执行解析"""
        try:
            # 创建独立的解析器副本，避免线程间干扰
            worker_parser = IEC104Parser(self.parse_mode)
            worker_parser.set_master_send_first(IEC104Parser.DEFAULT_MASTER_FIRST)

            frames = []
            if self.parse_mode == ParseMode.NORMAL:
                # 普通模式：按行解析
                hex_lines, line_map = self._extract_hex_lines(self.raw_text)
                total = len(hex_lines)

                if total == 0:
                    self.parse_finished.emit([])
                    return

                worker_parser.frames = []
                worker_parser.last_master_send = worker_parser.master_send_first

                for i, hex_line in enumerate(hex_lines):
                    if self._is_cancelled:
                        self.parse_finished.emit(frames)
                        return

                    hex_line = hex_line.strip()
                    if not hex_line:
                        continue

                    frame = self._parse_single_frame(worker_parser, hex_line, len(frames) + 1)
                    frames.append(frame)

                    # 未激活帧数上限：解析满即停
                    if self.max_frames and len(frames) >= self.max_frames:
                        self.hit_frame_limit = True
                        self.parse_finished.emit(frames)
                        return

                    # 每解析100帧更新一次进度
                    if (i + 1) % 100 == 0 or i + 1 == total:
                        self.progress_updated.emit(i + 1, total)

                # 保存输入行映射
                self._frame_input_lines = line_map
            else:
                # XN3000/XN8000模式
                blocks, block_line_map = self._extract_xn_blocks(self.raw_text, self.parse_mode)
                total = len(blocks)

                if total == 0:
                    self.parse_finished.emit([])
                    return

                worker_parser.frames = []
                worker_parser.last_master_send = worker_parser.master_send_first
                xn_line_map = []

                for i, (hex_text, timestamp, direction) in enumerate(blocks):
                    if self._is_cancelled:
                        self.parse_finished.emit(frames)
                        return

                    src_lines = block_line_map[i] if i < len(block_line_map) else []
                    split_frames = split_concatenated_frames(hex_text)
                    for frame_hex in split_frames:
                        if self._is_cancelled:
                            break
                        frame = self._parse_frame_with_metadata(
                            worker_parser, frame_hex, timestamp, direction, len(frames) + 1
                        )
                        frames.append(frame)
                        xn_line_map.append(list(src_lines))

                        # 未激活帧数上限：解析满即停
                        if self.max_frames and len(frames) >= self.max_frames:
                            self.hit_frame_limit = True
                            self.parse_finished.emit(frames)
                            return

                    if (i + 1) % 100 == 0 or i + 1 == total:
                        self.progress_updated.emit(i + 1, total)

                # 保存输入行映射
                self._frame_input_lines = xn_line_map

            self.parse_finished.emit(frames)

        except Exception as e:
            import traceback
            self.parse_error.emit(f"解析异常: {str(e)}\n{traceback.format_exc()}")

    def _extract_hex_lines(self, raw_text: str):
        """提取十六进制行（简化版）"""
        if is_log_format(raw_text):
            return parse_log_format_blocks(raw_text)

        cleaned = ''.join(c for c in raw_text if c in '0123456789ABCDEFabcdef')
        if cleaned and len(cleaned) % 2 == 0 and cleaned[:2].lower() == '68':
            try:
                raw_bytes = bytes.fromhex(cleaned)
                if len(raw_bytes) >= 6 and raw_bytes[0] == 0x68:
                    apdu_len = raw_bytes[1]
                    if apdu_len + 2 == len(raw_bytes):
                        all_lines = list(range(len(raw_text.strip().split('\n'))))
                        return [cleaned], [all_lines]
            except ValueError:
                pass

        all_lines = raw_text.strip().split('\n')
        hex_lines = []
        line_map = []
        for i, line in enumerate(all_lines):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
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
        return hex_lines, line_map

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