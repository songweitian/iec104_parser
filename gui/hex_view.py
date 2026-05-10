"""
Hex 视图工具 (预留，当前嵌入在主窗口中)
"""

from PyQt5.QtWidgets import QPlainTextEdit
from PyQt5.QtGui import QFont, QColor, QTextCursor


class HexHighlighter:
    """十六进制高亮器 (预留功能)"""

    def __init__(self, text_edit: QPlainTextEdit):
        self.text_edit = text_edit

    def highlight_range(self, start: int, end: int,
                        bg_color: str = "#FFF9C4",
                        text_color: str = "#000000"):
        """高亮指定范围 (当前为预留实现)"""
        pass

    def clear_highlight(self):
        """清除高亮"""
        pass