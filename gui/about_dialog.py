"""
关于页面 - 显示版本信息和作者
"""

from datetime import datetime

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QFrame
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont


# 编译日期 - 每次构建时更新
BUILD_DATE = datetime.now().strftime("%Y-%m-%d")
AUTHOR = "宋伟田"
VERSION = "1.1.0"


class AboutDialog(QDialog):
    """关于对话框"""

    def __init__(self, app_title: str = "IEC104报文解析器",
                 version: str = VERSION, parent=None):
        super().__init__(parent)
        self.app_title = app_title
        self.version = version

        self.setWindowTitle("关于")
        self.setFixedSize(420, 320)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 20, 24, 20)

        # 标题
        title_label = QLabel(self.app_title)
        title_font = QFont("Microsoft YaHei", 16, QFont.Bold)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #ddd;")
        layout.addWidget(line)

        # 信息
        info_layout = QVBoxLayout()
        info_layout.setSpacing(8)

        self._add_info(info_layout, "版本", f"v{self.version}")
        self._add_info(info_layout, "编辑日期", BUILD_DATE)
        self._add_info(info_layout, "作者", AUTHOR)
        self._add_info(info_layout, "协议标准", "IEC 60870-5-104 (2002版)")
        self._add_info(info_layout, "开发语言", "Python + PyQt5")
        self._add_info(info_layout, "协议支持",
                       "I帧/S帧/U帧，监视方向/控制方向\n"
                       "支持CP56Time2a时标、品质描述词\n"
                       "支持文件导入/导出CSV/JSON/TXT")

        layout.addLayout(info_layout)
        layout.addStretch()

        # 关闭按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        close_btn.setMinimumWidth(80)
        close_btn.setStyleSheet(
            "QPushButton { padding: 6px 16px; border-radius: 4px; }")
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

    def _add_info(self, layout: QVBoxLayout, label: str, value: str):
        """添加信息行"""
        row = QHBoxLayout()
        lbl = QLabel(f"{label}:")
        lbl.setStyleSheet("color: #666; font-weight: bold;")
        lbl.setFixedWidth(70)
        row.addWidget(lbl)
        val = QLabel(value)
        val.setWordWrap(True)
        row.addWidget(val, 1)
        layout.addLayout(row)