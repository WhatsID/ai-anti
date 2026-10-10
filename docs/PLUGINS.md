# 评审通道插件契约

评审通道分两类：内置通道 `codex`（`/codex-review`）与 `same-family`（`/opus-review`），以及以
插件形式接入的其他跨模型通道。插件放在 `plugins/<name>/`，由 `install.sh --with-plugin <name>`
按需安装；是否启用、排在第几档，由用户配置 `~/.config/ai-anti/config.json` 的
`review.channels` 决定（缺省 `["codex", "same-family"]`）。路由规则见
`adapters/claude-code/CLAUDE.global.md`「评审通道降级阶梯」。范例插件是 `plugins/qwen/`。

## 目录与清单

```
plugins/<name>/
  plugin.json
  commands/*.md      # 安装时逐文件链到 ~/.claude/commands/
```

`plugin.json` 字段：

| 字段 | 含义 |
|------|------|
| `name` | 插件名，与目录名一致；只允许小写字母、数字与连字符。也是 `review.channels` 里使用的通道名 |
| `kind` | `cross-model`（跨模型）或 `same-family`（同族）。同族插件受 R9 全部补偿条款约束，且只在配置中的全部跨模型通道均不可用时启用 |
| `commands` | 插件提供的命令文件（相对插件目录）。命令名不得与内置命令或其他插件重名，安装器发现重名即停止 |
| `requires` | 依赖：`cli`（需要的命令行工具）、`config`（需要的用户配置键）、`credential_keys`（凭证文件里要读的键） |
| `label` | 汇报时标注的通道名，如「qwen 评审」 |

安装器只校验 `name`、`kind` 与 `commands/*.md` 存在；其余字段是给主模型与读者的契约。

## 命令模板必须做到

1. **运行时注入 core 原文**（R3）：按 `CLAUDE_CONFIG_DIR`（未设时 `~/.claude`）定位本命令的
   链接，`resolve()` 后 `parents[3]` 即 ai-anti 根（`plugins/<name>/commands/x.md` 与
   `adapters/claude-code/commands/x.md` 深度相同），读取 `core/REVIEW_PRINCIPLES.md`，把各模式所需章节原文
   放进提示词；读不到即停止送审。`plan` 模式按 R3 fail closed 检查 sibling request。
2. **遵守 `core/CLI_DISCIPLINE.md` 四条纪律**：命令末尾 `< /dev/null`、输出直写文件、提示词经
   文件传入、后台运行必须监控。模板只引用，不复制四条正文。
3. **配置与凭证 fail closed**：插件参数（模型名、端点等）从用户配置 `plugins.<name>` 读取，
   不写进模板。配置或凭证缺失 → 本档判定不可用，不发起任何调用；配置文件存在但 JSON 解析失败 →
   停止并向用户报告，不当作「未配置」静默降档。凭证只存活在本次 shell 变量里，不写文件、不回显；
   凭证文件不入库，仓库内只写查找接口。
4. **结果判读写死判据**：写明怎样算成功、端点侧失败、客户端侧失败，唯一判据不能是某行日志文字
   （qwen 的 JSON 三态是范例）。
5. **权限拒绝核验**（R10）：拿到输出先核对被拒绝的工具调用；被拒命令对应的「实测」声明作废，
   `plan` 模式下 request 或方案的 Read 被拒则整轮作废。
6. **只报告本档成败**：成功、不可用及原因、配额耗尽及报文里的恢复时刻。插件不决定下一档，
   由主模型按 `review.channels` 顺序处理。
7. **汇报标注**：逐条完整呈现评审意见，标注 `label` 与实际使用的模型名。

## 新增一个插件

1. 建 `plugins/<name>/plugin.json` 与 `plugins/<name>/commands/<name>-review.md`，照上面七条写模板；
2. 在 `config.example.json` 的 `plugins` 下补占位配置；
3. `./install.sh --with-plugin <name>`，再把 `<name>` 写进自己的 `review.channels`；
4. 先用一次最小调用确认端点与凭证可用，再用于真实评审。

## Codex 主导的 Qwen 接入

Codex 的 `claude-review` skill 的源码入口 `review.py --channel qwen` 直接复用 `plugins.qwen` 配置，不经过
`review.channels` 插件路由，也不要求安装 qwen 插件。默认通道来自
`codex_relay.review_channel`，显式参数优先，失败不自动降级。Codex 相对
`credential_paths` 按 `--repo` 解析；Claude 插件现有 shell 按调用 cwd 解析。
两侧都展开 `~`，推荐绝对路径或用户级路径。
