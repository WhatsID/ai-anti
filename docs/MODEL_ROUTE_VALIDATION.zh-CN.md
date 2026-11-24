# 三个主模型的评审与实施验证

## 需求与会话归属

Claude Code、Codex、AGY 各验证下表中的 7 条评审路径和 11 条实施路径。验收单位为「主导宿主 × 角色 × 服务 × 请求模型」，共 54 项。同名模型经 Token Plan 和厂商直连分别计数。评审和实施分别验收，无需把 7 个评审模型和 11 个实施模型两两配对。

**每个宿主必须在自己的原生会话中发起验证、检查产物并登记结论。** Codex 会话负责 Codex 的 18 项，Claude Code 和 AGY 各自的 18 项留给相应会话执行。会话归属取决于发起并验收任务的主模型；例如 Codex 调用 Claude CLI 执行 Anthropic 评审，这条证据归 Codex。本轮已在 Codex 会话执行。Claude Code 与 AGY 留待各自会话验证。

| 服务 | 请求模型 ID | 评审 | 实施 | Codex 方案评审状态（2026-11-24） | Codex 实施状态（2026-11-24） |
|---|---|---|---|---|---|
| Token Plan | `qwen4-max` | 需验 | 需验 | Responses 通过 | 正式合成实施已通过（0.169.1） |
| Token Plan | `qwen3.8-max` | — | 需验 | — | 配置准入与离线准备已通过，正式实施待验 |
| Token Plan | `deepseek-v4-pro` | 需验 | 需验 | Responses 通过 | 配置准入与离线准备已通过，正式实施待验 |
| Token Plan | `deepseek-v4.1-flash` | — | 需验 | — | 配置准入与离线准备已通过，正式实施待验 |
| Token Plan | `glm-5.3` | 需验 | 需验 | Responses 通过 | 正式合成实施已通过（0.169.1） |
| 千问按量直连 | `qwen4-max` | 需验 | 需验 | Responses 通过 | 缺厂商直连实施入口 |
| 千问按量直连 | `qwen3.8-max` | — | 需验 | — | 缺厂商直连实施入口 |
| Kimi 官方直连 | `kimi-k3.1-pro` | 需验 | 需验 | Responses 通过 | 缺厂商直连实施入口 |
| Kimi 官方直连 | `kimi-k3` | 需验 | 需验 | Anthropic 通过；Responses 失败 | 缺厂商直连实施入口 |
| DeepSeek 官方直连 | `deepseek-v4-pro` | 需验 | 需验 | Anthropic 通过 | 缺厂商直连实施入口 |
| DeepSeek 官方直连 | `deepseek-flash` | — | 需验 | — | 缺厂商直连实施入口 |

## 已有 API 证据

下表记录 2026-11-24 的真实 API 测试。通过条件为模型通过流式响应请求指定工具、参数正确、收到随机工具结果后准确回传。宿主的材料读取、评审质量、隔离工作树写入和主模型验收另行取证。

| 服务 | 已通过的协议与范围 |
|---|---|
| Token Plan 个人版 | 在 `token-plan.maas.qianwenaiapi.com` 用备用订阅跑通表中五个型号的 Responses。Flash 也通过 Anthropic Messages。主订阅在首个请求返回月额度用尽。 |
| 千问按量计费 | 在 `maas.qianwenaiapi.com` 用按量 Key 跑通两个型号的 Chat Completions、Responses 和 Anthropic Messages。此前对 `dashscope.aliyuncs.com` 的测试记录单列。 |
| Kimi 官方 | 在 `api.moonshot.cn` 跑通两个型号的 Chat Completions 和 Responses，请求与响应模型名分别匹配。 |
| DeepSeek 官方 | 在 `api.deepseek.com` 跑通 Pro 和 Flash 的 Chat Completions。官方直连 Flash 使用 `deepseek-flash`，Token Plan 使用 `deepseek-v4.1-flash`。直连 Anthropic Base URL 为 `https://api.deepseek.com/anthropic`，Pro 的正式方案评审已在该入口通过。 |

历史正式评审与实施记录按各自的版本、宿主和服务复用作参考。两阶段 Qwen4 Responses 评审与隔离实施已有历史样本。当前会话的验收进度单独记录。[历史兼容性记录](COMPATIBILITY.md)

## 正式通过条件

- **评审**：由相应主模型从正式评审入口发送合成方案与需求原文，按该入口的材料完整性要求运行，主模型检查评审能否指出预置的具体错误。Codex 方案评审须完成独立清单和方案评审两个阶段。单独记录方案评审、增量评审的覆盖范围；本轮准备的是方案评审样本。
- **实施**：从同一基线为每个组合创建独立工作树，给实施者正确定稿的任务。主模型核对变更范围、读取 diff、独立运行验收断言，记录工作树和测试结果。
- **登记**：每项记录原生会话标识或可定位的会话记录、适配器提交、CLI 版本、服务端点、请求型号、执行器、角色、状态和证据位置。保留入口报告的模型证据级别，如 `requested_only`。额度耗尽、版本不符、路由缺失和已运行但验收失败分别说明。只有该宿主的正式运行与主模型验收均完成，才记为该项通过。

## Codex 会话正式方案评审 · 2026-11-24

适配器提交 `495e8f7`。七个服务与型号组合均完成独立清单和方案评审，并在报告中指出同一处错误：扫描结束或输入为空应返回 `None`，按送审方案会错误地返回合法偶数 `0`。主模型逐份核对了报告、合成仓库基线、需求与方案指纹、两个阶段的完成记录与日志指纹。此次验收范围为方案评审。

- **Responses**：Token Plan 的三个型号、千问按量的 `qwen4-max` 和 Kimi 直连的 `kimi-k3.1-pro` 由 Codex CLI `0.169.1` 执行。五份记录均为 `requested_only`。三条订阅路径均从主订阅切至备用订阅。
- **Anthropic**：Kimi 直连的 `kimi-k3` 和 DeepSeek 直连的 `deepseek-v4-pro` 由本 Codex 会话使用 Claude CLI `2.1.326` 执行；两个阶段均上报相应模型 ID。这两次计入 Codex 主导的验收。
- **Kimi K3 协议差异**：单独走 Responses 时，独立清单阶段在读取多份材料的过程中收到服务端 `invalid_request_error`，提示两项工具调用缺少对应的工具结果，正式入口返回失败。使用同一合成样例独立走 Anthropic 后两阶段均通过。保留两次记录。

**当时的实施预检**：另外 9 个组合均用正式 `implementation.py --prepare-only` 检查。订阅的三个额外型号由模型清单拒绝，六个厂商直连组合在提供方选择处被拒，均为退出码 2。当时 Codex 方案评审通过 7/7 项、实施通过 2/11 项。

逐项 manifest、状态、评审报告和 `implementation-preflight.json` 位于本机临时目录 `/private/tmp/ai-anti-codex-review-matrix.zNZeJyX0`。Kimi K3 的两次记录分别位于 `kimi-kimi-k3` 和 `kimi-kimi-k3-anthropic`。

## Codex Token Plan 实施验收 · 2026-11-24

本轮 Codex 实施通过 2/11 项。Codex 主导的正式 Token Plan 实施入口以 Codex CLI 0.169.1 请求
`glm-5.3` 和 `qwen4-max`，各在独立的 `first_even` 工作树完成写入。两个候选各只改动
`numbers_sample.py`，主模型独立复跑下文定义的 5 项断言均通过。两次运行均记录
`model_evidence=requested_only`，各从主订阅切至备用订阅一次。当前精确准入 0.160.0 / 0.163.0 / 0.169.1。
这两项通过计入实施列。

两份正式 manifest、配置和日志位于本机临时目录
`/private/tmp/ai-anti-token169.SfYAr1UA/live3-glm-5.3` 与
`/private/tmp/ai-anti-token169.SfYAr1UA/live3-qwen4-max`。适配器候选基于 `a098c73`。
[执行与隔离记录](COMPATIBILITY.md#2026-11-24--codex-01691-token-plan-独立实施)

### Codex Token Plan 配置准入

三条新增 Token Plan 路由分别通过正式 `implementation.py --prepare-only` 验证，覆盖配置字段与显式指定型号两种入口。隔离运行的状态记录均为 `prepared`、`completed=false`。三条路由的正式实施待验，Codex 实施验收仍为 2/11 项。

## Codex 会话起跑记录 · 2026-11-24

准备阶段的源码基线为 `a098c73`。当时 PATH 上的 Codex CLI 为 `0.169.1`，Claude CLI 为 `2.1.326`。Codex 的 Responses 只读评审准入包含 `0.169.1`；Token Plan 实施准入仅含 `0.160.0`、`0.163.0`。正式实施可通过 `implementation.py --cli` 指向受支持的独立二进制。这个版本检查发生在实际执行时。[版本门禁](../adapters/codex/skills/claude-review/scripts/review_codex.py) · [实施路由](../adapters/codex/skills/codex-relay/scripts/implementation_config.py)

**本次已完成 7 项评审材料准备和 2 项实施材料准备。** 两个正式脚本均以 `--prepare-only` 运行，9 份状态记录均为 `prepared`、`completed=false`。其余 9 项实施的阻塞原因见矩阵。Claude Code 与 AGY 的本轮原生会话验收均待开始；现有 Claude Code 实施仅接 Claude 代理或原生 Codex，AGY 的评审和实施仅接原生 Codex。

合成仓库仅含 `numbers_sample.py`，基线为 `86e2c4f6409d412b45452b309689099bc442e3df`。需求是取第一个偶数（零和负数均有效），空输入或全奇数返回 `None`。送审方案刻意把未命中时的返回值写成了 `0`。实施使用独立的正确任务书。主模型验收覆盖空输入、全奇数、首项为零、多个偶数以及负偶数，并核对只改动指定文件。

**准备阶段拟定的执行顺序**

1. 在当前 Codex 会话先跑 Token Plan `qwen4-max` 正式方案评审作为对照。使用已冻结样本、相同运行配置和新的输出目录。核对两个阶段的材料读取、报告是否指出回退值错误，以及实际使用的订阅槽位。
2. 复用同一样本依次跑余下 6 条评审。厂商直连的准备配置只引用各自的临时凭证路径，正式运行时需为该次运行绑定对应厂商的 Key 到受支持的 `model_services.custom_api.value`；绑定只在隔离环境内生效。DeepSeek 使用 Claude CLI 的 Anthropic 执行器，仍由本 Codex 会话发起与验收。
3. 为 Token Plan 的两个已准入型号选用受支持的 Codex CLI，在各自工作树执行 `implementation.py`。分别审查 diff、从主模型运行 `python3 accept.py <工作树>` 并登记结果。剩余 9 条待模型清单或实施服务入口支持后再从本宿主验收。

本机临时证据索引（临时目录可能被系统清理）：

- API 原始摘要及 Flash 复测：`/private/tmp/ai-anti-model-probe.MBQgSiq1`。
- 千问按量域名与 Kimi Responses 补测：`/private/tmp/ai-anti-qwen-kimi.USlK5ZVW`。
- Codex 合成仓库、任务书、独立断言、隔离配置及 9 组 manifest/status：`/private/tmp/ai-anti-codex-matrix-3okmwsof`。各项通过 `prepare-review-*`、`prepare-impl-*` 定位；正式运行另建输出目录。
