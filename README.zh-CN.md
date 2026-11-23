# ai-anti

[English](README.md) | 简体中文

AI 编程中的跨模型接力：一个模型出方案，**另一个**模型对抗性评审，独立的模型实施，主模型终审验收。

## 为什么

单个模型独立完成「出方案 → 写代码 → 验收」时，它自己的盲区会一路带进结果里。ai-anti 把这条链拆给
立场不同的角色：

1. **出方案**：主模型写方案文件，并逐字保留你的原始需求。
2. **交叉评审**：另一个模型只读仓库，以「这是别人做的方案」的立场，挑会导致返工的硬伤。
3. **分诊**：主模型逐条裁决评审意见，分为必修、登记或驳回，防止评审轮次膨胀。
4. **实施**：独立的实施者按定稿方案落地。
5. **终审**：主模型对照方案审查全部增量，再向你汇报。

重点不是让更多模型干更多活，而是让立场不同的第二个模型，找出第一个模型看不见的错。

## 工作原理

### 安装器做了什么

`install.sh` 不复制任何东西进你的项目，只在用户目录下放**符号链接**和**一小段导入**，全部指回本仓库：

| 写到哪里 | 指向本仓库的 | 作用 |
|----------|-------------|------|
| `~/.claude/CLAUDE.md` 中的 `ai-anti` 标记块 | `@adapters/claude-code/CLAUDE.global.md` | Claude Code 每次开会话都会加载，规则由此进入上下文 |
| `~/.claude/commands/*.md` | `adapters/claude-code/commands/` | 斜杠命令：`/codex-review`、`/codex-implement`、`/opus-review`、`/relay-mode`、`/bug-review`、`/disable-dual-plan` |
| `~/.claude/agents/*.md` | `adapters/claude-code/agents/` | 子代理：`implementer`（实施）、`adversarial-reviewer`（同族评审） |
| `${CODEX_HOME:-~/.codex}/AGENTS.md` 中的标记块（`--with-codex`） | `adapters/codex/AGENTS.global.md` | Codex 的用户级入口 |
| `~/.agents/skills/*`（`--with-codex`） | `adapters/codex/skills/` | Codex 技能：`$claude-review`、`$codex-relay`、`$bug-review` |
| `~/.gemini/config/plugins/ai-anti`（`--with-agy`） | `adapters/agy/plugin` | AGY 插件 |

每个安装对象都记在 `~/.claude/ai-anti.manifest.json`，卸载时只删登记过的对象。因为是符号链接，
`git pull` 更新本仓库后，**新开的会话**就会用上新规则；移动仓库位置后要重跑一次 `./install.sh`。

### 一次会话里如何生效

以 Claude Code 为例：

```text
开新会话
  └─ Claude Code 自动加载 ~/.claude/CLAUDE.md
       └─ @import CLAUDE.global.md ── @import core/WORKFLOW_ENTRY.md
            └─ 模型读到：协作模式、任务规模判定、高风险触发器
你提需求
  ├─ 小任务 → 直接做
  └─ 大任务 / 高风险 → 走接力流程：
       写方案 + 需求原文（.claude/plans/）
         （启用双方案时：AGY 同时独立起草，再综合两稿）
       → /codex-review：命令里的脚本读 ~/.config/ai-anti/config.json，
         在只读沙箱里调用 codex exec，评审结果落到临时目录
       → 主模型分诊 → 定稿与需求的差异逐项问你
       → 实施：implementer 子代理，或 Codex 在隔离 git 工作树里实施
       → 主模型终审增量 → 汇报（提交需你明确说「提交」）
```

Codex 的链路与之对应：`AGENTS.md` 标记块 → `AGENTS.global.md` → 同一份 `core/WORKFLOW_ENTRY.md`。
需要接力时，再读取 `codex-relay` 或 `claude-review` 技能，由技能里的脚本调用 Claude 或 Qwen 评审。
AGY 由插件加载同一入口。

### 两层约束

- **规则层**（`core/`、`adapters/` 下的 Markdown）：告诉模型什么时候走流程、每一步怎么做。这是模型
  读了以后遵循的**软约束**，安装成功不等于模型一定照做。
- **脚本层**（`adapters/**/scripts/*.py`）：真正调用外部 CLI 的封装器。负责只读沙箱、隔离工作树、
  CLI 版本钉、超时和输出上限、事件流校验，遇到异常一律 fail closed。这一层是**硬约束**，不依赖
  模型自觉。

项目不需要复制任何文件。项目可以在自己的 `CLAUDE.md` 里覆写参数，例如业务高风险触发器、评审意见
登记去处、部署规模；可覆写项见 `adapters/claude-code/CLAUDE.global.md` 的覆写表。

## 接力流程

| 主导 | 入口 | 评审与实施 |
|------|------|------------|
| Claude Code | `/codex-review plan <方案.md>`、`/codex-review diff` | Codex 在只读沙箱评审；实施由 implementer 子代理负责，也可配置 Codex 在隔离工作树实施 |
| Codex | `$claude-review`、`$codex-relay` | Claude 或 Qwen 评审；`$codex-relay` 跑完整接力 |
| AGY | `agy-relay`、`codex-review` | Codex 评审并在隔离工作树实施，AGY 独立终审 |

- **需求原文**：你的原话逐字存进方案旁边的 `.request.md`。实施前，定稿方案与原始需求之间凡是你能
  感知到的差异，都会逐项请你确认。
- **分诊**：每条意见都必须给出具体失败场景。只有导致数据丢失或不可逆损坏、让本次需求的核心功能
  不工作、或让未授权的人拿到数据时，才算**必修**；其余意见**登记**即关闭；说不出场景的**驳回**。
- **两轮上限**：两轮评审后仍有未解决的必修项，交给你裁决。
- **评审降级**：按配置的通道顺序逐档尝试（默认 Codex → 同族）。同族评审只在全部跨模型通道都不可用时
  启用，并且有额外的补偿要求。全部不可用时，普通任务注明「未经交叉评审」后继续，高风险任务停下问你。

规则全文见 `core/REVIEW_PRINCIPLES.md`；每条规则防的是哪次实际失败，见 `core/DESIGN_RATIONALE.md`。

### 双方案规划（可选）

评审是在方案写好之后挑错；双方案则在出方案这一步就引入第二个模型，减少单一视角导致的方案偏差。

1. **各自起草**：主模型写自己的草稿，协作方同时独立起草。Claude 主导时协作方是 AGY，AGY 主导时是
   Claude。协作方看不到主模型的草稿和设计倾向，只拿到需求原文，并在一份冻结到当前提交的隔离只读
   工作树里阅读代码，你在主工作树里的并发修改不会干扰它。
2. **综合定稿**：由综合方（默认主模型，也可配置为协作方）对照仓库事实合并两稿，记录每个设计点采纳了
   哪一稿、依据是什么。两稿在产品方向或用户可见行为上有实质分歧时，停下来请你裁决。
3. **照常评审**：综合后的方案继续走交叉评审、分诊和后面的步骤。

启用方式：在配置里设 `claude_relay.dual_plan.enabled`（Claude 主导）或
`agy_relay.dual_plan.enabled`（AGY 主导），可选协作方模型、思考深度、综合方，以及失败处置
`on_failure`：`stop` 是停下问你，`fallback` 是退回单方案。Claude Code 里可以用 `/disable-dual-plan`
在当前会话临时关闭；也可以在当次消息里直接要求开或关。双方案会多花一次协作方调用的额度和时间，
适合方向性强或容易想偏的任务。

## 协作模式

| 模式 | 行为 |
|------|------|
| `solo` | 不走接力，全部由主模型完成 |
| `auto` | 主模型自行判断是否走流程，下面的规则只作参考 |
| `rules`（默认） | 满足任一条就走流程：新功能或新模块；预计改动 3 个及以上会影响运行行为的生产文件（测试与纯文档不计）；跨两个及以上顶层子系统；需求含糊、需要先定方案 |
| `force` | 每个实质任务都走流程 |

无论 `rules` 还是 `auto`，命中**高风险触发器**的任务都必须走流程。高风险触发器包括接力机制自身的
改动，以及项目在 `CLAUDE.md` 里列出的数据库结构、部署、认证等。

用 `/relay-mode` 查看或切换模式。项目级设置写在仓库根的 `.claude/relay-mode.local`，全局默认在
`relay.default_mode`。

## 安装

前置条件：macOS、Python 3.9+，以及所选路径需要的
[Claude Code](https://docs.anthropic.com/en/docs/claude-code)、[Codex CLI](https://github.com/openai/codex)
或 AGY，并完成相应登录或配好 API 凭证。

```bash
git clone https://github.com/WhatsID/ai-anti ~/ai-anti
cd ~/ai-anti
./install.sh                 # Claude Code：命令、子代理、CLAUDE.md 导入
./install.sh --with-codex    # 可选：Codex 入口与技能
./install.sh --with-agy      # 可选：AGY 插件
./install.sh --with-hook     # 可选：开会话时播报当前协作模式
./install.sh --status        # 只读体检
```

装完后**新开一个会话**，规则就会生效，在任何仓库里都能用。第一次使用建议按
[最小上手指南](docs/QUICKSTART.zh-CN.md) 走一遍；安装器的所有权规则、Codex 与 AGY 入口的边界、
`--status` 的含义、卸载细节，见 [安装细节](docs/INSTALL.zh-CN.md)。

Codex 里的用法示例：

```text
使用 $claude-review 评审方案 .claude/plans/20260101-task.md
使用 $codex-relay 完成下面的任务：……
```

## 配置

配置文件可选：`~/.config/ai-anti/config.json`，模板见 [config.example.json](config.example.json)。
安装器不会创建或改写它。文件或键缺失时用内置默认；文件不是合法 JSON 时停下报告，不会静默回落默认。
凭证放在仓库外，例如 `~/.config/ai-anti/secrets.json`。

| 键 | 含义 | 默认 |
|----|------|------|
| `relay.default_mode` | 全局协作模式 | `rules` |
| `review.channels` | Claude 主导时的评审通道顺序 | `["codex", "same-family"]` |
| `review.codex_model` | Claude 主导 `/codex-review` 的评审模型 | Codex CLI 默认 |
| `review.codex_effort` / `review.codex_high_risk_effort` | 常规任务 / 高风险任务的评审思考深度 | CLI 默认 / 同常规档 |
| `review.timeout` / `review.idle_timeout` | 外部 CLI 评审的总超时 / 空闲超时（秒） | 1800 / 900 |
| `defaults.deployment_scale` | 项目未声明时，传给评审方的部署规模 | 未声明 |
| `claude_relay.implementation` | Claude 主导时改由 Codex 在隔离工作树实施，如 `{"provider":"codex","model":"…","effort":"high"}` | implementer 子代理 |
| `claude_relay.dual_plan` | Claude 主导时可选 AGY 双方案规划；`/disable-dual-plan` 可在会话内临时关闭 | 关闭 |
| `agy_relay.review` / `agy_relay.implementation` / `agy_relay.dual_plan` | AGY 主导的评审、实施与双方案规划 | 见模板 |
| `codex_relay.review_channel` | Codex 主导时的评审通道 | `claude` |
| `codex_relay.implementation_provider` | Codex 主导时的实施来源 | `codex` |
| `plugins.<name>` | 插件配置，如 qwen 的 `model`、`base_url`、`credential_paths` | 无 |

思考深度只接受 `low`、`medium`、`high`、`xhigh`、`max`、`ultra`，非法值会在启动 CLI 前报错。
同一通道两档都显式设置时，高风险档不得低于常规档。评审风险档由送审编排者按 R3 判定，漏传按高风险
处理。完整字段与取值见模板及 `adapters/codex/skills/claude-review/scripts/ai_anti_config.py`。

## 评审通道与插件

Claude 主导时内置两个评审通道：

| 通道 | 配置名 | 命令 | 说明 |
|------|--------|------|------|
| Codex 评审 | `codex` | `/codex-review` | 跨模型评审，默认第一档 |
| Claude 自评审 | `same-family` | `/opus-review` | 多个 Claude 子代理从不同视角评审；同族评审，只在全部跨模型通道都不可用时启用 |

其他模型以**插件**接入：放在 `plugins/<name>/`，用 `./install.sh --with-plugin <name>` 安装，再把插件名
写进 `review.channels`，排第几位就是第几档。插件契约见 [docs/PLUGINS.md](docs/PLUGINS.md)。

评审方只读仓库、挑方案硬伤，价值主要来自**换一个立场、换一套盲区**，用更便宜的模型通常就够。
例如把 `["qwen", "codex", "same-family"]` 设为成本优先，把 `["codex", "qwen", "same-family"]` 设为质量
优先、插件兜底。

### 接入示例：qwen 插件

1. 安装插件：`./install.sh --with-plugin qwen`
2. 在 `~/.config/ai-anti/config.json` 里配置插件：

   ```json
   {
     "review": {"channels": ["codex", "qwen", "same-family"]},
     "plugins": {
       "qwen": {
         "model": "qwen3.8-max",
         "base_url": "https://<你的 anthropic 兼容端点>",
         "credential_paths": ["~/.config/ai-anti/secrets.json"]
       }
     }
   }
   ```

3. 把 API key 写进 `~/.config/ai-anti/secrets.json`，并执行 `chmod 600`：

   ```json
   {"tokenplan": {"value": "<你的 API key>"}}
   ```

   插件按 `credential_paths` 的顺序取第一个非空的 `tokenplan.value`。路径请写绝对路径或以 `~` 开头。
   配置或凭证缺失时，这一档判为不可用并降到下一档，不会发起半配置的调用。

实测过的评审模型见 [兼容性说明](docs/COMPATIBILITY.md#实测过的评审模型)。

## 进阶

- **Codex 主导的路径**：评审通道由 `--channel claude|qwen` 选择，其次是 `codex_relay.review_channel`，
  失败不自动降级。Qwen 可以选 Claude CLI 或 Codex CLI 的 Responses 执行器
  （`codex_relay.qwen_executor`、`plugins.qwen.claude_executor`、`plugins.qwen.responses_base_url`）。
  调用方式与版本钉见 [codex-relay 技能](adapters/codex/skills/codex-relay/SKILL.md) 和
  [兼容性说明](docs/COMPATIBILITY.md)。
- **Codex 主导的实施来源**：可选原生 Codex、Token Plan（`glm-5.3` / `qwen4-max`）或 Qoder 订阅，
  Token Plan 还支持主、备两个账号在额度耗尽时自动切换。契约与验证边界见
  [codex-relay 技能](adapters/codex/skills/codex-relay/SKILL.md) 和
  [兼容性说明](docs/COMPATIBILITY.md)。
- **显式问题复核**：Codex 用 `$bug-review`，Claude Code 用 `/bug-review`，分「独立分析 → 结论复核」
  两阶段审查已有的问题分析，不要求 Git。见 [docs/BUG_REVIEW.md](docs/BUG_REVIEW.md)。
- **macOS 菜单栏工具**：可选的 [菜单栏应用](apps/macos/README.md)，用来编辑默认模型、评审通道、
  协作模式并做本地检测；业务评审仍从 CLI 入口发起。见 [产品说明](docs/MACOS_REVIEW_APP.md)。

## 兼容性

| 组件 | 有路径限定证据的版本 |
|------|---------------------|
| Claude Code | 2.1.258、2.1.280、2.1.326 |
| Codex CLI | 0.156.0、0.160.0、0.163.0、0.169.1 |
| AGY CLI | 1.5.13、1.5.14 |
| Qoder CLI | 1.1.74、1.1.77 |
| 基础脚本 | Python 3.9+；macOS 已测，其他系统未验 |
| 可选 macOS 菜单栏 | macOS 13+、Swift 6.1、Python 3.11+ |

不同版本验证过的路径不同。[compat.json](compat.json) 是机器可读的版本集合，各路径的证据与限制以
[docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) 为准。未列出的版本可能会警告，也可能被封装器的版本钉拒绝。

## 成本

走流程的任务都会额外调用外部模型。Claude 主导时，每轮方案评审调用一次 Codex，终审可能再加一次增量
评审；Codex 主导时，每轮方案评审调用两次评审模型（先独立列清单，再审方案）。评审最多两轮。同族评审
会并发多个 Claude 子代理，是最贵的一档。

## 卸载

```bash
./install.sh --uninstall
```

只移除 manifest 登记、且仍指向本仓库的对象，以及自己的标记块。单独移除某一部分见
[安装细节](docs/INSTALL.zh-CN.md#卸载)。

## 安全

见 [SECURITY.md](SECURITY.md)：分路径控制、发给模型的数据、本地证据保留、凭证、安装写入范围，以及
私密漏洞报告方式。

## 许可证

[Apache-2.0](LICENSE)
