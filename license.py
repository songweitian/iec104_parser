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
import logging
import hashlib
import uuid
import json
import platform
import base64
from datetime import date, datetime, timedelta
from typing import Optional

from app_paths import get_app_dir

logger = logging.getLogger(__name__)

# ============================================================
# Ed25519 公钥（由 gen_license.py --gen-keys 生成私钥时一并产出）。
# 注意：此处只能放公钥；私钥（license_ed25519.key）绝不放进仓库/客户端。
# ============================================================
_LICENSE_PUBLIC_KEY_HEX = "B781F29C54F887BF51DE9882573337C22DA8FD49EF2A38371DBBCE7E12DD7923"

# 载荷里的永久授权哨兵值
_PERMANENT_TOKEN = "0"

# 授权文件格式版本。升级到本机指纹验签后，旧格式授权文件（无论是否仍在
# 有效期内）一律作废，用户需重新激活一次——旧格式里的 fingerprint /
# components 都未签名、可任意编辑，继续兼容等于保留绕过口子。
_LICENSE_SCHEMA = 2

# 旧版（HMAC 对称签名）激活码格式：32 hex（4×4 带横线或无横线），可选日期后缀。
# 仅供参考换码提示用，识别宽松些（新 Ed25519 码为 86 字符 base64url，不会误判）。
# TODO(下版本): 旧激活体系已停用数月，若确认无存量用户使用旧码，
# 可移除本段（_LEGACY_CODE_RE / is_legacy_code 及其在 activate 中的换码提示）。
_LEGACY_CODE_RE = re.compile(
    r'^([0-9A-F]{4}(?:-[0-9A-F]{4}){3}|[0-9A-F]{32})(?:-\d{4}-\d{2}-\d{2})?$')


def _get_app_dir() -> str:
    """获取应用数据目录（统一实现见 app_paths.get_app_dir）"""
    return get_app_dir()


# 授权文件路径
LICENSE_FILE = os.path.join(_get_app_dir(), "license.dat")

# 时钟回拨防护：记录"见过的最大日期"，到期判定取 max(系统日期, 历史水位)。
# 水位双写（授权文件 last_seen + 独立状态文件，内容倒序混淆），
# 两处同时删除/篡改才可能重置，抬高 casual 回拨的成本。
_HWM_FILE = os.path.join(_get_app_dir(), ".lic_hwm")


def get_hardware_fingerprint() -> str:
    """生成硬件指纹（签发/验签绑定的标识）

    注意：算法不可变更——已签发激活码的签名绑定该指纹值。
    单一指纹的漂移问题（Win11 随机化 MAC、改主机名导致已激活用户失效）
    由 get_hardware_components 的组件级容忍兜底，见 load_license。
    """
    mac = uuid.getnode()
    hostname = platform.node()
    disk = os.getenv("SystemDrive", "C:")

    raw = f"{mac}:{hostname}:{disk}"
    return hashlib.md5(raw.encode()).hexdigest().upper()


def _windows_machine_guid() -> str:
    """Windows 安装唯一 ID（HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid）

    随系统安装生成，改硬件/改名都不变，重装系统才更换——组件中最稳定的一个。
    非 Windows 或读取失败返回空（参与匹配时跳过）。
    """
    if sys.platform != "win32":
        return ""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography") as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
        return str(value)
    except Exception:
        return ""


def _hash_component(value: str) -> str:
    """组件哈希：授权文件里不存机器信息明文"""
    if not value:
        return ""
    return hashlib.sha256(value.encode()).hexdigest()[:32].upper()


def get_hardware_components() -> dict:
    """采集本机硬件组件（哈希后），供组件级绑定校验"""
    return {
        "guid": _hash_component(_windows_machine_guid()),
        "mac": _hash_component(str(uuid.getnode())),
        "host": _hash_component(platform.node()),
    }


def _machine_matches(stored_components) -> bool:
    """当前机器是否为激活时的同一台。

    guid 是三者中最难篡改的一项（Windows 注册表 MachineGuid，改硬件、
    改主机名都不变，重装系统才换），因此 **必须命中**；mac/host 只需再
    中一项，用于容忍随机化 MAC 或改主机名这类单点漂移。

    原实现为"≥2/3 任意组件一致"——攻击者改一下网卡 MAC + 主机名即可让
    同一份授权在第二台机器通过，门槛过低。
    """
    if not isinstance(stored_components, dict):
        return False
    cur = get_hardware_components()

    if cur.get("guid") and stored_components.get("guid"):
        if cur["guid"] != stored_components["guid"]:
            return False
        return sum(1 for k in ("mac", "host")
                   if cur.get(k) and stored_components.get(k) == cur[k]) >= 1

    # 无 guid（非 Windows / 读取失败）：退化到"≥2 项真实命中"。
    # 注意不能用固定阈值 2——可用组件不足时会导致授权永久失效。
    available = [k for k, v in cur.items() if v]
    need = min(2, len(available)) if available else 1
    matched = sum(1 for k, v in cur.items() if v and stored_components.get(k) == v)
    return matched >= need


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
    """base64url 解码签名（容忍用户复制丢失结尾 padding）

    失败返回 b""（验签必然失败 → 判为无效，fail-closed）。
    此前直接抛出 binascii.Error，而调用点在 _verify_signature 的 try
    **之外**，异常会穿透到 Qt 槽外——项目未安装 sys.excepthook，PyQt5
    默认 qFatal 终止进程，一个被篡改的 license.dat 即可让程序闪退。
    """
    try:
        padding = "=" * (-len(sig_b64) % 4)
        return base64.urlsafe_b64decode(sig_b64 + padding)
    except Exception:
        return b""


def _resolve_license_expiry(fingerprint: str, code: str):
    """从激活码解析到期日期并用本机指纹验签。

    返回值：
    - ""  -> 永久授权（签名有效）
    - "YYYY-MM-DD" -> 限时授权（签名有效，日期未被篡改）
    - None -> 无效激活码（格式/长度/签名不匹配）
    """
    # 授权文件可被手工编辑：code 为 null/数字时 "".join(None.split()) 会抛
    # AttributeError，而调用链上没有 try → 启动即闪退（fail-closed 应先判类型）
    if not isinstance(code, str):
        return None
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
    """保存授权文件（保留原始激活码文本；注意新码区分大小写，不做 upper）

    同时记录激活时的硬件组件快照，供 load_license 做组件级漂移容忍。
    """
    try:
        data = {
            "schema": _LICENSE_SCHEMA,
            "fingerprint": fingerprint,
            "code": code.strip(),
            "holder": holder,
            "components": get_hardware_components(),
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
        # 旧格式授权文件一律作废（需重新激活）：_LICENSE_SCHEMA 说明
        if not isinstance(data, dict) or data.get("schema") != _LICENSE_SCHEMA:
            return None
        # 绑定校验：优先精确指纹；不匹配时按组件容忍单点漂移
        # （随机化 MAC / 改主机名）
        current_fp = get_hardware_fingerprint()
        if data.get("fingerprint") != current_fp:
            if not _machine_matches(data.get("components")):
                return None
            # 自愈：回写当前组件快照，避免多次漂移后累积失效。
            # 注意不更新 fingerprint——激活码签名绑定的是激活时的指纹值。
            try:
                data["components"] = get_hardware_components()
                with open(LICENSE_FILE, "w", encoding="utf-8") as f:
                    f.write(base64.b64encode(json.dumps(data).encode()).decode())
            except Exception:
                pass
        return data
    except Exception:
        return None


def _read_hwm_file():
    """读取独立水位文件（内容倒序混淆）"""
    try:
        with open(_HWM_FILE, "r", encoding="utf-8") as f:
            return _parse_date(f.read().strip()[::-1])
    except Exception:
        return None


def _write_hwm_file(d: date):
    try:
        with open(_HWM_FILE, "w", encoding="utf-8") as f:
            f.write(d.isoformat()[::-1])
    except Exception:
        pass


# 水位超前系统日期超过该天数时视为脏数据（时钟曾跳到未来的遗留）
_MAX_HWM_AHEAD_DAYS = 366


def _effective_today(data) -> date:
    """有效当前日期 = max(系统日期, 各处记录的历史水位)——时钟只能前进

    但水位明显超前系统日期（>1 年）时忽略：主板电池掉电、手工把时间调到
    2099 年再改回来，都会把未来日期写进水位，此后 max() 恒为该日期，
    授权永久判定过期且无法恢复。
    """
    today = date.today()
    candidates = [today]

    def accept(d) -> bool:
        if d and d > today + timedelta(days=_MAX_HWM_AHEAD_DAYS):
            logger.warning(
                "时钟水位 %s 明显超前系统日期 %s，已忽略（疑似系统时间异常遗留）",
                d, today)
            return False
        return bool(d)

    if isinstance(data, dict):
        stored = data.get("last_seen")
        d = _parse_date(stored) if isinstance(stored, str) else None
        if accept(d):
            candidates.append(d)
    d = _read_hwm_file()
    if accept(d):
        candidates.append(d)
    return max(candidates)


def _remember_today(data: dict, today: date, exp_date: Optional[date] = None):
    """推进水位（尽力而为：只读目录/文件损坏不影响判定本身）

    已过授权到期日时不再推进：水位只服务于"防回拨到期判定"，写到到期日之后
    没有任何额外价值，却会让一次错误的前跳时间把授权永久锁死。
    """
    if exp_date is not None and today > exp_date:
        return
    stored = data.get("last_seen")
    if not isinstance(stored, str) or _parse_date(stored) != today:
        data["last_seen"] = today.isoformat()
        try:
            with open(LICENSE_FILE, "w", encoding="utf-8") as f:
                f.write(base64.b64encode(json.dumps(data).encode()).decode())
        except Exception:
            pass
    _write_hwm_file(today)


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

    返回字段：activated / expired / permanent / expiry_date / days_remaining /
    holder / fingerprint / needs_reactivation

    needs_reactivation：存在授权文件但已不可用（旧格式待升级、硬件漂移、
    激活码无效），UI 可据提示"请重新激活"，区别于从未激活。
    """
    data = load_license()
    if not data:
        return {
            "activated": False, "expired": False, "permanent": False,
            "expiry_date": "", "days_remaining": None,
            "holder": "", "fingerprint": get_hardware_fingerprint(),
            "needs_reactivation": os.path.exists(LICENSE_FILE),
        }
    fp_current = get_hardware_fingerprint()
    # 验签只能用**本机**指纹。授权文件里的 fingerprint / components 都未
    # 签名、可任意编辑：此前直接用文件里的 fingerprint 验签，整机拷贝后
    # 只需把 components 换成目标机器的组件值就能通过机器绑定。
    expiry = _resolve_license_expiry(fp_current, data.get("code", ""))

    # 本机指纹已漂移（改主机名 / 换网卡），但组件仍高度一致 → 视作同一台
    # 机器，只是需要重新激活（激活码绑定的是原指纹，无法在本地安全迁移，
    # 否则等于给整机拷贝开后门）。
    needs_reactivation = (expiry is None
                          and bool(data.get("fingerprint"))
                          and data.get("fingerprint") != fp_current
                          and _machine_matches(data.get("components")))

    # 无效激活码（签名/格式不匹配）：视为未激活
    if expiry is None:
        return {
            "activated": False, "expired": False, "permanent": False,
            "expiry_date": "", "days_remaining": None,
            "holder": data.get("holder", ""), "fingerprint": fp_current,
            "needs_reactivation": needs_reactivation,
        }

    # 永久授权
    if not expiry:
        return {
            "activated": True, "expired": False, "permanent": True,
            "expiry_date": "", "days_remaining": None,
            "holder": data.get("holder", ""), "fingerprint": fp_current,
            "needs_reactivation": False,
        }

    exp_date = _parse_date(expiry)
    if exp_date is None:  # 日期无法解析（理论上已在上层过滤，防御）
        return {
            "activated": False, "expired": True, "permanent": False,
            "expiry_date": expiry, "days_remaining": 0,
            "holder": data.get("holder", ""), "fingerprint": fp_current,
            "needs_reactivation": False,
        }

    # 时钟回拨防护：以历史水位与系统日期的较大者参与到期判定
    today = _effective_today(data)
    expired = today > exp_date
    days_remaining = (exp_date - today).days  # 可为负数（已过期）
    _remember_today(data, today, exp_date)
    return {
        "activated": not expired, "expired": expired, "permanent": False,
        "expiry_date": expiry, "days_remaining": max(days_remaining, 0),
        "holder": data.get("holder", ""), "fingerprint": fp_current,
        "needs_reactivation": False,
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
        expiry = _resolve_license_expiry(fingerprint, license_code)
        exp_date = _parse_date(expiry) if expiry else None
        # 与 get_license_status 同口径：用"防回拨水位"日期判定，而非裸
        # date.today()。此前拨回系统时间即可激活一个早已过期的码，还会
        # 用拨回的日期播种水位，把防回拨一起废掉。
        today = _effective_today(load_license())
        if exp_date and today > exp_date:
            return False, "该激活码已过期，请联系开发者续期"
        if save_license(fingerprint, license_code, holder):
            # 激活即播种时钟水位，防止激活后把系统时钟拨回首次运行前
            seeded = load_license()
            if seeded:
                _remember_today(seeded, today, exp_date)
            label = "永久授权" if not exp_date else f"有效期至 {expiry}"
            return True, f"激活成功！{label}"
        return False, "保存授权文件失败"
    return False, "激活码无效或硬件不匹配"


def get_license_info() -> dict:
    """获取授权信息"""
    return get_license_status()
