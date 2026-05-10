"""
主窗口 - IEC104 报文解析器
"""

import os
import sys
import json
import csv
import struct
import logging
from datetime import datetime
from typing import List, Optional

from PyQt5.QtWidgets import (
    QMainWindow, QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTreeWidget, QTreeWidgetItem, QTextEdit, QPushButton,
    QToolBar, QAction, QFileDialog, QMessageBox, QStatusBar,
    QLabel, QLineEdit, QGroupBox, QGridLayout, QComboBox,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QCheckBox, QSpinBox, QProgressBar, QMenu, QMenuBar,
    QDialog, QListWidget, QListWidgetItem, QFrame, QPlainTextEdit,
    QDialogButtonBox, QFormLayout
)
from PyQt5.QtCore import Qt, pyqtSignal, QThread, QTimer
from PyQt5.QtGui import QFont, QColor, QTextCursor, QIcon, QPalette

# 确保能找到父模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from iec104_parser import (
    IEC104Parser, IEC104Frame, SignalType, TypeID, COT,
    APCIHeader, ASDU, InfoElement
)
from gui.about_dialog import AboutDialog
from gui.hex_view import HexHighlighter

logger = logging.getLogger(__name__)


class CaptureWorker(QThread):
    """网络抓包后台线程 (预留)"""
    packet_received = pyqtSignal(bytes)

    def __init__(self, interface: str, port: int):
        super().__init__()
        self.interface = interface
        self.port = port
        self.running = False

    def run(self):
        self.running = True
        pass

    def stop(self):
        self.running = False


class MainWindow(QMainWindow):
    """主窗口"""

    APP_TITLE = "IEC60870-5-104 报文解析器"
    APP_VERSION = "1.1.0"

    def __init__(self):
        super().__init__()
        self.parser = IEC104Parser()
        self.all_frames: List[IEC104Frame] = []
        self.capture_worker: Optional[CaptureWorker] = None

        self.setWindowTitle(self.APP_TITLE)
        self.resize(1320, 840)
        self.setMinimumSize(900, 640)

        self._setup_ui()
        self._setup_menu()
        self._setup_toolbar()
        self._setup_statusbar()

        self._update_status_stats()

        self.hex_input.setPlainText(
            "# 在此粘贴 IEC104 十六进制报文\n"
            "# 支持格式: 68 04 01 00 0E 00  或  680401000E00\n"
            "# 每行一条报文，以 # 开头的行为注释\n\n"
        )

    def _setup_ui(self):
        """构建界面布局"""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(4)

        splitter = QSplitter(Qt.Horizontal)

        # === 左侧: 输入面板 ===
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # 十六进制输入
        hex_group = QGroupBox("原始报文输入 (十六进制)")
        hex_layout = QVBoxLayout(hex_group)
        self.hex_input = QPlainTextEdit()
        self.hex_input.setPlaceholderText("粘贴十六进制报文...")
        self.hex_input.setFont(QFont("Consolas", 11))
        self.hex_input.setLineWrapMode(QPlainTextEdit.NoWrap)
        hex_layout.addWidget(self.hex_input)

        # 输入操作按钮
        btn_layout = QHBoxLayout()
        self.btn_parse = QPushButton("▶ 解析报文")
        self.btn_parse.clicked.connect(self._on_parse)
        self.btn_parse.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; "
            "font-weight: bold; padding: 6px 16px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #1976D2; }"
        )
        self.btn_clear = QPushButton("清空")
        self.btn_clear.clicked.connect(self._on_clear)
        self.btn_import_file = QPushButton("导入文件")
        self.btn_import_file.clicked.connect(self._on_import_file)

        btn_layout.addWidget(self.btn_parse)
        btn_layout.addWidget(self.btn_clear)
        btn_layout.addWidget(self.btn_import_file)
        btn_layout.addStretch()
        self.lbl_line_count = QLabel("0 行")
        btn_layout.addWidget(self.lbl_line_count)
        hex_layout.addLayout(btn_layout)
        left_layout.addWidget(hex_group)

        # 过滤选项
        filter_group = QGroupBox("过滤条件")
        filter_layout = QHBoxLayout(filter_group)

        filter_layout.addWidget(QLabel("信号类型:"))
        self.filter_signal = QComboBox()
        self.filter_signal.addItem("全部", "")
        self.filter_signal.addItem("遥信", "遥信")
        self.filter_signal.addItem("遥测", "遥测")
        self.filter_signal.addItem("遥控", "遥控")
        self.filter_signal.addItem("遥调", "遥调")
        self.filter_signal.addItem("遥脉", "遥脉")
        filter_layout.addWidget(self.filter_signal)

        filter_layout.addWidget(QLabel("类型:"))
        self.filter_type = QComboBox()
        self.filter_type.addItem("全部", -1)
        for tid in sorted(TypeID):
            self.filter_type.addItem(f"{tid.value}: {tid.name}", tid.value)
        filter_layout.addWidget(self.filter_type)

        filter_layout.addWidget(QLabel("传送原因:"))
        self.filter_cot = QComboBox()
        self.filter_cot.addItem("全部", -1)
        for cot in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 20, 37]:
            self.filter_cot.addItem(f"{cot}: {COT.get_description(cot)}", cot)
        filter_layout.addWidget(self.filter_cot)

        filter_layout.addWidget(QLabel("地址:"))
        self.filter_addr = QLineEdit()
        self.filter_addr.setPlaceholderText("公共地址")
        self.filter_addr.setMaximumWidth(80)
        filter_layout.addWidget(self.filter_addr)

        self.filter_btn = QPushButton("筛选")
        self.filter_btn.clicked.connect(self._on_filter)
        filter_layout.addWidget(self.filter_btn)
        self.filter_reset = QPushButton("重置")
        self.filter_reset.clicked.connect(self._on_filter_reset)
        filter_layout.addWidget(self.filter_reset)
        filter_layout.addStretch()
        left_layout.addWidget(filter_group)

        splitter.addWidget(left_panel)

        # === 右侧: 解析结果 ===
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.result_tabs = QTabWidget()

        # 标签页1: 树形结构
        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderLabels(["字段", "值", "说明"])
        self.tree_widget.setAlternatingRowColors(True)
        self.tree_widget.setColumnWidth(0, 280)
        self.tree_widget.setColumnWidth(1, 350)
        self.tree_widget.setColumnWidth(2, 400)
        self.tree_widget.itemSelectionChanged.connect(self._on_tree_select)
        self.result_tabs.addTab(self.tree_widget, "解析详情")

        # 标签页2: 报文列表
        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(9)
        self.table_widget.setHorizontalHeaderLabels(
            ["序号", "方向", "信号类型", "类型标识", "传送原因", "公共地址", "对象数", "值", "描述"]
        )
        self.table_widget.setAlternatingRowColors(True)
        self.table_widget.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_widget.horizontalHeader().setStretchLastSection(True)
        self.table_widget.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_widget.cellClicked.connect(self._on_table_click)
        self.result_tabs.addTab(self.table_widget, "报文列表")

        # 标签页3: 十六进制对照
        self.hex_view = QPlainTextEdit()
        self.hex_view.setReadOnly(True)
        self.hex_view.setFont(QFont("Consolas", 10))
        self.hex_view.setLineWrapMode(QPlainTextEdit.NoWrap)
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

        splitter.addWidget(right_panel)
        splitter.setSizes([500, 750])
        main_layout.addWidget(splitter)

    def _setup_menu(self):
        """设置菜单栏"""
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件(&F)")
        file_menu.addAction("导入文件(&I)...", self._on_import_file, "Ctrl+I")
        file_menu.addAction("导出CSV(&C)...", self._on_export_csv, "Ctrl+Shift+C")
        file_menu.addAction("导出JSON(&J)...", self._on_export_json, "Ctrl+Shift+J")
        file_menu.addAction("导出TXT(&T)...", self._on_export_txt, "Ctrl+Shift+T")
        file_menu.addSeparator()
        file_menu.addAction("退出(&X)", self.close, "Alt+F4")

        view_menu = menubar.addMenu("查看(&V)")
        view_menu.addAction("解析报文(&P)", self._on_parse, "F5")
        view_menu.addAction("清空(&L)", self._on_clear, "Ctrl+L")

        help_menu = menubar.addMenu("帮助(&H)")
        help_menu.addAction("关于(&A)...", self._on_about)

    def _setup_toolbar(self):
        """设置工具栏"""
        toolbar = QToolBar("主工具栏")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addAction("解析", self._on_parse)
        toolbar.addAction("导入", self._on_import_file)
        toolbar.addSeparator()
        toolbar.addAction("CSV", self._on_export_csv)
        toolbar.addAction("JSON", self._on_export_json)
        toolbar.addSeparator()
        toolbar.addAction("关于", self._on_about)

    def _setup_statusbar(self):
        """设置状态栏"""
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)

        self.lbl_stats = QLabel("就绪")
        self.statusbar.addWidget(self.lbl_stats, 1)

        self.lbl_count = QLabel("报文: 0")
        self.lbl_count.setStyleSheet("color: #666; padding: 0 8px;")
        self.statusbar.addPermanentWidget(self.lbl_count)

        self.lbl_version = QLabel(f"v{self.APP_VERSION}")
        self.lbl_version.setStyleSheet("color: #999; padding: 0 8px;")
        self.statusbar.addPermanentWidget(self.lbl_version)

    def _update_status_stats(self):
        """更新状态栏统计"""
        stats = self.parser.get_statistics()
        total = stats["total"]
        valid = stats["valid"]
        yk_count = sum(1 for f in self.all_frames if f.signal_type == SignalType.YK)
        yt_count = sum(1 for f in self.all_frames if f.signal_type == SignalType.YT)

        self.lbl_stats.setText(
            f"总报文: {total}  |  有效: {valid}  |  无效: {total - valid}  |  "
            f"I帧: {stats['i_frames']}  S帧: {stats['s_frames']}  U帧: {stats['u_frames']}  |  "
            f"主站→子站: {stats['master_send']}  |  子站→主站: {stats['slave_send']}  |  "
            f"遥控: {yk_count}  遥调: {yt_count}"
        )
        self.lbl_count.setText(f"报文: {total}")

    # ==================================================================
    # 解析
    # ==================================================================

    def _on_parse(self):
        """解析报文"""
        raw_text = self.hex_input.toPlainText()
        lines = raw_text.strip().split("\n")

        hex_lines = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            hex_lines.append(line)

        if not hex_lines:
            QMessageBox.information(self, "提示", "请输入十六进制报文数据")
            return

        self.lbl_line_count.setText(f"{len(hex_lines)} 行")
        self.parser.clear()
        self.all_frames = self.parser.parse_multiple(hex_lines)

        self._populate_table()
        self._populate_tree(0)
        self._update_status_stats()

        self.result_tabs.setCurrentIndex(1)

        error_count = sum(1 for f in self.all_frames if f.error)
        self.statusbar.showMessage(
            f"解析完成: {len(hex_lines)} 条报文, {error_count} 条失败", 5000)

    def _on_clear(self):
        """清空"""
        self.hex_input.clear()
        self.parser.clear()
        self.all_frames.clear()
        self.tree_widget.clear()
        self.table_widget.setRowCount(0)
        self.hex_view.clear()
        self.lbl_line_count.setText("0 行")
        self._update_status_stats()

    # ==================================================================
    # 导入/导出
    # ==================================================================

    def _on_import_file(self):
        """导入文件"""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入报文文件", "",
            "报文文件 (*.txt *.log *.hex *.dat);;二进制文件 (*.bin);;所有文件 (*.*)"
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            self.hex_input.setPlainText(content)
            self.statusbar.showMessage(f"已导入: {os.path.basename(path)}", 3000)
        except Exception:
            try:
                with open(path, "rb") as f:
                    raw = f.read()
                hex_lines = []
                for i in range(0, len(raw), 16):
                    chunk = raw[i:i+16]
                    hex_lines.append(chunk.hex(" ").upper())
                self.hex_input.setPlainText("\n".join(hex_lines))
                self.statusbar.showMessage(
                    f"已导入二进制文件: {os.path.basename(path)}", 3000)
            except Exception as e2:
                QMessageBox.critical(self, "导入失败", f"无法读取文件:\n{e2}")

    def _on_export_csv(self):
        """导出CSV"""
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有可导出的数据")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出CSV", "iec104_parsed.csv",
                                                "CSV文件 (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["序号", "方向", "信号类型", "帧类型", "类型标识", "类型描述",
                                 "传送原因", "传送原因描述", "公共地址", "对象数",
                                 "信息对象地址", "值", "时标", "错误"])
                for frame in self.all_frames:
                    if frame.asdu and frame.asdu.information_elements:
                        for elem in frame.asdu.information_elements:
                            ts = elem.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] \
                                if elem.timestamp else ""
                            writer.writerow([
                                frame.index, frame.direction,
                                frame.signal_label,
                                frame.apci.frame_type if frame.apci else "",
                                frame.asdu.type_id,
                                TypeID.get_description(frame.asdu.type_id),
                                frame.asdu.cot_cause,
                                COT.get_description(frame.asdu.cot),
                                frame.asdu.common_address,
                                len(frame.asdu.information_elements),
                                f"0x{elem.ioa:06X}",
                                elem.value_desc, ts, frame.error
                            ])
                    else:
                        writer.writerow([
                            frame.index, frame.direction if frame.direction else "",
                            "", frame.apci.frame_type if frame.apci else "",
                            "", "", "", "", "", "",
                            "", frame.description if frame.description else "",
                            "", frame.error
                        ])
            self.statusbar.showMessage(f"已导出CSV: {os.path.basename(path)}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _on_export_json(self):
        """导出JSON"""
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有可导出的数据")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出JSON", "iec104_parsed.json",
                                                "JSON文件 (*.json)")
        if not path:
            return
        try:
            data = []
            for frame in self.all_frames:
                entry = {
                    "index": frame.index,
                    "raw_hex": frame.raw_hex,
                    "direction": frame.direction,
                    "send_role": frame.send_role,
                    "recv_role": frame.recv_role,
                    "signal_type": frame.signal_label,
                    "description": frame.description,
                    "error": frame.error,
                    "apci": None,
                    "asdu": None,
                }
                if frame.apci:
                    entry["apci"] = {
                        "frame_type": frame.apci.frame_type,
                        "send_seq": frame.apci.send_seq,
                        "recv_seq": frame.apci.recv_seq,
                        "apdu_length": frame.apci.apdu_length,
                    }
                if frame.asdu:
                    entry["asdu"] = {
                        "type_id": frame.asdu.type_id,
                        "type_name": TypeID.get_name(frame.asdu.type_id),
                        "type_desc": TypeID.get_description(frame.asdu.type_id),
                        "signal_type": frame.asdu.signal_type.label,
                        "vsq": frame.asdu.vsq,
                        "object_count": frame.asdu.number_of_objects,
                        "is_sequence": frame.asdu.is_sequence,
                        "cot": frame.asdu.cot,
                        "cot_cause": frame.asdu.cot_cause,
                        "cot_desc": COT.get_description(frame.asdu.cot),
                        "cot_test": frame.asdu.cot_test,
                        "cot_pn": frame.asdu.cot_pn,
                        "org": frame.asdu.org,
                        "common_address": frame.asdu.common_address,
                        "elements": []
                    }
                    for elem in frame.asdu.information_elements:
                        entry["asdu"]["elements"].append({
                            "ioa": f"0x{elem.ioa:06X}",
                            "signal_type": elem.signal_type.label,
                            "value": str(elem.value),
                            "value_desc": elem.value_desc,
                            "quality": str(elem.quality) if elem.quality else None,
                            "timestamp": elem.timestamp.isoformat() if elem.timestamp else None,
                        })
                data.append(entry)

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.statusbar.showMessage(f"已导出JSON: {os.path.basename(path)}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _on_export_txt(self):
        """导出TXT"""
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有可导出的数据")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出TXT", "iec104_parsed.txt",
                                                "文本文件 (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("=" * 80 + "\n")
                f.write("IEC60870-5-104 报文解析报告\n")
                f.write(f"解析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"报文总数: {len(self.all_frames)}\n")
                f.write("=" * 80 + "\n\n")

                for frame in self.all_frames:
                    f.write(f"--- 报文 #{frame.index} ---\n")
                    f.write(f"方向: {frame.direction}\n")
                    if frame.signal_label:
                        f.write(f"信号类型: {frame.signal_label}\n")
                    f.write(f"原始数据: {frame.raw_hex}\n")
                    if frame.error:
                        f.write(f"错误: {frame.error}\n")
                    elif frame.description:
                        f.write(f"描述: {frame.description}\n")
                    if frame.apci:
                        f.write(f"帧类型: {frame.apci.frame_type}\n")
                        f.write(f"发送序号SN: {frame.apci.send_seq}\n")
                        f.write(f"接收序号RN: {frame.apci.recv_seq}\n")
                    if frame.asdu:
                        f.write(f"类型标识: {frame.asdu.type_id} "
                                f"({TypeID.get_description(frame.asdu.type_id)})\n")
                        f.write(f"传送原因: {COT.get_description(frame.asdu.cot)}\n")
                        f.write(f"公共地址: {frame.asdu.common_address}\n")
                        f.write(f"信息对象数: {frame.asdu.number_of_objects}\n")
                        for elem in frame.asdu.information_elements:
                            f.write(f"  IOA=0x{elem.ioa:06X} [{elem.signal_type.label}]: "
                                    f"{elem.value_desc}\n")
                            if elem.quality:
                                f.write(f"    品质: {elem.quality}\n")
                            if elem.timestamp:
                                f.write(f"    时标: {elem.timestamp}\n")
                    f.write("\n")
            self.statusbar.showMessage(f"已导出TXT: {os.path.basename(path)}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    # ==================================================================
    # 填充表格/树
    # ==================================================================

    def _populate_table(self):
        """填充报文列表表格"""
        self.table_widget.setRowCount(0)
        for frame in self.all_frames:
            row = self.table_widget.rowCount()
            self.table_widget.insertRow(row)

            self._set_table_item(row, 0, str(frame.index))

            # 方向
            dir_item = QTableWidgetItem(frame.direction)
            if "主站→子站" in frame.direction:
                dir_item.setForeground(QColor("#E91E63"))
            elif "子站→主站" in frame.direction:
                dir_item.setForeground(QColor("#2196F3"))
            self.table_widget.setItem(row, 1, dir_item)

            # 信号类型
            sig_item = QTableWidgetItem(frame.signal_label if frame.signal_label else "-")
            if frame.signal_type != SignalType.OTHER:
                sig_item.setForeground(QColor(frame.signal_type.color_hex))
                sig_item.setFont(QFont(self.font().family(), self.font().pointSize(), QFont.Bold))
            self.table_widget.setItem(row, 2, sig_item)

            # 类型标识
            if frame.asdu:
                tid_text = f"{frame.asdu.type_id}: {TypeID.get_description(frame.asdu.type_id)}"
            else:
                tid_text = frame.apci.frame_type if frame.apci else "-"
            self._set_table_item(row, 3, tid_text)

            # 传送原因
            if frame.asdu:
                cot_text = f"({frame.asdu.cot_cause}) {COT.get_description(frame.asdu.cot)}"
            else:
                cot_text = "-"
            self._set_table_item(row, 4, cot_text)

            # 公共地址
            ca_text = f"0x{frame.asdu.common_address:04X}" if frame.asdu else "-"
            self._set_table_item(row, 5, ca_text)

            # 对象数
            obj_text = str(frame.asdu.number_of_objects) if frame.asdu else "-"
            self._set_table_item(row, 6, obj_text)

            # 值 (汇总)
            if frame.asdu and frame.asdu.information_elements:
                vals = []
                for elem in frame.asdu.information_elements:
                    vals.append(elem.value_desc)
                self._set_table_item(row, 7, " | ".join(vals))
            else:
                self._set_table_item(row, 7, "-")

            # 描述
            desc = frame.description if frame.description else (frame.error if frame.error else "")
            self._set_table_item(row, 8, desc)

        self.table_widget.resizeColumnsToContents()
        # 确保关键列有足够宽度
        self.table_widget.setColumnWidth(2, 60)
        self.table_widget.setColumnWidth(7, 300)

    def _set_table_item(self, row: int, col: int, text: str):
        item = QTableWidgetItem(text)
        self.table_widget.setItem(row, col, item)

    def _populate_tree(self, frame_index: int):
        """填充解析树"""
        self.tree_widget.clear()

        if frame_index < 0 or frame_index >= len(self.all_frames):
            return

        frame = self.all_frames[frame_index]

        # 根节点
        self._add_tree_item(None, f"报文 #{frame.index}", frame.raw_hex, "")

        # 基本信息
        info_root = self._add_tree_item(None, "基本信息", "", "")

        if frame.direction:
            self._add_tree_item(info_root, "发送方向", frame.direction, "")
            self._add_tree_item(info_root, "发送方角色", frame.send_role, "")
            self._add_tree_item(info_root, "接收方角色", frame.recv_role, "")

        # 信号类型
        if frame.signal_label:
            sig_color = QColor(frame.signal_type.color_hex)
            self._add_tree_item(info_root, "信号类型", frame.signal_label,
                                self._signal_type_extra(frame.signal_type), sig_color)

        if frame.error:
            self._add_tree_item(info_root, "错误", frame.error, "解析错误", QColor("#F44336"))
            return

        if frame.description:
            self._add_tree_item(info_root, "描述", frame.description, "")

        # APCI
        if frame.apci:
            apci = frame.apci
            apci_root = self._add_tree_item(
                None, "APCI (应用规约控制信息)", "", "帧控制信息")
            self._add_tree_item(apci_root, "起始字节", f"0x{apci.start_byte:02X} (68H)", "固定0x68")
            self._add_tree_item(apci_root, "APDU长度", f"{apci.apdu_length} 字节",
                                f"总帧长={apci.total_length}字节")
            self._add_tree_item(apci_root, "帧类型", apci.frame_type,
                                self._frame_type_desc(apci.frame_type))
            self._add_tree_item(apci_root, "发送序号SN", str(apci.send_seq),
                                "Send Sequence Number")
            self._add_tree_item(apci_root, "接收序号RN", str(apci.recv_seq),
                                "Receive Sequence Number")

        # ASDU
        if frame.asdu:
            asdu = frame.asdu
            asdu_root = self._add_tree_item(
                None, "ASDU (应用服务数据单元)", "", "应用数据")

            sig_color = QColor(asdu.signal_type.color_hex) \
                if asdu.signal_type != SignalType.OTHER else None
            self._add_tree_item(asdu_root, "信号类型",
                                f"{asdu.signal_type.label}",
                                "", sig_color)

            self._add_tree_item(asdu_root, "类型标识",
                                f"{asdu.type_id} ({TypeID.get_name(asdu.type_id)})",
                                TypeID.get_description(asdu.type_id))
            self._add_tree_item(asdu_root, "VSQ (可变结构限定词)",
                                f"0x{asdu.vsq:02X}",
                                f"对象数={asdu.number_of_objects}, "
                                f"地址类型={'顺序' if asdu.is_sequence else '离散'}")
            self._add_tree_item(asdu_root, "传送原因",
                                f"0x{asdu.cot:04X} ({asdu.cot_cause})",
                                COT.get_description(asdu.cot) +
                                (", 试验" if asdu.cot_test else "") +
                                (", 否定确认" if asdu.cot_pn else ""))
            self._add_tree_item(asdu_root, "源发方地址(ORG)",
                                f"0x{asdu.org:02X} ({asdu.org})",
                                "发方/源地址(OCTET)")
            self._add_tree_item(asdu_root, "公共地址(ASDU地址)",
                                f"0x{asdu.common_address:04X} ({asdu.common_address})",
                                "公共地址/ASDU地址(2字节)")

            # 信息元素
            for i, elem in enumerate(asdu.information_elements):
                elem_sig = elem.signal_type.label
                elem_root = self._add_tree_item(
                    asdu_root, f"信息元素 #{i+1}",
                    f"IOA=0x{elem.ioa:06X} [{elem_sig}]",
                    f"信息对象地址")
                if elem.value_desc:
                    self._add_tree_item(elem_root, "值", elem.value_desc, "")
                if elem.quality:
                    self._add_tree_item(elem_root, "品质描述词",
                                        f"0x{elem.quality.raw:02X}",
                                        str(elem.quality))
                if elem.timestamp:
                    ts_str = elem.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    self._add_tree_item(elem_root, "时标", ts_str,
                                        "CP56Time2a" if len(elem.value_desc) > 50 else "")

            # 补齐字段
            if asdu.pad:
                self._add_tree_item(asdu_root, "补齐字段", f"0x{asdu.pad:02X}", "0补齐对齐")

        self.tree_widget.expandAll()
        self._update_hex_view(frame_index)

    def _signal_type_extra(self, st: SignalType) -> str:
        """信号类型额外说明"""
        return {
            SignalType.YX: "状态量(单点/双点信息)",
            SignalType.YC: "模拟量(测量值)",
            SignalType.YK: "开关控制(命令)",
            SignalType.YT: "调节控制(设定值/步位置)",
            SignalType.YM: "累计量/电能",
            SignalType.OTHER: "",
        }.get(st, "")

    def _add_tree_item(self, parent, col0: str, col1: str, col2: str,
                       fg_color: QColor = None) -> QTreeWidgetItem:
        if parent:
            item = QTreeWidgetItem(parent)
        else:
            item = QTreeWidgetItem(self.tree_widget)
        item.setText(0, col0)
        item.setText(1, col1)
        item.setText(2, col2)
        if fg_color:
            for col in range(3):
                item.setForeground(col, fg_color)
        return item

    def _frame_type_desc(self, ftype: str) -> str:
        if ftype == "I-format":
            return "信息传输格式(编号帧,携带ASDU数据)"
        elif ftype == "S-format":
            return "编号监视格式(确认帧,无ASDU数据)"
        elif ftype == "U-format":
            return "未编号控制格式(启动/停止/测试)"
        return ""

    def _update_hex_view(self, frame_index: int):
        """更新十六进制视图"""
        if frame_index < 0 or frame_index >= len(self.all_frames):
            return
        frame = self.all_frames[frame_index]
        self.hex_view.setPlainText(
            f"# 报文 #{frame.index} 原始十六进制\n\n"
            f"原始: {frame.raw_hex}\n\n"
            f"格式化:\n{self._format_hex(frame.raw_hex)}"
        )

    def _format_hex(self, raw: str) -> str:
        """格式化十六进制字符串，每16字节换行"""
        cleaned = raw.replace(" ", "").replace("\n", "")
        cleaned = ''.join(c for c in cleaned if c in '0123456789ABCDEFabcdef')
        if len(cleaned) % 2 != 0:
            return raw
        result = []
        byte_count = 0
        for i in range(0, len(cleaned), 2):
            result.append(cleaned[i:i+2].upper())
            byte_count += 1
            if byte_count % 16 == 0:
                result.append("\n")
        return " ".join(result)

    # ==================================================================
    # 事件处理
    # ==================================================================

    def _on_table_click(self, row: int, col: int):
        """表格行点击 -> 显示详情树和十六进制"""
        self._populate_tree(row)
        self.result_tabs.setCurrentIndex(0)

    def _on_tree_select(self):
        """树节点选择"""
        pass

    def _on_about(self):
        """关于对话框"""
        AboutDialog(self.APP_TITLE, self.APP_VERSION).exec_()

    def _on_filter(self):
        """应用过滤"""
        signal = self.filter_signal.currentData()
        type_id = self.filter_type.currentData()
        cot = self.filter_cot.currentData()
        addr_text = self.filter_addr.text().strip()

        filtered = self.all_frames
        if signal:
            filtered = [f for f in filtered if f.signal_label == signal]
        if type_id != -1 and type_id is not None:
            filtered = [f for f in filtered if f.asdu and f.asdu.type_id == type_id]
        if cot != -1 and cot is not None:
            filtered = [f for f in filtered
                        if f.asdu and f.asdu.cot_cause == cot]
        if addr_text:
            try:
                addr = int(addr_text)
                filtered = [f for f in filtered
                            if f.asdu and f.asdu.common_address == addr]
            except ValueError:
                pass

        saved = self.all_frames
        self.all_frames = filtered
        self._populate_table()
        self.all_frames = saved
        self.statusbar.showMessage(
            f"过滤结果: {len(filtered)}/{len(self.all_frames)} 条报文", 3000)

    def _on_filter_reset(self):
        """重置过滤"""
        self.filter_signal.setCurrentIndex(0)
        self.filter_type.setCurrentIndex(0)
        self.filter_cot.setCurrentIndex(0)
        self.filter_addr.clear()
        self._populate_table()
        self.statusbar.showMessage("过滤已重置", 2000)
