"""
IEC104 通信模拟器对话框
表格化显示四遥数据，支持主站/从站模式
从站模式下可直接编辑点位值和描述
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QGroupBox,
    QMessageBox, QSpinBox, QTableWidget, QTableWidgetItem,
    QCheckBox, QTabWidget,
    QDoubleSpinBox, QFileDialog, QFormLayout, QDialogButtonBox
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont

import csv
import json
import os
import re
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from iec104_simulator import (
    IEC104Simulator, SimulatorRole, ConnectionState,
    PointData, create_default_points
)
from iec104_parser import IEC104Parser, hex_to_bytes
from app_paths import get_app_dir
from gui.sim_point_tables import PointTablesMixin
from gui.sim_style import (
    YX_ON_COLOR,
    YX_OFF_COLOR,
    BLUE_LIGHT,
    PURPLE_LIGHT,
    BLUE_DARK,
    PURPLE_DARK,
    RED_LIGHT,
    RED_DARK,
    ERROR_FG_LIGHT,
    ERROR_FG_DARK,
    LOCAL_FG,
    START_BTN_ACTIVE,
    START_BTN_INACTIVE,
    STOP_BTN_ACTIVE,
    STOP_BTN_INACTIVE,
    SEL_BTN_NORMAL,
    SEL_BTN_ARMED,
    YK_BTN_ON_NORMAL,
    YK_BTN_ON_ARMED,
    YK_BTN_OFF_NORMAL,
    YK_BTN_OFF_ARMED,
)


# 样式常量
# 深色主题下的收发日志行背景（暗色系，避免浅色刺眼）
# 错误日志行背景（浅/深主题）

# 点位表格里允许编辑的列（描述）

# 启动/停止按钮点亮样式：当前状态对应的按钮高亮，另一按钮灰暗

# 遥控/遥调：合(绿)/分(红) 采用电力行业"合位绿、分位红"语义；
# 预制按钮在"已预制(armed)"时转绿并加粗，与初始橙色明确区分
# 遥控行按钮：预制阶段(预制合/预制分)与执行阶段(执行合/执行分)同色系，
# 执行态加粗描边以示"已解锁可下发"；合绿/分红语义保持

# IEC 104 协议参数默认值（对齐 lib60870 / QTester104 等专业工具暴露项）
DEFAULT_PARAMS = {
    "t1": 15.0,        # 发送 I 帧后等待确认超时 (s)
    "t2": 10.0,        # 收到 I 帧后延迟发 S 帧确认 (s)，需 < t1
    "t3": 20.0,        # 空闲时发送 TESTFR 周期 (s)
    "k": 12,           # 发送窗口（未被确认的最大 I 帧数）
    "w": 8,            # 接收窗口（发 S 帧前可收的 I 帧数），需 <= k
    "ca": 1,           # ASDU 公共地址（站地址）
    "gi_period": 0,    # 主站周期总召唤间隔 (s)，0=仅连接后总召唤一次
    "base_yx": 0x0001, # 遥信 IOA 基址
    "base_yc": 0x4001, # 遥测 IOA 基址
    "base_yk": 0x6001, # 遥控 IOA 基址
    "base_yt": 0x6201, # 遥调 IOA 基址
    "soe": False,      # 遥信/遥测带 CP56Time2a 时标 (SOE)
}

def _get_app_dir() -> str:
    """获取应用数据目录（统一实现见 app_paths.get_app_dir）"""
    return get_app_dir()


# 配置文件路径
CONFIG_DIR = os.path.join(_get_app_dir(), "config")
CONFIG_FILE = os.path.join(CONFIG_DIR, "simulator_config.json")


def _short_error(msg: str) -> str:
    """从错误日志提取一句话原因，用于状态标签（完整信息放 tooltip）"""
    m = re.search(r'失败[:：]\s*([^（(]+)', msg)
    if m:
        return m.group(1).strip()[:20]
    m = re.search(r'失败（([^）]+)）', msg)
    if m:
        return m.group(1)[:20]
    return msg.strip()[:20] or "连接失败"


class SimulatorDialog(PointTablesMixin, QDialog):
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
        # 遥控/遥调操作控件引用：YK {ioa: {"btn_on":.., "btn_off":.., "selected_cmd":..}}
        #                         YT {ioa: {"btn_set":.., "btn_select":..}}
        self._yk_ops = {}
        self._yt_ops = {}
        # IEC 104 协议参数（协议参数对话框配置，随配置文件持久化）
        self._params = dict(DEFAULT_PARAMS)
        # 遥控/遥调 ↔ 关联遥信/遥测点位映射 {yk_ioa: yx_ioa} / {yt_ioa: yc_ioa}
        # 遥控/遥调本质是写点位：执行结果体现为关联点位的值变化
        self._yk_links = {}
        self._yt_links = {}
        # 当前是否为深色主题（由 _apply_theme 依据样式表内容判定）
        self._dark_theme = False
        # 连接状态机辅助状态：最近失败原因 / 等待反馈的操作 / 停止中的线程
        self._last_error = ""      # 显示于状态标签，完整文案放 tooltip
        self._last_error_detail = ""
        self._pending_ops = {}     # key -> {btn,text,busy,label,timeout,timer}
        self._closing_sims = []    # 停止中的线程引用，防 GC，稍后异步收割
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
        self.role_combo.addItem("主站", SimulatorRole.MASTER)
        self.role_combo.addItem("从站", SimulatorRole.SLAVE)
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

        # 启动/停止（点亮样式与运行状态联动：停止时启动按钮高亮，运行时停止按钮高亮）
        self.btn_start = QPushButton("▶ 启动")
        self.btn_start.setStyleSheet(START_BTN_ACTIVE)
        self.btn_start.clicked.connect(self._on_start)
        conn_layout.addWidget(self.btn_start)

        self.btn_stop = QPushButton("■ 停止")
        self.btn_stop.setStyleSheet(STOP_BTN_INACTIVE)
        self.btn_stop.clicked.connect(self._on_stop)
        conn_layout.addWidget(self.btn_stop)

        # SBO 预制总开关（主站遥控/遥调：开启时需先"预制"才能执行）
        self.chk_sbo = QCheckBox("启用预制")
        self.chk_sbo.setChecked(True)
        self.chk_sbo.setToolTip(
            "开启(SBO 安全流程)：遥控点\"合/分\"先发\"预制(选择)\"，预制确认后再点一次才下发执行；\n"
            "遥调先点\"预制\"再点\"设定\"。\n"
            "关闭：点击合/分/设定直接执行(跳过预制)")
        self.chk_sbo.stateChanged.connect(self._on_sbo_toggled)
        conn_layout.addWidget(self.chk_sbo)

        # 协议参数（t1/t2/t3、k/w、公共地址、IOA 基址、周期总召唤、SOE）
        self.btn_params = QPushButton("协议参数…")
        self.btn_params.setStyleSheet(
            "QPushButton { background-color: #607D8B; color: white; padding: 4px 12px; "
            "border-radius: 3px; border: none; }"
            "QPushButton:hover { background-color: #455A64; }"
        )
        self.btn_params.setToolTip(
            "配置 IEC 104 协议参数：t1/t2/t3 超时、k/w 窗口、ASDU 公共地址、\n"
            "各类型 IOA 基址、主站周期总召唤间隔、遥信/遥测带时标(SOE)")
        self.btn_params.clicked.connect(self._on_edit_params)
        conn_layout.addWidget(self.btn_params)

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

        for label, slot, color, hover in [
            ("总召唤", self._on_gi, "#4CAF50", "#388E3C"),
            ("时钟同步", self._on_clock_sync, "#2196F3", "#1565C0"),
            ("启动传输", self._on_startdt, "#FF9800", "#E65100"),
            ("停止传输", self._on_stopdt, "#9E9E9E", "#757575"),
            ("测试帧", self._on_testfr, "#9C27B0", "#7B1FA2"),
        ]:
            btn = QPushButton(label)
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {color}; color: white; padding: 4px 10px; border-radius: 3px; border: none; font-size: 9pt; }}"
                f"QPushButton:hover {{ background-color: {hover}; }}"
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

        # 主站周期总召唤定时器（周期在协议参数中配置，0=关闭）
        self._gi_timer = QTimer()
        self._gi_timer.timeout.connect(self._on_gi_timer_tick)

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

        # 遥控（关联遥信列用于选择遥控结果体现到哪个遥信点位）
        self.yk_table = self._create_point_table(
            ["序号", "IOA", "IOA(10)", "描述", "当前值", "关联遥信", "操作"],
            [40, 70, 60, 120, 80, 150, 230]
        )
        self.tabs.addTab(self.yk_table, "遥控 (YK)")

        # 遥调（关联遥测列用于选择遥调结果体现到哪个遥测点位）
        self.yt_table = self._create_point_table(
            ["序号", "IOA", "IOA(10)", "描述", "当前值", "关联遥测", "操作"],
            [40, 70, 60, 120, 100, 150, 200]
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

        self.btn_send_manual = QPushButton("发送")
        self.btn_send_manual.setStyleSheet(
            "QPushButton { background-color: #FF9800; color: white; padding: 4px 14px; border-radius: 3px; border: none; }"
            "QPushButton:hover { background-color: #E65100; }"
        )
        self.btn_send_manual.setEnabled(False)
        self.btn_send_manual.clicked.connect(self._on_manual_send)
        manual_layout.addWidget(self.btn_send_manual)

        # 发送结果提示（3 秒后自动清除，替代"发送成功"弹窗）
        self.lbl_send_hint = QLabel("")
        self.lbl_send_hint.setStyleSheet("color: #4CAF50; font-size: 9pt;")
        manual_layout.addWidget(self.lbl_send_hint)

        btn_clear_log = QPushButton("清空日志")
        btn_clear_log.clicked.connect(lambda: self.log_table.setRowCount(0))
        manual_layout.addWidget(btn_clear_log)

        parent_layout.addLayout(manual_layout)


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
        """程序化设置角色（重置/加载配置时使用）

        必须屏蔽信号：setCurrentIndex 会触发 _on_role_changed → _on_apply_points，
        后者在已有点位数据时弹"确认重建"对话框，并把刚加载的配置点位冲掉。
        """
        blocked = self.role_combo.blockSignals(True)
        try:
            self.role_combo.setCurrentIndex(index)
        finally:
            self.role_combo.blockSignals(blocked)

    def _on_role_changed(self, index):
        """角色切换：更新默认 IP，并按数量重建点位

        运行中角色下拉框已被 _refresh_ui_state 禁用，无需切换确认弹窗。
        """
        is_slave = (index == 1)
        self.ip_input.setText("0.0.0.0" if is_slave else "127.0.0.1")
        # 重新创建点位
        self._on_apply_points()
        self._refresh_ui_state()

    def _refresh_ui_state(self):
        """统一状态机：由 (运行中, 已连接, 角色) 派生全部控件可用性与状态标签

        此前启用逻辑分散在各事件回调（_update_start_stop_buttons/_set_ops_enabled/
        行内按钮各自的 toggle），组合状态遗漏时出现"按钮可点但点击被弹窗拒绝"的
        割裂体验。收敛为单一出口：任何状态变化后调用本方法即可。
        """
        running = bool(self.simulator and self.simulator.isRunning())
        connected = bool(self.simulator and self.simulator.is_connected)
        is_master = self.role_combo.currentData() == SimulatorRole.MASTER

        # ---- 启动/停止按钮（当前状态的对方灰暗，可点的那个高亮）----
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.btn_start.setStyleSheet(
            START_BTN_ACTIVE if not running else START_BTN_INACTIVE)
        self.btn_stop.setStyleSheet(
            STOP_BTN_ACTIVE if running else STOP_BTN_INACTIVE)
        self.btn_stop.setToolTip("" if running else "当前未在运行")

        # ---- 连接/点位配置：运行中锁定 ----
        lock_tip = "运行中不可修改，请先停止" if running else ""
        for w in (self.role_combo, self.ip_input, self.port_input,
                  self.yx_count, self.yc_count, self.yk_count, self.yt_count,
                  self.btn_apply_points):
            w.setEnabled(not running)
            w.setToolTip(lock_tip)
        self.btn_reset.setEnabled(not running)
        self.btn_reset.setToolTip(
            lock_tip or "清空所有数据和日志，恢复初始状态")

        # ---- 状态标签 ----
        self._update_status_label(running, connected)

        # ---- 主站操作按钮 ----
        self.master_btns_group.setVisible(is_master)
        # SBO 开关只影响主站行为，从站模式隐藏——避免"在从站实例上取消
        # 勾选、主站实例仍勾选"导致误以为关了预制还在发选择帧
        self.chk_sbo.setVisible(is_master)
        ops_ready = running and connected and is_master
        for name in ("总召唤", "时钟同步", "启动传输", "停止传输", "测试帧"):
            btn = getattr(self, f'_btn_{name}', None)
            if btn:
                btn.setEnabled(ops_ready)

        # ---- 自动上送（仅从站连接后可用）----
        self.auto_group.setVisible(not is_master)
        auto_ready = running and connected and not is_master
        self.chk_auto_send.setEnabled(auto_ready)
        self.auto_interval_spin.setEnabled(auto_ready)
        self.auto_range_spin.setEnabled(auto_ready)
        if not auto_ready:
            self.auto_timer.stop()
            if self.chk_auto_send.isChecked():
                self.chk_auto_send.setChecked(False)

        # ---- 主站周期总召唤 ----
        self._update_gi_timer()

        # ---- 遥控/遥调行内按钮 ----
        self._update_point_op_buttons()

        # ---- 手动发送 ----
        manual_ready = running and connected
        self.btn_send_manual.setEnabled(manual_ready)
        self.hex_combo.lineEdit().setPlaceholderText(
            "输入十六进制报文，如: 68 04 07 00 00 00" if manual_ready
            else "启动连接后可发送")

    def _update_status_label(self, running: bool, connected: bool):
        """状态标签文字与颜色（失败原因放 tooltip）"""
        if running and connected:
            if self.simulator.state == ConnectionState.STARTED:
                self._set_status("● 数据传输中", "#2196F3")
            else:
                self._set_status("● 已连接", "#4CAF50")
        elif running:
            if self._last_error:
                self._set_status("● 连接失败（重试中…）", "#FF9800",
                                 tip=self._last_error)
            else:
                self._set_status("● 连接中…", "#FF9800")
        elif self._last_error:
            self._set_status(f"● {self._last_error}", "#F44336",
                             tip=self._last_error_detail)
        else:
            self._set_status("● 未连接", "#999999")

    def _set_status(self, text: str, color: str, tip: str = ""):
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet(f"color: {color}; font-weight: bold;")
        self.lbl_status.setToolTip(tip)

    def _update_point_op_buttons(self):
        """统一刷新遥控/遥调行内按钮可用状态

        由 (已连接, SBO开关, 该点位是否已预制, 是否等待预制反馈) 共同决定，
        未连接时行内按钮一律禁用——从根源上消灭"请先启动连接"弹窗。
        """
        connected = bool(self.simulator and self.simulator.is_connected)
        sbo_on = self.chk_sbo.isChecked()
        for ops in self._yk_ops.values():
            if ops["selected"]:
                # 预制成功：仅放行预制所对应的那条命令下发
                cmd = ops.get("selected_cmd")
                ops["btn_on"].setEnabled(connected and cmd == "on")
                ops["btn_off"].setEnabled(connected and cmd == "off")
            elif f"sel:45:{ops['ioa']}" in self._pending_ops:
                # 等待预制反馈期间：双按钮禁用，避免并发误点
                ops["btn_on"].setEnabled(False)
                ops["btn_off"].setEnabled(False)
            else:
                # 未预制：SBO 开启时点击=发送对应命令的"预制(选择)"；
                # SBO 关闭时点击=直接执行
                ops["btn_on"].setEnabled(connected)
                ops["btn_off"].setEnabled(connected)
        for ops in self._yt_ops.values():
            ops["btn_set"].setEnabled(
                connected and (not sbo_on or ops["selected"]))
            ops["btn_select"].setEnabled(
                connected and sbo_on
                and f"sel:50:{ops['ioa']}" not in self._pending_ops)
            # 预制成功后锁定设定值：SBO 要求"执行值 == 预制值"，
            # 允许中途改数会让从站回 COT=8（激活终止）
            spin = ops.get("spin")
            if spin is not None:
                spin.setEnabled(
                    connected and not (sbo_on and ops.get("selected")))

    # ==================================================================
    # 协议参数（t1/t2/t3、k/w、公共地址、IOA 基址、周期总召唤、SOE）
    # ==================================================================

    def _apply_params_to_simulator(self):
        """把协议参数应用到运行中的模拟器（支持连接中热更新）"""
        if not self.simulator:
            return
        sim = self.simulator
        sim.t1 = float(self._params["t1"])
        sim.t2 = float(self._params["t2"])
        sim.t3 = float(self._params["t3"])
        sim.k = int(self._params["k"])
        sim.w = int(self._params["w"])
        sim.common_addr = int(self._params["ca"])
        sim.soe_enabled = bool(self._params["soe"])

    def _update_gi_timer(self):
        """按 (主站, 运行中, 周期>0) 启停周期总召唤定时器"""
        period = int(self._params.get("gi_period", 0))
        is_master = self.role_combo.currentData() == SimulatorRole.MASTER
        running = bool(self.simulator and self.simulator.isRunning())
        if running and is_master and period > 0:
            interval_ms = period * 1000
            if not self._gi_timer.isActive() \
                    or self._gi_timer.interval() != interval_ms:
                self._gi_timer.start(interval_ms)
        else:
            self._gi_timer.stop()

    def _on_gi_timer_tick(self):
        """周期总召唤到点：复用总召唤按钮的发送与等待反馈逻辑"""
        if not (self.simulator and self.simulator.is_connected
                and self.role_combo.currentData() == SimulatorRole.MASTER):
            return
        self._on_gi()

    def _on_edit_params(self):
        """打开协议参数对话框并应用"""
        dlg = ProtocolParamsDialog(self._params, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        self._params = dlg.get_params()
        self._apply_params_to_simulator()
        self._update_gi_timer()
        p = self._params
        gi_txt = f"{p['gi_period']}s" if p['gi_period'] else "关闭"
        self._append_log(
            f"[协议参数] t1={p['t1']}s t2={p['t2']}s t3={p['t3']}s "
            f"k={p['k']} w={p['w']} 公共地址={p['ca']} "
            f"周期总召唤={gi_txt} "
            f"SOE={'开' if p['soe'] else '关'} "
            f"IOA基址 YX=0x{p['base_yx']:04X} YC=0x{p['base_yc']:04X} "
            f"YK=0x{p['base_yk']:04X} YT=0x{p['base_yt']:04X}"
            + ("（IOA 基址需重新\"应用配置\"生效）" if self._current_points else ""))

    # ==================================================================
    # 操作按钮反馈：等待 → 确认/超时
    # ==================================================================

    def _op_begin(self, key: str, btn, timeout_ms: int, busy_text: str, label: str):
        """操作按钮进入等待反馈状态：改忙文案、禁点、挂超时定时器"""
        if key in self._pending_ops or btn is None:
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda k=key: self._op_timeout(k))
        self._pending_ops[key] = {
            "btn": btn, "text": btn.text(), "label": label,
            "timeout": timeout_ms, "timer": timer,
        }
        btn.setText(busy_text)
        btn.setEnabled(False)
        timer.start(timeout_ms)

    def _op_end(self, key: str, note: str = ""):
        """操作收到反馈（或被取消）：恢复按钮文案，可选记日志"""
        entry = self._pending_ops.pop(key, None)
        if not entry:
            return
        timer = entry["timer"]
        timer.stop()
        timer.deleteLater()
        # 等待反馈期间表格可能被重建（角色/点数/配置变更），行内按钮的
        # C++ 对象已随 setRowCount 销毁，此时 setText 抛 RuntimeError；
        # PyQt5 对槽内未捕获异常会直接终止进程。
        try:
            entry["btn"].setText(entry["text"])
        except RuntimeError:
            pass
        if note:
            self._append_log(note)
        self._refresh_ui_state()

    def _op_timeout(self, key: str):
        """操作反馈超时：恢复按钮并提示（SBO 预制同时取消预制态）"""
        entry = self._pending_ops.get(key)
        if not entry:
            return
        note = f"[超时] {entry['label']}在 {entry['timeout'] // 1000}s 内未收到反馈"
        if key.startswith("sel:"):
            try:
                _, tid_s, ioa_s = key.split(":")
                self._reset_select(int(ioa_s), int(tid_s))
                note += "，预制已取消"
            except (ValueError, RuntimeError):
                pass  # 表格已重建、行内按钮已销毁时不再处理（见 _op_end）
        self._op_end(key, note)

    def _cancel_pending_ops(self):
        """连接断开/停止时取消所有等待中的操作反馈"""
        for key in list(self._pending_ops):
            self._op_end(key)

    def _clear_select_states(self):
        """清空全部点位的预制态（连接断开后会话结束，预制不再有效）"""
        for ops in list(self._yk_ops.values()) + list(self._yt_ops.values()):
            ops["selected"] = False
            if "selected_cmd" in ops:
                ops["selected_cmd"] = None
                ops["pending_cmd"] = None
                self._apply_yk_button_state(ops)
            else:
                ops["btn_select"].setText("预制")
                ops["btn_select"].setStyleSheet(SEL_BTN_NORMAL)
        self._update_point_op_buttons()

    def _reap_closing_sims(self):
        """收割已退出的停止中线程；仍在收尾的留待下次定时器"""
        self._closing_sims = [s for s in self._closing_sims if s.isRunning()]
        if self._closing_sims:
            QTimer.singleShot(500, self._reap_closing_sims)

    def _on_start(self):
        """启动模拟器"""
        if self.simulator and self.simulator.isRunning():
            return

        role = self.role_combo.currentData()
        host = self.ip_input.text().strip()
        port = self.port_input.value()

        self.simulator = IEC104Simulator(role)
        self.simulator.points = dict(self._current_points)
        self._sync_links_to_simulator()
        self._apply_params_to_simulator()
        self.simulator.frame_sent.connect(self._on_frame_sent)
        self.simulator.frame_received.connect(self._on_frame_received)
        self.simulator.state_changed.connect(self._on_state_changed)
        self.simulator.error_occurred.connect(self._on_error)
        self.simulator.log_message.connect(self._on_log)
        self.simulator.connection_established.connect(self._on_connected)
        self.simulator.connection_lost.connect(self._on_disconnected)
        self.simulator.point_updated.connect(self._on_point_updated)
        self.simulator.control_ack.connect(self._on_control_ack)

        self._last_error = ""
        self._last_error_detail = ""
        if role == SimulatorRole.SLAVE:
            self.simulator.start_server(host, port)
        else:
            self.simulator.connect_to(host, port)

        # 统一状态机：操作按钮在连接成功后才启用，
        # 避免连接前点击总召唤/启动传输等无响应让用户困惑
        self._refresh_ui_state()

    def _on_stop(self):
        """停止模拟器（异步：不阻塞 GUI 等待线程退出）

        stop() 关闭 socket 后工作循环会在下一个 0.5s 收包超时内退出；
        线程引用移入 _closing_sims 防止中途被 GC，稍后定时收割。
        """
        sim = self.simulator
        if not sim:
            return
        self.simulator = None
        self._last_error = ""
        self._last_error_detail = ""
        self._closing_sims.append(sim)
        sim.stop()
        # 断开旧会话信号：停止是异步的，收尾期间仍在队列里的
        # frame_received/point_updated 会把上一条会话的数据写进新会话
        for sig in (sim.frame_sent, sim.frame_received, sim.state_changed,
                    sim.error_occurred, sim.log_message,
                    sim.connection_established, sim.connection_lost,
                    sim.point_updated, sim.control_ack):
            try:
                sig.disconnect(self)
            except (TypeError, RuntimeError):
                pass
        self._cancel_pending_ops()
        self._clear_select_states()
        QTimer.singleShot(500, self._reap_closing_sims)
        self._refresh_ui_state()

    def _on_reset(self):
        """初始化：清空所有数据和日志"""
        if self.simulator and self.simulator.isRunning():
            return  # 运行中按钮已被禁用，双保险静默返回
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
        self.chk_sbo.setChecked(True)
        self._params = dict(DEFAULT_PARAMS)
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
        """关闭时停止模拟器并保存配置"""
        if self.simulator:
            self._on_stop()
        # 应用即将退出：给停止中的线程最多约 2s 收尾，避免 QThread 运行中被销毁。
        # 此处逐个 wait 并自然结束循环——此前只等 _closing_sims[0]，且依赖
        # QTimer.singleShot 的 _reap_closing_sims 收缩列表，而本循环正阻塞
        # 事件循环导致定时器永不触发，实际固定空耗满 2s 且其余线程完全没等。
        deadline = time.time() + 2.0
        still_running = []
        for sim in list(self._closing_sims):
            remaining_ms = max(0, int((deadline - time.time()) * 1000))
            if not sim.wait(remaining_ms):
                still_running.append(sim)
        # 超时未退出的线程**不能丢引用**：QThread 在运行中被析构会让整个
        # 进程 abort。进程马上退出，把引用留到最后即可。
        self._closing_sims = still_running
        self._cancel_pending_ops()
        self.auto_timer.stop()
        self._gi_timer.stop()
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
            },
            # 遥控/遥调的关联点位映射（用户可改，需持久化）
            "yk_links": {str(k): v for k, v in self._yk_links.items()},
            "yt_links": {str(k): v for k, v in self._yt_links.items()},
            # SBO 预制开关状态（此前不持久化，重启后默认勾选易造成误解）
            "sbo": self.chk_sbo.isChecked(),
            # IEC 104 协议参数
            "params": dict(self._params),
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

            # 恢复关联映射（仅保留双向仍有效的项；缺失则按默认规则补齐）
            for attr, key in (("_yk_links", "yk_links"), ("_yt_links", "yt_links")):
                saved = data.get(key) or {}
                links = {}
                for k, v in saved.items():
                    try:
                        k_i, v_i = int(k), int(v)
                    except (TypeError, ValueError):
                        continue
                    if k_i in self._current_points and v_i in self._current_points:
                        links[k_i] = v_i
                setattr(self, attr, links)
            self._rebuild_default_links(force=False)
            self._sync_links_to_simulator()
            # SBO 开关状态（默认勾选）
            self.chk_sbo.setChecked(bool(data.get("sbo", True)))
            # 协议参数（仅接受已知键，其余保持默认）
            saved_params = data.get("params") or {}
            self._params = {**DEFAULT_PARAMS,
                            **{k: v for k, v in saved_params.items()
                               if k in DEFAULT_PARAMS}}

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
        """导出配置为 CSV 表格（可用 Excel 打开编辑）"""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出模拟器配置", "simulator_config.csv",
            "CSV 表格 (*.csv);;所有文件 (*)"
        )
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["序号", "IOA", "类型", "描述", "值"])
                points = sorted(self._current_points.values(), key=lambda p: p.ioa)
                for i, pt in enumerate(points):
                    writer.writerow([
                        i + 1,
                        f"0x{pt.ioa:04X}",
                        pt.signal_type,
                        pt.description,
                        pt.value,
                    ])
            QMessageBox.information(
                self, "导出成功",
                f"配置已导出到:\n{path}\n\n可用 Excel 打开编辑，修改后通过\"导入\"加载。")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))

    def _on_import(self):
        """从 CSV 表格导入配置（Excel 编辑后导入）"""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入模拟器配置", "",
            "CSV 表格 (*.csv);;所有文件 (*)"
        )
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8-sig', newline='') as f:
                rows = list(csv.reader(f))
            if not rows:
                QMessageBox.warning(self, "导入失败", "CSV 文件为空")
                return
            # 跳过表头行
            data_rows = rows[1:] if rows else []
            points = {}
            for row in data_rows:
                if len(row) < 5:
                    continue
                try:
                    ioa = int(row[1].strip(), 16)
                except ValueError:
                    continue
                signal_type = row[2].strip() or "YC"
                desc = row[3].strip()
                val_text = row[4].strip()
                try:
                    if signal_type in ("YC", "YT"):
                        value = float(val_text)
                    else:
                        value = int(float(val_text))
                except ValueError:
                    value = 0
                points[ioa] = PointData(
                    ioa=ioa,
                    signal_type=signal_type,
                    value=value,
                    description=desc,
                )
            if not points:
                QMessageBox.warning(self, "导入失败", "CSV 中没有有效的点位数据")
                return
            self._current_points = points
            self._rebuild_default_links(force=True)
            self._sync_links_to_simulator()
            self._refresh_all_tables()
            if self.simulator:
                self.simulator.points = dict(self._current_points)
            QMessageBox.information(
                self, "导入成功", f"已导入 {len(points)} 个点位")
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
        self._current_points = create_default_points(
            yx, yc, yk, yt,
            ioa_bases={"YX": self._params["base_yx"],
                       "YC": self._params["base_yc"],
                       "YK": self._params["base_yk"],
                       "YT": self._params["base_yt"]})
        # 点位集合变化 → 按默认规则重排关联（第 i 个遥控↔第 i 个遥信…）
        self._rebuild_default_links(force=True)
        self._sync_links_to_simulator()
        self._refresh_all_tables()
        if self.simulator:
            self.simulator.points = dict(self._current_points)


    # ==================================================================
    # 遥控/遥调 ↔ 遥信/遥测 关联
    # ==================================================================

    def _rebuild_default_links(self, force: bool = True):
        """按默认规则排关联：第 i 个遥控 ↔ 第 i 个遥信、第 i 个遥调 ↔ 第 i 个遥测

        force=True 整表重排（点位集合变化后）；False 仅补缺失项并清理
        失效项（加载旧配置文件时保留用户已保存的关联）。
        """
        yx = sorted(p.ioa for p in self._current_points.values()
                    if p.signal_type == "YX")
        yc = sorted(p.ioa for p in self._current_points.values()
                    if p.signal_type == "YC")
        yk = sorted(p.ioa for p in self._current_points.values()
                    if p.signal_type == "YK")
        yt = sorted(p.ioa for p in self._current_points.values()
                    if p.signal_type == "YT")
        if force:
            self._yk_links = {}
            self._yt_links = {}
        for i, k in enumerate(yk):
            if k not in self._yk_links and i < len(yx):
                self._yk_links[k] = yx[i]
        for i, t in enumerate(yt):
            if t not in self._yt_links and i < len(yc):
                self._yt_links[t] = yc[i]
        # 清掉指向已不存在点位的关联
        self._yk_links = {k: v for k, v in self._yk_links.items()
                          if k in self._current_points and v in self._current_points}
        self._yt_links = {k: v for k, v in self._yt_links.items()
                          if k in self._current_points and v in self._current_points}

    def _sync_links_to_simulator(self):
        """把关联映射同步给运行中的模拟器（从站执行控制时写关联点位）"""
        if self.simulator:
            self.simulator.yk_links = dict(self._yk_links)
            self.simulator.yt_links = dict(self._yt_links)


    def _on_yk_link_changed(self, yk_ioa, link_ioa):
        """遥控关联遥信变更：更新映射、同步模拟器并刷新显示值"""
        if link_ioa is None:
            return
        self._yk_links[yk_ioa] = link_ioa
        self._sync_links_to_simulator()
        self._update_yk_value_cell(yk_ioa)

    def _on_yt_link_changed(self, yt_ioa, link_ioa):
        """遥调关联遥测变更：更新映射、同步模拟器并刷新显示值"""
        if link_ioa is None:
            return
        self._yt_links[yt_ioa] = link_ioa
        self._sync_links_to_simulator()
        self._update_yt_value_cell(yt_ioa)


    # ==================================================================
    # 主站操作
    # ==================================================================

    def _on_gi(self):
        if not (self.simulator and self.simulator.is_connected):
            return
        if self.simulator.send_general_interrogation(
                common_addr=self._params["ca"]):
            self._op_begin("gi", self._btn_总召唤, 15000, "总召唤…", "总召唤")

    def _on_clock_sync(self):
        if not (self.simulator and self.simulator.is_connected):
            return
        if self.simulator.send_clock_sync(common_addr=self._params["ca"]):
            self._op_begin("clock", self._btn_时钟同步, 10000, "对时…", "时钟同步")

    def _on_startdt(self):
        if not self.simulator:
            return
        if self.simulator.send_startdt():
            self._op_begin("startdt", self._btn_启动传输, 10000, "启动中…", "启动传输")

    def _on_stopdt(self):
        if not self.simulator:
            return
        if self.simulator.send_stopdt():
            self._op_begin("stopdt", self._btn_停止传输, 10000, "停止中…", "停止传输")

    def _on_testfr(self):
        if not self.simulator:
            return
        if self.simulator.send_testfr():
            self._op_begin("testfr", self._btn_测试帧, 10000, "测试…", "测试帧")

    # ==================================================================
    # 点位操作
    # ==================================================================

    def _on_toggle_yx(self, ioa):
        """切换遥信值 (从站模式)"""
        if ioa not in self._current_points:
            return
        pt = self._current_points[ioa]
        new_val = not bool(pt.value)
        if self.simulator:
            # 由 set_point_value 独占写入 pt.value：simulator.points 与
            # _current_points 共享同一批 PointData 对象，若此处先改 pt.value，
            # set_point_value 里的 old_val 会等于新值 → "值变化"判定恒为
            # False → 勾选自动上送后手动改点永不产生报文。
            self.simulator.set_point_value(ioa, new_val)
        else:
            pt.value = new_val
            pt.is_valid = True
            pt.timestamp = time.strftime("%H:%M:%S")
        self._refresh_yx_table()

    def _on_yc_edit_finished(self, spin):
        """遥测值编辑完成"""
        ioa = spin.property("ioa")
        if ioa is None or ioa not in self._current_points:
            return
        value = spin.value()
        pt = self._current_points[ioa]
        if self.simulator:
            # 同上：写入口唯一，避免 old_val 被提前覆盖
            self.simulator.set_point_value(ioa, value)
        elif pt.value != value:
            pt.value = value
            pt.is_valid = True
            pt.timestamp = time.strftime("%H:%M:%S")

    # ==================================================================
    # SBO 预制（遥控/遥调）
    # ==================================================================

    def _on_sbo_toggled(self):
        """启用预制总开关切换：取消等待中的预制，可用性交状态机统一刷新

        启用预制：合/分/设定需先"预制"确认后才能用；
        关闭预制：合/分/设定直接可用（跳过 SBO）。
        """
        sbo_on = self.chk_sbo.isChecked()
        if not sbo_on:
            for key in [k for k in self._pending_ops if k.startswith("sel:")]:
                self._op_end(key)
            self._clear_select_states()
        # SBO 开启/关闭都需刷新遥控按钮文案（预制合 ↔ 合）
        for ops in self._yk_ops.values():
            self._apply_yk_button_state(ops)
        self._update_point_op_buttons()
        self._append_log(
            f"[SBO] {'启用' if sbo_on else '关闭'}预制流程："
            f"{'遥控/遥调需先预制后执行' if sbo_on else '遥控/遥调直接执行'}")

    def _on_yt_select(self, ioa):
        """遥调预制：发送 SBO 选择命令（设点短浮点），5s 未确认自动取消"""
        if not (self.simulator and self.simulator.is_connected):
            return
        ops = self._yt_ops.get(ioa)
        if not ops:
            return
        spin = ops.get("spin")
        value = spin.value() if spin else 0.0
        if self.simulator.send_i_frame(
                type_id=50, cot=6, common_addr=self._params["ca"],
                ioa=ioa, value=value, select=True):
            # 记住预制值：执行时必须下发同一个值，否则从站按 SBO 校验
            # 会判"预制值不一致"并回 COT=8（此前执行时重新读 spin）
            ops["selected_value"] = float(value)
            self._op_begin(f"sel:50:{ioa}", ops["btn_select"], 5000, "预制…",
                           f"遥调预制 IOA=0x{ioa:04X}")
            self._append_log(
                f"[主站] 发送遥调预制 IOA=0x{ioa:04X} 值={value}，等待确认...")
        else:
            self._append_log(
                f"[主站] 遥调预制发送失败 IOA=0x{ioa:04X}（未进入数据传输状态或发送窗口已满）")

    def _on_control_ack(self, ioa, cot, tid):
        """收到从站控制命令确认（COT=7 确认 / COT=8 终止）"""
        sel_key = f"sel:{tid}:{ioa}"
        if cot == 7:
            was_select = sel_key in self._pending_ops
            self._op_end(sel_key)
            if was_select:
                # 选择确认 → 点亮该点位的执行按钮
                ops = (self._yk_ops if tid == 45 else
                       self._yt_ops if tid == 50 else {}).get(ioa)
                if ops:
                    ops["selected"] = True
                    if tid == 45:
                        # 记录预制的是哪条命令，仅放行其下发。
                        # 注意 _op_end 已把 pending_ops 弹出，必须用发送时
                        # 存在 ops["pending_cmd"] 的命令，不能回查 _pending_ops
                        ops["selected_cmd"] = ops.get("pending_cmd")
                        self._apply_yk_button_state(ops)
                    else:
                        ops["btn_select"].setText("已预制")
                        ops["btn_select"].setStyleSheet(SEL_BTN_ARMED)
                self._update_point_op_buttons()
                self._append_log(f"[主站] 预制确认成功 IOA=0x{ioa:04X}，可执行")
            else:
                self._append_log(f"[主站] 执行确认 IOA=0x{ioa:04X} TID={tid}")
        elif cot == 8:
            self._op_end(sel_key)
            self._reset_select(ioa, tid)
            self._append_log(f"[主站] 预制失败 IOA=0x{ioa:04X} (COT=8 激活终止)")

    def _reset_select(self, ioa, tid):
        """重置某点位的预制状态（执行后/预制失败/预制超时）"""
        ops = self._yk_ops.get(ioa) if tid == 45 else self._yt_ops.get(ioa)
        if not ops:
            return
        ops["selected"] = False
        if tid == 45:
            ops["selected_cmd"] = None
            ops["pending_cmd"] = None
            self._apply_yk_button_state(ops)
        else:
            ops["selected_value"] = None
            ops["btn_select"].setText("预制")
            ops["btn_select"].setStyleSheet(SEL_BTN_NORMAL)
        self._update_point_op_buttons()

    def _apply_yk_button_state(self, ops: dict):
        """按 (SBO 开关, 预制态) 刷新该行遥控按钮的文案与样式

        SBO 开启：预制阶段显示"预制合/预制分"，确认后对应按钮转
        "执行合/执行分"（加粗描边，表示已解锁可下发）；
        SBO 关闭：恒为"合/分"，点击直接执行。
        """
        sbo_on = self.chk_sbo.isChecked()
        armed = ops.get("selected_cmd") if ops.get("selected") else None
        if sbo_on:
            on_text = "执行合" if armed == "on" else "预制合"
            off_text = "执行分" if armed == "off" else "预制分"
        else:
            on_text, off_text = "合", "分"
        ops["btn_on"].setText(on_text)
        ops["btn_on"].setStyleSheet(
            YK_BTN_ON_ARMED if armed == "on" else YK_BTN_ON_NORMAL)
        ops["btn_off"].setText(off_text)
        ops["btn_off"].setStyleSheet(
            YK_BTN_OFF_ARMED if armed == "off" else YK_BTN_OFF_NORMAL)

    def _on_yk_command(self, ioa, value):
        """遥控命令（type 45 单点命令），显式两段式 SBO 流程

        SBO 开启：
          - 未预制 → 本次点击发送"选择(预制)"该命令（预制合/预制分），不执行；
          - 预制确认后对应按钮转"执行合/执行分"，再次点击才下发执行；
          - 未预制的命令按钮处于禁用态，无法直接执行。
        SBO 关闭：点击直接执行（跳过预制）。
        """
        if not (self.simulator and self.simulator.is_connected):
            return
        ops = self._yk_ops.get(ioa)
        if not ops:
            return
        kind = "合" if value else "分"
        cmd = "on" if value else "off"
        btn = ops["btn_on"] if value else ops["btn_off"]
        sbo_on = self.chk_sbo.isChecked()
        if sbo_on and not ops["selected"]:
            # 未预制 → 本次点击发"选择(预制)"该命令，不执行
            if self.simulator.send_i_frame(
                    type_id=45, cot=6, common_addr=self._params["ca"],
                    ioa=ioa, value=value, select=True):
                ops["pending_cmd"] = cmd
                self._op_begin(f"sel:45:{ioa}", btn, 5000, f"预制{kind}…",
                               f"遥控预制(控{kind}) IOA=0x{ioa:04X}")
                # 等待预制反馈期间禁用另一命令，避免并发误点
                other = ops["btn_off"] if value else ops["btn_on"]
                other.setEnabled(False)
                self._append_log(
                    f"[主站] 发送遥控预制(控{kind}) IOA=0x{ioa:04X}，等待确认...")
            return
        if sbo_on and ops.get("selected_cmd") != cmd:
            return  # 已预制另一命令，按钮应已禁用，双保险
        # SBO 关闭直接执行；SBO 开启须已预制同一命令 → 执行(下发)
        if self.simulator.send_i_frame(
                type_id=45, cot=6, common_addr=self._params["ca"],
                ioa=ioa, value=value, select=False):
            self._append_log(f"[主站] 执行遥控(控{kind}) IOA=0x{ioa:04X}")
            self._reset_select(ioa, 45)

    def _on_yt_command(self, ioa, value):
        """遥调执行（type 50 短浮点设点）"""
        if not (self.simulator and self.simulator.is_connected):
            return
        if self.chk_sbo.isChecked():
            ops = self._yt_ops.get(ioa)
            if not ops or not ops["selected"]:
                return
            # 执行必须用"预制时的值"：编辑框在预制后已锁定，这里再兜一层
            value = ops.get("selected_value", value)
        if self.simulator.send_i_frame(
                type_id=50, cot=6, common_addr=self._params["ca"],
                ioa=ioa, value=value, select=False):
            self._append_log(f"[主站] 执行遥调 IOA=0x{ioa:04X} 值={value}")
            self._reset_select(ioa, 50)
        else:
            # 下发失败时预制锁必须释放，否则该行永久停在"已预制"
            self._append_log(
                f"[主站] 执行遥调发送失败 IOA=0x{ioa:04X}，已取消预制")
            self._reset_select(ioa, 50)

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
            # 定点刷新单个单元格：整表重建会销毁编辑框，用户正在输入的
            # 遥测值会被点值覆盖、按钮被判为"已删除对象"
            self._update_yc_row(pt.ioa)
            changed.append(f"YC:{pt.ioa}")
        yx_points = [p for p in self.simulator.points.values() if p.signal_type == "YX"]
        if yx_points and random.random() < 0.3:
            pt = random.choice(yx_points)
            new_val = not bool(pt.value)
            self.simulator.set_point_value(pt.ioa, new_val)
            if pt.ioa in self._current_points:
                self._current_points[pt.ioa].value = new_val
                self._current_points[pt.ioa].is_valid = True
            self._update_yx_row(pt.ioa)
            changed.append(f"YX:{pt.ioa}")
        if changed:
            self.lbl_auto_status.setText(f"已变化: {', '.join(changed)}")

    def _on_manual_send(self):
        if not (self.simulator and self.simulator.is_connected):
            return  # 未连接时发送按钮已禁用，双保险静默返回
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

        if self.simulator.send_frame(frame_hex):
            # 瞬时结果提示（3 秒后清除），替代静默无反馈
            self.lbl_send_hint.setText(f"✓ 已发送 {len(frame_hex) // 2} 字节")
            QTimer.singleShot(3000, lambda: self.lbl_send_hint.setText(""))
            if hex_text not in self._hex_history:
                self._hex_history.add(hex_text)
                self.hex_combo.addItem(hex_text)
            self.hex_combo.setCurrentText("")

    # ==================================================================
    # 连接事件
    # ==================================================================

    def _on_connected(self):
        self._last_error = ""
        self._last_error_detail = ""
        self._refresh_ui_state()

    def _on_disconnected(self):
        # 会话结束：等待中的操作反馈不可能再来，预制态随之失效
        self._cancel_pending_ops()
        self._clear_select_states()
        self._refresh_ui_state()

    def _on_state_changed(self, text):
        self._refresh_ui_state()

    def _on_error(self, msg):
        self._add_log_row("❌", "错误", msg)
        # 仅当前运行中模拟器的错误才升级为状态标签（失败原因显性展示，
        # 避免用户只能翻日志才知道为什么连不上）；停止后迟到的报错只进日志
        if self.sender() is self.simulator:
            self._last_error_detail = msg
            self._last_error = _short_error(msg)
            self._refresh_ui_state()

    def _append_log(self, msg):
        """本地操作日志（非协议收发）"""
        self._add_log_row("", "本地", msg)

    def _on_log(self, msg):
        direction = "收" if "[收]" in msg else ("发" if "[发]" in msg else "")
        self._add_log_row(direction, "协议", msg)

    def _on_frame_sent(self, hex_str):
        self._add_frame_row("发", hex_str)
        if self.simulator:
            self.lbl_seq.setText(f"SN:{self.simulator.send_seq} RN:{self.simulator.recv_seq}")

    def _on_frame_received(self, hex_str):
        frame = self._add_frame_row("收", hex_str)
        if self.simulator:
            self.lbl_seq.setText(f"SN:{self.simulator.send_seq} RN:{self.simulator.recv_seq}")
        self._detect_op_responses(frame, hex_str)

    def _detect_op_responses(self, frame, hex_str):
        """从收到的帧识别操作反馈，结束对应按钮的等待态

        U 帧（STARTDT/STOPDT/TESTFR 的 CON）看控制域首字节；
        总召唤(100)/时钟同步(103) 看 ASDU 类型与传送原因。
        遥控/遥调预制确认走 control_ack 信号，不在此处理。
        """
        if frame is None or frame.error:
            return
        if frame.apci and frame.apci.frame_type == "U-format":
            try:
                ctrl = hex_to_bytes(hex_str)[2]
            except (ValueError, IndexError):
                return
            u_key = {0x0B: "startdt", 0x23: "stopdt", 0x83: "testfr"}.get(ctrl)
            if u_key and u_key in self._pending_ops:
                label = self._pending_ops[u_key]["label"]
                self._op_end(u_key, f"[主站] {label}已确认")
            return
        asdu = frame.asdu
        if not asdu:
            return
        tid, cot = asdu.type_id, asdu.cot_cause
        if tid == 100 and "gi" in self._pending_ops:
            if cot == 7:
                # 确认已收到，开始等数据：保持等待但更新按钮文案
                self._pending_ops["gi"]["btn"].setText("接收数据…")
            elif cot == 10:
                self._op_end("gi", "[主站] 总召唤完成")
        elif tid == 103 and cot == 7 and "clock" in self._pending_ops:
            self._op_end("clock", "[主站] 时钟同步已确认")

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
            # 定点刷新：整表重建在总召唤/高频变化上送时会以每帧一次的频率
            # 销毁重建全部行内按钮（GIL 压力大，且拖慢工作线程收发节奏）
            self._update_yx_row(ioa)
            # 关联此遥信的遥控行同步刷新显示值（遥控结果由关联遥信体现）
            for yk_ioa, yx_ioa in self._yk_links.items():
                if yx_ioa == ioa:
                    self._update_yk_value_cell(yk_ioa)
        elif pt.signal_type == "YC":
            self._update_yc_row(ioa)
            # 关联此遥测的遥调行同步刷新显示值
            for yt_ioa, yc_ioa in self._yt_links.items():
                if yc_ioa == ioa:
                    self._update_yt_value_cell(yt_ioa)
        elif pt.signal_type == "YK":
            # 只更新值单元格：整表重建会销毁行内按钮并重置 SBO 预制态，
            # 导致执行确认刚到手、按钮却被换掉（外部持有的旧引用随即失效）
            self._update_point_value_cell(self.yk_table, ioa,
                                          "合" if pt.value else "分",
                                          YX_ON_COLOR if pt.value else YX_OFF_COLOR)
        elif pt.signal_type == "YT":
            val_text = (f"{pt.value:.2f}" if isinstance(pt.value, (int, float))
                        else str(pt.value))
            self._update_point_value_cell(self.yt_table, ioa, val_text)


    # ==================================================================
    # 辅助
    # ==================================================================

    def _log_row_bg(self, direction):
        """收发日志行背景色（按主题选取，避免深色下浅底刺眼）"""
        if self._dark_theme:
            return BLUE_DARK if direction == "发" else PURPLE_DARK
        return BLUE_LIGHT if direction == "发" else PURPLE_LIGHT

    def _on_theme_applied(self):
        """主题切换后刷新已存在日志行的配色（收发行/错误行）"""
        for r in range(self.log_table.rowCount()):
            direction_item = self.log_table.item(r, 2)
            cat_item = self.log_table.item(r, 3)
            first = self.log_table.item(r, 0)
            if not first:
                continue
            if cat_item and cat_item.text() == "错误":
                bg = RED_DARK if self._dark_theme else RED_LIGHT
                fg = ERROR_FG_DARK if self._dark_theme else ERROR_FG_LIGHT
            elif direction_item and direction_item.text() in ("发", "收"):
                bg = self._log_row_bg(direction_item.text())
                fg = None
            else:
                continue
            for c in range(5):
                it = self.log_table.item(r, c)
                if it:
                    it.setBackground(bg)
                    if fg:
                        it.setForeground(fg)

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
        bg = self._log_row_bg(direction)
        seq = str(row + 1)
        for col, text in enumerate([seq, ts, direction, ftype, desc]):
            item = QTableWidgetItem(text)
            item.setBackground(bg)
            self.log_table.setItem(row, col, item)
        self.log_table.item(row, 4).setData(Qt.UserRole, hex_str)
        self.log_table.scrollToBottom()
        if self.log_table.rowCount() > 2000:
            self.log_table.removeRow(0)
        return frame

    def _add_log_row(self, direction, category, message):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        row = self.log_table.rowCount()
        self.log_table.insertRow(row)
        seq = str(row + 1)
        # 日志分级着色：错误红底醒目、本地操作灰字弱化，与收发行（蓝/紫）区分
        for col, text in enumerate([seq, ts, direction, category, message]):
            item = QTableWidgetItem(text)
            if category == "错误":
                item.setBackground(RED_DARK if self._dark_theme else RED_LIGHT)
                item.setForeground(
                    ERROR_FG_DARK if self._dark_theme else ERROR_FG_LIGHT)
            elif category == "本地":
                item.setForeground(LOCAL_FG)
            self.log_table.setItem(row, col, item)
        self.log_table.scrollToBottom()
        if self.log_table.rowCount() > 2000:
            self.log_table.removeRow(0)


class ProtocolParamsDialog(QDialog):
    """IEC 60870-5-104 协议参数配置对话框

    可配置项对齐专业工具（lib60870 官方示例 / QTester104 / FreyrScada）：
    t1/t2/t3 超时、k/w 滑动窗口、ASDU 公共地址、各类型 IOA 基址、
    主站周期总召唤间隔、遥信/遥测带时标 (SOE)。
    """

    def __init__(self, params: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("协议参数 (IEC 60870-5-104)")
        self.setMinimumWidth(420)

        form = QFormLayout(self)
        form.setSpacing(8)

        def _time_spin(value, tip):
            sp = QDoubleSpinBox()
            sp.setRange(1.0, 600.0)
            sp.setDecimals(1)
            sp.setSuffix(" s")
            sp.setValue(float(value))
            sp.setToolTip(tip)
            return sp

        # ---- 超时参数 ----
        self.sp_t1 = _time_spin(
            params.get("t1", 15.0),
            "t1：发送 I 帧后等待对端确认的超时（标准默认 15s），\n"
            "超时未确认视为连接失效并重连")
        self.sp_t2 = _time_spin(
            params.get("t2", 10.0),
            "t2：收到 I 帧后延迟发送 S 帧确认的时间（标准默认 10s），\n"
            "必须小于 t1")
        self.sp_t3 = _time_spin(
            params.get("t3", 20.0),
            "t3：链路空闲时发送 TESTFR 探活帧的周期（标准默认 20s）")
        form.addRow("t1 确认超时:", self.sp_t1)
        form.addRow("t2 延迟确认:", self.sp_t2)
        form.addRow("t3 空闲探活:", self.sp_t3)

        # ---- 滑动窗口 ----
        self.sp_k = QSpinBox()
        self.sp_k.setRange(1, 32767)
        self.sp_k.setValue(int(params.get("k", 12)))
        self.sp_k.setToolTip("k：发送窗口——未被对端确认的最大 I 帧数（标准默认 12），\n"
                             "发满 k 帧未确认将暂停发送")
        self.sp_w = QSpinBox()
        self.sp_w.setRange(1, 32767)
        self.sp_w.setValue(int(params.get("w", 8)))
        self.sp_w.setToolTip("w：接收窗口——收到 w 个 I 帧后主动发送 S 帧确认（标准默认 8），\n"
                             "要求 w <= k")
        form.addRow("k 发送窗口:", self.sp_k)
        form.addRow("w 接收窗口:", self.sp_w)

        # ---- 公共地址 ----
        self.sp_ca = QSpinBox()
        self.sp_ca.setRange(1, 65534)
        self.sp_ca.setValue(int(params.get("ca", 1)))
        self.sp_ca.setToolTip("ASDU 公共地址（站地址），主站与从站两侧需一致")
        form.addRow("公共地址 (CA):", self.sp_ca)

        # ---- 周期总召唤 ----
        self.sp_gi = QSpinBox()
        self.sp_gi.setRange(0, 3600)
        self.sp_gi.setSuffix(" s")
        self.sp_gi.setSpecialValueText("关闭（仅连接后总召唤一次）")
        self.sp_gi.setValue(int(params.get("gi_period", 0)))
        self.sp_gi.setToolTip("主站周期总召唤间隔；0 表示仅在 STARTDT 后总召唤一次。\n"
                              "调度数据网常用 15~30 分钟")
        form.addRow("周期总召唤:", self.sp_gi)

        # ---- IOA 基址 ----
        def _hex_spin(value, tip):
            sp = QSpinBox()
            sp.setRange(1, 0xFE00)
            sp.setDisplayIntegerBase(16)
            sp.setPrefix("0x")
            sp.setValue(int(value))
            sp.setToolTip(tip)
            return sp

        base_tip = ("该类型第一个点位的 IOA 地址。\n"
                    "建议保持分区：遥信 <0x4001、遥测 <0x6001、遥控 <0x6201、遥调，\n"
                    "否则对端按地址范围自动分类时会归错类")
        self.sp_base_yx = _hex_spin(params.get("base_yx", 0x0001), base_tip)
        self.sp_base_yc = _hex_spin(params.get("base_yc", 0x4001), base_tip)
        self.sp_base_yk = _hex_spin(params.get("base_yk", 0x6001), base_tip)
        self.sp_base_yt = _hex_spin(params.get("base_yt", 0x6201), base_tip)
        form.addRow("遥信 IOA 基址:", self.sp_base_yx)
        form.addRow("遥测 IOA 基址:", self.sp_base_yc)
        form.addRow("遥控 IOA 基址:", self.sp_base_yk)
        form.addRow("遥调 IOA 基址:", self.sp_base_yt)

        # ---- SOE ----
        self.chk_soe = QCheckBox("遥信/遥测带时标 (CP56Time2a / SOE)")
        self.chk_soe.setChecked(bool(params.get("soe", False)))
        self.chk_soe.setToolTip(
            "开启后总召唤与变化上送改用带时标类型（遥信 M_SP_TB_1/遥测 M_ME_TF_1），\n"
            "控制变位上送同样带时标，可用于检验对端 SOE 处理")
        form.addRow(self.chk_soe)

        # ---- 按钮 ----
        btns = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确定")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _on_accept(self):
        """参数合法性校验（IEC 104 标准约束）"""
        if self.sp_t2.value() >= self.sp_t1.value():
            QMessageBox.warning(self, "参数无效",
                                "t2 必须小于 t1（标准要求）。\n"
                                f"当前 t1={self.sp_t1.value()}s t2={self.sp_t2.value()}s")
            return
        if self.sp_w.value() > self.sp_k.value():
            QMessageBox.warning(self, "参数无效",
                                "w 必须小于等于 k（标准要求）。\n"
                                f"当前 k={self.sp_k.value()} w={self.sp_w.value()}")
            return
        self.accept()

    def get_params(self) -> dict:
        """收集对话框当前参数"""
        return {
            "t1": self.sp_t1.value(),
            "t2": self.sp_t2.value(),
            "t3": self.sp_t3.value(),
            "k": self.sp_k.value(),
            "w": self.sp_w.value(),
            "ca": self.sp_ca.value(),
            "gi_period": self.sp_gi.value(),
            "base_yx": self.sp_base_yx.value(),
            "base_yc": self.sp_base_yc.value(),
            "base_yk": self.sp_base_yk.value(),
            "base_yt": self.sp_base_yt.value(),
            "soe": self.chk_soe.isChecked(),
        }
