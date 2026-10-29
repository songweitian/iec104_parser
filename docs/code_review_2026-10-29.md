# 代码审核报告（2026-10-29）

范围：全项目（解析核心 / 帧生成 / 模拟器 / pcap / 授权 / GUI / 测试与仓库结构）
基线：审核前 `pytest` 233 项全绿（说明缺陷都发生在测试未覆盖或"测试固化了错误行为"的地方）
结果：**已修复 26 项缺陷**，新增 23 项回归测试，当前 `pytest` **256 项全绿**，`pyflakes` 零告警。

---

## 一、高危缺陷（会直接导致现场用错）

### 1. 遥控/遥调 S/E 位（选择/执行）语义全链路反了 —— 最严重

IEC 60870-5-101 §7.2.6 规定 S/E 位 **1 = 选择(预制)、0 = 执行**。
项目里三处实现一致地写反了，所以"自测能过、连真装置就出事"：

| 位置 | 原实现 | 后果 |
|---|---|---|
| `frame_generator.generate_i_frame` | `select=True` 时写 bit7=0 | 生成的"预制"帧被装置当作**执行**，一次点击就操作刀闸 |
| `iec104_simulator._handle_control_command` | `se == 0` 当预制 | 装置的预制帧被模拟器当成执行 |
| `iec104_parser`（45/46/48-51/58-64） | `se=1` 显示"执行" | 报文分析把预制帧标成执行，排查时被误导 |

`tests/test_frame_generator.py::TestSboSelectBit` 此前把 `select → 0x01` 写进了断言，
即**测试固化了错误语义**。本轮按标准改正三处实现，并把测试断言改成标准语义（附说明）。

### 2. 调节步命令 C_RC_NA_1（47 / 60）位布局错

RCO 结构与 SCO 不同：`bit0-5 = RCA`、`bit6 = S/E`、`bit7 = QL`。

- 原实现把 S/E 取成 bit7、RCA 取成 bit1-6 → 整个字节错位；
- RCA 的 bit0 是方向位（0=升一步、1=降一步），原显示反了；
- `_command_se_bit()` 对 47/60 也用 bit7，导致帧描述里的"（选择）/（执行）"跟着错。

修复：`iec104_parser` 47 分支 + 新增 `_CMD_SE_BITPOS = {47: 6, 60: 6}`；
`iec104_simulator._decode_command/_extract_sco`；`frame_generator._SE_BITPOS`（新增 47 回归测试）。

### 3. 传送原因把 ORG（源发方地址）并进去了

`asdu.cot = data[2] + (data[3] << 8)`。COT 域是两个字节：`[原因+P/N+T, ORG]`。
只要装置下发的 ORG≠0，COT 就变成 `0x0153` 之类的值，画面/导出/统计里的"传送原因"全错。
修复：`cot = data[2]`、`org = data[3]`。
同时 `frame_generator` 里"`cot > 255` 就把高字节当 ORG、并丢掉调用方给的 `origin_addr`"的写法
会让**帧结构随参数静默变化**，改为固定写 `origin_addr` 并对 cot 做 0..255 校验。

### 4. 主窗口：旧解析线程的迟到信号会"清掉新线程引用"

`_start_parse` 里 `worker.wait()` 期间，上一轮 worker 的 `finished` 已排队；新 worker 启动后该信号才投递，
`_on_parse_worker_finished` 无条件下 `self._parse_worker = None` → 新线程失去引用 → 取消/关闭时
`QThread: Destroyed while thread is still running`（进程直接 abort）。
修复：用 `self.sender()` 校验；`_on_parse_finished` 的行映射/帧数上限标志同样改为从 sender 读取。

### 5. 关闭输入标签页会把被关闭页的数据写进目标页

`removeTab()` 会同步触发 `currentChanged → _on_tab_changed`，此时 `_input_tab_list/_tab_data` 还没重排，
新索引上保存的是**被关闭页**的解析结果，随后重排再错位一格。
表现：关掉 A 之后 B 显示的是 A 的报文，再切页还会把错数据写回。
修复：保存当前页 → `blockSignals` 关闭 → 重排 → 显式 `_load_tab_state()`（新增回归测试）。

### 6. 模拟器/主窗口关闭时的 QThread 析构崩溃

- `simulator_dialog.closeEvent`：`wait()` 超时后仍然 `self._closing_sims.clear()`，线程引用丢失 → 运行中析构崩溃；
  且 `_gi_timer`、待确认操作未清理。
- `main_window.closeEvent`：5s 等待超时后照样退出，同样风险；且三个信号写在一个 `try` 里，
  第一个 `disconnect()` 抛 `TypeError` 后面的信号就漏断。
修复：超时的线程**保留引用**交给进程退出；逐个断开信号；主窗口超时用 `terminate()+wait()` 兜底。

---

## 二、中危缺陷（结果错、功能失效）

| # | 位置 | 问题 | 修复 |
|---|---|---|---|
| 7 | `iec104_parser` 5/6/32 | 步位置 VTI 按 8 位取符号（应为 7 位补码，bit7 保留）：`0x7F` 显示 +127 而非 -1 | `_to_signed(data[0] & 0x7F, 7)` |
| 8 | `iec104_parser` 15 | 累计量 BCR 按无符号解析（16/37 用补码），负计数显示成 40 亿 | 统一 `struct.unpack('<i')` |
| 9 | `iec104_parser._parse_asdu` | "补齐字段"按 `len(data)` 奇偶取最后一个字节，任何奇数长度 ASDU 都会凭空多出一个节点（值恰为最后信息字节） | 改为"未被消费且全 0 的尾部字节" |
| 10 | `frame_generator` | 不支持的类型落到 `else` 静默生成只有头部的错帧；IOA/公共地址/序号无校验；`vsq>1` 仍只生成 1 个对象 | 补全 2/4/6/8/10/12/14/16/30-37/51/58-64 等编码 + CP24/CP56；越界与不支持类型抛 `ValueError` |
| 11 | `pcap_importer` | IPv4 首片（`offset=0, MF=1`）被当成完整 TCP 段，负载被截断后注入；IPv6 分片头掩码漏 M 位 | `frag & 0x3FFF` / `frag_off & 0xFFF9` |
| 12 | `pcap_importer` | snaplen 截断包（IP 总长 > 实收字节）整包丢弃 → 用户只看到"导入结果为空"；IHL<20 未校验 | 按实收长度截取；`ihl < 20 → None` |
| 13 | `pcap_importer` | 时间戳用 `ts_sec % 86400`（UTC 时分秒），与界面/设备时标（本地时间）差一个时区 | `datetime.fromtimestamp()` |
| 14 | `license` | 时钟水位可被"未来日期"永久污染（主板电池掉电/手工改时间 → 授权永久过期且不可恢复） | 水位超前系统日期 >366 天视为脏数据忽略；已过到期日不再推进水位 |
| 15 | `license` | 授权文件 `code` 为 `null`/数字时 `load_license` 抛 `AttributeError`（启动闪退） | 先做类型判断，fail-closed |
| 16 | `parse_worker` | 取消/帧数上限两条提前返回路径不设置 `_frame_input_lines` → "输入行"列与 F2 定位失效 | 所有路径都赋值（提前绑定同一 list 对象） |
| 17 | `parse_worker` | 日志格式里 `13:25:00.692 RX:` 的时间与收发方向被整行丢弃 → 时间列恒 "-"，S/U 帧方向靠猜常标反 | 新增 `parse_log_format_frames()` 保留时间/方向，普通模式按日志元数据定方向 |
| 18 | `main_window._populate_table` | 数据源变化后视图回到全量，但过滤标志/搜索框还留着 → "过滤生效中"却是全量；清空搜索框会还原成 N 行幽灵空行 | `_populate_table` 复位过滤状态；`_start_parse/_on_clear` 清理书签与搜索框；别名修改改走 `_apply_current_view()` 保留条件 |
| 19 | `main_window` 键盘 | Enter/F2/Space 用 `len(all_frames)` 判断行号有效性（过滤/排序后视图行 ≠ 帧下标） | 统一走 `_frame_idx_at_row()`；书签标记由 `frame.index-1` 改为 `fi` |
| 20 | `simulator_dialog._op_end` | 等待反馈期间表格重建，`_op_end` 对已销毁按钮 `setText` → `RuntimeError` → PyQt5 终止进程 | 捕获 `RuntimeError`；定时器 `deleteLater()` |
| 21 | `simulator_dialog` 遥调 SBO | 执行时重新读编辑框（而非预制值）→ 违反 SBO"执行值=预制值"，从站回 COT=8；执行发送失败不释放预制锁，行永久停在"已预制" | 记录 `selected_value`、预制后锁定 spin、失败路径也复位 |
| 22 | `simulator_dialog` 四遥表格 | 所有列可双击编辑：改坏 IOA 列后该行再也匹配不到刷新目标；主站模式改"当前值"不入 `PointData` | 新增 `_PointTable`，只允许"描述"列编辑 |
| 23 | `simulator_dialog` 自动变化 | 每个 tick 全量重建 YC/YX 表格 → 编辑框被销毁、失焦、输入被覆盖 | 改为 `_update_yc_row()/_update_yx_row()` 定点刷新 |
| 24 | `generator_dialog` | COT 输入范围 1..65535（COT 域只有 1 字节）；用户无法生成"预制帧"（S/E 位会被生成器强制覆盖） | 范围改 1..255；新增"选择/预制（S/E=1）"勾选项，仅控制命令类型可用 |

---

## 三、文件架构调整与清理（已执行）

| 项 | 处理 |
|---|---|
| `构建方式.txt` | **删除**。它给出的 `--add-data=".;."` 会把 `gen_license.py` 与私钥打进发行包，与 `build.bat`/README 的安全约定相反，属危险过期文档 |
| 测试样本 | `XN3000测试报文.txt`/`XN8000测试报文.txt` → `tests/data/`，`test_phase1` 路径同步 |
| 评审过程文档 | `task_plan.md`/`findings.md`/`progress.md` → `docs/` |
| `gui/main_window.py` | 3200+ 行 → **2839 行**：把"虚拟表格行模型 + 单元格渲染"整层（`_populate_table/_set_view/_cell/_frame_cell/_frame_idx_at_row/_estimate_col_widths` 等 15 个方法、约 370 行）拆到新模块 **`gui/frame_table.py`（`FrameTableMixin`）**，主窗口改为 `class MainWindow(FrameTableMixin, QMainWindow)` |
| `.gitignore` | 重写：去重 `license.dat`，补 `.pytest_cache/`、`build/`、`dist/`、`.agents/`、`.claude/`、`.codebuddy/`，并注明私钥文件 |
| 未使用导入/变量 | `pyflakes` 全库清零（`main_window`/`frame_table`/5 个测试文件） |
| README | 项目结构树按实际文件重写（原列出的 `capture_dialog.py` 已删除）、打包命令改为与 `build.bat` 一致的 `--onefile`、补充 S/E 语义说明 |

---

## 四、剩余问题清单：已全部修复（2026-10-29 第二轮）

上一版列出的待修项已全部完成，逐条对应如下。

| # | 项 | 修复内容 |
|---|---|---|
| 34 | **自动检测把"带点分隔的 hex dump"判成 XN3000 → 0 帧** | 删除 `\b[0-9A-Fa-f]{2}\s+\.` 这条误判规则；新增 `iec104_parser.detect_input_format()` 作为**唯一**判定入口（只认 `HH:MM:SS(.mmm) RX/TX:` 与 `日- 时:分:秒.毫秒 send/recv:`）；`_auto_detect_mode` 改为调用它；`ParseWorker` 在日志模式收到 0 个标记行时**自动回退**按普通十六进制提取（双保险）。回归测试：`TestFormatDetection`（dump 判 NORMAL、XN3000 模式兜底仍能出 2 帧） |
| 35 | **pcap 无 TCP 流重组** | 新增 `read_pcap_frames()`：按四元组聚流 → `seq` 排序（含 32 位回绕处理）→ 重传重叠覆盖 → 拼成连续字节流 → 按 `68 + 长度域` 切帧（`4..253` 校验，尾部残帧安全丢弃），时间戳取该帧首字节所在 TCP 段。`pcap_to_hex_text()` 改用它；`read_pcap()` 保留逐包语义（调试用）。回归测试 4 项：跨段拼接、重传+乱序、单段多帧、截断尾部 |
| 36 | **`hex_to_bytes` 静默清洗非法字符** | 新增 `clean_hex_text()` 返回 `(cleaned, dropped)`；`ParseWorker.hex_warnings` 记录"第 N 行: 忽略 'xx'"，主窗口解析完成时在输入校验栏红字提示并把前 20 条放进 tooltip。回归测试：`TestHexWarnings` |
| 37 | **输入格式判定逻辑重复** | 与 #34 一并收敛为 `detect_input_format()`，`main_window._auto_detect_mode` 只剩 1 行委托 |
| 38 | **`main_window.py` 2906 行** | 拆出 `gui/ui_layout.py`（`UiLayoutMixin`，499 行，控件创建与摆位）与 `gui/frame_table.py`（`FrameTableMixin` + `FrameCellDelegate`，444 行）。主窗口 **2906 → 2389 行**，剩余为解析调度/定位/书签/授权等业务 |
| 39 | **`simulator_dialog.py` 2292 行** | 拆出 `gui/sim_point_tables.py`（`PointTablesMixin` + `_PointTable`，421 行，四遥表格创建/刷新/定点更新）与 `gui/sim_style.py`（颜色与按钮样式常量，避免两模块循环 import）。对话框 **2292 → 1873 行** |
| 40 | **协议常量两处维护** | 新增 `iec104_protocol.py`：`CONTROL_TYPE_IDS`/`MONITOR_TYPE_IDS`/`SE_BIT`/`RCO_SE_BIT`/`SE_BYTE_OFFSETS`/`SBO_COMMAND_TYPE_IDS` + `se_bit()/value_mask()/is_control_type()`。`iec104_parser` 与 `iec104_simulator` 全部改为引用（含 `_extract_sco`、`_handle_control_command`、`_command_se_bit`） |
| 41 | **标签页存档与当前数据共享 list** | `_load_tab_state` 改为 `_set_frames(list(data["frames"]))` |
| 42 | **`check_compliance` 不可达规则** | "APDU 超长(>253)"（解析阶段已拒绝，永不触发）替换为可达的"长度域与实际字节数不符"警告 |
| 43 | **`frame_generator` 死分支** | `if type_id in (58, 59, 60)` → `if type_id == 60`（58/59 已被前一支拦截） |
| 44 | **`parse_text_sync` 借用 QThread** | `ParseWorker.run()` 拆成"发信号"+"`parse(progress_cb, should_cancel)` 纯解析"；`parse_text_sync` 直接调 `parse()` 并 try/except 记录，不再调用未 `start()` 的 `run()` |
| 45 | `.gitignore` 缺 `logs/` | 已补 |
| 46 | 换行符不统一 | 新增 `.gitattributes`：`* text=auto eol=lf`，二进制资源（ico/png/docx/pcap/bin）显式标记 |
| 47 | `docs/` 残留过程文档 | `task_plan.md`/`findings.md`/`progress.md` → `docs/archive/` |
| — | `.pytest_cache` ACL 异常 | 仍是本机环境问题（非代码），已在 `.gitignore`；需管理员 `takeown /f .pytest_cache /r /d y` 后删除 |

---

## 五、验证

```
python -m pytest tests -q      # 269 passed
python -m pyflakes .           # 无输出
```

新增回归测试：`tests/test_protocol_regressions.py`（COT/ORG、S/E 极性与 RCO 位序、VTI/BCR 符号、
日志时间与方向、生成器参数校验、CP24 时标、APDU 长度），
`tests/test_pcap_importer.py`（分片 M 位、snaplen 截断、IHL、本地时间戳），
`tests/test_license.py`（水位污染、水位上限），
`tests/test_main_window.py`（关标签页数据隔离、清空后视图状态、模式回落、在飞结果丢弃、
崩溃钩子），`tests/test_frame_generator.py`（S/E 断言按标准改写 + RCO 用例）。

---

## 六、补充修复："解析 → 清空 → 导入 → 再解析"闪退（2026-10-29 追加）

### 6.1 根因定位：闪退 = 槽内异常 + PyQt5 的默认处置

PyQt5 对**槽函数里未捕获的 Python 异常**默认走 `qFatal()`：进程直接消失，
既没有异常框也没有日志——这正是"闪退"的表现。实测确认：

```python
sys.excepthook = custom_hook      # 装上自定义钩子后
s.sig.emit()   # 槽内 raise → 钩子被调用，进程继续存活
```

`main.py` 此前没有任何 `sys.excepthook` / `faulthandler`，所以哪怕只是一次
`RuntimeError: wrapped C/C++ object has been deleted`，用户看到的也是"窗口瞬间消失"。

**修复（结构性，先保证可诊断、不再静默死掉）**
新增 `main.py` 启动即安装的两级兜底：

| 层 | 机制 | 效果 |
|---|---|---|
| Python | `sys.excepthook` → 追加写 `logs/crash.log` + 非模态提示框 | 槽内异常不再终止进程，且留下完整 traceback |
| 原生 | `faulthandler.enable(file=...)` | 段错误/abort 时把调用栈写进同一份日志 |

日志位置：打包模式 `%APPDATA%/IEC104Parser/logs/crash.log`，源码模式项目下 `logs/crash.log`。

### 6.2 同路径上已确认并修复的缺陷

| # | 位置 | 问题 | 修复 |
|---|---|---|---|
| 27 | `_start_parse` / `_on_cancel_parse` / `closeEvent` | 直接 `self._parse_worker.isRunning()`：包装器指向已被 `deleteLater` 销毁的 QThread 时抛 `RuntimeError` → 槽内未捕获 → **闪退**（最符合用户描述的一处） | 统一走 `_live_worker()`，`RuntimeError` 时视为无线程 |
| 28 | `_auto_detect_mode` 应用处 | 自动检测只在"非 NORMAL"时切换：先解析过日志报文，`parse_mode` 永远停在 XN3000；再解析纯十六进制被按日志切块 → **0 帧**（"点了解析没反应"） | 无条件应用检测结果，含回落 NORMAL |
| 29 | `_on_clear` | 不处理"在飞"的解析：旧线程的 `parse_finished` 回来后又把刚清空的界面填满 | 取消 + 断开结果信号（线程自然收尾回收），并复位 `_frame_intervals`/`_current_frame_idx`/序列号 |
| 30 | `_on_parse_finished` | 未激活限制提示用 `box.exec_()` 在槽内再开事件循环，队列里还排着 worker 的 `finished/deleteLater`，会在嵌套循环里销毁发信号的对象 | 改为非阻塞 `open()` + `finished` 回调 |
| 31 | `_on_parse_finished` | 过期 worker 的迟到结果直接 `return`，按钮永远停在"解析中" | `return` 前恢复 `_set_parsing_state(False)` |
| 32 | `_highlight_input_line_for_frame` | `frame_index = -1` 时 `-1 >= len(map)` 为假 → 取到 `line_map[-1]` 高亮最后一条报文 | 显式拦负下标 |
| 33 | `license.py` / `simulator_dialog.py` / `ioa_alias_dialog.py` / `history_dialog.py` | 同一份 `_get_app_dir()` 抄了 4 遍 | 抽出 `app_paths.py`（`get_app_dir()` / `get_log_dir()`）作唯一实现，各模块保留 `_get_app_dir()` 兼容调用 |

### 6.3 若仍能复现

崩溃日志里会有完整 traceback 与调用栈，把 `%APPDATA%\IEC104Parser\logs\crash.log`
最后一段发出来即可定位到具体行——不再需要"靠描述猜"。

已离屏验证通过的用户路径：纯十六进制粘贴、文件导入（XN3000 287 帧 / XN8000 3552 帧）、
大报文解析中清空、连点解析、解析→清空→导入→再解析循环 5 次。

