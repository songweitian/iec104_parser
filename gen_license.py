"""
激活码签发工具（仅开发者使用，打包时已被 build.bat --exclude-module 排除）

私钥文件: license_ed25519.key（与本脚本同目录，已列入 .gitignore，绝不入库/进发行包）
  - 首次使用先 --gen-keys 生成密钥对
  - 私钥请另行备份（丢失则无法再签发；泄露则必须换钥并重发全部激活码）

用法:
  python gen_license.py --gen-keys [--force]        生成密钥对（默认拒绝覆盖已有私钥）
  python gen_license.py --public-key                打印客户端需内置的公钥常量
  python gen_license.py <硬件指纹> [到期日期]        签发激活码（日期缺省=永久）
示例:
  python gen_license.py ABA8BCA152697B6EB01A8F31C055FB90              # 永久授权
  python gen_license.py ABA8BCA152697B6EB01A8F31C055FB90 2026-12-31   # 有效期至 2026-12-31
"""
import os
import sys
import base64

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "license_ed25519.key")

_PERMANENT_TOKENS = {"", "0", "永久", "permanent", "PERMANENT"}


# ------------------------------------------------------------------
# 密钥管理
# ------------------------------------------------------------------

def generate_keypair(force: bool = False):
    """生成 Ed25519 密钥对。返回 (private_key, public_hex)。

    默认拒绝覆盖已有私钥文件（--force 才覆盖），防止手滑毁掉签发能力。
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey)

    if os.path.exists(KEY_FILE) and not force:
        raise SystemExit(
            f"错误: 私钥文件已存在 ({KEY_FILE})。\n"
            "为防误覆盖导致无法签发，请确认后加 --force 重新生成。\n"
            "注意: 换钥后旧激活码全部失效，需给所有用户重发。")

    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes_raw().hex().upper()

    with open(KEY_FILE, "w") as f:
        f.write(priv.private_bytes_raw().hex())
    if os.name != "nt":
        # 仅 POSIX 下收紧权限；Windows 无 POSIX 权限位，chmod 无效
        try:
            os.chmod(KEY_FILE, 0o600)
        except OSError:
            pass
    else:
        print("  提示: Windows 下无法设置 POSIX 文件权限，请确保私钥目录不被他人访问")

    print("已生成密钥对:")
    print(f"  私钥文件: {KEY_FILE}")
    print("           （已列入 .gitignore；请务必异地备份！）")
    print(f"  公钥(hex): {pub_hex}")
    print()
    print("请把上面的公钥填入 license.py 的 _LICENSE_PUBLIC_KEY_HEX 常量，")
    print("与客户端一并发布。")
    return priv, pub_hex


def load_private_key():
    """加载开发者私钥；不存在时给出可操作的错误提示"""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey)

    if not os.path.exists(KEY_FILE):
        raise SystemExit(
            f"错误: 未找到私钥文件 {KEY_FILE}\n"
            "首次使用请先执行: python gen_license.py --gen-keys")
    try:
        with open(KEY_FILE, "r") as f:
            seed = bytes.fromhex(f.read().strip())
        return Ed25519PrivateKey.from_private_bytes(seed)
    except ValueError as e:
        raise SystemExit(f"错误: 私钥文件损坏 ({e})，请用备份恢复或 --force 重新生成")


def sign_code(fingerprint: str, expiry_date: str = "") -> str:
    """用开发者私钥签发激活码。

    载荷 "<指纹>:<日期或0>" 经 Ed25519 签名后以 base64url 编码；
    限时授权在签名后追加明文日期后缀 "-YYYY-MM-DD"。
    """
    if fingerprint.upper() != fingerprint or len(fingerprint) != 32 \
            or any(c not in "0123456789ABCDEF" for c in fingerprint):
        raise SystemExit(f"错误: 硬件指纹格式无效 '{fingerprint}'"
                         "（应为 32 位大写十六进制，见激活对话框）")

    token = "" if expiry_date in _PERMANENT_TOKENS else expiry_date
    message = f"{fingerprint}:{token or '0'}".encode()

    priv = load_private_key()
    signature = priv.sign(message)
    sig_b64 = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{sig_b64}-{expiry_date}" if token else sig_b64


def show_public_key():
    """打印客户端 license.py 需内置的公钥"""
    priv = load_private_key()
    pub_hex = priv.public_key().public_bytes_raw().hex().upper()
    print(f"_LICENSE_PUBLIC_KEY_HEX = \"{pub_hex}\"")


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def _normalize_expiry(raw: str):
    """把第二参数规范化为到期日期或永久"""
    raw = raw.strip()
    if raw in _PERMANENT_TOKENS:
        return ""                      # 永久
    # 校验 YYYY-MM-DD 格式
    from datetime import datetime
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return None                    # 非法
    return raw


def main():
    argv = sys.argv[1:]

    if "--gen-keys" in argv:
        generate_keypair(force="--force" in argv)
        return
    if "--public-key" in argv:
        show_public_key()
        return

    if len(argv) < 1 or argv[0].startswith("--"):
        print(__doc__)
        sys.exit(1)

    fingerprint = argv[0].strip().upper()
    raw_expiry = argv[1] if len(argv) > 1 else ""
    expiry = _normalize_expiry(raw_expiry)
    if expiry is None:
        print(f"错误: 无效的到期日期格式 '{raw_expiry}'，请使用 YYYY-MM-DD（如 2026-12-31）或留空（永久）。")
        sys.exit(1)

    code = sign_code(fingerprint, expiry)
    label = "永久授权" if not expiry else f"有效期至 {expiry}"
    print(f"硬件指纹: {fingerprint}")
    print(f"授权期限: {label}")
    print(f"激活码:   {code}")


if __name__ == "__main__":
    main()
