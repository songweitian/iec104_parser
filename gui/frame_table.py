"""
报文列表视图层：虚拟表格行模型 + 单元格渲染

从 main_window.py 拆出（主窗口原本 2800+ 行，UI 布局/业务动作/表格视图
三层揉在一起）。本模块只负责：

    all_frames（全量） → _view_order（当前可见行） → 单元格文本/颜色

不涉及解析、导出、授权等业务动作。

约定：
- all_frames 恒为全量；_view_order 描述过滤/搜索/排序后"可见的帧索引"；
- 行号 ↔ 帧下标一律走 _frame_idx_at_row()/_row_of_frame()，
  禁止用行号直接索引 all_frames（过滤后二者不相等）。
"""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFontMetrics
from PyQt5.QtWidgets import QStyledItemDelegate, QStyle

from iec104_parser import IEC104Frame, SignalType, TypeID, COT
from gui.ioa_alias_dialog import get_alias

# 过滤按钮样式：空闲蓝色 / 生效中橙色（让用户一眼看出过滤还在起作用）
FILTER_BTN_STYLE_IDLE = (
    "QPushButton { background-color: #2196F3; color: white; font-weight: bold; "
    "padding: 3px 12px; border-radius: 3px; border: none; }"
    "QPushButton:hover { background-color: #1976D2; }"
)
FILTER_BTN_STYLE_ACTIVE = (
    "QPushButton { background-color: #FF9800; color: white; font-weight: bold; "
    "padding: 3px 12px; border-radius: 3px; border: none; }"
    "QPushButton:hover { background-color: #F57C00; }"
)


class FrameCellDelegate(QStyledItemDelegate):
    """报文列表虚拟绘制器。

    不再为每条/每列创建 QTableWidgetItem，而是在每个单元格绘制时实时从
    MainWindow.all_frames 与 _view_order（视图行→帧索引）取值。
    万帧级数据下填充开销从 O(N×12) 次 item 构造降至 O(1)，仅重绘可见区。
    """

    def __init__(self, window, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._w = window

    def paint(self, painter, option, index):
        w = self._w
        if w is None or not w.all_frames or not w._view_order:
            return super().paint(painter, option, index)
        vi = index.row()
        if not (0 <= vi < len(w._view_order)):
            return super().paint(painter, option, index)
        fi = w._view_order[vi]
        if not (0 <= fi < len(w.all_frames)):
            return super().paint(painter, option, index)

        cell = w._cell(vi, index.column())
        painter.save()
        rect = option.rect
        # 选中判定：优先查 selectionModel。部分环境下 hover 重绘时
        # option.state 的 Selected 位不稳定，会导致选中行配色错乱、文字看不清。
        selected = bool(option.state & QStyle.State_Selected)
        try:
            sm = w.table_widget.selectionModel()
            if sm is not None:
                selected = sm.isSelected(index)
        except Exception:
            pass
        if selected:
            # 选中行：固定深蓝背景 + 白色文字（不依赖系统高亮色，保证可读性）
            painter.fillRect(rect, QColor("#2196F3"))
            fg = QColor("#FFFFFF")
        else:
            painter.fillRect(rect, cell["bg"])
            fg = cell.get("fg") or (
                QColor("#E0E0E0") if w._dark_theme else QColor("#000000"))
        painter.setPen(fg)
        font = painter.font()
        font.setBold(cell.get("bold", False))
        painter.setFont(font)
        painter.drawText(rect.adjusted(4, 0, -4, 0),
                         int(cell.get("align", Qt.AlignLeft | Qt.AlignVCenter)),
                         cell["text"])
        painter.restore()

    def sizeHint(self, option, index):
        # 沿用默认行高；列宽由“估算/用户拖拽”决定，无需按内容精确量宽
        return super().sizeHint(option, index)


class FrameTableMixin:
    """主窗口的表格视图部分（依赖宿主提供 table_widget/all_frames 等属性）"""

    @staticmethod
    def _timestamp_to_sort_key(timestamp: str) -> float:
        """将时间戳字符串转为数值（毫秒），用于排序"""
        if not timestamp:
            return -1
        try:
            parts = timestamp.replace(',', '.').split(':')
            if len(parts) == 3:
                h, m = int(parts[0]), int(parts[1])
                s = float(parts[2])
                return h * 3600000 + m * 60000 + s * 1000
        except (ValueError, IndexError):
            pass
        return -1

    def _row_color_for_frame(self, frame: IEC104Frame) -> QColor:
        """根据帧类型和当前主题返回行背景色"""
        if self._dark_theme:
            if frame.error:
                return QColor("#3D2020")  # 错误: 暗红
            if not frame.apci:
                return QColor("#2b2b2b")
            ftype = frame.apci.frame_type
            if ftype == "I-format":
                if frame.signal_type in (SignalType.YK, SignalType.YT):
                    return QColor("#3D3520")  # 控制: 暗橙
                return QColor("#20283D")  # 数据: 暗蓝
            elif ftype == "S-format":
                return QColor("#203D20")  # 确认: 暗绿
            elif ftype == "U-format":
                return QColor("#3D2035")  # 控制: 暗粉
            return QColor("#2b2b2b")
        else:
            if frame.error:
                return QColor("#FFF0F0")  # 错误: 浅红
            if not frame.apci:
                return QColor("#FFFFFF")
            ftype = frame.apci.frame_type
            if ftype == "I-format":
                if frame.signal_type in (SignalType.YK, SignalType.YT):
                    return QColor("#FFF8E1")  # 控制: 浅橙
                return QColor("#F5FAFF")  # 数据: 浅蓝
            elif ftype == "S-format":
                return QColor("#F5FFF5")  # 确认: 浅绿
            elif ftype == "U-format":
                return QColor("#FFF5FA")  # 控制: 浅粉
            return QColor("#FFFFFF")

    def _populate_table(self):
        """填充报文列表表格 - 虚拟化：不创建单元格对象，仅更新映射与行数。

        由 FrameCellDelegate 在绘制时实时调用 _cell() 取内容。
        万帧级数据填充开销从 O(N×12) 降为 O(1)，仅重绘可见区域。
        注：all_frames 恒为全量，_view_order 只描述“当前可见行”（过滤/搜索/
        排序后仍可见的帧索引）——因此排序/搜索/点击/导出都经 _frame_idx_at_row 映射。
        """
        n = len(self.all_frames)
        # 数据源(重新解析/换标签页)改变后，回到默认序号序并清空搜索快照
        self._set_view(list(range(n)))
        self._sort_column = -1
        self.table_widget.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        # 视图已恢复全量：过滤标志必须同步复位，否则状态栏/tooltip 仍报
        # "过滤生效中"（列表却是全量），且下次点重置会退化成另一套状态
        self._reset_filter_state()

        if not self.all_frames:
            self.table_widget.viewport().update()
            return

        # 调整列宽：优先套用用户上次的列宽（同时避免大表全量自适应卡顿），
        # 没有则按前 300 行采样估算
        if self._saved_col_widths:
            for c, wd in enumerate(self._saved_col_widths):
                if c < self.table_widget.columnCount() and wd >= 20:
                    self.table_widget.setColumnWidth(c, wd)
        else:
            for c, wd in enumerate(self._estimate_col_widths()):
                self.table_widget.setColumnWidth(c, wd)

        # 自动选中第一条报文
        self._current_frame_idx = 0
        if n > 0:
            self.table_widget.selectRow(0)
            self._populate_tree(0)
        self.table_widget.viewport().update()

    def _set_view(self, display_indices: list, from_search: bool = False):
        """把表格显示行设为 display_indices（all_frames 的索引序列）。

        统一供填充/过滤/搜索/重置使用：重建 _view_order 与反查表、重设行数，
        随后整体重绘可见区。搜索路径传 from_search=True 以保留搜索快照；
        其余调用（重新解析/过滤/换标签页）都会取消活跃搜索。

        无可见数据时保留一行空态占位（delegate 绘制引导文字）。
        """
        self._view_order = [int(x) for x in display_indices]
        self._frame_to_row = {fi: vi for vi, fi in enumerate(self._view_order)}
        if not from_search and self._pre_search_view is not None:
            # 视图被数据源/过滤重建，活跃搜索随之取消
            self._pre_search_view = None
            lbl = getattr(self, "lbl_search_count", None)
            if lbl is not None:
                lbl.setText("")
        self.table_widget.setRowCount(0)
        if not self._view_order:
            # 空态：一行占位，delegate 据此绘制引导提示
            self._view_order = [-1]
            self._frame_to_row = {}
        self.table_widget.setRowCount(len(self._view_order))
        self.table_widget.viewport().update()

    def _reset_filter_state(self):
        """复位过滤状态标志与按钮外观（不改动过滤控件里的条件值）"""
        self._filter_active = False
        self._filter_counts = None
        btn = getattr(self, "filter_btn", None)
        if btn is not None:
            btn.setStyleSheet(FILTER_BTN_STYLE_IDLE)
            btn.setToolTip("按条件过滤报文列表")

    def _apply_current_view(self):
        """数据源内容变化后按当前过滤/搜索条件重建视图（无条件则显示全量）

        别名修改等"只改显示文本"的操作必须走这里：直接 _populate_table()
        会把视图重置成全量，而过滤标志/搜索框还留着，界面与状态自相矛盾。
        """
        if self._filter_active:
            self._on_filter()
        elif self.search_input.text().strip():
            self._pre_search_view = None
            self._do_search()
        else:
            self._populate_table()

    def _cell(self, vi: int, col: int) -> dict:
        """视图行 vi、列 col 的单元格内容（FrameCellDelegate 绘制时调用）。"""
        if not (0 <= vi < len(self._view_order)):
            return self._frame_cell(-1, col)
        fi = self._view_order[vi]
        if fi == -1:
            # 空态占位行：第0列显示引导，其余列为空
            if self._pre_search_view is not None:
                text = "无匹配报文 — 请调整搜索关键字或清空搜索框" if col == 0 else ""
            else:
                text = "无报文可显示 — 请先在左侧粘贴/导入报文后点击「解析报文」" if col == 0 else ""
            return {"text": text, "bg": self._empty_bg(), "fg": self._empty_fg(),
                    "bold": False, "align": Qt.AlignLeft | Qt.AlignVCenter,
                    "sort": None}
        if not (0 <= fi < len(self.all_frames)):
            return self._frame_cell(-1, col)
        return self._frame_cell(fi, col)

    def _empty_bg(self):
        return QColor("#1e1e1e") if self._dark_theme else QColor("#FFFFFF")

    def _empty_fg(self):
        return QColor("#888") if self._dark_theme else QColor("#aaa")

    def _frame_cell(self, fi: int, col: int) -> dict:
        """帧索引 fi、列 col 的单元格内容（供 _cell 与搜索匹配复用）。

        返回 dict：text / bg / fg / bold / align 以及 sort 排序键。
        """
        empty = {"text": "-", "bg": self._empty_bg(), "fg": self._empty_fg(),
                 "bold": False, "align": Qt.AlignLeft | Qt.AlignVCenter,
                 "sort": None}
        if not (0 <= fi < len(self.all_frames)):
            return empty
        frame = self.all_frames[fi]
        row_color = self._row_color_for_frame(frame)
        i = fi
        line_map = getattr(self, '_frame_input_lines', [])

        # 序号（书签帧在行首以 📌 标记）。书签集合统一以 all_frames 下标为键
        # （_toggle_bookmark 存的就是 fi）；此前这里用 frame.index-1，依赖
        # "index 恰为下标+1" 的隐含约定，数据来源变化后即可能标错行。
        text = str(frame.index)
        if fi in self._bookmarks:
            text = "📌 " + text
        if col == 0:
            return {"text": text, "bg": row_color, "fg": None, "bold": False,
                    "align": Qt.AlignLeft | Qt.AlignVCenter, "sort": frame.index}

        if col == 1:  # 原始时间
            ts_text = frame.original_timestamp if frame.original_timestamp else "-"
            return {"text": ts_text, "bg": row_color,
                    "sort": self._timestamp_to_sort_key(frame.original_timestamp)}

        if col == 2:  # 方向
            dir_text = frame.direction
            if not dir_text and frame.original_direction:
                dir_text = frame.original_direction.upper()
            fg = None
            if "主站→子站" in dir_text or "TX" in dir_text or "SEND" in dir_text:
                fg = QColor("#E91E63")
            elif "子站→主站" in dir_text or "RX" in dir_text or "RECV" in dir_text:
                fg = QColor("#2196F3")
            return {"text": dir_text, "bg": row_color, "fg": fg, "sort": None}

        if col == 3:  # 信号类型
            label = frame.signal_label if frame.signal_label else "-"
            is_sig = frame.signal_type != SignalType.OTHER
            return {"text": label, "bg": row_color,
                    "fg": QColor(frame.signal_type.color_hex) if is_sig else None,
                    "bold": is_sig, "sort": None}

        if col == 4:  # 类型标识
            tid_text = (f"{frame.asdu.type_id}: "
                        f"{TypeID.get_description(frame.asdu.type_id)}"
                        if frame.asdu else
                        (frame.apci.frame_type if frame.apci else "-"))
            return {"text": tid_text, "bg": row_color, "sort": None}

        if col == 5:  # 传送原因
            cot_text = (f"({frame.asdu.cot_cause}) "
                        f"{COT.get_description(frame.asdu.cot)}"
                        if frame.asdu else "-")
            return {"text": cot_text, "bg": row_color, "sort": None}

        if col == 6:  # 公共地址
            ca_text = f"0x{frame.asdu.common_address:04X}" if frame.asdu else "-"
            return {"text": ca_text, "bg": row_color, "sort": None}

        if col == 7:  # 对象数
            obj_text = str(frame.asdu.number_of_objects) if frame.asdu else "-"
            obj_count = frame.asdu.number_of_objects if frame.asdu else 0
            return {"text": obj_text, "bg": row_color, "sort": obj_count}

        if col == 8:  # 值（汇总）
            if frame.asdu and frame.asdu.information_elements:
                vals = []
                for elem in frame.asdu.information_elements:
                    alias = get_alias(elem.ioa, frame.asdu.common_address)
                    prefix = f"[{alias}] " if alias else ""
                    vals.append(f"{prefix}{elem.value_desc}")
                text = " | ".join(vals)
            else:
                text = "-"
            return {"text": text, "bg": row_color, "sort": None}

        if col == 9:  # 描述
            desc = frame.description if frame.description else (
                frame.error if frame.error else "")
            if frame.error:
                desc = f"❌ {desc}"
            return {"text": desc, "bg": row_color, "sort": None}

        if col == 10:  # 输入行号
            fidx = frame.index - 1
            if fidx < len(line_map) and line_map[fidx]:
                src_lines = line_map[fidx]
                line_text = ", ".join(str(n + 1) for n in src_lines)
                line_sort = src_lines[0] if src_lines else 0
            else:
                line_text = "-"
                line_sort = 0
            return {"text": line_text, "bg": row_color, "sort": line_sort}

        if col == 11:  # 时间间隔 (ms)
            if i < len(self._frame_intervals):
                interval = self._frame_intervals[i]
                if interval is not None:
                    text = f"{interval:.0f}ms" if interval > 0 else "-"
                    fg = None
                    if interval > 1000:
                        fg = QColor("#F44336")
                    elif interval > 500:
                        fg = QColor("#FF9800")
                    return {"text": text, "bg": row_color, "fg": fg,
                            "sort": interval if interval > 0 else -1}
            return {"text": "-", "bg": row_color, "sort": -1}

        return empty

    def _on_header_sort(self, col: int):
        """表头点击 → 按该列对 _view_order 排序（数值优先，文本回退）"""
        if self._sort_column == col:
            self._sort_order = (Qt.DescendingOrder
                                if self._sort_order == Qt.AscendingOrder
                                else Qt.AscendingOrder)
        else:
            self._sort_column = col
            self._sort_order = Qt.AscendingOrder

        self._sort_view_by(col, self._sort_order)
        self.table_widget.horizontalHeader().setSortIndicator(
            col, self._sort_order)
        self.table_widget.viewport().update()

    def _sort_view_by(self, col: int, order: int):
        """按列排序 _view_order，采用与旧版一致的“数值优先/文本回退”规则"""
        if not self._view_order:
            return

        def key_of(fi):
            # sort 传递给 key_of 的是 _view_order 的元素——即“帧索引”fi，
            # 因此直接用 _frame_cell(fi, col) 取键；若用 _cell(vi, col) 会把
            # fi 当成视图行再做一层 _view_order 反查，非恒等视图时键会错乱。
            cell = self._frame_cell(fi, col)
            sk = cell.get("sort")
            if sk is None:
                # 无数值：按显示文本兜底，排在有数值行之后
                return (False, 0, cell["text"])
            if isinstance(sk, bool):
                return (True, float(sk), cell["text"])
            try:
                return (True, float(sk), cell["text"])
            except (TypeError, ValueError):
                return (False, 0, cell["text"])

        self._view_order.sort(key=key_of, reverse=(order == Qt.DescendingOrder))
        if self._pre_search_view:
            # 搜索激活中排序：快照与视图同步排序，后续输入的匹配范围不回退到旧顺序
            self._pre_search_view.sort(key=key_of, reverse=(order == Qt.DescendingOrder))
        self._frame_to_row = {fi: vi for vi, fi in enumerate(self._view_order)}

    def _frame_idx_at_row(self, row: int) -> int:
        """视图行号 -> all_frames 索引（经 _view_order 映射，排序/搜索后仍正确）

        越界返回 -1（此前原样返回 row，过滤/搜索后 _view_order 远短于
        all_frames，视图行号会被当成帧索引 → 打开错误报文详情/打错书签）。
        调用方均已用 `0 <= fi < len(all_frames)` 或 `fi < 0` 判空。
        """
        if 0 <= row < len(self._view_order):
            return self._view_order[row]
        return -1

    def _row_of_frame(self, frame_idx: int) -> int:
        """all_frames 索引 -> 视图行号；找不到返回 -1"""
        return self._frame_to_row.get(frame_idx, -1)

    def _estimate_col_widths(self, sample_rows: int = 300) -> list:
        """按前 N 行文本实际宽度估算各列列宽。

        大报表（数千行）调用 resizeColumnsToContents 会逐单元格测量导致明显卡顿，
        抽样估算在毫秒级完成，视觉效果接近。
        """
        fm = QFontMetrics(self.table_widget.font())
        cols = self.table_widget.columnCount()
        rows = min(self.table_widget.rowCount(), sample_rows)
        max_w = [40] * cols
        for r in range(rows):
            for c in range(cols):
                w = fm.horizontalAdvance(self._cell(r, c)["text"]) + 28
                if w > max_w[c]:
                    max_w[c] = w
        return [min(w, 420) for w in max_w]
