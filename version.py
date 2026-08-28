"""应用版本信息统一管理（L5 重构）。

所有模块从这里导入版本号/应用元数据，发版时仅需修改本文件。
"""

# 版本号（每次发版同步更新）
APP_VERSION = "1.1.0"
# 兼容别名（gui.about_dialog 等既有引用）
VERSION = APP_VERSION

# 发布日期（与每次正式发版同步更新）
BUILD_DATE = "2026-08-28"

# 应用元数据
APP_NAME = "IEC104 Parser"
APP_TITLE = "IEC60870-5-104 报文解析器"
APP_ORG = "SWT"

# 作者信息
AUTHOR = "宋伟田"
EMAIL = "3028905947@qq.com"
