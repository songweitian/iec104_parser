"""
IEC104 通信模拟器对话框
表格化显示四遥数据，支持主站/从站模式
从站模式下可直接编辑点位值和描述
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QGroupBox, QFormLayout,
    QMessageBox, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
    QHeaderView, QPlainTextEdit, QCheckBox, QTabWidget, QWidget,
    QDoubleSpinBox, QToolBar, QAction, QSizePolicy, QAbstractItemView,
    QFileDialog
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont, QColor, QIcon

import json
import os
import re
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from iec104_simulator import (
    IEC104Simulator, SimulatorRole, ConnectionState,
    PointData, create_default_points, DEFAULT_IOA_RANGES
)
from iec104_parser import IEC104Parser, TypeID, COT, SignalType


# 样式常量
INVALID_COLOR = QColor("#AAAAAA")
YX_ON_COLOR = QColor("#4CAF50")
YX_OFF_COLOR = QColor("#F44336")
GRAY_BG = QColor("#F5F5F5")
BLUE_LIGHT = QColor("#E3F2FD")
PURPLE_LIGHT = QColor("#F3E5F5")

# 配置文件路径
CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
CONFIG_FILE = os.path.join(CONFIG_DIR, "simulator_config.json")


class SimulatorDialog(QDialog):
    """IEC104 通信模拟器对话框（非模态）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("IEC104 通信模拟器")
        self.setMinimumSize(1100, 700)
        self.resize(1200, 750)
        # 非模态设置
        self.setWindowFlags(self.windowFlags() | Qt.WindowMinMaxButtonsHint)
        self.simulator: IEC104Simulator = None
        self._current_points = {}
        self._yc_edit_timers = {}
        self._block_cell_changed = False  # 阻止 cellChanged 信号
        # 角色切换确认状态（记录上次角色，切换被拒时回退下拉框）
        self._last_role_index = 0
        self._suppress_role_confirm = False
        # 报文表格的独立解析器（复用实例，解析前 clear，避免每帧重建开销）
        self._frame_parser = IEC104Parser()
        self._setup_ui()
        # 加载上次保存的配置
        self._load_config()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(8, 8, 8, 8)

        # === 顶部：连接配置 ===
        self._setup_connection_bar(layout)

        # === 中部：点位配置 + 标签页 ===
        self._setup_point_config(layout)
        self._setup_tabs(layout)

        # === 底部：手动发送 ===
        self._setup_manual_send(layout)

        # 连接信号（放在最后，避免 addItem 时触发）
        self.role_combo.currentIndexChanged.connect(self._on_role_changed)
        # 初始化角色状态和点位
        self._on_role_changed(self.role_combo.currentIndex())

    # ==================================================================
    # UI 构建
    # ==================================================================

    def _setup_connection_bar(self, parent_layout):
        """连接配置栏"""
        conn_group = QGroupBox("连接配置")
        conn_layout = QHBoxLayout(conn_group)
        conn_layout.setSpacing(8)

        # 角色
        conn_layout.addWidget(QLabel("角色:"))
        self.role_combo = QComboBox()
        self.role_combo.addItem("主站 (Master)", SimulatorRole.MASTER)
        self.role_combo.addItem("从站 (Slave)", SimulatorRole.SLAVE)
        self.role_combo.setMinimumWidth(100)
        conn_layout.addWidget(self.role_combo)

        # IP
        conn_layout.addWidget(QLabel("IP:"))
        self.ip_input = QLineEdit("127.0.0.1")
        self.ip_input.setMaximumWidth(130)
        conn_layout.addWidget(self.ip_input)

        # 端口
        conn_layout.addWidget(QLabel("端口:"))
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(2404)
        self.port_input.setMaximumWidth(70)
        conn_layout.addWidget(self.port_input)

        # 启动/停止
        self.btn_start = QPushButton("▶ 启动")
        self.btn_start.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; font-weight: bold; padding: 5px 16px; border-radius: 3px; border: none; }"
            "QPushButton:hover { background-color: #388E3C; }"
        )
        self.btn_start.clicked.connect(self._on_start)
        conn_layout.addWidget(self.btn_start)

        self.btn_stop = QPushButton("■ 停止")
        self.btn_stop.setEnabled(False)
        self.btn_stop.setStyleSheet(
            "QPushButton { background-color: #f44336; color: white; font-weight: bold; padding: 5px 16px; border-radius: 3px; border: none; }"
            "QPushButton:hover { background-color: #c62828; }"
        )
        self.btn_stop.clicked.connect(self._on_stop)
        conn_layout.addWidget(self.btn_stop)

        # 分隔
        conn_layout.addStretch()

        # 状态
        self.lbl_status = QLabel("● 未连接")
        self.lbl_status.setStyleSheet("color: #999; font-weight: bold;")
        conn_layout.addWidget(self.lbl_status)

        self.lbl_seq = QLabel("SN:0 RN:0")
        self.lbl_seq.setStyleSheet("color: #666; font-size: 9pt;")
        conn_layout.addWidget(self.lbl_seq)

        parent_layout.addWidget(conn_group)

    def _setup_point_config(self, parent_layout):
        """中间操作区"""
        config_layout = QHBoxLayout()

        # 点位数量配置（主站/从站均可在启动前配置；
        # 主站模式下 YK/YT 用于定义可下发的遥控/遥调点位）
        self.points_group = QGroupBox("点位数量 (启动前配置)")
        points_layout = QHBoxLayout(self.points_group)
        points_layout.setSpacing(6)

        for label, attr, default in [
            ("遥信(YX):", "yx_count", 6),
            ("遥测(YC):", "yc_count", 8),
            ("遥控(YK):", "yk_count", 2),
            ("遥调(YT):", "yt_count", 2),
        ]:
            points_layout.addWidget(QLabel(label))
            spin = QSpinBox()
            spin.setRange(0, 999)
            spin.setValue(default)
            spin.setMaximumWidth(60)
            setattr(self, attr, spin)
            points_layout.addWidget(spin)

        self.btn_apply_points = QPushButton("应用配置")
        self.btn_apply_points.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; padding: 4px 12px; border-radius: 3px; border: none; }"
            "QPushButton:hover { background-color: #1565C0; }"
        )
        self.btn_apply_points.clicked.connect(self._on_apply_points)
        points_layout.addWidget(self.btn_apply_points)

        # 初始化按钮
        self.btn_reset = QPushButton("初始化")
        self.btn_reset.setStyleSheet(
            "QPushButton { background-color: #795548; color: white; padding: 4px 12px; border-radius: 3px; border: none; }"
            "QPushButton:hover { background-color: #4E342E; }"
        )
        self.btn_reset.setToolTip("清空所有数据和日志，恢复初始状态")
        self.btn_reset.clicked.connect(self._on_reset)
        points_layout.addWidget(self.btn_reset)

        config_layout.addWidget(self.points_group)

        # 主站：快捷操作按钮
        self.master_btns_group = QGroupBox("主站操作")
        master_btns_layout = QHBoxLayout(self.master_btns_group)
        master_btns_layout.setSpacing(6)
        master_btns_layout.setContentsMargins(6, 4, 6, 4)

        for label, slot, color in [
            ("总召唤", self._on_gi, "#4CAF50"),
            ("时钟同步", self._on_clock_sync, "#2196F3"),
            ("STARTDT", self._on_startdt, "#FF9800"),
            ("STOPDT", self._on_stopdt, "#9E9E9E"),
            ("TESTFR", self._on_testfr, "#9C27B0"),
        ]:
            btn = QPushButton(label)
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {color}; color: white; padding: 4px 10px; border-radius: 3px; border: none; font-size: 9pt; }}"
                f"QPushButton:hover {{ opacity: 0.8; }}"
            )
            btn.clicked.connect(slot)
            btn.setEnabled(False)
            master_btns_layout.addWidget(btn)
            setattr(self, f'_btn_{label}', btn)

        config_layout.addWidget(self.master_btns_group)

        # 导出/导入按钮
        io_group = QGroupBox("配置")
        io_layout = QHBoxLayout(io_group)
        io_layout.setSpacing(6)
        io_layout.setContentsMargins(6, 4, 6, 4)

        btn_export = QPushButton("导出")
        btn_export.setStyleSheet(
            "QPushButton { background-color: #607D8B; color: white; padding: 4px 10px; border-radius: 3px; border: none; font-size: 9pt; }"
            "QPushButton:hover { background-color: #455A64; }"
        )
        btn_export.clicked.connect(self._on_export)
        io_layout.addWidget(btn_export)

        btn_import = QPushButton("导入")
        btn_import.setStyleSheet(
            "QPushButton { background-color: #607D8B; color: white; padding: 4px 10px; border-radius: 3px; border: none; font-size: 9pt; }"
            "QPushButton:hover { background-color: #455A64; }"
        )
        btn_import.clicked.connect(self._on_import)
        io_layout.addWidget(btn_import)

        config_layout.addWidget(io_group)

        # 自动上送配置 (从站模式)
        self.auto_group = QGroupBox("自动上送 (从站模式)")
        auto_layout = QHBoxLayout(self.auto_group)
        auto_layout.setSpacing(6)

        self.chk_auto_send = QCheckBox("启用")
        self.chk_auto_send.stateChanged.connect(self._on_auto_send_changed)
        auto_layout.addWidget(self.chk_auto_send)

        auto_layout.addWidget(QLabel("间隔:"))
        self.auto_interval_spin = QSpinBox()
        self.auto_interval_spin.setRange(100, 60000)
        self.auto_interval_spin.setValue(2000)
        self.auto_interval_spin.setSuffix(" ms")
        self.auto_interval_spin.setMaximumWidth(90)
        auto_layout.addWidget(self.auto_interval_spin)

        auto_layout.addWidget(QLabel("幅度:"))
        self.auto_range_spin = QDoubleSpinBox()
        self.auto_range_spin.setRange(0.01, 9999)
        self.auto_range_spin.setValue(10)
        self.auto_range_spin.setMaximumWidth(80)
        auto_layout.addWidget(self.auto_range_spin)

        self.auto_timer = QTimer()
        self.auto_timer.timeout.connect(self._on_auto_tick)

        self.lbl_auto_status = QLabel("")
        self.lbl_auto_status.setStyleSheet("color: #4CAF50; font-size: 9pt;")
        auto_layout.addWidget(self.lbl_auto_status)

        config_layout.addWidget(self.auto_group)
        parent_layout.addLayout(config_layout)

    def _setup_tabs(self, parent_layout):
        """标签页"""
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        # 遥信
        self.yx_table = self._create_point_table(
            ["序号", "IOA", "IOA(10)", "描述", "当前值", "品质", "更新时间"],
            [40, 70, 60, 120, 100, 60, 90]
        )
        self.tabs.addTab(self.yx_table, "遥信 (YX)")

        # 遥测
        self.yc_table = self._create_point_table(
            ["序号", "IOA", "IOA(10)", "描述", "当前值", "品质", "更新时间"],
            [40, 70, 60, 120, 120, 60, 90]
        )
        self.tabs.addTab(self.yc_table, "遥测 (YC)")

        # 遥控
        self.yk_table = self._create_point_table(
            ["序号", "IOA", "IOA(10)", "描述", "当前值", "操作"],
            [40, 70, 60, 120, 80, 180]
        )
        self.tabs.addTab(self.yk_table, "遥控 (YK)")

        # 遥调
        self.yt_table = self._create_point_table(
            ["序号", "IOA", "IOA(10)", "描述", "当前值", "操作"],
            [40, 70, 60, 120, 100, 200]
        )
        self.tabs.addTab(self.yt_table, "遥调 (YT)")

        # 原始报文
        self.log_table = QTableWidget()
        self.log_table.setColumnCount(5)
        self.log_table.setHorizontalHeaderLabels(["序号", "时间", "方向", "类型", "内容"])
        self.log_table.setAlternatingRowColors(True)
        self.log_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.log_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.log_table.horizontalHeader().setStretchLastSection(True)
        self.log_table.setColumnWidth(0, 40)
        self.log_table.setColumnWidth(1, 100)
        self.log_table.setColumnWidth(2, 40)
        self.log_table.setColumnWidth(3, 80)
        self.log_table.cellDoubleClicked.connect(self._on_log_double_click)
        self.tabs.addTab(self.log_table, "原始报文")

        parent_layout.addWidget(self.tabs, 1)

    def _setup_manual_send(self, parent_layout):
        """手动发送区"""
        manual_layout = QHBoxLayout()
        manual_layout.setSpacing(6)

        lbl = QLabel("手动Hex:")
        lbl.setStyleSheet("font-weight: bold;")
        manual_layout.addWidget(lbl)

        self.hex_combo = QComboBox()
        self.hex_combo.setEditable(True)
        self.hex_combo.setPlaceholderText("输入十六进制报文，如: 68 04 07 00 00 00")
        self.hex_combo.setFont(QFont("Consolas", 9))
        self.hex_combo.setMinimumWidth(300)
        self.hex_combo.lineEdit().returnPressed.connect(self._on_manual_send)
        # 预置常用帧
        self.hex_combo.addItem("68 04 07 00 00 00", "STARTDT_ACT")
        self.hex_combo.addItem("68 04 13 00 00 00", "STOPDT_ACT")
        self.hex_combo.addItem("68 04 43 00 00 00", "TESTFR_ACT")
        self._hex_history = set()
        manual_layout.addWidget(self.hex_combo, 1)

        btn_send = QPushButton("发送")
        btn_send.setStyleSheet(
            "QPushButton { background-color: #FF9800; color: white; padding: 4px 14px; border-radius: 3px; border: none; }"
            "QPushButton:hover { background-color: #E65100; }"
        )
        btn_send.clicked.connect(self._on_manual_send)
        manual_layout.addWidget(btn_send)

        btn_clear_log = QPushButton("清空日志")
        btn_clear_log.clicked.connect(lambda: self.log_table.setRowCount(0))
        manual_layout.addWidget(btn_clear_log)

        parent_layout.addLayout(manual_layout)

    def _create_point_table(self, headers, widths):
        """创建点位表格（描述列可编辑）"""
        table = QTableWidget()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        # 允许编辑（用于描述列）
        table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        table.horizontalHeader().setStretchLastSection(True)
        for i, w in enumerate(widths):
            table.setColumnWidth(i, w)
        # cellChanged 信号用于保存描述编辑
        table.cellChanged.connect(self._on_cell_changed)
        return table

    # ==================================================================
    # 描述编辑
    # ==================================================================

    def _on_cell_changed(self, row, col):
        """单元格内容改变（仅描述列）"""
        if self._block_cell_changed:
            return
        # 找到是哪个表格
        table = self.sender()
        if table not in (self.yx_table, self.yc_table, self.yk_table, self.yt_table):
            return
        # 描述列索引：序号(0) IOA(1) IOA(10)(2) 描述(3)
        desc_col = 3
        if col != desc_col:
            return
        # 获取 IOA
        ioa_item = table.item(row, 1)
        desc_item = table.item(row, desc_col)
        if not ioa_item or not desc_item:
            return
        try:
            ioa = int(ioa_item.text(), 16)
        except ValueError:
            return
        new_desc = desc_item.text()
        if ioa in self._current_points:
            self._current_points[ioa].description = new_desc
        if self.simulator and ioa in self.simulator.points:
            self.simulator.points[ioa].description = new_desc

    # ==================================================================
    # 连接控制
    # ==================================================================

    def _set_role_silently(self, index: int):
        """程序化设置角色（重置/加载配置时使用，不触发切换确认）"""
        self._suppress_role_confirm = True
        try:
            self.role_combo.setCurrentIndex(index)
        finally:
            self._suppress_role_confirm = False

    def _on_role_changed(self, index):
        """角色切换"""
        # 运行中切换需确认；用户选择"否"时回退下拉框，保持界面与实际角色一致
        if (not self._suppress_role_confirm
                and index != self._last_role_index
                and self.simulator and self.simulator.isRunning()):
            ret = QMessageBox.question(
                self, "切换角色",
                "模拟器正在运行，切换角色将重新创建点位并断开当前会话。确定切换吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                self._suppress_role_confirm = True
                try:
                    self.role_combo.setCurrentIndex(self._last_role_index)
                finally:
                    self._suppress_role_confirm = False
                return
        self._last_role_index = index

        is_slave = (index == 1)
        self.ip_input.setText("0.0.0.0" if is_slave else "127.0.0.1")
        self.auto_group.setVisible(is_slave)
        # 点位数量在两种角色下都可见：主站模式用于配置遥控/遥调下发点位
        self.points_group.setVisible(True)
        self.master_btns_group.setVisible(not is_slave)
        # 重新创建点位
        self._on_apply_points()

    def _on_start(self):
        """启动模拟器"""
        if self.simulator and self.simulator.isRunning():
            return

        role = self.role_combo.currentData()
        host = self.ip_input.text().strip()
        port = self.port_input.value()

        self.simulator = IEC104Simulator(role)
        self.simulator.points = dict(self._current_points)
        self.simulator.frame_sent.connect(self._on_frame_sent)
        self.simulator.frame_received.connect(self._on_frame_received)
        self.simulator.state_changed.connect(self._on_state_changed)
        self.simulator.error_occurred.connect(self._on_error)
        self.simulator.log_message.connect(self._on_log)
        self.simulator.connection_established.connect(self._on_connected)
        self.simulator.connection_lost.connect(self._on_disconnected)
        self.simulator.point_updated.connect(self._on_point_updated)

        if role == SimulatorRole.SLAVE:
            self.simulator.start_server(host, port)
        else:
            self.simulator.connect_to(host, port)

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        # 操作按钮在连接成功后(_on_connected)才启用，
        # 避免连接前点击总召唤/STARTDT 等无响应让用户困惑
        self._set_ops_enabled(False)

    def _on_stop(self):
        """停止模拟器"""
        if self.simulator:
            self.simulator.stop()
            self.simulator.wait(2000)
            self.simulator = None
        self.auto_timer.stop()
        self.chk_auto_send.setChecked(False)
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._set_ops_enabled(False)
        self.lbl_status.setText("● 未连接")
        self.lbl_status.setStyleSheet("color: #999; font-weight: bold;")

    def _on_reset(self):
        """初始化：清空所有数据和日志"""
        if self.simulator and self.simulator.isRunning():
            QMessageBox.warning(self, "提示", "请先停止模拟器")
            return
        ret = QMessageBox.question(
            self, "确认初始化",
            "将清空所有点位数据、日志和配置，恢复初始状态。确定吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if ret != QMessageBox.Yes:
            return
        # 停止模拟器
        self._on_stop()
        # 清空数据
        self._current_points = {}
        # 重置配置
        self._set_role_silently(0)
        self.ip_input.setText("127.0.0.1")
        self.port_input.setValue(2404)
        self.yx_count.setValue(6)
        self.yc_count.setValue(8)
        self.yk_count.setValue(2)
        self.yt_count.setValue(2)
        self.chk_auto_send.setChecked(False)
        self.auto_interval_spin.setValue(2000)
        self.auto_range_spin.setValue(10)
        # 清空日志
        self.log_table.setRowCount(0)
        # 清空 hex 历史
        self.hex_combo.clear()
        self.hex_combo.addItem("68 04 07 00 00 00", "STARTDT_ACT")
        self.hex_combo.addItem("68 04 13 00 00 00", "STOPDT_ACT")
        self.hex_combo.addItem("68 04 43 00 00 00", "TESTFR_ACT")
        self._hex_history = set()
        # 重新初始化
        self._on_apply_points()
        self.lbl_auto_status.setText("")
        # 删除配置文件
        try:
            if os.path.exists(CONFIG_FILE):
                os.remove(CONFIG_FILE)
        except Exception:
            pass

    def closeEvent(self, event):
        """关闭时保存配置"""
        if self.simulator and self.simulator.isRunning():
            self.simulator.stop()
            self.simulator.wait(2000)
        self.auto_timer.stop()
        self._save_config()
        event.accept()

    # ==================================================================
    # 配置导出/导入/保存/加载
    # ==================================================================

    def _get_config_data(self):
        """获取当前配置数据"""
        return {
            "role": self.role_combo.currentIndex(),
            "ip": self.ip_input.text(),
            "port": self.port_input.value(),
            "yx_count": self.yx_count.value(),
            "yc_count": self.yc_count.value(),
            "yk_count": self.yk_count.value(),
            "yt_count": self.yt_count.value(),
            "auto_interval": self.auto_interval_spin.value(),
            "auto_range": self.auto_range_spin.value(),
            "points": {
                str(ioa): {
                    "ioa": pt.ioa,
                    "signal_type": pt.signal_type,
                    "value": pt.value,
                    "description": pt.description,
                }
                for ioa, pt in self._current_points.items()
            }
        }

    def _apply_config_data(self, data):
        """应用配置数据"""
        self._block_cell_changed = True
        try:
            self._set_role_silently(data.get("role", 0))
            self.ip_input.setText(data.get("ip", "127.0.0.1"))
            self.port_input.setValue(data.get("port", 2404))
            self.yx_count.setValue(data.get("yx_count", 6))
            self.yc_count.setValue(data.get("yc_count", 8))
            self.yk_count.setValue(data.get("yk_count", 2))
            self.yt_count.setValue(data.get("yt_count", 2))
            self.auto_interval_spin.setValue(data.get("auto_interval", 2000))
            self.auto_range_spin.setValue(data.get("auto_range", 10))

            # 恢复点位
            points_data = data.get("points", {})
            if points_data:
                self._current_points = {}
                for ioa_str, pd in points_data.items():
                    ioa = pd["ioa"]
                    self._current_points[ioa] = PointData(
                        ioa=ioa,
                        signal_type=pd.get("signal_type", "YC"),
                        value=pd.get("value", 0),
                        description=pd.get("description", ""),
                    )
            else:
                self._on_apply_points()

            self._refresh_all_tables()
        finally:
            self._block_cell_changed = False

    def _save_config(self):
        """保存配置到文件"""
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self._get_config_data(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存模拟器配置失败: {e}")

    def _load_config(self):
        """从文件加载配置"""
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._apply_config_data(data)
        except Exception as e:
            print(f"加载模拟器配置失败: {e}")

    def _on_export(self):
        """导出配置"""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出模拟器配置", "simulator_config.json",
            "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self._get_config_data(), f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "导出成功", f"配置已导出到:\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    def _on_import(self):
        """导入配置"""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入模拟器配置", "",
            "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._apply_config_data(data)
            QMessageBox.information(self, "导入成功", "配置已导入")
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))

    # ==================================================================
    # 点位管理
    # ==================================================================

    def _on_apply_points(self):
        """应用点位配置（主站/从站均按数量生成点位表）"""
        has_data = any(pt.is_valid for pt in self._current_points.values())
        if has_data:
            ret = QMessageBox.question(
                self, "确认", "当前点位数据将被重置，确定要重新配置吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if ret != QMessageBox.Yes:
                return

        # 主站模式下同样生成 YK/YT 点位，才能在遥控/遥调表中下发命令
        yx = self.yx_count.value()
        yc = self.yc_count.value()
        yk = self.yk_count.value()
        yt = self.yt_count.value()
        self._current_points = create_default_points(yx, yc, yk, yt)
        self._refresh_all_tables()
        if self.simulator:
            self.simulator.points = dict(self._current_points)

    def _refresh_all_tables(self):
        """刷新所有表格"""
        self._block_cell_changed = True
        try:
            self._refresh_yx_table()
            self._refresh_yc_table()
            self._refresh_yk_table()
            self._refresh_yt_table()
        finally:
            self._block_cell_changed = False

    def _refresh_yx_table(self):
        """刷新遥信表格"""
        yx_points = sorted(
            [p for p in self._current_points.values() if p.signal_type == "YX"],
            key=lambda p: p.ioa
        )
        is_slave = self.role_combo.currentData() == SimulatorRole.SLAVE
        self.yx_table.setRowCount(len(yx_points))

        for i, pt in enumerate(yx_points):
            self.yx_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.yx_table.setItem(i, 1, QTableWidgetItem(f"0x{pt.ioa:04X}"))
            self.yx_table.setItem(i, 2, QTableWidgetItem(str(pt.ioa)))
            self.yx_table.setItem(i, 3, QTableWidgetItem(pt.description))

            # 当前值
            if not pt.is_valid and not is_slave:
                val_item = QTableWidgetItem("-")
                val_item.setForeground(INVALID_COLOR)
                val_item.setBackground(GRAY_BG)
                self.yx_table.setItem(i, 4, val_item)
            elif is_slave:
                btn = QPushButton("合" if pt.value else "分")
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: {'#4CAF50' if pt.value else '#9E9E9E'}; "
                    f"color: white; font-weight: bold; padding: 3px 8px; border-radius: 3px; border: none; min-width: 50px; }}"
                    "QPushButton:hover { opacity: 0.8; }"
                )
                btn.clicked.connect(lambda _, ioa=pt.ioa: self._on_toggle_yx(ioa))
                self.yx_table.setCellWidget(i, 4, btn)
            else:
                val_item = QTableWidgetItem("合" if pt.value else "分")
                val_item.setForeground(YX_ON_COLOR if pt.value else YX_OFF_COLOR)
                self.yx_table.setItem(i, 4, val_item)

            q_text = "有效" if pt.quality == 0 else f"0x{pt.quality:02X}"
            self.yx_table.setItem(i, 5, QTableWidgetItem(q_text))
            self.yx_table.setItem(i, 6, QTableWidgetItem(pt.timestamp or "-"))

    def _refresh_yc_table(self):
        """刷新遥测表格"""
        yc_points = sorted(
            [p for p in self._current_points.values() if p.signal_type == "YC"],
            key=lambda p: p.ioa
        )
        is_slave = self.role_combo.currentData() == SimulatorRole.SLAVE
        self.yc_table.setRowCount(len(yc_points))

        for i, pt in enumerate(yc_points):
            self.yc_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.yc_table.setItem(i, 1, QTableWidgetItem(f"0x{pt.ioa:04X}"))
            self.yc_table.setItem(i, 2, QTableWidgetItem(str(pt.ioa)))
            self.yc_table.setItem(i, 3, QTableWidgetItem(pt.description))

            if is_slave:
                spin = QDoubleSpinBox()
                spin.setRange(-999999, 999999)
                spin.setDecimals(2)
                spin.setValue(float(pt.value) if pt.value is not None else 0)
                spin.setStyleSheet("QDoubleSpinBox { padding: 2px; }")
                spin.setProperty("ioa", pt.ioa)
                spin.editingFinished.connect(lambda s=spin: self._on_yc_edit_finished(s))
                self.yc_table.setCellWidget(i, 4, spin)
            elif not pt.is_valid:
                val_item = QTableWidgetItem("-")
                val_item.setForeground(INVALID_COLOR)
                val_item.setBackground(GRAY_BG)
                self.yc_table.setItem(i, 4, val_item)
            else:
                val_text = f"{pt.value:.2f}" if isinstance(pt.value, (int, float)) else str(pt.value)
                self.yc_table.setItem(i, 4, QTableWidgetItem(val_text))

            q_text = "有效" if pt.quality == 0 else f"0x{pt.quality:02X}"
            self.yc_table.setItem(i, 5, QTableWidgetItem(q_text))
            self.yc_table.setItem(i, 6, QTableWidgetItem(pt.timestamp or "-"))

    def _refresh_yk_table(self):
        """刷新遥控表格"""
        yk_points = sorted(
            [p for p in self._current_points.values() if p.signal_type == "YK"],
            key=lambda p: p.ioa
        )
        is_slave = self.role_combo.currentData() == SimulatorRole.SLAVE
        self.yk_table.setRowCount(len(yk_points))

        for i, pt in enumerate(yk_points):
            self.yk_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.yk_table.setItem(i, 1, QTableWidgetItem(f"0x{pt.ioa:04X}"))
            self.yk_table.setItem(i, 2, QTableWidgetItem(str(pt.ioa)))
            self.yk_table.setItem(i, 3, QTableWidgetItem(pt.description))

            val_text = "合" if pt.value else "分"
            val_item = QTableWidgetItem(val_text)
            val_item.setForeground(YX_ON_COLOR if pt.value else YX_OFF_COLOR)
            self.yk_table.setItem(i, 4, val_item)

            if is_slave:
                self.yk_table.setItem(i, 5, QTableWidgetItem("(等待主站遥控)"))
            else:
                ops = QWidget()
                ops_layout = QHBoxLayout(ops)
                ops_layout.setContentsMargins(4, 2, 4, 2)
                ops_layout.setSpacing(6)
                btn_on = QPushButton("合")
                btn_on.setStyleSheet(
                    "QPushButton { background-color: #4CAF50; color: white; padding: 3px 10px; border-radius: 3px; border: none; }"
                    "QPushButton:hover { background-color: #388E3C; }"
                )
                btn_on.clicked.connect(lambda _, ioa=pt.ioa: self._on_yk_command(ioa, 1))
                btn_off = QPushButton("分")
                btn_off.setStyleSheet(
                    "QPushButton { background-color: #9E9E9E; color: white; padding: 3px 10px; border-radius: 3px; border: none; }"
                    "QPushButton:hover { background-color: #757575; }"
                )
                btn_off.clicked.connect(lambda _, ioa=pt.ioa: self._on_yk_command(ioa, 0))
                ops_layout.addWidget(btn_on)
                ops_layout.addWidget(btn_off)
                ops_layout.addStretch()
                self.yk_table.setCellWidget(i, 5, ops)

    def _refresh_yt_table(self):
        """刷新遥调表格"""
        yt_points = sorted(
            [p for p in self._current_points.values() if p.signal_type == "YT"],
            key=lambda p: p.ioa
        )
        is_slave = self.role_combo.currentData() == SimulatorRole.SLAVE
        self.yt_table.setRowCount(len(yt_points))

        for i, pt in enumerate(yt_points):
            self.yt_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.yt_table.setItem(i, 1, QTableWidgetItem(f"0x{pt.ioa:04X}"))
            self.yt_table.setItem(i, 2, QTableWidgetItem(str(pt.ioa)))
            self.yt_table.setItem(i, 3, QTableWidgetItem(pt.description))

            val_text = f"{pt.value:.2f}" if isinstance(pt.value, (int, float)) else str(pt.value)
            self.yt_table.setItem(i, 4, QTableWidgetItem(val_text))

            if is_slave:
                self.yt_table.setItem(i, 5, QTableWidgetItem("(等待主站遥调)"))
            else:
                ops = QWidget()
                ops_layout = QHBoxLayout(ops)
                ops_layout.setContentsMargins(4, 2, 4, 2)
                ops_layout.setSpacing(6)
                spin = QDoubleSpinBox()
                spin.setRange(-999999, 999999)
                spin.setDecimals(2)
                spin.setValue(float(pt.value) if pt.value is not None else 0)
                spin.setMaximumWidth(100)
                ops_layout.addWidget(spin)
                btn_set = QPushButton("设定")
                btn_set.setStyleSheet(
                    "QPushButton { background-color: #2196F3; color: white; padding: 3px 10px; border-radius: 3px; border: none; }"
                    "QPushButton:hover { background-color: #1565C0; }"
                )
                btn_set.clicked.connect(lambda _, ioa=pt.ioa, s=spin: self._on_yt_command(ioa, s.value()))
                ops_layout.addWidget(btn_set)
                ops_layout.addStretch()
                self.yt_table.setCellWidget(i, 5, ops)

    # ==================================================================
    # 主站操作
    # ==================================================================

    def _on_gi(self):
        if self.simulator and self.simulator.is_connected:
            self.simulator.send_general_interrogation(common_addr=1)

    def _on_clock_sync(self):
        if self.simulator and self.simulator.is_connected:
            self.simulator.send_clock_sync(common_addr=1)

    def _on_startdt(self):
        if self.simulator:
            self.simulator.send_startdt()

    def _on_stopdt(self):
        if self.simulator:
            self.simulator.send_stopdt()

    def _on_testfr(self):
        if self.simulator:
            self.simulator.send_testfr()

    # ==================================================================
    # 点位操作
    # ==================================================================

    def _on_toggle_yx(self, ioa):
        """切换遥信值 (从站模式)"""
        if ioa not in self._current_points:
            return
        pt = self._current_points[ioa]
        new_val = not bool(pt.value)
        pt.value = new_val
        pt.is_valid = True
        pt.timestamp = __import__('time').strftime("%H:%M:%S")
        if self.simulator:
            self.simulator.set_point_value(ioa, new_val)
        self._refresh_yx_table()

    def _on_yc_edit_finished(self, spin):
        """遥测值编辑完成"""
        ioa = spin.property("ioa")
        if ioa is None or ioa not in self._current_points:
            return
        value = spin.value()
        pt = self._current_points[ioa]
        old_val = pt.value
        pt.value = value
        pt.is_valid = True
        pt.timestamp = __import__('time').strftime("%H:%M:%S")
        if self.simulator and old_val != value:
            self.simulator.set_point_value(ioa, value)

    def _on_yk_command(self, ioa, value):
        if self.simulator and self.simulator.is_connected:
            self.simulator.send_i_frame(type_id=45, cot=6, common_addr=1, ioa=ioa, value=value)

    def _on_yt_command(self, ioa, value):
        if self.simulator and self.simulator.is_connected:
            self.simulator.send_i_frame(type_id=50, cot=6, common_addr=1, ioa=ioa, value=value)

    def _on_auto_send_changed(self, state):
        if self.simulator:
            self.simulator._auto_send_enabled = (state == Qt.Checked)
        if state == Qt.Checked:
            self.auto_timer.start(self.auto_interval_spin.value())
        else:
            self.auto_timer.stop()

    def _on_auto_tick(self):
        """定时自动变化数据"""
        if not self.simulator or not self.simulator.is_connected:
            return
        import random
        changed = []
        yc_points = [p for p in self.simulator.points.values() if p.signal_type == "YC"]
        if yc_points:
            pt = random.choice(yc_points)
            old_val = pt.value if isinstance(pt.value, (int, float)) else 0
            delta = self.auto_range_spin.value()
            new_val = round(old_val + random.uniform(-delta, delta), 2)
            self.simulator.set_point_value(pt.ioa, new_val)
            if pt.ioa in self._current_points:
                self._current_points[pt.ioa].value = new_val
                self._current_points[pt.ioa].is_valid = True
            self._refresh_yc_table()
            changed.append(f"YC:{pt.ioa}")
        yx_points = [p for p in self.simulator.points.values() if p.signal_type == "YX"]
        if yx_points and random.random() < 0.3:
            pt = random.choice(yx_points)
            new_val = not bool(pt.value)
            self.simulator.set_point_value(pt.ioa, new_val)
            if pt.ioa in self._current_points:
                self._current_points[pt.ioa].value = new_val
                self._current_points[pt.ioa].is_valid = True
            self._refresh_yx_table()
            changed.append(f"YX:{pt.ioa}")
        if changed:
            self.lbl_auto_status.setText(f"已变化: {', '.join(changed)}")

    def _on_manual_send(self):
        if not self.simulator or not self.simulator.is_connected:
            QMessageBox.warning(self, "提示", "请先启动连接")
            return
        hex_text = self.hex_combo.currentText().strip()
        if not hex_text:
            return

        # 发送前校验：静默过滤会掩盖粘贴错误，这里明确拒绝非法输入
        raw_compact = re.sub(r'[\s.]', '', hex_text)      # 去除空格/点分隔符
        m = re.fullmatch(r'(?:0[xX])?([0-9a-fA-F]+)', raw_compact)
        if not m:
            QMessageBox.warning(
                self, "报文格式无效",
                f"无法发送: '{hex_text}'\n\n包含非十六进制字符，请检查后重新输入。")
            return
        frame_hex = m.group(1)
        if len(frame_hex) < 12 or len(frame_hex) % 2 != 0 \
                or frame_hex[:2].lower() != '68':
            QMessageBox.warning(
                self, "报文格式无效",
                f"无法发送: '{hex_text}'\n\n"
                "要求: 以 68 开头的完整 IEC104 帧（至少6字节，长度为偶数）。\n"
                f"当前有效长度: {len(frame_hex) // 2} 字节。")
            return

        self.simulator.send_frame(frame_hex)
        if hex_text not in self._hex_history:
            self._hex_history.add(hex_text)
            self.hex_combo.addItem(hex_text)
        self.hex_combo.setCurrentText("")

    # ==================================================================
    # 连接事件
    # ==================================================================

    def _on_connected(self):
        self._set_ops_enabled(True)
        self.lbl_status.setText("● 已连接")
        self.lbl_status.setStyleSheet("color: #4CAF50; font-weight: bold;")

    def _on_disconnected(self):
        self._set_ops_enabled(False)
        self.lbl_status.setText("● 已断开")
        self.lbl_status.setStyleSheet("color: #f44336; font-weight: bold;")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def _on_state_changed(self, text):
        if self.simulator:
            state = self.simulator.state
            colors = {
                ConnectionState.IDLE: "#999",
                ConnectionState.CONNECTING: "#FF9800",
                ConnectionState.CONNECTED: "#4CAF50",
                ConnectionState.STARTED: "#2196F3",
            }
            self.lbl_status.setText(f"● {state.value}")
            self.lbl_status.setStyleSheet(f"color: {colors.get(state, '#999')}; font-weight: bold;")

    def _on_error(self, msg):
        self._add_log_row("❌", "错误", msg)
        # 尚未成功连接时的错误（端口被占用/对端拒连等）同步反映到状态标签，
        # 避免用户只能翻日志才知道启动失败
        if self.simulator is None or not self.simulator.is_connected:
            if "已连接" not in self.lbl_status.text():
                self.lbl_status.setText("● 连接失败")
                self.lbl_status.setStyleSheet("color: #f44336; font-weight: bold;")
                self.lbl_status.setToolTip(msg[:200])

    def _on_log(self, msg):
        direction = "收" if "[收]" in msg else ("发" if "[发]" in msg else "")
        self._add_log_row(direction, "协议", msg)

    def _on_frame_sent(self, hex_str):
        self._add_frame_row("发", hex_str)
        if self.simulator:
            self.lbl_seq.setText(f"SN:{self.simulator.send_seq} RN:{self.simulator.recv_seq}")

    def _on_frame_received(self, hex_str):
        self._add_frame_row("收", hex_str)
        if self.simulator:
            self.lbl_seq.setText(f"SN:{self.simulator.send_seq} RN:{self.simulator.recv_seq}")

    def _on_log_double_click(self, row, col):
        item = self.log_table.item(row, 4)
        if item:
            hex_data = item.data(Qt.UserRole)
            if hex_data:
                from PyQt5.QtWidgets import QApplication
                QApplication.clipboard().setText(hex_data)

    # ==================================================================
    # 点位更新回调
    # ==================================================================

    def _on_point_updated(self, ioa, pt):
        """模拟器点位数据更新"""
        if ioa not in self._current_points:
            self._current_points[ioa] = pt
        else:
            self._current_points[ioa].value = pt.value
            self._current_points[ioa].is_valid = pt.is_valid
            self._current_points[ioa].quality = pt.quality
            self._current_points[ioa].timestamp = pt.timestamp

        if pt.signal_type == "YX":
            self._refresh_yx_table()
        elif pt.signal_type == "YC":
            self._refresh_yc_table()
        elif pt.signal_type == "YK":
            self._refresh_yk_table()
        elif pt.signal_type == "YT":
            self._refresh_yt_table()

    # ==================================================================
    # 辅助
    # ==================================================================

    def _set_ops_enabled(self, enabled):
        for name in ["总召唤", "时钟同步", "STARTDT", "STOPDT", "TESTFR"]:
            btn = getattr(self, f'_btn_{name}', None)
            if btn:
                btn.setEnabled(enabled)
        if not enabled:
            self.auto_timer.stop()
            self.chk_auto_send.setChecked(False)

    def _add_frame_row(self, direction, hex_str):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        # 复用独立解析器实例（每帧解析前清空状态，避免逐帧重建的开销）
        self._frame_parser.clear()
        frame = self._frame_parser.parse_hex(hex_str)
        ftype = frame.apci.frame_type if frame.apci else "-"
        desc = frame.description or frame.error or ""

        row = self.log_table.rowCount()
        self.log_table.insertRow(row)
        bg = BLUE_LIGHT if direction == "发" else PURPLE_LIGHT
        seq = str(row + 1)
        for col, text in enumerate([seq, ts, direction, ftype, desc]):
            item = QTableWidgetItem(text)
            item.setBackground(bg)
            self.log_table.setItem(row, col, item)
        self.log_table.item(row, 4).setData(Qt.UserRole, hex_str)
        self.log_table.scrollToBottom()
        if self.log_table.rowCount() > 2000:
            self.log_table.removeRow(0)

    def _add_log_row(self, direction, category, message):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        row = self.log_table.rowCount()
        self.log_table.insertRow(row)
        seq = str(row + 1)
        for col, text in enumerate([seq, ts, direction, category, message]):
            self.log_table.setItem(row, col, QTableWidgetItem(text))
        self.log_table.scrollToBottom()
        if self.log_table.rowCount() > 2000:
            self.log_table.removeRow(0)
