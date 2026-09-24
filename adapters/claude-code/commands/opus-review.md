---
description: 评审通道的同族评审档(配置中的跨模型通道均不可用时):派同族评审子代理做多视角对抗性只读评审并复核。用法:/opus-review plan <方案文件路径> 或 /opus-review diff
---

用同族评审子代理(`adversarial-reviewer`,model opus + effort max)做同族对抗性评审。参数: $ARGUMENTS

**定位与启用条件**：接力流程评审通道中的同族档（`same-family`，2026-08-07 用户批准）。启用前必须确认
配置中排在前面的所有跨模型通道均不可用（调用失败并已重试、或确定性配额耗尽）；本档若被配置在某个
跨模型通道之前，仍须先确认配置中的全部跨模型通道均不可用。任一跨模型通道恢复后立即回到跨模型评审。

**输入边界(2026-08-12)**：本档**暂不支持需求原文(`.request.md`)输入**，其评审不含独立 inventory
差集；启用时汇报须一并说明这个缺口。它是明确平台能力例外，不可声称已通过 R3 request 检查。

运行时路径解析（送审编排者执行，先于读取 R3）：

```python
from pathlib import Path
import os
config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()
command_source = (config_dir / "commands" / "opus-review.md").resolve()
ai_anti_root = command_source.parents[3]
core = ai_anti_root / "core" / "REVIEW_PRINCIPLES.md"
if not core.is_file() or not os.access(core, os.R_OK):
    raise SystemExit(f"停止送审：无法读取 {core}")
```

路径可含空格，不能由业务 cwd 推导。送审编排者按 R3 做 preflight；公共主模型规则不赋予 reviewer
实施、派发或额外权限。读取 core 后才把每种模式所列章节**原文**运行时放入 prompt，不能只传链接。
**补偿条款**：完整规则、原始 2026-08-07 事故与成本模型见 R6、R9。每个 reviewer prompt 注入
R1、R6、R7、R9、R10 原文，加上 R8 的作答规则、本视角认领的问题原文及项目部署规模口径；它只收到方案/改动清单、仓库、scratchpad、
自身视角和自身认领的四问。blocker/高严重度意见的独立复核员收到 R1、R6、R7、R9、R10 原文与该条
意见、方案、仓库、scratchpad；不要求指定视角或回答四问，缺少必要正文则停止并向委派者报告。

1. 并发派 3–5 个 `adversarial-reviewer`，一个代理一个不同视角。安全与凭证边界视角**必派**并认领
   R8 两条安全问题；两条资源问题归兼容与工程实现视角或另派资源视角。四问必须合计覆盖，无覆盖
   本档不算通过；不让每个 reviewer 重复回答全部四问。
2. 收齐后合并去重：同一问题由不同视角重复命中，保留最高严重度并记录命中次数。
3. 对 blocker/高条目并发派复核员；复核不成立的移出主清单，单列「已复核排除」。逐条按 R6 裁决，
   总轮次上限仍为两轮。

逐条完整呈现并逐条裁决，标注「**同族评审，非跨模型**」、实际视角数和复核排除数。任何
补偿未执行必须点名说明，不得标为通过。3–5 个 opus@max 子代理加复核轮是本仓库最贵的评审档，仅作降级使用，不进入常规流程。
