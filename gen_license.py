"""
激活码生成工具
用法: python gen_license.py <硬件指纹> [到期日期]
"""
import sys
sys.path.insert(0, '.')
from license import generate_license_code


def _normalize_expiry(raw: str):
    """把第二参数规范化为到期日期或永久"""
    raw = raw.strip()
    if not raw or raw in ("永久", "0"):
        return ""                      # 永久
    # 校验 YYYY-MM-DD 格式
    from datetime import datetime
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return None                    # 非法
    return raw


def main():
    if len(sys.argv) < 2:
        print("用法: python gen_license.py <硬件指纹> [到期日期]")
        print("示例:")
        print("  python gen_license.py ABA8BCA152697B6EB01A8F31C055FB90                # 永久授权")
        print("  python gen_license.py ABA8BCA152697B6EB01A8F31C055FB90 2026-12-31     # 有效期至 2026-12-31")
        sys.exit(1)

    fingerprint = sys.argv[1].strip().upper()
    raw_expiry = sys.argv[2] if len(sys.argv) > 2 else ""
    expiry = _normalize_expiry(raw_expiry)
    if expiry is None:
        print(f"错误: 无效的到期日期格式 '{raw_expiry}'，请使用 YYYY-MM-DD（如 2026-12-31）或留空（永久）。")
        sys.exit(1)

    code = generate_license_code(fingerprint, expiry)
    label = "永久授权" if not expiry else f"有效期至 {expiry}"
    print(f"硬件指纹: {fingerprint}")
    print(f"授权期限: {label}")
    print(f"激活码:   {code}")


if __name__ == "__main__":
    main()