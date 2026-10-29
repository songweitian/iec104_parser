# 修复过程中的发现

## 2026-09-08 全局审核结论（基线）
- 协议解析核心 `iec104_parser.py` 正确，无需改动
- TCP 拆包粘包重组（`iec104_simulator.py:361-407`）正确
- frozen/onefile 数据路径全部走 `_get_app_dir()`，正确

## 待修复项的关键技术细节

### compare_dialog（第一批 #1 #2）
- `all_keys` 定义于 139 行；`diffs` 元组结构：`(status, idx_a, ea, val_a, idx_b, eb, val_b)`
- 修复方案：把 `fa`/`fb` 加进 diffs 元组，解包处同步修改
- 注意 "仅A" 分支 `fb` 为 None、"仅B" 分支 `fa` 为 None，需判空

### main_window 书签导航（第一批 #3 #4）
- `_goto_next_bookmark:2621` 回绕用 `sorted()[0]` 正确（跳到最小）
- `_goto_prev_bookmark:2634` 回绕也用了 `[0]`，应改为 `[-1]`（跳到最大）
- `_frame_idx_at_row:1763-1767` 越界返回 `row` 是危险兜底，需改 `-1`
  - 但 `_table_key_press`(2663/2671) 用 `current_row < len(self.all_frames)` 判断，需一并核对

### parse_worker（第一批 #5）
- `parse_text_sync` 直接调 `worker.run()`（同步执行，非 start）
- `parse_error` 信号已定义但无人连接 → 异常静默
- 修复：连接 parse_error 并记录/抛出

### 模拟器 K 窗口（第二批 #6）
- `send_i_frame:754` `if self._unacknowledged_i >= self.k: return False`
- k 默认 12，默认点位 6YX+8YC=14 → 超窗丢弃
- ACTTERM(COT=10) 也走 send_i_frame，同样被吞
- 需区分"突发上送"与"控制类必须发出"，或改为入队等待 S 帧确认

### 模拟器变化上送（第二批 #7）
- `simulator_dialog._on_start:1219` `self.simulator.points = dict(self._current_points)` 浅拷贝
- `set_point_value:652` `old_val = pt.value` 但调用方已先写入新值
- 修复：改为深拷贝，或由 set_point_value 独占写入

### license 机器绑定（第三批 #13）
- `license.dat` = base64(JSON{fingerprint, code, holder, components})，**未签名**
- `get_license_status:317` 用 `data["fingerprint"]`（文件自带）验签
- 攻击：改 components 即可过 `_machine_matches`
- 修复必须保持激活码格式不变（`<sig>` 或 `<sig>-YYYY-MM-DD`），
  改为用**当前机器**指纹验签 + components 纳入签名载荷（components 摘要需在 gen_license 侧同步改）

### license 崩溃（第三批 #14）
- `_decode_signature:152-155` 的 `base64.urlsafe_b64decode` 可抛 `binascii.Error`
- 调用点 181/188 在 `_verify_signature` 的 try **之外**
- 全仓无 `sys.excepthook` → PyQt5 qFatal 终止
- 修复：`_decode_signature` 内 try/except 返回 b""（验签失败即无效，fail-closed）
