"""
统计图表对话框 - 用 QPainter 绘制柱状图/饼图
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QWidget
)
from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QFont, QColor, QPainter, QPen, QBrush


class BarChartWidget(QWidget):
    """柱状图绘制控件"""

    def __init__(self, title: str, data: list, parent=None):
        """
        data: [(label, value, color_hex), ...]
        """
        super().__init__(parent)
        self.title = title
        self.data = data
        self.setMinimumSize(400, 300)

    def paintEvent(self, event):
        if not self.data:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        # 标题
        title_font = QFont("Microsoft YaHei", 12, QFont.Bold)
        painter.setFont(title_font)
        painter.setPen(QColor("#333"))
        painter.drawText(QRectF(0, 10, w, 30), Qt.AlignCenter, self.title)

        # 绘图区域
        margin_left = 80
        margin_right = 30
        margin_top = 50
        margin_bottom = 60
        chart_w = w - margin_left - margin_right
        chart_h = h - margin_top - margin_bottom

        if chart_w <= 0 or chart_h <= 0:
            painter.end()
            return

        max_val = max(v for _, v, _ in self.data) if self.data else 1
        if max_val == 0:
            max_val = 1

        bar_count = len(self.data)
        bar_spacing = 10
        bar_width = max(20, (chart_w - bar_spacing * (bar_count + 1)) / bar_count)

        # 绘制网格线
        painter.setPen(QPen(QColor("#E0E0E0"), 1, Qt.DashLine))
        label_font = QFont("Consolas", 8)
        painter.setFont(label_font)
        for i in range(5):
            y = margin_top + chart_h - (chart_h * i / 4)
            painter.drawLine(int(margin_left), int(y), int(w - margin_right), int(y))
            val = int(max_val * i / 4)
            painter.drawText(QRectF(0, y - 10, margin_left - 5, 20),
                             Qt.AlignRight | Qt.AlignVCenter, str(val))

        # 绘制柱状图
        for i, (label, value, color) in enumerate(self.data):
            x = margin_left + bar_spacing + i * (bar_width + bar_spacing)
            bar_h = (value / max_val) * chart_h
            y = margin_top + chart_h - bar_h

            # 柱体
            color_obj = QColor(color)
            painter.setBrush(QBrush(color_obj))
            painter.setPen(QPen(color_obj.darker(120), 1))
            painter.drawRoundedRect(QRectF(x, y, bar_width, bar_h), 3, 3)

            # 数值标签
            painter.setPen(QColor("#333"))
            val_font = QFont("Consolas", 9, QFont.Bold)
            painter.setFont(val_font)
            painter.drawText(QRectF(x, y - 20, bar_width, 20),
                             Qt.AlignCenter, str(value))

            # X轴标签
            painter.setPen(QColor("#666"))
            lbl_font = QFont("Microsoft YaHei", 8)
            painter.setFont(lbl_font)
            painter.drawText(QRectF(x - 10, margin_top + chart_h + 5,
                                    bar_width + 20, 30),
                             Qt.AlignCenter | Qt.TextWordWrap, label)

        painter.end()


class PieChartWidget(QWidget):
    """饼图绘制控件"""

    def __init__(self, title: str, data: list, parent=None):
        """
        data: [(label, value, color_hex), ...]
        """
        super().__init__(parent)
        self.title = title
        self.data = data
        self.setMinimumSize(400, 300)

    def paintEvent(self, event):
        if not self.data:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        # 标题
        title_font = QFont("Microsoft YaHei", 12, QFont.Bold)
        painter.setFont(title_font)
        painter.setPen(QColor("#333"))
        painter.drawText(QRectF(0, 10, w, 30), Qt.AlignCenter, self.title)

        total = sum(v for _, v, _ in self.data)
        if total == 0:
            painter.end()
            return

        # 饼图区域
        cx, cy = w * 0.35, h * 0.55
        radius = min(w * 0.25, h * 0.35)

        start_angle = 0
        legend_y = 80

        for label, value, color in self.data:
            if value == 0:
                continue
            span = int(360 * 16 * value / total)
            color_obj = QColor(color)
            painter.setBrush(QBrush(color_obj))
            painter.setPen(QPen(Qt.white, 2))
            painter.drawPie(int(cx - radius), int(cy - radius),
                            int(radius * 2), int(radius * 2),
                            start_angle, span)

            # 图例
            painter.setBrush(QBrush(color_obj))
            painter.setPen(Qt.NoPen)
            lx = w * 0.65
            painter.drawRect(int(lx), int(legend_y), 14, 14)
            painter.setPen(QColor("#333"))
            legend_font = QFont("Microsoft YaHei", 9)
            painter.setFont(legend_font)
            pct = value / total * 100
            painter.drawText(int(lx + 20), int(legend_y),
                             int(w - lx - 20), 14,
                             Qt.AlignLeft | Qt.AlignVCenter,
                             f"{label}: {value} ({pct:.1f}%)")
            legend_y += 22
            start_angle += span

        painter.end()


class StatsDialog(QDialog):
    """统计图表对话框"""

    def __init__(self, frames, parent=None):
        super().__init__(parent)
        self.frames = frames
        self.setWindowTitle("报文统计分析")
        self.setMinimumSize(700, 500)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        tabs = QTabWidget()

        # 信号类型分布
        sig_data = self._count_signal_types()
        if sig_data:
            tabs.addTab(PieChartWidget("信号类型分布", sig_data), "信号类型")

        # 帧类型分布
        frame_data = self._count_frame_types()
        if frame_data:
            tabs.addTab(BarChartWidget("帧类型分布", frame_data), "帧类型")

        # 传送原因分布
        cot_data = self._count_cot()
        if cot_data:
            tabs.addTab(BarChartWidget("传送原因分布 (Top 10)", cot_data[:10]), "传送原因")

        # 方向分布
        dir_data = self._count_directions()
        if dir_data:
            tabs.addTab(PieChartWidget("报文方向分布", dir_data), "方向")

        layout.addWidget(tabs)

        # 关闭按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        close_btn.setMinimumWidth(80)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

    def _count_signal_types(self) -> list:
        counts = {}
        for f in self.frames:
            if f.signal_label:
                counts[f.signal_label] = counts.get(f.signal_label, 0) + 1
        colors = {
            "遥信": "#2196F3", "遥测": "#4CAF50", "遥控": "#F44336",
            "遥调": "#FF9800", "遥脉": "#9C27B0", "其他": "#9E9E9E",
        }
        return [(k, v, colors.get(k, "#9E9E9E")) for k, v in
                sorted(counts.items(), key=lambda x: -x[1])]

    def _count_frame_types(self) -> list:
        counts = {"I帧": 0, "S帧": 0, "U帧": 0, "错误": 0}
        for f in self.frames:
            if f.error:
                counts["错误"] += 1
            elif f.apci:
                ft = f.apci.frame_type
                if ft == "I-format":
                    counts["I帧"] += 1
                elif ft == "S-format":
                    counts["S帧"] += 1
                elif ft == "U-format":
                    counts["U帧"] += 1
        colors = {"I帧": "#2196F3", "S帧": "#4CAF50", "U帧": "#FF9800", "错误": "#F44336"}
        return [(k, v, colors.get(k, "#9E9E9E")) for k, v in counts.items() if v > 0]

    def _count_cot(self) -> list:
        from iec104_parser import COT
        counts = {}
        for f in self.frames:
            if f.asdu:
                cot = f.asdu.cot_cause
                counts[cot] = counts.get(cot, 0) + 1
        result = []
        for cot, cnt in sorted(counts.items(), key=lambda x: -x[1]):
            desc = COT.get_description(cot)
            result.append((f"{cot}\n{desc}", cnt, "#42A5F5"))
        return result

    def _count_directions(self) -> list:
        counts = {}
        for f in self.frames:
            d = f.direction if f.direction else "未知"
            counts[d] = counts.get(d, 0) + 1
        colors = {"主站→子站": "#E91E63", "子站→主站": "#2196F3", "未知": "#9E9E9E"}
        return [(k, v, colors.get(k, "#9E9E9E")) for k, v in counts.items()]
