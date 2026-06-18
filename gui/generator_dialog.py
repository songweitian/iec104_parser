"""
IEC104 帧生成器对话框
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QTextEdit, QGroupBox, QFormLayout,
    QMessageBox, QSpinBox, QApplication
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from frame_generator import generate_i_frame, generate_s_frame, generate_u_frame, TYPE_INFO


class GeneratorDialog(QDialog):
    """帧生成器对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("IEC104 帧生成器")
        self.setMinimumSize(500, 450)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 帧类型选择
        type_group = QGroupBox("帧类型")
        type_layout = QHBoxLayout()
        self.frame_type = QComboBox()
        self.frame_type.addItems(["I 格式帧 (信息传输)", "S 格式帧 (确认)", "U 格式帧 (控制)"])
        self.frame_type.currentIndexChanged.connect(self._on_type_changed)
        type_layout.addWidget(self.frame_type)
        type_group.setLayout(type_layout)
        layout.addWidget(type_group)

        # I 帧参数
        self.i_frame_group = QGroupBox("I 帧参数")
        i_layout = QFormLayout()

        self.type_id_combo = QComboBox()
        for tid, (name, desc) in TYPE_INFO.items():
            self.type_id_combo.addItem(f"{tid}: {name} - {desc}", tid)
        i_layout.addRow("类型标识:", self.type_id_combo)

        self.cot_input = QSpinBox()
        self.cot_input.setRange(1, 65535)
        self.cot_input.setValue(6)
        i_layout.addRow("传送原因 (COT):", self.cot_input)

        self.common_addr_input = QSpinBox()
        self.common_addr_input.setRange(1, 65535)
        self.common_addr_input.setValue(1)
        i_layout.addRow("公共地址:", self.common_addr_input)

        self.ioa_input = QLineEdit("0x4001")
        i_layout.addRow("IOA 地址:", self.ioa_input)

        self.value_input = QLineEdit("0")
        i_layout.addRow("值:", self.value_input)

        self.i_frame_group.setLayout(i_layout)
        layout.addWidget(self.i_frame_group)

        # S 帧参数
        self.s_frame_group = QGroupBox("S 帧参数")
        s_layout = QFormLayout()
        self.recv_seq_input = QSpinBox()
        self.recv_seq_input.setRange(0, 32767)
        self.recv_seq_input.setValue(0)
        s_layout.addRow("接收序列号:", self.recv_seq_input)
        self.s_frame_group.setLayout(s_layout)
        self.s_frame_group.setVisible(False)
        layout.addWidget(self.s_frame_group)

        # U 帧参数
        self.u_frame_group = QGroupBox("U 帧类型")
        u_layout = QHBoxLayout()
        self.u_type_combo = QComboBox()
        self.u_type_combo.addItems([
            "STARTDT_ACT",
            "STARTDT_CON",
            "STOPDT_ACT",
            "STOPDT_CON",
            "TESTFR_ACT",
            "TESTFR_CON",
        ])
        u_layout.addWidget(self.u_type_combo)
        self.u_frame_group.setLayout(u_layout)
        self.u_frame_group.setVisible(False)
        layout.addWidget(self.u_frame_group)

        # 生成按钮
        btn_layout = QHBoxLayout()
        self.generate_btn = QPushButton("生成报文")
        self.generate_btn.clicked.connect(self._on_generate)
        btn_layout.addWidget(self.generate_btn)

        self.copy_btn = QPushButton("复制到剪贴板")
        self.copy_btn.clicked.connect(self._on_copy)
        btn_layout.addWidget(self.copy_btn)
        layout.addLayout(btn_layout)

        # 输出
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont("Consolas", 10))
        self.output.setPlaceholderText("生成的报文将显示在这里...")
        layout.addWidget(self.output)

    def _on_type_changed(self, index):
        self.i_frame_group.setVisible(index == 0)
        self.s_frame_group.setVisible(index == 1)
        self.u_frame_group.setVisible(index == 2)

    def _on_generate(self):
        frame_type = self.frame_type.currentIndex()

        try:
            if frame_type == 0:  # I 帧
                type_id = self.type_id_combo.currentData()
                cot = self.cot_input.value()
                common_addr = self.common_addr_input.value()
                ioa_text = self.ioa_input.text().strip()
                if ioa_text.startswith('0x') or ioa_text.startswith('0X'):
                    ioa = int(ioa_text, 16)
                else:
                    ioa = int(ioa_text)
                value = self.value_input.text().strip()
                try:
                    value = float(value) if '.' in value else int(value)
                except ValueError:
                    value = 0

                frame = generate_i_frame(type_id, cot, common_addr, ioa, value)

            elif frame_type == 1:  # S 帧
                recv_seq = self.recv_seq_input.value()
                frame = generate_s_frame(recv_seq)

            else:  # U 帧
                u_type = self.u_type_combo.currentText()
                frame = generate_u_frame(u_type)

            self.output.setPlainText(frame)

        except Exception as e:
            QMessageBox.warning(self, "生成失败", str(e))

    def _on_copy(self):
        text = self.output.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            if hasattr(self, 'statusBar') and self.statusBar():
                self.statusBar().showMessage("已复制到剪贴板")
