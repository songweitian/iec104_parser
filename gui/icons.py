"""程序图标绘制函数（从 gui/main_window.py 拆分）。纯 Qt 绘制，不依赖窗口。"""
from PyQt5.QtCore import Qt
from PyQt5.QtGui import (QColor, QFont, QIcon, QPainter, QPen, QPixmap,
                         QLinearGradient, QBrush)

_ICON_PRIMARY = QColor("#1565C0")


def create_app_icon(size: int = 128) -> QIcon:
    """应用图标：蓝渐变圆角底 + 白色内描边 + "104"字样。

    模块级函数，供 main.py 设置 QApplication 全局图标；
    size 指定渲染边长，打包脚本传 256 生成多尺寸 ico。
    """
    s = size / 128.0
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    # 渐变圆角底
    grad = QLinearGradient(0, 0, 0, size)
    grad.setColorAt(0.0, QColor("#1E88E5"))
    grad.setColorAt(1.0, QColor("#0D47A1"))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(round(4*s), round(4*s), round(120*s), round(120*s), round(28*s), round(28*s))

    # 半透明白色内描边
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 70), max(1.0, 3*s)))
    p.drawRoundedRect(round(11*s), round(11*s), round(106*s), round(106*s), round(23*s), round(23*s))

    # 顶部高光带
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 18))
    p.drawRoundedRect(round(8*s), round(8*s), round(112*s), round(46*s), round(22*s), round(22*s))

    # 主字样（像素字号，保证不同尺寸下占比一致）
    p.setPen(QPen(Qt.white))
    font = QFont("Arial")
    font.setPixelSize(round(size * 0.36))
    font.setBold(True)
    p.setFont(font)
    p.drawText(pm.rect().adjusted(0, round(4*s), 0, round(4*s)), Qt.AlignCenter, "104")

    p.end()
    return QIcon(pm)


def _doc_icon(tag: str, tag_color: str) -> QIcon:
    """文档轮廓 + 彩色格式标签的小图标（CSV/JSON/TXT 工具栏用）"""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    p.setPen(QPen(_ICON_PRIMARY, 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.white)
    p.drawRoundedRect(18, 8, 30, 48, 5, 5)
    # 右上折角
    p.drawLine(38, 8, 38, 18)
    p.drawLine(38, 18, 48, 18)

    # 格式标签文字
    p.setFont(QFont("Arial", 13, QFont.Bold))
    p.setPen(QPen(QColor(tag_color)))
    p.drawText(pm.rect(), Qt.AlignCenter | Qt.AlignBottom, tag)

    p.end()
    return QIcon(pm)
