"""
报文对比对话框
并排对比两个解析结果
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QSplitter, QMessageBox, QPlainTextEdit
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
        self.setMinimumSize(900, 600)
        self.parser_a = IEC104Parser()
        self.parser_b = IEC104Parser()
        self.frames_a = []
        self.frames_b = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 输入区域
        input_splitter = QSplitter(Qt.Horizontal)

        # 左侧输入
        left_group = QLabel("报文 A:")
        left_group.setStyleSheet("font-weight: bold;")
        layout.addWidget(left_group)

        self.input_a = QPlainTextEdit()
        self.input_a.setPlaceholderText("粘贴第一组报文...")
        self.input_a.setFont(QFont("Consolas", 10))
        self.input_a.setMaximumHeight(100)
        layout.addWidget(self.input_a)

        btn_load_a = QPushButton("导入文件 A")
        btn_load_a.clicked.connect(lambda: self._load_file(self.input_a))
        layout.addWidget(btn_load_a)

        right_group = QLabel("报文 B:")
        right_group.setStyleSheet("font-weight: bold;")
        layout.addWidget(right_group)

        self.input_b = QPlainTextEdit()
        self.input_b.setPlaceholderText("粘贴第二组报文...")
        self.input_b.setFont(QFont("Consolas", 10))
        self.input_b.setMaximumHeight(100)
        layout.addWidget(self.input_b)

        btn_load_b = QPushButton("导入文件 B")
        btn_load_b.clicked.connect(lambda: self._load_file(self.input_b))
        layout.addWidget(btn_load_b)

        # 对比按钮
        btn_compare = QPushButton("开始对比")
        btn_compare.clicked.connect(self._do_compare)
        btn_compare.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; font-weight: bold; padding: 6px 16px; }")
        layout.addWidget(btn_compare)

        # 对比结果
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(8)
        self.result_table.setHorizontalHeaderLabels(
            ["序号A", "类型", "IOA", "值A", "序号B", "类型", "IOA", "值B"])
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.setAlternatingRowColors(True)
        layout.addWidget(self.result_table)

        # 统计
        self.lbl_stats = QLabel("")
        layout.addWidget(self.lbl_stats)

    def _load_file(self, text_edit):
        path, _ = QFileDialog.getOpenFileName(self, "导入报文", "",
                                                "文本文件 (*.txt *.log *.hex)")
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
                    diffs.append((fa.index, ea, val_a, fb.index, eb, val_b, "不同"))
            elif ioa in map_a:
                fa, ea = map_a[ioa]
                diffs.append((fa.index, ea, str(ea.value), "-", None, "-", "仅A"))
            else:
                fb, eb = map_b[ioa]
                diffs.append(("-", None, "-", fb.index, eb, str(eb.value), "仅B"))

        # Populate table
        self.result_table.setRowCount(len(diffs))
        for i, (idx_a, ea, val_a, idx_b, eb, val_b, status) in enumerate(diffs):
            self.result_table.setItem(i, 0, QTableWidgetItem(str(idx_a)))
            self.result_table.setItem(i, 1, QTableWidgetItem(ea.signal_type.label if ea else "-"))
            self.result_table.setItem(i, 2, QTableWidgetItem(f"0x{ea.ioa:06X}" if ea else "-"))
            item_val_a = QTableWidgetItem(val_a)
            self.result_table.setItem(i, 3, item_val_a)
            self.result_table.setItem(i, 4, QTableWidgetItem(str(idx_b)))
            self.result_table.setItem(i, 5, QTableWidgetItem(eb.signal_type.label if eb else "-"))
            self.result_table.setItem(i, 6, QTableWidgetItem(f"0x{eb.ioa:06X}" if eb else "-"))
            self.result_table.setItem(i, 7, QTableWidgetItem(val_b))
            # Highlight differences
            if status == "不同":
                item_val_a.setForeground(QColor("#F44336"))

        self.lbl_stats.setText(f"对比完成: {len(all_ioas)} 个IOA, {len(diffs)} 个差异")
