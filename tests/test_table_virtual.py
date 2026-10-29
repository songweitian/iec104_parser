"""报文列表虚拟化渲染回归测试

D6 重构把报文列表从“每帧创建 12 个 QTableWidgetItem”改为 QStyledItemDelegate
实时绘制：所有内容经 _view_order（视图行→全量帧索引）与 _frame_to_row 反查表路由。

本测试锁定该映射不被破坏：全量/过滤子集/搜索隐藏/排序后，
点击、详情、导出所依赖的 _frame_idx_at_row / _row_of_frame 必须与帧一一对应，
_cell() 内容也必须与原逐帧填充语义一致。

依赖 PyQt5；离屏平台不渲染文字，但 _cell() 取值与映射逻辑不依赖渲染，可正常断言。

注意：路径注入 / 离屏平台 / 授权文件隔离已在 tests/conftest.py 统一处理，
本文件若独立运行仍需下列等价设置（pytest 下会被 conftest 覆盖为相同值）。
"""
import os
import sys

# 独立运行兼容：确保 sys.path 与授权隔离与 conftest 一致（pytest 下已设置，重复无害）
if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import license as L
if not L.LICENSE_FILE.endswith("__test_no_license__.dat"):
    L.LICENSE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "config", "__test_no_license__.dat")

from PyQt5.QtWidgets import QApplication


def _build_hex(n: int) -> str:
    """构造 n 条类型标识=9(测量值归一化) 的 I 帧"""
    out = []
    for i in range(n):
        b = 0x1000 + i
        # 68 0E | 04 00 14 00 | 09 01 03 00 01 00 00 40 00 01 00 | <IOA 2字节>
        out.append(
            f"68 0E 04 00 14 00 09 01 03 00 01 00 00 40 00 01 00 "
            f"{b & 0xFF:02X} {(b >> 8) & 0xFF:02X}")
    return "\n".join(out)


# 在整个测试进程内持有 QApplication 单例（函数局部引用会被 GC 掉，导致窗口销毁）
_APP = QApplication.instance() or QApplication([])


def _make_window(n: int):
    """构造 MainWindow 并同步解析 n 条样例帧，返回 (w, frames)"""
    from gui.main_window import MainWindow, ParseWorker
    from iec104_parser import ParseMode

    w = MainWindow()
    w.show()
    QApplication.processEvents()

    frames = []
    worker = ParseWorker(w.parser, _build_hex(n), ParseMode.NORMAL, max_frames=0)
    worker.parse_finished.connect(frames.extend)
    worker.run()  # 同步解析，不启动线程
    w.all_frames = frames
    w._frame_byte_offset = []
    w._frame_input_lines = []
    w._populate_table()
    QApplication.processEvents()
    return w


class TestViewOrderMapping:
    """_view_order 与反查表的一致性（全量）"""

    def setup_method(self):
        self.w = _make_window(50)

    def teardown_method(self):
        from PyQt5.QtWidgets import QApplication
        self.w.close()
        QApplication.processEvents()

    def test_row_count_matches_frames(self):
        assert self.w.table_widget.rowCount() == len(self.w.all_frames) == 50
        assert len(self.w._view_order) == 50

    def test_identity_mapping_full_view(self):
        # 未过滤/未排序时，视图行 == 帧索引
        for r in range(50):
            assert self.w._frame_idx_at_row(r) == r
            assert self.w._row_of_frame(r) == r

    def test_cell_content_sane(self):
        c0 = self.w._cell(0, 0)
        assert c0["text"] == "1"      # 序号从帧.index 而来
        assert "sort" in c0
        # 列4 类型标识：09 -> M_ME_NA_1
        assert "9" in self.w._cell(0, 4)["text"]
        # 列6 公共地址：01 00 -> 0x0001
        assert "0x0001" in self.w._cell(0, 6)["text"]

    def test_out_of_range_row_returns_empty(self):
        cell = self.w._cell(9999, 0)
        assert cell["text"] == "-"
        # 越界返回 -1：此前"回退为自身"会把视图行号当成帧索引，
        # 过滤/搜索后 _view_order 短于 all_frames 时会打开错误报文详情。
        assert self.w._frame_idx_at_row(9999) == -1

    def test_bookmark_prefix_rendered(self):
        self.w._toggle_bookmark(3)          # 行3 → 帧3
        assert 3 in self.w._bookmarks
        assert "📌" in self.w._cell(3, 0)["text"]
        self.w._toggle_bookmark(3)          # 取消
        assert 3 not in self.w._bookmarks


class TestViewOrderUnderFilterSort:
    """过滤/排序后映射仍与帧一一对应"""

    def setup_method(self):
        self.w = _make_window(100)

    def teardown_method(self):
        from PyQt5.QtWidgets import QApplication
        self.w.close()
        QApplication.processEvents()

    def test_filter_subset_remaps(self):
        # 记录帧索引集合，过滤后视图行必须精确等于匹配帧索引子集
        self.w.filter_type.setCurrentIndex(self.w.filter_type.findData(9))  # 全部匹配
        self.w._on_filter()
        assert len(self.w._view_order) == 100
        assert all(self.w._frame_idx_at_row(r) == r for r in range(100))

        self.w._on_filter_reset()
        assert len(self.w._view_order) == 100

    def test_sort_reorders_view_but_not_frames(self):
        # 初始视图为全量升序
        assert list(self.w._view_order) == list(range(100))
        # 点两次表头→降序：视图应反转，但 all_frames 顺序不变
        self.w._on_header_sort(0)   # 第1次：升序（本就升序，无变化）
        self.w._on_header_sort(0)   # 第2次：降序 → 反转
        after = list(self.w._view_order)
        assert after == list(range(99, -1, -1))
        # 反查表与排序后视图一一对应
        for vi, fi in enumerate(after):
            assert self.w._row_of_frame(fi) == vi
        # all_frames 仍为解析顺序，不被排序改动
        assert [f.index for f in self.w.all_frames] == list(range(1, 101))

    def test_search_hides_nonmatching(self):
        self.w.search_input.setText("09 01")   # 所有帧都含该类型标识，全部保留
        visible = sum(1 for r in range(self.w.table_widget.rowCount())
                      if not self.w.table_widget.isRowHidden(r))
        assert visible == self.w.table_widget.rowCount()
        self.w.search_input.setText("")
