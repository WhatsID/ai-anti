---
description: 显式复核已有问题分析；调用通用双阶段问题复核入口
---

先解析本 command 的符号链接取得实际源码 checkout。读取同源 core/PROBLEM_REVIEW.md、
core/CLI_DISCIPLINE.md 和 docs/PROBLEM_REVIEW.md；不从业务 cwd 猜源码路径。
按文档准备含用户原话、独立分析文件、来源与覆盖范围的本地文本 bundle，然后调用：

```bash
python3 /resolved/ai-anti/adapters/codex/skills/claude-review/scripts/problem_review.py \
  --bundle /absolute/bundle --out /absolute/new-output
```

与 Codex skill 使用同一编排器，默认读取 codex_relay.review_channel，**不是 review.channels**。
可显式 --channel claude|qwen|qoder；--config-base 默认 cwd，仅解析相对凭证路径，不授权读取业务目录。
不需要 Git。prepare-only 不读凭证、不启动模型。失败不自动重试或降级。
先读 status.json，再完整读取报告和原始输出。逐条按证据采纳/待验证/驳回；
completed 是协议成功，supported 是可见材料支持，不是根因已证实或已修复。
核对主模型与实际评审模型；同模型、未知或 requested_only 必须披露身份边界。
补查由上层按已有授权完成，不自动重审；重要新事实或根因实质变化才开第二轮，最多两轮，
使用 --round 2 --previous 首轮completed输出 和递增 revision。保留材料、谱系与裁决。
缺证据或分歧不得包装成已证实根因。人工确认修复前停在报告；获授权后交既有方案评审，
携带证据、缺口、原故障验证条件并核对环境/版本。不自动修改业务 skill 或全局路由。
