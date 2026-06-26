"""
报文对比对话框
并排对比两个解析结果
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QMessageBox, QPlainTextEdit, QSplitter, QGroupBox
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QColor

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from iec104_parser import IEC104Parser, TypeID, COT, SignalType, ParseMode


class CompareDialog(QDialog):
    """报文对比对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("报文对比")
        self.setMinimumSize(1000, 650)
        self.resize(1100, 700)
        self.parser_a = IEC104Parser()
        self.parser_b = IEC104Parser()
        self.frames_a = []
        self.frames_b = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 输入区：左右并排
        input_splitter = QSplitter(Qt.Horizontal)

        # 左侧输入
        left_group = QGroupBox("报文 A")
        left_layout = QVBoxLayout(left_group)
        self.input_a = QPlainTextEdit()
        self.input_a.setPlaceholderText("粘贴第一组报文...")
        self.input_a.setFont(QFont("Consolas", 10))
        left_layout.addWidget(self.input_a)
        btn_load_a = QPushButton("导入文件 A")
        btn_load_a.clicked.connect(lambda: self._load_file(self.input_a))
        left_layout.addWidget(btn_load_a)
        input_splitter.addWidget(left_group)

        # 右侧输入
        right_group = QGroupBox("报文 B")
        right_layout = QVBoxLayout(right_group)
        self.input_b = QPlainTextEdit()
        self.input_b.setPlaceholderText("粘贴第二组报文...")
        self.input_b.setFont(QFont("Consolas", 10))
        right_layout.addWidget(self.input_b)
        btn_load_b = QPushButton("导入文件 B")
        btn_load_b.clicked.connect(lambda: self._load_file(self.input_b))
        right_layout.addWidget(btn_load_b)
        input_splitter.addWidget(right_group)

        input_splitter.setSizes([450, 450])
        layout.addWidget(input_splitter)

        # 对比按钮
        btn_layout = QHBoxLayout()
        btn_compare = QPushButton("开始对比")
        btn_compare.clicked.connect(self._do_compare)
        btn_compare.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; font-weight: bold; padding: 6px 16px; }")
        btn_layout.addWidget(btn_compare)
        btn_layout.addStretch()
        self.lbl_stats = QLabel("")
        self.lbl_stats.setStyleSheet("color: #666;")
        btn_layout.addWidget(self.lbl_stats)
        layout.addLayout(btn_layout)

        # 对比结果
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(9)
        self.result_table.setHorizontalHeaderLabels(
            ["状态", "序号A", "信号类型", "IOA", "值A", "序号B", "信号类型", "IOA", "值B"])
        self.result_table.setAlternatingRowColors(True)
        self.result_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.result_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.result_table.setSortingEnabled(True)
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.setColumnWidth(0, 60)
        self.result_table.setColumnWidth(1, 50)
        self.result_table.setColumnWidth(2, 60)
        self.result_table.setColumnWidth(3, 90)
        self.result_table.setColumnWidth(4, 180)
        self.result_table.setColumnWidth(5, 50)
        self.result_table.setColumnWidth(6, 60)
        self.result_table.setColumnWidth(7, 90)
        self.result_table.setColumnWidth(8, 180)
        layout.addWidget(self.result_table)

        # 关闭按钮
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.close)
        layout.addWidget(btn_close)

    def _load_file(self, text_edit):
        path, _ = QFileDialog.getOpenFileName(self, "导入报文", "",
                                                "文本文件 (*.txt *.log *.hex);;所有文件 (*.*)")
        if path:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                text_edit.setPlainText(f.read())

    def _do_compare(self):
        text_a = self.input_a.toPlainText().strip()
        text_b = self.input_b.toPlainText().strip()
        if not text_a or not text_b:
            QMessageBox.warning(self, "提示", "请输入两组报文")
            return

        # Parse both
        self.frames_a = self.parser_a.parse_from_text(text_a)
        self.frames_b = self.parser_b.parse_from_text(text_b)

        # Compare by IOA
        map_a = {}
        for f in self.frames_a:
            if f.asdu:
                for e in f.asdu.information_elements:
                    map_a[e.ioa] = (f, e)

        map_b = {}
        for f in self.frames_b:
            if f.asdu:
                for e in f.asdu.information_elements:
                    map_b[e.ioa] = (f, e)

        # Find differences
        all_ioas = sorted(set(list(map_a.keys()) + list(map_b.keys())))
        diffs = []
        for ioa in all_ioas:
            if ioa in map_a and ioa in map_b:
                fa, ea = map_a[ioa]
                fb, eb = map_b[ioa]
                val_a = str(ea.value) if ea.value is not None else "-"
                val_b = str(eb.value) if eb.value is not None else "-"
                if val_a != val_b:
                    diffs.append(("不同", fa.index, ea, val_a, fb.index, eb, val_b))
                else:
                    diffs.append(("相同", fa.index, ea, val_a, fb.index, eb, val_b))
            elif ioa in map_a:
                fa, ea = map_a[ioa]
                diffs.append(("仅A", fa.index, ea, str(ea.value), "-", None, "-"))
            else:
                fb, eb = map_b[ioa]
                diffs.append(("仅B", "-", None, "-", fb.index, eb, str(eb.value)))

        # Populate table
        self.result_table.setSortingEnabled(False)
        self.result_table.setRowCount(len(diffs))
        for i, (status, idx_a, ea, val_a, idx_b, eb, val_b) in enumerate(diffs):
            # 状态列
            status_item = QTableWidgetItem(status)
            if status == "不同":
                status_item.setForeground(QColor("#F44336"))
                status_item.setFont(QFont(self.font().family(), self.font().pointSize(), QFont.Bold))
            elif status in ("仅A", "仅B"):
                status_item.setForeground(QColor("#FF9800"))
            else:
                status_item.setForeground(QColor("#4CAF50"))
            self.result_table.setItem(i, 0, status_item)

            self.result_table.setItem(i, 1, QTableWidgetItem(str(idx_a)))
            self.result_table.setItem(i, 2, QTableWidgetItem(ea.signal_type.label if ea else "-"))
            self.result_table.setItem(i, 3, QTableWidgetItem(f"0x{ea.ioa:06X}" if ea else "-"))

            val_a_item = QTableWidgetItem(val_a)
            val_b_item = QTableWidgetItem(val_b)
            if status == "不同":
                val_a_item.setForeground(QColor("#F44336"))
                val_b_item.setForeground(QColor("#F44336"))
                val_a_item.setFont(QFont(self.font().family(), self.font().pointSize(), QFont.Bold))
                val_b_item.setFont(QFont(self.font().family(), self.font().pointSize(), QFont.Bold))
            self.result_table.setItem(i, 4, val_a_item)

            self.result_table.setItem(i, 5, QTableWidgetItem(str(idx_b)))
            self.result_table.setItem(i, 6, QTableWidgetItem(eb.signal_type.label if eb else "-"))
            self.result_table.setItem(i, 7, QTableWidgetItem(f"0x{eb.ioa:06X}" if eb else "-"))
            self.result_table.setItem(i, 8, val_b_item)

        self.result_table.setSortingEnabled(True)

        # 统计
        diff_count = sum(1 for d in diffs if d[0] != "相同")
        same_count = sum(1 for d in diffs if d[0] == "相同")
        self.lbl_stats.setText(
            f"共 {len(all_ioas)} 个 IOA | {diff_count} 个差异 | {same_count} 个相同"
        )
