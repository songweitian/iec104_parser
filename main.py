"""
IEC60870-5-104 报文解析器 - 入口文件
双击运行启动 GUI 程序

启动即安装崩溃兜底：
- Python 层：任何槽函数里的未捕获异常不再让 PyQt5 触发 qFatal（表现为
  "窗口突然消失、没有任何提示"），而是写日志 + 弹窗提示；
- 原生层：faulthandler 把段错误的调用栈追加到同一份日志。
问题在 %APPDATA%/IEC104Parser/logs/crash.log（开发模式在项目目录 logs/）。
"""

import os
import sys
import traceback
from datetime import datetime

# 确保可以导入项目模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app_paths import get_log_dir
from version import APP_NAME, APP_VERSION, APP_ORG

_CRASH_LOG = None          # 保持打开的日志文件（faulthandler 需要）
_CRASH_LOG_PATH = ""


def _install_crash_handlers():
    """安装 Python/原生两级崩溃日志，返回日志路径"""
    global _CRASH_LOG, _CRASH_LOG_PATH

    _CRASH_LOG_PATH = os.path.join(get_log_dir(), "crash.log")
    try:
        _CRASH_LOG = open(_CRASH_LOG_PATH, "a", encoding="utf-8", buffering=1)
    except OSError:
        _CRASH_LOG = None

    # 原生崩溃（段错误/abort）：没有它，闪退后什么线索都不剩
    try:
        import faulthandler
        if _CRASH_LOG is not None:
            faulthandler.enable(file=_CRASH_LOG)
    except Exception:
        pass

    def _hook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if _CRASH_LOG is not None:
            try:
                _CRASH_LOG.write(f"\n===== {stamp} 未捕获异常 =====\n{text}")
            except Exception:
                pass
        # 控制台版本（源码运行）同时打印
        try:
            sys.stderr.write(text)
        except Exception:
            pass
        _show_crash_dialog(text)

    sys.excepthook = _hook
    return _CRASH_LOG_PATH


def _show_crash_dialog(text: str):
    """把异常显示给用户（尽量不二次崩溃）

    用 open() 而不是 exec_()：崩溃点可能就在某个槽里，再开一层模态事件
    循环等于在异常现场继续处理事件，很容易二次崩溃。
    """
    try:
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QApplication, QMessageBox
        if QApplication.instance() is None:
            return
        box = QMessageBox()
        box.setAttribute(Qt.WA_DeleteOnClose, True)
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("程序内部错误")
        box.setText("操作过程中发生内部错误，程序已恢复正常运行。")
        box.setInformativeText(
            f"详细信息已写入日志：\n{_CRASH_LOG_PATH}\n\n"
            "如问题可复现，请把该日志发给开发者。")
        box.setDetailedText(text)
        box.open()
    except Exception:
        pass


def main():
    """应用程序入口"""
    _install_crash_handlers()

    # 高分屏适配：跟随系统缩放（125%/150% 屏幕），必须在 QApplication 创建前设置
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QApplication

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    from gui.icons import create_app_icon
    from gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # 全局应用图标（对话框/任务栏统一使用）
    app.setWindowIcon(create_app_icon())

    # 设置全局字体
    font = QFont("Microsoft YaHei", 9)
    app.setFont(font)

    # 设置应用程序元数据
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_ORG)

    # 启动主窗口
    window = MainWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
