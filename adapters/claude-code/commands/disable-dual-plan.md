---
description: 在当前会话内临时禁用、恢复或查看 Claude 主导的 AGY 双方案规划开关
---

用户输入：$ARGUMENTS

本命令为**纯会话级临时控制**，**绝不修改** `~/.config/ai-anti/config.json`，也**不写入**任何跨会话持久化文件（避免下次新会话在无人察觉的情况下静默停用双方案）。语义以 `CLAUDE.global.md` 接力第 1 步的双方案规则为准。

生效优先级：用户当次消息的明确指令 > 本会话 `/disable-dual-plan` 状态 > `~/.config/ai-anti/config.json` 的 `claude_relay.dual_plan.enabled` > 默认关闭。

将 `$ARGUMENTS` 去除首尾空白后按以下精确取值处理：

- **无参数 / `session` / `off`**：在**当前会话内**临时禁用双方案规划（本会话后续进入接力第 1 步及小改就地升级补写新方案时，均改走 Claude 独立单方案，不调用 AGY）。确认已生效，并提醒该设置仅限本会话、不会改动全局配置。
- **`once`**：仅对**下一次**接力出方案（含首次第 1 步或小改就地升级补写新方案）临时禁用双方案规划。**消费时点**：该次出方案一旦完成（方案落盘）或中止（错误/用户取消），`once` 状态即刻消费并自动恢复遵循全局配置 `claude_relay.dual_plan.enabled`。
- **`on` / `reset`**：清除当前会话内的临时禁用状态，恢复遵循全局配置 `~/.config/ai-anti/config.json` 的 `claude_relay.dual_plan.enabled`（不会替全局开启未启用的配置）。
- **`status`**：用已安装命令链接定位机制根，从 `adapters/codex/skills/claude-review/scripts` 的 `ai_anti_config.py` 导入 `load` 与 `claude_dual_plan` 两个函数读取 `~/.config/ai-anti/config.json`（配置 JSON 损坏或字段无效时立即报错停止，不静默回落默认）；汇报全局配置（`enabled`、`partner`、`model`、`effort`、`synthesizer`、`on_failure`）、当前会话覆写状态与实际生效结果。
- **其他任何参数**：**拒绝执行，不改变当前会话状态，不写任何文件**；报错并列出全部合法值：`无参数 / session / off / once / on / reset / status`。

### 长会话上下文压缩边界

由于本命令刻意不落盘持久状态，超长会话发生上下文压缩后，会话内的临时禁用状态可能丢失。压缩后如仍需确认或保持禁用，请运行 `/disable-dual-plan status` 复核，或直接在当次需求消息中显式说明（当次消息指令始终最高优先）。

本命令只更新或汇报会话状态，不做其他任何改动。
