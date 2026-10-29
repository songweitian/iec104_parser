"""应用数据目录解析（唯一事实来源）

打包安装到 Program Files 时 exe 所在目录没有写权限，所有需要落盘的数据
（授权文件、别名、历史会话、模拟器配置、崩溃日志）都必须写到
%APPDATA%/IEC104Parser，否则表现为"设置了但下次打开就没了"。

此前 license.py / simulator_dialog.py / ioa_alias_dialog.py / history_dialog.py
各抄了一份同样的实现，任何一处改动都容易漏。
"""
import os
import sys

APP_DIR_NAME = "IEC104Parser"


def get_app_dir() -> str:
    """返回可写的数据目录（一定存在；无法创建时退回 exe/脚本目录）"""
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA")
        if base:
            d = os.path.join(base, APP_DIR_NAME)
            try:
                os.makedirs(d, exist_ok=True)
            except OSError:
                pass
            return d
        return os.path.dirname(sys.executable or "")
    # 开发模式：项目根目录
    return os.path.dirname(os.path.abspath(__file__))


def get_log_dir() -> str:
    """崩溃日志目录（随数据目录，测试/开发时也便于查看）"""
    d = os.path.join(get_app_dir(), "logs")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        return get_app_dir()
    return d
