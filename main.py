"""
IEC60870-5-104 报文解析器 - 入口文件
双击运行启动 GUI 程序
"""

import sys
import os

# 确保可以导入项目模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui.main_window import MainWindow, create_app_icon
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont


def main():
    """应用程序入口"""
    # 高分屏适配：跟随系统缩放（125%/150% 屏幕），必须在 QApplication 创建前设置
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # 全局应用图标（对话框/任务栏统一使用）
    app.setWindowIcon(create_app_icon())

    # 设置全局字体
    font = QFont("Microsoft YaHei", 9)
    app.setFont(font)

    # 设置应用程序元数据
    app.setApplicationName("IEC104 Parser")
    app.setApplicationVersion("1.1.0")
    app.setOrganizationName("SWT")

    # 启动主窗口
    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()