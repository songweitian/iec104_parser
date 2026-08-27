"""
全局样式常量 - 浅色主题基础 QSS

目的：不改变整体视觉语言，只做一致性统一——
  · 所有按钮统一圆角与内边距
  · 输入类控件统一边框/圆角/焦点高亮环
  · GroupBox、Tab、工具栏细节对齐深色主题已有的处理
深色主题仍使用 main_window 内的完整样式表；子对话框通过
MainWindow._apply_theme_to_dialog 继承当前主题表。
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

QToolBar {
    border-bottom: 1px solid #DDDDDD;
    spacing: 2px;
}
QToolBar QToolButton { padding: 3px 8px; border-radius: 4px; }
QToolBar QToolButton:hover { background: rgba(33,150,243,0.12); }

QStatusBar::item { border: none; }

QToolTip {
    border: 1px solid #999999;
    padding: 4px;
    background-color: #FFFFFF;
    color: #333333;
}
"""
