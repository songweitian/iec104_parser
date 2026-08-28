# IEC104 Parser 代码审核报告

- **审核日期**：2026-08-28
- **项目规模**：约 20 个 Python 文件（核心引擎 + GUI 模块 + 工具 + 测试）
- **测试结果**：`97 passed, 1 xpassed`（2.14s）
- **Lint**：无报错

---

## 一、总体评价

项目是一个功能完整的 IEC60870-5-104 报文解析桌面工具（PyQt5），工程化程度良好：

- **架构清晰**：核心协议引擎 `iec104_parser.py` 与 GUI 解耦，`parse_worker.py` 拆分后台线程逻辑，便于单测。
- **代码质量较高**：类型标注普遍、注释充分、命名规范、常量提取（`_CONTROL_TYPE_IDS` / `_MONITOR_TYPE_IDS` 等）。
- **测试覆盖不错**：97 个测试覆盖了解析、帧生成、授权、虚拟表格映射等关键逻辑。
- **安全意识好**：激活码采用 Ed25519 非对称签名、硬件指纹绑定、构建脚本排除 `gen_license.py`、私钥不入库。

整体为“可维护、可交付”的代码。以下按严重程度列出发现的问题与改进建议。

---

## 二、问题清单

### 🔴 高优先级

| # | 文件 | 问题 | 建议 |
|---|------|------|------|
| H1 | `tests/test_phase3.py:230` | `test_comment_lines_ignored` 标注 `xfail`（理由：`parse_from_text` 缺少 `_hex_to_lines_with_mapping_old` 方法），但**实际测试是通过的**（出现 1 xpassed）。说明 xfail 标记已过时，且注释中提及的“缺失方法”是历史残留信息。 | 移除该 `xfail` 标记；确认 `parse_from_text` 注释文本中关于旧方法的描述是否需要清理。 |
| H2 | `pcap_importer.py:63` | 时间戳格式化为 `f"{ts_sec // 3600:02d}:..."`，`ts_sec` 是自 epoch 起的绝对秒数，除以 3600 得到的是**自 1970 年起的总小时数**（如 480000+），而不是当日的小时。会导致时间戳显示异常，且影响 `_analyze_time_intervals` 的时间间隔计算。 | 应使用 `(ts_sec % 86400) // 3600`（当天小时）并附加日期，或改用 `datetime.fromtimestamp` 格式化。 |

### 🟡 中优先级

| # | 文件 | 问题 | 建议 |
|---|------|------|------|
| M1 | `gui/parse_worker.py:20-35` | `CaptureWorker` 是空壳：`run()` 内只有 `pass`，`packet_received` 信号从未发出，`main_window.py:135` 中 `capture_worker` 恒为 `None`，存在“预留未实现”的死代码。 | 若短中期不实现抓包，建议移除该类及主窗口相关字段；或补上 TODO 说明并隐藏入口。 |
| M2 | `gui/parse_worker.py:179` 与 `iec104_parser.py` | `_split_concatenated_frames` 实例方法与 `split_concatenated_frames` 模块级函数**逻辑重复**（两处各自实现了一段拼接帧切分逻辑）。 | 统一收敛到 `iec104_parser.split_concatenated_frames`，删除重复实现，避免两处逻辑漂移。 |
| M3 | `gui/parse_worker.py:264-284` | 普通模式（NORMAL）下帧方向（主站/子站）通过 `last_master_send` **交替猜测**（`not last_master_send`），若输入中同向帧连续出现（如连续 5 条 RX），方向会被误判。仅依赖 type_id 的部分纠偏无法覆盖 S/U 帧。 | 普通模式建议提供“首帧方向”设定项；或将方向猜测改为基于 type_id 的确定性推断，仅对无类型帧使用交替规则。 |
| M4 | `gui/main_window.py` | `main_window.py` 约 3300 行，虽已拆分出 `parse_worker`/`icons`/`style`，但窗口类仍承担了 UI 布局、解析调度、导出、过滤、书签、主题、历史、拖放等过多职责。 | 可将“导出逻辑”“过滤/搜索状态”“会话持久化”继续拆分为独立模块，降低单文件复杂度。 |
| M5 | `frame_generator.py:61` | 48/49/50 分支注释写“带时标”，但 `C_SE_NA_1/NB_1/NC_1`（48/49/50）本身**不带时标**，而代码却追加了 `_generate_cp56time2a()`，生成结果与标准不符。 | 核对标准：48/49/50 不应附加 CP56Time2a；带时标版本应为 C_SE_TA_1/TB_1/TC_1（61/62/63），需补充实现或修正注释。 |
| M6 | `pcap_importer.py:69-126` | `_extract_tcp_payload` 仅支持 Ethernet (1) / RAW (101) 链路类型，**不支持 IPv6、VLAN 多标签、IP 分片重组**；方向判断基于端口 2404 的单向假设。 | 增加对 VLAN (0x88A8/0x8100 多级) 与 IPv6 的处理；对无法判断方向的情况返回未知方向而非默认 TX。 |

### 🟢 低优先级 / 代码卫生

| # | 文件 | 问题 | 建议 |
|---|------|------|------|
| L1 | `license.py:35-36` | `_LEGACY_CODE_RE` 与 `is_legacy_code` 仅用于提示“旧码已停用”，但新 Ed25519 码为 86 字符 base64url，与旧格式区分明显，这段兼容代码可保留但建议标注“下版本可移除”。 | 增加版本注释或删除阈值。 |
| L2 | `gen_license.py:52` | `os.chmod` 在 Windows 下无效但被 `try/except OSError` 吞掉——可保留，但建议改为平台判断并在 Windows 上打印提醒。 | 平台化处理文件权限提示。 |
| L3 | `gui/main_window.py` 主题 QSS | 深色/浅色主题的大段 QSS 字符串内联在方法中，`_apply_theme_to_dialog` 仅简单继承父样式表，部分对话框（如 stats/compare）深色模式下可能样式不完整。 | 将 QSS 提取为常量模块，统一管理。 |
| L4 | `build.bat` | 依赖安装使用清华镜像、无版本锁定，且未校验 Python 版本是否满足 `>=3.8`（仅检测 `python --version` 存在）。 | 在脚本中校验 Python 主版本；建议使用 `requirements.txt` 的 `-r` 安装。 |
| L5 | 版本号维护 | `main.py:35`、`gui/about_dialog.py:14-17` 两处硬编码版本号 `1.1.0`。 | 提取到统一常量（如 `__version__.py`），避免发版漏改。 |
| L6 | `tests/test_phase3.py` | 1 个 `xpassed` 已覆盖（H1）；测试文件间存在对 `L.LICENSE_FILE` 的猴子补丁，建议提取 fixture 统一处理。 | 提取 conftest fixture。 |

---

## 三、做得好的点（建议保持）

1. **授权安全模型**：Ed25519 验签 + 客户端仅内置公钥 + 私钥不入库/不入包，`build.bat` 用 `--exclude-module gen_license` 防止签码工具泄漏，思路正确。
2. **后台解析不阻塞 UI**：`ParseWorker` 在独立线程内创建**解析器副本**（`IEC104Parser(self.parse_mode)`），避免线程间共享解析器状态；取消使用协作式检查点而非 `terminate()`，处理得当。
3. **大表格性能**：`_view_order` + `_frame_to_row` 反查表 + QStyledItemDelegate 虚拟化渲染，`test_table_virtual.py` 对映射一致性有专项回归测试，设计严谨。
4. **协议覆盖完整**：TypeID 枚举、CP56Time2a/CP24Time2a、品质描述词、VSQ、COT 等标准字段均有覆盖，且多输入格式（XN3000/XN8000/日志/pcap）有专门的预处理。
5. **错误路径处理**：输入校验、文件导入大小限制、授权过期/未激活时的分级提示（仅解析第 1 条）设计合理。

---

## 四、建议的整改顺序

1. **立刻修复**：H1（xfail 标记清理）、H2（pcap 时间戳 bug）。
2. **近期改进**：M1（死代码）、M2（重复函数）、M5（帧生成标准性）。
3. **结构优化**：M4（main_window 继续拆分）、L3（QSS 常量化）、L5（版本号统一）。
4. **长期**：M3（方向判定增强）、M6（pcap 支持 IPv6/VLAN）。

---

## 五、结论

项目整体质量良好，**无阻断性问题**，测试全部通过。建议优先处理 H1/H2 两个高优先级问题（一个影响测试可信度、一个影响 pcap 导入功能的正确性），其余按优先级逐步完善即可。

---

## 六、修复记录（2026-08-28）

| 编号 | 内容 | 状态 |
|------|------|------|
| H1 | 移除 `tests/test_phase3.py` 中过时的 `xfail` 标记 | ✅ 已修复 |
| H2 | `pcap_importer.py` 时间戳改为按当天折算时分秒（`ts_sec % 86400`） | ✅ 已修复 |
| M1 | 移除 `CaptureWorker` 空壳类及 `main_window.py` 相关字段/导入 | ✅ 已修复 |
| M2 | 删除 `ParseWorker._split_concatenated_frames` 重复实现，测试改用模块级 `split_concatenated_frames` | ✅ 已修复 |
| M5 | `frame_generator.py`：48/49/50 不再误附 CP56Time2a；新增 61/62/63 带时标设定命令分支；修正 `TYPE_INFO` 中 50 的命名（C_SE_TA_1 → C_SE_NC_1） | ✅ 已修复 |

**验证结果**：全部 98 个测试通过（原为 97 passed + 1 xpassed），无 Lint 错误。
- 帧生成器冒烟验证：48 帧 APDU=0x10（无时标）、61 帧 APDU=0x17（含 7 字节时标），符合 IEC 104 标准。
- pcap 导入冒烟验证：epoch 时间戳正确折算为当日小时（0~23），时间间隔分析不再受影响。

---

## 七、修复记录（第二批，2026-08-28）

| 编号 | 内容 | 状态 |
|------|------|------|
| L5 | 新增 `version.py` 统一管理版本号/应用元数据；`main.py`、`gui/main_window.py`、`gui/about_dialog.py`、`generate_doc.py` 全部改为导入，消除 4 处硬编码 | ✅ 已修复 |
| M6 | `pcap_importer._extract_tcp_payload` 重写：支持 IPv6（含扩展头链）、多级 VLAN（0x8100/0x88a8/0x9100 QinQ）、IPv4/IPv6 分片非首片防护、RAW(101) 链路类型 | ✅ 已修复 |
| M3 | NORMAL 模式方向判定增强：I 帧 type_id 修正后同步 `last_master_send` 与 `_last_i_sender` 状态；S 帧依据最近 I 帧发送方推断方向（不再盲目交替） | ✅ 已修复 |
| L3 | 深色主题 QSS 从 `main_window._apply_dark_theme` 内联提取为 `gui/style.py` 的 `DARK_QSS` 常量，与 `LIGHT_QSS` 统一维护 | ✅ 已修复 |

**第二批验证结果**：
- 98 个测试全部通过，无 Lint 错误。
- M3 冒烟验证：连续 5 条同向监视帧均正确判为"子站→主站"；主站总召唤 I 帧后的 S 帧正确推断为"子站→主站"（此前交替猜测会误判）。
- M6 冒烟验证：构造 IPv6 + 双层 VLAN（0x8100/0x88a8）pcap 包，正确提取 IEC104 负载与方向。
- GUI 冒烟验证：标题、版本标签 v1.1.0、深浅主题切换均正常。

---

## 八、修复记录（第三批：M4 结构拆分，2026-08-28）

| 编号 | 内容 | 状态 |
|------|------|------|
| M4 | 新增 `gui/exporters.py`：CSV/JSON/TXT 三种导出的数据组装逻辑从 `MainWindow` 提取为纯函数（`export_csv`/`export_json`/`export_txt`），GUI 只保留文件对话框/写盘/状态提示 | ✅ 已修复 |
| M4 | `main_window.py` 新增 `_visible_frame_indices()` 辅助方法，三个导出入口共用 | ✅ 已修复 |
| M4 | 删除 `main_window.py` 中 `_hex_to_lines` 系列 4 个死代码方法（功能已被 `parse_worker._extract_hex_lines` 取代，无任何调用点），并清理相关导入 | ✅ 已修复 |

**第三批验证结果**：
- 新增 `tests/test_exporters.py` 11 个用例（CSV/JSON/TXT 结构、过滤导出、空帧），全部通过。
- 完整测试套件 **109 个全部通过**，无 Lint 错误。
- `main_window.py` 从约 3300 行降至 **3001 行**。
- GUI 冒烟验证：解析 2 条帧 → 表格填充 → 三种导出输出正确。

---

## 九、修复记录（第四批：收尾 + 测试补全，2026-08-28）

| 编号 | 内容 | 状态 |
|------|------|------|
| L1 | `license.py` 旧版 HMAC 兼容代码：补充 TODO 移除计划注释（`is_legacy_code` 仍被 `activate` 换码提示使用，予以保留） | ✅ 已修复 |
| L2 | `gen_license.py`：`chmod` 改为平台判断（仅 POSIX 执行），Windows 下打印权限提示 | ✅ 已修复 |
| L4 | `build.bat`：增加 Python ≥3.8 版本校验；依赖安装改为统一按 `requirements.txt`（消除版本漂移） | ✅ 已修复 |
| L6 | 新增 `tests/conftest.py` 集中处理 sys.path 注入 / 离屏 Qt / 授权文件隔离 / `qapp` fixture；`test_table_virtual.py` 兼容重复设置 | ✅ 已修复 |
| — | 新增 `tests/test_frame_generator.py` 14 个用例：帧结构（APCI/控制域/长度）、各类型 ASDU 布局、M5 时标回归、APDU 长度一致性 | ✅ 已修复 |
| — | 新增 `tests/test_simulator.py` 15 个用例：U 帧握手状态机、I 帧接收序号管理、W 窗口 S 帧、从站自动回复、点位管理、状态重置 | ✅ 已修复 |

**第四批验证结果**：完整测试套件 **138 个全部通过**（此前 109），无 Lint 错误。

**全部审核问题已修复完毕；计划项一、二（收尾 + 测试补全）已全部完成。**

---

## 十、修复记录（第五批：工程化，2026-08-28）

| 编号 | 内容 | 状态 |
|------|------|------|
| — | **依赖锁定**：`requirements.txt` 增加版本区间（`PyQt5>=5.15.0,<5.16`、`cryptography>=42,<47`） | ✅ 已修复 |
| — | **CI 接入**：新增 `.github/workflows/ci.yml` —— Ubuntu 上 Python 3.8/3.10/3.13 三矩阵跑 pytest；Windows 上跑测试 + PyInstaller 打包 + 产物安全断言（不含 `gen_license`/私钥）+ 上传产物 | ✅ 已修复 |
| — | **打包发布验证**：实际执行 PyInstaller 打包成功（`dist/IEC104_Parser/IEC104_Parser.exe`，约 102MB）；产物递归扫描确认无 `gen_license` / `license_ed25519.key`；exe 实际启动 6 秒正常运行 | ✅ 已通过 |

**第五批验证结果**：
- `requirements.txt` 与 CI YAML 均通过本地校验（YAML 解析成功，matrix/needs 正确）。
- 打包产物安全检查通过、exe 冒烟启动通过；`build/`、`dist/`、`.pytest_cache/` 均已被 .gitignore 覆盖，不入库。
- 完整测试套件 138 个全部通过。

**全部计划项（收尾 → 测试补全 → 打包验证 → CI/依赖锁定）已全部完成。**

---

## 十一、功能增强：遥控遥调 SBO 预制流程（2026-08-28）

**背景**：审查发现模拟器遥控遥调（45/46/47/48/49/50）不支持 IEC 104 标准的 SBO（select-before-operate，先选择后执行）安全操作模式——收到 COT=6 命令直接改值并回确认，无选择锁定。

**实现内容**：
| 模块 | 改动 |
|------|------|
| `frame_generator.py` | `generate_i_frame` 新增 `select` 参数；45/46/47 遥控命令、48/49/50/61/62/63 设点命令正确编码 S/E（选择/执行）位 |
| `iec104_simulator.py` | 从站实现 SBO 状态机 `_select_locks`：选择→COT=7(选择确认)；执行校验→COT=7(执行确认)；未预制/值不一致→COT=8(激活终止)；新增 **K 发送窗口**限制与 S 帧确认序号跟踪（此前 `k` 参数未落地）；连接重置清理预制锁 |
| `gui/simulator_dialog.py` | YK 遥控表格增加"预制"复选框：勾选发选择命令，取消直接执行 |

**审查发现的其他标准不符点**（本次一并修复）：
1. **K 发送窗口未实现**：`k` 定义了但从不校验未确认 I 帧数 → 已实现 `_unacknowledged_i` 计数 + `send_i_frame` 达到上限暂缓发送
2. **S 帧确认未跟踪**：`_handle_s_frame_received_raw` 不更新确认序号 → 已按 `send_seq - rn` 缩减未确认窗口
3. **遥调设点命令 S/E 位**：48/49/50 的 QOS S/E 位此前未编码 → 已支持

**验证**：新增 SBO 全流程/未预制拒绝/值不一致拒绝 + S/E 位编码共 7 个用例；**完整测试 145 个全部通过**；端到端冒烟验证 4 步 SBO 流程（未预制→COT8、选择→COT7、值不一致→COT8、预制后执行→COT7+点位生效）全部正确。提交 `a82b44c`。
