"""
主窗口 - IEC104 报文解析器
"""

import os
import sys
import re
import json
import csv
import struct
import logging
from datetime import datetime
from typing import List, Optional

from PyQt5.QtWidgets import (
    QMainWindow, QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTreeWidget, QTreeWidgetItem, QTextEdit, QPushButton,
    QToolBar, QAction, QFileDialog, QMessageBox, QStatusBar,
    QLabel, QLineEdit, QGroupBox, QGridLayout, QComboBox,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QCheckBox, QSpinBox, QProgressBar, QMenu, QMenuBar,
    QDialog, QListWidget, QListWidgetItem, QFrame, QPlainTextEdit,
    QDialogButtonBox, QFormLayout, QInputDialog
)
from PyQt5.QtCore import Qt, pyqtSignal, QThread, QTimer
from PyQt5.QtGui import QFont, QColor, QTextCursor, QIcon, QPalette, QPainter, QBrush, QPen, QPixmap

# 确保能找到父模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from iec104_parser import (
    IEC104Parser, IEC104Frame, SignalType, TypeID, COT,
    APCIHeader, ASDU, InfoElement, ParseMode,
    hex_to_bytes, is_log_format, split_concatenated_frames, parse_log_format_blocks
)
from gui.about_dialog import AboutDialog
from gui.stats_dialog import StatsDialog
from gui.ioa_alias_dialog import IOAAliasDialog, get_alias

logger = logging.getLogger(__name__)


class CaptureWorker(QThread):
    """网络抓包后台线程 (预留)"""
    packet_received = pyqtSignal(bytes)

    def __init__(self, interface: str, port: int):
        super().__init__()
        self.interface = interface
        self.port = port
        self.running = False

    def run(self):
        self.running = True
        pass

    def stop(self):
        self.running = False


class ParseWorker(QThread):
    """后台解析工作线程 - 避免UI阻塞"""
    progress_updated = pyqtSignal(int, int)  # 当前进度, 总数
    parse_finished = pyqtSignal(list)         # 解析完成的帧列表
    parse_error = pyqtSignal(str)             # 错误信息

    def __init__(self, parser: IEC104Parser, raw_text: str, parse_mode: ParseMode):
        super().__init__()
        self.parser = parser
        self.raw_text = raw_text
        self.parse_mode = parse_mode
        self._is_cancelled = False

    def cancel(self):
        """请求取消解析"""
        self._is_cancelled = True

    def run(self):
        """在后台线程中执行解析"""
        try:
            from copy import copy
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

                    # 每解析100帧更新一次进度
                    if (i + 1) % 100 == 0 or i + 1 == total:
                        self.progress_updated.emit(i + 1, total)

                # 保存输入行映射
                worker_parser._frame_input_lines = line_map
            else:
                # XN3000/XN8000模式
                blocks = self._extract_xn_blocks(self.raw_text, self.parse_mode)
                total = len(blocks)

                if total == 0:
                    self.parse_finished.emit([])
                    return

                worker_parser.frames = []
                worker_parser.last_master_send = worker_parser.master_send_first

                for i, (hex_text, timestamp, direction) in enumerate(blocks):
                    if self._is_cancelled:
                        self.parse_finished.emit(frames)
                        return

                    split_frames = split_concatenated_frames(hex_text)
                    for frame_hex in split_frames:
                        if self._is_cancelled:
                            break
                        frame = self._parse_frame_with_metadata(
                            worker_parser, frame_hex, timestamp, direction, len(frames) + 1
                        )
                        frames.append(frame)

                    if (i + 1) % 100 == 0 or i + 1 == total:
                        self.progress_updated.emit(i + 1, total)

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
            hex_lines.append(line)
            line_map.append([i])
        return hex_lines, line_map

    def _split_concatenated_frames(self, hex_block: str) -> list:
        cleaned = hex_block.replace(" ", "").replace(".", "")
        cleaned = ''.join(c for c in cleaned if c in '0123456789ABCDEFabcdef')
        if len(cleaned) < 12:
            return [hex_block] if cleaned else []

        frames = []
        i = 0
        while i < len(cleaned) - 1:
            if cleaned[i:i+2].lower() == '68':
                if i + 4 > len(cleaned):
                    break
                try:
                    apdu_len = int(cleaned[i+2:i+4], 16)
                except ValueError:
                    i += 2
                    continue
                frame_len = apdu_len + 2
                hex_len = frame_len * 2
                if i + hex_len <= len(cleaned):
                    frames.append(cleaned[i:i+hex_len])
                    i += hex_len
                else:
                    frames.append(cleaned[i:])
                    break
            else:
                i += 2
        return frames

    def _extract_xn_blocks(self, text: str, parse_mode: ParseMode):
        """提取XN格式的数据块"""
        lines = text.strip().split('\n')
        blocks = []
        current_timestamp = ""
        current_direction = ""
        current_hex_parts = []

        for line in lines:
            line = line.strip()

            if parse_mode == ParseMode.XN3000:
                ts_match = re.match(r'^(\d{2}:\d{2}:\d{2}\.\d+)\s+(RX|TX):\s*$', line)
            else:
                ts_match = re.match(r'^([\d\- :\.]+)\s+(send|recv):\s*$', line)

            if ts_match:
                if current_hex_parts:
                    blocks.append((''.join(current_hex_parts), current_timestamp, current_direction))
                    current_hex_parts = []
                current_timestamp = ts_match.group(1)
                current_direction = ts_match.group(2)
                continue

            if current_timestamp:
                cleaned = line.replace('.', '').strip()
                if cleaned:
                    current_hex_parts.append(cleaned)

        if current_hex_parts:
            blocks.append((''.join(current_hex_parts), current_timestamp, current_direction))

        return blocks

    def _parse_single_frame(self, parser, hex_string: str, index: int):
        """解析单个帧"""
        from iec104_parser import IEC104Frame, _CONTROL_TYPE_IDS, _MONITOR_TYPE_IDS
        frame = IEC104Frame()
        frame.index = index
        frame.raw_hex = hex_string.strip()

        try:
            raw_bytes = hex_to_bytes(frame.raw_hex)
        except ValueError as e:
            frame.error = f"十六进制解析错误: {e}"
            return frame

        if len(raw_bytes) < 6:
            frame.error = "报文太短 (最少6字节)"
            return frame

        frame.apci = parser._parse_apci(raw_bytes)
        if frame.apci is None:
            frame.error = "APCI解析失败"
            return frame

        apci = frame.apci

        if len(raw_bytes) < apci.total_length:
            frame.error = f"报文不完整 (需要{apci.total_length}字节, 实际{len(raw_bytes)}字节)"
        else:
            raw_bytes = raw_bytes[:apci.total_length]

        if parser.last_master_send:
            frame.send_role = "子站"
            frame.recv_role = "主站"
            frame.direction = "子站→主站"
        else:
            frame.send_role = "主站"
            frame.recv_role = "子站"
            frame.direction = "主站→子站"
        parser.last_master_send = not parser.last_master_send

        if apci.frame_type == "I-format" and len(raw_bytes) >= 7:
            try:
                frame.asdu = parser._parse_asdu(raw_bytes[6:], apci)
                parser._build_description(frame)

                tid = frame.asdu.type_id
                if tid in _CONTROL_TYPE_IDS:
                    frame.direction = "主站→子站"
                    frame.send_role = "主站"
                    frame.recv_role = "子站"
                    parser.last_master_send = True
                elif tid in _MONITOR_TYPE_IDS:
                    frame.direction = "子站→主站"
                    frame.send_role = "子站"
                    frame.recv_role = "主站"
                    parser.last_master_send = False

                frame.signal_type = frame.asdu.signal_type
                frame.signal_label = frame.asdu.signal_type.label

            except Exception as e:
                frame.error = f"ASDU解析异常: {e}"
        elif apci.frame_type == "S-format":
            frame.description = f"S帧 (确认帧, RN={apci.recv_seq})"
        elif apci.frame_type == "U-format":
            frame.description = parser._describe_u_frame(raw_bytes[2:6])

        return frame

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


class MainWindow(QMainWindow):
    """主窗口"""

    APP_TITLE = "IEC60870-5-104 报文解析器"
    APP_VERSION = "1.1.0"

    def __init__(self):
        super().__init__()
        self.parser = IEC104Parser()
        self.all_frames: List[IEC104Frame] = []
        self._byte_position_map = []
        self._frame_byte_offset = []
        self._current_frame_idx = 0
        self.capture_worker: Optional[CaptureWorker] = None
        self.parse_mode: ParseMode = ParseMode.NORMAL
        self._dark_theme = False
        self._current_theme = "light"

        # 后台解析相关
        self._parse_worker: Optional[ParseWorker] = None
        self._is_parsing: bool = False

        self.setWindowTitle(self.APP_TITLE)
        self.resize(1320, 840)
        self.setMinimumSize(900, 640)

        self._setup_icon()
        self.setAcceptDrops(True)

        self._setup_ui()
        self._setup_menu()
        self._setup_toolbar()
        self._setup_statusbar()

        self._update_status_stats()

        self.hex_input.setPlainText(
            "# 在此粘贴 IEC104 十六进制报文\n"
            "# 支持格式: 68 04 01 00 0E 00  或  680401000E00\n"
            "# 每行一条报文，以 # 开头的行为注释\n\n"
        )

    def _setup_ui(self):
        """构建界面布局"""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(4)

        self.splitter = QSplitter(Qt.Horizontal)

        # === 左侧: 输入面板 ===
        self.left_panel = QWidget()
        left_panel = self.left_panel
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # 十六进制输入
        hex_group = QGroupBox("原始报文输入 (十六进制)")
        hex_layout = QVBoxLayout(hex_group)
        # 多标签页输入
        self.input_tabs = QTabWidget()
        self.input_tabs.setTabsClosable(True)
        self.input_tabs.tabCloseRequested.connect(self._close_input_tab)
        hex_layout.addWidget(self.input_tabs)

        # 创建第一个输入标签页
        self._input_tab_list = []  # [(tab_widget, hex_input), ...]
        self._add_input_tab()

        # 输入操作按钮
        btn_layout = QHBoxLayout()
        self.btn_parse = QPushButton("▶ 解析报文")
        self.btn_parse.clicked.connect(self._on_parse)
        self.btn_parse.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; "
            "font-weight: bold; padding: 6px 16px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #1976D2; }"
        )
        self.btn_clear = QPushButton("清空")
        self.btn_clear.clicked.connect(self._on_clear)
        self.btn_import_file = QPushButton("导入文件")
        self.btn_import_file.clicked.connect(self._on_import_file)

        btn_layout.addWidget(self.btn_parse)
        btn_layout.addWidget(self.btn_clear)
        btn_layout.addWidget(self.btn_import_file)
        btn_layout.addStretch()
        self.lbl_input_status = QLabel("")
        self.lbl_input_status.setStyleSheet("color: #888; font-size: 9pt;")
        btn_layout.addWidget(self.lbl_input_status)
        self.lbl_line_count = QLabel("0 行")
        btn_layout.addWidget(self.lbl_line_count)
        hex_layout.addLayout(btn_layout)
        left_layout.addWidget(hex_group)

        # 解析模式选择
        mode_group = QGroupBox("解析模式")
        mode_layout = QHBoxLayout(mode_group)
        mode_layout.addWidget(QLabel("模式:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("普通模式", ParseMode.NORMAL)
        self.mode_combo.addItem("XN3000 模式", ParseMode.XN3000)
        self.mode_combo.addItem("XN8000 模式", ParseMode.XN8000)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_layout.addWidget(self.mode_combo)
        mode_layout.addStretch()
        mode_desc = QLabel("普通: 标准格式 | XN3000: 带时间戳RX/TX格式 | XN8000: 带时间戳send/recv格式")
        mode_desc.setStyleSheet("color: #666; font-size: 9pt;")
        mode_layout.addWidget(mode_desc)
        left_layout.addWidget(mode_group)

        # 过滤选项
        filter_group = QGroupBox("过滤条件")
        filter_layout = QHBoxLayout(filter_group)

        filter_layout.addWidget(QLabel("信号类型:"))
        self.filter_signal = QComboBox()
        self.filter_signal.addItem("全部", "")
        self.filter_signal.addItem("遥信", "遥信")
        self.filter_signal.addItem("遥测", "遥测")
        self.filter_signal.addItem("遥控", "遥控")
        self.filter_signal.addItem("遥调", "遥调")
        self.filter_signal.addItem("遥脉", "遥脉")
        filter_layout.addWidget(self.filter_signal)

        filter_layout.addWidget(QLabel("类型:"))
        self.filter_type = QComboBox()
        self.filter_type.setMinimumWidth(260)
        self.filter_type.addItem("全部", -1)
        for tid in sorted(TypeID):
            desc = TypeID.get_description(tid.value)
            self.filter_type.addItem(f"{tid.value}: {tid.name} ({desc})", tid.value)
        filter_layout.addWidget(self.filter_type)

        filter_layout.addWidget(QLabel("传送原因:"))
        self.filter_cot = QComboBox()
        self.filter_cot.setMinimumWidth(200)
        self.filter_cot.addItem("全部", -1)
        for cot in range(1, 42):
            self.filter_cot.addItem(f"{cot}: {COT.get_description(cot)}", cot)
        filter_layout.addWidget(self.filter_cot)

        filter_layout.addWidget(QLabel("地址:"))
        self.filter_addr = QLineEdit()
        self.filter_addr.setPlaceholderText("公共地址")
        self.filter_addr.setMaximumWidth(80)
        filter_layout.addWidget(self.filter_addr)

        # IOA 范围过滤
        filter_layout.addWidget(QLabel("IOA 范围:"))
        self.ioa_from_input = QLineEdit()
        self.ioa_from_input.setPlaceholderText("起始")
        self.ioa_from_input.setMaximumWidth(80)
        filter_layout.addWidget(self.ioa_from_input)
        filter_layout.addWidget(QLabel("~"))
        self.ioa_to_input = QLineEdit()
        self.ioa_to_input.setPlaceholderText("结束")
        self.ioa_to_input.setMaximumWidth(80)
        filter_layout.addWidget(self.ioa_to_input)

        self.filter_btn = QPushButton("筛选")
        self.filter_btn.clicked.connect(self._on_filter)
        filter_layout.addWidget(self.filter_btn)
        self.filter_reset = QPushButton("重置")
        self.filter_reset.clicked.connect(self._on_filter_reset)
        filter_layout.addWidget(self.filter_reset)
        filter_layout.addStretch()
        left_layout.addWidget(filter_group)

        self.splitter.addWidget(left_panel)

        # === 右侧: 解析结果 ===
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.result_tabs = QTabWidget()

        # 标签页1: 树形结构
        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderLabels(["字段", "值", "说明"])
        self.tree_widget.setAlternatingRowColors(True)
        self.tree_widget.setColumnWidth(0, 280)
        self.tree_widget.setColumnWidth(1, 350)
        self.tree_widget.setColumnWidth(2, 400)
        self.tree_widget.itemSelectionChanged.connect(self._on_tree_select)
        self.result_tabs.addTab(self.tree_widget, "解析详情")

        # 标签页2: 报文列表 (带搜索栏)
        table_container = QWidget()
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(2)

        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("🔍 搜索:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入关键字搜索 IOA / 值 / 描述...")
        self.search_input.textChanged.connect(self._on_search)
        self.search_input.setClearButtonEnabled(True)
        search_layout.addWidget(self.search_input)
        self.lbl_search_count = QLabel("")
        self.lbl_search_count.setStyleSheet("color: #888; font-size: 9pt;")
        search_layout.addWidget(self.lbl_search_count)
        table_layout.addLayout(search_layout)

        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(11)
        self.table_widget.setHorizontalHeaderLabels(
            ["序号", "原始时间", "方向", "信号类型", "类型标识", "传送原因", "公共地址", "对象数", "值", "描述", "输入行"]
        )
        self.table_widget.setAlternatingRowColors(True)
        self.table_widget.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_widget.horizontalHeader().setStretchLastSection(True)
        self.table_widget.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_widget.cellClicked.connect(self._on_table_click)
        self.table_widget.cellDoubleClicked.connect(self._on_table_double_click)
        self.table_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_widget.customContextMenuRequested.connect(self._on_table_context_menu)
        table_layout.addWidget(self.table_widget)

        self.result_tabs.addTab(table_container, "报文列表")

        # 标签页3: 十六进制对照
        self.hex_view = QTextEdit()
        self.hex_view.setReadOnly(True)
        self.hex_view.setFont(QFont("Consolas", 10))
        self.result_tabs.addTab(self.hex_view, "原始十六进制")

        right_layout.addWidget(self.result_tabs)

        # 导出按钮
        export_layout = QHBoxLayout()
        export_layout.addStretch()
        for label, slot in [
            ("导出CSV", self._on_export_csv),
            ("导出JSON", self._on_export_json),
            ("导出TXT", self._on_export_txt),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            export_layout.addWidget(btn)
        right_layout.addLayout(export_layout)

        self.splitter.addWidget(right_panel)
        self.splitter.setSizes([500, 750])
        main_layout.addWidget(self.splitter)

    def _setup_menu(self):
        """设置菜单栏"""
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件(&F)")
        file_menu.addAction("导入文件(&I)...", self._on_import_file, "Ctrl+I")
        file_menu.addAction("新建标签页(&N)", self._add_input_tab, "Ctrl+N")
        file_menu.addAction("导出CSV(&C)...", self._on_export_csv, "Ctrl+Shift+C")
        file_menu.addAction("导出JSON(&J)...", self._on_export_json, "Ctrl+Shift+J")
        file_menu.addAction("导出TXT(&T)...", self._on_export_txt, "Ctrl+Shift+T")
        file_menu.addSeparator()
        file_menu.addAction("退出(&X)", self.close, "Alt+F4")

        view_menu = menubar.addMenu("查看(&V)")
        view_menu.addAction("解析报文(&P)", self._on_parse, "F5")
        view_menu.addAction("清空(&L)", self._on_clear, "Ctrl+L")
        view_menu.addAction("定位原始报文(&L)", lambda: self._locate_original_message(self.table_widget.currentRow()), "F2")
        view_menu.addAction("切换主题(&D)", self._toggle_theme, "Ctrl+Shift+D")
        view_menu.addSeparator()
        view_menu.addAction("折叠/展开输入面板(&B)", self._toggle_input_panel, "Ctrl+B")
        view_menu.addSeparator()
        view_menu.addAction("查找(&F)...", self._on_focus_search, "Ctrl+F")
        view_menu.addAction("统计图表(&T)...", self._on_show_stats, "Ctrl+T")
        view_menu.addSeparator()
        view_menu.addAction("解析详情", lambda: self.result_tabs.setCurrentIndex(0), "Ctrl+1")
        view_menu.addAction("报文列表", lambda: self.result_tabs.setCurrentIndex(1), "Ctrl+2")
        view_menu.addAction("原始十六进制", lambda: self.result_tabs.setCurrentIndex(2), "Ctrl+3")

        tools_menu = menubar.addMenu("工具(&T)")
        tools_menu.addAction("帧生成器(&G)...", self._open_generator, "Ctrl+G")
        tools_menu.addAction("历史报文管理(&H)...", self._open_history_manager, "Ctrl+Shift+R")
        tools_menu.addSeparator()
        tools_menu.addAction("序号检查(&S)...", self._on_check_sequence, "Ctrl+Shift+S")
        tools_menu.addAction("协议合规检查(&C)...", self._on_check_compliance)
        tools_menu.addAction("报文配对分析(&P)...", self._on_find_pairs)
        tools_menu.addSeparator()
        tools_menu.addAction("IOA地址别名(&I)...", self._on_manage_aliases, "Ctrl+Shift+I")

        help_menu = menubar.addMenu("帮助(&H)")
        help_menu.addAction("关于(&A)...", self._on_about)

    def _setup_toolbar(self):
        """设置工具栏"""
        toolbar = QToolBar("主工具栏")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        act_parse = toolbar.addAction("解析", self._on_parse)
        act_parse.setToolTip("解析报文 (F5)")
        act_import = toolbar.addAction("导入", self._on_import_file)
        act_import.setToolTip("导入文件 (Ctrl+I)")
        toolbar.addSeparator()
        act_gen = toolbar.addAction("生成报文", self._open_generator)
        act_gen.setToolTip("帧生成器 (Ctrl+G)")
        toolbar.addSeparator()
        act_csv = toolbar.addAction("CSV", self._on_export_csv)
        act_csv.setToolTip("导出CSV (Ctrl+Shift+C)")
        act_json = toolbar.addAction("JSON", self._on_export_json)
        act_json.setToolTip("导出JSON (Ctrl+Shift+J)")
        toolbar.addSeparator()
        self._left_panel_visible = True
        self._toggle_input_action = toolbar.addAction("◀ 隐藏输入")
        self._toggle_input_action.triggered.connect(self._toggle_input_panel)
        self._toggle_input_action.setToolTip("折叠/展开原始报文输入面板 (Ctrl+B)")
        toolbar.addSeparator()
        act_about = toolbar.addAction("关于", self._on_about)
        act_about.setToolTip("关于本软件")

    def _setup_icon(self):
        """创建应用程序图标"""
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor("#1565C0"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(4, 4, 56, 56, 10, 10)
        painter.setPen(QPen(Qt.white, 2))
        font = QFont("Arial", 18, QFont.Bold)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "104")
        painter.end()
        self.setWindowIcon(QIcon(pixmap))

    def _setup_statusbar(self):
        """设置状态栏"""
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)

        self.lbl_stats = QLabel("就绪")
        self.statusbar.addWidget(self.lbl_stats, 1)

        # 进度条（解析时显示）
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(200)
        self.progress_bar.setMaximumHeight(16)
        self.progress_bar.setVisible(False)
        self.statusbar.addWidget(self.progress_bar)

        # 取消按钮（解析时显示）
        self.btn_cancel_parse = QPushButton("取消解析")
        self.btn_cancel_parse.setMaximumHeight(20)
        self.btn_cancel_parse.setStyleSheet(
            "QPushButton { background-color: #f44336; color: white; padding: 2px 8px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #d32f2f; }"
        )
        self.btn_cancel_parse.clicked.connect(self._on_cancel_parse)
        self.btn_cancel_parse.setVisible(False)
        self.statusbar.addWidget(self.btn_cancel_parse)

        self.lbl_count = QLabel("报文: 0")
        self.lbl_count.setStyleSheet("color: #666; padding: 0 8px;")
        self.statusbar.addPermanentWidget(self.lbl_count)

        self.lbl_version = QLabel(f"v{self.APP_VERSION}")
        self.lbl_version.setStyleSheet("color: #999; padding: 0 8px;")
        self.statusbar.addPermanentWidget(self.lbl_version)

    def _update_status_stats(self):
        """更新状态栏统计"""
        stats = self.parser.get_statistics()
        total = stats["total"]
        valid = stats["valid"]
        yx_count = sum(1 for f in self.all_frames if f.signal_type == SignalType.YX)
        yc_count = sum(1 for f in self.all_frames if f.signal_type == SignalType.YC)
        yk_count = sum(1 for f in self.all_frames if f.signal_type == SignalType.YK)
        yt_count = sum(1 for f in self.all_frames if f.signal_type == SignalType.YT)
        ym_count = sum(1 for f in self.all_frames if f.signal_type == SignalType.YM)

        self.lbl_stats.setText(
            f"总报文: {total}  |  有效: {valid}  |  无效: {total - valid}  |  "
            f"I帧: {stats['i_frames']}  S帧: {stats['s_frames']}  U帧: {stats['u_frames']}  |  "
            f"主→从: {stats['master_send']}  从→主: {stats['slave_send']}  |  "
            f"遥信: {yx_count}  遥测: {yc_count}  遥控: {yk_count}  遥调: {yt_count}  遥脉: {ym_count}"
        )
        self.lbl_count.setText(f"报文: {total}")

    # ==================================================================
    # 解析
    # ==================================================================

    def _hex_to_lines(self, raw_text: str):
        """智能拆分报文：支持日志格式、换行合并、批量解析"""
        # 检测日志格式
        if is_log_format(raw_text):
            return self._parse_log_format(raw_text)

        # 去除空白字符，提取纯十六进制
        cleaned = ''.join(c for c in raw_text if c in '0123456789ABCDEFabcdef')

        # 尝试将整个输入作为单条报文解析 (自动合并换行)
        if cleaned and len(cleaned) % 2 == 0 and cleaned[:2].lower() == '68':
            try:
                raw_bytes = bytes.fromhex(cleaned)
                if len(raw_bytes) >= 6 and raw_bytes[0] == 0x68:
                    apdu_len = raw_bytes[1]
                    if apdu_len + 2 == len(raw_bytes):
                        return [cleaned]
            except ValueError:
                pass

        # 回退：按行拆分 (批量解析模式)
        lines = raw_text.strip().split("\n")
        hex_lines = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            hex_lines.append(line)
        return hex_lines

    def _hex_to_lines_with_mapping(self, raw_text: str):
        """拆分报文并返回每个报文对应的输入行号列表

        Returns:
            hex_lines: list[str]  - 每条报文的十六进制字符串
            line_map: list[list[int]] - 每条报文对应的输入行号(0-based)
        """
        if is_log_format(raw_text):
            return self._parse_log_format_with_mapping(raw_text)

        cleaned = ''.join(c for c in raw_text if c in '0123456789ABCDEFabcdef')

        # 单条合并报文
        if cleaned and len(cleaned) % 2 == 0 and cleaned[:2].lower() == '68':
            try:
                raw_bytes = bytes.fromhex(cleaned)
                if len(raw_bytes) >= 6 and raw_bytes[0] == 0x68:
                    apdu_len = raw_bytes[1]
                    if apdu_len + 2 == len(raw_bytes):
                        all_lines = list(range(len(raw_text.strip().split("\n"))))
                        return [cleaned], [all_lines]
            except ValueError:
                pass

        # 按行拆分
        all_lines = raw_text.strip().split("\n")
        hex_lines = []
        line_map = []
        for i, line in enumerate(all_lines):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            hex_lines.append(line)
            line_map.append([i])
        return hex_lines, line_map

    def _parse_log_format_with_mapping(self, text: str):
        """解析日志格式并返回行号映射（自动拆分拼接帧）"""
        return parse_log_format_blocks(text)

    def _parse_log_format(self, text: str) -> list:
        """解析通信日志格式，返回十六进制报文列表（自动拆分拼接帧）"""
        lines = text.strip().split('\n')
        blocks = []      # 报文块列表
        current_hex = [] # 当前块的十六进制行

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 检测时间戳 + 方向头 (如: "13:25:00.692 RX:")
            m = re.match(r'[\d:.\s]+(RX|TX):\s*$', line)
            if m:
                if current_hex:
                    blocks.append(' '.join(current_hex))
                    current_hex = []
                continue

            # 检测纯方向头 (如: "RX:" 或 "TX:")
            m = re.match(r'^(RX|TX):\s*$', line)
            if m:
                if current_hex:
                    blocks.append(' '.join(current_hex))
                    current_hex = []
                continue

            # 十六进制数据行
            if re.search(r'[0-9A-Fa-f]{2}', line):
                current_hex.append(line)

        # 最后一个块
        if current_hex:
            blocks.append(' '.join(current_hex))

        # 拆分每个块中的拼接帧
        result = []
        for block in blocks:
            frames = split_concatenated_frames(block)
            result.extend(frames)
        return result

    def _on_parse(self):
        """解析报文 - 使用后台线程避免UI阻塞"""
        raw_text = self.hex_input.toPlainText()

        if not raw_text.strip() or raw_text.strip().startswith("# 在此粘贴"):
            QMessageBox.information(self, "提示", "请输入十六进制报文数据")
            return

        # 如果正在解析，先取消
        if self._is_parsing:
            if self._parse_worker and self._parse_worker.isRunning():
                self._parse_worker.cancel()
                self._parse_worker.wait(1000)
            self._is_parsing = False

        # 清除旧数据
        self.parser.clear()
        self.all_frames.clear()
        self._byte_position_map = []
        self._frame_byte_offset = []
        self._frame_input_lines = []
        self.tree_widget.clear()
        self.table_widget.setRowCount(0)
        self.hex_view.clear()

        # 预处理输入区颜色（仅普通模式）
        if self.parse_mode == ParseMode.NORMAL:
            self._apply_input_line_colors()

        # 启动后台解析
        self._is_parsing = True
        self._parse_worker = ParseWorker(self.parser, raw_text, self.parse_mode)

        # 连接信号
        self._parse_worker.progress_updated.connect(self._on_parse_progress)
        self._parse_worker.parse_finished.connect(self._on_parse_finished)
        self._parse_worker.parse_error.connect(self._on_parse_error)

        # 更新UI状态
        self._set_parsing_state(True)
        self.statusbar.showMessage("正在解析报文...", 0)

        # 启动后台线程
        self._parse_worker.start()

    def _on_clear(self):
        """清空"""
        self.hex_input.clear()
        self.parser.clear()
        self.all_frames.clear()
        self._byte_position_map = []
        self._frame_byte_offset = []
        self._frame_input_lines = []
        self.tree_widget.clear()
        self.table_widget.setRowCount(0)
        self.hex_view.clear()
        self.lbl_line_count.setText("0 行")
        self._update_status_stats()

    def _set_parsing_state(self, parsing: bool):
        """切换解析状态的UI"""
        self._is_parsing = parsing
        self.progress_bar.setVisible(parsing)
        self.btn_cancel_parse.setVisible(parsing)

        # 禁用/启用相关按钮
        self.btn_parse.setEnabled(not parsing)
        self.btn_import_file.setEnabled(not parsing)
        self.btn_clear.setEnabled(not parsing)

        if parsing:
            self.progress_bar.setValue(0)
            self.btn_parse.setText("⏳ 解析中...")
        else:
            self.btn_parse.setText("▶ 解析报文")

    def _on_parse_progress(self, current: int, total: int):
        """更新解析进度"""
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.statusbar.showMessage(f"正在解析报文... {current}/{total}")

    def _on_parse_finished(self, frames: list):
        """解析完成处理"""
        self._is_parsing = False
        self.all_frames = frames

        # 存储输入行映射
        if hasattr(self._parse_worker, '_frame_input_lines'):
            self._frame_input_lines = self._parse_worker._frame_input_lines
        else:
            self._frame_input_lines = [[] for _ in self.all_frames]

        # 更新UI
        self._populate_table()
        self._update_status_stats()

        # 恢复按钮状态
        self._set_parsing_state(False)

        # 统计信息
        error_count = sum(1 for f in self.all_frames if f.error)
        self.statusbar.showMessage(
            f"解析完成: {len(self.all_frames)} 条报文, {error_count} 条失败", 5000)

        # 自动切换到报文列表标签
        self.result_tabs.setCurrentIndex(1)

    def _on_parse_error(self, error_msg: str):
        """解析错误处理"""
        self._is_parsing = False
        self._set_parsing_state(False)
        self.statusbar.showMessage(f"解析失败: {error_msg}", 5000)
        QMessageBox.critical(self, "解析错误", f"解析过程中发生错误:\n\n{error_msg}")

    def _on_cancel_parse(self):
        """取消解析"""
        if self._parse_worker and self._parse_worker.isRunning():
            self._parse_worker.cancel()
            self.statusbar.showMessage("正在取消解析...", 0)
            # 等待线程结束
            if not self._parse_worker.wait(2000):
                self._parse_worker.terminate()

    def _on_mode_changed(self, index: int):
        """解析模式变更"""
        self.parse_mode = self.mode_combo.currentData()
        mode_name = self.mode_combo.currentText()
        self.statusbar.showMessage(f"已切换到 {mode_name}", 2000)

    # ==================================================================
    # 主题切换
    # ==================================================================

    def _toggle_theme(self):
        """切换深色/浅色主题"""
        self._dark_theme = not self._dark_theme
        if self._dark_theme:
            self._apply_dark_theme()
            self.statusbar.showMessage("已切换到深色主题", 2000)
        else:
            self._apply_light_theme()
            self.statusbar.showMessage("已切换到浅色主题", 2000)

    def _apply_dark_theme(self):
        """应用深色主题"""
        dark_style = """
            QMainWindow, QWidget { background-color: #2b2b2b; color: #e0e0e0; }
            QGroupBox { border: 1px solid #555; border-radius: 4px; margin-top: 8px; padding-top: 16px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
            QPlainTextEdit, QTextEdit, QLineEdit { background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #555; border-radius: 3px; }
            QTableWidget { background-color: #1e1e1e; color: #d4d4d4; gridline-color: #404040; }
            QTableWidget::item:selected { background-color: #264f78; }
            QHeaderView::section { background-color: #333; color: #ccc; border: 1px solid #555; padding: 4px; }
            QComboBox { background-color: #3c3c3c; color: #d4d4d4; border: 1px solid #555; border-radius: 3px; padding: 3px; }
            QComboBox QAbstractItemView { background-color: #3c3c3c; color: #d4d4d4; selection-background-color: #264f78; }
            QPushButton { background-color: #3c3c3c; color: #d4d4d4; border: 1px solid #555; border-radius: 4px; padding: 4px 12px; }
            QPushButton:hover { background-color: #4a4a4a; }
            QToolBar { background-color: #333; border-bottom: 1px solid #555; }
            QTabWidget::pane { border: 1px solid #555; }
            QTabBar::tab { background-color: #333; color: #ccc; padding: 6px 16px; border: 1px solid #555; border-bottom: none; }
            QTabBar::tab:selected { background-color: #2b2b2b; color: #fff; }
            QTreeWidget { background-color: #1e1e1e; color: #d4d4d4; }
            QProgressBar { border: 1px solid #555; border-radius: 3px; text-align: center; }
            QProgressBar::chunk { background-color: #2196F3; }
            QMenuBar { background-color: #333; color: #e0e0e0; }
            QMenuBar::item:selected { background-color: #4a4a4a; }
            QMenu { background-color: #333; color: #e0e0e0; }
            QMenu::item:selected { background-color: #264f78; }
        """
        self.setStyleSheet(dark_style)

    def _apply_light_theme(self):
        """应用浅色主题（恢复默认）"""
        self.setStyleSheet("")

    # ==================================================================
    # 多标签页输入
    # ==================================================================

    def _add_input_tab(self, title=None, content=None):
        """添加新的输入标签页"""
        tab_widget = QWidget()
        tab_layout = QVBoxLayout(tab_widget)
        tab_layout.setContentsMargins(0, 0, 0, 0)

        hex_input = QPlainTextEdit()
        hex_input.setPlaceholderText("粘贴十六进制报文...")
        hex_input.setFont(QFont("Consolas", 11))
        hex_input.setLineWrapMode(QPlainTextEdit.NoWrap)
        hex_input.setAcceptDrops(False)
        hex_input.textChanged.connect(self._on_input_changed)
        hex_input.setContextMenuPolicy(Qt.CustomContextMenu)
        hex_input.customContextMenuRequested.connect(self._on_input_context_menu)
        tab_layout.addWidget(hex_input)

        if content:
            hex_input.setPlainText(content)

        count = len(self._input_tab_list) + 1
        tab_title = title or f"报文 {count}"
        self.input_tabs.addTab(tab_widget, tab_title)
        self._input_tab_list.append((tab_widget, hex_input))
        self.input_tabs.setCurrentIndex(len(self._input_tab_list) - 1)

        # 更新当前 hex_input 引用
        self.hex_input = hex_input
        return hex_input

    def _close_input_tab(self, index: int):
        """关闭输入标签页"""
        if len(self._input_tab_list) <= 1:
            return  # 至少保留一个标签页
        self.input_tabs.removeTab(index)
        self._input_tab_list.pop(index)
        if self._input_tab_list:
            idx = min(index, len(self._input_tab_list) - 1)
            self.hex_input = self._input_tab_list[idx][1]
            self.input_tabs.setCurrentIndex(idx)

    def _get_current_hex_input(self):
        """获取当前活动的输入框"""
        idx = self.input_tabs.currentIndex()
        if 0 <= idx < len(self._input_tab_list):
            return self._input_tab_list[idx][1]
        return self.hex_input

    # ==================================================================
    # 导入/导出
    # ==================================================================

    # _on_import_file 已移至下方"多文件批量导入"区域

    def _on_export_csv(self):
        """导出CSV"""
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有可导出的数据")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出CSV", "iec104_parsed.csv",
                                                "CSV文件 (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["序号", "方向", "信号类型", "帧类型", "类型标识", "类型描述",
                                 "传送原因", "传送原因描述", "公共地址", "对象数",
                                 "信息对象地址", "值", "时标", "错误"])
                for frame in self.all_frames:
                    if frame.asdu and frame.asdu.information_elements:
                        for elem in frame.asdu.information_elements:
                            ts = elem.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] \
                                if elem.timestamp else ""
                            writer.writerow([
                                frame.index, frame.direction,
                                frame.signal_label,
                                frame.apci.frame_type if frame.apci else "",
                                frame.asdu.type_id,
                                TypeID.get_description(frame.asdu.type_id),
                                frame.asdu.cot_cause,
                                COT.get_description(frame.asdu.cot),
                                frame.asdu.common_address,
                                len(frame.asdu.information_elements),
                                f"0x{elem.ioa:06X}",
                                elem.value_desc, ts, frame.error
                            ])
                    else:
                        writer.writerow([
                            frame.index, frame.direction if frame.direction else "",
                            "", frame.apci.frame_type if frame.apci else "",
                            "", "", "", "", "", "",
                            "", frame.description if frame.description else "",
                            "", frame.error
                        ])
            self.statusbar.showMessage(f"已导出CSV: {os.path.basename(path)}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _on_export_json(self):
        """导出JSON"""
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有可导出的数据")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出JSON", "iec104_parsed.json",
                                                "JSON文件 (*.json)")
        if not path:
            return
        try:
            data = []
            for frame in self.all_frames:
                entry = {
                    "index": frame.index,
                    "raw_hex": frame.raw_hex,
                    "direction": frame.direction,
                    "send_role": frame.send_role,
                    "recv_role": frame.recv_role,
                    "signal_type": frame.signal_label,
                    "description": frame.description,
                    "error": frame.error,
                    "apci": None,
                    "asdu": None,
                }
                if frame.apci:
                    entry["apci"] = {
                        "frame_type": frame.apci.frame_type,
                        "send_seq": frame.apci.send_seq,
                        "recv_seq": frame.apci.recv_seq,
                        "apdu_length": frame.apci.apdu_length,
                    }
                if frame.asdu:
                    entry["asdu"] = {
                        "type_id": frame.asdu.type_id,
                        "type_name": TypeID.get_name(frame.asdu.type_id),
                        "type_desc": TypeID.get_description(frame.asdu.type_id),
                        "signal_type": frame.asdu.signal_type.label,
                        "vsq": frame.asdu.vsq,
                        "object_count": frame.asdu.number_of_objects,
                        "is_sequence": frame.asdu.is_sequence,
                        "cot": frame.asdu.cot,
                        "cot_cause": frame.asdu.cot_cause,
                        "cot_desc": COT.get_description(frame.asdu.cot),
                        "cot_test": frame.asdu.cot_test,
                        "cot_pn": frame.asdu.cot_pn,
                        "org": frame.asdu.org,
                        "common_address": frame.asdu.common_address,
                        "elements": []
                    }
                    for elem in frame.asdu.information_elements:
                        entry["asdu"]["elements"].append({
                            "ioa": f"0x{elem.ioa:06X}",
                            "signal_type": elem.signal_type.label,
                            "value": str(elem.value),
                            "value_desc": elem.value_desc,
                            "quality": str(elem.quality) if elem.quality else None,
                            "timestamp": elem.timestamp.isoformat() if elem.timestamp else None,
                        })
                data.append(entry)

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.statusbar.showMessage(f"已导出JSON: {os.path.basename(path)}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _on_export_txt(self):
        """导出TXT"""
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有可导出的数据")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出TXT", "iec104_parsed.txt",
                                                "文本文件 (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("=" * 80 + "\n")
                f.write("IEC60870-5-104 报文解析报告\n")
                f.write(f"解析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"报文总数: {len(self.all_frames)}\n")
                f.write("=" * 80 + "\n\n")

                for frame in self.all_frames:
                    f.write(f"--- 报文 #{frame.index} ---\n")
                    f.write(f"方向: {frame.direction}\n")
                    if frame.signal_label:
                        f.write(f"信号类型: {frame.signal_label}\n")
                    f.write(f"原始数据: {frame.raw_hex}\n")
                    if frame.error:
                        f.write(f"错误: {frame.error}\n")
                    elif frame.description:
                        f.write(f"描述: {frame.description}\n")
                    if frame.apci:
                        f.write(f"帧类型: {frame.apci.frame_type}\n")
                        f.write(f"发送序号SN: {frame.apci.send_seq}\n")
                        f.write(f"接收序号RN: {frame.apci.recv_seq}\n")
                    if frame.asdu:
                        f.write(f"类型标识: {frame.asdu.type_id} "
                                f"({TypeID.get_description(frame.asdu.type_id)})\n")
                        f.write(f"传送原因: {COT.get_description(frame.asdu.cot)}\n")
                        f.write(f"公共地址: {frame.asdu.common_address}\n")
                        f.write(f"信息对象数: {frame.asdu.number_of_objects}\n")
                        for elem in frame.asdu.information_elements:
                            f.write(f"  IOA=0x{elem.ioa:06X} [{elem.signal_type.label}]: "
                                    f"{elem.value_desc}\n")
                            if elem.quality:
                                f.write(f"    品质: {elem.quality}\n")
                            if elem.timestamp:
                                f.write(f"    时标: {elem.timestamp}\n")
                    f.write("\n")
            self.statusbar.showMessage(f"已导出TXT: {os.path.basename(path)}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    # ==================================================================
    # 填充表格/树
    # ==================================================================

    def _row_color_for_frame(self, frame: IEC104Frame) -> QColor:
        """根据帧类型返回行背景色"""
        if frame.error:
            return QColor("#FFF0F0")  # 错误: 浅红
        if not frame.apci:
            return QColor("#FFFFFF")
        ftype = frame.apci.frame_type
        if ftype == "I-format":
            if frame.signal_type == SignalType.YK or frame.signal_type == SignalType.YT:
                return QColor("#FFF8E1")  # 控制: 浅橙
            return QColor("#F5FAFF")  # 数据: 浅蓝
        elif ftype == "S-format":
            return QColor("#F5FFF5")  # 确认: 浅绿
        elif ftype == "U-format":
            return QColor("#FFF5FA")  # 控制: 浅粉
        return QColor("#FFFFFF")

    def _populate_table(self):
        """填充报文列表表格 - 优化大量数据性能"""
        self.table_widget.setRowCount(0)

        if not self.all_frames:
            return

        line_map = getattr(self, '_frame_input_lines', [])

        # 预分配行数（性能优化）
        total_frames = len(self.all_frames)
        self.table_widget.setRowCount(total_frames)

        # 禁用排序和更新以提高性能
        self.table_widget.setSortingEnabled(False)
        self.table_widget.setUpdatesEnabled(False)

        # 填充所有行
        for i, frame in enumerate(self.all_frames):
            row = i

            # 行背景色
            row_color = self._row_color_for_frame(frame)

            # 序号
            idx_item = QTableWidgetItem(str(frame.index))
            idx_item.setBackground(row_color)
            idx_item.setData(Qt.UserRole + 1, frame.index)
            self.table_widget.setItem(row, 0, idx_item)

            # 原始时间
            ts_text = frame.original_timestamp if frame.original_timestamp else "-"
            self._set_table_item(row, 1, ts_text, row_color)

            # 方向
            dir_text = frame.direction
            if not dir_text and frame.original_direction:
                dir_text = frame.original_direction.upper()
            dir_item = QTableWidgetItem(dir_text)
            if "主站→子站" in dir_text or "TX" in dir_text or "SEND" in dir_text:
                dir_item.setForeground(QColor("#E91E63"))
            elif "子站→主站" in dir_text or "RX" in dir_text or "RECV" in dir_text:
                dir_item.setForeground(QColor("#2196F3"))
            dir_item.setBackground(row_color)
            self.table_widget.setItem(row, 2, dir_item)

            # 信号类型
            sig_item = QTableWidgetItem(frame.signal_label if frame.signal_label else "-")
            if frame.signal_type != SignalType.OTHER:
                sig_item.setForeground(QColor(frame.signal_type.color_hex))
                sig_item.setFont(QFont(self.font().family(), self.font().pointSize(), QFont.Bold))
            sig_item.setBackground(row_color)
            self.table_widget.setItem(row, 3, sig_item)

            # 类型标识
            if frame.asdu:
                tid_text = f"{frame.asdu.type_id}: {TypeID.get_description(frame.asdu.type_id)}"
            else:
                tid_text = frame.apci.frame_type if frame.apci else "-"
            self._set_table_item(row, 4, tid_text, row_color)

            # 传送原因
            if frame.asdu:
                cot_text = f"({frame.asdu.cot_cause}) {COT.get_description(frame.asdu.cot)}"
            else:
                cot_text = "-"
            self._set_table_item(row, 5, cot_text, row_color)

            # 公共地址
            ca_text = f"0x{frame.asdu.common_address:04X}" if frame.asdu else "-"
            self._set_table_item(row, 6, ca_text, row_color)

            # 对象数
            obj_text = str(frame.asdu.number_of_objects) if frame.asdu else "-"
            obj_count = frame.asdu.number_of_objects if frame.asdu else 0
            self._set_table_item(row, 7, obj_text, row_color, sort_key=obj_count)

            # 值 (汇总)
            if frame.asdu and frame.asdu.information_elements:
                vals = []
                for elem in frame.asdu.information_elements:
                    alias = get_alias(elem.ioa)
                    prefix = f"[{alias}] " if alias else ""
                    vals.append(f"{prefix}{elem.value_desc}")
                self._set_table_item(row, 8, " | ".join(vals), row_color)
            else:
                self._set_table_item(row, 8, "-", row_color)

            # 描述
            desc = frame.description if frame.description else (frame.error if frame.error else "")
            self._set_table_item(row, 9, desc, row_color)

            # 输入行号
            fidx = frame.index - 1
            if fidx < len(line_map) and line_map[fidx]:
                src_lines = line_map[fidx]
                line_text = ", ".join(str(n + 1) for n in src_lines)
                line_sort = src_lines[0] if src_lines else 0
            else:
                line_text = "-"
                line_sort = 0
            self._set_table_item(row, 10, line_text, row_color, sort_key=line_sort)

        # 恢复排序和更新
        self.table_widget.setSortingEnabled(True)
        self.table_widget.setUpdatesEnabled(True)

        # 调整列宽
        self.table_widget.resizeColumnsToContents()
        self.table_widget.setColumnWidth(0, 50)
        self.table_widget.setColumnWidth(1, 130)
        self.table_widget.setColumnWidth(3, 60)
        self.table_widget.setColumnWidth(8, 300)
        self.table_widget.setColumnWidth(10, 60)

        # 自动选中第一条报文
        if self.all_frames and self.table_widget.rowCount() > 0:
            self._current_frame_idx = 0
            self.table_widget.selectRow(0)
            self._populate_tree(0)

    def _set_table_item(self, row: int, col: int, text: str, bg: QColor = None,
                         sort_key=None):
        item = QTableWidgetItem(text)
        if bg:
            item.setBackground(bg)
        # 设置自定义排序键（数值列用数值排序）
        if sort_key is not None:
            item.setData(Qt.UserRole + 1, sort_key)
        self.table_widget.setItem(row, col, item)

    def _populate_tree(self, frame_index: int):
        """填充解析树"""
        self.tree_widget.clear()

        if frame_index < 0 or frame_index >= len(self.all_frames):
            return

        frame = self.all_frames[frame_index]

        # 根节点 (整个报文)
        self._add_tree_item(None, f"报文 #{frame.index}", frame.raw_hex, "",
                           byte_range=(0, frame.apci.total_length if frame.apci else 0))

        # 基本信息
        info_root = self._add_tree_item(None, "基本信息", "", "")

        if frame.original_timestamp:
            self._add_tree_item(info_root, "原始时间戳", frame.original_timestamp, "")
        if frame.original_direction:
            self._add_tree_item(info_root, "原始方向标记", frame.original_direction, "")

        if frame.direction:
            self._add_tree_item(info_root, "发送方向", frame.direction, "")
            self._add_tree_item(info_root, "发送方角色", frame.send_role, "")
            self._add_tree_item(info_root, "接收方角色", frame.recv_role, "")

        # 信号类型
        if frame.signal_label:
            sig_color = QColor(frame.signal_type.color_hex)
            self._add_tree_item(info_root, "信号类型", frame.signal_label,
                                self._signal_type_extra(frame.signal_type), sig_color)

        if frame.error:
            self._add_tree_item(info_root, "错误", frame.error, "解析错误", QColor("#F44336"))
            return

        if frame.description:
            self._add_tree_item(info_root, "描述", frame.description, "")

        # APCI (字节0-5)
        if frame.apci:
            apci = frame.apci
            apci_root = self._add_tree_item(
                None, "APCI (应用规约控制信息)", "", "帧控制信息",
                byte_range=(0, 6))
            self._add_tree_item(apci_root, "起始字节", f"0x{apci.start_byte:02X} (68H)", "固定0x68",
                               byte_range=(0, 1))
            self._add_tree_item(apci_root, "APDU长度", f"{apci.apdu_length} 字节",
                                f"总帧长={apci.total_length}字节",
                                byte_range=(1, 2))
            self._add_tree_item(apci_root, "帧类型", apci.frame_type,
                                self._frame_type_desc(apci.frame_type),
                                byte_range=(2, 6))
            self._add_tree_item(apci_root, "发送序号SN", str(apci.send_seq),
                                "Send Sequence Number")
            self._add_tree_item(apci_root, "接收序号RN", str(apci.recv_seq),
                                "Receive Sequence Number")

        # ASDU (字节6+)
        if frame.asdu:
            asdu = frame.asdu
            asdu_end = frame.apci.total_length if frame.apci else 6 + len(asdu.raw_data)
            asdu_root = self._add_tree_item(
                None, "ASDU (应用服务数据单元)", "", "应用数据",
                byte_range=(6, asdu_end))

            sig_color = QColor(asdu.signal_type.color_hex) \
                if asdu.signal_type != SignalType.OTHER else None
            self._add_tree_item(asdu_root, "信号类型",
                                f"{asdu.signal_type.label}",
                                "", sig_color)

            self._add_tree_item(asdu_root, "类型标识",
                                f"{asdu.type_id} ({TypeID.get_name(asdu.type_id)})",
                                TypeID.get_description(asdu.type_id),
                                byte_range=(6, 7))
            self._add_tree_item(asdu_root, "VSQ (可变结构限定词)",
                                f"0x{asdu.vsq:02X}",
                                f"对象数={asdu.number_of_objects}, "
                                f"地址类型={'顺序' if asdu.is_sequence else '离散'}",
                                byte_range=(7, 8))
            self._add_tree_item(asdu_root, "传送原因",
                                f"0x{asdu.cot:04X} ({asdu.cot_cause})",
                                COT.get_description(asdu.cot) +
                                (", 试验" if asdu.cot_test else "") +
                                (", 否定确认" if asdu.cot_pn else ""),
                                byte_range=(8, 10))
            self._add_tree_item(asdu_root, "源发方地址(ORG)",
                                f"0x{asdu.org:02X} ({asdu.org})",
                                "COT高字节/源发方地址")
            self._add_tree_item(asdu_root, "公共地址(ASDU地址)",
                                f"0x{asdu.common_address:04X} ({asdu.common_address})",
                                "公共地址/ASDU地址(2字节)",
                                byte_range=(10, 12))

            # 信息元素
            for i, elem in enumerate(asdu.information_elements):
                elem_sig = elem.signal_type.label
                alias = get_alias(elem.ioa)
                alias_str = f" ({alias})" if alias else ""
                elem_root = self._add_tree_item(
                    asdu_root, f"信息元素 #{i+1}",
                    f"IOA=0x{elem.ioa:06X}{alias_str} [{elem_sig}]",
                    f"信息对象地址")
                if elem.value_desc:
                    self._add_tree_item(elem_root, "值", elem.value_desc, "")
                if elem.quality:
                    self._add_tree_item(elem_root, "品质描述词",
                                        f"0x{elem.quality.raw:02X}",
                                        str(elem.quality))
                if elem.timestamp:
                    ts_str = elem.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    self._add_tree_item(elem_root, "时标", ts_str,
                                        "CP56Time2a" if len(elem.value_desc) > 50 else "")

            # 补齐字段
            if asdu.pad:
                self._add_tree_item(asdu_root, "补齐字段", f"0x{asdu.pad:02X}", "0补齐对齐")

        self.tree_widget.expandAll()
        self._update_hex_view(frame_index)

    def _signal_type_extra(self, st: SignalType) -> str:
        """信号类型额外说明"""
        return {
            SignalType.YX: "状态量(单点/双点信息)",
            SignalType.YC: "模拟量(测量值)",
            SignalType.YK: "开关控制(命令)",
            SignalType.YT: "调节控制(设定值/步位置)",
            SignalType.YM: "累计量/电能",
            SignalType.OTHER: "",
        }.get(st, "")

    def _add_tree_item(self, parent, col0: str, col1: str, col2: str,
                       fg_color: QColor = None, byte_range: tuple = None) -> QTreeWidgetItem:
        if parent:
            item = QTreeWidgetItem(parent)
        else:
            item = QTreeWidgetItem(self.tree_widget)
        item.setText(0, col0)
        item.setText(1, col1)
        item.setText(2, col2)
        if fg_color:
            for col in range(3):
                item.setForeground(col, fg_color)
        if byte_range:
            item.setData(0, Qt.UserRole, byte_range)
        return item

    def _frame_type_desc(self, ftype: str) -> str:
        if ftype == "I-format":
            return "信息传输格式(编号帧,携带ASDU数据)"
        elif ftype == "S-format":
            return "编号监视格式(确认帧,无ASDU数据)"
        elif ftype == "U-format":
            return "未编号控制格式(启动/停止/测试)"
        return ""

    def _update_hex_view(self, frame_index: int, highlight_start: int = -1, highlight_end: int = -1):
        """更新十六进制视图，可选高亮指定字节范围"""
        if frame_index < 0 or frame_index >= len(self.all_frames):
            return
        frame = self.all_frames[frame_index]
        html = self._format_hex_html(frame.raw_hex, highlight_start, highlight_end)
        self.hex_view.setHtml(
            f"<p style='color:#666;font-family:Consolas;font-size:10pt'>"
            f"# 报文 #{frame.index} 原始十六进制</p>"
            f"<p style='font-family:Consolas;font-size:10pt'>{html}</p>"
        )

    def _format_hex_html(self, raw: str, hl_start: int = -1, hl_end: int = -1) -> str:
        """格式化十六进制为HTML，支持高亮指定字节范围"""
        cleaned = raw.replace(" ", "").replace("\n", "")
        cleaned = ''.join(c for c in cleaned if c in '0123456789ABCDEFabcdef')
        if len(cleaned) % 2 != 0:
            return raw

        parts = []
        for i in range(0, len(cleaned), 2):
            if i > 0:
                if i % 32 == 0:
                    parts.append("<br>")
                else:
                    parts.append(" ")
            byte_idx = i // 2
            hex_byte = cleaned[i:i+2].upper()
            if hl_start <= byte_idx < hl_end:
                parts.append(
                    f'<span style="background-color:#FFEB3B;color:#000;'
                    f'padding:1px 2px;border-radius:2px">{hex_byte}</span>'
                )
            else:
                parts.append(hex_byte)
        return "".join(parts)

    def _format_hex(self, raw: str) -> str:
        """格式化十六进制字符串，每16字节换行"""
        cleaned = raw.replace(" ", "").replace("\n", "")
        cleaned = ''.join(c for c in cleaned if c in '0123456789ABCDEFabcdef')
        if len(cleaned) % 2 != 0:
            return raw
        result = []
        byte_count = 0
        for i in range(0, len(cleaned), 2):
            result.append(cleaned[i:i+2].upper())
            byte_count += 1
            if byte_count % 16 == 0:
                result.append("\n")
        return " ".join(result)

    # ==================================================================
    # 事件处理
    # ==================================================================

    def _on_table_click(self, row: int, col: int):
        """表格行点击 -> 显示详情树和十六进制"""
        self._current_frame_idx = row
        self._populate_tree(row)
        self.result_tabs.setCurrentIndex(0)

    def _on_table_double_click(self, row: int, col: int):
        """表格行双击 -> 切换到解析详情标签页"""
        self._current_frame_idx = row
        self._populate_tree(row)
        self.result_tabs.setCurrentIndex(0)

    def _on_input_context_menu(self, pos):
        """输入区右键菜单"""
        menu = QMenu(self)

        # 根据光标位置定位所在行对应的报文
        cursor = self.hex_input.cursorForPosition(pos)
        cursor_line = cursor.blockNumber()

        line_map = getattr(self, '_frame_input_lines', [])
        found_frame_idx = -1
        for fidx, src_lines in enumerate(line_map):
            if cursor_line in src_lines:
                found_frame_idx = fidx
                break

        if found_frame_idx >= 0 and found_frame_idx < len(self.all_frames):
            frame = self.all_frames[found_frame_idx]
            act_jump = menu.addAction(f"🔍 跳转到报文 #{frame.index} 的解析结果")
            act_jump.triggered.connect(lambda: self._jump_to_frame(found_frame_idx))
            menu.addSeparator()

        act_parse = menu.addAction("▶ 解析报文 (F5)")
        act_parse.triggered.connect(self._on_parse)

        act_clear = menu.addAction("🗑 清空")
        act_clear.triggered.connect(self._on_clear)

        menu.exec_(self.hex_input.mapToGlobal(pos))

    def _jump_to_frame(self, frame_index: int):
        """跳转到指定报文的解析结果"""
        if frame_index < 0 or frame_index >= len(self.all_frames):
            return
        self._current_frame_idx = frame_index
        self._populate_tree(frame_index)
        self.table_widget.selectRow(frame_index)
        self.result_tabs.setCurrentIndex(0)

    def _on_table_context_menu(self, pos):
        """表格右键菜单"""
        row = self.table_widget.rowAt(pos.y())
        if row < 0 or row >= len(self.all_frames):
            return

        self.table_widget.selectRow(row)
        frame = self.all_frames[row]

        menu = QMenu(self)

        # 复制原始报文
        act_copy_hex = menu.addAction("📋 复制原始报文")
        act_copy_hex.triggered.connect(lambda: self._copy_to_clipboard(frame.raw_hex))

        # 复制描述
        desc = frame.description if frame.description else frame.error
        if desc:
            act_copy_desc = menu.addAction("📋 复制描述")
            act_copy_desc.triggered.connect(lambda: self._copy_to_clipboard(desc))

        # 复制所有值
        if frame.asdu and frame.asdu.information_elements:
            act_copy_vals = menu.addAction("📋 复制所有值")
            vals = "\n".join(
                f"IOA=0x{e.ioa:06X}: {e.value_desc}"
                for e in frame.asdu.information_elements
            )
            act_copy_vals.triggered.connect(lambda: self._copy_to_clipboard(vals))

        # 复制 IOA 地址
        if frame.asdu and frame.asdu.information_elements:
            copy_ioa_action = menu.addAction("📋 复制 IOA 地址")
            copy_ioa_action.triggered.connect(lambda: self._copy_ioa_to_clipboard())

        menu.addSeparator()

        # 复制全部解析结果
        act_copy_all = menu.addAction("📋 复制全部解析结果")
        act_copy_all.triggered.connect(lambda: self._copy_all_results())

        menu.addSeparator()

        # 定位原始报文
        act_locate = menu.addAction("📍 定位原始报文")
        act_locate.triggered.connect(lambda: self._locate_original_message(row))

        # 跳转详情
        act_detail = menu.addAction("🔍 查看解析详情")
        act_detail.triggered.connect(lambda: self._show_detail(row))

        # 跳转十六进制视图
        act_hex_view = menu.addAction("🔢 查看十六进制")
        act_hex_view.triggered.connect(lambda: self._show_hex_view(row))

        menu.exec_(self.table_widget.viewport().mapToGlobal(pos))

    def _copy_to_clipboard(self, text: str):
        """复制文本到剪贴板"""
        QApplication.clipboard().setText(text)
        self.statusbar.showMessage("已复制到剪贴板", 2000)

    def _copy_ioa_to_clipboard(self):
        """复制选中行的IOA地址到剪贴板"""
        rows = set(idx.row() for idx in self.table_widget.selectedIndexes())
        if not rows:
            return
        ioas = []
        for row in rows:
            if row < len(self.all_frames):
                frame = self.all_frames[row]
                if frame.asdu:
                    for elem in frame.asdu.information_elements:
                        ioas.append(f"0x{elem.ioa:06X}")
        if ioas:
            QApplication.clipboard().setText("\n".join(ioas))
            self.statusbar.showMessage(f"已复制 {len(ioas)} 个 IOA 地址", 2000)

    def _copy_all_results(self):
        """复制全部解析结果到剪贴板"""
        if not self.all_frames:
            return
        lines = []
        for frame in self.all_frames:
            line = f"#{frame.index} {frame.direction} {frame.signal_label or ''}"
            if frame.asdu:
                line += f" TID={frame.asdu.type_id}"
                if frame.asdu.information_elements:
                    for elem in frame.asdu.information_elements:
                        alias = get_alias(elem.ioa)
                        prefix = f"[{alias}] " if alias else ""
                        line += f" IOA=0x{elem.ioa:06X} {prefix}{elem.value_desc}"
            elif frame.error:
                line += f" ERROR={frame.error}"
            lines.append(line)
        QApplication.clipboard().setText("\n".join(lines))
        self.statusbar.showMessage(f"已复制 {len(self.all_frames)} 条解析结果", 2000)

    def _show_detail(self, row: int):
        """显示指定行的解析详情"""
        self._current_frame_idx = row
        self._populate_tree(row)
        self.result_tabs.setCurrentIndex(0)

    def _show_hex_view(self, row: int):
        """显示指定行的十六进制视图"""
        self._current_frame_idx = row
        self._update_hex_view(row)
        self.result_tabs.setCurrentIndex(2)

    def _locate_original_message(self, row: int):
        """定位原始报文 - 滚动到输入区对应位置并高亮"""
        line_map = getattr(self, '_frame_input_lines', [])
        if row >= len(line_map) or not line_map[row]:
            self.statusbar.showMessage("未找到对应的原始报文位置", 2000)
            return

        # 获取该帧对应的输入行号（取第一个行号）
        line_numbers = line_map[row]
        target_line = line_numbers[0] if line_numbers else 0

        # 滚动到目标行并选中
        cursor = self.hex_input.textCursor()
        cursor.movePosition(cursor.Start)
        for _ in range(target_line):
            cursor.movePosition(cursor.Down)
        cursor.select(cursor.LineUnderCursor)
        self.hex_input.setTextCursor(cursor)
        self.hex_input.centerCursor()
        self.hex_input.setFocus()

        # 高亮选中的行（通过设置选区样式）
        self.hex_input.setStyleSheet(
            "QPlainTextEdit { selection-background-color: #FFEB3B; selection-color: #000; }"
        )
        # 1.5秒后恢复正常样式
        from PyQt5.QtCore import QTimer
        QTimer.singleShot(1500, lambda: self.hex_input.setStyleSheet(""))

        self.statusbar.showMessage(f"已定位到输入区第 {target_line + 1} 行", 2000)

    def _on_focus_search(self):
        """Ctrl+F 聚焦到地址过滤框"""
        self.filter_addr.setFocus()
        self.filter_addr.selectAll()

    def _toggle_input_panel(self):
        """折叠/展开原始报文输入面板"""
        self._left_panel_visible = not self._left_panel_visible
        if self._left_panel_visible:
            self.left_panel.show()
            self._toggle_input_action.setText("◀ 隐藏输入")
            self.splitter.setSizes([500, 750])
        else:
            self.left_panel.hide()
            self._toggle_input_action.setText("▶ 显示输入")
        self._left_panel_visible_saved = self._left_panel_visible

    def _on_tree_select(self):
        """树节点选择时在十六进制视图中高亮对应字节范围"""
        items = self.tree_widget.selectedItems()
        if not items:
            self._update_hex_view(self._current_frame_idx)
            return

        item = items[0]
        byte_range = item.data(0, Qt.UserRole)
        if byte_range:
            self._update_hex_view(self._current_frame_idx,
                                  highlight_start=byte_range[0],
                                  highlight_end=byte_range[1])
        else:
            self._update_hex_view(self._current_frame_idx)

    def _clear_input_highlight(self):
        """清除输入区高亮"""
        self.hex_input.setExtraSelections([])

    def _highlight_input_line_for_frame(self, frame_index: int):
        """高亮输入区中对应报文的源行"""
        line_map = getattr(self, '_frame_input_lines', [])
        if frame_index >= len(line_map) or not line_map[frame_index]:
            self._clear_input_highlight()
            return

        source_lines = line_map[frame_index]
        all_text = self.hex_input.toPlainText()
        all_lines = all_text.split("\n")

        selections = []
        for line_num in source_lines:
            if line_num >= len(all_lines):
                continue
            # 计算该行在文本中的起止位置
            pos_start = sum(len(all_lines[i]) + 1 for i in range(line_num))
            pos_end = pos_start + len(all_lines[line_num])

            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(QColor("#BBDEFB"))
            sel.format.setForeground(QColor("#000000"))
            cursor = self.hex_input.textCursor()
            cursor.setPosition(pos_start)
            cursor.setPosition(pos_end, QTextCursor.KeepAnchor)
            sel.cursor = cursor
            selections.append(sel)

        self.hex_input.setExtraSelections(selections)

        # 滚动到第一个高亮行
        if selections:
            self.hex_input.setTextCursor(selections[0].cursor)

    def _apply_input_line_colors(self):
        """解析后为输入区每行添加交替背景色，便于区分各报文"""
        all_text = self.hex_input.toPlainText()
        all_lines = all_text.split("\n")
        line_map = getattr(self, '_frame_input_lines', [])

        # 构建行号 -> 报文索引的映射
        line_to_frame = {}
        for fidx, src_lines in enumerate(line_map):
            for ln in src_lines:
                line_to_frame[ln] = fidx

        selections = []
        colors = [QColor("#F5F5F5"), QColor("#FFFFFF")]
        for i, line in enumerate(all_lines):
            if not line.strip() or line.strip().startswith("#"):
                continue
            fidx = line_to_frame.get(i, -1)
            if fidx < 0:
                continue

            pos_start = sum(len(all_lines[j]) + 1 for j in range(i))
            pos_end = pos_start + len(line)

            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(colors[fidx % 2])
            cursor = self.hex_input.textCursor()
            cursor.setPosition(pos_start)
            cursor.setPosition(pos_end, QTextCursor.KeepAnchor)
            sel.cursor = cursor
            selections.append(sel)

        self.hex_input.setExtraSelections(selections)

    def _highlight_input_bytes(self, start_byte: int, end_byte: int, frame_index: int = 0):
        """在原始报文输入区高亮指定字节范围"""
        byte_map = getattr(self, '_byte_position_map', [])
        frame_offsets = getattr(self, '_frame_byte_offset', [])

        # Convert frame-relative to global byte index
        if frame_index < len(frame_offsets):
            offset = frame_offsets[frame_index]
            start_byte += offset
            end_byte += offset

        if start_byte >= len(byte_map) or end_byte > len(byte_map):
            return

        text_start = byte_map[start_byte]
        text_end = byte_map[end_byte - 1] + 1

        cursor = self.hex_input.textCursor()
        cursor.setPosition(text_start)
        cursor.setPosition(text_end, QTextCursor.KeepAnchor)

        extra_sel = QTextEdit.ExtraSelection()
        extra_sel.format.setBackground(QColor("#FFEB3B"))
        extra_sel.format.setForeground(QColor("#000000"))
        extra_sel.cursor = cursor
        self.hex_input.setExtraSelections([extra_sel])

    def _build_byte_position_map(self, text: str) -> list:
        """构建字节索引到原始文本位置的映射。
        处理普通十六进制和带时间戳/方向标记的通信日志格式。
        返回 list[byte_idx] = text_position_of_first_hex_char。
        """
        byte_map = []
        is_log = is_log_format(text)
        i = 0
        n = len(text)

        while i < n:
            if is_log:
                rest = text[i:]
                # 跳过纯元数据行 (时间戳 + RX/TX，行尾无十六进制数据)
                m = re.match(r'[\d:.\s]+(?:RX|TX):[ \t]*[\r\n]+', rest)
                if m:
                    i += m.end()
                    continue
                m = re.match(r'^(?:RX|TX):[ \t]*[\r\n]+', rest)
                if m:
                    i += m.end()
                    continue

                # 跳过行首的元数据前缀 (时间戳 + RX/TX + 空格，后面跟十六进制数据)
                m = re.match(r'[\d:.\s]+(?:RX|TX):[ \t]*', rest)
                if m:
                    i += m.end()
                else:
                    m = re.match(r'^(?:RX|TX):[ \t]*', rest)
                    if m:
                        i += m.end()

            # 从当前位置开始收集十六进制字节对
            byte_start = None
            byte_chars = []
            while i < n:
                c = text[i]
                if c in '0123456789ABCDEFabcdef':
                    if byte_start is None:
                        byte_start = i
                    byte_chars.append(c)
                    if len(byte_chars) == 2:
                        byte_map.append(byte_start)
                        byte_chars = []
                        byte_start = None
                elif c in '\r\n':
                    i += 1
                    break
                # else: 分隔符 (. 或 空格), 忽略
                i += 1
            else:
                break  # 文本结束

        return byte_map

    def _current_frame_index(self) -> int:
        """获取当前展示的报文索引"""
        for i in range(self.table_widget.rowCount()):
            item = self.table_widget.item(i, 0)
            if item and item.isSelected():
                return i
        return 0

    # ==================================================================
    # 拖放文件支持
    # ==================================================================

    def dragEnterEvent(self, event):
        """拖入事件：接受文件拖放"""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    file_path = url.toLocalFile()
                    if file_path.lower().endswith(('.txt', '.log', '.hex', '.dat', '.bin', '.pcap')):
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dropEvent(self, event):
        """放下事件：读取拖入的文件"""
        urls = event.mimeData().urls()
        if not urls:
            return

        file_path = urls[0].toLocalFile()
        if not file_path:
            return

        if file_path.lower().endswith('.pcap'):
            try:
                from pcap_importer import pcap_to_hex_text
                hex_text = pcap_to_hex_text(file_path)
                if hex_text:
                    self.hex_input.setPlainText(hex_text)
                    self.statusBar().showMessage(f"已导入 PCAP: {os.path.basename(file_path)}")
                else:
                    QMessageBox.warning(self, "导入失败", "PCAP 文件中未找到 IEC104 报文")
            except ImportError:
                QMessageBox.warning(self, "导入失败", "PCAP 导入模块未找到")
            except Exception as e:
                QMessageBox.warning(self, "导入失败", f"导入 PCAP 失败: {e}")
            return

        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            self.hex_input.setPlainText(content)
            self.statusbar.showMessage(f"已加载拖放文件: {os.path.basename(file_path)}", 3000)
        except Exception:
            try:
                with open(file_path, "rb") as f:
                    raw = f.read()
                hex_lines = []
                for i in range(0, len(raw), 16):
                    chunk = raw[i:i + 16]
                    hex_lines.append(chunk.hex(" ").upper())
                self.hex_input.setPlainText("\n".join(hex_lines))
                self.statusbar.showMessage(
                    f"已加载二进制文件: {os.path.basename(file_path)}", 3000)
            except Exception as e:
                QMessageBox.critical(self, "加载失败", f"无法读取文件:\n{e}")

    # ==================================================================
    # 实时输入校验
    # ==================================================================

    def _on_input_changed(self):
        """输入区内容变化时实时校验"""
        text = self.hex_input.toPlainText()
        if not text.strip():
            self.lbl_input_status.setText("")
            return

        # 统计有效十六进制字符
        hex_chars = ''.join(c for c in text if c in '0123456789ABCDEFabcdef')
        non_hex = len(text.replace(' ', '').replace('\n', '').replace('\r', '')) - len(hex_chars)

        if non_hex > 0:
            self.lbl_input_status.setText(f"⚠️ 含{non_hex}个非法字符")
            self.lbl_input_status.setStyleSheet("color: #E65100; font-size: 9pt;")
        elif len(hex_chars) % 2 != 0:
            self.lbl_input_status.setText("⚠️ 十六进制长度为奇数")
            self.lbl_input_status.setStyleSheet("color: #E65100; font-size: 9pt;")
        else:
            byte_count = len(hex_chars) // 2
            self.lbl_input_status.setText(f"✓ {byte_count} 字节")
            self.lbl_input_status.setStyleSheet("color: #4CAF50; font-size: 9pt;")

    # ==================================================================
    # 搜索
    # ==================================================================

    def _on_search(self, text: str):
        """在表格中搜索关键字"""
        keyword = text.strip().lower()
        if not keyword:
            # 显示所有行
            for row in range(self.table_widget.rowCount()):
                self.table_widget.setRowHidden(row, False)
            self.lbl_search_count.setText("")
            return

        match_count = 0
        for row in range(self.table_widget.rowCount()):
            match = False
            for col in range(self.table_widget.columnCount()):
                item = self.table_widget.item(row, col)
                if item and keyword in item.text().lower():
                    match = True
                    break
            self.table_widget.setRowHidden(row, not match)
            if match:
                match_count += 1

        self.lbl_search_count.setText(f"{match_count} 条匹配")

    # ==================================================================
    # 序号连续性检查
    # ==================================================================

    def _on_check_sequence(self):
        """检查I帧序号连续性"""
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有已解析的报文，请先解析")
            return

        issues = self.parser.check_sequence()
        if not issues:
            QMessageBox.information(
                self, "序号检查",
                f"✅ 检查通过！共 {len(self.all_frames)} 条报文，未发现序号异常。")
            return

        # 构建详细报告
        report_lines = [f"发现 {len(issues)} 处序号异常：\n"]
        for issue in issues:
            report_lines.append(f"  • {issue['detail']}")

        dialog = QMessageBox(self)
        dialog.setWindowTitle("序号检查结果")
        dialog.setIcon(QMessageBox.Warning)
        dialog.setText(f"发现 {len(issues)} 处序号跳变")
        dialog.setDetailedText("\n".join(report_lines))
        dialog.exec_()

    # ==================================================================
    # 多文件批量导入
    # ==================================================================

    def _on_import_file(self):
        """导入文件（支持多选）"""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "导入报文文件", "",
            "报文文件 (*.txt *.log *.hex *.dat *.bin *.pcap);;所有文件 (*.*)"
        )
        if not paths:
            return

        if len(paths) == 1:
            # 单文件：沿用原逻辑
            self._load_single_file(paths[0])
        else:
            # 多文件：合并内容
            all_content = []
            for path in paths:
                try:
                    if path.lower().endswith('.pcap'):
                        from pcap_importer import pcap_to_hex_text
                        content = pcap_to_hex_text(path)
                        if content:
                            all_content.append(f"# === 文件: {os.path.basename(path)} ===")
                            all_content.append(content)
                        else:
                            all_content.append(f"# 读取失败: {os.path.basename(path)} - 未找到 IEC104 报文")
                        continue
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    all_content.append(f"# === 文件: {os.path.basename(path)} ===")
                    all_content.append(content)
                except Exception:
                    try:
                        with open(path, "rb") as f:
                            raw = f.read()
                        hex_lines = []
                        for i in range(0, len(raw), 16):
                            chunk = raw[i:i + 16]
                            hex_lines.append(chunk.hex(" ").upper())
                        all_content.append(f"# === 文件: {os.path.basename(path)} ===")
                        all_content.append("\n".join(hex_lines))
                    except Exception as e:
                        all_content.append(f"# 读取失败: {os.path.basename(path)} - {e}")

            self.hex_input.setPlainText("\n".join(all_content))
            self.statusbar.showMessage(f"已合并导入 {len(paths)} 个文件", 3000)

    def _load_single_file(self, path: str):
        """加载单个文件"""
        if path.lower().endswith('.pcap'):
            try:
                from pcap_importer import pcap_to_hex_text
                hex_text = pcap_to_hex_text(path)
                if hex_text:
                    self.hex_input.setPlainText(hex_text)
                    self.statusbar.showMessage(f"已导入 PCAP: {os.path.basename(path)}", 3000)
                else:
                    QMessageBox.warning(self, "导入失败", "PCAP 文件中未找到 IEC104 报文")
            except ImportError:
                QMessageBox.warning(self, "导入失败", "PCAP 导入模块未找到")
            except Exception as e:
                QMessageBox.warning(self, "导入失败", f"导入 PCAP 失败: {e}")
            return

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            self.hex_input.setPlainText(content)
            self.statusbar.showMessage(f"已导入: {os.path.basename(path)}", 3000)
        except Exception:
            try:
                with open(path, "rb") as f:
                    raw = f.read()
                hex_lines = []
                for i in range(0, len(raw), 16):
                    chunk = raw[i:i + 16]
                    hex_lines.append(chunk.hex(" ").upper())
                self.hex_input.setPlainText("\n".join(hex_lines))
                self.statusbar.showMessage(
                    f"已导入二进制文件: {os.path.basename(path)}", 3000)
            except Exception as e2:
                QMessageBox.critical(self, "导入失败", f"无法读取文件:\n{e2}")

    # ==================================================================
    # 统计图表
    # ==================================================================

    def _on_show_stats(self):
        """显示统计图表"""
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有已解析的报文，请先解析")
            return
        StatsDialog(self.all_frames, self).exec_()

    # ==================================================================
    # 协议合规检查
    # ==================================================================

    def _on_check_compliance(self):
        """协议合规性检查"""
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有已解析的报文，请先解析")
            return

        issues = self.parser.check_compliance()
        if not issues:
            QMessageBox.information(
                self, "协议合规检查",
                f"✅ 检查通过！共 {len(self.all_frames)} 条报文，未发现协议违规。")
            return

        # 按严重程度分组
        errors = [i for i in issues if i.get("severity") == "error"]
        warnings = [i for i in issues if i.get("severity") == "warning"]
        infos = [i for i in issues if i.get("severity") == "info"]

        report = []
        report.append(f"共发现 {len(issues)} 个问题：")
        if errors:
            report.append(f"  ❌ 错误: {len(errors)}")
        if warnings:
            report.append(f"  ⚠️ 警告: {len(warnings)}")
        if infos:
            report.append(f"  ℹ️ 提示: {len(infos)}")
        report.append("")
        for issue in issues:
            icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(issue["severity"], "•")
            report.append(f"  {icon} {issue['detail']}")

        dialog = QMessageBox(self)
        dialog.setWindowTitle("协议合规检查结果")
        dialog.setIcon(QMessageBox.Warning if errors else QMessageBox.Information)
        dialog.setText(f"发现 {len(issues)} 个合规问题")
        dialog.setDetailedText("\n".join(report))
        dialog.exec_()

    # ==================================================================
    # 报文配对分析
    # ==================================================================

    def _on_find_pairs(self):
        """报文配对分析"""
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有已解析的报文，请先解析")
            return

        pairs = self.parser.find_pairs()
        if not pairs:
            QMessageBox.information(self, "配对分析", "未发现可配对的报文（总召/遥控/时钟同步）")
            return

        lines = [f"共发现 {len(pairs)} 组配对关系：\n"]
        for p in pairs:
            req_idx = f"#{p['request'].index}" if p['request'] else "无"
            resp_idx = f"#{p['response'].index}" if p['response'] else "无"
            status = p['status']
            icon = "✅" if status in ("确认", "完成") else "⚠️"
            lines.append(
                f"  {icon} [{p['type']}] 请求:{req_idx} → 响应:{resp_idx} ({status})")

        dialog = QMessageBox(self)
        dialog.setWindowTitle("报文配对分析")
        dialog.setIcon(QMessageBox.Information)
        dialog.setText(f"发现 {len(pairs)} 组配对")
        dialog.setDetailedText("\n".join(lines))
        dialog.exec_()

    # ==================================================================
    # IOA地址别名
    # ==================================================================

    def _on_manage_aliases(self):
        """打开IOA别名管理对话框"""
        IOAAliasDialog(self).exec_()
        # 刷新当前显示
        if self.all_frames:
            self._populate_table()
            if self._current_frame_idx < len(self.all_frames):
                self._populate_tree(self._current_frame_idx)

    def _open_generator(self):
        """打开帧生成器对话框"""
        from gui.generator_dialog import GeneratorDialog
        dialog = GeneratorDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            pass

    def closeEvent(self, event):
        """关闭窗口时安全停止后台线程"""
        if self._parse_worker and self._parse_worker.isRunning():
            self._parse_worker.cancel()
            if not self._parse_worker.wait(2000):
                self._parse_worker.terminate()
        event.accept()

    def _on_about(self):
        """关于对话框"""
        AboutDialog(self.APP_TITLE, self.APP_VERSION).exec_()

    def _open_history_manager(self):
        """打开历史报文管理"""
        from gui.history_dialog import HistoryDialog
        dialog = HistoryDialog(self)
        if dialog.exec_() == QDialog.Accepted and dialog.selected_session:
            session = dialog.selected_session
            self.hex_input.setPlainText(session.get('input_text', ''))
            mode_str = session.get('parse_mode', 'normal')
            from iec104_parser import ParseMode
            mode_map = {'normal': ParseMode.NORMAL, 'xn3000': ParseMode.XN3000, 'xn8000': ParseMode.XN8000}
            self.parse_mode = mode_map.get(mode_str, ParseMode.NORMAL)
            idx = self.mode_combo.findData(self.parse_mode)
            if idx >= 0:
                self.mode_combo.setCurrentIndex(idx)
            self._on_parse()

    def _on_filter(self):
        """应用过滤"""
        signal = self.filter_signal.currentData()
        type_id = self.filter_type.currentData()
        cot = self.filter_cot.currentData()
        addr_text = self.filter_addr.text().strip()

        filtered = self.all_frames
        if signal:
            filtered = [f for f in filtered if f.signal_label == signal]
        if type_id != -1 and type_id is not None:
            filtered = [f for f in filtered if f.asdu and f.asdu.type_id == type_id]
        if cot != -1 and cot is not None:
            filtered = [f for f in filtered
                        if f.asdu and f.asdu.cot_cause == cot]
        if addr_text:
            try:
                addr = int(addr_text)
                filtered = [f for f in filtered
                            if f.asdu and f.asdu.common_address == addr]
            except ValueError:
                pass

        # IOA 范围过滤
        try:
            ioa_from = int(self.ioa_from_input.text(), 0) if self.ioa_from_input.text() else None
            ioa_to = int(self.ioa_to_input.text(), 0) if self.ioa_to_input.text() else None
        except ValueError:
            ioa_from = ioa_to = None

        if ioa_from is not None or ioa_to is not None:
            def ioa_in_range(elem):
                if ioa_from is not None and ioa_to is not None:
                    return ioa_from <= elem.ioa <= ioa_to
                elif ioa_from is not None:
                    return elem.ioa >= ioa_from
                elif ioa_to is not None:
                    return elem.ioa <= ioa_to
                return True
            filtered = [f for f in filtered
                        if f.asdu and f.asdu.information_elements
                        and any(ioa_in_range(e) for e in f.asdu.information_elements)]

        total_count = len(self.all_frames)
        saved = self.all_frames
        self.all_frames = filtered
        self._populate_table()
        self.all_frames = saved
        self.statusbar.showMessage(
            f"过滤结果: {len(filtered)}/{total_count} 条报文", 3000)

    def _on_filter_reset(self):
        """重置过滤"""
        self.filter_signal.setCurrentIndex(0)
        self.filter_type.setCurrentIndex(0)
        self.filter_cot.setCurrentIndex(0)
        self.filter_addr.clear()
        self.ioa_from_input.clear()
        self.ioa_to_input.clear()
        self._populate_table()
        self.statusbar.showMessage("过滤已重置", 2000)
