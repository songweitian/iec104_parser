"""
关于页面 - 版本信息和IOA地址说明
"""

from datetime import datetime

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QWidget
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont


BUILD_DATE = datetime.now().strftime("%Y-%m-%d")
AUTHOR = "宋伟田"
EMAIL = "3028905947@qq.com"
VERSION = "1.1.0"


class AboutDialog(QDialog):
    """版本信息对话框"""

    def __init__(self, app_title: str = "IEC104报文解析器",
                 version: str = VERSION, parent=None):
        super().__init__(parent)
        self.app_title = app_title
        self.version = version

        self.setWindowTitle("关于")
        self.setFixedSize(420, 340)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 20, 24, 20)

        title_label = QLabel(self.app_title)
        title_label.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        from PyQt5.QtWidgets import QFrame
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #ddd;")
        layout.addWidget(line)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(8)

        self._add_info(info_layout, "版本", f"v{self.version}")
        self._add_info(info_layout, "编辑日期", BUILD_DATE)
        self._add_info(info_layout, "作者", AUTHOR)
        self._add_info(info_layout, "邮箱", EMAIL)
        self._add_info(info_layout, "协议标准", "IEC 60870-5-104 (2002版)")
        self._add_info(info_layout, "开发语言", "Python + PyQt5")
        self._add_info(info_layout, "协议支持",
                       "I帧/S帧/U帧，监视方向/控制方向\n"
                       "支持CP56Time2a时标、品质描述词\n"
                       "支持文件导入/导出CSV/JSON/TXT")

        layout.addLayout(info_layout)
        layout.addStretch()

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        close_btn.setMinimumWidth(80)
        close_btn.setStyleSheet("QPushButton { padding: 6px 16px; border-radius: 4px; }")
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

    def _add_info(self, layout: QVBoxLayout, label: str, value: str):
        row = QHBoxLayout()
        lbl = QLabel(f"{label}:")
        lbl.setStyleSheet("color: #666; font-weight: bold;")
        lbl.setFixedWidth(70)
        row.addWidget(lbl)
        val = QLabel(value)
        val.setWordWrap(True)
        row.addWidget(val, 1)
        layout.addLayout(row)


class IOAHelpDialog(QDialog):
    """IOA 地址说明对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("IOA 地址说明")
        self.setFixedSize(560, 380)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        title = QLabel("信息对象地址 (IOA) 按区间划分信号类型")
        title.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        ioa_table = QTableWidget()
        ioa_table.setColumnCount(4)
        ioa_table.setHorizontalHeaderLabels(["IOA 范围", "信号类型", "说明", "示例"])
        ioa_table.horizontalHeader().setStretchLastSection(True)
        ioa_table.setEditTriggers(QTableWidget.NoEditTriggers)
        ioa_table.setSelectionBehavior(QTableWidget.SelectRows)
        ioa_table.setAlternatingRowColors(True)

        ioa_data = [
            ("0x0001 ~ 0x4000", "遥信 (YX)", "单点/双点信息状态", "开关位置、保护动作信号"),
            ("0x4001 ~ 0x5000", "遥测 (YC)", "模拟量测量值", "电压、电流、功率、温度"),
            ("0x6001 ~ 0x6100", "遥控 (YK)", "单点/双点控制命令", "开关分合闸命令"),
            ("0x6201 ~ 0x6400", "遥调 (YT)", "设定值调节命令", "有功/无功功率给定"),
            ("0x6401 ~ 0x6600", "遥脉 (YM)", "电度/累计量", "正向有功电度、无功电度"),
        ]

        ioa_table.setRowCount(len(ioa_data))
        for i, (range_str, signal_type, desc, example) in enumerate(ioa_data):
            ioa_table.setItem(i, 0, QTableWidgetItem(range_str))
            ioa_table.setItem(i, 1, QTableWidgetItem(signal_type))
            ioa_table.setItem(i, 2, QTableWidgetItem(desc))
            ioa_table.setItem(i, 3, QTableWidgetItem(example))

        ioa_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        ioa_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        ioa_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

        layout.addWidget(ioa_table)

        note_label = QLabel("注：IOA 地址在项目中可自定义分配，以上为典型分段约定。")
        note_label.setStyleSheet("color: #666; font-size: 9pt;")
        layout.addWidget(note_label)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        close_btn.setMinimumWidth(80)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
