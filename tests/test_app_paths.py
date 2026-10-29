"""应用数据目录解析：打包后落盘 %APPDATA%，避免在 Program Files 静默写失败"""
import os
import sys

import license as L
from gui import simulator_dialog as SD


def test_frozen_uses_appdata_and_creates_dir(tmp_path, monkeypatch):
    """冻结（打包）模式下数据目录应为 %APPDATA%/IEC104Parser 且已创建"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))

    d = L._get_app_dir()
    assert d == os.path.join(str(tmp_path), "IEC104Parser")
    assert os.path.isdir(d)  # 必须已创建，否则首次写入仍会失败

    d2 = SD._get_app_dir()
    assert d2 == os.path.join(str(tmp_path), "IEC104Parser")
    assert os.path.isdir(d2)


def test_frozen_falls_back_to_exe_dir_without_appdata(tmp_path, monkeypatch):
    """无 APPDATA 时回退到 exe 目录，不抛异常"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    exe = os.path.join(str(tmp_path), "app.exe")
    monkeypatch.setattr(sys, "executable", exe, raising=False)

    assert L._get_app_dir() == str(tmp_path)


def test_dev_mode_uses_script_dir(monkeypatch):
    """开发模式（未冻结）保持原脚本目录行为，不影响测试环境"""
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    root = os.path.dirname(os.path.dirname(os.path.abspath(L.__file__)))
    assert L._get_app_dir() == os.path.join(root, "iec104_parser")
