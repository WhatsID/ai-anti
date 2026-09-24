# ai-anti

[English](README.md) | 简体中文

面向 AI 编程代理的对抗性跨模型评审。装一次，之后在任何仓库里从 Claude Code 或 Codex 使用。

## 是什么，为什么

单个模型独立完成「出方案 → 写代码 → 自我检查」时，自己的盲区会一路带到结果里。ai-anti
把这条链拆开，交给立场不同的角色接力：

1. **出方案**——主模型写方案文件，并逐字保留用户的原始需求；
2. **交叉评审**——**另一个**模型只读仓库，对照原始需求审方案，立场是「这是别人做的方案，
   挑会导致返工的硬伤」；
3. **分诊**——主模型逐条裁决评审意见：必修、登记备查、或驳回，避免评审轮次膨胀；
4. **实施**——由独立的实施者按定稿方案落地；
5. **终审**——主模型对照方案审查全部增量，再向用户汇报。

名字取自 **a**dversarial（对抗性）：重点不是让更多模型分担更多工作，而是让立场不同的第二个
模型去找第一个模型看不见的错。

## 两条主路径

| 方向 | 入口 | 做什么 |
|------|------|--------|
| Claude → Codex | `/codex-review plan <方案.md>`、`/codex-review diff`、`/codex-implement <方案.md>` | Claude Code 主导；Codex 在只读沙箱里评审，或在实施子代理因 API 故障受阻时接手实施 |
| Codex → Claude | `$claude-review`、`$codex-relay` | Codex 主导；Claude 在 fail-closed 检查下评审方案（两阶段）或指定范围的 diff；`$codex-relay` 跑完整接力 |

在 Claude Code 里，大任务与高风险任务会自动走接力流程；`/relay-mode` 在 `solo`、`required`
（默认）、`auto`、`force` 之间切换。

## 最小上手

前置：macOS、[Claude Code](https://docs.anthropic.com/en/docs/claude-code)、
[Codex CLI](https://github.com/openai/codex)、Python 3.9+。

```bash
git clone https://github.com/WhatsID/ai-anti ~/ai-anti
cd ~/ai-anti
./install.sh                 # Claude Code 侧：命令、子代理、CLAUDE.md 导入
./install.sh --with-codex    # 可选：Codex skill 链到 ~/.agents/skills
./install.sh --status        # 只读体检
```

之后在任意仓库打开 Claude Code 或 Codex 即可。项目里不需要复制任何东西，机制由用户级
`~/.claude/CLAUDE.md` 加载。项目可以在自己的 `CLAUDE.md` 里覆写参数（高风险触发器、评审意见
登记去处、部署规模等），可覆写项见 `adapters/claude-code/CLAUDE.global.md` 的覆写表。

Codex → Claude 用法示例：

```text
使用 $claude-review 评审方案 .claude/plans/20260101-task.md
使用 $codex-relay 完成下面的任务：……
```

`./install.sh --with-hook` 额外加一个 SessionStart hook，开会话时播报当前协作模式。链接与导入
都是绝对路径：移动仓库位置后重跑一次 `./install.sh`。

## 插件

内置两个评审通道：`codex`（`/codex-review`）与 `same-family`（`/opus-review`，由 Claude 子代理
做多视角评审）。其他通道以插件形式放在 `plugins/<name>/`：用 `./install.sh --with-plugin <name>`
安装，再把名字写进配置的 `review.channels`。首个插件是 `qwen`（经 `claude -p` 驱动 anthropic
兼容端点）。编写插件的契约见 [docs/PLUGINS.md](docs/PLUGINS.md)。

## 配置

可选的用户配置：`~/.config/ai-anti/config.json`（模板见 [config.example.json](config.example.json)）。
安装器不创建也不改写它。文件或键缺失即用内置默认；文件不是合法 JSON 时停下报告，不静默回落默认。

| 键 | 含义 | 默认 |
|----|------|------|
| `review.channels` | 评审通道顺序 | `["codex", "same-family"]` |
| `defaults.deployment_scale` | 项目未声明时传给评审方的部署规模 | 未设置 → 评审提示写明「未声明部署规模」 |
| `claude_review.model_families` | `$claude-review` 接受的 `--model` 取值，及实际模型 ID 必须全匹配的正则 | `opus`、`fable` |
| `codex_relay.secondary_model` | `$codex-relay` 为实施者请求的模型 | 未设置 → 每次任务指定 |
| `plugins.<name>` | 插件配置，如 qwen 的 `model`、`base_url`、`credential_paths` | 无 |

凭证放在仓库之外，例如 `~/.config/ai-anti/secrets.json`；插件只读取你配置的路径。

## 兼容性

实测通过的版本列在 [compat.json](compat.json)，说明见 [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md)。
其他版本只给警告、不阻断，结果仍由 fail-closed 检查决定。只在 macOS 上验证过。

## 成本

每次评审与实施调用都走你自己的账号，消耗对应服务的额度（Claude、Codex 或插件端点）。
`$claude-review` 的方案评审每轮调用两次模型；接力流程的评审最多两轮。

## 卸载

```bash
./install.sh --uninstall
```

只移除 manifest（`~/.claude/ai-anti.manifest.json`）登记且仍指向本仓库的对象，以及
`~/.claude/CLAUDE.md` 里自己的标记块；你自己的文件与 hook 不受影响。单独移除某部分：
`--without-hook`、`--without-codex`、`--remove-plugin <name>`。

## 安全

见 [SECURITY.md](SECURITY.md)：外部 CLI 的沙箱方式、凭证的读取方式、安装器写哪些位置、如何报告漏洞。

## 语言与机制概述

规则正文（`core/`、`adapters/claude-code/CLAUDE.global.md`、命令模板）只有中文，是唯一权威；
它们由模型读取。英文 README 附有一屏机制概述，对应下列要点：

- **五步接力**：出方案（用户需求逐字存入同名 `.request.md`）→ 跨模型评审 → 分诊 → 独立实施者
  实施 → 主模型终审增量并汇报。实施前，定稿方案与原始需求之间凡用户可见的差异，逐项请用户确认。
- **分诊**：每条意见必须给出具体失败场景（输入或状态 → 错误结果）。只有导致数据丢失或不可逆
  损坏、让本次需求核心功能不工作、或让未授权的人拿到数据或获得访问，才算**必修**；其余**登记**
  即算关闭；说不出具体场景的**驳回**并记理由。
- **两轮上限**：采纳必修项不自动复审；只有架构被推翻或出现 blocker 级新事实才开第二轮。两轮后
  仍有未解决的必修项，转用户裁决。
- **降级顺序**：按配置顺序逐档尝试（默认 Codex → 同族）。每档只报告本档结果，由主模型进入
  下一档；本会话已确认额度耗尽的档不再重试。全部不可用时，普通任务注明「未经交叉评审」继续，
  高风险任务停下问用户。
- **同族评审补偿**：同族评审是受限例外，只在全部跨模型通道不可用时启用，且必须同时做到空上下文、
  固定对抗立场、3–5 个不同视角、自跑只读探针、每条 blocker 独立复核，缺一不算通过。

每条规则背后的实际事故见 `core/DESIGN_RATIONALE.md`。

## 仓库结构

```
core/                     共用评审原则、CLI 调用纪律、规则由来
adapters/claude-code/     Claude Code 接力流程、命令、子代理、hook
adapters/codex/skills/    Codex 侧的 claude-review 与 codex-relay
adapters/codex/tests/     Codex skill 的离线测试
plugins/                  评审通道插件（qwen）
scripts/                  发布卫生检查与公开导出
tests/                    安装器测试
install.sh                安装器（bash 3.2，除 python3 外无依赖）
```

## 许可证

[Apache-2.0](LICENSE)
