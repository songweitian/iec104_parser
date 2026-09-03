"""
网络抓包对话框 (预留功能)
说明: 完整的实时网络抓包需要 npcap 驱动和管理员权限
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QGroupBox, QFormLayout,
    QCheckBox, QMessageBox
)


class CaptureDialog(QDialog):
    """实时抓包配置对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("实时捕获 IEC104 报文")
        self.setMinimumSize(480, 300)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 提示
        note = QLabel(
            "⚠️ 实时网络抓包需要:\n"
            "  1. 管理员/root 权限运行本程序\n"
            "  2. 安装 Npcap (Windows) 或 libpcap (Linux)\n"
            "  3. 本功能为预留，如需实时捕获请安装 Scapy\n"
            "     (pip install scapy)"
        )
        note.setStyleSheet("color: #E65100; background: #FFF3E0; "
                           "padding: 8px; border-radius: 4px;")
        note.setWordWrap(True)
        layout.addWidget(note)

        # 网卡选择
        iface_group = QGroupBox("网卡配置")
        iface_layout = QFormLayout(iface_group)

        self.iface_combo = QComboBox()
        self.iface_combo.addItem("eth0 (以太网)", "eth0")
        self.iface_combo.addItem("Wi-Fi (无线)", "wlan0")
        self.iface_combo.addItem("本地连接", "eth1")
        iface_layout.addRow("网卡:", self.iface_combo)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(0, 65535)
        self.port_spin.setValue(2404)
        self.port_spin.setPrefix("端口: ")
        iface_layout.addRow("端口:", self.port_spin)

        self.promisc_check = QCheckBox("混杂模式 (Promiscuous)")
        self.promisc_check.setChecked(True)
        iface_layout.addRow("", self.promisc_check)

        layout.addWidget(iface_group)

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.start_btn = QPushButton("▶ 开始捕获")
        self.start_btn.clicked.connect(self._on_start)
        self.start_btn.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; "
            "font-weight: bold; padding: 6px 20px; border-radius: 4px; border: none; }")

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

    def _on_start(self):
        iface = self.iface_combo.currentData()
        port = self.port_spin.value()
        QMessageBox.information(
            self, "功能预留",
            f"实时抓包功能正在开发中。\n"
            f"目标网卡: {iface}\n"
            f"目标端口: {port}\n\n"
            f"请使用文件导入方式加载报文数据。")
