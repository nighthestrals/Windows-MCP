# 控制权指示灯 UX（DSH 定制分支）

- 分支：`dsh/custom`，基线 `upstream/main @ b04359e`（Windows-MCP 0.8.7 之后的 main）
- 目的：把上游的控制权子系统改造成「蓝 / 绿 / 红 + 黄闪」指示灯方案，并**保留**上游对物理输入的屏蔽语义。
- 约束：上游已实现的接管机制（hook、InputLedger、overlay、工具门禁）尽量复用，只改行为策略与视觉。

## 1. 目标行为

| 状态 | 触发 | 边框 | 物理输入 |
| --- | --- | --- | --- |
| `ai` + 有正在执行的调用 | AI 调工具 | **蓝**（呼吸） | 被屏蔽 |
| `ai` + 无活动调用，15 秒租约内 | 调用刚结束 | **绿** | **不屏蔽** |
| 蓝/绿期间鼠标移动 | 物理移动 | **持续黄闪**，直到双击或鼠标停住 | 被屏蔽 |
| `paused` | 双击左键 / 热键 | **红**（常亮） | **不屏蔽** |
| 恢复后第一次调用 | 再双击 / 热键 | 红 → 蓝 | 被屏蔽（等同正常工作） |
| 租约未续期到期 / 无控制权 | — | 消失 | 不屏蔽 |

## 2. 状态机

状态集合：`unavailable` / `ready` / `ai` / `paused`（新增）。

- `ready`：无 AI 控制，边框不显示。
- `ai`：AI 持有控制权。**是否屏蔽物理输入只取决于 `_active_calls > 0`**（蓝色）。租约空闲期（绿色）不屏蔽、也不续期。
- `paused`：用户显式暂停。粘性状态，只有再次双击/热键才会离开；期间所有工具被拒绝。
- `unavailable`：hook / 指示灯失效，fail-open。
- 上游的 `user` / `takeover_pending` 不再由**鼠标移动**触发；`user` 仅作为内部冷却态保留（rehook 恢复、启动时近期有输入的情况）。

关键差异（相对上游）：

1. 鼠标移动**不再**触发让位（删除 `_candidate_locked` 的移动路径、`mouse_takeover_*_pixels/units` 阈值判定、移动时设置 `_fast_pending`）。
2. 屏蔽不再无条件持续：仅 `ai` 且 `_active_calls > 0` 时由看门狗续期；租约空闲（绿）时 `_suppress = False`。
3. 新增 `paused` 状态与「恢复后必须先观察」的强制约束。

## 3. 黄闪

- 触发：**非注入**的物理鼠标移动（`LLMHF_INJECTED` 的 AI 注入事件直接放行不参与），位移 ≥ 2px 去抖。
- 表现：整框变黄，脉冲周期 0.6s；只要持续移动就持续闪。
- 结束：停止移动 0.35s 后回到底色（蓝或绿）。
- 副作用：**无**。不改变控制状态、不打断正在执行的调用、不设置 `_fast_pending`、不阻塞新调用。

## 4. 双击暂停 / 恢复

- 检测（在 `physical_mouse` 钩子里，物理事件）：两次左键 down 间隔 ≤ `GetDoubleClickTime()`（默认 500ms）、两次位置位移 ≤ 6px。
- 手势事件（这两次 down/up）一律 `return 1` 吞掉，避免误触应用。
- **暂停** (`pause_by_user()`)：
  1. `input_ledger.release_all()`（弹起 AI 按住的键/鼠标键，避免卡键）
  2. `_suppress = False`（立即停止屏蔽）
  3. `_set_locked("paused")` → 通知
  4. 正在执行的调用在下一个 `checkpoint` 抛 `CONTROL_PREEMPTED`
- **恢复** (`resume_by_user()`)：
  1. `_set_locked("ready")`
  2. 作废 UI 元素缓存（内容已被用户改动，旧元素 id 必须失效）
  3. `_resume_observation_required = True`
  4. 通知
- 局限：单次长调用（如 `PowerShell` 长命令）无法中途掐断，只在下一个检查点生效。

## 5. 输入屏蔽规则（精确）

| 状态 | 鼠标移动 | 鼠标点击 | 键盘 |
| --- | --- | --- | --- |
| `ai` + 活动调用（蓝） | 吞 | 吞（双击手势被识别并执行） | 吞 |
| `ai` + 租约空闲（绿） | 放行（只黄闪） | 放行（双击仍可暂停） | 放行 |
| `paused`（红） | 放行 | 放行，仅恢复手势被吞 | 放行 |
| `ready` / `unavailable` | 放行 | 放行 | 放行 |

- 热键 `Ctrl+Alt+Shift+Backspace`：**任何状态**可用，语义 = 双击（蓝/绿时暂停，红时恢复）。作为键盘逃生口保留。
- AI 注入事件（`LLMHF_INJECTED` / `LLKHF_INJECTED`）永远不被吞、也不触发手势。

## 6. 工具门禁（`ControlToolGate`）

- `ControlStatus` **永远放行**。
- `paused`：其余工具一律 `ToolError {"code": "USER_PAUSED", ...}`。
- `_resume_observation_required`：只放行 `Snapshot`；其余返回 `{"code": "RESUME_REQUIRES_OBSERVATION", ...}`，并提示模型先重新观察、重新规划。一次成功的 `Snapshot` 后清除该标志。
- `ControlStatus` 返回值扩展：`state`、`generation`、`wait_seconds`、`active_calls`、`lease_seconds_remaining`、`resume_observation_required`、`last_user_move`。

## 7. 提示面板文案（中文）

| 状态 | 面板 |
| --- | --- |
| 蓝 | `AI 正在控制这台电脑` / 双击左键或 Ctrl+Alt+Shift+Backspace 暂停 |
| 绿 | `本轮已完成（15 秒内可能继续）` / 同上 |
| 黄闪 | 只闪边框，不弹面板 |
| 红 | `已暂停 · 双击左键恢复` |

## 8. 实现映射

| 文件 | 改动 |
| --- | --- |
| `src/windows_mcp/desktop/control.py` | 新增 `pause_by_user` / `resume_by_user` / `_paused`；`begin_call` / `checkpoint` 增加 `USER_PAUSED`；删除移动让位路径；看门狗续期条件改为活动调用；`status()` 扩展字段 |
| `src/windows_mcp/desktop/control_hooks.py` | 移动 → 只发黄闪信号；新增双击手势检测与吞掉；热键语义改为暂停/恢复开关 |
| `src/windows_mcp/desktop/control_win32.py` | `GetDoubleClickTime` 绑定、物理左键时间/坐标跟踪所需结构 |
| `src/windows_mcp/desktop/control_overlay.py` | 增加 `mode`（active/lease/paused）与 `flash()`；绿框不跟随光标 |
| `src/windows_mcp/desktop/control_overlay_art.py` | 绿 / 红 / 黄配色、黄闪脉冲、中文化面板文案 |
| `src/windows_mcp/tools/control_notifications.py` | 门禁：`USER_PAUSED` / `RESUME_REQUIRES_OBSERVATION`；状态事件扩展 |
| `src/windows_mcp/tools/control_status.py` | 描述更新，字段扩展 |
| `src/windows_mcp/__main__.py` | 状态 → overlay mode 映射；面板/闪烁接线 |

## 9. 测试计划

- 扩展现有 `tests/test_control*.py`：
  - 屏蔽只发生在 `ai` + 活动调用；租约空闲不屏蔽
  - 移动不改变状态、不打断调用、只触发黄闪
  - 双击暂停 → `paused`、release_all、工具返回 `USER_PAUSED`
  - 恢复 → 强制先 `Snapshot`，其他工具返回 `RESUME_REQUIRES_OBSERVATION`
  - 热键等价开关
  - AI 注入事件不受影响
- 视觉层单测沿用 `test_control_overlay_visuals.py` 的模式（颜色表断言）。

## 10. 与上游的关系

- MIT 许可，保留原 LICENSE 与作者署名。
- `main` 持续跟踪 `upstream/main`；本分支只做行为策略与视觉层改动，冲突面集中在 `desktop/control*.py` 与 `tools/control_notifications.py`。
- 上游升级：`git fetch upstream && git rebase upstream/main`。

## 11. 实现现状（真机验证后）

| 状态 | 边框 | 含义 |
| --- | --- | --- |
| `ready` | **白框（暗，45% 透明）** + 面板「AI 已连接 · 空闲中」 | 服务在线但没有活动调用 |
| `ai`（有活动调用） | 蓝框 + 跟随光标光晕 | AI 正在执行 |
| `ai`（无活动调用，15 秒租约） | 绿框 | 刚做完，租约期内 |
| 蓝/绿期间鼠标移动 | 整框黄闪 | 你的输入正被暂停 |
| `paused` | 红框 + 「已暂停」 | 显式暂停 |
| `disabled` | 无边框 | 软退出，需 ControlResume |

按键（已在真机验证）：

| 操作 | 行为 |
| --- | --- |
| `Ctrl+Backspace` | 暂停 / 恢复（0.8 秒防抖；仅在 AI 持权或暂停期间注册，空闲时归还系统） |
| `Ctrl+Alt+Shift+F12` | 软退出；**只要服务在线就常驻注册**（空闲时也能退出），disabled 时注销 |
| `Ctrl+Alt+Shift+F12` | 软退出（原 Ctrl+Alt+Shift+Backspace 会与输入法的 Alt+Shift 冲突，故换 F12） |
| 摇一摇鼠标 | 与 Ctrl+Backspace 等价（三次方向反转 + 240px 位移） |
| 恢复后第一次调用 | 必须是 `Snapshot`，否则 `RESUME_REQUIRES_OBSERVATION` |

工具门禁错误码：`USER_PAUSED`、`USER_ACTIVE`、`RESUME_REQUIRES_OBSERVATION`、`CONTROL_DISABLED`。