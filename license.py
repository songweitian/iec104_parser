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
from datetime import date, datetime, timedelta
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


# 永久授权哨兵值（传给 generate_license_code 的 expiry_date）
_PERMANENT_TOKENS = {"", "0", "永久", "permanent", "PERMANENT"}


def generate_license_code(fingerprint: str, expiry_date: str = "") -> str:
    """生成激活码（供开发者使用）

    永久授权：expiry_date 为空/0/永久，载荷为 fingerprint:0，格式 XXXX-XXXX-XXXX-XXXX（32hex）。
    限时授权：expiry_date 为 YYYY-MM-DD，载荷为 fingerprint:YYYY-MM-DD，
              格式 XXXX-XXXX-XXXX-XXXX-YYYY-MM-DD（日期后缀随签名一同防伪）。
    """
    token = "0" if expiry_date in _PERMANENT_TOKENS else expiry_date
    code = hmac.new(_secret_key, f"{fingerprint}:{token}".encode(),
                    hashlib.sha256).hexdigest()[:32].upper()
    if expiry_date in _PERMANENT_TOKENS:
        return f"{code[:8]}-{code[8:16]}-{code[16:24]}-{code[24:32]}"
    return f"{code[:8]}-{code[8:16]}-{code[16:24]}-{code[24:32]}-{expiry_date}"


def _parse_date(s: str):
    """严格解析 YYYY-MM-DD，非法返回 None"""
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _resolve_license_expiry(fingerprint: str, code: str):
    """从激活码解析到期日期。

    返回值：
    - ""  -> 永久授权（32hex 永久码，签名有效）
    - "YYYY-MM-DD" -> 限时授权（签名有效，日期未篡改，含日期后缀）
    - None -> 无效激活码（长度/格式/签名不匹配）

    注意：code 为原始输入（允许带横线），日期分隔符会被保留识别；
    永久 32hex 是否带内部横线均可。
    """
    code = code.strip().upper()
    # 限时格式：<32hex 可带横线> + '-YYYY-MM-DD'
    if len(code) > 32 and code[-11] == "-":
        date_part = code[-10:]
        if _parse_date(date_part):
            hex_part = code[:-11].replace("-", "")
            if len(hex_part) == 32:
                sig = hmac.new(_secret_key, f"{fingerprint}:{date_part}".encode(),
                               hashlib.sha256).hexdigest()[:32].upper()
                if sig == hex_part:
                    return date_part          # 日期未篡改
                return None                   # 签名不匹配
    # 永久格式：32hex（可带内部横线）
    hex_only = code.replace("-", "")
    if len(hex_only) == 32:
        sig = hmac.new(_secret_key, f"{fingerprint}:0".encode(),
                       hashlib.sha256).hexdigest()[:32].upper()
        if sig == hex_only:
            return ""                         # 永久
    return None


def verify_license_code(fingerprint: str, code: str) -> bool:
    """验证激活码（签名有效即通过，含永久与限时）"""
    return _resolve_license_expiry(fingerprint, code) is not None


def save_license(fingerprint: str, code: str, holder: str = "") -> bool:
    """保存授权文件（保留原始激活码文本，含日期后缀分隔符）"""
    try:
        data = {
            "fingerprint": fingerprint,
            "code": code.strip().upper(),
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


def is_license_expired() -> bool:
    """授权是否已到期（存在授权文件但已过期）"""
    return get_license_status()["expired"]


def get_license_status() -> dict:
    """获取完整授权状态（所有解锁/判定以它为准）

    返回字段：activated / expired / permanent / expiry_date / days_remaining / holder / fingerprint
    """
    data = load_license()
    if not data:
        return {
            "activated": False, "expired": False, "permanent": False,
            "expiry_date": "", "days_remaining": None,
            "holder": "", "fingerprint": get_hardware_fingerprint(),
        }
    fp = data.get("fingerprint", "")
    expiry = _resolve_license_expiry(fp, data.get("code", ""))

    # 无效激活码（签名/格式不匹配）：视为未激活
    if expiry is None:
        return {
            "activated": False, "expired": False, "permanent": False,
            "expiry_date": "", "days_remaining": None,
            "holder": data.get("holder", ""), "fingerprint": fp,
        }

    # 永久授权
    if not expiry:
        return {
            "activated": True, "expired": False, "permanent": True,
            "expiry_date": "", "days_remaining": None,
            "holder": data.get("holder", ""), "fingerprint": fp,
        }

    exp_date = _parse_date(expiry)
    if exp_date is None:  # 日期无法解析（理论上已在上层过滤，防御）
        return {
            "activated": False, "expired": True, "permanent": False,
            "expiry_date": expiry, "days_remaining": 0,
            "holder": data.get("holder", ""), "fingerprint": fp,
        }

    today = date.today()
    expired = today > exp_date
    days_remaining = (exp_date - today).days  # 可为负数（已过期）
    return {
        "activated": not expired, "expired": expired, "permanent": False,
        "expiry_date": expiry, "days_remaining": max(days_remaining, 0),
        "holder": data.get("holder", ""), "fingerprint": fp,
    }


def is_activated() -> bool:
    """检查是否已激活（过期视为未激活）"""
    return get_license_status()["activated"]


def activate(license_code: str, holder: str = ""):
    """执行激活"""
    fingerprint = get_hardware_fingerprint()
    if verify_license_code(fingerprint, license_code):
        # 解析出到期日期，若已过期则拒绝（过期即拒，必须续期码）
        expiry = _resolve_license_expiry(fingerprint, license_code)
        if expiry and _parse_date(expiry) and date.today() > _parse_date(expiry):
            return False, "该激活码已过期，请联系开发者续期"
        if save_license(fingerprint, license_code, holder):
            label = "永久授权" if not expiry else f"有效期至 {expiry}"
            return True, f"激活成功！{label}"
        return False, "保存授权文件失败"
    return False, "激活码无效或硬件不匹配"


def get_license_info() -> dict:
    """获取授权信息"""
    return get_license_status()
