# IEC104 Parser 全面改进设计方案

## 1. 概述

本方案对 IEC104 Parser 项目进行分阶段改进，涵盖 Bug 修复、代码重构、协议补全、测试覆盖、GUI 优化和新功能开发。

## 2. 分阶段路线图

| 阶段 | 内容 | 依赖 |
|------|------|------|
| Phase 1 | Bug 修复 + 代码重构 | 无 |
| Phase 2 | 补全缺失 TypeID 解析 | Phase 1 |
| Phase 3 | 添加测试覆盖 | Phase 1 |
| Phase 4 | GUI 优化 | Phase 1 |
| Phase 5 | 新功能开发 | Phase 1, 2 |

---

## Phase 1: Bug 修复 + 代码重构

### 1.1 修复 CP56Time2a/CP24Time2a 时标 bug

**问题**：`parse_cp24time2a()` 总是返回小时=0，丢失了小时上下文。CP24Time2a 按标准只含分钟和毫秒（需外部时基），但当前实现完全丢弃了小时信息。

**方案**：为 CP24Time2a 解析函数添加可选的 `hour` 参数，允许调用者传入当前小时上下文（从帧元数据或最近的 CP56Time2a 帧推断）。对于无法确定小时的情况，保留默认值 0 但标注为"小时未知"。

**涉及文件**：`iec104_parser.py`（`parse_cp24time2a` 函数，约第 650 行）

### 1.2 删除重复方法 `set_master_send_first`

**问题**：`IEC104Parser` 类中 `set_master_send_first()` 方法定义了两次（第 536-539 行和 541-544 行），完全相同。

**方案**：删除其中一个重复定义。

**涉及文件**：`iec104_parser.py`

### 1.3 消除 parser 与 GUI worker 之间的重复代码

**问题**：以下逻辑在 `IEC104Parser` 和 `ParseWorker`/`MainWindow` 中存在重复：

| 重复函数 | parser 位置 | GUI 位置 |
|----------|-------------|----------|
| `_hex_to_bytes()` | 第 986 行 | `ParseWorker` 第 380 行 |
| `_split_concatenated_frames()` | — | `ParseWorker` 第 230 行, `MainWindow` 第 859 行 |
| `_is_log_format()` | — | `ParseWorker` 第 180 行, `MainWindow` 第 739 行 |
| `_parse_single_frame()` | `parse_hex()` | `ParseWorker` 第 400 行 |

**方案**：
1. 将 `_hex_to_bytes()`、`_is_log_format()`、`_split_concatenated_frames()` 提取为 `iec104_parser.py` 中的公共工具函数（或模块级函数）
2. `ParseWorker` 和 `MainWindow` 直接调用这些函数，不再重复实现
3. 删除 `IEC104Parser` 中 4 个 `_old` 后缀的方法（`_hex_to_lines_with_mapping_old` 等）
4. 简化 `ParseWorker._run()` 方法，使其复用 parser 的输入处理能力

### 1.4 清理死代码

**问题**：
- `gui/hex_view.py` 仅 23 行，`HexHighlighter` 的两个方法都是空壳，实际高亮逻辑在 `main_window.py` 中内联实现
- `gui/capture_dialog.py` 是空壳（84 行 UI + 空 `CaptureWorker`）
- 项目根目录有 3 个调试临时文件 `_dream_tmp*.py`

**方案**：
- 删除 `gui/hex_view.py`（无任何有效代码）
- `capture_dialog.py` 保留（作为 Phase 5 网络抓包功能的 UI 基础）
- 删除 `_dream_tmp*.py` 临时文件
- 在 `build.bat` 中修正输出路径说明

### 1.5 IOA Alias 加载优化

**问题**：`get_alias()` 每次调用都从磁盘读取 JSON 文件，在表格填充时被频繁调用（每个元素每帧调用一次），造成不必要的 I/O。

**方案**：在模块级别添加缓存，仅在文件修改时间变化时重新加载。

**涉及文件**：`gui/ioa_alias_dialog.py`（`get_alias()` 函数）

### 1.6 Phase 1 涉及的文件清单

| 文件 | 操作 |
|------|------|
| `iec104_parser.py` | 修复 CP24Time2a、删除重复方法、提取公共函数、删除 `_old` 方法 |
| `gui/main_window.py` | 重构 `ParseWorker` 和 `MainWindow` 使用公共函数 |
| `gui/hex_view.py` | 删除 |
| `gui/__init__.py` | 移除 hex_view 导入 |
| `gui/ioa_alias_dialog.py` | 添加 alias 缓存 |
| `build.bat` | 修正路径说明 |
| `_dream_tmp*.py` | 删除 |

---

## Phase 2: 补全缺失 TypeID 解析

### 2.1 已定义但未解析的 TypeID

在 `_parse_element_value()` 中添加以下类型的值解析逻辑：

| TypeID | 名称 | 数据格式 |
|--------|------|----------|
| 5 | M_ST_NA_1 | 步位置信息（2字节值 + QDS） |
| 6 | M_ST_TA_1 | 步位置 + CP24Time2a + QDS |
| 7 | M_BO_NA_1 | 32比特串（4字节） |
| 8 | M_BO_TA_1 | 32比特串 + CP24Time2a |
| 12 | M_ME_TB_1 | 标度化值 + QDS + CP24Time2a |
| 14 | M_ME_TC_1 | 短浮点 + QDS + CP24Time2a |
| 16 | M_IT_TA_1 | 累计量 + QDS + CP24Time2a |

### 2.2 标准中缺失的 TypeID

在 `TypeID` 枚举中添加并实现：

| TypeID | 名称 | 说明 |
|--------|------|------|
| 108 | P_ME_NA_1 | 参数（归一化值） |
| 109 | P_ME_NB_1 | 参数（标度化值） |
| 110 | P_ME_NC_1 | 参数（短浮点） |
| 120 | F_FR_NA_1 | 文件 ready |
| 121 | F_SR_NA_1 | 文件 select |
| 122 | F_SC_NA_1 | 文件 call/abort |
| 123 | F_LS_NA_1 | 文件 last section |
| 124 | F_FA_NA_1 | 文件确认 |
| 125 | F_SG_NA_1 | 文件 segment |
| 126 | F_DR_TA_1 | 文件目录 |
| 127 | F_SC_NB_1 | 文件 call/abort (带名称) |

---

## Phase 3: 测试覆盖

### 3.1 测试框架选择

使用 `pytest`，添加 `pytest.ini` 或 `pyproject.toml` 配置。

### 3.2 测试计划

| 测试类别 | 覆盖内容 |
|----------|----------|
| APCI 解析 | I/S/U 帧检测、序列号、长度验证 |
| ASDU 解析 | COT、VSQ、公共地址、信息对象地址 |
| 各 TypeID | 逐类型构造测试报文，验证值解析 |
| 时标解析 | CP56Time2a、CP24Time2a 边界值 |
| 输入格式 | 普通十六进制、XN3000/XN8000 日志格式、混合格式 |
| 序列号连续性 | 正常、跳号、回绕 |
| 合规性检查 | 各种违规场景 |

---

## Phase 4: GUI 优化

### 4.1 表格列排序

修复 `setSortingEnabled(True)` 的排序问题，确保各列按正确数据类型排序（数值列按数值、字符串列按字母）。

### 4.2 IOA 范围过滤

在过滤面板中添加 IOA 起始/结束地址输入框。

### 4.3 书签/标注功能

允许用户在报文列表中添加书签标记，支持书签列表快速跳转。

### 4.4 交互式统计图表

为统计对话框中的图表添加鼠标悬停提示和点击穿透功能。

---

## Phase 5: 新功能开发

### 5.1 PCAP 导入

支持导入 Wireshark `.pcap` / `.pcapng` 文件，提取 TCP 负载中的 IEC104 数据。

### 5.2 报文生成/仿真

根据用户输入的参数（TypeID、IOA、值等）生成符合 IEC104 标准的报文帧。

### 5.3 网络抓包

实现 `capture_dialog.py` 的实际功能，使用 `scapy` 或 `pyshark` 进行实时抓包。

### 5.4 趋势可视化

在新窗口中以折线图展示测量值随时间的变化趋势。

### 5.5 会话持久化

支持保存/加载解析会话（输入报文 + 解析结果 + IOA 别名 + 书签）。
