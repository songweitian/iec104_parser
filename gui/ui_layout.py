"""
主窗口界面布局层（从 main_window.py 拆出）

只负责"创建控件并摆好位置"，不包含解析/导出/授权等业务动作——
主窗口原本 2900+ 行，改一个按钮位置都要在几千行里翻找。

约定：本 mixin 里的方法把控件挂到 self 上（self.btn_parse 等），
业务逻辑仍在 MainWindow，二者通过控件名约定关联。
"""

from PyQt5.QtCore import Qt, QSize, QTimer, QPoint
from PyQt5.QtGui import (QFont, QColor, QIcon, QPainter, QPixmap,
                         QPen, QBrush)
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTreeWidget,
    QTreeWidgetItem, QTextEdit, QPushButton, QToolBar, QLabel,
    QLineEdit, QGroupBox, QComboBox, QTabWidget, QTableWidget,
    QProgressBar, QApplication, QStatusBar,
)

from iec104_parser import ParseMode, TypeID, COT
from gui.frame_table import FrameCellDelegate, FILTER_BTN_STYLE_IDLE
from gui.icons import create_app_icon, _doc_icon, _ICON_PRIMARY
from version import APP_VERSION

# 授权状态常驻按钮：状态色
LICENSE_COLOR_OK = "#2E7D32"
LICENSE_COLOR_WARN = "#FF9800"    # 剩余 ≤7 天 / 未激活
LICENSE_COLOR_DANGER = "#F44336"  # 剩余 ≤3 天 / 已到期


class UiLayoutMixin:
    """主窗口的界面布局部分（依赖宿主提供 self.xxx 控件与部分状态）"""

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
        self.btn_clear.setToolTip("清空当前输入与解析结果 (Ctrl+L)")
        self.btn_import_file = QPushButton("📁 导入文件")
        self.btn_import_file.clicked.connect(self._on_import_file)
        self.btn_import_file.setStyleSheet(
            "QPushButton { border: 1px solid #ccc; padding: 5px 12px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #e3f2fd; border-color: #2196F3; }"
        )

        btn_layout.setSpacing(6)
        btn_layout.addWidget(self.btn_parse)
        btn_layout.addWidget(self.btn_clear)
        btn_layout.addWidget(self.btn_import_file)
        btn_layout.addStretch()
        self.lbl_input_status = QLabel("")
        self.lbl_input_status.setStyleSheet("color: #888; font-size: 9pt;")
        btn_layout.addWidget(self.lbl_input_status)
        self.lbl_line_count = QLabel("0 行")
        self.lbl_line_count.setStyleSheet("color: #666; font-size: 9pt; padding-right: 4px;")
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

        # 过滤选项 - 分组布局（标签+控件对齐，筛选/重置独立在行尾）
        filter_group = QGroupBox("过滤条件")
        filter_layout = QVBoxLayout(filter_group)
        filter_layout.setSpacing(4)

        # 第1行: 信号类型 | 类型标识 | 传送原因 | 地址
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        row1.addWidget(QLabel("信号:"))
        self.filter_signal = QComboBox()
        self.filter_signal.addItem("全部", "")
        self.filter_signal.addItem("遥信", "遥信")
        self.filter_signal.addItem("遥测", "遥测")
        self.filter_signal.addItem("遥控", "遥控")
        self.filter_signal.addItem("遥调", "遥调")
        self.filter_signal.addItem("遥脉", "遥脉")
        row1.addWidget(self.filter_signal)

        row1.addWidget(QLabel("类型:"))
        self.filter_type = QComboBox()
        self.filter_type.setMinimumWidth(120)
        self.filter_type.addItem("全部", -1)
        for tid in sorted(TypeID):
            desc = TypeID.get_description(tid.value)
            self.filter_type.addItem(f"{tid.value}: {tid.name} ({desc})", tid.value)
        row1.addWidget(self.filter_type, 1)

        row1.addWidget(QLabel("COT:"))
        self.filter_cot = QComboBox()
        self.filter_cot.setMinimumWidth(100)
        self.filter_cot.addItem("全部", -1)
        for cot in range(1, 42):
            self.filter_cot.addItem(f"{cot}: {COT.get_description(cot)}", cot)
        row1.addWidget(self.filter_cot)

        row1.addWidget(QLabel("地址:"))
        self.filter_addr = QLineEdit()
        self.filter_addr.setPlaceholderText("CA/0x1A")
        self.filter_addr.setMaximumWidth(90)
        self.filter_addr.setToolTip("公共地址(ASDU地址)，支持十进制或 0x 十六进制")
        row1.addWidget(self.filter_addr)
        filter_layout.addLayout(row1)

        # 第2行: 方向 | 时间范围 | IOA范围 | 筛选/重置
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        row2.addWidget(QLabel("方向:"))
        self.filter_direction = QComboBox()
        self.filter_direction.addItem("全部", "")
        self.filter_direction.addItem("主→从", "主站→子站")
        self.filter_direction.addItem("从→主", "子站→主站")
        row2.addWidget(self.filter_direction)

        row2.addWidget(QLabel("时间:"))
        self.filter_time_from = QLineEdit()
        self.filter_time_from.setPlaceholderText("HH:MM:SS")
        self.filter_time_from.setMaximumWidth(85)
        row2.addWidget(self.filter_time_from)
        row2.addWidget(QLabel("~"))
        self.filter_time_to = QLineEdit()
        self.filter_time_to.setPlaceholderText("HH:MM:SS")
        self.filter_time_to.setMaximumWidth(85)
        row2.addWidget(self.filter_time_to)

        row2.addWidget(QLabel("IOA:"))
        self.ioa_from_input = QLineEdit()
        self.ioa_from_input.setPlaceholderText("起始")
        self.ioa_from_input.setMaximumWidth(65)
        self.ioa_from_input.setToolTip("IOA起始地址，支持十进制或0x前缀")
        row2.addWidget(self.ioa_from_input)
        row2.addWidget(QLabel("~"))
        self.ioa_to_input = QLineEdit()
        self.ioa_to_input.setPlaceholderText("结束")
        self.ioa_to_input.setMaximumWidth(65)
        self.ioa_to_input.setToolTip("IOA结束地址")
        row2.addWidget(self.ioa_to_input)

        row2.addStretch(1)
        self.filter_btn = QPushButton("筛选")
        self.filter_btn.clicked.connect(self._on_filter)
        self.filter_btn.setStyleSheet(FILTER_BTN_STYLE_IDLE)
        self.filter_btn.setToolTip("按条件过滤报文列表")
        row2.addWidget(self.filter_btn)
        self.filter_reset = QPushButton("重置")
        self.filter_reset.clicked.connect(self._on_filter_reset)
        row2.addWidget(self.filter_reset)
        filter_layout.addLayout(row2)

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
        self.tree_widget.setColumnWidth(0, 200)
        self.tree_widget.setColumnWidth(1, 260)
        self.tree_widget.setColumnWidth(2, 300)
        self.tree_widget.header().setStretchLastSection(True)
        self.tree_widget.setRootIsDecorated(True)
        self.tree_widget.setUniformRowHeights(True)
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
        search_layout.setSpacing(4)
        search_layout.addWidget(QLabel("🔍 搜索:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入关键字或正则搜索...")
        self.search_input.setClearButtonEnabled(True)
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._do_search)
        self.search_input.textChanged.connect(self._on_search_text_changed)

        # 输入区实时校验防抖：大文件每次按键全量扫描会卡顿，延迟 200ms 合并
        self._input_timer = QTimer()
        self._input_timer.setSingleShot(True)
        self._input_timer.setInterval(200)
        self._input_timer.timeout.connect(self._on_input_changed)
        search_layout.addWidget(self.search_input, 1)
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
        self.table_widget.verticalHeader().setDefaultSectionSize(26)
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
        self.splitter.setSizes([420, 900])
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setHandleWidth(8)
        self.left_panel.setMinimumWidth(280)
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

        self.lbl_version = QLabel(f"v{APP_VERSION}")
        self.lbl_version.setStyleSheet("color: #999; padding: 0 8px;")
        self.statusbar.addPermanentWidget(self.lbl_version)
