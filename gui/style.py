"""
全局样式常量 - 浅色/深色主题 QSS

目的：不改变整体视觉语言，只做一致性统一——
  · 所有按钮统一圆角与内边距
  · 输入类控件统一边框/圆角/焦点高亮环
  · GroupBox、Tab、工具栏细节对齐深色主题已有的处理
深色主题样式表原内联于 main_window._apply_dark_theme，现提取到本文件统一维护；
子对话框通过 MainWindow._apply_theme_to_dialog 继承当前主题表。
"""

LIGHT_QSS = """
QGroupBox {
    border: 1px solid #D8D8D8;
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 12px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #444444;
}

QLineEdit, QPlainTextEdit, QTextEdit,
QComboBox, QSpinBox, QDoubleSpinBox {
    border: 1px solid #C9C9C9;
    border-radius: 4px;
    padding: 2px 4px;
    background-color: #FFFFFF;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #2196F3;
}
QComboBox::drop-down { border: none; width: 18px; }
/* 下拉弹出列表：QSS 设置 QComboBox 后，弹出列表绘制即被样式表接管，
   必须显式给出背景/文字/hover/选中色，否则 hover 项白底白字"消失" */
QComboBox QAbstractItemView {
    background-color: #FFFFFF;
    color: #222222;
    border: 1px solid #C9C9C9;
    outline: none;
    /* 弹出列表的 hover/选中行走 selected 通道，由这两个属性决定，
       缺失时会回落调色板导致白底白字 */
    selection-background-color: #E3F2FD;
    selection-color: #0D47A1;
}
QComboBox QAbstractItemView::item { min-height: 22px; padding: 2px 6px; }
QComboBox QAbstractItemView::item:hover {
    background-color: #E3F2FD;
    color: #0D47A1;
}
QComboBox QAbstractItemView::item:selected {
    background-color: #E3F2FD;
    color: #0D47A1;
}

/* 注意：QSS 一旦设置圆角/内边距，按钮绘制即由样式表接管，
   必须显式给出边框与背景，否则按钮会退化成无边框纯文字 */
QPushButton {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                      stop:0 #FFFFFF, stop:1 #E9E9E9);
    border: 1px solid #BEBEBE;
    border-radius: 4px;
    padding: 4px 14px;
}
QPushButton:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                      stop:0 #FAFDFF, stop:1 #E2EEF9);
}
QPushButton:pressed { background-color: #DDEAF7; }
QPushButton:focus, QPushButton:default { border-color: #2196F3; }
QPushButton:disabled {
    color: #999999;
    background-color: #F2F2F2;
    border-color: #D8D8D8;
}

QTabWidget::pane { border: 1px solid #D5D5D5; }
QTabBar::tab { padding: 5px 14px; }
QTabBar::tab:selected { border-bottom: 2px solid #2196F3; color: #1565C0; font-weight: bold; }

QTreeWidget::item { padding: 2px 2px; }
QTreeWidget::item:hover { background: rgba(33,150,243,0.08); }
QTreeWidget::item:selected { background-color: #2196F3; color: white; }
QTreeWidget::item:selected:hover { background-color: #1976D2; color: white; }

QToolBar {
    border-bottom: 1px solid #DDDDDD;
    spacing: 2px;
}
QToolBar QToolButton { padding: 3px 8px; border-radius: 4px; }
QToolBar QToolButton:hover { background: rgba(33,150,243,0.12); }

QStatusBar::item { border: none; }

QSplitter::handle { background: #DDDDDD; }
QSplitter::handle:hover { background: #2196F3; }

QScrollBar:vertical { background: #F0F0F0; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #BDBDBD; border-radius: 5px; min-height: 30px; margin: 2px; }
QScrollBar::handle:vertical:hover { background: #9E9E9E; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #F0F0F0; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: #BDBDBD; border-radius: 5px; min-width: 30px; margin: 2px; }
QScrollBar::handle:horizontal:hover { background: #9E9E9E; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }

QToolTip {
    border: 1px solid #999999;
    padding: 4px;
    background-color: #FFFFFF;
    color: #333333;
}
"""


DARK_QSS = """
QMainWindow, QWidget { background-color: #2b2b2b; color: #e0e0e0; }
QGroupBox { border: 1px solid #555; border-radius: 4px; margin-top: 8px; padding-top: 16px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
QPlainTextEdit, QTextEdit, QLineEdit { background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #555; border-radius: 3px; }
QPlainTextEdit:focus, QTextEdit:focus, QLineEdit:focus { border-color: #2196F3; }
QLineEdit:disabled { background-color: #2a2a2a; color: #777; }
QTableWidget { background-color: #1e1e1e; color: #d4d4d4; gridline-color: #404040; }
QTableWidget::item { padding: 2px 4px; }
QTableWidget::item:selected { background-color: #264f78; }
QTableWidget::item:hover { background-color: #2a3f55; }
QHeaderView::section { background-color: #333; color: #ccc; border: 1px solid #555; padding: 4px; }
QComboBox { background-color: #3c3c3c; color: #d4d4d4; border: 1px solid #555; border-radius: 3px; padding: 3px; }
QComboBox QAbstractItemView { background-color: #3c3c3c; color: #d4d4d4; selection-background-color: #264f78; selection-color: #ffffff; outline: none; }
QComboBox QAbstractItemView::item { min-height: 22px; padding: 2px 6px; }
QComboBox QAbstractItemView::item:hover { background-color: #264f78; color: #ffffff; }
QComboBox QAbstractItemView::item:selected { background-color: #264f78; color: #ffffff; }
QPushButton { background-color: #3c3c3c; color: #d4d4d4; border: 1px solid #555; border-radius: 4px; padding: 4px 12px; }
QPushButton:hover { background-color: #4a4a4a; }
QPushButton:pressed { background-color: #2a2a2a; }
QPushButton:disabled { color: #777; background-color: #2a2a2a; border-color: #444; }
QToolBar { background-color: #333; border-bottom: 1px solid #555; }
QToolBar QToolButton { padding: 3px 8px; border-radius: 4px; }
QToolBar QToolButton:hover { background: rgba(33,150,243,0.2); }
QTabWidget::pane { border: 1px solid #555; }
QTabBar::tab { background-color: #333; color: #ccc; padding: 6px 16px; border: 1px solid #555; border-bottom: none; }
QTabBar::tab:selected { background-color: #2b2b2b; color: #fff; }
QTabBar::tab:hover:!selected { background-color: #3a3a3a; }
QTreeWidget { background-color: #1e1e1e; color: #d4d4d4; }
QTreeWidget::item { padding: 2px 2px; }
QTreeWidget::item:selected { background-color: #1976D2; color: white; }
QTreeWidget::item:selected:hover { background-color: #1565C0; color: white; }
QTreeWidget::item:hover { background-color: #2a3f55; }
QProgressBar { border: 1px solid #555; border-radius: 3px; text-align: center; }
QProgressBar::chunk { background-color: #2196F3; }
QMenuBar { background-color: #333; color: #e0e0e0; }
QMenuBar::item:selected { background-color: #4a4a4a; }
QMenu { background-color: #333; color: #e0e0e0; }
QMenu::item:selected { background-color: #264f78; }
QMenu::separator { background: #555; height: 1px; margin: 4px 8px; }
QStatusBar { background-color: #2b2b2b; color: #bbb; }
QStatusBar::item { border: none; }
QSplitter::handle { background: #333; }
QSplitter::handle:hover { background: #2196F3; }
QScrollBar:vertical { background: #2b2b2b; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #555; border-radius: 5px; min-height: 30px; margin: 2px; }
QScrollBar::handle:vertical:hover { background: #777; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #2b2b2b; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: #555; border-radius: 5px; min-width: 30px; margin: 2px; }
QScrollBar::handle:horizontal:hover { background: #777; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }
QToolTip { border: 1px solid #666; padding: 4px; background-color: #333; color: #e0e0e0; }
QCheckBox::indicator { width: 14px; height: 14px; }
QCheckBox { spacing: 4px; }
QDialog { background-color: #2b2b2b; }
QMessageBox { background-color: #2b2b2b; }
QTableView { background-color: #1e1e1e; color: #d4d4d4; gridline-color: #404040; }
"""


# ============================================================================
# 选择高亮调色板
# ============================================================================
# 下拉弹出列表的 hover/选中行由委托按 QPalette.Highlight / HighlightedText
# 绘制，QSS 的 ::item:hover 子控件选择器对它不生效——应用主题时须对每个
# QComboBox 的 view 显式 setPalette，否则会出现白底白字等不可读配色。
def apply_selection_palette(widget, dark: bool):
    """把弹出列表高亮配色应用到 widget 下所有 QComboBox 的 view"""
    from PyQt5.QtWidgets import QComboBox
    from PyQt5.QtGui import QPalette, QColor
    highlight = "#264F78" if dark else "#2196F3"
    base = "#1E1E1E" if dark else "#FFFFFF"
    text = "#D4D4D4" if dark else "#222222"
    for combo in widget.findChildren(QComboBox):
        view = combo.view()
        if view is None:
            continue
        pal = view.palette()
        pal.setColor(QPalette.Highlight, QColor(highlight))
        pal.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
        pal.setColor(QPalette.Base, QColor(base))
        pal.setColor(QPalette.Text, QColor(text))
        view.setPalette(pal)
