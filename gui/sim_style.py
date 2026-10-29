"""
模拟器界面的颜色/按钮样式常量（从 simulator_dialog.py 拆出）

纯数据（只有 QColor 与样式字符串），供 simulator_dialog（连接/SBO/日志）
与 sim_point_tables（四遥表格）共用——两个模块都要用这些颜色，放在任一方
都会造成循环 import。
"""

from PyQt5.QtGui import QColor

# 样式常量
INVALID_COLOR = QColor("#AAAAAA")
YX_ON_COLOR = QColor("#4CAF50")
YX_OFF_COLOR = QColor("#F44336")
GRAY_BG = QColor("#F5F5F5")
BLUE_LIGHT = QColor("#E3F2FD")
PURPLE_LIGHT = QColor("#F3E5F5")
# 深色主题下的收发日志行背景（暗色系，避免浅色刺眼）
BLUE_DARK = QColor("#1A3650")
PURPLE_DARK = QColor("#3A2A4A")
# 错误日志行背景（浅/深主题）
RED_LIGHT = QColor("#FDE7E7")
RED_DARK = QColor("#4A2222")
ERROR_FG_LIGHT = QColor("#C62828")
ERROR_FG_DARK = QColor("#EF9A9A")
LOCAL_FG = QColor("#9E9E9E")

# 启动/停止按钮点亮样式：当前状态对应的按钮高亮，另一按钮灰暗
START_BTN_ACTIVE = (
    "QPushButton { background-color: #4CAF50; color: white; font-weight: bold; "
    "padding: 5px 16px; border-radius: 3px; border: none; }"
    "QPushButton:hover { background-color: #388E3C; }"
)
START_BTN_INACTIVE = (
    "QPushButton { background-color: #9E9E9E; color: #E0E0E0; font-weight: bold; "
    "padding: 5px 16px; border-radius: 3px; border: none; }"
)
STOP_BTN_ACTIVE = (
    "QPushButton { background-color: #f44336; color: white; font-weight: bold; "
    "padding: 5px 16px; border-radius: 3px; border: none; }"
    "QPushButton:hover { background-color: #c62828; }"
)
STOP_BTN_INACTIVE = (
    "QPushButton { background-color: #9E9E9E; color: #E0E0E0; font-weight: bold; "
    "padding: 5px 16px; border-radius: 3px; border: none; }"
)

# 遥控/遥调：合(绿)/分(红) 采用电力行业"合位绿、分位红"语义；
# 预制按钮在"已预制(armed)"时转绿并加粗，与初始橙色明确区分
YK_ON_COLOR = "#4CAF50"
YK_OFF_COLOR = "#F44336"   # 分/跳闸，区别于禁用灰(#9E9E9E)避免误读
SEL_BTN_NORMAL = (
    "QPushButton { background-color: #FF9800; color: white; padding: 3px 10px; "
    "border-radius: 3px; border: none; }"
    "QPushButton:hover { background-color: #F57C00; }"
)
SEL_BTN_ARMED = (
    "QPushButton { background-color: #4CAF50; color: white; font-weight: bold; "
    "padding: 3px 10px; border-radius: 3px; border: none; }"
    "QPushButton:hover { background-color: #388E3C; }"
)
# 遥控行按钮：预制阶段(预制合/预制分)与执行阶段(执行合/执行分)同色系，
# 执行态加粗描边以示"已解锁可下发"；合绿/分红语义保持
YK_BTN_ON_NORMAL = (
    "QPushButton { background-color: #4CAF50; color: white; padding: 3px 10px; "
    "border-radius: 3px; border: none; }"
    "QPushButton:hover { background-color: #388E3C; }"
)
YK_BTN_ON_ARMED = (
    "QPushButton { background-color: #4CAF50; color: white; font-weight: bold; "
    "padding: 3px 10px; border-radius: 3px; border: 2px solid #1B5E20; }"
    "QPushButton:hover { background-color: #388E3C; }"
)
YK_BTN_OFF_NORMAL = (
    "QPushButton { background-color: #F44336; color: white; padding: 3px 10px; "
    "border-radius: 3px; border: none; }"
    "QPushButton:hover { background-color: #C62828; }"
)
YK_BTN_OFF_ARMED = (
    "QPushButton { background-color: #F44336; color: white; font-weight: bold; "
    "padding: 3px 10px; border-radius: 3px; border: 2px solid #B71C1C; }"
    "QPushButton:hover { background-color: #C62828; }"
)
