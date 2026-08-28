"""pytest 共享配置（L6 收尾）。

集中处理各测试文件重复的初始化：
1. 项目根目录注入 sys.path；
2. 离屏 Qt 平台（必须在任何 QApplication 创建前设置）；
3. 授权文件隔离（避免测试受本机激活状态影响）；
4. 进程级 QApplication 单例 fixture。

注意：pytest 会在收集测试前加载本文件，因此上述副作用对所有测试统一生效；
各测试文件若需独立以 `python tests/xxx.py` 运行，仍保留各自的模块级等价设置。
"""
import os
import sys

# 项目根目录（tests/ 的上一级）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 必须在创建 QApplication 前指定离屏平台，避免弹出真实窗口
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 隔离真实授权文件：所有测试统一指向一个不存在的授权文件，防止受本机激活状态影响
import license as L

L.LICENSE_FILE = os.path.join(_ROOT, "config", "__test_no_license__.dat")

import pytest


@pytest.fixture(scope="session")
def qapp():
    """进程级 QApplication 单例（函数局部引用会被 GC 掉，导致窗口销毁）"""
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
