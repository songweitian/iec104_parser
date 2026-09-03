"""三大分析功能与主解析器同步的回归测试。

ParseWorker 在内部副本上解析，而主窗口的序号检查/合规检查/报文配对
从 self.parser.frames 取数——此前 parser 从未被喂帧，三者在空表上
恒报"无问题"（审查 Top1 缺陷）。本测试锁定：解析完成、标签页切换
两个入口都必须把帧同步进 parser。
"""
import os
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


def _parse_two_i_frames_with_sn_gap():
    """构造同方向两 I 帧（SN 0 → 5，跳变），经 ParseWorker 解析出帧列表"""
    h1 = generate_i_frame(type_id=9, cot=3, common_addr=1, ioa=0x4001,
                          value=1.0, send_seq=0, recv_seq=0)
    h2 = generate_i_frame(type_id=9, cot=3, common_addr=1, ioa=0x4001,
                          value=2.0, send_seq=5, recv_seq=0)
    w = ParseWorker(None, h1 + "\n" + h2, ParseMode.NORMAL)
    out = []
    w.parse_finished.connect(out.append)
    w.run()
    return out[0] if out else []


class TestParserSync:
    def setup_method(self):
        from gui.main_window import MainWindow
        self.w = MainWindow()
        QApplication.processEvents()

    def teardown_method(self):
        from PyQt5.QtWidgets import QApplication
        self.w.close()
        QApplication.processEvents()

    def test_parse_finished_feeds_parser(self):
        frames = _parse_two_i_frames_with_sn_gap()
        assert len(frames) == 2
        self.w._on_parse_finished(frames)
        assert len(self.w.parser.frames) == 2
        # 序号检查此前恒返回空——现在应发现 SN 跳变
        issues = self.w.parser.check_sequence()
        assert issues, "SN 0→5 跳变应被序号检查发现"
        assert issues[0]["type"] == "SN跳变"

    def test_tab_switch_resyncs_parser(self):
        frames = _parse_two_i_frames_with_sn_gap()
        self.w._on_parse_finished(frames)
        self.w._save_current_tab_state()
        # 模拟切到新标签页（清空）再切回
        self.w._set_frames([])
        assert self.w.parser.frames == []
        self.w._load_tab_state(self.w._current_tab_index)
        assert len(self.w.parser.frames) == 2
        assert self.w.parser.check_sequence()

    def test_clear_empties_parser_analysis(self):
        frames = _parse_two_i_frames_with_sn_gap()
        self.w._on_parse_finished(frames)
        self.w._set_frames([])
        assert self.w.parser.check_sequence() == []
        assert self.w.parser.find_pairs() == []


if __name__ == "__main__":
    import traceback
    total = failed = 0
    inst = None
    for name in sorted(dir(TestParserSync)):
        if not name.startswith("test_"):
            continue
        total += 1
        cls = TestParserSync()
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
