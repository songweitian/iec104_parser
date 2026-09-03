"""
历史报文管理对话框
保存/加载解析会话
"""

import os
import sys
import json
import time
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QMessageBox,
    QLineEdit, QInputDialog
)
from PyQt5.QtCore import Qt


def _get_app_dir() -> str:
    """获取应用数据目录（兼容 --onefile 和开发模式）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


HISTORY_DIR = os.path.join(_get_app_dir(), "sessions")


class HistoryDialog(QDialog):
    """历史报文管理对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("历史报文管理")
        self.setMinimumSize(600, 400)
        self.selected_session = None
        self._setup_ui()
        self._load_sessions()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 搜索栏
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("搜索:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入关键字搜索...")
        self.search_input.textChanged.connect(self._filter_sessions)
        search_layout.addWidget(self.search_input)
        layout.addLayout(search_layout)

        # 会话列表
        self.session_table = QTableWidget()
        self.session_table.setColumnCount(4)
        self.session_table.setHorizontalHeaderLabels(["会话名称", "保存时间", "报文数量", "文件大小"])
        self.session_table.horizontalHeader().setStretchLastSection(True)
        self.session_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.session_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.session_table.doubleClicked.connect(self._on_load)
        layout.addWidget(self.session_table)

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        save_btn = QPushButton("保存当前会话")
        save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(save_btn)

        load_btn = QPushButton("加载选中会话")
        load_btn.clicked.connect(self._on_load)
        btn_layout.addWidget(load_btn)

        delete_btn = QPushButton("删除选中")
        delete_btn.clicked.connect(self._on_delete)
        btn_layout.addWidget(delete_btn)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

    def _load_sessions(self):
        """加载历史会话列表"""
        if not os.path.exists(HISTORY_DIR):
            os.makedirs(HISTORY_DIR)

        sessions = []
        for f in os.listdir(HISTORY_DIR):
            if f.endswith('.json'):
                path = os.path.join(HISTORY_DIR, f)
                try:
                    with open(path, 'r', encoding='utf-8') as fh:
                        data = json.load(fh)
                    mtime = os.path.getmtime(path)
                    size = os.path.getsize(path)
                    sessions.append({
                        'name': data.get('name', f.replace('.json', '')),
                        'time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mtime)),
                        'count': data.get('frame_count', 0),
                        'size': f'{size / 1024:.1f} KB',
                        'file': f
                    })
                except Exception:
                    continue

        sessions.sort(key=lambda x: x['time'], reverse=True)

        self.session_table.setRowCount(len(sessions))
        for i, s in enumerate(sessions):
            name_item = QTableWidgetItem(s['name'])
            name_item.setData(Qt.UserRole, s['file'])
            self.session_table.setItem(i, 0, name_item)
            self.session_table.setItem(i, 1, QTableWidgetItem(s['time']))
            self.session_table.setItem(i, 2, QTableWidgetItem(str(s['count'])))
            self.session_table.setItem(i, 3, QTableWidgetItem(s['size']))

    def _filter_sessions(self):
        """过滤会话列表"""
        keyword = self.search_input.text().lower()
        for i in range(self.session_table.rowCount()):
            name = self.session_table.item(i, 0)
            if name:
                self.session_table.setRowHidden(i, keyword not in name.text().lower())

    def _on_save(self):
        """保存当前会话"""
        name, ok = QInputDialog.getText(self, "保存会话", "会话名称:")
        if not ok or not name:
            return

        # 获取主窗口数据
        main_window = self.parent()
        if not main_window:
            return

        session_data = {
            'name': name,
            'frame_count': len(main_window.all_frames),
            'input_text': main_window.hex_input.toPlainText(),
            'parse_mode': main_window.parse_mode.value,
        }

        if not os.path.exists(HISTORY_DIR):
            os.makedirs(HISTORY_DIR)

        filename = f"{name.replace(' ', '_')}_{int(time.time())}.json"
        path = os.path.join(HISTORY_DIR, filename)

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)

        self._load_sessions()
        QMessageBox.information(self, "保存成功", f"会话已保存: {filename}")

    def _on_load(self):
        """加载选中会话"""
        row = self.session_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择一个会话")
            return

        name_item = self.session_table.item(row, 0)
        if not name_item:
            return

        # 从 UserRole 获取文件路径
        filename = name_item.data(Qt.UserRole)
        if not filename:
            return

        path = os.path.join(HISTORY_DIR, filename)
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            self.selected_session = data
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "加载失败", f"无法加载会话: {e}")

    def _on_delete(self):
        """删除选中会话"""
        row = self.session_table.currentRow()
        if row < 0:
            return

        name_item = self.session_table.item(row, 0)
        if not name_item:
            return

        filename = name_item.data(Qt.UserRole)
        if not filename:
            return

        reply = QMessageBox.question(self, "确认删除",
                                     f"确定要删除会话 '{name_item.text()}' 吗？",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        path = os.path.join(HISTORY_DIR, filename)
        try:
            os.remove(path)
            self._load_sessions()
        except Exception as e:
            QMessageBox.warning(self, "删除失败", f"无法删除会话: {e}")
