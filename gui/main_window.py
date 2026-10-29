"""
主窗口 - IEC104 报文解析器
"""

import os
import sys
import re
import json
import logging
from datetime import datetime
from typing import List, Optional

from PyQt5.QtWidgets import (
    QMainWindow, QApplication, QWidget, QVBoxLayout,
    QTreeWidgetItem, QTextEdit, QFileDialog, QMessageBox,
    QTableWidget, QMenu, QDialog, QPlainTextEdit, QInputDialog,
)
from PyQt5.QtCore import Qt, QTimer, QSettings
from PyQt5.QtGui import QFont, QColor, QTextCursor

# 确保能找到父模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from iec104_parser import (
    IEC104Parser, IEC104Frame, SignalType, TypeID, COT,
    ParseMode, detect_input_format
)
from gui.about_dialog import AboutDialog
from gui.stats_dialog import StatsDialog
from gui.ioa_alias_dialog import IOAAliasDialog, get_alias
from gui.style import LIGHT_QSS, DARK_QSS, apply_selection_palette
from gui.exporters import export_csv, export_json, export_txt
# 拆分出去的模块，这里重新导出以保持 `from gui.main_window import ...` 既有引用不变
from gui.parse_worker import ParseWorker
from gui.frame_table import FrameTableMixin, FILTER_BTN_STYLE_IDLE, FILTER_BTN_STYLE_ACTIVE
from gui.ui_layout import UiLayoutMixin
from version import APP_TITLE, APP_VERSION

logger = logging.getLogger(__name__)

# 文件导入大小上限：防止大文件把界面/内存卡死
MAX_TEXT_IMPORT_BYTES = 20 * 1024 * 1024   # 文本类导入上限 20MB
MAX_BIN_IMPORT_BYTES = 8 * 1024 * 1024     # 二进制转hex导入上限 8MB

# UI 设置持久化（QSettings 注册表键前缀 ui/）
_SETTINGS_ORG = "SWT"
_SETTINGS_APP = "IEC104 Parser"

# 过滤按钮样式常量见 gui/frame_table.py（随视图层一起迁走）

# 授权状态常驻按钮：状态色
LICENSE_COLOR_OK = "#2E7D32"
LICENSE_COLOR_WARN = "#FF9800"    # 剩余 ≤7 天 / 未激活
LICENSE_COLOR_DANGER = "#F44336"  # 剩余 ≤3 天 / 已到期


class MainWindow(UiLayoutMixin, FrameTableMixin, QMainWindow):
    """主窗口"""

    def __init__(self):
        super().__init__()
        self.parser = IEC104Parser()
        self.all_frames: List[IEC104Frame] = []
        self._byte_position_map = []
        self._frame_byte_offset = []
        self._frame_input_lines = []
        self._current_frame_idx = 0
        self.parse_mode: ParseMode = ParseMode.NORMAL
        self._dark_theme = False
        self._current_theme = "light"
        self._auto_detect = True
        self._is_activated = False
        self._is_license_expired = False

        # 后台解析相关
        self._parse_worker: Optional[ParseWorker] = None
        self._is_parsing: bool = False
        self._parse_cancelled: bool = False

        # 过滤生效状态（防止用户忘记列表处于过滤中）
        self._filter_active: bool = False
        self._filter_counts: tuple = None  # (保留条数, 总条数)

        # 导入/导出记忆的目录
        self._last_dir: str = ""
        # 用户保存过的列宽偏好（关闭时采集，下次启动直接套用）
        self._saved_col_widths: Optional[list] = None

        # 报文列表虚拟化状态（见 FrameCellDelegate / _cell / _on_header_sort）
        self._view_order: list = []      # 视图行 → all_frames 索引（当前显示顺序）
        self._frame_to_row: dict = {}    # all_frames 索引 → 视图行（0(1) 反查）
        self._pre_search_view: Optional[list] = None  # 搜索激活前的视图快照（None=非搜索态）
        self._sort_column: int = -1
        self._sort_order: int = Qt.AscendingOrder
        self._frame_intervals: list = []

        self.setWindowTitle(APP_TITLE)
        self.resize(1320, 840)
        self.setMinimumSize(900, 640)

        self._setup_icon()
        self.setAcceptDrops(True)

        self._setup_ui()
        # 浅色基础样式表：统一各控件圆角/内边距/焦点环（深色主题切换时整体替换）
        self.setStyleSheet(LIGHT_QSS)
        # 下拉弹出列表 hover/选中配色（QSS ::item:hover 对弹出不生效）
        apply_selection_palette(self, dark=False)
        self._setup_menu()
        self._setup_toolbar()
        self._setup_statusbar()

        self._check_license()
        # 授权已到期的提示放在窗口显示后触发（singleShot 在事件循环启动后执行）
        QTimer.singleShot(0, self._may_prompt_expired)
        self._update_status_stats()

        # 提示文字通过 placeholder 显示（输入内容后自动消失），不再写入实际内容
        # 恢复上次关闭时的界面状态（窗口大小/分栏/主题/列宽/目录）
        self._load_ui_settings()


    def _update_status_stats(self):
        """更新状态栏统计（基于 self.all_frames，过滤时反映过滤结果）"""
        frames = self.all_frames
        total = len(frames)
        valid = sum(1 for f in frames if not f.error)
        i_frames = sum(1 for f in frames if f.apci and f.apci.frame_type == "I-format")
        s_frames = sum(1 for f in frames if f.apci and f.apci.frame_type == "S-format")
        u_frames = sum(1 for f in frames if f.apci and f.apci.frame_type == "U-format")
        yx_count = sum(1 for f in frames if f.signal_type == SignalType.YX)
        yc_count = sum(1 for f in frames if f.signal_type == SignalType.YC)
        yk_count = sum(1 for f in frames if f.signal_type == SignalType.YK)
        yt_count = sum(1 for f in frames if f.signal_type == SignalType.YT)
        ym_count = sum(1 for f in frames if f.signal_type == SignalType.YM)
        master_send = sum(1 for f in frames if f.direction == "主站→子站")
        slave_send = sum(1 for f in frames if f.direction == "子站→主站")

        # 状态栏简要信息（过滤中时前置常驻提示，防止误以为数据缺失）
        prefix = ""
        if getattr(self, '_filter_active', False) and self._filter_counts:
            kept, tot = self._filter_counts
            prefix = f"🔍 过滤中 {kept}/{tot} | "
        summary = f"{prefix}共 {total} 帧 | I:{i_frames} S:{s_frames} U:{u_frames}"
        if total - valid > 0:
            summary += f" | ❌ {total - valid} 错误"
        self.lbl_stats.setText(summary)

        # Tooltip 显示完整统计
        detail = (
            f"总报文: {total}  有效: {valid}  无效: {total - valid}\n"
            f"I帧: {i_frames}  S帧: {s_frames}  U帧: {u_frames}\n"
            f"主→从: {master_send}  从→主: {slave_send}\n"
            f"遥信: {yx_count}  遥测: {yc_count}  遥控: {yk_count}  遥调: {yt_count}  遥脉: {ym_count}"
        )
        self.lbl_stats.setToolTip(detail)
        self.lbl_count.setText(f"报文: {total}")

    # ==================================================================
    # 解析
    # ==================================================================

    def _on_parse(self):
        """解析全部报文 - 使用后台线程避免UI阻塞"""
        raw_text = self.hex_input.toPlainText()

        if not raw_text.strip():
            QMessageBox.information(self, "提示", "请输入十六进制报文数据")
            return

        self._start_parse(raw_text)

    def _on_parse_text(self, text: str):
        """解析指定文本（用于选中内容解析）"""
        if not text.strip():
            return
        self._start_parse(text)

    def _live_worker(self):
        """返回解析线程对象；Python 包装器指向已销毁的 C++ 对象时返回 None

        QThread 被 deleteLater 销毁后，包装器仍可能被引用；此时任何
        isRunning()/wait() 都会抛 RuntimeError("wrapped C/C++ object ...
        has been deleted")，而槽里的未捕获异常会让 PyQt5 走 qFatal →
        进程直接消失（用户看到的"闪退"，连报错框都没有）。
        """
        worker = self._parse_worker
        if worker is None:
            return None
        try:
            worker.isRunning()
        except RuntimeError:
            self._parse_worker = None
            return None
        return worker

    def _start_parse(self, raw_text: str):
        """启动解析流程"""

        # 上一次解析未结束时：请求取消并等待其收尾，避免双线程并发写结果
        worker = self._live_worker()
        if worker is not None and worker.isRunning():
            worker.cancel()
            for sig, slot in (
                (worker.parse_finished, self._on_parse_finished),
                (worker.progress_updated, self._on_parse_progress),
                (worker.parse_error, self._on_parse_error),
            ):
                try:
                    sig.disconnect(slot)
                except TypeError:
                    pass  # 该信号本就未连接，不能因此漏掉后面的
            if not worker.wait(2000):
                self.statusbar.showMessage("上一次解析仍在结束中，请稍候再试", 3000)
                return

        # 清除旧数据
        self.parser.clear()
        self.all_frames.clear()
        self._byte_position_map = []
        self._frame_byte_offset = []
        self._frame_input_lines = []
        # 书签是"帧下标"集合，重新解析后指向完全不同的报文，必须清空
        self._bookmarks.clear()
        # 搜索框文本若留着，视图却是全量 → 搜索态与显示不一致
        self._pre_search_view = None
        if getattr(self, "search_input", None) is not None \
                and self.search_input.text():
            self.search_input.blockSignals(True)
            self.search_input.clear()
            self.search_input.blockSignals(False)
        self.tree_widget.clear()
        self.table_widget.setRowCount(0)
        self.hex_view.clear()

        # 自动检测模式
        if self._auto_detect:
            detected = self._auto_detect_mode(raw_text)
            # 必须无条件应用检测结果：此前只在 detected != NORMAL 时才切换，
            # 于是"先解析过日志报文，再清空导入纯十六进制"时 parse_mode 仍停在
            # XN3000，普通报文被按日志切块 → 0 帧（用户看到"点了解析没反应"）。
            if detected != self.parse_mode:
                self.parse_mode = detected
                idx = self.mode_combo.findData(detected)
                if idx >= 0:
                    self.mode_combo.blockSignals(True)
                    self.mode_combo.setCurrentIndex(idx)
                    self.mode_combo.blockSignals(False)
                if detected != ParseMode.NORMAL:
                    self.statusbar.showMessage(
                        f"自动检测到 {detected.value.upper()} 模式", 2000)

        # 启动后台解析
        self._is_parsing = True
        # 未激活版本：每次仅解析第 1 条报文（激活后不限制）
        max_frames = 0 if self._is_activated else 1
        self._parse_worker = ParseWorker(self.parser, raw_text, self.parse_mode,
                                         max_frames=max_frames)

        # 连接信号
        self._parse_worker.progress_updated.connect(self._on_parse_progress)
        self._parse_worker.parse_finished.connect(self._on_parse_finished)
        self._parse_worker.parse_error.connect(self._on_parse_error)
        # 结束后自动回收：此前从不 deleteLater，反复解析会累积 QThread 对象，
        # 且上面 wait(2000) 超时后旧线程仍在跑，GC 回收时触发
        # "QThread: Destroyed while thread is still running"。
        # finished 在 run() 返回后发射，晚于 parse_finished，槽函数安全。
        # 必须先清 self._parse_worker 引用再 deleteLater：否则引用悬空指向
        # 已销毁的 C++ 对象，下次解析/取消/退出时 isRunning() 抛
        # RuntimeError，PyQt5 对槽内未捕获异常会 qFatal → 整个程序闪退。
        self._parse_worker.finished.connect(self._on_parse_worker_finished)
        self._parse_worker.finished.connect(self._parse_worker.deleteLater)

        # 更新UI状态
        self._set_parsing_state(True)
        self.statusbar.showMessage("正在解析报文...", 0)

        # 启动后台线程
        self._parse_worker.start()

    def _on_clear(self):
        """清空"""
        # 丢弃"在飞"的解析结果：否则旧线程的 parse_finished 回来后又把刚清空的
        # 界面填满（用户以为清空没生效）；线程继续跑到结束再自然回收，
        # 引用保留在 _parse_worker 上，关闭窗口时仍能等待它，不会悬空。
        worker = self._live_worker()
        if worker is not None and worker.isRunning():
            worker.cancel()
            for sig in (worker.parse_finished, worker.progress_updated,
                        worker.parse_error):
                try:
                    sig.disconnect()
                except TypeError:
                    pass
            self._is_parsing = False
            self._set_parsing_state(False)

        self.hex_input.clear()
        self.parser.clear()
        self.all_frames.clear()
        self._byte_position_map = []
        self._frame_byte_offset = []
        self._frame_input_lines = []
        self._frame_intervals = []
        self._current_frame_idx = 0
        self._last_send_seq = -1
        self._bookmarks.clear()
        self._tab_data.pop(self._current_tab_index, None)
        self.tree_widget.clear()
        self.hex_view.clear()
        # 搜索/过滤/虚拟视图状态一并复位：此前只清数据不清视图，之后清空
        # 搜索框会把旧 _view_order 长度还原成 N 行空白（幽灵行），状态栏
        # 也仍显示"过滤中"。
        self._pre_search_view = None
        self._filter_active = False
        self._filter_counts = None
        if self.search_input.text():
            self.search_input.blockSignals(True)
            self.search_input.clear()
            self.search_input.blockSignals(False)
        self.lbl_search_count.setText("")
        self._populate_table()
        self.lbl_line_count.setText("0 行")
        self._update_status_stats()

    def _set_parsing_state(self, parsing: bool):
        """切换解析状态的UI"""
        self._is_parsing = parsing
        self.progress_bar.setVisible(parsing)
        self.btn_cancel_parse.setVisible(parsing)

        # 禁用/启用相关按钮
        self.btn_parse.setEnabled(not parsing)
        self.btn_import_file.setEnabled(not parsing)
        self.btn_clear.setEnabled(not parsing)

        if parsing:
            self.progress_bar.setValue(0)
            self.btn_parse.setText("⏳ 解析中...")
        else:
            self.btn_parse.setText("▶ 解析报文")
            self.btn_cancel_parse.setEnabled(True)

    def _on_parse_progress(self, current: int, total: int):
        """更新解析进度"""
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.statusbar.showMessage(f"正在解析报文... {current}/{total}")

    def _on_parse_worker_finished(self):
        """解析线程结束：清空引用，避免残留指向已被 deleteLater 销毁的
        C++ 对象的悬空包装器（下次解析/取消/退出访问即崩溃）

        必须校验 sender：上一次解析被 wait() 收尾期间排队到达的 finished
        会在新 worker 启动后才投递，无条件下清引用会把**新** worker 丢掉，
        随后取消/关闭都拿不到它 → "QThread: Destroyed while thread is
        still running"。
        """
        worker = self.sender()
        if isinstance(worker, ParseWorker) and worker is not self._parse_worker:
            return
        self._parse_worker = None

    def _set_frames(self, frames: list):
        """更新全量帧表并同步主解析器

        序号检查/合规检查/报文配对三个分析功能都从 self.parser.frames 取数，
        而 ParseWorker 在自己的内部副本上解析——若只更新 all_frames，三者
        实际永远面对空表，恒报"无问题"（审查 Top1 缺陷）。
        """
        self.all_frames = frames
        self.parser.frames = list(frames)

    def _on_parse_finished(self, frames: list):
        """解析完成处理"""
        self._is_parsing = False

        # 元数据一律取自**发信号的那个** worker：排队信号可能在新一轮解析
        # 启动后才投递，读 self._parse_worker 会读到别人的映射/上限标志
        worker = self.sender()
        if not isinstance(worker, ParseWorker):
            worker = self._live_worker()
        elif worker is not self._parse_worker:
            # 过期 worker 的迟到结果：丢弃，但按钮状态必须恢复，
            # 否则界面永远停在"解析中"
            self._set_parsing_state(False)
            return

        self._set_frames(frames)

        # 存储输入行映射
        lines = getattr(worker, '_frame_input_lines', None)
        self._frame_input_lines = (lines if lines is not None
                                   else [[] for _ in self.all_frames])

        # 应用输入行颜色（仅普通模式）
        if self.parse_mode == ParseMode.NORMAL:
            self._apply_input_line_colors()

        # 更新UI
        self._last_send_seq = -1
        self._analyze_time_intervals()
        self._populate_table()
        self._update_status_stats()

        # 保存到当前标签页数据
        self._save_current_tab_state()

        # 恢复按钮状态
        self._set_parsing_state(False)

        # 输入清洗提示：告诉用户哪些字符被丢弃了（此前静默丢弃，用户只看到
        # "解析出来的报文不对"，分不清是抄错还是工具问题）
        hex_warns = getattr(worker, 'hex_warnings', None) or []
        if hex_warns:
            first = hex_warns[0]
            more = f" 等 {len(hex_warns)} 处" if len(hex_warns) > 1 else ""
            self.lbl_input_status.setText(
                f"⚠ 解析时忽略了非法字符：{first}{more}")
            self.lbl_input_status.setStyleSheet(
                "color: #E65100; font-size: 9pt;")
            self.lbl_input_status.setToolTip("\n".join(hex_warns[:20]))

        # 统计信息（用户取消时明确提示为部分结果；未激活截断时提示单条限制）
        limit_hit = getattr(worker, 'hit_frame_limit', False)
        error_count = sum(1 for f in self.all_frames if f.error)
        if self._parse_cancelled:
            self._parse_cancelled = False
            self.statusbar.showMessage(
                f"已取消: 显示部分结果（{len(self.all_frames)} 条报文），重新解析可获取完整结果", 5000)
        elif limit_hit:
            self.statusbar.showMessage(
                "未激活: 每次仅解析第 1 条报文，其余已被忽略", 5000)
        else:
            self.statusbar.showMessage(
                f"解析完成: {len(self.all_frames)} 条报文, {error_count} 条失败", 5000)

        # 未激活被截断 → 提示并提供激活入口（非阻塞）
        if limit_hit:
            self._show_limit_dialog()

        # 自动切换到报文列表标签
        self.result_tabs.setCurrentIndex(1)

    def _show_limit_dialog(self):
        """未激活次数限制提示（非阻塞）

        此前用 box.exec_() 在 parse_finished 槽里再开一层事件循环：此时队列里
        还排着 worker 的 finished/deleteLater，会在嵌套循环里把发信号的对象
        销毁，槽返回时访问 sender 属于未定义行为（严重时整个进程消失）。
        改为 open()，槽立即返回。
        """
        box = QMessageBox(self)
        box.setAttribute(Qt.WA_DeleteOnClose, True)
        box.setWindowTitle("功能限制")
        box.setIcon(QMessageBox.Information)
        box.setText(
            "未激活版本每次只能解析 1 条报文，本次输入中的其余报文已被忽略。\n\n"
            "激活后可一次性解析全部报文，并解锁文件导入、导出等功能。")
        open_btn = box.addButton("打开激活窗口", QMessageBox.ActionRole)
        box.addButton("知道了", QMessageBox.RejectRole)

        def _on_done(_result, box=box, btn=open_btn):
            try:
                if box.clickedButton() is btn:
                    self._open_license_dialog()
            except RuntimeError:
                pass    # 对话框已销毁

        box.finished.connect(_on_done)
        box.open()

    def _on_parse_error(self, error_msg: str):
        """解析错误处理"""
        self._is_parsing = False
        self._set_parsing_state(False)
        self.statusbar.showMessage(f"解析失败: {error_msg}", 5000)
        QMessageBox.critical(self, "解析错误", f"解析过程中发生错误:\n\n{error_msg}")

    def _analyze_time_intervals(self):
        """分析帧间时间间隔（毫秒；跨零点按 +24h 处理）"""
        self._frame_intervals = []
        prev = None
        for i, frame in enumerate(self.all_frames):
            if i == 0:
                self._frame_intervals.append(0)
                prev = self._timestamp_to_sort_key(frame.original_timestamp)
                continue
            cur = self._timestamp_to_sort_key(frame.original_timestamp)
            if cur < 0 or prev is None or prev < 0:
                self._frame_intervals.append(None)
            else:
                interval = cur - prev
                if interval < 0:
                    interval += 86400000      # 跨零点
                self._frame_intervals.append(round(interval, 1))
            prev = cur

    def _on_cancel_parse(self):
        """取消解析"""
        worker = self._live_worker()
        if worker is not None and worker.isRunning():
            self._parse_cancelled = True
            worker.cancel()
            self.btn_cancel_parse.setEnabled(False)
            self.statusbar.showMessage("正在取消解析...", 0)
            # 线程在检查点自行退出并回调 _on_parse_finished 呈现部分结果，
            # 不使用 terminate()（强制杀线程可能导致 Qt 内部状态损坏）

    def _on_mode_changed(self, index: int):
        """解析模式变更"""
        self.parse_mode = self.mode_combo.currentData()
        self._auto_detect = (index == 0)
        mode_name = self.mode_combo.currentText()
        self.statusbar.showMessage(f"已切换到 {mode_name}", 2000)

    def _auto_detect_mode(self, text: str) -> ParseMode:
        """自动检测输入格式（判定规则唯一实现见 iec104_parser.detect_input_format）"""
        return detect_input_format(text)

    # ==================================================================
    # 主题切换
    # ==================================================================

    def _toggle_theme(self):
        """切换深色/浅色主题（同步已打开的子对话框）"""
        self._dark_theme = not self._dark_theme
        if self._dark_theme:
            self._apply_dark_theme()
            self.statusbar.showMessage("已切换到深色主题", 2000)
        else:
            self._apply_light_theme()
            self.statusbar.showMessage("已切换到浅色主题", 2000)
        self._sync_open_dialogs_theme()

    def _sync_open_dialogs_theme(self):
        """把当前主题同步到已打开的非模态子对话框"""
        dlg = getattr(self, '_simulator_dialog', None)
        if dlg and dlg.isVisible():
            self._apply_theme_to_dialog(dlg)

    def _apply_dark_theme(self):
        """应用深色主题（样式表统一维护在 gui/style.py）"""
        self.setStyleSheet(DARK_QSS)
        # 重新应用特殊按钮样式
        self.btn_parse.setStyleSheet(
            "QPushButton { background-color: #1976D2; color: white; "
            "font-weight: bold; padding: 6px 16px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background-color: #1565C0; }"
        )
        self.btn_cancel_parse.setStyleSheet(
            "QPushButton { background-color: #c62828; color: white; padding: 2px 8px; border-radius: 3px; border: none; }"
            "QPushButton:hover { background-color: #b71c1c; }"
        )
        self._apply_status_label_colors(dark=True)
        # 下拉弹出列表 hover/选中配色（QSS ::item:hover 对弹出不生效）
        apply_selection_palette(self, dark=True)

    def _apply_light_theme(self):
        """应用浅色主题（恢复基础样式表）"""
        self.setStyleSheet(LIGHT_QSS)
        # 重新应用特殊按钮样式
        self.btn_parse.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; "
            "font-weight: bold; padding: 6px 16px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background-color: #1976D2; }"
        )
        self.btn_cancel_parse.setStyleSheet(
            "QPushButton { background-color: #f44336; color: white; padding: 2px 8px; border-radius: 3px; border: none; }"
            "QPushButton:hover { background-color: #d32f2f; }"
        )
        self._apply_status_label_colors(dark=False)
        apply_selection_palette(self, dark=False)

    def _apply_status_label_colors(self, dark: bool):
        """按主题设置状态文字标签颜色（次要信息在深色下提亮、浅色下正常）"""
        dim = "#999" if dark else "#888"      # 次要信息
        faint = "#aaa" if dark else "#666"    # 更弱信息（行数/计数）
        try:
            self.lbl_input_status.setStyleSheet(f"color: {dim}; font-size: 9pt;")
            self.lbl_line_count.setStyleSheet(
                f"color: {faint}; font-size: 9pt; padding-right: 4px;")
            self.lbl_search_count.setStyleSheet(f"color: {dim}; font-size: 9pt;")
            self.lbl_count.setStyleSheet(f"color: {faint}; padding: 0 8px;")
            self.lbl_version.setStyleSheet(f"color: {dim}; padding: 0 8px;")
        except AttributeError:
            pass  # 初始化早期属性未就绪

    def _apply_theme_to_dialog(self, dialog):
        """将当前主题应用到子对话框（浅色为统一基础表，深色为完整暗色表）"""
        dialog.setStyleSheet(self.styleSheet())
        # 下拉弹出列表 hover/选中配色（QSS ::item:hover 对弹出不生效）
        apply_selection_palette(dialog, self._dark_theme)
        # 同步主题标记（供对话框内部绘制时区分深浅色）
        if hasattr(dialog, "_dark_theme"):
            dialog._dark_theme = self._dark_theme
            if hasattr(dialog, "_on_theme_applied"):
                dialog._on_theme_applied()

    # ==================================================================
    # 多标签页输入
    # ==================================================================

    def _add_input_tab(self, title=None, content=None, require_license=True):
        """添加新的输入标签页"""
        # 菜单/快捷键新建标签页属于受限功能；启动时的首个标签页绕过校验
        if require_license and not self._require_license():
            return None
        tab_widget = QWidget()
        tab_layout = QVBoxLayout(tab_widget)
        tab_layout.setContentsMargins(0, 0, 0, 0)

        hex_input = QPlainTextEdit()
        hex_input.setPlaceholderText(
            "# 在此粘贴 IEC104 十六进制报文\n"
            "# 支持格式: 68 04 01 00 0E 00  或  680401000E00\n"
            "# 每行一条报文，以 # 开头的行为注释"
        )
        hex_input.setFont(QFont("Consolas", 11))
        hex_input.setLineWrapMode(QPlainTextEdit.NoWrap)
        hex_input.setAcceptDrops(False)
        hex_input.textChanged.connect(self._on_input_timer_start)
        hex_input.setContextMenuPolicy(Qt.CustomContextMenu)
        hex_input.customContextMenuRequested.connect(self._on_input_context_menu)
        tab_layout.addWidget(hex_input)

        if content:
            hex_input.setPlainText(content)

        count = len(self._input_tab_list) + 1
        tab_title = title or f"报文 {count}"
        self.input_tabs.addTab(tab_widget, tab_title)
        self._input_tab_list.append((tab_widget, hex_input))
        self.input_tabs.setCurrentIndex(len(self._input_tab_list) - 1)

        # 更新当前 hex_input 引用
        self.hex_input = hex_input
        return hex_input

    def _close_input_tab(self, index: int):
        """关闭输入标签页"""
        if len(self._input_tab_list) <= 1:
            return  # 至少保留一个标签页

        # removeTab 会同步触发 currentChanged → _on_tab_changed，后者按"切换
        # 到目标页"的逻辑把**被关闭页**的解析结果存进目标页索引（此时
        # _input_tab_list/_tab_data 都还没重排），随后重排又整体错位一格，
        # 表现为"关掉 A 后 B 显示的是 A 的报文"，且再切页会把错数据写回。
        # 正确处理：先保存当前页 → 屏蔽信号关闭 → 重排 → 显式加载目标页。
        self._save_current_tab_state()

        blocked = self.input_tabs.blockSignals(True)
        try:
            self.input_tabs.removeTab(index)
        finally:
            self.input_tabs.blockSignals(blocked)

        self._input_tab_list.pop(index)

        old_data = dict(self._tab_data)
        self._tab_data.clear()
        for old_idx, data in old_data.items():
            if old_idx < index:
                self._tab_data[old_idx] = data
            elif old_idx > index:
                self._tab_data[old_idx - 1] = data

        if not self._input_tab_list:
            return
        new_index = min(index, len(self._input_tab_list) - 1)
        self._current_tab_index = new_index
        self.hex_input = self._input_tab_list[new_index][1]
        self._load_tab_state(new_index)

    def _on_tab_changed(self, index: int):
        """标签页切换时保存当前状态并加载目标标签页状态"""
        if not (0 <= index < len(self._input_tab_list)):
            return

        # 保存当前标签页的解析结果
        self._save_current_tab_state()

        # 切换到新标签页
        self._current_tab_index = index
        self.hex_input = self._input_tab_list[index][1]

        # 加载目标标签页的解析结果
        self._load_tab_state(index)

    def _save_current_tab_state(self):
        """保存当前标签页的解析结果"""
        idx = self._current_tab_index
        self._tab_data[idx] = {
            "frames": list(self.all_frames),
            "input_lines": list(getattr(self, '_frame_input_lines', [])),
            "bookmarks": set(self._bookmarks),
        }

    def _load_tab_state(self, index: int):
        """加载指定标签页的解析结果"""
        if index in self._tab_data:
            data = self._tab_data[index]
            # 传副本：_set_frames 后 all_frames 与存档是同一个 list，
            # 之后 _start_parse/_on_clear 里的 all_frames.clear() 会把存档一起清掉
            # （表现为解析中途切标签页，上一页结果丢失）
            self._set_frames(list(data["frames"]))
            self._frame_input_lines = data["input_lines"]
            self._bookmarks = data["bookmarks"]
            self._populate_table()
            self._update_status_stats()
        else:
            # 新标签页，清空结果
            self._set_frames([])
            self._frame_input_lines = []
            self._bookmarks = set()
            self.tree_widget.clear()
            self.table_widget.setRowCount(0)
            self.hex_view.clear()
            self._update_status_stats()


    # ==================================================================
    # 导入/导出
    # ==================================================================

    # _on_import_file 已移至下方"多文件批量导入"区域

    def _visible_frame_indices(self) -> list:
        """当前视图中的帧下标列表（过滤/搜索后），按显示顺序"""
        return [fi for fi in self._view_order if fi >= 0]

    def _on_export_csv(self):
        """导出CSV - 支持筛选后导出"""
        if not self._require_license():
            return
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有可导出的数据")
            return

        visible_rows = self._visible_frame_indices()
        if not visible_rows:
            QMessageBox.information(self, "提示", "没有可见的数据")
            return

        default_name = f"iec104_parsed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        if self._last_dir:
            default_name = os.path.join(self._last_dir, default_name)
        path, _ = QFileDialog.getSaveFileName(self, "导出CSV", default_name,
                                                "CSV文件 (*.csv);;所有文件 (*)")
        if not path:
            return
        self._last_dir = os.path.dirname(path) or self._last_dir
        try:
            content = export_csv(self.all_frames, visible_rows)
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                f.write(content)
            export_count = len(visible_rows)
            total_count = len(self.all_frames)
            msg = f"已导出 {export_count}/{total_count} 条记录" if export_count < total_count else f"已导出 {export_count} 条记录"
            self.statusbar.showMessage(f"{msg}: {os.path.basename(path)}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _on_export_json(self):
        """导出JSON - 支持筛选后导出"""
        if not self._require_license():
            return
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有可导出的数据")
            return

        visible_rows = self._visible_frame_indices()
        if not visible_rows:
            QMessageBox.information(self, "提示", "没有可见的数据")
            return

        default_name = f"iec104_parsed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        if self._last_dir:
            default_name = os.path.join(self._last_dir, default_name)
        path, _ = QFileDialog.getSaveFileName(self, "导出JSON", default_name,
                                                "JSON文件 (*.json);;所有文件 (*)")
        if not path:
            return
        self._last_dir = os.path.dirname(path) or self._last_dir
        try:
            data = export_json(self.all_frames, visible_rows)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            export_count = len(visible_rows)
            total_count = len(self.all_frames)
            msg = f"已导出 {export_count}/{total_count} 条记录" if export_count < total_count else f"已导出 {export_count} 条记录"
            self.statusbar.showMessage(f"{msg}: {os.path.basename(path)}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _on_export_txt(self):
        """导出TXT - 支持筛选后导出"""
        if not self._require_license():
            return
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有可导出的数据")
            return

        visible_rows = self._visible_frame_indices()
        if not visible_rows:
            QMessageBox.information(self, "提示", "没有可见的数据")
            return

        default_name = f"iec104_parsed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        if self._last_dir:
            default_name = os.path.join(self._last_dir, default_name)
        path, _ = QFileDialog.getSaveFileName(self, "导出TXT", default_name,
                                                "文本文件 (*.txt);;所有文件 (*)")
        if not path:
            return
        self._last_dir = os.path.dirname(path) or self._last_dir
        try:
            export_count = len(visible_rows)
            total_count = len(self.all_frames)
            content = export_txt(self.all_frames, visible_rows)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            msg = f"已导出 {export_count}/{total_count} 条记录" if export_count < total_count else f"已导出 {export_count} 条记录"
            self.statusbar.showMessage(f"{msg}: {os.path.basename(path)}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    # ==================================================================
    # 填充解析树（表格视图见 gui/frame_table.py）
    # ==================================================================

    def _populate_tree(self, frame_index: int):
        """填充解析树"""
        self.tree_widget.clear()

        if frame_index < 0 or frame_index >= len(self.all_frames):
            return

        frame = self.all_frames[frame_index]

        # 根节点 (整个报文)
        self._add_tree_item(None, f"报文 #{frame.index}", frame.raw_hex, "",
                           byte_range=(0, frame.apci.total_length if frame.apci else 0))

        # 基本信息
        info_root = self._add_tree_item(None, "基本信息", "", "")

        if frame.original_timestamp:
            self._add_tree_item(info_root, "原始时间戳", frame.original_timestamp, "")
        if frame.original_direction:
            self._add_tree_item(info_root, "原始方向标记", frame.original_direction, "")

        if frame.direction:
            self._add_tree_item(info_root, "发送方向", frame.direction, "")
            self._add_tree_item(info_root, "发送方角色", frame.send_role, "")
            self._add_tree_item(info_root, "接收方角色", frame.recv_role, "")

        # 信号类型
        if frame.signal_label:
            sig_color = QColor(frame.signal_type.color_hex)
            self._add_tree_item(info_root, "信号类型", frame.signal_label,
                                self._signal_type_extra(frame.signal_type), sig_color)

        if frame.error:
            self._add_tree_item(info_root, "错误", frame.error, "解析错误", QColor("#F44336"))
            return

        if frame.description:
            self._add_tree_item(info_root, "描述", frame.description, "")

        # APCI (字节0-5)
        if frame.apci:
            apci = frame.apci
            apci_root = self._add_tree_item(
                None, "APCI (应用规约控制信息)", "", "帧控制信息",
                byte_range=(0, 6))
            self._add_tree_item(apci_root, "起始字节", f"0x{apci.start_byte:02X} (68H)", "固定0x68",
                               byte_range=(0, 1))
            self._add_tree_item(apci_root, "APDU长度", f"{apci.apdu_length} 字节",
                                f"总帧长={apci.total_length}字节",
                                byte_range=(1, 2))
            self._add_tree_item(apci_root, "帧类型", apci.frame_type,
                                self._frame_type_desc(apci.frame_type),
                                byte_range=(2, 6))
            self._add_tree_item(apci_root, "发送序号SN", str(apci.send_seq),
                                "Send Sequence Number")
            self._add_tree_item(apci_root, "接收序号RN", str(apci.recv_seq),
                                "Receive Sequence Number")

            if apci.frame_type == "I-format":
                if hasattr(self, '_last_send_seq') and self._last_send_seq >= 0:
                    expected = (self._last_send_seq + 1) % 32768
                    if apci.send_seq != expected:
                        self._add_tree_item(apci_root,
                            f"⚠ 序列号跳跃: 期望 {expected}, 实际 {apci.send_seq}",
                            "", "序列号不连续")
                self._last_send_seq = apci.send_seq

        # ASDU (字节6+)
        if frame.asdu:
            asdu = frame.asdu
            asdu_end = frame.apci.total_length if frame.apci else 6 + len(asdu.raw_data)
            asdu_root = self._add_tree_item(
                None, "ASDU (应用服务数据单元)", "", "应用数据",
                byte_range=(6, asdu_end))

            sig_color = QColor(asdu.signal_type.color_hex) \
                if asdu.signal_type != SignalType.OTHER else None
            self._add_tree_item(asdu_root, "信号类型",
                                f"{asdu.signal_type.label}",
                                "", sig_color)

            self._add_tree_item(asdu_root, "类型标识",
                                f"{asdu.type_id} ({TypeID.get_name(asdu.type_id)})",
                                TypeID.get_description(asdu.type_id),
                                byte_range=(6, 7))
            self._add_tree_item(asdu_root, "VSQ (可变结构限定词)",
                                f"0x{asdu.vsq:02X}",
                                f"对象数={asdu.number_of_objects}, "
                                f"地址类型={'顺序' if asdu.is_sequence else '离散'}",
                                byte_range=(7, 8))
            self._add_tree_item(asdu_root, "传送原因",
                                f"0x{asdu.cot:04X} ({asdu.cot_cause})",
                                COT.get_description(asdu.cot) +
                                (", 试验" if asdu.cot_test else "") +
                                (", 否定确认" if asdu.cot_pn else ""),
                                byte_range=(8, 10))
            self._add_tree_item(asdu_root, "源发方地址(ORG)",
                                f"0x{asdu.org:02X} ({asdu.org})",
                                "COT高字节/源发方地址")
            self._add_tree_item(asdu_root, "公共地址(ASDU地址)",
                                f"0x{asdu.common_address:04X} ({asdu.common_address})",
                                "公共地址/ASDU地址(2字节)",
                                byte_range=(10, 12))

            # 信息元素
            for i, elem in enumerate(asdu.information_elements):
                elem_sig = elem.signal_type.label
                alias = get_alias(elem.ioa, asdu.common_address)
                alias_str = f" ({alias})" if alias else ""
                elem_root = self._add_tree_item(
                    asdu_root, f"信息元素 #{i+1}",
                    f"IOA=0x{elem.ioa:06X}{alias_str} [{elem_sig}]",
                    "信息对象地址")
                if elem.value_desc:
                    self._add_tree_item(elem_root, "值", elem.value_desc, "")
                if elem.quality:
                    self._add_tree_item(elem_root, "品质描述词",
                                        f"0x{elem.quality.raw:02X}",
                                        str(elem.quality))
                if elem.timestamp:
                    ts_str = elem.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    self._add_tree_item(elem_root, "时标", ts_str,
                                        "CP56Time2a" if len(elem.value_desc) > 50 else "")

            # 补齐字段
            if asdu.pad:
                self._add_tree_item(asdu_root, "补齐字段", f"0x{asdu.pad:02X}", "0补齐对齐")

        # 默认展开前 2 层（根节点 + ASDU 节点），避免信息元素过多时树过长
        for i in range(self.tree_widget.topLevelItemCount()):
            top_item = self.tree_widget.topLevelItem(i)
            top_item.setExpanded(True)
            for j in range(top_item.childCount()):
                top_item.child(j).setExpanded(True)
        self._update_hex_view(frame_index)

    def _signal_type_extra(self, st: SignalType) -> str:
        """信号类型额外说明"""
        return {
            SignalType.YX: "状态量(单点/双点信息)",
            SignalType.YC: "模拟量(测量值)",
            SignalType.YK: "开关控制(命令)",
            SignalType.YT: "调节控制(设定值/步位置)",
            SignalType.YM: "累计量/电能",
            SignalType.OTHER: "",
        }.get(st, "")

    def _add_tree_item(self, parent, col0: str, col1: str, col2: str,
                       fg_color: QColor = None, byte_range: tuple = None) -> QTreeWidgetItem:
        if parent:
            item = QTreeWidgetItem(parent)
        else:
            item = QTreeWidgetItem(self.tree_widget)
        item.setText(0, col0)
        item.setText(1, col1)
        item.setText(2, col2)
        if fg_color:
            for col in range(3):
                item.setForeground(col, fg_color)
        if byte_range:
            item.setData(0, Qt.UserRole, byte_range)
        return item

    def _frame_type_desc(self, ftype: str) -> str:
        if ftype == "I-format":
            return "信息传输格式(编号帧,携带ASDU数据)"
        elif ftype == "S-format":
            return "编号监视格式(确认帧,无ASDU数据)"
        elif ftype == "U-format":
            return "未编号控制格式(启动/停止/测试)"
        return ""

    def _update_hex_view(self, frame_index: int, highlight_start: int = -1, highlight_end: int = -1):
        """更新十六进制视图，可选高亮指定字节范围"""
        if frame_index < 0 or frame_index >= len(self.all_frames):
            return
        frame = self.all_frames[frame_index]
        html = self._format_hex_html(frame.raw_hex, highlight_start, highlight_end)
        title_color = "#999" if self._dark_theme else "#666"
        text_color = "#d4d4d4" if self._dark_theme else "#000"
        self.hex_view.setHtml(
            f"<p style='color:{title_color};font-family:Consolas;font-size:10pt'>"
            f"# 报文 #{frame.index} 原始十六进制</p>"
            f"<p style='color:{text_color};font-family:Consolas;font-size:10pt'>{html}</p>"
        )

    def _format_hex_html(self, raw: str, hl_start: int = -1, hl_end: int = -1) -> str:
        """格式化十六进制为HTML，支持高亮指定字节范围，并附 ASCII 对照列。

        布局：每行 16 字节，每 8 字节多一个分隔，行尾显示 ASCII 可打印字符对照。
        """
        cleaned = raw.replace(" ", "").replace("\n", "")
        cleaned = ''.join(c for c in cleaned if c in '0123456789ABCDEFabcdef')
        if len(cleaned) % 2 != 0:
            return raw

        dim_color = "#777" if self._dark_theme else "#999"   # ASCII 对照列文字
        ascii_dim = "#4a4a4a" if self._dark_theme else "#ddd"  # 不可打印字符占位
        rows = []
        row_bytes = []
        row_texts = []

        def flush_row():
            # 行内 hex 拼接（每 8 字节一个分隔）
            inner = []
            for j, b in enumerate(row_bytes):
                if j > 0 and j % 8 == 0:
                    inner.append("&nbsp;&nbsp;")
                else:
                    inner.append(" ")
                inner.append(b)
            hex_part = "".join(inner).strip()
            ascii_part = "".join(row_texts)
            rows.append(
                f'<span style="color:{dim_color}">|</span>{hex_part}'
                f'<span style="color:{dim_color}">&nbsp;&nbsp;| {ascii_part} |</span>'
            )
            row_bytes.clear()
            row_texts.clear()

        for i in range(0, len(cleaned), 2):
            byte_idx = i // 2
            hex_byte = cleaned[i:i+2].upper()
            # ASCII 对照：可打印显示原字符，否则显示 '.'
            val = int(hex_byte, 16)
            ch = chr(val)
            if 0x20 <= val <= 0x7E:
                ascii_ch = ch.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                ascii_txt = f'<span style="color:{dim_color}">{ascii_ch}</span>'
            else:
                ascii_txt = f'<span style="color:{ascii_dim}">.</span>'

            if hl_start <= byte_idx < hl_end:
                hl_bg = "#5C4B00" if self._dark_theme else "#FFEB3B"
                hl_fg = "#FFD54F" if self._dark_theme else "#000"
                row_bytes.append(
                    f'<span style="background-color:{hl_bg};color:{hl_fg};'
                    f'padding:1px 2px;border-radius:2px">{hex_byte}</span>'
                )
            else:
                row_bytes.append(hex_byte)
            row_texts.append(ascii_txt)

            if len(row_bytes) == 16:
                flush_row()
        if row_bytes:
            flush_row()
        return "<br>".join(rows)


    # ==================================================================
    # 事件处理
    # ==================================================================


    def _on_table_click(self, row: int, col: int):
        """表格行单击 -> 更新解析详情（不切换标签页）"""
        fi = self._frame_idx_at_row(row)
        if not (0 <= fi < len(self.all_frames)):
            return
        self._current_frame_idx = fi
        self._populate_tree(fi)

    def _on_table_double_click(self, row: int, col: int):
        """表格行双击 -> 切换到解析详情标签页"""
        fi = self._frame_idx_at_row(row)
        if not (0 <= fi < len(self.all_frames)):
            return
        self._current_frame_idx = fi
        self._populate_tree(fi)
        self.result_tabs.setCurrentIndex(0)

    def _on_input_context_menu(self, pos):
        """输入区右键菜单"""
        menu = QMenu(self)

        # 根据光标位置定位所在行对应的报文
        cursor = self.hex_input.cursorForPosition(pos)
        cursor_line = cursor.blockNumber()

        line_map = getattr(self, '_frame_input_lines', [])
        found_frame_idx = -1
        for fidx, src_lines in enumerate(line_map):
            if cursor_line in src_lines:
                found_frame_idx = fidx
                break

        if found_frame_idx >= 0 and found_frame_idx < len(self.all_frames):
            frame = self.all_frames[found_frame_idx]
            act_jump = menu.addAction(f"🔍 跳转到报文 #{frame.index} 的解析结果")
            act_jump.triggered.connect(lambda: self._jump_to_frame(found_frame_idx))
            menu.addSeparator()

        selected_text = self.hex_input.textCursor().selectedText()
        if selected_text.strip():
            act_parse_sel = menu.addAction("▶ 解析选中内容")
            act_parse_sel.triggered.connect(lambda: self._on_parse_text(selected_text))
            act_parse = menu.addAction("▶ 解析全部报文 (F5)")
            act_parse.triggered.connect(self._on_parse)
        else:
            act_parse = menu.addAction("▶ 解析报文 (F5)")
            act_parse.triggered.connect(self._on_parse)

        act_clear = menu.addAction("🗑 清空")
        act_clear.triggered.connect(self._on_clear)

        menu.exec_(self.hex_input.mapToGlobal(pos))

    def _jump_to_frame(self, frame_index: int):
        """跳转到指定报文的解析结果"""
        if frame_index < 0 or frame_index >= len(self.all_frames):
            return
        self._current_frame_idx = frame_index
        self._populate_tree(frame_index)
        target_row = self._row_of_frame(frame_index)
        if target_row >= 0:
            self.table_widget.selectRow(target_row)
        self.result_tabs.setCurrentIndex(0)

    def _on_table_context_menu(self, pos):
        """表格右键菜单"""
        row = self.table_widget.rowAt(pos.y())
        if row < 0:
            return
        fi = self._frame_idx_at_row(row)
        if not (0 <= fi < len(self.all_frames)):
            return

        self.table_widget.selectRow(row)
        frame = self.all_frames[fi]

        menu = QMenu(self)

        # 复制原始报文
        act_copy_hex = menu.addAction("📋 复制原始报文")
        act_copy_hex.triggered.connect(lambda: self._copy_to_clipboard(frame.raw_hex))

        # 复制描述
        desc = frame.description if frame.description else frame.error
        if desc:
            act_copy_desc = menu.addAction("📋 复制描述")
            act_copy_desc.triggered.connect(lambda: self._copy_to_clipboard(desc))

        # 复制所有值
        if frame.asdu and frame.asdu.information_elements:
            act_copy_vals = menu.addAction("📋 复制所有值")
            vals = "\n".join(
                f"IOA=0x{e.ioa:06X}: {e.value_desc}"
                for e in frame.asdu.information_elements
            )
            act_copy_vals.triggered.connect(lambda: self._copy_to_clipboard(vals))

        # 复制 IOA 地址
        if frame.asdu and frame.asdu.information_elements:
            copy_ioa_action = menu.addAction("📋 复制 IOA 地址")
            copy_ioa_action.triggered.connect(lambda: self._copy_ioa_to_clipboard())

            # 快捷添加别名
            for elem in frame.asdu.information_elements:
                alias = get_alias(elem.ioa, frame.asdu.common_address)
                if not alias:
                    act_add_alias = menu.addAction(f"✏️ 添加别名 IOA=0x{elem.ioa:06X}")
                    act_add_alias.triggered.connect(lambda checked, ioa=elem.ioa: self._quick_add_alias(ioa))
                    break  # Only show for first unaliased IOA

        menu.addSeparator()

        # 书签标记（书签集合以帧索引存储）
        is_bookmarked = fi in self._bookmarks
        act_bookmark = menu.addAction("取消书签" if is_bookmarked else "📌 添加书签")
        act_bookmark.triggered.connect(lambda: self._toggle_bookmark(row))

        menu.addSeparator()

        # 复制全部解析结果
        act_copy_all = menu.addAction("📋 复制全部解析结果")
        act_copy_all.triggered.connect(lambda: self._copy_all_results())

        menu.addSeparator()

        # 定位原始报文
        act_locate = menu.addAction("📍 定位原始报文")
        act_locate.triggered.connect(lambda: self._locate_original_message(row))

        # 跳转详情
        act_detail = menu.addAction("🔍 查看解析详情")
        act_detail.triggered.connect(lambda: self._show_detail(row))

        # 跳转十六进制视图
        act_hex_view = menu.addAction("🔢 查看十六进制")
        act_hex_view.triggered.connect(lambda: self._show_hex_view(row))

        menu.exec_(self.table_widget.viewport().mapToGlobal(pos))

    def _on_header_context_menu(self, pos):
        """表头右键菜单 - 列显示控制"""
        menu = QMenu(self)
        headers = ["序号", "原始时间", "方向", "信号类型", "类型标识",
                   "传送原因", "公共地址", "对象数", "值", "描述", "输入行", "间隔"]

        for i, header in enumerate(headers):
            if i >= self.table_widget.columnCount():
                break
            action = menu.addAction(header)
            action.setCheckable(True)
            action.setChecked(not self.table_widget.isColumnHidden(i))
            action.triggered.connect(lambda checked, col=i: self._toggle_column(col, checked))

        menu.exec_(self.table_widget.horizontalHeader().mapToGlobal(pos))

    def _toggle_column(self, col: int, visible: bool):
        """切换列显示"""
        self.table_widget.setColumnHidden(col, not visible)

    def _copy_to_clipboard(self, text: str):
        """复制文本到剪贴板"""
        QApplication.clipboard().setText(text)
        self.statusbar.showMessage("已复制到剪贴板", 2000)

    def _copy_ioa_to_clipboard(self):
        """复制选中行的IOA地址到剪贴板"""
        rows = set(idx.row() for idx in self.table_widget.selectedIndexes())
        if not rows:
            return
        ioas = []
        for row in rows:
            fi = self._frame_idx_at_row(row)
            if 0 <= fi < len(self.all_frames):
                frame = self.all_frames[fi]
                if frame.asdu:
                    for elem in frame.asdu.information_elements:
                        ioas.append(f"0x{elem.ioa:06X}")
        if ioas:
            QApplication.clipboard().setText("\n".join(ioas))
            self.statusbar.showMessage(f"已复制 {len(ioas)} 个 IOA 地址", 2000)

    def _copy_all_results(self):
        """复制全部解析结果到剪贴板"""
        if not self.all_frames:
            return
        lines = []
        for frame in self.all_frames:
            line = f"#{frame.index} {frame.direction} {frame.signal_label or ''}"
            if frame.asdu:
                line += f" TID={frame.asdu.type_id}"
                if frame.asdu.information_elements:
                    for elem in frame.asdu.information_elements:
                        alias = get_alias(elem.ioa, frame.asdu.common_address)
                        prefix = f"[{alias}] " if alias else ""
                        line += f" IOA=0x{elem.ioa:06X} {prefix}{elem.value_desc}"
            elif frame.error:
                line += f" ERROR={frame.error}"
            lines.append(line)
        QApplication.clipboard().setText("\n".join(lines))
        self.statusbar.showMessage(f"已复制 {len(self.all_frames)} 条解析结果", 2000)

    def _show_detail(self, row: int):
        """显示指定行的解析详情"""
        fi = self._frame_idx_at_row(row)
        if not (0 <= fi < len(self.all_frames)):
            return
        self._current_frame_idx = fi
        self._populate_tree(fi)
        self.result_tabs.setCurrentIndex(0)

    def _show_hex_view(self, row: int):
        """显示指定行的十六进制视图"""
        fi = self._frame_idx_at_row(row)
        if not (0 <= fi < len(self.all_frames)):
            return
        self._current_frame_idx = fi
        self._update_hex_view(fi)
        self.result_tabs.setCurrentIndex(2)

    def _locate_original_message(self, row: int):
        """定位原始报文 - 滚动到输入区对应位置并高亮"""
        fi = self._frame_idx_at_row(row)
        line_map = getattr(self, '_frame_input_lines', [])
        if fi < 0 or fi >= len(line_map) or not line_map[fi]:
            self.statusbar.showMessage("未找到对应的原始报文位置", 2000)
            return

        self._highlight_input_line_for_frame(fi)
        self.hex_input.setFocus()

        self.statusbar.showMessage(f"已定位到输入区第 {line_map[fi][0] + 1} 行", 2000)

    def _on_focus_search(self):
        """Ctrl+F 聚焦到报文搜索框"""
        self.search_input.setFocus()
        self.search_input.selectAll()

    def _toggle_input_panel(self):
        """折叠/展开原始报文输入面板"""
        self._left_panel_visible = not self._left_panel_visible
        if self._left_panel_visible:
            self.splitter.setSizes([430, 870])
            self._toggle_input_action.setText("◀ 隐藏输入")
        else:
            self.splitter.setSizes([0, self.splitter.width()])
            self._toggle_input_action.setText("▶ 显示输入")

    def _on_tree_select(self):
        """树节点选择时在十六进制视图中高亮对应字节范围"""
        items = self.tree_widget.selectedItems()
        if not items:
            self._update_hex_view(self._current_frame_idx)
            return

        item = items[0]
        byte_range = item.data(0, Qt.UserRole)
        if byte_range:
            self._update_hex_view(self._current_frame_idx,
                                  highlight_start=byte_range[0],
                                  highlight_end=byte_range[1])
        else:
            self._update_hex_view(self._current_frame_idx)

    def _highlight_input_line_for_frame(self, frame_index: int):
        """高亮输入区中对应报文的源行（使用 ExtraSelection，不碰样式表）"""
        line_map = getattr(self, '_frame_input_lines', [])
        doc = self.hex_input.document()

        # 负下标必须显式拦截：空列表/无选中帧时 frame_index 为 -1，
        # 此前会走到 line_map[-1]（高亮最后一条报文，且可能越界构造游标）
        if frame_index < 0 or frame_index >= len(line_map) \
                or not line_map[frame_index]:
            self.hex_input.setExtraSelections([])
            return

        source_lines = line_map[frame_index]
        selections = []

        for line_num in source_lines:
            block = doc.findBlockByLineNumber(line_num)
            if not block.isValid():
                continue

            sel = QTextEdit.ExtraSelection()
            if self._dark_theme:
                sel.format.setBackground(QColor("#5C4B00"))
                sel.format.setForeground(QColor("#FFD54F"))
            else:
                sel.format.setBackground(QColor("#FFEB3B"))
                sel.format.setForeground(QColor("#000000"))
            cursor = QTextCursor(block)
            cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
            sel.cursor = cursor
            selections.append(sel)

        self.hex_input.setExtraSelections(selections)

        # 滚动到第一个高亮行
        if selections:
            self.hex_input.setTextCursor(selections[0].cursor)
            self.hex_input.centerCursor()

    def _apply_input_line_colors(self):
        """解析后为输入区每行添加交替背景色，便于区分各报文"""
        all_text = self.hex_input.toPlainText()
        all_lines = all_text.split("\n")
        line_map = getattr(self, '_frame_input_lines', [])

        # 构建行号 -> 报文索引的映射
        line_to_frame = {}
        for fidx, src_lines in enumerate(line_map):
            for ln in src_lines:
                line_to_frame[ln] = fidx

        # 行数过大时整片着色代价过高（每行都要构造 QTextCursor 与
        # ExtraSelection），直接跳过：着色只是可读性增强，不影响解析结果。
        MAX_COLORED_LINES = 5000
        if len(all_lines) > MAX_COLORED_LINES:
            self.hex_input.setExtraSelections([])
            return

        selections = []
        colors = [QColor("#F5F5F5"), QColor("#FFFFFF")]
        # 线性推进字符偏移：此前在循环内 sum(...for j in range(i)) 是 O(n²)，
        # 5 万行输入需要 12.5 亿次操作，界面直接冻死。
        pos = 0
        for i, line in enumerate(all_lines):
            line_len = len(line)
            if line.strip() and not line.strip().startswith("#"):
                fidx = line_to_frame.get(i, -1)
                if fidx >= 0:
                    sel = QTextEdit.ExtraSelection()
                    sel.format.setBackground(colors[fidx % 2])
                    cursor = self.hex_input.textCursor()
                    cursor.setPosition(pos)
                    cursor.setPosition(pos + line_len, QTextCursor.KeepAnchor)
                    sel.cursor = cursor
                    selections.append(sel)
            pos += line_len + 1   # +1 为换行符

        self.hex_input.setExtraSelections(selections)


    # ==================================================================
    # 拖放文件支持
    # ==================================================================

    def dragEnterEvent(self, event):
        """拖入事件：接受文件拖放"""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    file_path = url.toLocalFile()
                    if file_path.lower().endswith(('.txt', '.log', '.hex', '.dat', '.bin', '.pcap')):
                        event.acceptProposedAction()
                        # 拖放视觉反馈
                        self._drag_overlay = True
                        self.hex_input.setStyleSheet(
                            "QPlainTextEdit { border: 2px dashed #2196F3; background-color: rgba(33,150,243,0.05); }"
                        )
                        return
        event.ignore()

    def dragLeaveEvent(self, event):
        """拖出事件：清除视觉反馈"""
        self._clear_drag_feedback()

    def _clear_drag_feedback(self):
        """清除拖放视觉反馈"""
        self._drag_overlay = False
        self.hex_input.setStyleSheet("")

    def dropEvent(self, event):
        """放下事件：读取拖入的文件（支持多文件合并）"""
        urls = event.mimeData().urls()
        if not urls:
            return
        # 与菜单“导入文件”保持一致的激活门控
        if not self._require_license():
            self._clear_drag_feedback()
            event.ignore()
            return

        loaded_count = failed_count = 0
        all_content = []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for url in urls:
                file_path = url.toLocalFile()
                if not file_path:
                    continue
                name = os.path.basename(file_path)
                content, err = self._read_import_file(file_path)
                if content is not None:
                    all_content.append(content)
                    loaded_count += 1
                else:
                    all_content.append(f"# 导入失败: {name} - {err}")
                    failed_count += 1
        finally:
            QApplication.restoreOverrideCursor()

        if all_content:
            self._clear_drag_feedback()
            self.hex_input.setPlainText("\n".join(all_content))
            msg = f"已拖放导入 {loaded_count} 个文件"
            if failed_count:
                msg += f"，{failed_count} 个失败"
            self.statusbar.showMessage(msg, 3000)
            # 走到这里说明已激活，自动解析
            if loaded_count > 0:
                self._on_parse()

    # ==================================================================
    # 实时输入校验
    # ==================================================================

    def _on_input_timer_start(self):
        """输入变化仅重启防抖定时器，真正的校验由 _on_input_changed 执行"""
        self._input_timer.start()

    def _on_input_changed(self):
        """输入区内容变化时实时校验（忽略注释行与日志格式的时间/方向标记）"""
        text = self.hex_input.toPlainText()
        if not text.strip():
            self.lbl_input_status.setText("")
            return

        # 逐行收集数据行：跳过空行/# 注释/日志格式元数据行（如 "13:25:00.692 RX:"），
        # 标记后带同行十六进制时仅保留 hex 部分（时间戳数字是合法 hex 字符，
        # 混入会把字节数/合法性判断带偏）
        data_lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            m = re.match(r'^[\d\- :.]*?(RX|TX|send|recv)\s*:\s*(.*)$',
                         stripped, re.IGNORECASE)
            if m:
                rest = m.group(2).strip()
                if rest:
                    data_lines.append(rest)
                continue
            data_lines.append(stripped)

        if not data_lines:
            self.lbl_input_status.setText("")
            return

        raw = ''.join(data_lines)
        # 统计有效十六进制字符（忽略空格、点分隔符、0x前缀）
        cleaned = raw.replace(' ', '').replace('\t', '').replace('.', '')
        cleaned = re.sub(r'0[xX]', '', cleaned)
        hex_chars = ''.join(c for c in cleaned if c in '0123456789ABCDEFabcdef')
        non_hex = len(cleaned) - len(hex_chars)

        if non_hex > 0:
            self.lbl_input_status.setText(f"⚠️ 含{non_hex}个非法字符")
            self.lbl_input_status.setStyleSheet("color: #E65100; font-size: 9pt;")
        elif len(hex_chars) % 2 != 0:
            self.lbl_input_status.setText("⚠️ 十六进制长度为奇数")
            self.lbl_input_status.setStyleSheet("color: #E65100; font-size: 9pt;")
        else:
            byte_count = len(hex_chars) // 2
            self.lbl_input_status.setText(f"✓ {byte_count} 字节")
            self.lbl_input_status.setStyleSheet("color: #4CAF50; font-size: 9pt;")

    # ==================================================================
    # 搜索
    # ==================================================================

    def _on_search_text_changed(self, text: str):
        """搜索输入变化时启动防抖定时器"""
        self._search_timer.stop()
        if not text:
            # 空内容立即还原搜索前视图，不等防抖
            self._end_search()
            return
        if self._pre_search_view is None:
            # 首次进入搜索态：快照当前视图作为匹配范围
            self._pre_search_view = list(self._view_order)
        self._search_timer.start()

    def _end_search(self):
        """清空搜索：还原发起搜索前的视图并复位状态"""
        if self._pre_search_view is None:
            self.lbl_search_count.setText("")
            return
        snapshot = self._pre_search_view
        self._pre_search_view = None
        self._set_view(snapshot)
        self.lbl_search_count.setText("")
        if self._view_order and self._view_order[0] >= 0:
            self._current_frame_idx = self._view_order[0]
            self.table_widget.selectRow(0)
            self._populate_tree(self._view_order[0])
        self.table_widget.viewport().update()

    def _do_search(self):
        """实际执行搜索（防抖后触发）：与过滤同构，经 _set_view 重建可见行。

        匹配范围恒为发起搜索前的视图快照（_pre_search_view）——若直接在当前
        _view_order 上匹配，逐字输入会在上一轮结果上递归收窄（如搜 "5" 后
        再搜 "[0-9]" 只剩之前含 5 的行）。此前实现逐行 setRowHidden 并为每行
        构建 12 列单元格（O(N×12)），且行隐藏与过滤/排序互相干扰。
        """
        text = self.search_input.text()
        if not text:
            self._end_search()
            return

        # 防御 ReDoS：搜索框直接把用户输入当正则编译。嵌套量词（如 (a+)+$）
        # 配合较长匹配串会指数级回溯卡死 UI。单元格文本天然很短，主要风险
        # 来自异常长的模式，故限制长度；编译失败（语法非法）再回退字面量。
        if len(text) > 256:
            text = text[:256]
        try:
            pattern = re.compile(text, re.IGNORECASE)
        except re.error:
            pattern = re.compile(re.escape(text), re.IGNORECASE)

        domain = self._pre_search_view
        if domain is None:
            domain = list(self._view_order)
            self._pre_search_view = list(domain)

        ncols = self.table_widget.columnCount()
        display = []
        for fi in domain:
            if fi < 0:
                continue
            if any(pattern.search(self._frame_cell(fi, j)["text"])
                   for j in range(ncols)):
                display.append(fi)

        self._set_view(display, from_search=True)
        self.lbl_search_count.setText(f"找到 {len(display)} 条")

        # 行序已变：原选中帧仍在结果中则重映射选中，否则选中首个匹配
        if display:
            if self._current_frame_idx in self._frame_to_row:
                self.table_widget.selectRow(self._frame_to_row[self._current_frame_idx])
            else:
                self._current_frame_idx = display[0]
                self.table_widget.selectRow(0)
                self._populate_tree(display[0])
        self.table_widget.viewport().update()

    # ==================================================================
    # 序号连续性检查
    # ==================================================================

    def _on_check_sequence(self):
        """检查I帧序号连续性"""
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有已解析的报文，请先解析")
            return

        issues = self.parser.check_sequence()
        if not issues:
            QMessageBox.information(
                self, "序号检查",
                f"✅ 检查通过！共 {len(self.all_frames)} 条报文，未发现序号异常。")
            return

        # 构建详细报告
        report_lines = [f"发现 {len(issues)} 处序号异常：\n"]
        for issue in issues:
            report_lines.append(f"  • {issue['detail']}")

        dialog = QMessageBox(self)
        dialog.setWindowTitle("序号检查结果")
        dialog.setIcon(QMessageBox.Warning)
        dialog.setText(f"发现 {len(issues)} 处序号跳变")
        dialog.setDetailedText("\n".join(report_lines))
        dialog.exec_()

    # ==================================================================
    # 多文件批量导入
    # ==================================================================

    def _on_import_file(self):
        """导入文件（支持多选）"""
        if not self._require_license():
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "导入报文文件", self._last_dir,
            "报文文件 (*.txt *.log *.hex *.dat *.bin *.pcap);;所有文件 (*.*)"
        )
        if not paths:
            return
        self._last_dir = os.path.dirname(paths[0]) or self._last_dir

        if len(paths) == 1:
            # 单文件：沿用原逻辑
            self._load_single_file(paths[0])
        else:
            # 多文件：合并内容
            all_content = []
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                for path in paths:
                    content, err = self._read_import_file(path)
                    if content is not None:
                        all_content.append(content)
                    else:
                        all_content.append(
                            f"# 读取失败: {os.path.basename(path)} - {err}")
            finally:
                QApplication.restoreOverrideCursor()

            self.hex_input.setPlainText("\n".join(all_content))
            self.statusbar.showMessage(f"已合并导入 {len(paths)} 个文件", 3000)

    def _read_import_file(self, path: str):
        """读取导入文件（带大小上限，防止大文件卡死界面）

        Returns:
            (content, error): 成功返回 (文本内容(含文件头注释), None)；
              文本读取失败时按二进制转hex行；失败返回 (None, 错误信息)。
        """
        try:
            size = os.path.getsize(path)
        except OSError as e:
            return None, str(e)

        # PCAP
        if path.lower().endswith('.pcap'):
            if size > MAX_BIN_IMPORT_BYTES:
                return None, (f"文件过大 "
                              f"({size // (1024 * 1024)}MB > 上限 "
                              f"{MAX_BIN_IMPORT_BYTES // (1024 * 1024)}MB)")
            try:
                from pcap_importer import pcap_to_hex_text
                text = pcap_to_hex_text(path)
            except Exception as e:
                return None, str(e)
            if not text:
                return None, "未找到 IEC104 报文"
            return f"# === 文件: {os.path.basename(path)} ===\n{text}", None

        # 文本（超限直接拒绝，不读入）；utf-8 失败回退 GBK（工控机日志常见编码）
        if size <= MAX_TEXT_IMPORT_BYTES:
            content = None
            for enc in ("utf-8", "gbk"):
                try:
                    with open(path, "r", encoding=enc) as f:
                        content = f.read()
                    break
                except UnicodeDecodeError:
                    continue  # 换下一种编码
                except OSError:
                    break      # 文本打开失败 → 尝试按二进制处理
            if content is not None:
                header = f"# === 文件: {os.path.basename(path)} ===\n"
                return header + content, None

        # 二进制转 hex 行
        limit = min(MAX_BIN_IMPORT_BYTES, MAX_TEXT_IMPORT_BYTES)
        if size > limit:
            return None, (f"文件过大 ({size // (1024 * 1024)}MB > 上限 "
                          f"{limit // (1024 * 1024)}MB)")
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as e:
            return None, str(e)
        hex_lines = [raw[i:i + 16].hex(" ").upper() for i in range(0, len(raw), 16)]
        header = f"# === 文件: {os.path.basename(path)} ===\n"
        return header + "\n".join(hex_lines), None

    def _load_single_file(self, path: str):
        """加载单个文件"""
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            content, err = self._read_import_file(path)
        finally:
            QApplication.restoreOverrideCursor()

        if content is None:
            if err == "未找到 IEC104 报文":
                QMessageBox.warning(self, "导入失败", "PCAP 文件中未找到 IEC104 报文")
            else:
                QMessageBox.critical(self, "导入失败", f"无法读取文件:\n{err}")
            return

        self.hex_input.setPlainText(content)
        suffix = " (PCAP)" if path.lower().endswith('.pcap') else ""
        self.statusbar.showMessage(
            f"已导入{suffix}: {os.path.basename(path)}", 3000)

    # ==================================================================
    # 统计图表
    # ==================================================================

    def _on_show_stats(self):
        """显示统计图表"""
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有已解析的报文，请先解析")
            return
        dlg = StatsDialog(self.all_frames, self)
        self._apply_theme_to_dialog(dlg)
        dlg.exec_()

    # ==================================================================
    # 协议合规检查
    # ==================================================================

    def _on_check_compliance(self):
        """协议合规性检查"""
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有已解析的报文，请先解析")
            return

        issues = self.parser.check_compliance()
        if not issues:
            QMessageBox.information(
                self, "协议合规检查",
                f"✅ 检查通过！共 {len(self.all_frames)} 条报文，未发现协议违规。")
            return

        # 按严重程度分组
        errors = [i for i in issues if i.get("severity") == "error"]
        warnings = [i for i in issues if i.get("severity") == "warning"]
        infos = [i for i in issues if i.get("severity") == "info"]

        report = []
        report.append(f"共发现 {len(issues)} 个问题：")
        if errors:
            report.append(f"  ❌ 错误: {len(errors)}")
        if warnings:
            report.append(f"  ⚠️ 警告: {len(warnings)}")
        if infos:
            report.append(f"  ℹ️ 提示: {len(infos)}")
        report.append("")
        for issue in issues:
            icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(issue["severity"], "•")
            report.append(f"  {icon} {issue['detail']}")

        dialog = QMessageBox(self)
        dialog.setWindowTitle("协议合规检查结果")
        dialog.setIcon(QMessageBox.Warning if errors else QMessageBox.Information)
        dialog.setText(f"发现 {len(issues)} 个合规问题")
        dialog.setDetailedText("\n".join(report))
        dialog.exec_()

    # ==================================================================
    # 报文配对分析
    # ==================================================================

    def _on_find_pairs(self):
        """报文配对分析"""
        if not self.all_frames:
            QMessageBox.information(self, "提示", "没有已解析的报文，请先解析")
            return

        pairs = self.parser.find_pairs()
        if not pairs:
            QMessageBox.information(self, "配对分析", "未发现可配对的报文（总召/遥控/时钟同步）")
            return

        lines = [f"共发现 {len(pairs)} 组配对关系：\n"]
        for p in pairs:
            req_idx = f"#{p['request'].index}" if p['request'] else "无"
            resp_idx = f"#{p['response'].index}" if p['response'] else "无"
            status = p['status']
            icon = "✅" if status in ("确认", "完成") else "⚠️"
            lines.append(
                f"  {icon} [{p['type']}] 请求:{req_idx} → 响应:{resp_idx} ({status})")

        dialog = QMessageBox(self)
        dialog.setWindowTitle("报文配对分析")
        dialog.setIcon(QMessageBox.Information)
        dialog.setText(f"发现 {len(pairs)} 组配对")
        dialog.setDetailedText("\n".join(lines))
        dialog.exec_()

    # ==================================================================
    # IOA地址别名
    # ==================================================================

    def _on_manage_aliases(self):
        """打开IOA别名管理对话框"""
        dlg = IOAAliasDialog(self)
        self._apply_theme_to_dialog(dlg)
        dlg.exec_()
        # 刷新当前显示（保留过滤/搜索条件，不退回全量视图）
        if self.all_frames:
            self._apply_current_view()
            if self._current_frame_idx < len(self.all_frames):
                self._populate_tree(self._current_frame_idx)

    def _open_generator(self):
        """打开帧生成器对话框"""
        if not self._require_license():
            return
        from gui.generator_dialog import GeneratorDialog
        dialog = GeneratorDialog(self)
        self._apply_theme_to_dialog(dialog)
        if dialog.exec_() == QDialog.Accepted:
            pass

    def _open_compare(self):
        """打开报文对比"""
        if not self._require_license():
            return
        from gui.compare_dialog import CompareDialog
        dialog = CompareDialog(self)
        self._apply_theme_to_dialog(dialog)
        dialog.exec_()

    def _open_simulator(self):
        """打开通信模拟器（非模态，可同时操作主窗口）"""
        if not self._require_license():
            return
        # 防止重复打开
        if hasattr(self, '_simulator_dialog') and self._simulator_dialog and self._simulator_dialog.isVisible():
            self._simulator_dialog.activateWindow()
            return
        from gui.simulator_dialog import SimulatorDialog
        self._simulator_dialog = SimulatorDialog(self)
        self._apply_theme_to_dialog(self._simulator_dialog)
        self._simulator_dialog.setAttribute(Qt.WA_DeleteOnClose, False)
        self._simulator_dialog.show()

    # ==================================================================
    # 书签功能
    # ==================================================================

    def _toggle_bookmark(self, row: int):
        """切换书签状态（入参为视图行号，书签集合以帧索引存储）"""
        fi = self._frame_idx_at_row(row)
        frame = self.all_frames[fi] if 0 <= fi < len(self.all_frames) else None
        display = str(frame.index) if frame else str(fi + 1)
        if fi in self._bookmarks:
            self._bookmarks.discard(fi)
            self.statusbar.showMessage(f"已取消报文 #{display} 的书签", 2000)
        else:
            self._bookmarks.add(fi)
            self.statusbar.showMessage(f"已添加报文 #{display} 的书签", 2000)
        target_row = self._row_of_frame(fi)
        if target_row >= 0:
            self._update_bookmark_visual(target_row)

    def _update_bookmark_visual(self, row: int):
        """更新书签行的视觉效果——刷新该行，由 _cell(列0) 依据书签集合画 📌 前缀"""
        self.table_widget.viewport().update()

    def _select_bookmark_row(self, frame_idx: int):
        """按帧索引选中对应表格行并刷新详情"""
        row = self._row_of_frame(frame_idx)
        if row >= 0:
            self.table_widget.selectRow(row)
            self._on_table_click(row, 0)

    def _goto_next_bookmark(self):
        """跳转到下一个书签"""
        if not self._bookmarks:
            self.statusbar.showMessage("没有书签，按 Space 添加", 2000)
            return
        current_fi = self._frame_idx_at_row(self.table_widget.currentRow())
        for bm in sorted(self._bookmarks):
            if bm > current_fi:
                self._select_bookmark_row(bm)
                return
        # Wrap around
        self._select_bookmark_row(sorted(self._bookmarks)[0])

    def _goto_prev_bookmark(self):
        """跳转到上一个书签"""
        if not self._bookmarks:
            self.statusbar.showMessage("没有书签，按 Space 添加", 2000)
            return
        current_fi = self._frame_idx_at_row(self.table_widget.currentRow())
        for bm in sorted(self._bookmarks, reverse=True):
            if bm < current_fi:
                self._select_bookmark_row(bm)
                return
        # Wrap around（回绕到最大书签，此前误用 [0] 跳到最小）
        self._select_bookmark_row(sorted(self._bookmarks)[-1])

    # ==================================================================
    # 快捷添加IOA别名
    # ==================================================================

    def _quick_add_alias(self, ioa: int):
        """快捷添加IOA别名"""
        from gui.ioa_alias_dialog import save_aliases, load_aliases
        alias, ok = QInputDialog.getText(self, "添加别名",
                                          f"为 IOA=0x{ioa:06X} 设置别名:")
        if ok and alias:
            aliases = load_aliases()
            aliases[f"0x{ioa:06X}"] = alias
            try:
                save_aliases(aliases)
            except OSError as e:
                # 槽函数里抛未捕获异常会被 PyQt5 当致命错误；写盘失败
                # （只读目录/占用）只提示，不带走整个程序
                QMessageBox.warning(self, "保存失败", f"别名写入失败: {e}")
                return
            self.statusbar.showMessage(f"已设置别名: 0x{ioa:06X} -> {alias}", 2000)
            # 刷新显示（保留过滤/搜索条件）
            self._apply_current_view()

    # ==================================================================
    # 键盘导航
    # ==================================================================

    def _table_key_press(self, event):
        """表格键盘导航"""
        current_row = self.table_widget.currentRow()
        # 视图行 → 帧下标：过滤/搜索/排序后视图行号 ≠ 帧下标，用 len(all_frames)
        # 做上界判断会把有效行判成越界（反之亦然），统一走映射函数
        fi = self._frame_idx_at_row(current_row)
        valid_row = fi >= 0

        if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            # Enter: 显示解析详情
            if valid_row:
                self._show_detail(current_row)
        elif event.key() == Qt.Key_F2:
            # F2: 定位原始报文
            if valid_row:
                self._locate_original_message(current_row)
        elif event.key() == Qt.Key_Space:
            # Space: 切换书签
            if valid_row:
                self._toggle_bookmark(current_row)
        else:
            QTableWidget.keyPressEvent(self.table_widget, event)

    def closeEvent(self, event):
        """关闭窗口时安全停止后台线程并保存界面状态"""

        # 模拟器对话框是非模态子窗口且 WA_DeleteOnClose=False，主窗口析构
        # 子对象时 Qt **不会**触发它的 closeEvent → 其中 sim.stop() /
        # _save_config() 全部不执行，退出时报
        # "QThread: Destroyed while thread is still running"（可能 abort）。
        sim_dlg = getattr(self, '_simulator_dialog', None)
        if sim_dlg is not None:
            sim_dlg.close()

        worker = self._live_worker()
        if worker is not None and worker.isRunning():
            worker.cancel()
            # 断开信号，避免线程收尾时回调已半销毁的控件。
            # 逐个 try：某个信号本就没连接时 disconnect 抛 TypeError，
            # 写在同一个 try 里会让后面的信号继续连着。
            for sig in (worker.parse_finished, worker.progress_updated,
                        worker.parse_error):
                try:
                    sig.disconnect()
                except TypeError:
                    pass
            # 等待线程自行退出（不用 terminate：强杀线程有崩溃风险）。
            # 但 wait 超时后若直接 accept 退出，QThread 仍可能在运行中被
            # 析构 → abort；此时 terminate 是唯一可行兜底（退出前一刻，
            # 可接受）。
            if not worker.wait(5000):
                logger.warning("解析线程 5s 内未退出，强制终止")
                worker.terminate()
                worker.wait(500)
        if worker is not None:
            # 此前从不回收：旧线程仍可能被 GC 时销毁于运行中
            worker.deleteLater()
            self._parse_worker = None

        self._save_ui_settings()
        event.accept()

    # ==================================================================
    # 界面设置持久化（窗口大小/分栏/主题/列宽/最近目录）
    # ==================================================================

    def _load_ui_settings(self):
        """启动时恢复上次关闭时的界面状态"""
        try:
            s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)

            geometry = s.value("ui/geometry")
            if geometry is not None:
                self.restoreGeometry(geometry)

            splitter_state = s.value("ui/splitter")
            if splitter_state is not None:
                self.splitter.restoreState(splitter_state)

            # 主题不再持久化，始终以浅色主题启动（深色仅当前会话手动切换）
            # 清除旧版遗留的深色主题记录，避免界面意外变黑
            if s.contains("ui/dark_theme"):
                s.remove("ui/dark_theme")
            if self._dark_theme:
                self._dark_theme = False
                self._apply_light_theme()

            widths = s.value("ui/col_widths")
            if isinstance(widths, (list, tuple)) and widths:
                try:
                    saved = [int(float(w)) for w in widths]
                    if len(saved) == self.table_widget.columnCount():
                        self._saved_col_widths = saved
                except (TypeError, ValueError):
                    self._saved_col_widths = None

            last_dir = s.value("ui/last_dir", "")
            if isinstance(last_dir, str):
                self._last_dir = last_dir
        except Exception as e:
            logger.warning(f"恢复界面设置失败: {e}")

    def _save_ui_settings(self):
        """关闭时保存界面状态"""
        try:
            s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
            s.setValue("ui/geometry", self.saveGeometry())
            s.setValue("ui/splitter", self.splitter.saveState())
            s.setValue("ui/last_dir", self._last_dir)
            widths = [self.table_widget.columnWidth(c)
                      for c in range(self.table_widget.columnCount())]
            s.setValue("ui/col_widths", widths)
        except Exception as e:
            logger.warning(f"保存界面设置失败: {e}")


    def _on_about(self):
        """版本信息对话框"""
        dlg = AboutDialog(APP_TITLE, APP_VERSION)
        self._apply_theme_to_dialog(dlg)
        dlg.exec_()

    def _on_ioa_help(self):
        """IOA 地址说明对话框"""
        from gui.about_dialog import IOAHelpDialog
        IOAHelpDialog(self).exec_()

    def _check_license(self):
        """检查授权状态"""
        from license import get_license_status
        status = get_license_status()
        self._is_activated = status["activated"]
        self._is_license_expired = status["expired"]
        self._license_days_remaining = status.get("days_remaining")
        self._license_expiry_date = status.get("expiry_date", "")
        if status["expired"]:
            self.setWindowTitle(f"{APP_TITLE} [授权已到期 - 功能已锁定]")
        elif not self._is_activated:
            self.setWindowTitle(f"{APP_TITLE} [未激活 - 仅支持单条解析]")
        else:
            self.setWindowTitle(APP_TITLE)
        self._update_license_label()

    def _update_license_label(self):
        """更新状态栏授权常驻按钮的文字与颜色"""
        from license import get_license_status
        st = get_license_status()
        if st["activated"]:
            if st.get("permanent"):
                self.btn_license_state.setText("已激活")
                color, weight = LICENSE_COLOR_OK, "normal"
            else:
                days = st.get("days_remaining")
                exp = st.get("expiry_date", "")
                text = f"剩{days}天" if days is not None else (exp or "限时授权")
                if days is not None and days <= 3:
                    color, weight = LICENSE_COLOR_DANGER, "bold"
                elif days is not None and days <= 7:
                    color, weight = LICENSE_COLOR_WARN, "bold"
                else:
                    color, weight = LICENSE_COLOR_OK, "normal"
                self.btn_license_state.setText(text)
        elif st["expired"]:
            self.btn_license_state.setText("授权已到期")
            color, weight = LICENSE_COLOR_DANGER, "bold"
        else:
            self.btn_license_state.setText("未激活")
            color, weight = LICENSE_COLOR_WARN, "bold"
        self.btn_license_state.setStyleSheet(
            f"QPushButton {{ border: none; color: {color}; font-weight: {weight}; "
            f"padding: 0 8px; background: transparent; }}"
            f"QPushButton:hover {{ color: #1565C0; }}")

    def _may_prompt_expired(self):
        """启动时若授权已到期则锁定提示；限时授权临近到期则红色预警"""
        from license import get_license_status
        st = get_license_status()
        if st["expired"]:
            QMessageBox.warning(
                self, "授权已到期",
                "您的软件授权已到期，受限功能已被锁定。\n\n"
                "请联系开发者获取新的激活码，在 帮助 → 软件激活 "
                "中输入即可恢复全部功能。")
            return
        # 剩余 ≤3 天的红色预警（永久/未激活不弹）
        if st["activated"] and not st.get("permanent"):
            days = st.get("days_remaining")
            if days is not None and days <= 3:
                QMessageBox.warning(
                    self, "授权即将到期",
                    f"您的软件授权将于 {days} 天后到期（{st.get('expiry_date', '')}）。\n\n"
                    "到期后受限功能将被锁定，请尽快联系开发者续期。\n"
                    "在 帮助 → 软件激活 中输入新激活码即可完成续期。")

    def _require_license(self) -> bool:
        """检查是否需要激活，未激活/已过期则提示并返回False"""
        if self._is_activated:
            return True
        box = QMessageBox(self)
        if self._is_license_expired:
            box.setWindowTitle("授权已到期")
            box.setIcon(QMessageBox.Warning)
            box.setText("您的软件授权已到期，功能已被锁定。\n\n"
                        "请联系开发者获取新的激活码，在 帮助 → 软件激活 中输入即可恢复。")
        else:
            box.setWindowTitle("功能限制")
            box.setIcon(QMessageBox.Information)
            box.setText("此功能需要激活后使用。\n\n"
                        "将硬件指纹发送给开发者即可获取激活码。")
        box.addButton("打开激活窗口", QMessageBox.ActionRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec_()
        if box.buttonRole(box.clickedButton()) == QMessageBox.ActionRole:
            self._open_license_dialog()
        return False

    def _open_license_dialog(self):
        """打开激活对话框"""
        from gui.license_dialog import LicenseDialog
        dialog = LicenseDialog(self)
        self._apply_theme_to_dialog(dialog)
        dialog.exec_()
        # 重新检查授权状态
        old_status = self._is_activated
        self._check_license()
        if self._is_activated and not old_status:
            self.setWindowTitle(APP_TITLE)
            QMessageBox.information(self, "激活成功", "软件已激活，所有功能已解锁！")

    def _open_history_manager(self):
        """打开历史报文管理"""
        if not self._require_license():
            return
        from gui.history_dialog import HistoryDialog
        dialog = HistoryDialog(self)
        self._apply_theme_to_dialog(dialog)
        if dialog.exec_() == QDialog.Accepted and dialog.selected_session:
            session = dialog.selected_session
            self.hex_input.setPlainText(session.get('input_text', ''))
            mode_str = session.get('parse_mode', 'normal')
            from iec104_parser import ParseMode
            mode_map = {'normal': ParseMode.NORMAL, 'xn3000': ParseMode.XN3000, 'xn8000': ParseMode.XN8000}
            self.parse_mode = mode_map.get(mode_str, ParseMode.NORMAL)
            idx = self.mode_combo.findData(self.parse_mode)
            if idx >= 0:
                self.mode_combo.setCurrentIndex(idx)
            self.statusbar.showMessage("已加载历史会话，按 F5 或点击「解析报文」开始解析", 3000)

    def _on_filter(self):
        """应用过滤"""
        signal = self.filter_signal.currentData()
        type_id = self.filter_type.currentData()
        cot = self.filter_cot.currentData()
        addr_text = self.filter_addr.text().strip()
        direction = self.filter_direction.currentData()
        time_from = self.filter_time_from.text().strip()
        time_to = self.filter_time_to.text().strip()

        filtered = self.all_frames
        if signal:
            filtered = [f for f in filtered if f.signal_label == signal]
        if type_id != -1 and type_id is not None:
            filtered = [f for f in filtered if f.asdu and f.asdu.type_id == type_id]
        if cot != -1 and cot is not None:
            filtered = [f for f in filtered
                        if f.asdu and f.asdu.cot_cause == cot]
        if addr_text:
            try:
                addr = int(addr_text, 0)   # 支持十进制与 0x 前缀十六进制
            except ValueError:
                addr = None
            if addr is None:
                self.statusbar.showMessage(
                    f"公共地址 '{addr_text}' 格式无效，已忽略该筛选条件", 4000)
            else:
                filtered = [f for f in filtered
                            if f.asdu and f.asdu.common_address == addr]

        # 方向过滤
        if direction:
            filtered = [f for f in filtered if f.direction == direction]

        # 时间范围过滤（非法输入给出提示并忽略该条件，不抛异常）
        if time_from or time_to:
            def parse_filter_time(t):
                parts = t.replace(',', '.').split(':')
                if len(parts) == 3:
                    try:
                        h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
                    except ValueError:
                        return None
                    return h * 3600 + m * 60 + s
                return None

            t_from = parse_filter_time(time_from) if time_from else None
            t_to = parse_filter_time(time_to) if time_to else None
            if (time_from and t_from is None) or (time_to and t_to is None):
                self.statusbar.showMessage(
                    "时间格式无效，请使用 HH:MM:SS（如 13:25:00.500），该条件已忽略", 4000)

            def time_in_range(frame):
                if not frame.original_timestamp:
                    return False
                t = parse_filter_time(frame.original_timestamp)
                if t is None:
                    return False
                if t_from is not None and t < t_from:
                    return False
                if t_to is not None and t > t_to:
                    return False
                return True

            filtered = [f for f in filtered if time_in_range(f)]

        # IOA 范围过滤（非法输入给出提示并忽略）
        ioa_invalid = False
        try:
            ioa_from = int(self.ioa_from_input.text(), 0) if self.ioa_from_input.text() else None
        except ValueError:
            ioa_from = None
            ioa_invalid = True
        try:
            ioa_to = int(self.ioa_to_input.text(), 0) if self.ioa_to_input.text() else None
        except ValueError:
            ioa_to = None
            ioa_invalid = True
        if ioa_invalid:
            self.statusbar.showMessage(
                "IOA 范围格式无效，已忽略该筛选条件（支持十进制或 0x 前缀）", 4000)

        if ioa_from is not None or ioa_to is not None:
            def ioa_in_range(elem):
                if ioa_from is not None and ioa_to is not None:
                    return ioa_from <= elem.ioa <= ioa_to
                elif ioa_from is not None:
                    return elem.ioa >= ioa_from
                elif ioa_to is not None:
                    return elem.ioa <= ioa_to
                return True
            filtered = [f for f in filtered
                        if f.asdu and f.asdu.information_elements
                        and any(ioa_in_range(e) for e in f.asdu.information_elements)]

        total_count = len(self.all_frames)

        # 过滤生效状态可视化：先置标志再刷新统计，保证统计栏同步显示"过滤中"前缀
        has_criteria = bool(
            signal or direction or addr_text or time_from or time_to
            or ioa_from is not None or ioa_to is not None
            or (type_id != -1 and type_id is not None)
            or (cot != -1 and cot is not None))
        self._filter_active = has_criteria
        self._filter_counts = (len(filtered), total_count) if has_criteria else None

        # 显示子集：用匹配帧在 all_frames 中的索引重建 _view_order。
        # all_frames 恒为全量，点击/导出经 _frame_idx_at_row 映射到正确帧（修复旧实现
        # 在过滤后点击/导出错位的隐患）。
        if has_criteria:
            frame_to_idx = {id(f): i for i, f in enumerate(self.all_frames)}
            display = [frame_to_idx[id(f)] for f in filtered]
            self._set_view(display)
        else:
            self._set_view(list(range(total_count)))
        self._update_status_stats()
        # 过滤后自动选中首行并刷新解析详情；行 0 显示的是 _view_order[0]，
        # 树必须取同一帧（此前写死 0，非恒等视图下详情与选中行错位）
        if self._view_order and self._view_order[0] >= 0:
            first_fi = self._view_order[0]
            self._current_frame_idx = first_fi
            self.table_widget.selectRow(0)
            self._populate_tree(first_fi)

        self.filter_btn.setStyleSheet(
            FILTER_BTN_STYLE_ACTIVE if has_criteria else FILTER_BTN_STYLE_IDLE)
        self.filter_btn.setToolTip(
            "⚠ 过滤生效中（条件仍保留），点「重置」恢复全部"
            if has_criteria else "按条件过滤报文列表")
        self.statusbar.showMessage(
            f"过滤结果: {len(filtered)}/{total_count} 条报文", 3000)

    def _on_filter_reset(self):
        """重置过滤"""
        self.filter_signal.setCurrentIndex(0)
        self.filter_type.setCurrentIndex(0)
        self.filter_cot.setCurrentIndex(0)
        self.filter_addr.clear()
        self.filter_direction.setCurrentIndex(0)
        self.filter_time_from.clear()
        self.filter_time_to.clear()
        self.ioa_from_input.clear()
        self.ioa_to_input.clear()
        self._populate_table()   # 内部会复位过滤状态与按钮外观
        self._update_status_stats()
        self.statusbar.showMessage("过滤已重置", 2000)
