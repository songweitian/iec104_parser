# IEC104 Parser 代码审核缺陷修复计划

## 目标
按审核报告依次修复第一、二、三批缺陷（共 18 项），每批完成后运行 `python -m pytest tests -q` 验证，
保证不引入回归（基线：200 passed, 1 failed）。

## 基线
- 测试基线：`200 passed, 1 failed`
- 唯一失败：`tests/test_simulator_dialog.py::TestLifecycle::test_full_master_slave_cycle`
  （`RuntimeError: wrapped C/C++ object of type QPushButton has been deleted`）

---

## 第一批：纯正确性修复（几行改动）
状态：`complete` — 200 passed, 1 failed（回到基线）

| # | 文件:行 | 问题 | 状态 |
|---|---------|------|------|
| 1 | `gui/compare_dialog.py:201` | `all_ioas` 未定义 → 应为 `all_keys` | pending |
| 2 | `gui/compare_dialog.py:177/191` | `fa`/`fb` 为循环残留变量，diffs 只存了 index | pending |
| 3 | `gui/main_window.py:2634` | `_goto_prev_bookmark` 回绕 `sorted()[0]` → `[-1]` | pending |
| 4 | `gui/main_window.py:1763-1767` | `_frame_idx_at_row` 越界原样返回 row → 返回 -1 | pending |
| 5 | `gui/parse_worker.py:26-30` | `parse_text_sync` 未连 `parse_error`，异常被吞 | pending |

验收：`pytest tests -q` ≥ 基线

---

## 第二批：稳定性修复
状态：`complete` — **201 passed, 0 failed**（首次全绿）

| # | 文件:行 | 问题 | 状态 |
|---|---------|------|------|
| 6 | `iec104_simulator.py:754` + `:637-645` | K 窗口满时静默丢帧，ACTTERM 发不出 | pending |
| 7 | `iec104_simulator.py:649-663` + `simulator_dialog.py:1219` | 共享 PointData，变化上送失效 | pending |
| 8 | `main_window.py:2676` | closeEvent 不关模拟器对话框 → QThread 运行时被销毁 | pending |
| 9 | `main_window.py:2093` | `_apply_input_line_colors` O(n²) 累加 | pending |
| 10 | `main_window.py:748/2678` | ParseWorker 无 `deleteLater` | pending |
| 11 | `iec104_simulator.py:427-433` | `frame.apci` 为 None 仍调用 → AttributeError 杀线程 | pending |
| 12 | `simulator_dialog.py:766-772` | closeEvent 等待循环阻塞事件循环，空耗 2 秒 | pending |

验收：`pytest tests -q` 应达到 **0 failed**（#6/#7/#11 直接决定失败用例）

---

## 第三批：安全修复
状态：`complete` — **202 passed, 0 failed**（新增 1 个绕过回归测试）

## 第四批：健壮性 / 测试质量 / 死代码清理
状态：`complete` — **215 passed, 0 failed**（原 211 基线 + 新增 4 个测试文件）

### 用户决策（先于第四批）
1. 旧激活全部作废：升级到本版本后所有用户重新激活（不再向后兼容旧 license.dat）
2. 授权不追求过度专业：离线即可，无需在线校验/硬件狗

### 第四批实施
- license：新增 `_LICENSE_SCHEMA=2`，`load_license` 拒绝旧格式（强制重新激活）；
  `needs_reactivation` 在"有授权文件但不可用"时为 True（提示重新激活 vs 从未激活）
- pcap_importer：pcapng 魔数识别并给出可操作提示；`incl_len` 超 256KB 显式报错
  （防 OOM/卡死）；新增 test_pcap_importer.py（5 例）
- main_window：搜索框正则编译前限制长度 256（ReDoS 防御）；输入区校验防抖
  （200ms，避免大文件每次按键全量扫描）
- 落盘路径：license/simulator_config/ioa_aliases/历史会话 四个 `_get_app_dir`
  在 frozen 模式改落 `%APPDATA%/IEC104Parser`，避免装到 Program Files 静默写失败
  （新增 test_app_paths.py）
- 测试假绿断言修复：test_parse_worker 恒真断言、test_simulator 无断言（改 FakeSocket
  验证真实发送）、test_phase1 文件缺失静默 PASS（改 pytest.skip）、test_phase3
  getattr 默认返回（改直接属性访问）
- iec104_simulator：t1 确认超时 / t3 连续 2 次 TESTFR 无应答 → 置 `_reconnect_requested`，
  内层循环退出后由外层 `while` 重连；`_close_sockets` 增加 `shutdown(SHUT_RDWR)`
  （新增 TestProtocolTimers）
- 死代码清理：删除未引用的 capture_dialog.py、icons._arrow_icon、iec104_simulator
  APCIType/ConnectionState.STOPPING/self._auto_send_interval
- stats_dialog：柱状图/饼图文字与网格色改为跟随系统主题（QPalette），深色主题下不再不可见

### 刻意未做（用户"不过度专业"）
- 离线授权在拥有本地管理员权限的攻击者面前仍可被 patch 二进制绕过——已知残留，
  彻底方案需在线激活校验或硬件狗，本次不做。
- frame_generator 的 `vsq` 参数经核对确为 VSQ（可变结构限定符），命名正确，未改。

| # | 文件:行 | 问题 | 状态 |
|---|---------|------|------|
| 13 | `license.py:317` + `:109-119` | 用文件里的 fingerprint 验签 → 机器绑定可绕过 | pending |
| 14 | `license.py:152-155` | `_decode_signature` 无 try，畸形输入致启动崩溃 | pending |
| 15 | `license.py:372` vs `:263` | `activate` 用裸 `date.today()`，与 `_effective_today` 口径不一致 | pending |

验收：`pytest tests -q` 全绿；手工验证激活/过期/防回拨路径

---

## 遇到的错误
| 错误 | 尝试次数 | 解决方案 |
|------|---------|---------|
| 改 `_frame_idx_at_row` 越界返回 -1 后 `test_out_of_range_row_returns_empty` 失败 | 1 | 测试把"越界回退为自身"写成了契约断言，同步更新为新契约 `-1` |
| 给 `send_i_frame` 加"K 窗口满时等待确认" → SBO 预制超时、从站卡死 9s+ | 2 | **自死锁**：`_auto_reply_slave` 在收包循环同步栈内发送，工作线程无法处理对端 S 帧。改为 `_gi_queue` 分段发送 + ACTTERM 哨兵，由 `_handle_timers` 分批 flush |
| 第三批先用"候选指纹（当前值 × 授权文件里的 hw 原值）"方案，跨机模拟验证发现仍可绕过 | 2 | hw 也未签名，保留即可重建原机指纹。改为**严格只用本机真实指纹验签**，移除 hw；漂移用户改由 `needs_reactivation=True` 提示重新激活 |
| 严格验签后 `test_single_drift_tolerated` 失败 | 1 | 有意的行为变更（安全优先）。测试改名 `test_single_drift_needs_reactivation` 并新增 `test_components_swap_rejected` 覆盖绕过场景 |

## 关键决策与取舍
- **漂移 vs 防拷贝**：二者在纯离线本地授权下不可兼得（本地文件都可被管理员改写）。
  选择**安全优先**：验签只认本机指纹；同机漂移不再静默放行，而是返回
  `needs_reactivation=True`，UI 可据此提示重新激活（本次未接 UI）。
- **残留风险（诚实披露）**：拥有本地管理员权限的攻击者仍可 patch 程序二进制或
  伪造全部硬件标识。彻底方案需在线激活校验或硬件狗，超出本次范围。

## 关键约束
- 每批改完必须跑测试，结果写入 `progress.md`
- 不得降低现有测试通过数（基线 200 passed）
- 修改 `license.py` 时不得改变激活码格式契约（否则已发出的码全部失效）
