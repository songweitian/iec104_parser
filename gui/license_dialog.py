"""
激活对话框
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QGroupBox, QFormLayout, QMessageBox, QApplication,
    QTableWidget, QTableWidgetItem, QHeaderView
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QColor

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from license import get_hardware_fingerprint, get_license_info, activate, remove_license


class LicenseDialog(QDialog):
    """激活对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("软件激活")
        self.setFixedSize(450, 580)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._setup_ui()
        self._check_license()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 16, 20, 16)

        # 标题
        title = QLabel("IEC104 报文解析器 - 激活")
        title.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        # 状态
        self.lbl_status = QLabel("")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet("font-size: 11pt; padding: 8px;")
        layout.addWidget(self.lbl_status)

        # 硬件指纹
        fp_group = QGroupBox("硬件指纹")
        fp_layout = QFormLayout()
        self.lbl_fingerprint = QLabel(get_hardware_fingerprint())
        self.lbl_fingerprint.setFont(QFont("Consolas", 10))
        self.lbl_fingerprint.setTextInteractionFlags(Qt.TextSelectableByMouse)
        fp_layout.addRow("指纹:", self.lbl_fingerprint)
        fp_group.setLayout(fp_layout)
        layout.addWidget(fp_group)

        # 激活码输入
        input_group = QGroupBox("输入激活码")
        input_layout = QHBoxLayout()
        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("XXXX-XXXX-XXXX-XXXX 或 XXXX-XXXX-XXXX-XXXX-YYYY-MM-DD")
        self.code_input.setFont(QFont("Consolas", 11))
        self.code_input.setMaxLength(60)
        self.code_input.returnPressed.connect(self._on_activate)  # 回车直接激活
        input_layout.addWidget(self.code_input)

        self.btn_activate = QPushButton("激活")
        self.btn_activate.setMinimumWidth(70)
        self.btn_activate.clicked.connect(self._on_activate)
        input_layout.addWidget(self.btn_activate)
        input_group.setLayout(input_layout)
        layout.addWidget(input_group)

        # 权限对比表格（已激活后隐藏）
        self.perm_label = QLabel("功能权限对比：")
        self.perm_label.setStyleSheet("font-weight: bold; margin-top: 4px;")
        layout.addWidget(self.perm_label)

        perm_data = [
            ("单条报文解析（每次1条）", "✅ 可用", "✅ 多条批量解析"),
            ("文件导入（菜单+拖放）", "❌ 不可用", "✅ 可用"),
            ("导出 CSV/JSON/TXT", "❌ 不可用", "✅ 可用"),
            ("帧生成器 / 报文对比", "❌ 不可用", "✅ 可用"),
            ("通信模拟器", "❌ 不可用", "✅ 可用"),
            ("历史会话管理", "❌ 不可用", "✅ 可用"),
            ("多标签页输入", "❌ 不可用", "✅ 可用"),
        ]
        self.perm_table = QTableWidget(len(perm_data), 3)
        self.perm_table.setHorizontalHeaderLabels(["功能", "未激活", "已激活"])
        self.perm_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.perm_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.perm_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.perm_table.setAlternatingRowColors(True)

        for i, (func, before, after) in enumerate(perm_data):
            self.perm_table.setItem(i, 0, QTableWidgetItem(func))
            item_before = QTableWidgetItem(before)
            item_after = QTableWidgetItem(after)
            if "❌" in before:
                item_before.setForeground(QColor("#F44336"))
            if "✅" in after:
                item_after.setForeground(QColor("#4CAF50"))
            self.perm_table.setItem(i, 1, item_before)
            self.perm_table.setItem(i, 2, item_after)

        self.perm_table.setFixedHeight(180)
        layout.addWidget(self.perm_table)

        # 提示
        self.hint = QLabel("请将硬件指纹发送给开发者获取激活码\n联系邮箱: 3028905947@qq.com")
        self.hint.setStyleSheet("color: #666; font-size: 9pt;")
        self.hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.hint)

        layout.addStretch()

        # 关闭按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

    def _check_license(self):
        """检查当前激活状态（含限时授权与过期状态）"""
        info = get_license_info()
        if info.get("expired"):
            self.lbl_status.setText("⚠ 授权已到期 - 功能已锁定，请重新输入激活码")
            self.lbl_status.setStyleSheet("color: #F44336; font-size: 11pt; padding: 8px;")
            self._set_perm_section_visible(True)
            self.code_input.setEnabled(True)
            self.btn_activate.setEnabled(True)
        elif info["activated"]:
            if info.get("permanent"):
                text = "✅ 已激活（永久授权）"
            else:
                days = info.get("days_remaining")
                text = f"✅ 已激活（有效期至 {info.get('expiry_date', '')}" \
                       + (f"，剩余 {days} 天" if days is not None else "") + "）"
            self.lbl_status.setText(text)
            self.lbl_status.setStyleSheet("color: #4CAF50; font-size: 11pt; padding: 8px;")
            self.code_input.setEnabled(False)
            self.btn_activate.setEnabled(False)
            # 已激活无需再看权限对比，隐藏以精简界面
            self._set_perm_section_visible(False)
        else:
            self.lbl_status.setText("⚠ 未激活 - 每次仅解析1条，导入/导出等受限")
            self.lbl_status.setStyleSheet("color: #FF9800; font-size: 11pt; padding: 8px;")
            self._set_perm_section_visible(True)

    def _set_perm_section_visible(self, visible: bool):
        """显示/隐藏权限对比区域；隐藏时同步收缩对话框高度"""
        for w in (self.perm_label, self.perm_table, self.hint):
            w.setVisible(visible)
        self.setFixedSize(450, 580 if visible else 330)

    def _on_activate(self):
        """执行激活"""
        code = self.code_input.text().strip()
        if not code:
            QMessageBox.warning(self, "提示", "请输入激活码")
            return

        success, msg = activate(code)
        if success:
            QMessageBox.information(self, "成功", msg)
            self._check_license()
        else:
            QMessageBox.warning(self, "失败", msg)
