"""主窗口解析流程回归测试（离屏）。

覆盖：
- 解析线程结束被 deleteLater 回收后，再次解析/取消不得崩溃
  （曾因 self._parse_worker 悬空指向已销毁的 C++ 对象，槽内抛
  RuntimeError → PyQt5 qFatal → 整个程序闪退）
- 右侧详情打开状态下修改输入并重新解析，流程正常
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication
_APP = QApplication.instance() or QApplication([])

from gui.main_window import MainWindow


def _pump(sec: float):
    end = time.time() + sec
    while time.time() < end:
        _APP.processEvents()
        time.sleep(0.01)


def _make_window() -> MainWindow:
    w = MainWindow()
    # 离屏测试：屏蔽授权弹窗，避免模态对话框阻塞事件循环
    w._may_prompt_expired = lambda: None
    w._open_license_dialog = lambda: None
    w._is_activated = True
    return w


class TestReparseAfterWorkerRecycled:
    """解析线程回收后再解析：曾致整个程序闪退的回归"""

    def test_second_parse_after_finished(self):
        w = _make_window()
        try:
            w.hex_input.setPlainText(
                "68 04 07 00 00 00\n68 04 13 00 00 00")
            w._on_parse()
            _pump(3.0)
            assert not w._is_parsing, "首次解析应结束"
            assert len(w.all_frames) >= 1

            # 让事件循环处理 finished→deleteLater，回收 C++ 线程对象
            _pump(0.3)

            # 回归点：回收后再解析不得抛 RuntimeError（曾致 qFatal 闪退）
            w._on_parse()
            _pump(3.0)
            assert not w._is_parsing, "第二次解析应正常结束"
            assert len(w.all_frames) >= 1
        finally:
            w._save_ui_settings = lambda: None   # 测试不写真实用户配置
            w.close()

    def test_cancel_after_finished(self):
        w = _make_window()
        try:
            w.hex_input.setPlainText("68 04 07 00 00 00")
            w._on_parse()
            _pump(3.0)
            _pump(0.3)
            # 回归点：回收后点"取消解析"不得崩溃（按钮此时本已隐藏，防御）
            w._on_cancel_parse()
        finally:
            w._save_ui_settings = lambda: None
            w.close()

    def test_reparse_with_detail_open(self):
        """打开右侧详情 → 修改输入 → 重新解析（用户报告的闪退操作序列）"""
        w = _make_window()
        try:
            text = ("14:13:08.832 RX: 68 0E 04 00 0C 00 2D 01 06 00 01 00 01 60 00 81\n"
                    "14:13:09.169 TX: 68 0E 0C 00 06 00 2D 01 07 00 01 00 01 60 00 81")
            w.hex_input.setPlainText(text)
            w._on_parse()
            _pump(3.0)
            assert len(w.all_frames) >= 1

            # 右侧点开一个解析完的报文
            w._on_table_double_click(0, 0)
            _pump(0.2)

            # 左侧删掉一条报文
            w.hex_input.setPlainText(
                "14:13:08.832 RX: 68 0E 04 00 0C 00 2D 01 06 00 01 00 01 60 00 81")
            _pump(0.2)

            # 点解析报文：不得崩溃
            w._on_parse()
            _pump(3.0)
            assert not w._is_parsing
            assert len(w.all_frames) >= 1
        finally:
            w._save_ui_settings = lambda: None
            w.close()


class TestTabAndViewState:
    """标签页数据隔离与"清空"后的视图状态回归"""

    def test_close_tab_keeps_target_tab_data(self):
        """关闭标签页不得把被关闭页的解析结果写进目标页

        此前 removeTab 同步触发 currentChanged，未重排的索引让目标页拿到
        的是被关闭页的数据（"关掉 A 后 B 显示 A 的报文"）。
        """
        w = _make_window()
        try:
            w.hex_input.setPlainText("68 04 07 00 00 00")
            w._on_parse()
            _pump(2.5)
            count_a = len(w.all_frames)

            w._add_input_tab(title="B")
            w.hex_input.setPlainText(
                "68 04 07 00 00 00\n68 04 13 00 00 00\n68 04 43 00 00 00")
            w._on_parse()
            _pump(2.5)
            count_b = len(w.all_frames)
            assert count_a == 1 and count_b == 3

            w.input_tabs.setCurrentIndex(0)
            _pump(0.2)
            assert len(w.all_frames) == count_a

            w._close_input_tab(0)
            _pump(0.2)
            assert len(w.all_frames) == count_b, "关闭 A 后 B 的报文被 A 覆盖"
        finally:
            w._save_ui_settings = lambda: None
            w.close()

    def test_clear_resets_view_and_filter_state(self):
        """清空后视图/过滤/搜索状态必须复位（否则出现'幽灵空行'）"""
        w = _make_window()
        try:
            w.hex_input.setPlainText(
                "68 04 07 00 00 00\n68 04 13 00 00 00\n68 04 43 00 00 00")
            w._on_parse()
            _pump(2.5)
            assert len(w.all_frames) == 3

            w._on_clear()
            _pump(0.1)
            assert w.all_frames == []
            assert w._view_order == [-1]          # 仅空态占位行
            assert w._filter_active is False
            assert w._pre_search_view is None
        finally:
            w._save_ui_settings = lambda: None
            w.close()


class TestClearThenParseAgain:
    """解析 → 清空 → 导入新报文 → 再解析（用户报告的闪退场景）"""

    LOG = ("17:01:02.612 RX:\n68 04 0B 00 00 00\n\n"
           "17:01:02.725 TX:\n68 04 43 00 00 00\n")
    PLAIN = ("68 0e 00 00 00 00 01 03 00 01 00 01 40 00 46 02 00\n"
             "68 0e 02 00 00 00 03 03 00 01 00 02 40 00 46 02 00\n")

    def test_auto_detect_falls_back_to_normal(self):
        """解析过日志格式后再解析纯十六进制：必须回落到 NORMAL

        此前只在"检测到非 NORMAL 模式"时才切换 parse_mode，日志模式一旦
        生效就再也退不回来，后面的纯十六进制被按日志切块 → 0 帧。
        """
        from iec104_parser import ParseMode
        w = _make_window()
        try:
            w.hex_input.setPlainText(self.LOG)
            w._on_parse()
            _pump(2.5)
            assert len(w.all_frames) == 2
            assert w.parse_mode == ParseMode.XN3000

            w._on_clear()
            _pump(0.3)
            w.hex_input.setPlainText(self.PLAIN)
            w._on_parse()
            _pump(2.5)

            assert w.parse_mode == ParseMode.NORMAL
            assert len(w.all_frames) == 2, "自动检测没有回落，第二次解析丢帧"
        finally:
            w._save_ui_settings = lambda: None
            w.close()

    def test_clear_discards_inflight_results(self):
        """清空后，进行中的解析结果不得再回填（否则"清空没反应"）"""
        w = _make_window()
        try:
            w.hex_input.setPlainText(self.PLAIN * 30000)
            w._on_parse()
            _pump(0.05)          # 不等解析结束
            w._on_clear()
            _pump(3)
            assert w.all_frames == []
            assert w.table_widget.rowCount() == 1      # 仅空态占位行
        finally:
            w._save_ui_settings = lambda: None
            w.close()

    def test_highlight_negative_index_is_safe(self):
        """无选中帧时 frame_index=-1 不得取到 line_map[-1]（高亮最后一条）"""
        w = _make_window()
        try:
            w.hex_input.setPlainText(self.PLAIN)
            w._on_parse()
            _pump(2.5)
            w._highlight_input_line_for_frame(-1)       # 不应抛异常/误高亮
            assert w.hex_input.extraSelections() in ([], None) or \
                len(w.hex_input.extraSelections()) == 0
        finally:
            w._save_ui_settings = lambda: None
            w.close()


class TestCrashHandlers:
    """闪退兜底：未捕获异常要写日志而不是让进程消失"""

    def test_excepthook_installed_and_logs(self, tmp_path, monkeypatch):
        import main as M
        monkeypatch.setattr(M, "get_log_dir", lambda: str(tmp_path))
        monkeypatch.setattr(M, "_show_crash_dialog", lambda _t: None)
        log = M._install_crash_handlers()
        assert os.path.basename(log) == "crash.log"
        assert callable(sys.excepthook)
        # 触发一次异常，日志里必须出现 traceback
        try:
            raise RuntimeError("boom-for-test")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())
        with open(log, encoding="utf-8") as f:
            assert "boom-for-test" in f.read()


if __name__ == "__main__":
    total = failed = 0
    for cls in (TestReparseAfterWorkerRecycled,):
        for name in sorted(n for n in dir(cls) if n.startswith("test_")):
            total += 1
            try:
                getattr(cls(), name)()
                print(f"PASS {name}")
            except Exception:
                failed += 1
                import traceback
                traceback.print_exc()
                print(f"FAIL {name}")
    sys.exit(1 if failed else 0)
