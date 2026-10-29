"""
模拟器四遥点位表格层（从 simulator_dialog.py 拆出）

四遥表格的创建、刷新与定点更新都在这里。这些方法只依赖宿主的
`self.simulator` / `self._current_points` / `self._links` / `self._yk_ops` /
`self._yt_ops` 与四个表格控件，与连接、SBO 交互、日志等业务解耦，
便于单独构造与测试。
"""

from PyQt5.QtWidgets import (
    QTableWidget, QTableWidgetItem, QPushButton, QComboBox,
    QDoubleSpinBox, QWidget, QHBoxLayout,
)

from iec104_simulator import SimulatorRole
from gui.sim_style import (
    INVALID_COLOR, YX_ON_COLOR, YX_OFF_COLOR, GRAY_BG,
    YK_ON_COLOR, YK_OFF_COLOR, SEL_BTN_NORMAL,
)

_DESC_COL = 3


class _PointTable(QTableWidget):
    """四遥点位表格：只有"描述"列可编辑

    此前所有列都可双击编辑：改坏 IOA 列后该行再也匹配不到刷新目标
    （_update_*_row 按 "0xXXXX" 文本反查行号），主站模式改"当前值"列
    则既不入 PointData 也不回写，下次刷新即被覆盖。
    """

    def edit(self, index, trigger, event):
        if index.isValid() and index.column() != _DESC_COL:
            return False
        return super().edit(index, trigger, event)


class PointTablesMixin:
    """四遥点位表格的创建/刷新/定点更新"""

    def _create_point_table(self, headers, widths):
        """创建点位表格（仅描述列可编辑）"""
        table = _PointTable()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        # 允许编辑（用于描述列）
        table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed)
        table.horizontalHeader().setStretchLastSection(True)
        for i, w in enumerate(widths):
            table.setColumnWidth(i, w)
        # cellChanged 信号用于保存描述编辑
        table.cellChanged.connect(self._on_cell_changed)
        return table

    def _refresh_all_tables(self):
        """刷新所有表格"""
        self._block_cell_changed = True
        try:
            self._refresh_yx_table()
            self._refresh_yc_table()
            self._refresh_yk_table()
            self._refresh_yt_table()
        finally:
            self._block_cell_changed = False

    def _build_link_combo(self, source_type: str, current_ioa, on_change):
        """构造关联点位下拉框（列出全部该类型点位，userData=IOA）"""
        combo = QComboBox()
        pts = sorted([p for p in self._current_points.values()
                      if p.signal_type == source_type], key=lambda p: p.ioa)
        for p in pts:
            combo.addItem(f"0x{p.ioa:04X} {p.description}", p.ioa)
        if current_ioa is not None:
            idx = combo.findData(current_ioa)
            if idx >= 0:
                combo.setCurrentIndex(idx)   # 须在 connect 前设置，避免误触发
        combo.currentIndexChanged.connect(
            lambda _, c=combo: on_change(c.currentData()))
        return combo

    def _yk_value_item(self, yk_ioa: int) -> QTableWidgetItem:
        """遥控"当前值"单元格：显示关联遥信的合/分（未学到真实值显示 -）"""
        link_ioa = self._yk_links.get(yk_ioa)
        lp = self._current_points.get(link_ioa) if link_ioa is not None else None
        if lp is None or not lp.is_valid:
            item = QTableWidgetItem("-")
            item.setForeground(INVALID_COLOR)
            item.setBackground(GRAY_BG)
            return item
        item = QTableWidgetItem("合" if lp.value else "分")
        item.setForeground(YX_ON_COLOR if lp.value else YX_OFF_COLOR)
        return item

    def _yt_value_item(self, yt_ioa: int) -> QTableWidgetItem:
        """遥调"当前值"单元格：显示关联遥测的值（未学到真实值显示 -）"""
        link_ioa = self._yt_links.get(yt_ioa)
        lp = self._current_points.get(link_ioa) if link_ioa is not None else None
        if lp is None or not lp.is_valid:
            item = QTableWidgetItem("-")
            item.setForeground(INVALID_COLOR)
            item.setBackground(GRAY_BG)
            return item
        val_text = (f"{lp.value:.2f}"
                    if isinstance(lp.value, (int, float)) else str(lp.value))
        return QTableWidgetItem(val_text)

    def _update_yk_value_cell(self, yk_ioa: int):
        """按 IOA 定位遥控行并只刷新值单元格（不重建表格、保留按钮预制态）"""
        self._update_point_value_cell(self.yk_table, yk_ioa,
                                      self._yk_value_item(yk_ioa))

    def _update_yt_value_cell(self, yt_ioa: int):
        """按 IOA 定位遥调行并只刷新值单元格"""
        self._update_point_value_cell(self.yt_table, yt_ioa,
                                      self._yt_value_item(yt_ioa))

    def _update_yx_row(self, ioa: int):
        """按 IOA 定点刷新遥信行（值/品质/时间），不重建整表"""
        pt = self._current_points.get(ioa)
        if pt is None:
            return
        is_slave = self.role_combo.currentData() == SimulatorRole.SLAVE
        table = self.yx_table
        target = f"0x{ioa:04X}"
        for row in range(table.rowCount()):
            item = table.item(row, 1)
            if item is None or item.text() != target:
                continue
            if is_slave:
                btn = table.cellWidget(row, 4)
                if isinstance(btn, QPushButton):
                    on = bool(pt.value)
                    btn.setText("合" if on else "分")
                    btn.setStyleSheet(
                        f"QPushButton {{ background-color: "
                        f"{YK_ON_COLOR if on else YK_OFF_COLOR}; color: white; "
                        "font-weight: bold; padding: 3px 8px; border-radius: 3px; "
                        f"border: none; min-width: 50px; }} QPushButton:hover {{ "
                        f"background-color: {'#388E3C' if on else '#C62828'}; }}")
            elif not pt.is_valid:
                val_item = QTableWidgetItem("-")
                val_item.setForeground(INVALID_COLOR)
                val_item.setBackground(GRAY_BG)
                table.setItem(row, 4, val_item)
            else:
                val_item = QTableWidgetItem("合" if pt.value else "分")
                val_item.setForeground(YX_ON_COLOR if pt.value else YX_OFF_COLOR)
                table.setItem(row, 4, val_item)
            table.setItem(row, 5, QTableWidgetItem(
                "有效" if pt.quality == 0 else f"0x{pt.quality:02X}"))
            table.setItem(row, 6, QTableWidgetItem(pt.timestamp or "-"))
            return

    def _update_yc_row(self, ioa: int):
        """按 IOA 定点刷新遥测行（值/品质/时间），不重建整表"""
        pt = self._current_points.get(ioa)
        if pt is None:
            return
        is_slave = self.role_combo.currentData() == SimulatorRole.SLAVE
        table = self.yc_table
        target = f"0x{ioa:04X}"
        for row in range(table.rowCount()):
            item = table.item(row, 1)
            if item is None or item.text() != target:
                continue
            if is_slave:
                spin = table.cellWidget(row, 4)
                if isinstance(spin, QDoubleSpinBox) and not spin.hasFocus():
                    spin.setValue(float(pt.value)
                                  if isinstance(pt.value, (int, float)) else 0)
            elif not pt.is_valid:
                val_item = QTableWidgetItem("-")
                val_item.setForeground(INVALID_COLOR)
                val_item.setBackground(GRAY_BG)
                table.setItem(row, 4, val_item)
            else:
                val_text = (f"{pt.value:.2f}"
                            if isinstance(pt.value, (int, float)) else str(pt.value))
                table.setItem(row, 4, QTableWidgetItem(val_text))
            table.setItem(row, 5, QTableWidgetItem(
                "有效" if pt.quality == 0 else f"0x{pt.quality:02X}"))
            table.setItem(row, 6, QTableWidgetItem(pt.timestamp or "-"))
            return

    def _make_yx_button(self, ioa: int, on: bool) -> QPushButton:
        """从站模式遥信行的"合/分"切换按钮"""
        btn = QPushButton("合" if on else "分")
        btn.setStyleSheet(
            f"QPushButton {{ background-color: {YK_ON_COLOR if on else YK_OFF_COLOR}; "
            f"color: white; font-weight: bold; padding: 3px 8px; "
            f"border-radius: 3px; border: none; min-width: 50px; }}"
            f"QPushButton:hover {{ background-color: {'#388E3C' if on else '#C62828'}; }}"
        )
        btn.clicked.connect(lambda _, i=ioa: self._on_toggle_yx(i))
        return btn

    def _refresh_yx_table(self):
        """刷新遥信表格"""
        yx_points = sorted(
            [p for p in self._current_points.values() if p.signal_type == "YX"],
            key=lambda p: p.ioa
        )
        is_slave = self.role_combo.currentData() == SimulatorRole.SLAVE
        self.yx_table.setRowCount(len(yx_points))

        for i, pt in enumerate(yx_points):
            self.yx_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.yx_table.setItem(i, 1, QTableWidgetItem(f"0x{pt.ioa:04X}"))
            self.yx_table.setItem(i, 2, QTableWidgetItem(str(pt.ioa)))
            self.yx_table.setItem(i, 3, QTableWidgetItem(pt.description))

            # 当前值
            if not pt.is_valid and not is_slave:
                val_item = QTableWidgetItem("-")
                val_item.setForeground(INVALID_COLOR)
                val_item.setBackground(GRAY_BG)
                self.yx_table.setItem(i, 4, val_item)
            elif is_slave:
                self.yx_table.setCellWidget(i, 4, self._make_yx_button(pt.ioa, bool(pt.value)))
            else:
                val_item = QTableWidgetItem("合" if pt.value else "分")
                val_item.setForeground(YX_ON_COLOR if pt.value else YX_OFF_COLOR)
                self.yx_table.setItem(i, 4, val_item)

            q_text = "有效" if pt.quality == 0 else f"0x{pt.quality:02X}"
            self.yx_table.setItem(i, 5, QTableWidgetItem(q_text))
            self.yx_table.setItem(i, 6, QTableWidgetItem(pt.timestamp or "-"))

    def _refresh_yc_table(self):
        """刷新遥测表格"""
        yc_points = sorted(
            [p for p in self._current_points.values() if p.signal_type == "YC"],
            key=lambda p: p.ioa
        )
        is_slave = self.role_combo.currentData() == SimulatorRole.SLAVE
        self.yc_table.setRowCount(len(yc_points))

        for i, pt in enumerate(yc_points):
            self.yc_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.yc_table.setItem(i, 1, QTableWidgetItem(f"0x{pt.ioa:04X}"))
            self.yc_table.setItem(i, 2, QTableWidgetItem(str(pt.ioa)))
            self.yc_table.setItem(i, 3, QTableWidgetItem(pt.description))

            if is_slave:
                spin = QDoubleSpinBox()
                spin.setRange(-999999, 999999)
                spin.setDecimals(2)
                spin.setValue(float(pt.value) if pt.value is not None else 0)
                spin.setStyleSheet("QDoubleSpinBox { padding: 2px; }")
                spin.setProperty("ioa", pt.ioa)
                spin.editingFinished.connect(lambda s=spin: self._on_yc_edit_finished(s))
                self.yc_table.setCellWidget(i, 4, spin)
            elif not pt.is_valid:
                val_item = QTableWidgetItem("-")
                val_item.setForeground(INVALID_COLOR)
                val_item.setBackground(GRAY_BG)
                self.yc_table.setItem(i, 4, val_item)
            else:
                val_text = f"{pt.value:.2f}" if isinstance(pt.value, (int, float)) else str(pt.value)
                self.yc_table.setItem(i, 4, QTableWidgetItem(val_text))

            q_text = "有效" if pt.quality == 0 else f"0x{pt.quality:02X}"
            self.yc_table.setItem(i, 5, QTableWidgetItem(q_text))
            self.yc_table.setItem(i, 6, QTableWidgetItem(pt.timestamp or "-"))

    def _refresh_yk_table(self):
        """刷新遥控表格"""
        yk_points = sorted(
            [p for p in self._current_points.values() if p.signal_type == "YK"],
            key=lambda p: p.ioa
        )
        is_slave = self.role_combo.currentData() == SimulatorRole.SLAVE
        # 必须先清空：setRowCount 缩减行数会销毁行内按钮的 C++ 对象，而
        # _yk_ops 只在"遍历到的点位"上覆盖写、从站模式下还会 pop，点数
        # 减少（尤其减到 0）时旧条目会残留已销毁控件引用，后续
        # _update_point_op_buttons 对其 setEnabled 抛
        # RuntimeError: wrapped C/C++ object has been deleted。
        self._yk_ops.clear()
        self.yk_table.setRowCount(len(yk_points))

        for i, pt in enumerate(yk_points):
            self.yk_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.yk_table.setItem(i, 1, QTableWidgetItem(f"0x{pt.ioa:04X}"))
            self.yk_table.setItem(i, 2, QTableWidgetItem(str(pt.ioa)))
            self.yk_table.setItem(i, 3, QTableWidgetItem(pt.description))

            # 当前值显示关联遥信点位的合/分（遥控是写操作，自身无遥信含义）
            self.yk_table.setItem(i, 4, self._yk_value_item(pt.ioa))

            # 关联遥信下拉框（默认第 i 个遥信，可改）
            self.yk_table.setCellWidget(i, 5, self._build_link_combo(
                "YX", self._yk_links.get(pt.ioa),
                lambda link_ioa, k=pt.ioa: self._on_yk_link_changed(k, link_ioa)))

            if is_slave:
                self.yk_table.setItem(i, 6, QTableWidgetItem("(等待主站遥控)"))
                self._yk_ops.pop(pt.ioa, None)
            else:
                ops = QWidget()
                ops_layout = QHBoxLayout(ops)
                ops_layout.setContentsMargins(4, 2, 4, 2)
                ops_layout.setSpacing(6)
                # 显式"预制"步骤（与遥调一致且命令专属）：
                # SBO 开启 → 按钮为"预制合/预制分"，点击发送选择命令，
                #   预制确认后对应按钮转"执行合/执行分"，再次点击才下发；
                #   未预制时不能执行（另一命令同时禁用）。
                # SBO 关闭 → 按钮为"合/分"，点击直接执行。
                btn_on = QPushButton()
                btn_off = QPushButton()
                btn_on.clicked.connect(
                    lambda _, ioa=pt.ioa: self._on_yk_command(ioa, 1))
                btn_off.clicked.connect(
                    lambda _, ioa=pt.ioa: self._on_yk_command(ioa, 0))
                # 初始全部禁用，可用性由 _update_point_op_buttons 统一派生
                btn_on.setEnabled(False)
                btn_off.setEnabled(False)
                ops_layout.addWidget(btn_on)
                ops_layout.addWidget(btn_off)
                ops_layout.addStretch()
                self.yk_table.setCellWidget(i, 6, ops)
                self._yk_ops[pt.ioa] = {
                    "ioa": pt.ioa,
                    "btn_on": btn_on, "btn_off": btn_off,
                    "selected": False, "selected_cmd": None,
                    "pending_cmd": None,
                }
                self._apply_yk_button_state(self._yk_ops[pt.ioa])
        self._update_point_op_buttons()

    def _refresh_yt_table(self):
        """刷新遥调表格"""
        yt_points = sorted(
            [p for p in self._current_points.values() if p.signal_type == "YT"],
            key=lambda p: p.ioa
        )
        is_slave = self.role_combo.currentData() == SimulatorRole.SLAVE
        # 同 _refresh_yk_table：重建前清空，避免残留已销毁的 spin/按钮引用
        self._yt_ops.clear()
        self.yt_table.setRowCount(len(yt_points))

        for i, pt in enumerate(yt_points):
            self.yt_table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.yt_table.setItem(i, 1, QTableWidgetItem(f"0x{pt.ioa:04X}"))
            self.yt_table.setItem(i, 2, QTableWidgetItem(str(pt.ioa)))
            self.yt_table.setItem(i, 3, QTableWidgetItem(pt.description))

            # 当前值显示关联遥测点位的值（遥调是写操作，自身无测量含义）
            self.yt_table.setItem(i, 4, self._yt_value_item(pt.ioa))

            # 关联遥测下拉框（默认第 i 个遥测，可改）
            self.yt_table.setCellWidget(i, 5, self._build_link_combo(
                "YC", self._yt_links.get(pt.ioa),
                lambda link_ioa, k=pt.ioa: self._on_yt_link_changed(k, link_ioa)))

            if is_slave:
                self.yt_table.setItem(i, 6, QTableWidgetItem("(等待主站遥调)"))
                self._yt_ops.pop(pt.ioa, None)
            else:
                ops = QWidget()
                ops_layout = QHBoxLayout(ops)
                ops_layout.setContentsMargins(4, 2, 4, 2)
                ops_layout.setSpacing(6)
                # 独立预制按钮：发送 SBO 选择命令，确认后点亮设定
                btn_select = QPushButton("预制")
                btn_select.setStyleSheet(SEL_BTN_NORMAL)
                btn_select.clicked.connect(
                    lambda _, ioa=pt.ioa: self._on_yt_select(ioa))
                spin = QDoubleSpinBox()
                spin.setRange(-999999, 999999)
                spin.setDecimals(2)
                spin.setValue(float(pt.value) if pt.value is not None else 0)
                spin.setMaximumWidth(100)
                ops_layout.addWidget(btn_select)
                ops_layout.addWidget(spin)
                btn_set = QPushButton("设定")
                btn_set.setStyleSheet(
                    "QPushButton { background-color: #2196F3; color: white; padding: 3px 10px; border-radius: 3px; border: none; }"
                    "QPushButton:hover { background-color: #1565C0; }"
                )
                btn_set.clicked.connect(
                    lambda _, ioa=pt.ioa, s=spin: self._on_yt_command(ioa, s.value()))
                # 初始全部禁用，可用性由 _update_point_op_buttons 统一派生
                btn_set.setEnabled(False)
                btn_select.setEnabled(False)
                ops_layout.addWidget(btn_set)
                ops_layout.addStretch()
                self.yt_table.setCellWidget(i, 6, ops)
                self._yt_ops[pt.ioa] = {
                    "ioa": pt.ioa,
                    "btn_set": btn_set, "btn_select": btn_select,
                    "spin": spin, "selected": False,
                }
        self._update_point_op_buttons()

    @staticmethod
    def _update_point_value_cell(table, ioa: int, value, color=None):
        """按 IOA 定位行并只刷新"值"单元格（列4），不触碰操作列控件

        列1 存的是 0xXXXX 格式的 IOA，据此反查行号。
        value 可为 str（生成默认 item）或现成的 QTableWidgetItem。
        """
        target = f"0x{ioa:04X}"
        for row in range(table.rowCount()):
            item = table.item(row, 1)
            if item is not None and item.text() == target:
                if isinstance(value, QTableWidgetItem):
                    cell = value
                else:
                    cell = QTableWidgetItem(value)
                    if color is not None:
                        cell.setForeground(color)
                table.setItem(row, 4, cell)
                return
