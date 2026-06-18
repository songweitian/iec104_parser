"""
IOA 地址别名管理对话框
支持导入/导出映射表，保存为 JSON 文件
"""

import os
import json
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QFileDialog, QMessageBox,
    QHeaderView, QLineEdit, QGroupBox, QFormLayout
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont


ALIAS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "ioa_aliases.json")

# 缓存相关
_cached_aliases: dict = {}
_cached_aliases_mtime: float = 0.0


def load_aliases() -> dict:
    """加载IOA别名映射 {ioa_str: alias_name}，带缓存"""
    global _cached_aliases, _cached_aliases_mtime
    try:
        mtime = os.path.getmtime(ALIAS_FILE) if os.path.exists(ALIAS_FILE) else 0
    except OSError:
        mtime = 0

    if mtime != _cached_aliases_mtime:
        _cached_aliases_mtime = mtime
        if os.path.exists(ALIAS_FILE):
            try:
                with open(ALIAS_FILE, "r", encoding="utf-8") as f:
                    _cached_aliases = json.load(f)
            except Exception:
                _cached_aliases = {}
        else:
            _cached_aliases = {}
    return _cached_aliases


def invalidate_alias_cache():
    """使别名缓存失效（在保存后调用）"""
    global _cached_aliases_mtime
    _cached_aliases_mtime = 0


def save_aliases(aliases: dict):
    """保存IOA别名映射"""
    with open(ALIAS_FILE, "w", encoding="utf-8") as f:
        json.dump(aliases, f, ensure_ascii=False, indent=2)
    invalidate_alias_cache()


def get_alias(ioa: int) -> str:
    """获取单个IOA的别名，无别名返回空字符串"""
    aliases = load_aliases()
    return aliases.get(f"0x{ioa:06X}", "")


class IOAAliasDialog(QDialog):
    """IOA地址别名管理对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("IOA 地址别名管理")
        self.setMinimumSize(550, 450)
        self.aliases = load_aliases()
        self._setup_ui()
        self._populate_table()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 说明
        note = QLabel("为信息对象地址(IOA)设置自定义名称，方便识别。\n"
                      "映射保存在 ioa_aliases.json 文件中。")
        note.setStyleSheet("color: #555; padding: 4px;")
        note.setWordWrap(True)
        layout.addWidget(note)

        # 添加新别名
        add_group = QGroupBox("添加/编辑别名")
        add_layout = QHBoxLayout(add_group)

        add_layout.addWidget(QLabel("IOA地址:"))
        self.input_ioa = QLineEdit()
        self.input_ioa.setPlaceholderText("如: 0x000001 或 1")
        self.input_ioa.setMaximumWidth(120)
        add_layout.addWidget(self.input_ioa)

        add_layout.addWidget(QLabel("名称:"))
        self.input_name = QLineEdit()
        self.input_name.setPlaceholderText("如: 1号开关")
        add_layout.addWidget(self.input_name)

        btn_add = QPushButton("添加")
        btn_add.clicked.connect(self._on_add)
        add_layout.addWidget(btn_add)

        layout.addWidget(add_group)

        # 别名表格
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["IOA地址", "别名", "操作"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)

        # 底部按钮
        btn_layout = QHBoxLayout()

        btn_import = QPushButton("导入JSON")
        btn_import.clicked.connect(self._on_import)
        btn_layout.addWidget(btn_import)

        btn_export = QPushButton("导出JSON")
        btn_export.clicked.connect(self._on_export)
        btn_layout.addWidget(btn_export)

        btn_layout.addStretch()

        btn_clear = QPushButton("清空全部")
        btn_clear.clicked.connect(self._on_clear)
        btn_layout.addWidget(btn_clear)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        close_btn.setMinimumWidth(80)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

    def _populate_table(self):
        """填充表格"""
        self.table.setRowCount(0)
        for ioa_str, name in sorted(self.aliases.items()):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(ioa_str))
            self.table.setItem(row, 1, QTableWidgetItem(name))

            btn_del = QPushButton("删除")
            btn_del.setMaximumWidth(60)
            btn_del.clicked.connect(lambda checked, k=ioa_str: self._on_delete(k))
            self.table.setCellWidget(row, 2, btn_del)

    def _on_add(self):
        """添加/更新别名"""
        ioa_text = self.input_ioa.text().strip()
        name = self.input_name.text().strip()

        if not ioa_text or not name:
            QMessageBox.warning(self, "提示", "请填写IOA地址和名称")
            return

        # 解析IOA
        try:
            if ioa_text.startswith("0x") or ioa_text.startswith("0X"):
                ioa_val = int(ioa_text, 16)
            else:
                ioa_val = int(ioa_text)
            ioa_key = f"0x{ioa_val:06X}"
        except ValueError:
            QMessageBox.warning(self, "错误", "IOA地址格式不正确")
            return

        self.aliases[ioa_key] = name
        save_aliases(self.aliases)
        self._populate_table()
        self.input_ioa.clear()
        self.input_name.clear()

    def _on_delete(self, ioa_key: str):
        """删除单个别名"""
        if ioa_key in self.aliases:
            del self.aliases[ioa_key]
            save_aliases(self.aliases)
            self._populate_table()

    def _on_clear(self):
        """清空全部"""
        if not self.aliases:
            return
        reply = QMessageBox.question(
            self, "确认", "确定清空所有IOA别名？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.aliases.clear()
            save_aliases(self.aliases)
            self._populate_table()

    def _on_import(self):
        """从JSON文件导入"""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入IOA别名", "", "JSON文件 (*.json);;所有文件 (*.*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("JSON文件格式不正确，应为 {\"0x000001\": \"名称\", ...}")
            self.aliases.update(data)
            save_aliases(self.aliases)
            self._populate_table()
            QMessageBox.information(self, "成功", f"已导入 {len(data)} 条别名")
        except Exception as e:
            QMessageBox.critical(self, "导入失败", str(e))

    def _on_export(self):
        """导出为JSON文件"""
        if not self.aliases:
            QMessageBox.information(self, "提示", "没有可导出的别名")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出IOA别名", "ioa_aliases.json", "JSON文件 (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.aliases, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "成功", f"已导出到: {path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))
