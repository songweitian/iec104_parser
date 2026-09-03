"""搜索与过滤统一视图模型（_set_view）的回归测试。

此前搜索逐行 setRowHidden 并为每行构建 12 列单元格（O(N×12)），且行隐藏
与过滤/排序互相干扰；现搜索与过滤同构，经 _set_view 重建可见行。匹配范围
恒为发起搜索前的视图快照（_pre_search_view），避免逐字输入时在上一轮结果
上递归收窄导致漏配。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import license as L
if not L.LICENSE_FILE.endswith("__test_no_license__.dat"):
    L.LICENSE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "config", "__test_no_license__.dat")

from PyQt5.QtWidgets import QApplication
_APP = QApplication.instance() or QApplication([])

from gui.parse_worker import ParseWorker
from frame_generator import generate_i_frame
from iec104_parser import ParseMode


def _parse_three_frames():
    """三帧 type9，公共地址 1/2/3 各不相同，值也不重叠，便于构造互斥搜索串"""
    hexes = [
        generate_i_frame(type_id=9, cot=3, common_addr=ca, ioa=0x4001,
                         value=v, send_seq=i, recv_seq=0)
        for i, (ca, v) in enumerate([(1, 512), (2, 1024), (3, 2048)])
    ]
    w = ParseWorker(None, "\n".join(hexes), ParseMode.NORMAL)
    out = []
    w.parse_finished.connect(out.append)
    w.run()
    return out[0] if out else []


class TestSearchView:
    def setup_method(self):
        from gui.main_window import MainWindow
        self.w = MainWindow()
        QApplication.processEvents()
        self.w._on_parse_finished(_parse_three_frames())
        QApplication.processEvents()

    def teardown_method(self):
        from PyQt5.QtWidgets import QApplication
        self.w._search_timer.stop()
        self.w.close()
        QApplication.processEvents()

    def _search(self, text):
        """模拟输入搜索词：走 textChanged 快照逻辑，再直接触发防抖回调"""
        self.w.search_input.setText(text)
        self.w._do_search()

    def _ca_texts(self):
        return [self.w._frame_cell(fi, 6)["text"] for fi in range(3)]

    def test_search_narrows_view(self):
        pat = re.escape(self._ca_texts()[1])   # 仅第二帧的公共地址
        self._search(pat)
        assert self.w._view_order == [1]
        assert self.w._visible_frame_indices() == [1]
        assert self.w.lbl_search_count.text() == "找到 1 条"

    def test_domain_is_snapshot_not_narrowed(self):
        """连续两次搜索：第二次的范围必须是初始全量，而非第一次的结果

        回归：若在上一轮结果上继续匹配，搜"仅帧1"后再搜"帧0|帧2"会得空集。
        """
        ca = self._ca_texts()
        self._search(re.escape(ca[1]))
        assert self.w._view_order == [1]
        self._search("|".join(re.escape(t) for t in (ca[0], ca[2])))
        assert self.w._view_order == [0, 2]

    def test_clear_search_restores_view(self):
        ca = self._ca_texts()
        self._search(re.escape(ca[1]))
        assert self.w._view_order == [1]
        self.w.search_input.setText("")      # 触发 textChanged → _end_search
        assert self.w._view_order == [0, 1, 2]
        assert self.w._pre_search_view is None
        assert self.w.lbl_search_count.text() == ""

    def test_no_match_shows_placeholder(self):
        self._search("绝不匹配的关键词QQQ777")
        assert self.w._view_order == [-1]    # 空态占位行
        assert self.w.lbl_search_count.text() == "找到 0 条"
        # 清空后还原
        self.w.search_input.setText("")
        assert self.w._view_order == [0, 1, 2]

    def test_filter_rebuild_cancels_active_search(self):
        ca = self._ca_texts()
        self._search(re.escape(ca[1]))
        assert self.w._pre_search_view is not None
        # 过滤/重新解析等经 _set_view（非搜索路径）重建视图 → 搜索态取消
        self.w._set_view([0, 1, 2])
        assert self.w._pre_search_view is None
        assert self.w.lbl_search_count.text() == ""

    def test_sort_during_search_keeps_snapshot_synced(self):
        ca = self._ca_texts()
        self._search("|".join(re.escape(t) for t in (ca[0], ca[2])))
        assert self.w._view_order == [0, 2]
        # 序号列降序：视图（匹配子集）与搜索快照（完整匹配范围）同步翻转，
        # 后续输入的匹配范围不回退旧顺序
        self.w._on_header_sort(0)
        self.w._on_header_sort(0)
        assert self.w._view_order == [2, 0]
        assert self.w._pre_search_view == [2, 1, 0]

    def test_search_preserves_selection_when_frame_still_visible(self):
        ca = self._ca_texts()
        self.w.table_widget.selectRow(2)
        self.w._current_frame_idx = 2
        self._search("|".join(re.escape(t) for t in (ca[0], ca[2])))
        # 帧2 仍在结果中：选中行应重映射到帧2（行1），详情树同帧
        assert self.w._current_frame_idx == 2
        assert self.w._frame_to_row[2] == 1


if __name__ == "__main__":
    import traceback
    total = failed = 0
    inst = None
    for name in sorted(dir(TestSearchView)):
        if not name.startswith("test_"):
            continue
        total += 1
        cls = TestSearchView()
        try:
            cls.setup_method()
            getattr(cls, name)()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
        finally:
            try:
                cls.teardown_method()
            except Exception:
                pass
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
