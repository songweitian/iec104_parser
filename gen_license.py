"""
激活码生成工具
用法: python gen_license.py <硬件指纹>
"""

import sys
sys.path.insert(0, '.')
from license import generate_license_code

if len(sys.argv) < 2:
    print("用法: python gen_license.py <硬件指纹>")
    print("示例: python gen_license.py ABA8BCA152697B6EB01A8F31C055FB90")
    sys.exit(1)

fingerprint = sys.argv[1].strip().upper()
code = generate_license_code(fingerprint)
print(f"硬件指纹: {fingerprint}")
print(f"激活码:   {code}")
