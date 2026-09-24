---
description: 查看或切换多模型接力工作流的协作模式（solo/required/auto/force）
---

用户输入：$ARGUMENTS

各档语义以 `CLAUDE.global.md`「协作模式开关」节为准。模式文件为仓库根目录下的 `.claude/relay-mode.local`（单行纯文本，应在各仓库 gitignore，仅本机生效）；仓库根按 `git rev-parse --show-toplevel` 判定，非 git 目录才回退当前工作目录（与 `hooks/session-start.sh` 同口径）。

- **无参数**：读模式文件（不存在即为默认 `required`），报告当前模式和该档下的行为，一两句话即可。
- **参数为 `solo` / `required` / `auto` / `force` 之一**：把该值单行写入 `.claude/relay-mode.local`，然后确认切换结果并简述新档位的行为。切到 `solo` 时额外提醒一句：此后大任务与高风险任务都不再经交叉评审。
- **其他参数**：不写文件，报错并列出四个合法值。

切换只影响模式文件，不做其他任何改动。
