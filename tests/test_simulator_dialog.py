"""模拟器对话框 UI 状态机冒烟测试（离屏）。

覆盖模拟器专项改造的核心行为：
- 初始态按钮可用性（启动亮/停止灰/操作禁用）
- 运行中锁定连接配置与点位配置
- 主站连接后操作按钮启用，总召唤按钮等待→完成反馈
- SBO 预制确认点亮执行按钮、执行后复位
- 手动发送结果提示
- 连接失败原因显性展示（重试中）
- 停止异步化（GUI 不被 wait 阻塞，线程稍后收割）

全部走真实 localhost TCP 回环，不 mock 协议栈。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 离屏平台必须在 PyQt5 导入前设置
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication
# 整个进程持有一个 QApplication 单例（局部引用会被 GC，窗口销毁即崩溃）
_APP = QApplication.instance() or QApplication([])

import gui.simulator_dialog as sd
from gui.simulator_dialog import SimulatorDialog
from iec104_simulator import ConnectionState, SimulatorRole
from frame_generator import generate_i_frame


def _loopback_available() -> bool:
    """探测本环境是否允许 loopback TCP（部分安全策略会静默丢弃回环包）"""
    import socket
    import threading
    ok = False
    srv = socket.socket()
    srv.settimeout(2)
    try:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def _accept():
            try:
                c, _ = srv.accept()
                c.close()
            except OSError:
                pass
        t = threading.Thread(target=_accept, daemon=True)
        t.start()
        time.sleep(0.05)
        s = socket.create_connection(("127.0.0.1", port), timeout=1)
        ok = True
        s.close()
        t.join(2)
    except OSError:
        ok = False
    finally:
        srv.close()
    return ok


_LOOPBACK = _loopback_available()


def _wait(cond, timeout_ms=10000):
    """泵事件循环直到 cond() 为真或超时（信号是排队跨线程投递的）"""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    QApplication.processEvents()
    return cond()


def _make_dialog(tmp_path, monkeypatch):
    """构造对话框，把配置文件指到临时目录，避免读写真实用户配置"""
    monkeypatch.setattr(sd, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(sd, "CONFIG_FILE", str(tmp_path / "simulator_config.json"))
    return SimulatorDialog()


class TestInitialStates:
    """未启动时的静态可用性"""

    def test_initial_buttons(self, tmp_path, monkeypatch):
        dlg = _make_dialog(tmp_path, monkeypatch)
        try:
            assert dlg.btn_start.isEnabled()
            assert not dlg.btn_stop.isEnabled()
            # 主站操作按钮全部禁用（未连接，点了也没用）
            for name in ("总召唤", "时钟同步", "启动传输", "停止传输", "测试帧"):
                assert not getattr(dlg, f'_btn_{name}').isEnabled()
            # 手动发送禁用，遥控行内按钮禁用（不再弹"请先启动连接"）
            assert not dlg.btn_send_manual.isEnabled()
            for ops in dlg._yk_ops.values():
                assert not ops["btn_on"].isEnabled()
                assert not ops["btn_select"].isEnabled()
            for ops in dlg._yt_ops.values():
                assert not ops["btn_set"].isEnabled()
            assert not dlg.chk_auto_send.isEnabled()
        finally:
            dlg.deleteLater()


class TestConnectFailVisible:
    """连接失败原因要显性出现在状态标签"""

    def test_refused_shows_reason(self, tmp_path, monkeypatch):
        dlg = _make_dialog(tmp_path, monkeypatch)
        try:
            dlg.role_combo.setCurrentIndex(0)     # 主站
            dlg.ip_input.setText("127.0.0.1")
            dlg.port_input.setValue(1)            # 保留端口，立即被拒
            dlg.btn_start.click()
            assert _wait(lambda: "重试中" in dlg.lbl_status.text(), 8000), \
                f"状态标签应显示连接失败（重试中），实际: {dlg.lbl_status.text()}"
            assert dlg.lbl_status.toolTip(), "失败原因应写入 tooltip"
            # 运行中锁配置
            assert not dlg.role_combo.isEnabled()
            assert not dlg.port_input.isEnabled()
        finally:
            dlg.btn_stop.click()
            assert _wait(lambda: not dlg._closing_sims, 5000)
            dlg.deleteLater()


class TestLifecycle:
    """主从回环：连接 → 总召唤反馈 → SBO 预制/执行 → 停止

    需要真实 loopback TCP；被安全策略禁回环的环境自动跳过
    （状态机逻辑另见 TestStateMachine，无需网络）。
    """

    def test_full_master_slave_cycle(self, tmp_path, monkeypatch):
        if not _LOOPBACK:
            import pytest
            pytest.skip("本环境禁止 loopback TCP，回环联调测试跳过")
        port = 24104

        slave = _make_dialog(tmp_path, monkeypatch)
        master = _make_dialog(tmp_path, monkeypatch)
        try:
            # ---- 从站监听（绑回环，避免防火墙弹窗）----
            slave.role_combo.setCurrentIndex(1)
            slave.ip_input.setText("127.0.0.1")
            slave.port_input.setValue(port)
            slave.btn_start.click()
            assert _wait(lambda: slave.simulator and slave.simulator.isRunning(), 5000)
            # 未连接时从站自动上送不可用
            assert not slave.chk_auto_send.isEnabled()

            # ---- 主站连接 ----
            master.role_combo.setCurrentIndex(0)
            master.ip_input.setText("127.0.0.1")
            master.port_input.setValue(port)
            master.btn_start.click()
            # 主站连接后自动 STARTDT 握手，双方应进入数据传输态
            assert _wait(lambda: master.simulator and master.simulator.is_connected, 8000), \
                f"主站应连上从站，状态: {master.lbl_status.text()}"
            assert _wait(lambda: "数据传输中" in master.lbl_status.text(), 8000)
            # 连接后主站操作按钮启用；从站自动上送启用
            assert _wait(lambda: master._btn_总召唤.isEnabled(), 3000)
            assert _wait(lambda: slave.chk_auto_send.isEnabled(), 3000)
            # 运行中锁点位/连接配置
            assert not master.port_input.isEnabled()
            assert not slave.port_input.isEnabled()

            # ---- 总召唤按钮：点击 → 忙文案 → 收到激活终止后复原 ----
            master._btn_总召唤.click()
            assert master._btn_总召唤.text() == "总召唤…"
            assert not master._btn_总召唤.isEnabled()
            assert _wait(lambda: master._btn_总召唤.text() == "总召唤", 15000), \
                "总召唤应在收到 COT=10 后恢复按钮文案"

            # ---- SBO 预制 → 确认点亮 → 执行 → 复位 ----
            ioa = sorted(master._yk_ops)[0]
            ops = master._yk_ops[ioa]
            ops["btn_select"].click()
            assert _wait(lambda: ops["selected"], 8000), "预制应在从站确认后点亮"
            assert ops["btn_select"].text() == "已预制"
            assert ops["btn_on"].isEnabled()
            ops["btn_on"].click()
            assert _wait(lambda: not ops["btn_on"].isEnabled(), 5000), \
                "执行后预制态应复位"
            assert _wait(lambda: any("执行确认" in r for r in
                                     [master.log_table.item(rr, 4).text()
                                      for rr in range(master.log_table.rowCount())
                                      if master.log_table.item(rr, 4)]), 5000), \
                "应收到执行确认日志"

            # ---- 手动发送：结果提示 ----
            master.hex_combo.setCurrentText("68 04 43 00 00 00")
            master.btn_send_manual.click()
            assert "已发送 6 字节" in master.lbl_send_hint.text()

            # ---- 停止异步化：点击后立刻恢复启动按钮（不阻塞）----
            master.btn_stop.click()
            assert master.btn_start.isEnabled()
            assert master.simulator is None
            assert not master._btn_总召唤.isEnabled()
            assert _wait(lambda: not master._closing_sims, 5000), "主站线程应收割"

            slave.btn_stop.click()
            assert slave.btn_start.isEnabled()
            assert _wait(lambda: not slave._closing_sims, 5000), "从站线程应收割"
        finally:
            for dlg in (master, slave):
                if dlg.simulator:
                    dlg.simulator.stop()
            QApplication.processEvents()
            for dlg in (master, slave):
                dlg.deleteLater()


class _FakeSim:
    """鸭嘴型模拟器替身：不起线程不占 socket，专供状态机测试

    isRunning/is_connected/state 与 IEC104Simulator 语义一致；
    send_* 记录调用并返回 True，模拟"发送成功，等待对端反馈"。
    """

    def __init__(self):
        self._state = ConnectionState.STARTED
        self.send_seq = 1
        self.recv_seq = 0
        self.points = {}
        self.sent = []

    @property
    def state(self):
        return self._state

    @property
    def is_connected(self):
        return self._state in (ConnectionState.CONNECTED, ConnectionState.STARTED)

    def isRunning(self):
        return True

    def stop(self):
        self._state = ConnectionState.IDLE

    def send_general_interrogation(self, common_addr=1):
        self.sent.append(("gi", common_addr))
        return True

    def send_clock_sync(self, common_addr=1):
        self.sent.append(("clock", common_addr))
        return True

    def send_startdt(self):
        self.sent.append(("startdt",))
        return True

    def send_i_frame(self, **kw):
        self.sent.append(kw)
        return True


class TestStateMachine:
    """无需网络：用替身模拟器驱动状态机与操作反馈"""

    def _connected_dialog(self, tmp_path, monkeypatch):
        dlg = _make_dialog(tmp_path, monkeypatch)
        dlg.simulator = _FakeSim()
        dlg._refresh_ui_state()
        return dlg

    def test_connected_unlocks_and_locks(self, tmp_path, monkeypatch):
        dlg = self._connected_dialog(tmp_path, monkeypatch)
        try:
            assert dlg._btn_总召唤.isEnabled()
            assert dlg.btn_send_manual.isEnabled()
            assert "数据传输中" in dlg.lbl_status.text()
            # 连接后行内按钮：SBO 开启 → 仅预制可用
            for ops in dlg._yk_ops.values():
                assert ops["btn_select"].isEnabled()
                assert not ops["btn_on"].isEnabled()
            # 运行中锁配置
            assert not dlg.port_input.isEnabled()
            assert not dlg.btn_apply_points.isEnabled()
            assert not dlg.role_combo.isEnabled()
        finally:
            dlg.deleteLater()

    def test_gi_feedback_cycle(self, tmp_path, monkeypatch):
        dlg = self._connected_dialog(tmp_path, monkeypatch)
        try:
            dlg._on_gi()
            assert dlg._btn_总召唤.text() == "总召唤…"
            assert not dlg._btn_总召唤.isEnabled()
            # 收到激活确认（COT=7）→ 忙文案切换为接收数据
            ack = generate_i_frame(type_id=100, cot=7, common_addr=1,
                                   ioa=0, value=20, send_seq=1, recv_seq=0)
            dlg._on_frame_received(ack)
            assert dlg._btn_总召唤.text() == "接收数据…"
            # 收到激活终止（COT=10）→ 按钮复原并记完成日志
            term = generate_i_frame(type_id=100, cot=10, common_addr=1,
                                    ioa=0, value=20, send_seq=2, recv_seq=1)
            dlg._on_frame_received(term)
            assert dlg._btn_总召唤.text() == "总召唤"
            assert dlg._btn_总召唤.isEnabled()
        finally:
            dlg.deleteLater()

    def test_sbo_select_ack_execute_reset(self, tmp_path, monkeypatch):
        dlg = self._connected_dialog(tmp_path, monkeypatch)
        try:
            ioa = sorted(dlg._yk_ops)[0]
            ops = dlg._yk_ops[ioa]
            dlg._on_yk_select(ioa)
            assert ops["btn_select"].text() == "预制…"
            assert not ops["btn_select"].isEnabled()
            # 从站选择确认 → 点亮执行
            dlg._on_control_ack(ioa, 7, 45)
            assert ops["selected"] is True
            assert ops["btn_select"].text() == "已预制"
            assert ops["btn_on"].isEnabled() and ops["btn_off"].isEnabled()
            # 执行 → 预制态复位
            dlg._on_yk_command(ioa, 1)
            assert ops["selected"] is False
            assert not ops["btn_on"].isEnabled()
            assert ops["btn_select"].text() == "预制"
        finally:
            dlg.deleteLater()

    def test_sbo_select_timeout_resets(self, tmp_path, monkeypatch):
        dlg = self._connected_dialog(tmp_path, monkeypatch)
        try:
            ioa = sorted(dlg._yk_ops)[0]
            ops = dlg._yk_ops[ioa]
            dlg._on_yk_select(ioa)
            # 模拟 5s 超时触发
            dlg._op_timeout(f"sel:45:{ioa}")
            assert ops["selected"] is False
            assert ops["btn_select"].text() == "预制"
            assert ops["btn_select"].isEnabled(), "超时后应可重新预制"
            assert any("超时" in (dlg.log_table.item(r, 4).text() or "")
                       for r in range(dlg.log_table.rowCount())
                       if dlg.log_table.item(r, 4)), "应记录超时日志"
        finally:
            dlg.deleteLater()

    def test_select_reject_cot8(self, tmp_path, monkeypatch):
        dlg = self._connected_dialog(tmp_path, monkeypatch)
        try:
            ioa = sorted(dlg._yt_ops)[0]
            ops = dlg._yt_ops[ioa]
            dlg._on_yt_select(ioa)
            dlg._on_control_ack(ioa, 8, 50)   # 激活终止
            assert ops["selected"] is False
            assert not ops["btn_set"].isEnabled()
            assert ops["btn_select"].text() == "预制"
        finally:
            dlg.deleteLater()

    def test_disconnect_clears_everything(self, tmp_path, monkeypatch):
        dlg = self._connected_dialog(tmp_path, monkeypatch)
        try:
            ioa = sorted(dlg._yk_ops)[0]
            dlg._on_yk_select(ioa)
            # 连接掉线：模拟器状态退回连接中（真实掉线/重试时如此），
            # is_connected=False 才是行内按钮禁用的驱动源
            dlg.simulator._state = ConnectionState.CONNECTING
            dlg._on_disconnected()      # 会话中断
            for ops in dlg._yk_ops.values():
                assert not ops["btn_on"].isEnabled()
                assert not ops["btn_select"].isEnabled()
                assert ops["selected"] is False
                assert ops["btn_select"].text() == "预制"
            assert not dlg._btn_总召唤.isEnabled()
            assert not dlg.btn_send_manual.isEnabled()
            assert not dlg._pending_ops, "断开应取消等待中的操作"
        finally:
            dlg.deleteLater()


if __name__ == "__main__":
    import traceback
    import tempfile

    total = failed = 0
    for cls in (TestInitialStates, TestStateMachine, TestConnectFailVisible, TestLifecycle):
        for name in sorted(dir(cls)):
            if not name.startswith("test_"):
                continue
            total += 1
            inst = cls()
            try:
                tmp = tempfile.mkdtemp()

                class _MP:
                    """monkeypatch 最小实现（直接运行时用）"""
                    def __init__(self):
                        self._set = {}

                    def setattr(self, obj, attr, val):
                        self._set[(id(obj), attr)] = (obj, attr, getattr(obj, attr))
                        setattr(obj, attr, val)

                    def undo(self):
                        for obj, attr, old in self._set.values():
                            setattr(obj, attr, old)
                mp = _MP()
                getattr(inst, name)(tmp, mp)
                mp.undo()
                print(f"PASS {cls.__name__}.{name}")
            except Exception:
                failed += 1
                print(f"FAIL {cls.__name__}.{name}")
                traceback.print_exc()
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
