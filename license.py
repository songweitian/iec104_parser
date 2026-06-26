"""
注册激活模块
硬件绑定 + 本地验证
"""

import os
import sys
import hashlib
import hmac
import uuid
import json
import platform
import base64
from pathlib import Path

# 激活密钥（混淆存储，不可明文）
_key_parts = [
    b'SVVD',      # part1
    b'QzEwNC1Q',  # part2
    b'QVJTRVI=',  # part3
]
_secret_key = b''.join(base64.b64decode(p) for p in _key_parts)


def _get_app_dir() -> str:
    """获取应用数据目录（兼容 --onefile 和开发模式）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后，基于可执行文件路径
        return os.path.dirname(sys.executable)
    else:
        # 开发模式，基于脚本路径
        return os.path.dirname(os.path.abspath(__file__))


# 授权文件路径
LICENSE_FILE = os.path.join(_get_app_dir(), "license.dat")


def get_hardware_fingerprint() -> str:
    """生成硬件指纹"""
    mac = uuid.getnode()
    hostname = platform.node()
    disk = os.getenv("SystemDrive", "C:")

    raw = f"{mac}:{hostname}:{disk}"
    return hashlib.md5(raw.encode()).hexdigest().upper()


def generate_license_code(fingerprint: str, expiry_days: int = 0) -> str:
    """生成激活码（供开发者使用）"""
    data = f"{fingerprint}:{expiry_days}"
    code = hmac.new(_secret_key, data.encode(), hashlib.sha256).hexdigest()[:32].upper()
    return f"{code[:8]}-{code[8:16]}-{code[16:24]}-{code[24:32]}"


def verify_license_code(fingerprint: str, code: str) -> bool:
    """验证激活码"""
    # 格式化为无横线格式
    code_clean = code.replace("-", "").upper()
    if len(code_clean) != 32:
        return False

    # 尝试验证（ expiry_days=0 表示永久）
    for days in [0, 365, 730, 1095]:
        data = f"{fingerprint}:{days}"
        expected = hmac.new(_secret_key, data.encode(), hashlib.sha256).hexdigest()[:32].upper()
        if code_clean == expected:
            return True

    return False


def save_license(fingerprint: str, code: str, holder: str = "") -> bool:
    """保存授权文件"""
    try:
        data = {
            "fingerprint": fingerprint,
            "code": code.replace("-", "").upper(),
            "holder": holder,
        }
        # 简单混淆：base64 编码
        import base64
        encoded = base64.b64encode(json.dumps(data).encode()).decode()
        with open(LICENSE_FILE, "w", encoding="utf-8") as f:
            f.write(encoded)
        return True
    except Exception:
        return False


def load_license():
    """加载授权文件"""
    try:
        if not os.path.exists(LICENSE_FILE):
            return None
        with open(LICENSE_FILE, "r", encoding="utf-8") as f:
            encoded = f.read().strip()
        import base64
        data = json.loads(base64.b64decode(encoded))
        # 验证当前硬件指纹
        current_fp = get_hardware_fingerprint()
        if data.get("fingerprint") != current_fp:
            return None
        return data
    except Exception:
        return None


def remove_license() -> bool:
    """删除授权文件，取消激活"""
    try:
        if os.path.exists(LICENSE_FILE):
            os.remove(LICENSE_FILE)
        return True
    except Exception:
        return False


def is_activated() -> bool:
    """检查是否已激活"""
    return load_license() is not None


def activate(license_code: str, holder: str = ""):
    """执行激活"""
    fingerprint = get_hardware_fingerprint()
    if verify_license_code(fingerprint, license_code):
        if save_license(fingerprint, license_code, holder):
            return True, "激活成功！"
        return False, "保存授权文件失败"
    return False, "激活码无效或硬件不匹配"


def get_license_info() -> dict:
    """获取授权信息"""
    data = load_license()
    if data:
        return {"activated": True, "holder": data.get("holder", ""), "fingerprint": data.get("fingerprint", "")}
    return {"activated": False, "fingerprint": get_hardware_fingerprint()}
