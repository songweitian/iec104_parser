# Phase 1: Bug 修复 + 代码重构 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复已知 bug、消除重复代码、清理死代码、优化性能，为后续功能开发打好基础。

**Architecture:** 将 parser 和 GUI 之间的重复工具函数提取到 `iec104_parser.py` 模块级别，GUI 直接调用。修复 CP24Time2a 时标 bug。删除死代码和重复方法。为 IOA alias 添加缓存。

**Tech Stack:** Python 3.8+, PyQt5

---

## 文件变更总览

| 文件 | 操作 | 说明 |
|------|------|------|
| `iec104_parser.py` | 修改 | 修复 CP24Time2a、删除重复方法、提取公共函数、删除 `_old` 方法 |
| `gui/main_window.py` | 修改 | ParseWorker 和 MainWindow 改用公共函数 |
| `gui/hex_view.py` | 删除 | 空壳代码，无实际功能 |
| `gui/__init__.py` | 修改 | 移除 HexHighlighter 导入 |
| `gui/ioa_alias_dialog.py` | 修改 | 添加 alias 缓存 |
| `build.bat` | 修改 | 修正输出路径说明 |
| `tests/test_phase1.py` | 创建 | 回归测试 |

---

## Task 1: 修复 CP24Time2a 时标 bug

**Files:**
- Modify: `iec104_parser.py:491-509`

CP24Time2a 按标准不含小时信息，但当前实现将小时固定为 0，且没有让调用者传入上下文。修复方案：添加可选 `hour` 参数。

- [ ] **Step 1: 修改 `parse_cp24time2a` 函数**

在 `iec104_parser.py` 第 491 行，修改 `parse_cp24time2a` 函数签名，添加 `hour: int = 0` 参数：

```python
def parse_cp24time2a(data: bytes, hour: int = 0) -> datetime:
    """解析CP24Time2a格式时标 (3字节) - 用于 type_id=2/4/6/8/10/12/14/16
    Byte 0-1: 毫秒 (0-59999)
    Byte 2: 分钟 (bit0-5)
    注意: CP24Time2a 不含日期和小时信息，需要外部提供
    """
    if len(data) < 3:
        return datetime(2000, 1, 1)

    ms = data[0] + ((data[1] & 0xFF) << 8)
    minute = data[2] & 0x3F

    minute = min(59, minute)
    hour = max(0, min(23, hour))

    try:
        return datetime(2000, 1, 1, hour, minute, 0, ms * 1000)
    except ValueError:
        return datetime(2000, 1, 1)
```

- [ ] **Step 2: 更新所有 `parse_cp24time2a` 调用点**

在 `_parse_element_value` 方法中，所有调用 `parse_cp24time2a` 的地方，改为从当前帧的元数据推断小时。搜索 `parse_cp24time2a` 所有调用点：

```python
# 在调用 parse_cp24time2a 时传入 hour 参数
# 例如：
elem.timestamp = parse_cp24time2a(data[1:8], hour=hour_context)
```

在 `_parse_element_value` 方法顶部（约第 1210 行），添加从帧元数据获取小时上下文的逻辑。由于 `_parse_element_value` 没有直接访问帧的时标，需要在调用链中传递。最简方案：在 `_parse_asdu` 方法中，先解析出 hour 作为局部变量，传给 `_parse_element_value`。

- [ ] **Step 3: 验证**

手动用测试报文验证带时标的类型（TypeID 2, 4, 6, 8, 10, 12, 14）能正确显示小时。

---

## Task 2: 删除重复的 `set_master_send_first` 方法

**Files:**
- Modify: `iec104_parser.py:536-544`

`set_master_send_first` 方法定义了两次，完全相同。删除第二个（第 541-544 行）。

- [ ] **Step 1: 删除重复定义**

在 `iec104_parser.py` 中，删除第 541-544 行（第二个 `set_master_send_first` 定义）：

```python
# 删除以下重复代码（第541-544行）：
    def set_master_send_first(self, master_first: bool):
        """设置第一个报文是否由主站发出"""
        self.master_send_first = master_first
        self.last_master_send = master_first
```

保留第 536-539 行的第一个定义。

- [ ] **Step 2: 验证**

运行 `python -c "from iec104_parser import IEC104Parser; p = IEC104Parser(); p.set_master_send_first(True); print('OK')"` 确认无语法错误。

---

## Task 3: 提取公共工具函数

**Files:**
- Modify: `iec104_parser.py`（在文件顶部附近添加公共函数）
- Modify: `gui/main_window.py`（ParseWorker 和 MainWindow 改用公共函数）

将 `hex_to_bytes`、`is_log_format`、`parse_log_format`、`split_concatenated_frames` 提取为 `iec104_parser.py` 中的模块级公共函数。

- [ ] **Step 1: 在 `iec104_parser.py` 中添加公共函数**

在 `iec104_parser.py` 的 `# ============================================================================
# IEC104 帧解析器
# ============================================================================` 注释之前（约第 510 行），添加以下公共函数：

```python
# ============================================================================
# 公共工具函数
# ============================================================================

def hex_to_bytes(hex_string: str) -> bytes:
    """十六进制字符串 → 字节，支持空格/点/换行/0x前缀等格式"""
    cleaned = hex_string.replace(" ", "").replace("\n", "").replace("\r", "")
    cleaned = cleaned.replace("0x", "").replace("0X", "")
    cleaned = cleaned.replace(",", "").replace("[", "").replace("]", "")
    cleaned = cleaned.replace(".", "")
    cleaned = ''.join(c for c in cleaned if c in '0123456789ABCDEFabcdef')
    if len(cleaned) % 2 != 0:
        raise ValueError("十六进制字符串长度为奇数")
    return bytes.fromhex(cleaned)


def is_log_format(text: str) -> bool:
    """检测是否为通信日志格式（含时间戳和RX/TX标记或点分隔符）"""
    import re
    return bool(re.search(r'\d{2}:\d{2}:\d{2}\.\d{3}\s+(RX|TX):', text)) or \
           bool(re.search(r'\b[0-9A-Fa-f]{2}\s+\.', text))


def split_concatenated_frames(hex_block: str) -> list:
    """将包含多个拼接IEC104帧的十六进制字符串拆分为单帧列表"""
    cleaned = hex_block.replace(" ", "").replace(".", "")
    cleaned = ''.join(c for c in cleaned if c in '0123456789ABCDEFabcdef')
    if len(cleaned) < 12:
        return [hex_block] if cleaned else []

    frames = []
    i = 0
    while i < len(cleaned) - 1:
        if cleaned[i:i+2].lower() == '68':
            if i + 4 > len(cleaned):
                break
            try:
                apdu_len = int(cleaned[i+2:i+4], 16)
            except ValueError:
                i += 2
                continue
            frame_len = apdu_len + 2
            hex_len = frame_len * 2
            if i + hex_len <= len(cleaned):
                frames.append(cleaned[i:i+hex_len])
                i += hex_len
            else:
                frames.append(cleaned[i:])
                break
        else:
            i += 2
    return frames


def parse_log_format_blocks(text: str) -> list:
    """解析通信日志格式，返回 [(hex_block, timestamp, direction), ...]"""
    import re
    lines = text.strip().split('\n')
    blocks = []
    current_hex = []
    current_line_nums = []

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        m = re.match(r'[\d:.\s]+(RX|TX):\s*$', line)
        if m:
            if current_hex:
                blocks.append((' '.join(current_hex), list(current_line_nums)))
                current_hex = []
                current_line_nums = []
            continue

        m = re.match(r'^(RX|TX):\s*$', line)
        if m:
            if current_hex:
                blocks.append((' '.join(current_hex), list(current_line_nums)))
                current_hex = []
                current_line_nums = []
            continue

        if re.search(r'[0-9A-Fa-f]{2}', line):
            current_hex.append(line)
            current_line_nums.append(i)

    if current_hex:
        blocks.append((' '.join(current_hex), list(current_line_nums)))

    result_frames = []
    result_lines = []
    for block, src_lines in blocks:
        frames = split_concatenated_frames(block)
        result_frames.extend(frames)
        result_lines.extend([src_lines] * len(frames))
    return result_frames, result_lines
```

- [ ] **Step 2: 让 `IEC104Parser` 使用公共函数**

在 `IEC104Parser` 类中，修改 `_hex_to_bytes` 方法（第 986 行）为委托调用：

```python
    def _hex_to_bytes(self, hex_string: str) -> bytes:
        """十六进制字符串 → 字节"""
        return hex_to_bytes(hex_string)
```

- [ ] **Step 3: 修改 `gui/main_window.py` 的 `ParseWorker`**

在 `gui/main_window.py` 的 `ParseWorker` 类中：

1. 在文件顶部添加导入（在现有 import 区域）：
```python
from iec104_parser import hex_to_bytes, is_log_format, parse_log_format_blocks, split_concatenated_frames
```

2. 删除 `ParseWorker._hex_to_bytes` 方法（第 380-388 行），改用导入的 `hex_to_bytes`

3. 删除 `ParseWorker._is_log_format` 方法（第 180-182 行），改用导入的 `is_log_format`

4. 删除 `ParseWorker._split_concatenated_frames` 方法（第 230-257 行），改用导入的 `split_concatenated_frames`

5. 修改 `ParseWorker._parse_log_format` 方法（第 184-228 行），改为调用 `parse_log_format_blocks`

6. 修改 `ParseWorker._run()` 方法中调用这些方法的地方，改为调用公共函数

- [ ] **Step 4: 修改 `gui/main_window.py` 的 `MainWindow`**

在 `MainWindow` 类中：

1. 删除 `MainWindow._is_log_format` 方法（约第 739 行），改为调用公共函数
2. 删除 `MainWindow._split_concatenated_frames` 方法（约第 859 行），改为调用公共函数
3. 更新 `MainWindow` 中调用这些方法的代码

- [ ] **Step 5: 验证**

运行 `python main.py`，导入测试报文文件，确认解析结果与重构前一致。

---

## Task 4: 删除 `_old` 方法

**Files:**
- Modify: `iec104_parser.py:1834-1924`

`IEC104Parser` 类中有 4 个 `_old` 后缀的方法，已被公共函数替代，应删除。

- [ ] **Step 1: 删除 4 个 `_old` 方法**

在 `iec104_parser.py` 中删除以下方法（第 1834-1924 行）：

```python
# 删除以下方法：
    def _hex_to_lines_with_mapping_old(self, raw_text: str):
        ...
    def _is_log_format_old(self, text: str) -> bool:
        ...
    def _parse_log_format_old(self, text: str) -> list:
        ...
    def _split_concatenated_frames_old(self, hex_block: str) -> list:
        ...
```

确保这些方法没有被其他地方调用（搜索 `_old` 确认）。

- [ ] **Step 2: 验证**

运行 `python -c "from iec104_parser import IEC104Parser; print('OK')"` 确认无语法错误。

---

## Task 5: 删除 `gui/hex_view.py` 和清理导入

**Files:**
- Delete: `gui/hex_view.py`
- Modify: `gui/__init__.py`
- Modify: `gui/main_window.py`

`HexHighlighter` 类的两个方法都是空壳，且在 `main_window.py` 中虽有导入但未使用。

- [ ] **Step 1: 确认 `HexHighlighter` 未被使用**

搜索整个项目确认 `HexHighlighter` 仅在导入处出现，无实际调用。

- [ ] **Step 2: 从 `gui/main_window.py` 移除导入**

删除 `gui/main_window.py` 第 36 行：
```python
from gui.hex_view import HexHighlighter
```

- [ ] **Step 3: 修改 `gui/__init__.py`**

```python
"""
GUI 模块 - IEC104 报文解析器图形界面
"""

from .main_window import MainWindow
from .about_dialog import AboutDialog

__all__ = ['MainWindow', 'AboutDialog']
```

- [ ] **Step 4: 删除 `gui/hex_view.py` 文件**

```bash
del "gui\hex_view.py"
```

- [ ] **Step 5: 验证**

运行 `python main.py` 确认程序正常启动。

---

## Task 6: IOA Alias 缓存优化

**Files:**
- Modify: `gui/ioa_alias_dialog.py`

`get_alias()` 每次调用都读磁盘，在表格填充时被高频调用。

- [ ] **Step 1: 添加模块级缓存**

在 `gui/ioa_alias_dialog.py` 中，修改 `load_aliases` 和 `get_alias` 函数：

```python
import os
import json
import time
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QFileDialog, QMessageBox,
    QHeaderView, QLineEdit, QGroupBox, QFormLayout
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont


ALIAS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "ioa_aliases.json")

# 缓存相关
_cached_aliases: dict = {}
_cached_aliases_mtime: float = 0.0


def load_aliases() -> dict:
    """加载IOA别名映射 {ioa_str: alias_name}，带缓存"""
    global _cached_aliases, _cached_aliases_mtime
    try:
        mtime = os.path.getmtime(ALIAS_FILE) if os.path.exists(ALIAS_FILE) else 0
    except OSError:
        mtime = 0

    if mtime != _cached_aliases_mtime:
        _cached_aliases_mtime = mtime
        if os.path.exists(ALIAS_FILE):
            try:
                with open(ALIAS_FILE, "r", encoding="utf-8") as f:
                    _cached_aliases = json.load(f)
            except Exception:
                _cached_aliases = {}
        else:
            _cached_aliases = {}
    return _cached_aliases


def invalidate_alias_cache():
    """使别名缓存失效（在保存后调用）"""
    global _cached_aliases_mtime
    _cached_aliases_mtime = 0


def save_aliases(aliases: dict):
    """保存IOA别名映射"""
    with open(ALIAS_FILE, "w", encoding="utf-8") as f:
        json.dump(aliases, f, ensure_ascii=False, indent=2)
    invalidate_alias_cache()


def get_alias(ioa: int) -> str:
    """获取单个IOA的别名，无别名返回空字符串"""
    aliases = load_aliases()
    return aliases.get(f"0x{ioa:06X}", "")
```

- [ ] **Step 2: 在 `IOAAliasDialog` 保存后刷新缓存**

在 `IOAAliasDialog` 类中，找到调用 `save_aliases` 的地方，确保之后缓存自动失效（已在 `save_aliases` 中实现）。

- [ ] **Step 3: 验证**

运行 `python main.py`，打开 IOA 别名管理对话框，添加/修改别名，确认表格中即时生效。

---

## Task 7: 修正 build.bat 输出路径

**Files:**
- Modify: `build.bat:53-54`

`build.bat` 中输出路径写的是 `dist\IEC104_Parser.exe`，但 onedir 模式实际输出在 `dist\IEC104_Parser\IEC104_Parser.exe`。

- [ ] **Step 1: 修正路径说明**

将 `build.bat` 第 53-54 行：
```
echo  可执行文件位置:
echo  dist\IEC104_Parser.exe
```

改为：
```
echo  可执行文件位置:
echo  dist\IEC104_Parser\IEC104_Parser.exe
```

---

## Task 8: 添加回归测试

**Files:**
- Create: `tests/test_phase1.py`

为 Phase 1 的修复和重构添加基本回归测试。

- [ ] **Step 1: 创建测试文件**

```python
"""Phase 1 回归测试 - Bug修复和重构验证"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from iec104_parser import (
    IEC104Parser, ParseMode, hex_to_bytes, is_log_format,
    split_concatenated_frames, parse_cp24time2a, parse_cp56time2a
)
from datetime import datetime


class TestHexToBytes:
    def test_basic_hex(self):
        assert hex_to_bytes("68 04 01 00 0E 00") == b'\x68\x04\x01\x00\x0E\x00'

    def test_no_spaces(self):
        assert hex_to_bytes("680401000E00") == b'\x68\x04\x01\x00\x0E\x00'

    def test_dot_separator(self):
        assert hex_to_bytes("68.04.01.00.0E.00") == b'\x68\x04\x01\x00\x0E\x00'

    def test_0x_prefix(self):
        assert hex_to_bytes("0x68 0x04") == b'\x68\x04'

    def test_odd_length_raises(self):
        try:
            hex_to_bytes("68 0")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass

    def test_newlines(self):
        assert hex_to_bytes("68 04\n01 00") == b'\x68\x04\x01\x00'


class TestIsLogFormat:
    def test_rx_tx_format(self):
        assert is_log_format("13:25:00.692 RX:") is True

    def test_dot_separator_format(self):
        assert is_log_format("68.15.15.16.0a.00") is True

    def test_plain_hex(self):
        assert is_log_format("68 04 01 00 0E 00") is False

    def test_xn8000_format(self):
        assert is_log_format("17- 9:24:59.947 send:") is False


class TestSplitConcatenatedFrames:
    def test_single_frame(self):
        result = split_concatenated_frames("680401000E00")
        assert len(result) == 1
        assert result[0] == "680401000E00"

    def test_two_frames(self):
        block = "680401000E00" + "680E04001400090103000100014000460200"
        result = split_concatenated_frames(block)
        assert len(result) == 2

    def test_with_spaces(self):
        block = "68 04 01 00 0E 00 68 0E 04 00 14 00 09 01 03 00 01 00 01 40 00 46 02 00"
        result = split_concatenated_frames(block)
        assert len(result) == 2

    def test_empty(self):
        result = split_concatenated_frames("")
        assert result == []


class TestCP24Time2a:
    def test_basic(self):
        # ms=1000, minute=30
        data = bytes([0xE8, 0x03, 0x1E])
        result = parse_cp24time2a(data)
        assert result.minute == 30
        assert result.second == 0

    def test_with_hour(self):
        data = bytes([0xE8, 0x03, 0x1E])
        result = parse_cp24time2a(data, hour=15)
        assert result.hour == 15
        assert result.minute == 30

    def test_short_data(self):
        result = parse_cp24time2a(b'\x00\x00')
        assert result == datetime(2000, 1, 1)

    def test_minute_overflow(self):
        # minute byte = 0x7F (127), should be clamped to 59
        data = bytes([0x00, 0x00, 0x7F])
        result = parse_cp24time2a(data)
        assert result.minute == 59


class TestParserBasic:
    def test_s_frame(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 04 01 00 0E 00")
        assert frame.apci is not None
        assert frame.apci.frame_type == "S-format"

    def test_u_frame(self):
        parser = IEC104Parser()
        frame = parser.parse_hex("68 04 07 00 00 00")
        assert frame.apci is not None
        assert frame.apci.frame_type == "U-format"

    def test_parse_mode(self):
        parser = IEC104Parser(mode=ParseMode.XN3000)
        assert parser.mode == ParseMode.XN3000

    def test_set_master_send_first_no_duplicate(self):
        parser = IEC104Parser()
        parser.set_master_send_first(False)
        assert parser.master_send_first is False
        assert parser.last_master_send is False


class TestXNFormats:
    def test_xn3000_parse(self):
        parser = IEC104Parser(mode=ParseMode.XN3000)
        test_file = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                  "XN3000测试报文.txt")
        if os.path.exists(test_file):
            with open(test_file, "r", encoding="utf-8") as f:
                text = f.read()
            from iec104_parser import parse_log_format_blocks
            frames, lines = parse_log_format_blocks(text)
            assert len(frames) > 0

    def test_xn8000_parse(self):
        parser = IEC104Parser(mode=ParseMode.XN8000)
        test_file = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                  "XN8000测试报文.txt")
        if os.path.exists(test_file):
            with open(test_file, "r", encoding="utf-8") as f:
                text = f.read()
            from iec104_parser import parse_log_format_blocks
            frames, lines = parse_log_format_blocks(text)
            assert len(frames) > 0
```

- [ ] **Step 2: 运行测试**

```bash
cd F:\1.SongWeitian\1.project\iec104_parser
python -m pytest tests/test_phase1.py -v
```

预期：所有测试通过。

---

## Task 9: 最终验证

- [ ] **Step 1: 运行完整程序**

```bash
python main.py
```

导入 `XN3000测试报文.txt` 和 `XN8000测试报文.txt`，确认解析结果正确。

- [ ] **Step 2: 运行所有测试**

```bash
python -m pytest tests/ -v
```

- [ ] **Step 3: 确认无语法错误**

```bash
python -c "from iec104_parser import IEC104Parser, hex_to_bytes, is_log_format, split_concatenated_frames; print('All imports OK')"
python -c "from gui import MainWindow; print('GUI imports OK')"
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor: Phase 1 - 修复CP24Time2a时标bug，消除重复代码，清理死代码，优化IOA缓存

- 修复 parse_cp24time2a 丢失小时上下文的问题
- 删除重复的 set_master_send_first 方法
- 提取 hex_to_bytes/is_log_format/split_concatenated_frames 为公共函数
- 删除 4 个 _old 后缀的冗余方法
- 删除空壳 gui/hex_view.py 和 HexHighlighter 导入
- 为 IOA alias 添加文件修改时间缓存
- 修正 build.bat 输出路径说明
- 添加 Phase 1 回归测试"
```
