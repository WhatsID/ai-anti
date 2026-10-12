# ai-anti 项目级约定（自举治理）

共用评审原则见 `core/REVIEW_PRINCIPLES.md`（唯一权威）；Claude Code 适配、模式与编排见
`adapters/claude-code/CLAUDE.global.md`（安装后由用户级 `~/.claude/CLAUDE.md` 的 `@import` 加载）。
本文件只写**本仓库自己的覆写与治理规则**，不复制机制正文。

## 自举：本仓库的变更一律走接力流程

本仓库装的就是守门机制本身，**守门机制不得比它守的东西更好改**。因此：

- **全部机制文件都是高风险触发器**——不论改动几个文件、改动多小，一律走接力流程
  （出方案 → 需求原文 → 跨模型交叉评审 → 分诊裁决 → 需求核对 → implementer 实施 →
  终审 → 汇报）。具体清单：
  - `core/DEBUGGING.md`、`core/TESTING.md`
  - `core/WORKFLOW_ENTRY.md`、`adapters/codex/AGENTS.global.md`
  - `adapters/claude-code/CLAUDE.global.md`
  - `adapters/claude-code/commands/`、`adapters/claude-code/agents/`、
    `adapters/claude-code/hooks/`
  - `adapters/codex/skills/claude-review/`、`adapters/codex/tests/`
  - `adapters/codex/skills/codex-relay/`
  - `core/CLI_DISCIPLINE.md`、`core/REVIEW_PRINCIPLES.md`、`core/DESIGN_RATIONALE.md`
  - `plugins/`、`tests/`、`scripts/`、`.github/`
  - `apps/macos/backend/`、`apps/macos/Sources/AIReviewBar/Bridge.swift`、
    `apps/macos/Sources/AIReviewBar/AppModel.swift`（配置与凭证控制面）
  - `compat.json`、`config.example.json`
  - `install.sh`、`.claude/settings.json`
- 只改 `README.md`、`README.zh-CN.md`、`CHANGELOG.md` 的错别字或链接这类**无行为影响**的编辑
  不在此列。

## 规则由来保真纪律

机制文本里的日期注脚与事故引用是每条规则的成本证据。修改这些文本时：

- 日期注脚与事故引用（2026-08-06 提交总纪律、08-07 Opus 降级档批准、08-11 CLI 调用
  事故、08-12 分诊指令等）**不得删改或概括**——删掉它们，规则就会退化成风格偏好被人
  随手改掉。要改规则，先在 `core/DESIGN_RATIONALE.md` 留下新的事实依据。
- `<scratchpad>` 是占位符，保持原样，不要替换成具体路径。
- 新增规则要同时说明**它防的是哪次实际失败**；说不出来的不加。

## 本仓库的覆写参数

| 参数 | 本仓库取值 |
|------|-----------|
| 业务高风险触发器清单 | 无业务面；见上「自举」节的机制文件清单 |
| 「跨子系统」判定口径 | `adapters/` 与 `core/` 两侧同时改动 |
| 评审意见登记去处 | `docs/FEATURE_TODOS.md` |
| 部署规模口径 | 开源命令行工具，由用户在自己机器上安装运行；无服务端面 |

## 硬边界

- 不执行部署、不推送、不改任何真实数据库；`install.sh` 只写 `~/.claude`、`~/.agents/skills`，以及可选的 `${CODEX_HOME:-~/.codex}/AGENTS.md` 自有块。
- 安装器的破坏性动作只允许 `rm`（单个已知对象）与 `rmdir`（空目录），**禁止 `rm -rf`**。
- 凭证不入库：用户级密钥放 `~/.config/ai-anti/secrets.json`，仓库内只写查找接口。
- 发布公开版前跑 `scripts/export_public.sh`，导出树必须通过 `scripts/check_hygiene.sh`。
