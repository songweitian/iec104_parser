"""
IEC60870-5-104 报文解析器 - 入口文件
双击运行启动 GUI 程序
"""

import sys
import os

# 确保可以导入项目模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui.main_window import MainWindow
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont


def main():
    """应用程序入口"""
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 设置全局字体
    font = QFont("Microsoft YaHei", 9)
    app.setFont(font)

    # 设置应用程序元数据
    app.setApplicationName("IEC104 Parser")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("SWT")

    # 启动主窗口
    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()