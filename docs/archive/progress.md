# 进度日志

## 2026-09-08 会话 1

### 阶段 0：建立计划
- 完成全局代码审核（13 gui 模块 + 6 核心模块 + license + 12 测试文件）
- 基线测试：`200 passed, 1 failed`
- 创建 task_plan.md / findings.md / progress.md

### 阶段 1（第一批）：纯正确性修复 ✅ 完成
- `compare_dialog.py:201` `all_ioas` → `all_keys`
- `compare_dialog.py` diffs 元组改为直接存 `fa`/`fb` 帧对象（原存 index，填表用循环残留变量）
  - 同时修正 "仅A"/"仅B" 分支的 value None → "-" 与 fa/fb 判空
- `main_window.py:2634` 书签回绕 `sorted()[0]` → `[-1]`
- `main_window.py:1763-1767` `_frame_idx_at_row` 越界返回 -1（原返回 row）
- `parse_worker.py:19-45` `parse_text_sync` 连接 `parse_error` + logger + `deleteLater()`

**踩坑**：`tests/test_table_virtual.py:100` 把"越界回退为自身"写成了断言契约，
改后该测试失败。已同步更新为新契约 `-1`（该旧行为正是本次修复目标）。
→ 教训：审核报告里"测试固化缺陷"不是理论问题，修真 bug 时会立刻撞上。

验收：`200 passed, 1 failed`（回到基线，仅剩 test_full_master_slave_cycle）

### 阶段 2（第二批）：稳定性修复 ✅ 完成（201 passed, 0 failed）
- `iec104_simulator.py` `_send_lock` → `RLock`（send_i_frame 与 _send_raw 同栈重入）
- `iec104_simulator.py` `send_i_frame`：序号自增整体纳入锁；新增 `_release_send_window(rn)`
  统一处理 S 帧与 **I 帧捎带确认**（此前只有 S 帧更新 `_unacknowledged_i`），
  并改为 `(send_seq - rn) % 32768` 处理 15 位回绕
- `iec104_simulator.py:427-433` `frame.apci` 为 None 时不再调用 `_handle_i_frame_received`
- `iec104_simulator.py` `_send_all_points` 改为 **_gi_queue 分段发送** + ACTTERM 哨兵，
  由 `_handle_timers` 分批 flush
- `simulator_dialog.py` `_on_toggle_yx` / `_on_yc_edit_finished`：写入口唯一化为
  `set_point_value`（修变化上送失效）；`__import__('time')` → `time`
- `main_window.py` closeEvent：显式 `sim_dlg.close()` + worker 断连/`deleteLater`
- `main_window.py` `_start_parse`：`finished → deleteLater`
- `main_window.py` `_apply_input_line_colors`：O(n²) → 线性 + 5000 行上限
- `simulator_dialog.py` closeEvent：逐个 wait（原只等 [0] 且依赖被阻塞的定时器）
- `simulator_dialog.py` `_refresh_yk/yt_table` 开头 clear + `_on_point_updated`
  对 YK/YT 改为 `_update_point_value_cell` 单格更新（不再整表重建）

**重大踩坑（务必记住）**：
最初给 `send_i_frame` 加"K 窗口满时等待对端确认"（`_wait_for_send_window`），
结果**自死锁**——`_auto_reply_slave` 在收包循环的同步调用栈内发送，工作线程
无法腾手处理对端 S 帧，窗口永不释放，3s 超时后丢弃，且把工作线程卡住 9s+
导致后续 SBO 命令全部超时。改为"不等待 + 队列分段"才正确。
→ 教训：**在同步收包栈内做阻塞等待必然自死锁**，批量发送必须走队列+定时 flush。
诊断手段：临时脚本 `_diag_sbo.py`（已删）打印双方 log_message 与序号状态，
比读代码快得多。

耗时从 27s → 5.45s（不再走 15s 总召唤超时路径），主站收帧数 12 → 32。

### 阶段 3（第三批）：安全修复 ✅ 完成（202 passed, 0 failed）
- `license.py` `_decode_signature` 加 try/except 返回 b""（fail-closed）。
  原异常在 `_verify_signature` 的 try **之外**抛出，全仓无 sys.excepthook →
  畸形 license.dat 可让程序启动即闪退
- `license.py` `get_license_status`：**验签改用本机真实指纹**，不再用授权文件里的
  `fingerprint`（该字段与 components 都未签名、可任意编辑）
- `license.py` `_machine_matches` 收紧：guid 必须命中 + mac/host 至少中 1
  （原 ≥2/3 任意组件，改 MAC+主机名即可在第二台机器通过）；无 guid 时用
  `min(2, 可用组件数)`，避免非 Windows 上授权永久失效
- `license.py` `activate` 过期判定改用 `_effective_today()`（原用裸 `date.today()`，
  拨回系统时间即可激活过期码，还会用拨回日期播种水位）
- 新增状态字段 `needs_reactivation`：同机硬件漂移时提示重新激活而非静默失效
- 测试：`test_single_drift_tolerated` → `test_single_drift_needs_reactivation`；
  新增 `test_components_swap_rejected`（覆盖旧实现的绕过手法）

**重大踩坑**：先实现的"候选指纹（当前值 × 文件里 hw 原值笛卡尔组合）"经跨机
模拟验证**仍可绕过**——hw 本身也未签名，攻击者保留 hw 即可在 B 机器重建 A 的指纹。
→ 教训：**离线授权里任何存入本地文件的"历史真值"都不可信**，只能以当前机器
硬件值为唯一可信来源。安全方案必须做跨机模拟验证，不能只在原机上测。
验证方法：monkey-patch `L.get_hardware_fingerprint` / `get_hardware_components`
模拟另一台机器（临时脚本 `_diag_license.py`，已删）。

---

## 测试运行记录

| 批次 | 命令 | 结果 | 备注 |
|------|------|------|------|
| 基线 | `python -m pytest tests -q` | 200 passed, 1 failed | test_full_master_slave_cycle |
