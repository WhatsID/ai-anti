# CLI 版本兼容性

机器可读清单是仓库根 `compat.json`。`install.sh --status` 打印本机 `claude`、`codex`、`python3`
的实际版本并标出是否在清单内；`claude-review` 把 `claude --version` 原样记进 manifest 的
`cli_version`，版本不在清单内时在 `status.json`、manifest 与报告开头各写一条警告。
**警告不阻断**：不在清单内只说明没有实测过，真正决定结果的仍是各入口的 fail-closed 门禁。

## 实测通过的版本

| 组件 | 版本 | 实测路径 |
|------|------|----------|
| Claude Code | 2.1.258 | Codex→Claude：`$claude-review` plan 两阶段与 diff 评审（`completed`，实际模型同家族匹配） |
| Claude Code | 2.1.280 | Codex→Claude：`$claude-review` diff 评审（`completed`，init 上报内置插件后按白名单放行） |
| Codex CLI | 0.156.0 | Claude→Codex：`/codex-review` 方案评审（`codex exec --sandbox read-only`） |
| Python | 3.9+ | `claude-review` 脚本与安装器内嵌脚本只用标准库；没有 `tomllib`，所以用户配置用 JSON |

只在 macOS 上验证过；其他平台未验证。

## 实测过的评审模型

| 评审模型 | 接入方式 | 实测程度 |
|----------|----------|----------|
| `gpt-6-astra` | 内置 `codex` 通道（Codex CLI 默认模型） | 方案评审与增量评审完整跑通 |
| `claude-opus-5`、`claude-opus-5-5`、`claude-fable-5-1` | Codex → Claude 路径的 `$claude-review` | 方案（两阶段）与增量评审完整跑通 |
| `qwen3.8-max` | `qwen` 插件（anthropic 兼容端点） | 方案评审与增量评审完整跑通，单次通常要 10 分钟以上 |
| `qwen3.7-max`、`deepseek-v4-pro` | `qwen` 插件（同一端点） | 仅验证端点可调用，尚未做完整评审 |

模型更新后按「升级 CLI 之后」同样的做法：先跑真实冒烟，通过后再追加到本表并写明实测程度；
只做过端点连通性检查的，不能写成「完整跑通」。

## 已知不兼容

- **Claude Code 2.1.280 的内置插件**：`--safe-mode` 下 init 事件仍上报 `agents-md@builtin`、
  `telemetry@builtin`，旧门禁要求插件列表为空，于是在该版本上一律失败。现门禁只放行名字、
  路径、来源三项都精确对上的这两个内置插件；CLI 以后新增或改名内置插件仍会 fail closed，
  需要人工判断后再放行。
- **`claude -p` 的模型直传**（2026-08-18 实测）：用 `ANTHROPIC_MODEL` 直传非 Claude 模型名会被
  CLI 客户端侧的模型注册表拦下，插件通道必须走「档位别名 + 档位默认模型映射」，见
  `plugins/qwen/commands/qwen-review.md`。

## 升级 CLI 之后

1. 跑一遍对应路径的真实冒烟（`/codex-review diff`、`$claude-review diff`、插件最小调用）；
2. 通过后把版本追加进 `compat.json` 与「实测通过的版本」表，并写明实测了哪条路径；
3. 失败时不要放松门禁迁就新版本，先查清是哪项能力变了。

## Codex → Qwen 新入口验收

2026-10-10 临时探针与自举方案评审使用 Claude CLI 2.1.280、sonnet 别名及
三个默认模型映射调用 `qwen3.8-max`，两阶段只读材料门禁已验证；这不是正式
新入口验收。主模型随后独立验证正式 `review.py --channel qwen`：临时 Git 小仓库
的 plan 两阶段及 scoped diff 均 `completed`，CLI 2.1.280、init 上报 `qwen3.8-max`，
默认每阶段 timeout 1800 秒。完整材料读取、权限及指纹门禁通过，plan 命中预置
除零异常，diff 命中删除除零检查的回归。plan 调用后仅增补 completed status
的 actual_model/limits 两个留证字段，diff 与最终离线检查使用增补后的代码。
这不覆盖大材料全量评审、真实业务数据或端点内部路由。
离线 fixture 只证明协议与失败路径。实测每次调用有一行已知模型提示；新入口
允许重复但每行 payload 必须精确匹配，保留原始合流日志及 transport warning。
