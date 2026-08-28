"""
注册激活模块
硬件绑定 + Ed25519 非对称签名验证（客户端仅内置公钥，私钥只在开发者手里）

激活码格式：
    <签名86字符 base64url>[-YYYY-MM-DD]
    - 签名 = Ed25519 签发，载荷为 "<指纹>:<到期日或0>"，"0" 表示永久授权
    - 带日期后缀为限时授权；无后缀为永久授权
安全模型：
    客户端不含任何签发密钥——反编译最多拿到公钥，无法伪造激活码。
    激活码签发见 gen_license.py（需要开发者本地的 license_ed25519.key 私钥文件）。
"""

import os
import re
import sys
import hashlib
import uuid
import json
import platform
import base64
from datetime import date, datetime

# ============================================================
# Ed25519 公钥（由 gen_license.py --gen-keys 生成私钥时一并产出）。
# 注意：此处只能放公钥；私钥（license_ed25519.key）绝不放进仓库/客户端。
# ============================================================
_LICENSE_PUBLIC_KEY_HEX = "B781F29C54F887BF51DE9882573337C22DA8FD49EF2A38371DBBCE7E12DD7923"

# 载荷里的永久授权哨兵值
_PERMANENT_TOKEN = "0"

# 旧版（HMAC 对称签名）激活码格式：32 hex（4×4 带横线或无横线），可选日期后缀。
# 仅供参考换码提示用，识别宽松些（新 Ed25519 码为 86 字符 base64url，不会误判）。
# TODO(下版本): 旧激活体系已停用数月，若确认无存量用户使用旧码，
# 可移除本段（_LEGACY_CODE_RE / is_legacy_code 及其在 activate 中的换码提示）。
_LEGACY_CODE_RE = re.compile(
    r'^([0-9A-F]{4}(?:-[0-9A-F]{4}){3}|[0-9A-F]{32})(?:-\d{4}-\d{2}-\d{2})?$')


def _get_app_dir() -> str:
    """获取应用数据目录（兼容打包和开发模式）"""
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


def is_legacy_code(code: str) -> bool:
    """是否旧版（HMAC）格式激活码——已停用，只用于给出换码提示"""
    return bool(_LEGACY_CODE_RE.match(code.strip().upper()))


def _verify_signature(message: bytes, signature: bytes) -> bool:
    """Ed25519 验签。

    故意宽泛捕获所有异常失败关闭(closed-fail)：签名不匹配、公钥损坏、
    cryptography 库缺失等一律视为无效，绝不让未验证的码通过。
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey)
        pub = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(_LICENSE_PUBLIC_KEY_HEX))
        pub.verify(signature, message)
        return True
    except Exception:
        return False


def _parse_date(s: str):
    """严格解析 YYYY-MM-DD，非法返回 None"""
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _decode_signature(sig_b64: str) -> bytes:
    """base64url 解码签名（容忍用户复制丢失结尾 padding）"""
    padding = "=" * (-len(sig_b64) % 4)
    return base64.urlsafe_b64decode(sig_b64 + padding)


def _resolve_license_expiry(fingerprint: str, code: str):
    """从激活码解析到期日期并用本机指纹验签。

    返回值：
    - ""  -> 永久授权（签名有效）
    - "YYYY-MM-DD" -> 限时授权（签名有效，日期未被篡改）
    - None -> 无效激活码（格式/长度/签名不匹配）
    """
    code = "".join(code.split())          # 去掉复制粘贴可能带入的所有空白
    if not code:
        return None

    if not fingerprint:
        return None

    # 限时格式：<签名> - YYYY-MM-DD（签名 base64url 含 '-'/'_'，用尾部锚定日期）。
    # 验签失败时不立刻判死：极小概率某个永久码签名尾部长得像日期，
    # 落到下面的永久分支再试一次；篡改日期的组合在两个分支都不会通过。
    m = re.match(r'^(.+)-(\d{4}-\d{2}-\d{2})$', code)
    if m:
        sig_b64, expiry_str = m.group(1), m.group(2)
        if _parse_date(expiry_str) and sig_b64:
            message = f"{fingerprint}:{expiry_str}".encode()
            if _verify_signature(message, _decode_signature(sig_b64)):
                return expiry_str          # 日期未篡改且签名有效

    # 永久格式：<签名>（64 字节 Ed25519 签名 = 86 个 base64url 字符，
    # 容忍用户复制时带上/丢了结尾 padding；其余长度直接判无效）
    if 86 <= len(code) <= 88:
        message = f"{fingerprint}:{_PERMANENT_TOKEN}".encode()
        if _verify_signature(message, _decode_signature(code)):
            return ""                      # 永久
    return None                            # 格式无效或签名不匹配


def verify_license_code(fingerprint: str, code: str) -> bool:
    """验证激活码（签名有效即通过，含永久与限时）"""
    return _resolve_license_expiry(fingerprint, code) is not None


def save_license(fingerprint: str, code: str, holder: str = "") -> bool:
    """保存授权文件（保留原始激活码文本；注意新码区分大小写，不做 upper）"""
    try:
        data = {
            "fingerprint": fingerprint,
            "code": code.strip(),
            "holder": holder,
        }
        # 简单混淆：base64 编码
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

    if is_legacy_code(license_code):
        return False, ("检测到旧版激活码。旧版激活体系已停用，"
                       "请将本机硬件指纹发送给开发者换取新版激活码")

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
