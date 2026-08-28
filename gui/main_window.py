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
    QDialogButtonBox, QFormLayout, QInputDialog,
    QStyledItemDelegate, QStyleOptionViewItem, QStyle
)
from PyQt5.QtCore import Qt, QTimer, QSettings, QSize, QPoint
from PyQt5.QtGui import (QFont, QColor, QTextCursor, QIcon, QPalette,
                         QPainter, QBrush, QPen, QPixmap, QFontMetrics)

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
from gui.style import LIGHT_QSS
# 拆分出去的模块，这里重新导出以保持 `from gui.main_window import ...` 既有引用不变
from gui.icons import create_app_icon, _doc_icon, _arrow_icon, _ICON_PRIMARY
from gui.parse_worker import CaptureWorker, ParseWorker

logger = logging.getLogger(__name__)

# 文件导入大小上限：防止大文件把界面/内存卡死
MAX_TEXT_IMPORT_BYTES = 20 * 1024 * 1024   # 文本类导入上限 20MB
MAX_BIN_IMPORT_BYTES = 8 * 1024 * 1024     # 二进制转hex导入上限 8MB

# UI 设置持久化（QSettings 注册表键前缀 ui/）
_SETTINGS_ORG = "SWT"
_SETTINGS_APP = "IEC104 Parser"

# 过滤按钮样式：空闲蓝色 / 生效中橙色（让用户一眼看出过滤还在起作用）
FILTER_BTN_STYLE_IDLE = (
    "QPushButton { background-color: #2196F3; color: white; font-weight: bold; "
    "padding: 3px 12px; border-radius: 3px; border: none; }"
    "QPushButton:hover { background-color: #1976D2; }"
)
FILTER_BTN_STYLE_ACTIVE = (
    "QPushButton { background-color: #FF9800; color: white; font-weight: bold; "
    "padding: 3px 12px; border-radius: 3px; border: none; }"
    "QPushButton:hover { background-color: #F57C00; }"
)

# 授权状态常驻按钮：状态色
LICENSE_COLOR_OK = "#2E7D32"
LICENSE_COLOR_WARN = "#FF9800"    # 剩余 ≤7 天 / 未激活
LICENSE_COLOR_DANGER = "#F44336"  # 剩余 ≤3 天 / 已到期



class FrameCellDelegate(QStyledItemDelegate):
    """报文列表虚拟绘制器。

    不再为每条/每列创建 QTableWidgetItem，而是在每个单元格绘制时实时从
    MainWindow.all_frames 与 _view_order（视图行→帧索引）取值。
    万帧级数据下填充开销从 O(N×12) 次 item 构造降至 O(1)，仅重绘可见区。
    """

    def __init__(self, window, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._w = window

    def paint(self, painter, option, index):
        w = self._w
        if w is None or not w.all_frames or not w._view_order:
            return super().paint(painter, option, index)
        vi = index.row()
        if not (0 <= vi < len(w._view_order)):
            return super().paint(painter, option, index)
        fi = w._view_order[vi]
        if not (0 <= fi < len(w.all_frames)):
            return super().paint(painter, option, index)

        cell = w._cell(vi, index.column())
        painter.save()
        rect = option.rect
        if option.state & QStyle.State_Selected:
            painter.fillRect(rect, option.palette.highlight())
        else:
            painter.fillRect(rect, cell["bg"])
        fg = cell.get("fg") or (
            QColor("#E0E0E0") if w._dark_theme else QColor("#000000"))
        painter.setPen(fg)
        font = painter.font()
        font.setBold(cell.get("bold", False))
        painter.setFont(font)
        painter.drawText(rect.adjusted(4, 0, -4, 0),
                         int(cell.get("align", Qt.AlignLeft | Qt.AlignVCenter)),
                         cell["text"])
        painter.restore()

    def sizeHint(self, option, index):
        # 沿用默认行高；列宽由“估算/用户拖拽”决定，无需按内容精确量宽
        return super().sizeHint(option, index)


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
        self._frame_input_lines = []
        self._current_frame_idx = 0
        self.capture_worker: Optional[CaptureWorker] = None
        self.parse_mode: ParseMode = ParseMode.NORMAL
        self._dark_theme = False
        self._current_theme = "light"
        self._auto_detect = True
        self._is_activated = False
        self._is_license_expired = False

        # 后台解析相关
        self._parse_worker: Optional[ParseWorker] = None
        self._is_parsing: bool = False
        self._parse_cancelled: bool = False

        # 过滤生效状态（防止用户忘记列表处于过滤中）
        self._filter_active: bool = False
        self._filter_counts: tuple = None  # (保留条数, 总条数)

        # 导入/导出记忆的目录
        self._last_dir: str = ""
        # 用户保存过的列宽偏好（关闭时采集，下次启动直接套用）
        self._saved_col_widths: Optional[list] = None

        # 报文列表虚拟化状态（见 FrameCellDelegate / _cell / _on_header_sort）
        self._view_order: list = []      # 视图行 → all_frames 索引（当前显示顺序）
        self._frame_to_row: dict = {}    # all_frames 索引 → 视图行（0(1) 反查）
        self._base_order: list = []      # 非搜索时的完整显示顺序（搜索快照用）
        self._pre_search_order: list = []  # 发起搜索前保存，清空搜索时还原
        self._sort_column: int = -1
        self._sort_order: int = Qt.AscendingOrder
        self._frame_intervals: list = []

        self.setWindowTitle(self.APP_TITLE)
        self.resize(1320, 840)
        self.setMinimumSize(900, 640)

        self._setup_icon()
        self.setAcceptDrops(True)

        self._setup_ui()
        # 浅色基础样式表：统一各控件圆角/内边距/焦点环（深色主题切换时整体替换）
        self.setStyleSheet(LIGHT_QSS)
        self._setup_menu()
        self._setup_toolbar()
        self._setup_statusbar()

        self._check_license()
        # 授权已到期的提示放在窗口显示后触发（singleShot 在事件循环启动后执行）
        QTimer.singleShot(0, self._may_prompt_expired)
        self._update_status_stats()

        self.hex_input.setPlainText(
            "# 在此粘贴 IEC104 十六进制报文\n"
            "# 支持格式: 68 04 01 00 0E 00  或  680401000E00\n"
            "# 每行一条报文，以 # 开头的行为注释\n\n"
        )

        # 恢复上次关闭时的界面状态（窗口大小/分栏/主题/列宽/目录）
        self._load_ui_settings()

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
        self.input_tabs.currentChanged.connect(self._on_tab_changed)
        hex_layout.addWidget(self.input_tabs)

        # 创建第一个输入标签页（启动初始化，不走激活校验）
        self._input_tab_list = []  # [(tab_widget, hex_input), ...]
        self._tab_data = {}  # {tab_index: {"frames": [...], "input_lines": [...], "bookmarks": set()}}
        self._current_tab_index = 0
        self._bookmarks = set()  # Set of frame indices
        self._add_input_tab(require_license=False)

        # 输入操作按钮
        btn_layout = QHBoxLayout()
        self.btn_parse = QPushButton("▶ 解析报文")
        self.btn_parse.clicked.connect(self._on_parse)
        self.btn_parse.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; "
            "font-weight: bold; padding: 6px 16px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background-color: #1976D2; }"
        )
        self.btn_clear = QPushButton("清空")
        self.btn_clear.clicked.connect(self._on_clear)
        self.btn_import_file = QPushButton("📁 导入文件")
        self.btn_import_file.clicked.connect(self._on_import_file)
        self.btn_import_file.setStyleSheet(
            "QPushButton { border: 1px solid #ccc; padding: 5px 12px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #e3f2fd; border-color: #2196F3; }"
        )

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
        self.mode_combo.addItem("自动检测", ParseMode.NORMAL)
        self.mode_combo.addItem("普通模式", ParseMode.NORMAL)
        self.mode_combo.addItem("XN3000 模式", ParseMode.XN3000)
        self.mode_combo.addItem("XN8000 模式", ParseMode.XN8000)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.mode_combo.setToolTip("自动: 自动识别格式\n普通: 标准十六进制格式\nXN3000: 带时间戳 RX/TX 格式\nXN8000: 带时间戳 send/recv 格式")
        mode_layout.addWidget(self.mode_combo)
        mode_layout.addStretch()
        left_layout.addWidget(mode_group)

        # 过滤选项 - 2行网格布局
        filter_group = QGroupBox("过滤条件")
        filter_grid = QGridLayout(filter_group)
        filter_grid.setSpacing(4)

        # 第1行: 信号类型 | 类型标识 | 传送原因 | 地址
        row = 0
        filter_grid.addWidget(QLabel("信号:"), row, 0)
        self.filter_signal = QComboBox()
        self.filter_signal.addItem("全部", "")
        self.filter_signal.addItem("遥信", "遥信")
        self.filter_signal.addItem("遥测", "遥测")
        self.filter_signal.addItem("遥控", "遥控")
        self.filter_signal.addItem("遥调", "遥调")
        self.filter_signal.addItem("遥脉", "遥脉")
        filter_grid.addWidget(self.filter_signal, row, 1)

        filter_grid.addWidget(QLabel("类型:"), row, 2)
        self.filter_type = QComboBox()
        self.filter_type.setMinimumWidth(120)
        self.filter_type.addItem("全部", -1)
        for tid in sorted(TypeID):
            desc = TypeID.get_description(tid.value)
            self.filter_type.addItem(f"{tid.value}: {tid.name} ({desc})", tid.value)
        filter_grid.addWidget(self.filter_type, row, 3, 1, 3)

        filter_grid.addWidget(QLabel("COT:"), row, 6)
        self.filter_cot = QComboBox()
        self.filter_cot.setMinimumWidth(100)
        self.filter_cot.addItem("全部", -1)
        for cot in range(1, 42):
            self.filter_cot.addItem(f"{cot}: {COT.get_description(cot)}", cot)
        filter_grid.addWidget(self.filter_cot, row, 7, 1, 3)

        filter_grid.addWidget(QLabel("地址:"), row, 10)
        self.filter_addr = QLineEdit()
        self.filter_addr.setPlaceholderText("CA/0x1A")
        self.filter_addr.setMaximumWidth(90)
        self.filter_addr.setToolTip("公共地址(ASDU地址)，支持十进制或 0x 十六进制")
        filter_grid.addWidget(self.filter_addr, row, 11)

        # 第2行: 方向 | 时间范围 | IOA范围 | 筛选/重置
        row = 1
        filter_grid.addWidget(QLabel("方向:"), row, 0)
        self.filter_direction = QComboBox()
        self.filter_direction.addItem("全部", "")
        self.filter_direction.addItem("主→从", "主站→子站")
        self.filter_direction.addItem("从→主", "子站→主站")
        filter_grid.addWidget(self.filter_direction, row, 1)

        filter_grid.addWidget(QLabel("时间:"), row, 2)
        time_layout = QHBoxLayout()
        time_layout.setSpacing(2)
        self.filter_time_from = QLineEdit()
        self.filter_time_from.setPlaceholderText("HH:MM:SS")
        self.filter_time_from.setMaximumWidth(90)
        time_layout.addWidget(self.filter_time_from)
        time_layout.addWidget(QLabel("~"))
        self.filter_time_to = QLineEdit()
        self.filter_time_to.setPlaceholderText("HH:MM:SS")
        self.filter_time_to.setMaximumWidth(90)
        time_layout.addWidget(self.filter_time_to)
        filter_grid.addLayout(time_layout, row, 3, 1, 3)

        filter_grid.addWidget(QLabel("IOA:"), row, 6)
        ioa_layout = QHBoxLayout()
        ioa_layout.setSpacing(2)
        self.ioa_from_input = QLineEdit()
        self.ioa_from_input.setPlaceholderText("起始")
        self.ioa_from_input.setMaximumWidth(70)
        self.ioa_from_input.setToolTip("IOA起始地址，支持十进制或0x前缀")
        ioa_layout.addWidget(self.ioa_from_input)
        ioa_layout.addWidget(QLabel("~"))
        self.ioa_to_input = QLineEdit()
        self.ioa_to_input.setPlaceholderText("结束")
        self.ioa_to_input.setMaximumWidth(70)
        self.ioa_to_input.setToolTip("IOA结束地址")
        ioa_layout.addWidget(self.ioa_to_input)
        filter_grid.addLayout(ioa_layout, row, 7, 1, 3)

        self.filter_btn = QPushButton("筛选")
        self.filter_btn.clicked.connect(self._on_filter)
        self.filter_btn.setStyleSheet(FILTER_BTN_STYLE_IDLE)
        self.filter_btn.setToolTip("按条件过滤报文列表")
        filter_grid.addWidget(self.filter_btn, row, 10)
        self.filter_reset = QPushButton("重置")
        self.filter_reset.clicked.connect(self._on_filter_reset)
        filter_grid.addWidget(self.filter_reset, row, 11)

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
        self.tree_widget.setColumnWidth(0, 220)
        self.tree_widget.setColumnWidth(1, 280)
        self.tree_widget.setColumnWidth(2, 300)
        self.tree_widget.itemSelectionChanged.connect(self._on_tree_select)
        # 空状态提示
        placeholder = QTreeWidgetItem(self.tree_widget)
        placeholder.setText(0, "请在左侧粘贴报文后点击「解析报文」或拖放文件到窗口")
        placeholder.setFlags(Qt.NoItemFlags)
        placeholder.setForeground(0, QColor("#999"))
        self.result_tabs.addTab(self.tree_widget, "解析详情")

        # 标签页2: 报文列表 (带搜索栏)
        table_container = QWidget()
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(2)

        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("🔍 搜索:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入关键字或正则搜索...")
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._do_search)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        self.search_input.setClearButtonEnabled(True)
        search_layout.addWidget(self.search_input)
        self.lbl_search_count = QLabel("")
        self.lbl_search_count.setStyleSheet("color: #888; font-size: 9pt;")
        search_layout.addWidget(self.lbl_search_count)
        table_layout.addLayout(search_layout)

        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(12)
        self.table_widget.setHorizontalHeaderLabels(
            ["序号", "原始时间", "方向", "信号类型", "类型标识", "传送原因", "公共地址", "对象数", "值", "描述", "输入行", "时间间隔"]
        )
        self.table_widget.setAlternatingRowColors(True)
        self.table_widget.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_widget.horizontalHeader().setStretchLastSection(True)
        self.table_widget.setEditTriggers(QTableWidget.NoEditTriggers)
        # 虚拟绘制：不创建单元格对象，由 FrameCellDelegate 实时绘制（万帧级性能）
        self._cell_delegate = FrameCellDelegate(self)
        self.table_widget.setItemDelegate(self._cell_delegate)
        self.table_widget.horizontalHeader().sectionClicked.connect(
            self._on_header_sort)
        self.table_widget.cellClicked.connect(self._on_table_click)
        self.table_widget.cellDoubleClicked.connect(self._on_table_double_click)
        self.table_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_widget.customContextMenuRequested.connect(self._on_table_context_menu)
        self.table_widget.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_widget.horizontalHeader().customContextMenuRequested.connect(
            self._on_header_context_menu)
        # 键盘导航
        self.table_widget.keyPressEvent = self._table_key_press
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
        self.splitter.setSizes([430, 870])
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setHandleWidth(6)
        self.left_panel.setMinimumWidth(250)
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
        view_menu.addAction("定位原始报文(&O)", lambda: self._locate_original_message(self.table_widget.currentRow()), "F2")
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
        view_menu.addSeparator()
        view_menu.addAction("下一个书签", self._goto_next_bookmark, "Ctrl+Down")
        view_menu.addAction("上一个书签", self._goto_prev_bookmark, "Ctrl+Up")

        tools_menu = menubar.addMenu("工具(&T)")
        tools_menu.addAction("报文对比(&C)...", self._open_compare, "Ctrl+Shift+M")
        tools_menu.addAction("通信模拟(&U)...", self._open_simulator, "Ctrl+M")
        tools_menu.addAction("帧生成器(&G)...", self._open_generator, "Ctrl+G")
        tools_menu.addAction("历史报文管理(&H)...", self._open_history_manager, "Ctrl+Shift+R")
        tools_menu.addSeparator()
        tools_menu.addAction("序号检查(&S)...", self._on_check_sequence, "Ctrl+Shift+S")
        tools_menu.addAction("协议合规检查(&R)...", self._on_check_compliance)
        tools_menu.addAction("报文配对分析(&P)...", self._on_find_pairs)
        tools_menu.addSeparator()
        tools_menu.addAction("IOA地址别名(&I)...", self._on_manage_aliases, "Ctrl+Shift+I")

        help_menu = menubar.addMenu("帮助(&H)")
        help_menu.addAction("软件激活(&A)...", self._open_license_dialog)
        help_menu.addSeparator()
        help_menu.addAction("版本信息(&V)...", self._on_about)
        help_menu.addAction("IOA 地址说明(&I)...", self._on_ioa_help)

    def _setup_toolbar(self):
        """设置工具栏"""
        toolbar = QToolBar("主工具栏")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(18, 18))
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)

        act_parse = toolbar.addAction(self._vector_icon("parse"), "解析", self._on_parse)
        act_parse.setToolTip("解析报文 (F5)")
        act_import = toolbar.addAction(self._vector_icon("import"), "导入", self._on_import_file)
        act_import.setToolTip("导入文件 (Ctrl+I)")
        toolbar.addSeparator()
        act_gen = toolbar.addAction(self._vector_icon("gen"), "生成报文", self._open_generator)
        act_gen.setToolTip("帧生成器 (Ctrl+G)")
        act_sim = toolbar.addAction(self._vector_icon("sim"), "通信模拟", self._open_simulator)
        act_sim.setToolTip("IEC104 通信模拟器 (Ctrl+M)")
        toolbar.addSeparator()
        act_csv = toolbar.addAction(_doc_icon("CSV", "#43A047"), "CSV", self._on_export_csv)
        act_csv.setToolTip("导出CSV (Ctrl+Shift+C)")
        act_json = toolbar.addAction(_doc_icon("JSON", "#FB8C00"), "JSON", self._on_export_json)
        act_json.setToolTip("导出JSON (Ctrl+Shift+J)")
        toolbar.addSeparator()
        self._left_panel_visible = True
        self._toggle_input_action = toolbar.addAction(self._vector_icon("hideinput"),
                                                      "◀ 隐藏输入")
        self._toggle_input_action.triggered.connect(self._toggle_input_panel)
        self._toggle_input_action.setToolTip("折叠/展开原始报文输入面板 (Ctrl+B)")
        toolbar.addSeparator()
        act_about = toolbar.addAction(self._vector_icon("about"), "关于", self._on_about)
        act_about.setToolTip("关于本软件")

    def _setup_icon(self):
        """设置应用图标（渐变精修版，同时注册为全局图标）"""
        icon = create_app_icon()
        self.setWindowIcon(icon)
        QApplication.setWindowIcon(icon)

    def _vector_icon(self, name: str) -> QIcon:
        """按名称绘制工具栏矢量小图标（蓝 #1565C0 线条风格）"""
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(_ICON_PRIMARY, 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)

        if name == "parse":          # ▶ 解析
            p.setBrush(QBrush(_ICON_PRIMARY))
            p.setPen(Qt.NoPen)
            p.drawPolygon([QPoint(22, 16), QPoint(22, 48), QPoint(50, 32)])
        elif name == "import":       # 文件夹 导入
            p.setBrush(QColor("#90CAF9"))
            p.drawRoundedRect(8, 24, 48, 28, 5, 5)
            p.drawRoundedRect(8, 15, 22, 12, 3, 3)
            p.setBrush(Qt.NoBrush)
            p.setPen(pen)
            p.drawRoundedRect(8, 24, 48, 28, 5, 5)
        elif name == "gen":          # ✨ 生成（魔棒+火花）
            p.drawLine(16, 50, 42, 24)
            p.drawLine(46, 14, 46, 20)
            p.drawLine(43, 17, 49, 17)
            p.drawLine(54, 24, 54, 30)
            p.drawLine(51, 27, 57, 27)
        elif name == "sim":          # ⇄ 通信模拟
            p.drawLine(10, 26, 46, 26)
            p.drawLine(38, 18, 48, 26)
            p.drawLine(38, 34, 48, 26)
            p.drawLine(54, 40, 18, 40)
            p.drawLine(26, 32, 16, 40)
            p.drawLine(26, 48, 16, 40)
        elif name == "hideinput":    # ◀ 折叠输入面板
            p.drawRect(10, 14, 44, 36)
            p.setBrush(QBrush(_ICON_PRIMARY))
            p.drawRect(10, 14, 10, 36)
            p.drawLine(38, 25, 29, 32)
            p.drawLine(29, 32, 38, 39)
        elif name == "about":        # ⓘ 关于
            p.drawEllipse(11, 11, 42, 42)
            p.drawPoint(32, 21)
            p.drawLine(32, 21, 32, 22)
            p.drawLine(32, 30, 32, 45)

        p.end()
        return QIcon(pm)

    def _setup_statusbar(self):
        """设置状态栏"""
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)

        self.lbl_stats = QLabel("就绪")
        self.statusbar.addWidget(self.lbl_stats, 1)

        # 授权状态常驻按钮（点击打开激活对话框；颜色随时效变化）
        self.btn_license_state = QPushButton("")
        self.btn_license_state.setFlat(True)
        self.btn_license_state.setCursor(Qt.PointingHandCursor)
        self.btn_license_state.clicked.connect(self._open_license_dialog)
        self.btn_license_state.setToolTip("授权状态，点击查看详情 / 激活")
        self.statusbar.addPermanentWidget(self.btn_license_state)

        # 进度条（解析时显示）
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(200)
        self.progress_bar.setMaximumHeight(18)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%v/%m (%p%)")
        self.progress_bar.setVisible(False)
        self.statusbar.addWidget(self.progress_bar)

        # 取消按钮（解析时显示）
        self.btn_cancel_parse = QPushButton("取消解析")
        self.btn_cancel_parse.setMaximumHeight(20)
        self.btn_cancel_parse.setStyleSheet(
            "QPushButton { background-color: #f44336; color: white; padding: 2px 8px; border-radius: 3px; border: none; }"
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
        """更新状态栏统计（基于 self.all_frames，过滤时反映过滤结果）"""
        frames = self.all_frames
        total = len(frames)
        valid = sum(1 for f in frames if not f.error)
        i_frames = sum(1 for f in frames if f.apci and f.apci.frame_type == "I-format")
        s_frames = sum(1 for f in frames if f.apci and f.apci.frame_type == "S-format")
        u_frames = sum(1 for f in frames if f.apci and f.apci.frame_type == "U-format")
        yx_count = sum(1 for f in frames if f.signal_type == SignalType.YX)
        yc_count = sum(1 for f in frames if f.signal_type == SignalType.YC)
        yk_count = sum(1 for f in frames if f.signal_type == SignalType.YK)
        yt_count = sum(1 for f in frames if f.signal_type == SignalType.YT)
        ym_count = sum(1 for f in frames if f.signal_type == SignalType.YM)
        master_send = sum(1 for f in frames if f.direction == "主站→子站")
        slave_send = sum(1 for f in frames if f.direction == "子站→主站")

        # 状态栏简要信息（过滤中时前置常驻提示，防止误以为数据缺失）
        prefix = ""
        if getattr(self, '_filter_active', False) and self._filter_counts:
            kept, tot = self._filter_counts
            prefix = f"🔍 过滤中 {kept}/{tot} | "
        summary = f"{prefix}共 {total} 帧 | I:{i_frames} S:{s_frames} U:{u_frames}"
        if total - valid > 0:
            summary += f" | ❌ {total - valid} 错误"
        self.lbl_stats.setText(summary)

        # Tooltip 显示完整统计
        detail = (
            f"总报文: {total}  有效: {valid}  无效: {total - valid}\n"
            f"I帧: {i_frames}  S帧: {s_frames}  U帧: {u_frames}\n"
            f"主→从: {master_send}  从→主: {slave_send}\n"
            f"遥信: {yx_count}  遥测: {yc_count}  遥控: {yk_count}  遥调: {yt_count}  遥脉: {ym_count}"
        )
        self.lbl_stats.setToolTip(detail)
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
        """解析全部报文 - 使用后台线程避免UI阻塞"""
        raw_text = self.hex_input.toPlainText()

        if not raw_text.strip() or raw_text.strip().startswith("# 在此粘贴"):
            QMessageBox.information(self, "提示", "请输入十六进制报文数据")
            return

        self._start_parse(raw_text)

    def _on_parse_text(self, text: str):
        """解析指定文本（用于选中内容解析）"""
        if not text.strip():
            return
        self._start_parse(text)

    def _start_parse(self, raw_text: str):
        """启动解析流程"""

        # 上一次解析未结束时：请求取消并等待其收尾，避免双线程并发写结果
        if self._parse_worker and self._parse_worker.isRunning():
            self._parse_worker.cancel()
            try:
                self._parse_worker.parse_finished.disconnect(self._on_parse_finished)
                self._parse_worker.progress_updated.disconnect(self._on_parse_progress)
                self._parse_worker.parse_error.disconnect(self._on_parse_error)
            except TypeError:
                pass  # 信号本就未连接
            if not self._parse_worker.wait(2000):
                self.statusbar.showMessage("上一次解析仍在结束中，请稍候再试", 3000)
                return

        # 清除旧数据
        self.parser.clear()
        self.all_frames.clear()
        self._byte_position_map = []
        self._frame_byte_offset = []
        self._frame_input_lines = []
        self.tree_widget.clear()
        self.table_widget.setRowCount(0)
        self.hex_view.clear()

        # 自动检测模式
        if self._auto_detect:
            detected = self._auto_detect_mode(raw_text)
            if detected != ParseMode.NORMAL:
                self.parse_mode = detected
                idx = self.mode_combo.findData(detected)
                if idx >= 0:
                    self.mode_combo.blockSignals(True)
                    self.mode_combo.setCurrentIndex(idx)
                    self.mode_combo.blockSignals(False)
                self.statusbar.showMessage(f"自动检测到 {detected.value.upper()} 模式", 2000)

        # 启动后台解析
        self._is_parsing = True
        # 未激活版本：每次仅解析第 1 条报文（激活后不限制）
        max_frames = 0 if self._is_activated else 1
        self._parse_worker = ParseWorker(self.parser, raw_text, self.parse_mode,
                                         max_frames=max_frames)

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
        self._bookmarks.clear()
        self._tab_data.pop(self._current_tab_index, None)
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
            self.btn_cancel_parse.setEnabled(True)

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

        # 应用输入行颜色（仅普通模式）
        if self.parse_mode == ParseMode.NORMAL:
            self._apply_input_line_colors()

        # 更新UI
        self._last_send_seq = -1
        self._analyze_time_intervals()
        self._populate_table()
        self._update_status_stats()

        # 保存到当前标签页数据
        self._save_current_tab_state()

        # 恢复按钮状态
        self._set_parsing_state(False)

        # 统计信息（用户取消时明确提示为部分结果；未激活截断时提示单条限制）
        limit_hit = getattr(self._parse_worker, 'hit_frame_limit', False)
        error_count = sum(1 for f in self.all_frames if f.error)
        if self._parse_cancelled:
            self._parse_cancelled = False
            self.statusbar.showMessage(
                f"已取消: 显示部分结果（{len(self.all_frames)} 条报文），重新解析可获取完整结果", 5000)
        elif limit_hit:
            self.statusbar.showMessage(
                "未激活: 每次仅解析第 1 条报文，其余已被忽略", 5000)
        else:
            self.statusbar.showMessage(
                f"解析完成: {len(self.all_frames)} 条报文, {error_count} 条失败", 5000)

        # 未激活被截断 → 弹窗说明并提供激活入口
        if limit_hit:
            box = QMessageBox(self)
            box.setWindowTitle("功能限制")
            box.setIcon(QMessageBox.Information)
            box.setText(
                "未激活版本每次只能解析 1 条报文，本次输入中的其余报文已被忽略。\n\n"
                "激活后可一次性解析全部报文，并解锁文件导入、导出等功能。")
            box.addButton("打开激活窗口", QMessageBox.ActionRole)
            box.addButton("知道了", QMessageBox.RejectRole)
            box.exec_()
            if box.buttonRole(box.clickedButton()) == QMessageBox.ActionRole:
                self._open_license_dialog()

        # 自动切换到报文列表标签
        self.result_tabs.setCurrentIndex(1)

    def _on_parse_error(self, error_msg: str):
        """解析错误处理"""
        self._is_parsing = False
        self._set_parsing_state(False)
        self.statusbar.showMessage(f"解析失败: {error_msg}", 5000)
        QMessageBox.critical(self, "解析错误", f"解析过程中发生错误:\n\n{error_msg}")

    def _analyze_time_intervals(self):
        """分析帧间时间间隔"""
        self._frame_intervals = []
        for i, frame in enumerate(self.all_frames):
            if i == 0:
                self._frame_intervals.append(0)
                continue
            ts_current = frame.original_timestamp
            ts_previous = self.all_frames[i-1].original_timestamp
            if ts_current and ts_previous:
                try:
                    def parse_ts(ts):
                        parts = ts.replace(',', '.').split(':')
                        if len(parts) == 3:
                            h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
                            return h * 3600 + m * 60 + s
                        return None
                    curr = parse_ts(ts_current)
                    prev = parse_ts(ts_previous)
                    if curr is not None and prev is not None:
                        interval = curr - prev
                        if interval < 0:
                            interval += 86400
                        self._frame_intervals.append(round(interval * 1000, 1))
                        continue
                except Exception:
                    pass
            self._frame_intervals.append(None)

    def _on_cancel_parse(self):
        """取消解析"""
        if self._parse_worker and self._parse_worker.isRunning():
            self._parse_cancelled = True
            self._parse_worker.cancel()
            self.btn_cancel_parse.setEnabled(False)
            self.statusbar.showMessage("正在取消解析...", 0)
            # 线程在检查点自行退出并回调 _on_parse_finished 呈现部分结果，
            # 不使用 terminate()（强制杀线程可能导致 Qt 内部状态损坏）

    def _on_mode_changed(self, index: int):
        """解析模式变更"""
        self.parse_mode = self.mode_combo.currentData()
        self._auto_detect = (index == 0)
        mode_name = self.mode_combo.currentText()
        self.statusbar.showMessage(f"已切换到 {mode_name}", 2000)

    def _auto_detect_mode(self, text: str) -> ParseMode:
        """自动检测输入格式"""
        if re.search(r'\d{2}:\d{2}:\d{2}\.\d{3}\s+(RX|TX):', text):
            return ParseMode.XN3000
        if re.search(r'\d+-\s*\d{2}:\d{2}:\d{2}\.\d{3}\s+(send|recv):', text):
            return ParseMode.XN8000
        if re.search(r'\b[0-9A-Fa-f]{2}\s+\.', text):
            return ParseMode.XN3000
        return ParseMode.NORMAL

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
        # 重新应用特殊按钮样式
        self.btn_parse.setStyleSheet(
            "QPushButton { background-color: #1976D2; color: white; "
            "font-weight: bold; padding: 6px 16px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background-color: #1565C0; }"
        )
        self.btn_cancel_parse.setStyleSheet(
            "QPushButton { background-color: #c62828; color: white; padding: 2px 8px; border-radius: 3px; border: none; }"
            "QPushButton:hover { background-color: #b71c1c; }"
        )

    def _apply_light_theme(self):
        """应用浅色主题（恢复基础样式表）"""
        self.setStyleSheet(LIGHT_QSS)
        # 重新应用特殊按钮样式
        self.btn_parse.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; "
            "font-weight: bold; padding: 6px 16px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background-color: #1976D2; }"
        )
        self.btn_cancel_parse.setStyleSheet(
            "QPushButton { background-color: #f44336; color: white; padding: 2px 8px; border-radius: 3px; border: none; }"
            "QPushButton:hover { background-color: #d32f2f; }"
        )

    def _apply_theme_to_dialog(self, dialog):
        """将当前主题应用到子对话框（浅色为统一基础表，深色为完整暗色表）"""
        dialog.setStyleSheet(self.styleSheet())

    # ==================================================================
    # 多标签页输入
    # ==================================================================

    def _add_input_tab(self, title=None, content=None, require_license=True):
        """添加新的输入标签页"""
        # 菜单/快捷键新建标签页属于受限功能；启动时的首个标签页绕过校验
        if require_license and not self._require_license():
            return None
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
        # 清理标签页数据并重建索引
        old_data = dict(self._tab_data)
        self._tab_data.clear()
        for old_idx, data in old_data.items():
            if old_idx < index:
                self._tab_data[old_idx] = data
            elif old_idx > index:
                self._tab_data[old_idx - 1] = data
        if self._input_tab_list:
            idx = min(index, len(self._input_tab_list) - 1)
            self.hex_input = self._input_tab_list[idx][1]
            self.input_tabs.setCurrentIndex(idx)

    def _on_tab_changed(self, index: int):
        """标签页切换时保存当前状态并加载目标标签页状态"""
        if not (0 <= index < len(self._input_tab_list)):
            return

        # 保存当前标签页的解析结果
        self._save_current_tab_state()

        # 切换到新标签页
        self._current_tab_index = index
        self.hex_input = self._input_tab_list[index][1]

        # 加载目标标签页的解析结果
        self._load_tab_state(index)

    def _save_current_tab_state(self):
        """保存当前标签页的解析结果"""
        idx = self._current_tab_index
        self._tab_data[idx] = {
            "frames": list(self.all_frames),
            "input_lines": list(getattr(self, '_frame_input_lines', [])),
            "bookmarks": set(self._bookmarks),
        }

    def _load_tab_state(self, index: int):
        """加载指定标签页的解析结果"""
        if index in self._tab_data:
            data = self._tab_data[index]
            self.all_frames = data["frames"]
            self._frame_input_lines = data["input_lines"]
            self._bookmarks = data["bookmarks"]
            self._populate_table()
            self._update_status_stats()
        else:
            # 新标签页，清空结果
            self.all_frames = []
            self._frame_input_lines = []
            self._bookmarks = set()
            self.tree_widget.clear()
            self.table_widget.setRowCount(0)
            self.hex_view.clear()
            self._update_status_stats()

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
        """导出CSV - 支持筛选后导出"""
        if not self._require_license():
            return
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有可导出的数据")
            return

        visible_rows = []
        for i in range(self.table_widget.rowCount()):
            if not self.table_widget.isRowHidden(i):
                visible_rows.append(self._frame_idx_at_row(i))

        if not visible_rows:
            QMessageBox.information(self, "提示", "没有可见的数据")
            return

        default_name = f"iec104_parsed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        if self._last_dir:
            default_name = os.path.join(self._last_dir, default_name)
        path, _ = QFileDialog.getSaveFileName(self, "导出CSV", default_name,
                                                "CSV文件 (*.csv);;所有文件 (*)")
        if not path:
            return
        self._last_dir = os.path.dirname(path) or self._last_dir
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["序号", "原始时间戳", "方向", "信号类型", "帧类型", "类型标识", "类型描述",
                                 "传送原因", "传送原因描述", "公共地址", "对象数",
                                 "信息对象地址", "值", "时标", "错误"])
                for row_idx in visible_rows:
                    if row_idx < len(self.all_frames):
                        frame = self.all_frames[row_idx]
                        if frame.asdu and frame.asdu.information_elements:
                            for elem in frame.asdu.information_elements:
                                ts = elem.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] \
                                    if elem.timestamp else ""
                                writer.writerow([
                                    frame.index, frame.original_timestamp or "", frame.direction,
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
                                frame.index, frame.original_timestamp or "",
                                frame.direction if frame.direction else "",
                                "", frame.apci.frame_type if frame.apci else "",
                                "", "", "", "", "", "",
                                "", frame.description if frame.description else "",
                                "", frame.error
                            ])
            export_count = len(visible_rows)
            total_count = len(self.all_frames)
            msg = f"已导出 {export_count}/{total_count} 条记录" if export_count < total_count else f"已导出 {export_count} 条记录"
            self.statusbar.showMessage(f"{msg}: {os.path.basename(path)}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _on_export_json(self):
        """导出JSON - 支持筛选后导出"""
        if not self._require_license():
            return
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有可导出的数据")
            return

        visible_rows = []
        for i in range(self.table_widget.rowCount()):
            if not self.table_widget.isRowHidden(i):
                visible_rows.append(self._frame_idx_at_row(i))

        if not visible_rows:
            QMessageBox.information(self, "提示", "没有可见的数据")
            return

        default_name = f"iec104_parsed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        if self._last_dir:
            default_name = os.path.join(self._last_dir, default_name)
        path, _ = QFileDialog.getSaveFileName(self, "导出JSON", default_name,
                                                "JSON文件 (*.json);;所有文件 (*)")
        if not path:
            return
        self._last_dir = os.path.dirname(path) or self._last_dir
        try:
            data = []
            for row_idx in visible_rows:
                if row_idx < len(self.all_frames):
                    frame = self.all_frames[row_idx]
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
            export_count = len(visible_rows)
            total_count = len(self.all_frames)
            msg = f"已导出 {export_count}/{total_count} 条记录" if export_count < total_count else f"已导出 {export_count} 条记录"
            self.statusbar.showMessage(f"{msg}: {os.path.basename(path)}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _on_export_txt(self):
        """导出TXT - 支持筛选后导出"""
        if not self._require_license():
            return
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有可导出的数据")
            return

        visible_rows = []
        for i in range(self.table_widget.rowCount()):
            if not self.table_widget.isRowHidden(i):
                visible_rows.append(self._frame_idx_at_row(i))

        if not visible_rows:
            QMessageBox.information(self, "提示", "没有可见的数据")
            return

        default_name = f"iec104_parsed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        if self._last_dir:
            default_name = os.path.join(self._last_dir, default_name)
        path, _ = QFileDialog.getSaveFileName(self, "导出TXT", default_name,
                                                "文本文件 (*.txt);;所有文件 (*)")
        if not path:
            return
        self._last_dir = os.path.dirname(path) or self._last_dir
        try:
            export_count = len(visible_rows)
            total_count = len(self.all_frames)
            with open(path, "w", encoding="utf-8") as f:
                f.write("=" * 80 + "\n")
                f.write("IEC60870-5-104 报文解析报告\n")
                f.write(f"解析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"报文总数: {export_count}/{total_count}\n")
                f.write("=" * 80 + "\n\n")

                for row_idx in visible_rows:
                    if row_idx < len(self.all_frames):
                        frame = self.all_frames[row_idx]
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
            msg = f"已导出 {export_count}/{total_count} 条记录" if export_count < total_count else f"已导出 {export_count} 条记录"
            self.statusbar.showMessage(f"{msg}: {os.path.basename(path)}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    # ==================================================================
    # 填充表格/树
    # ==================================================================

    @staticmethod
    def _timestamp_to_sort_key(timestamp: str) -> float:
        """将时间戳字符串转为数值（毫秒），用于排序"""
        if not timestamp:
            return -1
        try:
            parts = timestamp.replace(',', '.').split(':')
            if len(parts) == 3:
                h, m = int(parts[0]), int(parts[1])
                s = float(parts[2])
                return h * 3600000 + m * 60000 + s * 1000
        except (ValueError, IndexError):
            pass
        return -1

    def _row_color_for_frame(self, frame: IEC104Frame) -> QColor:
        """根据帧类型和当前主题返回行背景色"""
        if self._dark_theme:
            if frame.error:
                return QColor("#3D2020")  # 错误: 暗红
            if not frame.apci:
                return QColor("#2b2b2b")
            ftype = frame.apci.frame_type
            if ftype == "I-format":
                if frame.signal_type in (SignalType.YK, SignalType.YT):
                    return QColor("#3D3520")  # 控制: 暗橙
                return QColor("#20283D")  # 数据: 暗蓝
            elif ftype == "S-format":
                return QColor("#203D20")  # 确认: 暗绿
            elif ftype == "U-format":
                return QColor("#3D2035")  # 控制: 暗粉
            return QColor("#2b2b2b")
        else:
            if frame.error:
                return QColor("#FFF0F0")  # 错误: 浅红
            if not frame.apci:
                return QColor("#FFFFFF")
            ftype = frame.apci.frame_type
            if ftype == "I-format":
                if frame.signal_type in (SignalType.YK, SignalType.YT):
                    return QColor("#FFF8E1")  # 控制: 浅橙
                return QColor("#F5FAFF")  # 数据: 浅蓝
            elif ftype == "S-format":
                return QColor("#F5FFF5")  # 确认: 浅绿
            elif ftype == "U-format":
                return QColor("#FFF5FA")  # 控制: 浅粉
            return QColor("#FFFFFF")

    def _populate_table(self):
        """填充报文列表表格 - 虚拟化：不创建单元格对象，仅更新映射与行数。

        由 FrameCellDelegate 在绘制时实时调用 _cell() 取内容。
        万帧级数据填充开销从 O(N×12) 降为 O(1)，仅重绘可见区域。
        注：all_frames 恒为全量，_view_order 只描述“当前可见行”（过滤/搜索/
        排序后仍可见的帧索引）——因此排序/搜索/点击/导出都经 _frame_idx_at_row 映射。
        """
        n = len(self.all_frames)
        # 数据源(重新解析/换标签页)改变后，回到默认序号序并清空搜索快照
        self._set_view(list(range(n)))
        self._sort_column = -1
        self.table_widget.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)

        if not self.all_frames:
            self.table_widget.viewport().update()
            return

        # 调整列宽：优先套用用户上次的列宽（同时避免大表全量自适应卡顿），
        # 没有则按前 300 行采样估算
        if self._saved_col_widths:
            for c, wd in enumerate(self._saved_col_widths):
                if c < self.table_widget.columnCount() and wd >= 20:
                    self.table_widget.setColumnWidth(c, wd)
        else:
            for c, wd in enumerate(self._estimate_col_widths()):
                self.table_widget.setColumnWidth(c, wd)

        # 自动选中第一条报文
        self._current_frame_idx = 0
        if n > 0:
            self.table_widget.selectRow(0)
            self._populate_tree(0)
        self.table_widget.viewport().update()

    def _set_view(self, display_indices: list):
        """把表格显示行设为 display_indices（all_frames 的索引序列）。

        统一供填充/过滤/搜索/重置使用：重建 _view_order 与反查表、重设行数、
        清除上一轮的行隐藏标记，随后整体重绘可见区。
        """
        self._view_order = [int(x) for x in display_indices]
        self._frame_to_row = {fi: vi for vi, fi in enumerate(self._view_order)}
        self._base_order = list(self._view_order)
        self._pre_search_order = []
        self.table_widget.setRowCount(0)
        self.table_widget.setRowCount(len(self._view_order))
        for r in range(len(self._view_order)):
            self.table_widget.setRowHidden(r, False)
        self.table_widget.viewport().update()

    def _cell(self, vi: int, col: int) -> dict:
        """视图行 vi、列 col 的单元格内容（FrameCellDelegate 绘制时调用）。"""
        if not (0 <= vi < len(self._view_order)):
            return self._frame_cell(-1, col)
        fi = self._view_order[vi]
        if not (0 <= fi < len(self.all_frames)):
            return self._frame_cell(-1, col)
        return self._frame_cell(fi, col)

    def _frame_cell(self, fi: int, col: int) -> dict:
        """帧索引 fi、列 col 的单元格内容（供 _cell 与搜索匹配复用）。

        返回 dict：text / bg / fg / bold / align 以及 sort 排序键。
        """
        empty = {"text": "-", "bg": QColor("#FFFFFF"), "fg": None,
                 "bold": False, "align": Qt.AlignLeft | Qt.AlignVCenter,
                 "sort": None}
        if not (0 <= fi < len(self.all_frames)):
            return empty
        frame = self.all_frames[fi]
        row_color = self._row_color_for_frame(frame)
        i = fi
        line_map = getattr(self, '_frame_input_lines', [])

        # 序号（书签帧在行首以 📌 标记）
        text = str(frame.index)
        if frame.index - 1 in self._bookmarks:
            text = "📌 " + text
        if col == 0:
            return {"text": text, "bg": row_color, "fg": None, "bold": False,
                    "align": Qt.AlignLeft | Qt.AlignVCenter, "sort": frame.index}

        if col == 1:  # 原始时间
            ts_text = frame.original_timestamp if frame.original_timestamp else "-"
            return {"text": ts_text, "bg": row_color,
                    "sort": self._timestamp_to_sort_key(frame.original_timestamp)}

        if col == 2:  # 方向
            dir_text = frame.direction
            if not dir_text and frame.original_direction:
                dir_text = frame.original_direction.upper()
            fg = None
            if "主站→子站" in dir_text or "TX" in dir_text or "SEND" in dir_text:
                fg = QColor("#E91E63")
            elif "子站→主站" in dir_text or "RX" in dir_text or "RECV" in dir_text:
                fg = QColor("#2196F3")
            return {"text": dir_text, "bg": row_color, "fg": fg, "sort": None}

        if col == 3:  # 信号类型
            label = frame.signal_label if frame.signal_label else "-"
            is_sig = frame.signal_type != SignalType.OTHER
            return {"text": label, "bg": row_color,
                    "fg": QColor(frame.signal_type.color_hex) if is_sig else None,
                    "bold": is_sig, "sort": None}

        if col == 4:  # 类型标识
            tid_text = (f"{frame.asdu.type_id}: "
                        f"{TypeID.get_description(frame.asdu.type_id)}"
                        if frame.asdu else
                        (frame.apci.frame_type if frame.apci else "-"))
            return {"text": tid_text, "bg": row_color, "sort": None}

        if col == 5:  # 传送原因
            cot_text = (f"({frame.asdu.cot_cause}) "
                        f"{COT.get_description(frame.asdu.cot)}"
                        if frame.asdu else "-")
            return {"text": cot_text, "bg": row_color, "sort": None}

        if col == 6:  # 公共地址
            ca_text = f"0x{frame.asdu.common_address:04X}" if frame.asdu else "-"
            return {"text": ca_text, "bg": row_color, "sort": None}

        if col == 7:  # 对象数
            obj_text = str(frame.asdu.number_of_objects) if frame.asdu else "-"
            obj_count = frame.asdu.number_of_objects if frame.asdu else 0
            return {"text": obj_text, "bg": row_color, "sort": obj_count}

        if col == 8:  # 值（汇总）
            if frame.asdu and frame.asdu.information_elements:
                vals = []
                for elem in frame.asdu.information_elements:
                    alias = get_alias(elem.ioa, frame.asdu.common_address)
                    prefix = f"[{alias}] " if alias else ""
                    vals.append(f"{prefix}{elem.value_desc}")
                text = " | ".join(vals)
            else:
                text = "-"
            return {"text": text, "bg": row_color, "sort": None}

        if col == 9:  # 描述
            desc = frame.description if frame.description else (
                frame.error if frame.error else "")
            if frame.error:
                desc = f"❌ {desc}"
            return {"text": desc, "bg": row_color, "sort": None}

        if col == 10:  # 输入行号
            fidx = frame.index - 1
            if fidx < len(line_map) and line_map[fidx]:
                src_lines = line_map[fidx]
                line_text = ", ".join(str(n + 1) for n in src_lines)
                line_sort = src_lines[0] if src_lines else 0
            else:
                line_text = "-"
                line_sort = 0
            return {"text": line_text, "bg": row_color, "sort": line_sort}

        if col == 11:  # 时间间隔 (ms)
            if i < len(self._frame_intervals):
                interval = self._frame_intervals[i]
                if interval is not None:
                    text = f"{interval:.0f}ms" if interval > 0 else "-"
                    fg = None
                    if interval > 1000:
                        fg = QColor("#F44336")
                    elif interval > 500:
                        fg = QColor("#FF9800")
                    return {"text": text, "bg": row_color, "fg": fg,
                            "sort": interval if interval > 0 else -1}
            return {"text": "-", "bg": row_color, "sort": -1}

        return empty

    def _on_header_sort(self, col: int):
        """表头点击 → 按该列对 _view_order 排序（数值优先，文本回退）"""
        # 排序会重排各行内容，先清除搜索隐藏，避免“隐藏行显示错位”
        self._clear_search_rows()
        if self._sort_column == col:
            self._sort_order = (Qt.DescendingOrder
                                if self._sort_order == Qt.AscendingOrder
                                else Qt.AscendingOrder)
        else:
            self._sort_column = col
            self._sort_order = Qt.AscendingOrder

        self._sort_view_by(col, self._sort_order)
        self.table_widget.horizontalHeader().setSortIndicator(
            col, self._sort_order)
        self.table_widget.viewport().update()

    def _sort_view_by(self, col: int, order: int):
        """按列排序 _view_order，采用与旧版一致的“数值优先/文本回退”规则"""
        if not self._view_order:
            return

        def key_of(fi):
            # sort 传递给 key_of 的是 _view_order 的元素——即“帧索引”fi，
            # 因此直接用 _frame_cell(fi, col) 取键；若用 _cell(vi, col) 会把
            # fi 当成视图行再做一层 _view_order 反查，非恒等视图时键会错乱。
            cell = self._frame_cell(fi, col)
            sk = cell.get("sort")
            if sk is None:
                # 无数值：按显示文本兜底，排在有数值行之后
                return (False, 0, cell["text"])
            if isinstance(sk, bool):
                return (True, float(sk), cell["text"])
            try:
                return (True, float(sk), cell["text"])
            except (TypeError, ValueError):
                return (False, 0, cell["text"])

        self._view_order.sort(key=key_of, reverse=(order == Qt.DescendingOrder))
        self._frame_to_row = {fi: vi for vi, fi in enumerate(self._view_order)}

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

            if apci.frame_type == "I-format":
                if hasattr(self, '_last_send_seq') and self._last_send_seq >= 0:
                    expected = (self._last_send_seq + 1) % 32768
                    if apci.send_seq != expected:
                        self._add_tree_item(apci_root,
                            f"⚠ 序列号跳跃: 期望 {expected}, 实际 {apci.send_seq}",
                            "", "序列号不连续")
                self._last_send_seq = apci.send_seq

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
                alias = get_alias(elem.ioa, asdu.common_address)
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

        # 默认展开前 2 层（根节点 + ASDU 节点），避免信息元素过多时树过长
        for i in range(self.tree_widget.topLevelItemCount()):
            top_item = self.tree_widget.topLevelItem(i)
            top_item.setExpanded(True)
            for j in range(top_item.childCount()):
                top_item.child(j).setExpanded(True)
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
        title_color = "#999" if self._dark_theme else "#666"
        text_color = "#d4d4d4" if self._dark_theme else "#000"
        self.hex_view.setHtml(
            f"<p style='color:{title_color};font-family:Consolas;font-size:10pt'>"
            f"# 报文 #{frame.index} 原始十六进制</p>"
            f"<p style='color:{text_color};font-family:Consolas;font-size:10pt'>{html}</p>"
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
                elif i % 16 == 0:
                    parts.append("&nbsp;&nbsp;")  # 每 8 字节额外分隔
                else:
                    parts.append(" ")
            byte_idx = i // 2
            hex_byte = cleaned[i:i+2].upper()
            if hl_start <= byte_idx < hl_end:
                hl_bg = "#5C4B00" if self._dark_theme else "#FFEB3B"
                hl_fg = "#FFD54F" if self._dark_theme else "#000"
                parts.append(
                    f'<span style="background-color:{hl_bg};color:{hl_fg};'
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

    def _frame_idx_at_row(self, row: int) -> int:
        """视图行号 -> all_frames 索引（经 _view_order 映射，排序/搜索后仍正确）"""
        if 0 <= row < len(self._view_order):
            return self._view_order[row]
        return row

    def _row_of_frame(self, frame_idx: int) -> int:
        """all_frames 索引 -> 视图行号；找不到返回 -1"""
        return self._frame_to_row.get(frame_idx, -1)

    def _on_table_click(self, row: int, col: int):
        """表格行单击 -> 更新解析详情（不切换标签页）"""
        fi = self._frame_idx_at_row(row)
        if not (0 <= fi < len(self.all_frames)):
            return
        self._current_frame_idx = fi
        self._populate_tree(fi)

    def _on_table_double_click(self, row: int, col: int):
        """表格行双击 -> 切换到解析详情标签页"""
        fi = self._frame_idx_at_row(row)
        if not (0 <= fi < len(self.all_frames)):
            return
        self._current_frame_idx = fi
        self._populate_tree(fi)
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

        selected_text = self.hex_input.textCursor().selectedText()
        if selected_text.strip():
            act_parse_sel = menu.addAction("▶ 解析选中内容")
            act_parse_sel.triggered.connect(lambda: self._on_parse_text(selected_text))
            act_parse = menu.addAction("▶ 解析全部报文 (F5)")
            act_parse.triggered.connect(self._on_parse)
        else:
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
        target_row = self._row_of_frame(frame_index)
        if target_row >= 0:
            self.table_widget.selectRow(target_row)
        self.result_tabs.setCurrentIndex(0)

    def _on_table_context_menu(self, pos):
        """表格右键菜单"""
        row = self.table_widget.rowAt(pos.y())
        if row < 0:
            return
        fi = self._frame_idx_at_row(row)
        if not (0 <= fi < len(self.all_frames)):
            return

        self.table_widget.selectRow(row)
        frame = self.all_frames[fi]

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

            # 快捷添加别名
            for elem in frame.asdu.information_elements:
                alias = get_alias(elem.ioa, frame.asdu.common_address)
                if not alias:
                    act_add_alias = menu.addAction(f"✏️ 添加别名 IOA=0x{elem.ioa:06X}")
                    act_add_alias.triggered.connect(lambda checked, ioa=elem.ioa: self._quick_add_alias(ioa))
                    break  # Only show for first unaliased IOA

        menu.addSeparator()

        # 书签标记（书签集合以帧索引存储）
        is_bookmarked = fi in self._bookmarks
        act_bookmark = menu.addAction("取消书签" if is_bookmarked else "📌 添加书签")
        act_bookmark.triggered.connect(lambda: self._toggle_bookmark(row))

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

    def _on_header_context_menu(self, pos):
        """表头右键菜单 - 列显示控制"""
        menu = QMenu(self)
        headers = ["序号", "原始时间", "方向", "信号类型", "类型标识",
                   "传送原因", "公共地址", "对象数", "值", "描述", "输入行", "间隔"]

        for i, header in enumerate(headers):
            if i >= self.table_widget.columnCount():
                break
            action = menu.addAction(header)
            action.setCheckable(True)
            action.setChecked(not self.table_widget.isColumnHidden(i))
            action.triggered.connect(lambda checked, col=i: self._toggle_column(col, checked))

        menu.exec_(self.table_widget.horizontalHeader().mapToGlobal(pos))

    def _toggle_column(self, col: int, visible: bool):
        """切换列显示"""
        self.table_widget.setColumnHidden(col, not visible)

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
            fi = self._frame_idx_at_row(row)
            if 0 <= fi < len(self.all_frames):
                frame = self.all_frames[fi]
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
                        alias = get_alias(elem.ioa, frame.asdu.common_address)
                        prefix = f"[{alias}] " if alias else ""
                        line += f" IOA=0x{elem.ioa:06X} {prefix}{elem.value_desc}"
            elif frame.error:
                line += f" ERROR={frame.error}"
            lines.append(line)
        QApplication.clipboard().setText("\n".join(lines))
        self.statusbar.showMessage(f"已复制 {len(self.all_frames)} 条解析结果", 2000)

    def _show_detail(self, row: int):
        """显示指定行的解析详情"""
        fi = self._frame_idx_at_row(row)
        if not (0 <= fi < len(self.all_frames)):
            return
        self._current_frame_idx = fi
        self._populate_tree(fi)
        self.result_tabs.setCurrentIndex(0)

    def _show_hex_view(self, row: int):
        """显示指定行的十六进制视图"""
        fi = self._frame_idx_at_row(row)
        if not (0 <= fi < len(self.all_frames)):
            return
        self._current_frame_idx = fi
        self._update_hex_view(fi)
        self.result_tabs.setCurrentIndex(2)

    def _locate_original_message(self, row: int):
        """定位原始报文 - 滚动到输入区对应位置并高亮"""
        fi = self._frame_idx_at_row(row)
        line_map = getattr(self, '_frame_input_lines', [])
        if fi < 0 or fi >= len(line_map) or not line_map[fi]:
            self.statusbar.showMessage("未找到对应的原始报文位置", 2000)
            return

        self._highlight_input_line_for_frame(fi)
        self.hex_input.setFocus()

        self.statusbar.showMessage(f"已定位到输入区第 {line_map[fi][0] + 1} 行", 2000)

    def _on_focus_search(self):
        """Ctrl+F 聚焦到报文搜索框"""
        self.search_input.setFocus()
        self.search_input.selectAll()

    def _toggle_input_panel(self):
        """折叠/展开原始报文输入面板"""
        self._left_panel_visible = not self._left_panel_visible
        if self._left_panel_visible:
            self.splitter.setSizes([430, 870])
            self._toggle_input_action.setText("◀ 隐藏输入")
        else:
            self.splitter.setSizes([0, self.splitter.width()])
            self._toggle_input_action.setText("▶ 显示输入")

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

    def _highlight_input_line_for_frame(self, frame_index: int):
        """高亮输入区中对应报文的源行（使用 ExtraSelection，不碰样式表）"""
        line_map = getattr(self, '_frame_input_lines', [])
        doc = self.hex_input.document()

        if frame_index >= len(line_map) or not line_map[frame_index]:
            self.hex_input.setExtraSelections([])
            return

        source_lines = line_map[frame_index]
        selections = []

        for line_num in source_lines:
            block = doc.findBlockByLineNumber(line_num)
            if not block.isValid():
                continue

            sel = QTextEdit.ExtraSelection()
            if self._dark_theme:
                sel.format.setBackground(QColor("#5C4B00"))
                sel.format.setForeground(QColor("#FFD54F"))
            else:
                sel.format.setBackground(QColor("#FFEB3B"))
                sel.format.setForeground(QColor("#000000"))
            cursor = QTextCursor(block)
            cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
            sel.cursor = cursor
            selections.append(sel)

        self.hex_input.setExtraSelections(selections)

        # 滚动到第一个高亮行
        if selections:
            self.hex_input.setTextCursor(selections[0].cursor)
            self.hex_input.centerCursor()

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
        sel = self.table_widget.selectionModel()
        if sel and sel.hasSelection():
            rows = sel.selectedRows()
            if rows:
                return self._frame_idx_at_row(rows[0].row())
        return self._frame_idx_at_row(self.table_widget.currentRow())

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
                        # 拖放视觉反馈
                        self._drag_overlay = True
                        self.hex_input.setStyleSheet(
                            "QPlainTextEdit { border: 2px dashed #2196F3; background-color: rgba(33,150,243,0.05); }"
                        )
                        return
        event.ignore()

    def dragLeaveEvent(self, event):
        """拖出事件：清除视觉反馈"""
        self._clear_drag_feedback()

    def _clear_drag_feedback(self):
        """清除拖放视觉反馈"""
        self._drag_overlay = False
        self.hex_input.setStyleSheet("")

    def dropEvent(self, event):
        """放下事件：读取拖入的文件（支持多文件合并）"""
        urls = event.mimeData().urls()
        if not urls:
            return
        # 与菜单“导入文件”保持一致的激活门控
        if not self._require_license():
            self._clear_drag_feedback()
            event.ignore()
            return

        loaded_count = failed_count = 0
        all_content = []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for url in urls:
                file_path = url.toLocalFile()
                if not file_path:
                    continue
                name = os.path.basename(file_path)
                content, err = self._read_import_file(file_path)
                if content is not None:
                    all_content.append(content)
                    loaded_count += 1
                else:
                    all_content.append(f"# 导入失败: {name} - {err}")
                    failed_count += 1
        finally:
            QApplication.restoreOverrideCursor()

        if all_content:
            self._clear_drag_feedback()
            self.hex_input.setPlainText("\n".join(all_content))
            msg = f"已拖放导入 {loaded_count} 个文件"
            if failed_count:
                msg += f"，{failed_count} 个失败"
            self.statusbar.showMessage(msg, 3000)
            # 走到这里说明已激活，自动解析
            if loaded_count > 0:
                self._on_parse()

    # ==================================================================
    # 实时输入校验
    # ==================================================================

    def _on_input_changed(self):
        """输入区内容变化时实时校验（忽略注释行与日志格式的时间/方向标记）"""
        text = self.hex_input.toPlainText()
        if not text.strip():
            self.lbl_input_status.setText("")
            return

        # 逐行收集数据行：跳过空行/# 注释/日志格式元数据行（如 "13:25:00.692 RX:"）
        data_lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            if re.match(r'^[\d\- :.]*?(RX|TX|send|recv)\s*:\s*$',
                        stripped, re.IGNORECASE):
                continue
            data_lines.append(stripped)

        if not data_lines:
            self.lbl_input_status.setText("")
            return

        raw = ''.join(data_lines)
        # 统计有效十六进制字符（忽略空格、点分隔符、0x前缀）
        cleaned = raw.replace(' ', '').replace('\t', '').replace('.', '')
        cleaned = re.sub(r'0[xX]', '', cleaned)
        hex_chars = ''.join(c for c in cleaned if c in '0123456789ABCDEFabcdef')
        non_hex = len(cleaned) - len(hex_chars)

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

    def _on_search_text_changed(self, text: str):
        """搜索输入变化时启动防抖定时器"""
        self._search_timer.stop()
        if not text:
            # 空内容立即清除过滤，不等防抖
            self._clear_search_rows()
            self.lbl_search_count.setText("")
            return
        self._search_timer.start()

    def _clear_search_rows(self):
        """取消所有行的搜索隐藏状态"""
        for i in range(self.table_widget.rowCount()):
            self.table_widget.setRowHidden(i, False)

    def _do_search(self):
        """实际执行搜索（防抖后触发）"""
        text = self.search_input.text()
        if not text:
            self._clear_search_rows()
            self.lbl_search_count.setText("")
            return

        try:
            pattern = re.compile(text, re.IGNORECASE)
        except re.error:
            pattern = re.compile(re.escape(text), re.IGNORECASE)

        match_count = 0
        for vi in range(self.table_widget.rowCount()):
            row_match = False
            for j in range(12):
                if pattern.search(self._cell(vi, j)["text"]):
                    row_match = True
                    break
            self.table_widget.setRowHidden(vi, not row_match)
            if row_match:
                match_count += 1

        self.lbl_search_count.setText(f"找到 {match_count} 条")

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
        if not self._require_license():
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "导入报文文件", self._last_dir,
            "报文文件 (*.txt *.log *.hex *.dat *.bin *.pcap);;所有文件 (*.*)"
        )
        if not paths:
            return
        self._last_dir = os.path.dirname(paths[0]) or self._last_dir

        if len(paths) == 1:
            # 单文件：沿用原逻辑
            self._load_single_file(paths[0])
        else:
            # 多文件：合并内容
            all_content = []
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                for path in paths:
                    content, err = self._read_import_file(path)
                    if content is not None:
                        all_content.append(content)
                    else:
                        all_content.append(
                            f"# 读取失败: {os.path.basename(path)} - {err}")
            finally:
                QApplication.restoreOverrideCursor()

            self.hex_input.setPlainText("\n".join(all_content))
            self.statusbar.showMessage(f"已合并导入 {len(paths)} 个文件", 3000)

    def _read_import_file(self, path: str):
        """读取导入文件（带大小上限，防止大文件卡死界面）

        Returns:
            (content, error): 成功返回 (文本内容(含文件头注释), None)；
              文本读取失败时按二进制转hex行；失败返回 (None, 错误信息)。
        """
        try:
            size = os.path.getsize(path)
        except OSError as e:
            return None, str(e)

        # PCAP
        if path.lower().endswith('.pcap'):
            if size > MAX_BIN_IMPORT_BYTES:
                return None, (f"文件过大 "
                              f"({size // (1024 * 1024)}MB > 上限 "
                              f"{MAX_BIN_IMPORT_BYTES // (1024 * 1024)}MB)")
            try:
                from pcap_importer import pcap_to_hex_text
                text = pcap_to_hex_text(path)
            except Exception as e:
                return None, str(e)
            if not text:
                return None, "未找到 IEC104 报文"
            return f"# === 文件: {os.path.basename(path)} ===\n{text}", None

        # 文本（超限直接拒绝，不读入）；utf-8 失败回退 GBK（工控机日志常见编码）
        if size <= MAX_TEXT_IMPORT_BYTES:
            content = None
            for enc in ("utf-8", "gbk"):
                try:
                    with open(path, "r", encoding=enc) as f:
                        content = f.read()
                    break
                except UnicodeDecodeError:
                    continue  # 换下一种编码
                except OSError:
                    break      # 文本打开失败 → 尝试按二进制处理
            if content is not None:
                header = f"# === 文件: {os.path.basename(path)} ===\n"
                return header + content, None

        # 二进制转 hex 行
        limit = min(MAX_BIN_IMPORT_BYTES, MAX_TEXT_IMPORT_BYTES)
        if size > limit:
            return None, (f"文件过大 ({size // (1024 * 1024)}MB > 上限 "
                          f"{limit // (1024 * 1024)}MB)")
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as e:
            return None, str(e)
        hex_lines = [raw[i:i + 16].hex(" ").upper() for i in range(0, len(raw), 16)]
        header = f"# === 文件: {os.path.basename(path)} ===\n"
        return header + "\n".join(hex_lines), None

    def _load_single_file(self, path: str):
        """加载单个文件"""
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            content, err = self._read_import_file(path)
        finally:
            QApplication.restoreOverrideCursor()

        if content is None:
            if err == "未找到 IEC104 报文":
                QMessageBox.warning(self, "导入失败", "PCAP 文件中未找到 IEC104 报文")
            else:
                QMessageBox.critical(self, "导入失败", f"无法读取文件:\n{err}")
            return

        self.hex_input.setPlainText(content)
        suffix = " (PCAP)" if path.lower().endswith('.pcap') else ""
        self.statusbar.showMessage(
            f"已导入{suffix}: {os.path.basename(path)}", 3000)

    # ==================================================================
    # 统计图表
    # ==================================================================

    def _on_show_stats(self):
        """显示统计图表"""
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有已解析的报文，请先解析")
            return
        dlg = StatsDialog(self.all_frames, self)
        self._apply_theme_to_dialog(dlg)
        dlg.exec_()

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
        dlg = IOAAliasDialog(self)
        self._apply_theme_to_dialog(dlg)
        dlg.exec_()
        # 刷新当前显示
        if self.all_frames:
            self._populate_table()
            if self._current_frame_idx < len(self.all_frames):
                self._populate_tree(self._current_frame_idx)

    def _open_generator(self):
        """打开帧生成器对话框"""
        if not self._require_license():
            return
        from gui.generator_dialog import GeneratorDialog
        dialog = GeneratorDialog(self)
        self._apply_theme_to_dialog(dialog)
        if dialog.exec_() == QDialog.Accepted:
            pass

    def _open_compare(self):
        """打开报文对比"""
        if not self._require_license():
            return
        from gui.compare_dialog import CompareDialog
        dialog = CompareDialog(self)
        self._apply_theme_to_dialog(dialog)
        dialog.exec_()

    def _open_simulator(self):
        """打开通信模拟器（非模态，可同时操作主窗口）"""
        if not self._require_license():
            return
        # 防止重复打开
        if hasattr(self, '_simulator_dialog') and self._simulator_dialog and self._simulator_dialog.isVisible():
            self._simulator_dialog.activateWindow()
            return
        from gui.simulator_dialog import SimulatorDialog
        self._simulator_dialog = SimulatorDialog(self)
        self._apply_theme_to_dialog(self._simulator_dialog)
        self._simulator_dialog.setAttribute(Qt.WA_DeleteOnClose, False)
        self._simulator_dialog.show()

    # ==================================================================
    # 书签功能
    # ==================================================================

    def _toggle_bookmark(self, row: int):
        """切换书签状态（入参为视图行号，书签集合以帧索引存储）"""
        fi = self._frame_idx_at_row(row)
        frame = self.all_frames[fi] if 0 <= fi < len(self.all_frames) else None
        display = str(frame.index) if frame else str(fi + 1)
        if fi in self._bookmarks:
            self._bookmarks.discard(fi)
            self.statusbar.showMessage(f"已取消报文 #{display} 的书签", 2000)
        else:
            self._bookmarks.add(fi)
            self.statusbar.showMessage(f"已添加报文 #{display} 的书签", 2000)
        target_row = self._row_of_frame(fi)
        if target_row >= 0:
            self._update_bookmark_visual(target_row)

    def _update_bookmark_visual(self, row: int):
        """更新书签行的视觉效果——刷新该行，由 _cell(列0) 依据书签集合画 📌 前缀"""
        self.table_widget.viewport().update()

    def _select_bookmark_row(self, frame_idx: int):
        """按帧索引选中对应表格行并刷新详情"""
        row = self._row_of_frame(frame_idx)
        if row >= 0:
            self.table_widget.selectRow(row)
            self._on_table_click(row, 0)

    def _goto_next_bookmark(self):
        """跳转到下一个书签"""
        if not self._bookmarks:
            self.statusbar.showMessage("没有书签，按 Space 添加", 2000)
            return
        current_fi = self._frame_idx_at_row(self.table_widget.currentRow())
        for bm in sorted(self._bookmarks):
            if bm > current_fi:
                self._select_bookmark_row(bm)
                return
        # Wrap around
        self._select_bookmark_row(sorted(self._bookmarks)[0])

    def _goto_prev_bookmark(self):
        """跳转到上一个书签"""
        if not self._bookmarks:
            self.statusbar.showMessage("没有书签，按 Space 添加", 2000)
            return
        current_fi = self._frame_idx_at_row(self.table_widget.currentRow())
        for bm in sorted(self._bookmarks, reverse=True):
            if bm < current_fi:
                self._select_bookmark_row(bm)
                return
        # Wrap around
        self._select_bookmark_row(sorted(self._bookmarks)[0])

    # ==================================================================
    # 快捷添加IOA别名
    # ==================================================================

    def _quick_add_alias(self, ioa: int):
        """快捷添加IOA别名"""
        from gui.ioa_alias_dialog import save_aliases, load_aliases
        alias, ok = QInputDialog.getText(self, "添加别名",
                                          f"为 IOA=0x{ioa:06X} 设置别名:")
        if ok and alias:
            aliases = load_aliases()
            aliases[f"0x{ioa:06X}"] = alias
            save_aliases(aliases)
            self.statusbar.showMessage(f"已设置别名: 0x{ioa:06X} -> {alias}", 2000)
            # Refresh table to show new alias
            self._populate_table()

    # ==================================================================
    # 键盘导航
    # ==================================================================

    def _table_key_press(self, event):
        """表格键盘导航"""
        current_row = self.table_widget.currentRow()

        if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            # Enter: 显示解析详情
            if 0 <= current_row < len(self.all_frames):
                self._show_detail(current_row)
        elif event.key() == Qt.Key_F2:
            # F2: 定位原始报文
            if 0 <= current_row < len(self.all_frames):
                self._locate_original_message(current_row)
        elif event.key() == Qt.Key_Space:
            # Space: 切换书签
            if 0 <= current_row < len(self.all_frames):
                self._toggle_bookmark(current_row)
        else:
            QTableWidget.keyPressEvent(self.table_widget, event)

    def closeEvent(self, event):
        """关闭窗口时安全停止后台线程并保存界面状态"""
        if self._parse_worker and self._parse_worker.isRunning():
            self._parse_worker.cancel()
            # 等待线程自行退出（不用 terminate：强杀线程有崩溃风险）
            self._parse_worker.wait(5000)
        self._save_ui_settings()
        event.accept()

    # ==================================================================
    # 界面设置持久化（窗口大小/分栏/主题/列宽/最近目录）
    # ==================================================================

    def _load_ui_settings(self):
        """启动时恢复上次关闭时的界面状态"""
        try:
            s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)

            geometry = s.value("ui/geometry")
            if geometry is not None:
                self.restoreGeometry(geometry)

            splitter_state = s.value("ui/splitter")
            if splitter_state is not None:
                self.splitter.restoreState(splitter_state)

            dark = s.value("ui/dark_theme", False, type=bool)
            if dark and not self._dark_theme:
                self._dark_theme = True
                self._apply_dark_theme()

            widths = s.value("ui/col_widths")
            if isinstance(widths, (list, tuple)) and widths:
                try:
                    saved = [int(float(w)) for w in widths]
                    if len(saved) == self.table_widget.columnCount():
                        self._saved_col_widths = saved
                except (TypeError, ValueError):
                    self._saved_col_widths = None

            last_dir = s.value("ui/last_dir", "")
            if isinstance(last_dir, str):
                self._last_dir = last_dir
        except Exception as e:
            logger.warning(f"恢复界面设置失败: {e}")

    def _save_ui_settings(self):
        """关闭时保存界面状态"""
        try:
            s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
            s.setValue("ui/geometry", self.saveGeometry())
            s.setValue("ui/splitter", self.splitter.saveState())
            s.setValue("ui/dark_theme", self._dark_theme)
            s.setValue("ui/last_dir", self._last_dir)
            widths = [self.table_widget.columnWidth(c)
                      for c in range(self.table_widget.columnCount())]
            s.setValue("ui/col_widths", widths)
        except Exception as e:
            logger.warning(f"保存界面设置失败: {e}")

    def _estimate_col_widths(self, sample_rows: int = 300) -> list:
        """按前 N 行文本实际宽度估算各列列宽。

        大报表（数千行）调用 resizeColumnsToContents 会逐单元格测量导致明显卡顿，
        抽样估算在毫秒级完成，视觉效果接近。
        """
        fm = QFontMetrics(self.table_widget.font())
        cols = self.table_widget.columnCount()
        rows = min(self.table_widget.rowCount(), sample_rows)
        max_w = [40] * cols
        for r in range(rows):
            for c in range(cols):
                w = fm.horizontalAdvance(self._cell(r, c)["text"]) + 28
                if w > max_w[c]:
                    max_w[c] = w
        return [min(w, 420) for w in max_w]

    def _on_about(self):
        """版本信息对话框"""
        dlg = AboutDialog(self.APP_TITLE, self.APP_VERSION)
        self._apply_theme_to_dialog(dlg)
        dlg.exec_()

    def _on_ioa_help(self):
        """IOA 地址说明对话框"""
        from gui.about_dialog import IOAHelpDialog
        IOAHelpDialog(self).exec_()

    def _check_license(self):
        """检查授权状态"""
        from license import get_license_status
        status = get_license_status()
        self._is_activated = status["activated"]
        self._is_license_expired = status["expired"]
        self._license_days_remaining = status.get("days_remaining")
        self._license_expiry_date = status.get("expiry_date", "")
        if status["expired"]:
            self.setWindowTitle(f"{self.APP_TITLE} [授权已到期 - 功能已锁定]")
        elif not self._is_activated:
            self.setWindowTitle(f"{self.APP_TITLE} [未激活 - 仅支持单条解析]")
        else:
            self.setWindowTitle(self.APP_TITLE)
        self._update_license_label()

    def _update_license_label(self):
        """更新状态栏授权常驻按钮的文字与颜色"""
        from license import get_license_status
        st = get_license_status()
        if st["activated"]:
            if st.get("permanent"):
                self.btn_license_state.setText("已激活")
                color, weight = LICENSE_COLOR_OK, "normal"
            else:
                days = st.get("days_remaining")
                exp = st.get("expiry_date", "")
                text = f"剩{days}天" if days is not None else (exp or "限时授权")
                if days is not None and days <= 3:
                    color, weight = LICENSE_COLOR_DANGER, "bold"
                elif days is not None and days <= 7:
                    color, weight = LICENSE_COLOR_WARN, "bold"
                else:
                    color, weight = LICENSE_COLOR_OK, "normal"
                self.btn_license_state.setText(text)
        elif st["expired"]:
            self.btn_license_state.setText("授权已到期")
            color, weight = LICENSE_COLOR_DANGER, "bold"
        else:
            self.btn_license_state.setText("未激活")
            color, weight = LICENSE_COLOR_WARN, "bold"
        self.btn_license_state.setStyleSheet(
            f"QPushButton {{ border: none; color: {color}; font-weight: {weight}; "
            f"padding: 0 8px; background: transparent; }}"
            f"QPushButton:hover {{ color: #1565C0; }}")

    def _may_prompt_expired(self):
        """启动时若授权已到期则锁定提示；限时授权临近到期则红色预警"""
        from license import get_license_status
        st = get_license_status()
        if st["expired"]:
            QMessageBox.warning(
                self, "授权已到期",
                "您的软件授权已到期，受限功能已被锁定。\n\n"
                "请联系开发者获取新的激活码，在 帮助 → 软件激活 "
                "中输入即可恢复全部功能。")
            return
        # 剩余 ≤3 天的红色预警（永久/未激活不弹）
        if st["activated"] and not st.get("permanent"):
            days = st.get("days_remaining")
            if days is not None and days <= 3:
                QMessageBox.warning(
                    self, "授权即将到期",
                    f"您的软件授权将于 {days} 天后到期（{st.get('expiry_date', '')}）。\n\n"
                    "到期后受限功能将被锁定，请尽快联系开发者续期。\n"
                    "在 帮助 → 软件激活 中输入新激活码即可完成续期。")

    def _require_license(self) -> bool:
        """检查是否需要激活，未激活/已过期则提示并返回False"""
        if self._is_activated:
            return True
        box = QMessageBox(self)
        if self._is_license_expired:
            box.setWindowTitle("授权已到期")
            box.setIcon(QMessageBox.Warning)
            box.setText("您的软件授权已到期，功能已被锁定。\n\n"
                        "请联系开发者获取新的激活码，在 帮助 → 软件激活 中输入即可恢复。")
        else:
            box.setWindowTitle("功能限制")
            box.setIcon(QMessageBox.Information)
            box.setText("此功能需要激活后使用。\n\n"
                        "将硬件指纹发送给开发者即可获取激活码。")
        box.addButton("打开激活窗口", QMessageBox.ActionRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec_()
        if box.buttonRole(box.clickedButton()) == QMessageBox.ActionRole:
            self._open_license_dialog()
        return False

    def _open_license_dialog(self):
        """打开激活对话框"""
        from gui.license_dialog import LicenseDialog
        dialog = LicenseDialog(self)
        self._apply_theme_to_dialog(dialog)
        dialog.exec_()
        # 重新检查授权状态
        old_status = self._is_activated
        self._check_license()
        if self._is_activated and not old_status:
            self.setWindowTitle(self.APP_TITLE)
            QMessageBox.information(self, "激活成功", "软件已激活，所有功能已解锁！")

    def _open_history_manager(self):
        """打开历史报文管理"""
        if not self._require_license():
            return
        from gui.history_dialog import HistoryDialog
        dialog = HistoryDialog(self)
        self._apply_theme_to_dialog(dialog)
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
            self.statusbar.showMessage("已加载历史会话，按 F5 或点击「解析报文」开始解析", 3000)

    def _on_filter(self):
        """应用过滤"""
        signal = self.filter_signal.currentData()
        type_id = self.filter_type.currentData()
        cot = self.filter_cot.currentData()
        addr_text = self.filter_addr.text().strip()
        direction = self.filter_direction.currentData()
        time_from = self.filter_time_from.text().strip()
        time_to = self.filter_time_to.text().strip()

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
                addr = int(addr_text, 0)   # 支持十进制与 0x 前缀十六进制
            except ValueError:
                addr = None
            if addr is None:
                self.statusbar.showMessage(
                    f"公共地址 '{addr_text}' 格式无效，已忽略该筛选条件", 4000)
            else:
                filtered = [f for f in filtered
                            if f.asdu and f.asdu.common_address == addr]

        # 方向过滤
        if direction:
            filtered = [f for f in filtered if f.direction == direction]

        # 时间范围过滤（非法输入给出提示并忽略该条件，不抛异常）
        if time_from or time_to:
            def parse_filter_time(t):
                parts = t.replace(',', '.').split(':')
                if len(parts) == 3:
                    try:
                        h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
                    except ValueError:
                        return None
                    return h * 3600 + m * 60 + s
                return None

            t_from = parse_filter_time(time_from) if time_from else None
            t_to = parse_filter_time(time_to) if time_to else None
            if (time_from and t_from is None) or (time_to and t_to is None):
                self.statusbar.showMessage(
                    "时间格式无效，请使用 HH:MM:SS（如 13:25:00.500），该条件已忽略", 4000)

            def time_in_range(frame):
                if not frame.original_timestamp:
                    return False
                t = parse_filter_time(frame.original_timestamp)
                if t is None:
                    return False
                if t_from is not None and t < t_from:
                    return False
                if t_to is not None and t > t_to:
                    return False
                return True

            filtered = [f for f in filtered if time_in_range(f)]

        # IOA 范围过滤（非法输入给出提示并忽略）
        ioa_invalid = False
        try:
            ioa_from = int(self.ioa_from_input.text(), 0) if self.ioa_from_input.text() else None
        except ValueError:
            ioa_from = None
            ioa_invalid = True
        try:
            ioa_to = int(self.ioa_to_input.text(), 0) if self.ioa_to_input.text() else None
        except ValueError:
            ioa_to = None
            ioa_invalid = True
        if ioa_invalid:
            self.statusbar.showMessage(
                "IOA 范围格式无效，已忽略该筛选条件（支持十进制或 0x 前缀）", 4000)

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

        # 过滤生效状态可视化：先置标志再刷新统计，保证统计栏同步显示"过滤中"前缀
        has_criteria = bool(
            signal or direction or addr_text or time_from or time_to
            or ioa_from is not None or ioa_to is not None
            or (type_id != -1 and type_id is not None)
            or (cot != -1 and cot is not None))
        self._filter_active = has_criteria
        self._filter_counts = (len(filtered), total_count) if has_criteria else None

        # 显示子集：用匹配帧在 all_frames 中的索引重建 _view_order。
        # all_frames 恒为全量，点击/导出经 _frame_idx_at_row 映射到正确帧（修复旧实现
        # 在过滤后点击/导出错位的隐患）。
        if has_criteria:
            frame_to_idx = {id(f): i for i, f in enumerate(self.all_frames)}
            display = [frame_to_idx[id(f)] for f in filtered]
            self._set_view(display)
        else:
            self._set_view(list(range(total_count)))
        self._update_status_stats()
        # 过滤后自动选中首行并刷新解析详情（与旧 _populate_table 行为一致）
        if self._view_order:
            self._current_frame_idx = 0
            self.table_widget.selectRow(0)
            self._populate_tree(0)

        self.filter_btn.setStyleSheet(
            FILTER_BTN_STYLE_ACTIVE if has_criteria else FILTER_BTN_STYLE_IDLE)
        self.filter_btn.setToolTip(
            "⚠ 过滤生效中（条件仍保留），点「重置」恢复全部"
            if has_criteria else "按条件过滤报文列表")
        self.statusbar.showMessage(
            f"过滤结果: {len(filtered)}/{total_count} 条报文", 3000)

    def _on_filter_reset(self):
        """重置过滤"""
        self.filter_signal.setCurrentIndex(0)
        self.filter_type.setCurrentIndex(0)
        self.filter_cot.setCurrentIndex(0)
        self.filter_addr.clear()
        self.filter_direction.setCurrentIndex(0)
        self.filter_time_from.clear()
        self.filter_time_to.clear()
        self.ioa_from_input.clear()
        self.ioa_to_input.clear()
        self._filter_active = False
        self._filter_counts = None
        self.filter_btn.setStyleSheet(FILTER_BTN_STYLE_IDLE)
        self.filter_btn.setToolTip("按条件过滤报文列表")
        self._populate_table()
        self._update_status_stats()
        self.statusbar.showMessage("过滤已重置", 2000)
