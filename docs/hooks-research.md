# Claude Code Hooks 调研报告 — AgentHive 状态检测方案

> 调研日期: 2026-03-11
> 数据来源: 官方文档 (code.claude.com/docs/en/hooks), GitHub issues (anthropics/claude-code), 社区实现

---

## 目录

1. [Hook 事件详细分析](#1-hook-事件详细分析)
2. [关键问题解答](#2-关键问题解答)
3. [状态检测映射表](#3-状态检测映射表)
4. [迁移优先级建议](#4-迁移优先级建议)
5. [已知限制和 Workaround](#5-已知限制和-workaround)
6. [推荐 Event File 格式](#6-推荐-event-file-格式)
7. [现有 AgentHive 实现参考](#7-现有-agenthive-实现参考)

---

## 1. Hook 事件详细分析

### 总览：17 个 Hook Events

| Event | 触发条件 | 能否阻塞 | Matcher | Hook 类型 |
|-------|---------|---------|---------|----------|
| SessionStart | session 开始/恢复 | No | startup/resume/clear/compact | command only |
| SessionEnd | session 终止 | No | clear/logout/prompt_input_exit/bypass_permissions_disabled/other | command only |
| UserPromptSubmit | 用户提交 prompt | Yes | 无 | all 4 types |
| PreToolUse | 工具调用前 | Yes | 工具名 (regex) | all 4 types |
| PostToolUse | 工具调用成功后 | No (feedback only) | 工具名 (regex) | all 4 types |
| PostToolUseFailure | 工具调用失败后 | No (feedback only) | 工具名 (regex) | all 4 types |
| PermissionRequest | 权限弹窗出现时 | Yes | 工具名 (regex) | all 4 types |
| Notification | 发送通知时 | No | permission_prompt/idle_prompt/auth_success/elicitation_dialog | command only |
| SubagentStart | 子 agent 创建时 | No | agent 类型 (Bash/Explore/Plan/custom) | command only |
| SubagentStop | 子 agent 完成时 | Yes | agent 类型 | all 4 types |
| Stop | Claude 完成回复时 | Yes | 无 | all 4 types |
| TeammateIdle | 队友 agent 空闲 | Yes | 无 | command only |
| TaskCompleted | task 标记完成 | Yes | 无 | all 4 types |
| PreCompact | context compaction 前 | No | manual/auto | command only |
| ConfigChange | 配置变更 | Yes (except policy) | 配置源 | command only |
| WorktreeCreate | worktree 创建 | Yes | 无 | command only |
| WorktreeRemove | worktree 移除 | No | 无 | command only |
| InstructionsLoaded | CLAUDE.md 加载 | No | 无 | command only |

### 通用 stdin JSON 字段 (所有 events)

```json
{
  "session_id": "string",
  "transcript_path": "string",
  "cwd": "string",
  "permission_mode": "default|plan|acceptEdits|dontAsk|bypassPermissions",
  "hook_event_name": "string",
  "agent_id": "string (subagent only)",
  "agent_type": "string (subagent or --agent mode)"
}
```

### 通用 JSON 输出字段 (exit 0 时解析)

```json
{
  "continue": true,           // false → Claude 立即停止
  "stopReason": "string",     // continue=false 时显示给用户
  "suppressOutput": false,    // true → 隐藏 verbose 输出
  "systemMessage": "string",  // 警告信息显示给用户
  "additionalContext": "string" // 注入到 Claude context
}
```

---

### 1.1 SessionStart

**触发条件**: session 开始（新建、恢复、/clear 后、/compact 后）

**Matcher 值**: `startup` | `resume` | `clear` | `compact`

**stdin JSON 额外字段**:
```json
{
  "source": "startup|resume|clear|compact",
  "model": "string"
}
```

**Exit Code 行为**:
- `0`: 成功。stdout 文本或 JSON 的 `additionalContext` 注入到 Claude context
- `2`: 非阻塞错误。stderr 显示给用户（不能阻塞 session 启动）
- 其他: 非阻塞错误

**stdout 影响 Claude**: **是**。stdout 文本直接作为 context 注入 Claude 可见

**特殊环境变量**: `CLAUDE_ENV_FILE` — 写入 export 语句可持久化环境变量到后续 Bash 命令

**限制**:
- 仅支持 `type: "command"`
- `CLAUDE_ENV_FILE` 可能为空 (issue #15840)
- Plugin 安装时会误触发 (issue #32702)

---

### 1.2 SessionEnd

**触发条件**: session 终止

**Matcher 值**: `clear` | `logout` | `prompt_input_exit` | `bypass_permissions_disabled` | `other`

**stdin JSON 额外字段**:
```json
{
  "reason": "clear|logout|prompt_input_exit|bypass_permissions_disabled|other"
}
```

**Exit Code 行为**:
- 所有 exit code: 不能阻塞 session 终止。stderr 仅在 debug 模式可见
- 纯粹用于 cleanup/side-effect

**stdout 影响 Claude**: **否**

**限制**: 仅支持 `type: "command"`

---

### 1.3 Stop

**触发条件**: Claude 完成一轮回复（注意：用户中断 Ctrl+C 时**不触发**）

**Matcher**: 无（每次都触发）

**stdin JSON 额外字段**:
```json
{
  "stop_hook_active": false,  // true = Claude 已经在响应 stop hook（防无限循环）
  "last_assistant_message": "string"
}
```

**Exit Code 行为**:
- `0`: 成功。可返回 JSON `decision: "block"` + `reason` 阻止 Claude 停止（Claude 继续对话）
- `2`: 阻塞错误。stderr 作为 reason 反馈给 Claude，Claude 继续

**stdout 影响 Claude**: 间接 — 通过 `decision: "block"` 和 `reason` 影响 Claude 是否继续

**⚠️ 关键**: 必须检查 `stop_hook_active` 防止无限循环！

**已知问题**:
- Background agents bypass Stop hooks (issue #25147)
- settings.json 中的 Stop hook 可能不触发 (issue #26770)
- Transcript race condition — hook 可能读到 stale transcript (issue #15813)

---

### 1.4 SubagentStop

**触发条件**: 子 agent（Explore, Plan, 自定义等）完成任务

**Matcher**: agent 类型名 (regex)，如 `Bash`, `Explore`, `Plan`, 自定义 agent 名

**stdin JSON 额外字段**:
```json
{
  "stop_hook_active": true,
  "agent_id": "string",
  "agent_type": "string",
  "agent_transcript_path": "string",  // 子 agent 自己的 transcript
  "last_assistant_message": "string"
}
```

注意: `transcript_path` 是主 session 的 transcript，`agent_transcript_path` 是子 agent 的

**Exit Code 行为**: 同 Stop（可以 block 阻止子 agent 结束）

**已知问题**: Agent frontmatter 中定义 Stop hook 时，`hook_event_name` 会是 `"SubagentStop"` 而非 `"Stop"` (issue #19220)

---

### 1.5 Notification

**触发条件**: Claude Code 发送通知时

**Matcher 值**: `permission_prompt` | `idle_prompt` | `auth_success` | `elicitation_dialog`

**stdin JSON 额外字段**:
```json
{
  "message": "string",
  "title": "string (optional)",
  "notification_type": "permission_prompt|idle_prompt|auth_success|elicitation_dialog"
}
```

**Exit Code 行为**: 不能阻塞。所有 exit code 仅影响 verbose 输出

**stdout 影响 Claude**: 可返回 `additionalContext`

**已知问题**:
- ~8-10 秒延迟 (issue #5186)
- Phantom permission_prompt events (issue #16102)
- 在 auto-accept 模式下仍然触发 (issue #30233)
- permission_prompt 通知缺少 tool 详情 (issue #32952)

---

### 1.6 PreToolUse

**触发条件**: 工具调用执行前

**Matcher**: 工具名 regex，如 `Bash`, `Edit|Write`, `mcp__.*`, `Agent`

**stdin JSON 额外字段**:
```json
{
  "tool_name": "string",
  "tool_input": {/* 工具特定参数 */},
  "tool_use_id": "string"
}
```

**tool_input 按工具类型**:
- `Bash`: `{command, description, timeout, run_in_background}`
- `Write`: `{file_path, content}`
- `Edit`: `{file_path, old_string, new_string, replace_all}`
- `Read`: `{file_path, offset, limit}`
- `Agent`: `{prompt, description, subagent_type, model}`
- `WebFetch`: `{url, prompt}`
- `WebSearch`: `{query, allowed_domains, blocked_domains}`

**Exit Code 行为**:
- `0`: 可返回 `hookSpecificOutput.permissionDecision`:
  - `"allow"` — 绕过权限系统直接执行
  - `"deny"` — 阻止工具调用，`permissionDecisionReason` 反馈给 Claude
  - `"ask"` — 弹出权限对话框（附 reason）
  - `updatedInput` — 修改工具输入参数
  - `additionalContext` — 注入额外 context
- `2`: 阻止工具调用，stderr 反馈给 Claude

**stdout 影响 Claude**: **是** — 通过 permissionDecision 和 additionalContext

**已知问题**:
- Hook 脚本被删除后静默 fail-open (issue #32990 — 安全隐患!)
- Agent/Prompt 类型 hook 不能真正阻塞 (issue #33125)
- 多个 hook 并发时 `updatedInput` 被忽略 (issue #15897)
- 与 ToolSearch 配合时可能导致 hang (issue #33073)
- Exit code 2 单独使用时阻塞行为不可靠 (issue #31250)

---

### 1.7 PostToolUse

**触发条件**: 工具调用**成功执行后**

**Matcher**: 工具名 regex

**stdin JSON 额外字段**:
```json
{
  "tool_name": "string",
  "tool_input": {/* 工具输入 */},
  "tool_response": "string|object",
  "tool_use_id": "string"
}
```

**Exit Code 行为**:
- `0`: 可返回 `decision: "block"` + `reason`（但工具已经执行了，只能反馈给 Claude）
- `2`: stderr 显示给 Claude（不能撤销）

**stdout 影响 Claude**: 是 — `additionalContext` 和 `decision` 反馈。MCP 工具可用 `updatedMCPToolOutput` 替换输出

---

### 1.8 PostToolUseFailure

**触发条件**: 工具调用**失败后**

**Matcher**: 工具名 regex

**stdin JSON 额外字段**:
```json
{
  "tool_name": "string",
  "tool_input": {/* 工具输入 */},
  "tool_use_id": "string",
  "error": "string",
  "is_interrupt": "boolean (optional)"
}
```

**Exit Code 行为**: 不能阻塞。可返回 `additionalContext`

---

### 1.9 PermissionRequest

**触发条件**: 权限弹窗**即将显示**时（vs PreToolUse 是在工具执行前不论权限状态）

**Matcher**: 工具名 regex

**stdin JSON 额外字段**:
```json
{
  "tool_name": "string",
  "tool_input": {/* 工具输入，无 tool_use_id */},
  "permission_suggestions": [/* array */]
}
```

**Exit Code 行为**:
- `0`: 可返回 `hookSpecificOutput.decision`:
  - `behavior: "allow"` — 自动批准权限
  - `behavior: "deny"` + `message` — 拒绝，message 反馈给 Claude
  - `updatedInput` — allow 时修改工具输入
  - `updatedPermissions` — 应用权限规则更新（如"always allow"）
  - `interrupt: true` — deny 时完全停止 Claude
- `2`: 拒绝权限，stderr 反馈给 Claude

**stdout 影响 Claude**: 是 — 通过 decision 控制

**⚠️ 关键限制**: 非交互模式 (`-p`) 下**不触发**！应使用 PreToolUse 替代

**已知问题**:
- `additionalContext` 被静默丢弃 (issue #28035)
- Race condition — 用户可能先看到弹窗再被 hook 处理 (issue #12176)
- Remote control 模式下 hook-approved 的命令仍显示弹窗 (issue #32493)

---

### 1.10 UserPromptSubmit

**触发条件**: 用户提交消息后、Claude 处理前

**Matcher**: 无（每次都触发）

**stdin JSON 额外字段**:
```json
{
  "prompt": "string"  // 用户提交的文本
}
```

**Exit Code 行为**:
- `0`: 可返回 `decision: "block"` + `reason`（阻止处理并擦除 prompt）；`additionalContext` 注入 context
- `2`: 阻止处理，stderr 显示给用户

**stdout 影响 Claude**: **是** — stdout 文本和 `additionalContext` 都注入 Claude context

**已知问题**:
- Agent 类型 hook 报错 "Messages are required" (issue #26474)
- 用户在 Claude 处理中发消息时不触发 (issue #31114, regression)

---

### 1.11 SubagentStart

**触发条件**: 子 agent 被创建时

**Matcher**: agent 类型名

**stdin JSON 额外字段**:
```json
{
  "agent_id": "string",
  "agent_type": "string"
}
```

**Exit Code 行为**: 不能阻塞。可返回 `additionalContext` 注入子 agent context

**限制**: 不包含启动子 agent 的 prompt (issue #32016)

---

### 1.12 PreCompact

**触发条件**: context compaction 前

**Matcher 值**: `manual` | `auto`

**stdin JSON 额外字段**:
```json
{
  "trigger": "manual|auto",
  "custom_instructions": "string (user's /compact instructions; empty for auto)"
}
```

**Exit Code 行为**: 不能阻塞。stderr 显示给用户

**已知问题**: Compaction 后所有 plugin hooks 停止触发 (issue #25655)

---

### 1.13 InstructionsLoaded

**触发条件**: CLAUDE.md 或 `.claude/rules/*.md` 加载时

**Matcher**: 无

**stdin JSON 额外字段**:
```json
{
  "file_path": "string",
  "memory_type": "User|Project|Local|Managed",
  "load_reason": "session_start|nested_traversal|path_glob_match|include",
  "globs": "string (optional)",
  "trigger_file_path": "string (optional)",
  "parent_file_path": "string (optional, for include loads)"
}
```

**Exit Code 行为**: 完全忽略。纯观测用途

**已知问题**: Compaction 后不触发 (issue #30973)

---

## 2. 关键问题解答

### Q1: Permission 等待检测

**可用 Hook**: `PermissionRequest` 和 `Notification` (matcher: `permission_prompt`)

**区分"等权限" vs "等用户输入"**:
- `PermissionRequest` hook 在权限弹窗出现时触发，payload 包含 `tool_name` 和 `tool_input`，**明确标识是权限等待**
- `Notification` matcher `permission_prompt` 也触发，但缺少 tool 详情 (issue #32952)
- `Notification` matcher `idle_prompt` 在等用户输入时触发
- 两者组合可以精确区分

**推荐方案**:
```
PermissionRequest hook → 写入 event: {type: "waiting_permission", tool_name, tool_input}
Notification[idle_prompt] → 写入 event: {type: "waiting_user_input"}
```

**⚠️ 注意**: PermissionRequest 在 `-p` 非交互模式下不触发。AgentHive 通过 tmux 运行的 Claude 是交互模式，所以 OK。

### Q2: Streaming 状态

**没有** "开始生成" hook。只有 `Stop` hook 标记 "结束生成"。

**检测方案**:
- **开始**: `UserPromptSubmit` → Claude 开始处理（可推断为"开始生成"）
- **结束**: `Stop` → Claude 完成回复
- **中间**: 没有 hook。可以通过 JSONL mtime 变化推断 streaming 中
- **工具执行中**: `PreToolUse` → 开始执行工具，`PostToolUse` → 工具执行完

**状态推断链**:
```
UserPromptSubmit → state: "thinking/streaming"
PreToolUse → state: "tool_executing"
PostToolUse → state: "thinking/streaming" (继续生成)
Stop → state: "idle"
PermissionRequest → state: "waiting_permission"
Notification[idle_prompt] → state: "waiting_user_input"
```

### Q3: Plan Mode 交互

**没有** Plan Mode 专用 hook (feature request: issue #20526, #31459)。

**可用检测**:
- `permission_mode` 字段在所有 hook 的 stdin 中都有，值为 `"plan"` 表示在 plan mode
- Plan mode 下 Claude 需要用户反馈时，触发 `Notification[idle_prompt]`
- 可通过 `Stop` hook 的 `permission_mode == "plan"` 检测 Claude 在 plan mode 下完成回复

### Q4: 工具执行状态

**PostToolUse 在命令结束后才触发**。没有办法通过 hook 知道"正在执行中"。

**精确检测方案**:
```
PreToolUse[Bash] → state: "executing_bash" (command 在 tool_input 中)
PostToolUse[Bash] → state: "bash_complete"
PreToolUse[Agent] → state: "spawning_subagent"
SubagentStop → state: "subagent_complete"
```

**长时间 Bash**: PreToolUse 触发后到 PostToolUse 触发前，agent 状态就是"正在执行"。timeout 默认 600s。

### Q5: Hook 并发安全

**所有匹配的 hooks 并行执行**。

- 同一 event 多个 handler: **并行**
- 相同 command 或 URL 的 handler: **自动去重**
- 如果 hook 执行慢: 会**阻塞 Claude**（sync hook 阻塞到 timeout，默认 600s）
- `async: true` 的 hook 不阻塞，结果在下一个 conversation turn 交付

**已知问题**:
- 6+ 并行 hooks 时 session 可能崩溃 (issue #28372)
- 多个 PreToolUse hooks 的 `updatedInput` 互相覆盖 (issue #15897)

**AgentHive 建议**: 每个 event 只用 1 个 hook handler，在 hook 脚本内部 dispatch 到不同逻辑。使用 `async: true` 避免阻塞。

### Q6: 环境变量传递

**所有 hook 都继承 Claude Code 的环境变量**。

- `AHIVE_AGENT_ID`: 在 `_create_tmux_claude_session()` 中 export，所有 hook 都能读到 ✓
- `CLAUDE_PROJECT_DIR`: 所有 hook 可用
- `CLAUDE_ENV_FILE`: **仅 SessionStart** 可用（且有 bug，可能为空 #15840）
- `CLAUDE_PLUGIN_ROOT`: SessionStart 可能读不到 (#27145)，其他 hook OK

**验证**: AgentHive 已验证 SessionStart 读到 AHIVE_AGENT_ID。由于环境变量继承是 Unix 进程模型的基本特性，所有 hook 子进程都会继承。

### Q7: stream-json 交互

**Hook 的 stdout 不会直接出现在 stream-json 输出里**。

- Hook 通信严格通过 stdin/stdout/stderr/exit code
- stream-json 输出中会有 `hook_started` 和 `hook_response` system messages (针对 SessionStart 和 Stop)
- PreToolUse/PostToolUse 的 hook events 也会出现在 stream-json 中（之前是 bug #27200，已修复）
- 格式: system message type，不是 raw stdout

---

## 3. 状态检测映射表

| AgentHive 状态 | 推荐 Hook | stdin 关键字段 | 替代方案 |
|---------------|----------|---------------|---------|
| **Session 开始** | `SessionStart` ✅ (已实现) | `source`, `session_id` | JSONL 文件出现 |
| **Session 终止** | `SessionEnd` | `reason` | tmux pane 消失 / PID 死亡 |
| **/clear 后 session 切换** | `SessionStart[clear]` ✅ (已实现) | `source="clear"`, `session_id` | FD scan + unowned JSONL |
| **/compact 后** | `SessionStart[compact]` | `source="compact"` | PreCompact event |
| **Claude 回复完毕 (idle)** | `Stop` | `last_assistant_message` | JSONL mtime 停止变化 |
| **等待权限审批** | `PermissionRequest` | `tool_name`, `tool_input` | `Notification[permission_prompt]` |
| **等待用户输入** | `Notification[idle_prompt]` | `message` | Stop + 无后续活动 |
| **正在执行工具** | `PreToolUse` → `PostToolUse` | `tool_name`, `tool_input` | JSONL streaming 检测 |
| **正在执行 Bash** | `PreToolUse[Bash]` | `tool_input.command` | tmux pane 子进程检测 |
| **子 agent 创建** | `SubagentStart` | `agent_id`, `agent_type` | JSONL 中 Agent tool call |
| **子 agent 完成** | `SubagentStop` | `agent_id`, `agent_type`, `agent_transcript_path` | JSONL 中 Agent result |
| **正在生成回复** | UserPromptSubmit(开始) → Stop(结束) | `prompt` / `last_assistant_message` | JSONL mtime 持续变化 |
| **Plan mode** | 任何 hook 的 `permission_mode=="plan"` | `permission_mode` | API/transcript 检测 |
| **Context compaction** | `PreCompact` | `trigger` (manual/auto) | SessionStart[compact] |
| **Agent 死亡** | `SessionEnd` + 无后续 SessionStart | `reason` | PID 检测 + stale threshold |
| **工具被 hook 阻止** | `PreToolUse` (exit 2) | - | PostToolUseFailure |

---

## 4. 迁移优先级建议

### P0 — 立即实现（高可靠性，高收益）

#### 4.1 Stop hook → "idle" 状态检测
- **收益**: 消除 JSONL mtime 轮询，精确知道 Claude 何时完成回复
- **可靠性**: 高（单一事件，无 matcher 复杂性）
- **注意**: 必须检查 `stop_hook_active` 防无限循环
- **已知风险**: Background agents 可能 bypass (issue #25147)

#### 4.2 SessionEnd hook → agent 退出检测
- **收益**: 消除 PID 死亡检测 + stale threshold heuristic
- **可靠性**: 高（session 终止时必触发）
- **收益叠加**: 与 SessionStart 配合形成完整生命周期

#### 4.3 PermissionRequest hook → 权限等待检测
- **收益**: 精确知道 agent 卡在权限弹窗，不再靠 tmux 内容猜测
- **可靠性**: 中高（交互模式下可靠，有 race condition）
- **Payload**: 包含 `tool_name` 和 `tool_input`，可以显示给用户"正在等批准什么"

### P1 — 近期实现（中等收益）

#### 4.4 PreToolUse / PostToolUse → 工具执行状态
- **收益**: 精确追踪 agent 正在做什么（执行 bash、编辑文件等）
- **复杂性**: 中等（高频事件，需要 async hook 或高效脚本）
- **建议**: 使用 `async: true` 避免阻塞 Claude

#### 4.5 SubagentStart / SubagentStop → 子 agent 追踪
- **收益**: 替代 JSONL 解析来检测子 agent
- **可靠性**: 中等
- **注意**: SubagentStart 不包含 prompt (issue #32016)

#### 4.6 Notification[permission_prompt] → 权限通知补充
- **收益**: 作为 PermissionRequest 的补充信号
- **注意**: 有 8-10 秒延迟 (issue #5186)

### P2 — 远期实现（低优先级/探索性）

#### 4.7 UserPromptSubmit → 用户消息追踪
- **用途**: 追踪用户通过 tmux 手动发送的消息
- **限制**: 用户 mid-turn 消息不触发 (issue #31114)

#### 4.8 PreCompact → compaction 感知
- **用途**: 备份 transcript，但 compaction 后 hooks 可能失效 (issue #25655)

#### 4.9 InstructionsLoaded → 配置追踪
- **用途**: 纯观测，低价值

---

## 5. 已知限制和 Workaround

### 5.1 安全风险

| 问题 | Issue | 严重性 | Workaround |
|------|-------|--------|-----------|
| Claude 可以修改自己的 hook 脚本 | #32376 | 高 | 将 hook 脚本设为只读 (`chmod 444`) |
| Hook 脚本被删除后 fail-open | #32990 | 高 | 定期检查 hook 脚本是否存在 |
| Shell 注入风险 | #27289 | 中 | 避免 .zshrc 中无条件 echo |

### 5.2 可靠性问题

| 问题 | Issue | 影响 | Workaround |
|------|-------|------|-----------|
| Compaction 后所有 plugin hooks 停止触发 | #25655 | 高 | 用 settings.json hooks 而非 plugin hooks |
| Background agents bypass Stop hooks | #25147 | 中 | 同时保留 PID/mtime fallback |
| 6+ 并行 hooks 导致 crash | #28372 | 中 | 每 event 只用 1 个 handler |
| Stop hook 读到 stale transcript | #15813 | 低 | 不依赖 transcript_path，用 last_assistant_message |
| Agent/Prompt hook 不能真正阻塞 PreToolUse | #33125 | 中 | 只用 command 类型 hook |
| settings.json Stop hook 可能不触发 | #26770 | 中 | 验证后使用，保留 fallback |

### 5.3 功能限制

| 限制 | 状态 | Workaround |
|------|------|-----------|
| 没有 "开始生成" hook | 无计划 | UserPromptSubmit → Stop 推断 |
| 没有 Plan mode 专用 hook | Feature request #20526 | 检查 permission_mode 字段 |
| 没有 User Interrupt hook | Feature request #9516 | 无（只能靠 polling 检测） |
| PermissionRequest 不支持 additionalContext | #28035 | 用 PreToolUse 替代 |
| SessionStart CLAUDE_ENV_FILE 可能为空 | #15840 | 不依赖它，用 /tmp 文件 |
| 没有 PostCompact hook | Feature request #29451 | 用 SessionStart[compact] |

### 5.4 平台问题

| 问题 | 平台 | Issue |
|------|------|-------|
| Hooks 创建闪烁窗口 | Windows | #32140 |
| Plugin root var 不展开 | Windows | #32486 |
| WorktreeCreate stdout 处理导致 hang | All | #27467 |

---

## 6. 推荐 Event File 格式

### 目录结构

```
/tmp/ahive/events/{agent_id}/
├── current          # 最新状态 (atomic write)
├── events.jsonl     # 事件流 (append-only)
└── lock             # flock 用
```

### current 文件格式 (JSON, atomic overwrite)

```json
{
  "agent_id": "abc123",
  "state": "idle|streaming|tool_executing|waiting_permission|waiting_input|dead",
  "ts": 1710100000.123,
  "session_id": "uuid-string",
  "detail": {
    "tool_name": "Bash",
    "tool_input": {"command": "npm test"},
    "permission_mode": "default",
    "last_message_preview": "I'll run the tests now..."
  }
}
```

### events.jsonl 格式 (每行一个 JSON)

```json
{"ts":1710100000.0,"event":"session_start","agent_id":"abc123","session_id":"sid","source":"startup"}
{"ts":1710100001.0,"event":"user_prompt","agent_id":"abc123","session_id":"sid","prompt":"fix the bug"}
{"ts":1710100002.0,"event":"tool_start","agent_id":"abc123","session_id":"sid","tool":"Bash","input":{"command":"npm test"}}
{"ts":1710100005.0,"event":"tool_done","agent_id":"abc123","session_id":"sid","tool":"Bash"}
{"ts":1710100006.0,"event":"permission_wait","agent_id":"abc123","session_id":"sid","tool":"Write","input":{"file_path":"/etc/hosts"}}
{"ts":1710100007.0,"event":"stop","agent_id":"abc123","session_id":"sid","last_message":"Tests passed!"}
{"ts":1710100100.0,"event":"session_end","agent_id":"abc123","session_id":"sid","reason":"prompt_input_exit"}
```

### 统一 Hook 脚本设计

单一入口脚本 `ahive-hook.sh`，通过 `hook_event_name` 分发：

```bash
#!/usr/bin/env bash
set -euo pipefail

# 读取 stdin JSON
INPUT=$(cat)

AGENT_ID="${AHIVE_AGENT_ID:-unknown}"
EVENT=$(echo "$INPUT" | jq -r '.hook_event_name')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id')
TS=$(date +%s.%N)

EVENT_DIR="/tmp/ahive/events/${AGENT_ID}"
mkdir -p "$EVENT_DIR"

case "$EVENT" in
  SessionStart)
    SOURCE=$(echo "$INPUT" | jq -r '.source')
    # 兼容现有 newsession 机制
    echo "$SESSION_ID" > "/tmp/ahive-${AGENT_ID}.newsession"
    # 写入 event
    echo "{\"ts\":$TS,\"event\":\"session_start\",\"agent_id\":\"$AGENT_ID\",\"session_id\":\"$SESSION_ID\",\"source\":\"$SOURCE\"}" >> "$EVENT_DIR/events.jsonl"
    # 更新 current state
    echo "{\"agent_id\":\"$AGENT_ID\",\"state\":\"idle\",\"ts\":$TS,\"session_id\":\"$SESSION_ID\"}" > "$EVENT_DIR/current.tmp" && mv "$EVENT_DIR/current.tmp" "$EVENT_DIR/current"
    ;;
  SessionEnd)
    REASON=$(echo "$INPUT" | jq -r '.reason')
    echo "{\"ts\":$TS,\"event\":\"session_end\",\"agent_id\":\"$AGENT_ID\",\"session_id\":\"$SESSION_ID\",\"reason\":\"$REASON\"}" >> "$EVENT_DIR/events.jsonl"
    echo "{\"agent_id\":\"$AGENT_ID\",\"state\":\"dead\",\"ts\":$TS,\"session_id\":\"$SESSION_ID\"}" > "$EVENT_DIR/current.tmp" && mv "$EVENT_DIR/current.tmp" "$EVENT_DIR/current"
    ;;
  Stop)
    # 防无限循环
    STOP_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')
    if [ "$STOP_ACTIVE" = "true" ]; then exit 0; fi
    LAST_MSG=$(echo "$INPUT" | jq -r '.last_assistant_message // "" | .[0:200]')
    echo "{\"ts\":$TS,\"event\":\"stop\",\"agent_id\":\"$AGENT_ID\",\"session_id\":\"$SESSION_ID\",\"last_message\":$(echo "$LAST_MSG" | jq -Rs .)}" >> "$EVENT_DIR/events.jsonl"
    echo "{\"agent_id\":\"$AGENT_ID\",\"state\":\"idle\",\"ts\":$TS,\"session_id\":\"$SESSION_ID\"}" > "$EVENT_DIR/current.tmp" && mv "$EVENT_DIR/current.tmp" "$EVENT_DIR/current"
    ;;
  PreToolUse)
    TOOL=$(echo "$INPUT" | jq -r '.tool_name')
    echo "{\"ts\":$TS,\"event\":\"tool_start\",\"agent_id\":\"$AGENT_ID\",\"session_id\":\"$SESSION_ID\",\"tool\":\"$TOOL\"}" >> "$EVENT_DIR/events.jsonl"
    echo "{\"agent_id\":\"$AGENT_ID\",\"state\":\"tool_executing\",\"ts\":$TS,\"session_id\":\"$SESSION_ID\",\"detail\":{\"tool_name\":\"$TOOL\"}}" > "$EVENT_DIR/current.tmp" && mv "$EVENT_DIR/current.tmp" "$EVENT_DIR/current"
    ;;
  PostToolUse)
    TOOL=$(echo "$INPUT" | jq -r '.tool_name')
    echo "{\"ts\":$TS,\"event\":\"tool_done\",\"agent_id\":\"$AGENT_ID\",\"session_id\":\"$SESSION_ID\",\"tool\":\"$TOOL\"}" >> "$EVENT_DIR/events.jsonl"
    echo "{\"agent_id\":\"$AGENT_ID\",\"state\":\"streaming\",\"ts\":$TS,\"session_id\":\"$SESSION_ID\"}" > "$EVENT_DIR/current.tmp" && mv "$EVENT_DIR/current.tmp" "$EVENT_DIR/current"
    ;;
  PermissionRequest)
    TOOL=$(echo "$INPUT" | jq -r '.tool_name')
    TINPUT=$(echo "$INPUT" | jq -c '.tool_input // {}')
    echo "{\"ts\":$TS,\"event\":\"permission_wait\",\"agent_id\":\"$AGENT_ID\",\"session_id\":\"$SESSION_ID\",\"tool\":\"$TOOL\",\"input\":$TINPUT}" >> "$EVENT_DIR/events.jsonl"
    echo "{\"agent_id\":\"$AGENT_ID\",\"state\":\"waiting_permission\",\"ts\":$TS,\"session_id\":\"$SESSION_ID\",\"detail\":{\"tool_name\":\"$TOOL\",\"tool_input\":$TINPUT}}" > "$EVENT_DIR/current.tmp" && mv "$EVENT_DIR/current.tmp" "$EVENT_DIR/current"
    ;;
  Notification)
    NTYPE=$(echo "$INPUT" | jq -r '.notification_type')
    if [ "$NTYPE" = "idle_prompt" ]; then
      echo "{\"ts\":$TS,\"event\":\"waiting_input\",\"agent_id\":\"$AGENT_ID\",\"session_id\":\"$SESSION_ID\"}" >> "$EVENT_DIR/events.jsonl"
      echo "{\"agent_id\":\"$AGENT_ID\",\"state\":\"waiting_input\",\"ts\":$TS,\"session_id\":\"$SESSION_ID\"}" > "$EVENT_DIR/current.tmp" && mv "$EVENT_DIR/current.tmp" "$EVENT_DIR/current"
    fi
    ;;
  SubagentStart)
    ATYPE=$(echo "$INPUT" | jq -r '.agent_type')
    AID=$(echo "$INPUT" | jq -r '.agent_id')
    echo "{\"ts\":$TS,\"event\":\"subagent_start\",\"agent_id\":\"$AGENT_ID\",\"session_id\":\"$SESSION_ID\",\"sub_agent_id\":\"$AID\",\"sub_agent_type\":\"$ATYPE\"}" >> "$EVENT_DIR/events.jsonl"
    ;;
  SubagentStop)
    STOP_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')
    if [ "$STOP_ACTIVE" = "true" ]; then exit 0; fi
    ATYPE=$(echo "$INPUT" | jq -r '.agent_type')
    AID=$(echo "$INPUT" | jq -r '.agent_id')
    echo "{\"ts\":$TS,\"event\":\"subagent_stop\",\"agent_id\":\"$AGENT_ID\",\"session_id\":\"$SESSION_ID\",\"sub_agent_id\":\"$AID\",\"sub_agent_type\":\"$ATYPE\"}" >> "$EVENT_DIR/events.jsonl"
    ;;
  UserPromptSubmit)
    PROMPT=$(echo "$INPUT" | jq -r '.prompt // "" | .[0:200]')
    echo "{\"ts\":$TS,\"event\":\"user_prompt\",\"agent_id\":\"$AGENT_ID\",\"session_id\":\"$SESSION_ID\",\"prompt\":$(echo "$PROMPT" | jq -Rs .)}" >> "$EVENT_DIR/events.jsonl"
    echo "{\"agent_id\":\"$AGENT_ID\",\"state\":\"streaming\",\"ts\":$TS,\"session_id\":\"$SESSION_ID\"}" > "$EVENT_DIR/current.tmp" && mv "$EVENT_DIR/current.tmp" "$EVENT_DIR/current"
    ;;
  PreCompact)
    TRIGGER=$(echo "$INPUT" | jq -r '.trigger')
    echo "{\"ts\":$TS,\"event\":\"pre_compact\",\"agent_id\":\"$AGENT_ID\",\"session_id\":\"$SESSION_ID\",\"trigger\":\"$TRIGGER\"}" >> "$EVENT_DIR/events.jsonl"
    ;;
esac

exit 0
```

### settings.json 配置

```json
{
  "hooks": {
    "SessionStart": [
      {"matcher": "", "hooks": [{"type": "command", "command": "/path/to/ahive-hook.sh"}]}
    ],
    "SessionEnd": [
      {"matcher": "", "hooks": [{"type": "command", "command": "/path/to/ahive-hook.sh"}]}
    ],
    "Stop": [
      {"matcher": "", "hooks": [{"type": "command", "command": "/path/to/ahive-hook.sh"}]}
    ],
    "PreToolUse": [
      {"matcher": "", "hooks": [{"type": "command", "command": "/path/to/ahive-hook.sh", "async": true}]}
    ],
    "PostToolUse": [
      {"matcher": "", "hooks": [{"type": "command", "command": "/path/to/ahive-hook.sh", "async": true}]}
    ],
    "PermissionRequest": [
      {"matcher": "", "hooks": [{"type": "command", "command": "/path/to/ahive-hook.sh"}]}
    ],
    "Notification": [
      {"matcher": "", "hooks": [{"type": "command", "command": "/path/to/ahive-hook.sh", "async": true}]}
    ],
    "SubagentStart": [
      {"matcher": "", "hooks": [{"type": "command", "command": "/path/to/ahive-hook.sh", "async": true}]}
    ],
    "SubagentStop": [
      {"matcher": "", "hooks": [{"type": "command", "command": "/path/to/ahive-hook.sh", "async": true}]}
    ],
    "UserPromptSubmit": [
      {"matcher": "", "hooks": [{"type": "command", "command": "/path/to/ahive-hook.sh"}]}
    ],
    "PreCompact": [
      {"matcher": "", "hooks": [{"type": "command", "command": "/path/to/ahive-hook.sh", "async": true}]}
    ]
  }
}
```

**关键设计决策**:
- `Stop`, `SessionEnd`, `PermissionRequest`, `UserPromptSubmit`, `SessionStart` 必须是同步的（需要及时状态更新或可能需要阻塞）
- `PreToolUse`, `PostToolUse`, `Notification`, `SubagentStart`, `SubagentStop`, `PreCompact` 用 `async: true` 避免阻塞 Claude
- 使用 `mv` 原子写入 `current` 文件，避免 Python 读到半写数据
- 保持与现有 `/tmp/ahive-{agent_id}.newsession` 机制的兼容性

---

## 7. 现有 AgentHive 实现参考

### 已实现

| 机制 | 位置 | 状态 |
|------|------|------|
| SessionStart hook 信号 | `/tmp/ahive-{agent_id}.newsession` | ✅ 生产 |
| AHIVE_AGENT_ID 环境变量 | main.py:134 | ✅ 生产 |
| 4-layer fallback 检测 | agent_dispatcher.py:5337-5561 | ✅ 生产 |
| .owner sidecar 文件 | agent_dispatcher.py:288-322 | ✅ 生产 |
| PID/FD scan | agent_dispatcher.py:1613 | ✅ fallback |
| JSONL mtime polling | agent_dispatcher.py:5707+ | ✅ 生产 |
| tmux pane alive check | agent_dispatcher.py:4885+ | ✅ 生产 |

### 待实现（本文档建议）

| 机制 | 优先级 | 替代什么 |
|------|--------|---------|
| Stop hook → idle 检测 | P0 | JSONL mtime polling |
| SessionEnd hook → 退出检测 | P0 | PID 死亡 + stale threshold |
| PermissionRequest hook → 权限等待 | P0 | tmux 内容猜测 |
| PreToolUse/PostToolUse → 工具状态 | P1 | JSONL 内容解析 |
| SubagentStart/Stop → 子 agent | P1 | JSONL 中 Agent tool call 解析 |
| Notification[idle_prompt] → 等输入 | P1 | Stop 后无活动推断 |
| UserPromptSubmit → 用户消息 | P2 | 无现有机制 |
| PreCompact → compaction 感知 | P2 | SessionStart[compact] |
