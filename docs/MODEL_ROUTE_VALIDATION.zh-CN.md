# 三个主模型的评审与实施验证

## 需求与会话归属

Claude Code、Codex、AGY 各验证下表中的 7 条评审路径和 11 条实施路径。验收单位为「主导宿主 × 角色 × 服务 × 请求模型」，共 54 项。同名模型经 Token Plan 和厂商直连分别计数。评审和实施分别验收，无需把 7 个评审模型和 11 个实施模型两两配对。

**每个宿主必须在自己的原生会话中发起验证、检查产物并登记结论。** Codex 会话负责 Codex 的 18 项，Claude Code 和 AGY 各自的 18 项留给相应会话执行。会话归属取决于发起并验收任务的主模型；例如 Codex 调用 Claude CLI 执行 Anthropic 评审，这条证据归 Codex。本轮已在 Codex 会话执行。Claude Code 与 AGY 留待各自会话验证。

| 服务 | 请求模型 ID | 评审 | 实施 | Codex 方案评审状态（2026-11-24） | Codex 实施状态（2026-11-24） |
|---|---|---|---|---|---|
| Token Plan | `qwen4-max` | 需验 | 需验 | Responses 通过 | 正式合成实施已通过（0.169.1） |
| Token Plan | `qwen3.8-max` | — | 需验 | — | 正式合成实施已通过（0.169.1，修复后复测） |
| Token Plan | `deepseek-v4-pro` | 需验 | 需验 | Responses 通过 | 两次复测均通过 5/5 项断言，正式入口均拒绝会话号 0 |
| Token Plan | `deepseek-v4.1-flash` | — | 需验 | — | 正式合成实施已通过（0.169.1，修复后复测） |
| Token Plan | `glm-5.3` | 需验 | 需验 | Responses 通过 | 正式合成实施已通过（0.169.1） |
| 千问按量直连 | `qwen4-max` | 需验 | 需验 | Responses 通过 | 正式直连实施通过，5/5 项断言（API 上报型号匹配） |
| 千问按量直连 | `qwen3.8-max` | — | 需验 | — | 正式直连实施通过，5/5 项断言（API 上报型号匹配） |
| Kimi 官方直连 | `kimi-k3.1-pro` | 需验 | 需验 | Responses 通过 | 正式直连实施通过，5/5 项断言（API 上报型号匹配） |
| Kimi 官方直连 | `kimi-k3` | 需验 | 需验 | Anthropic 通过，Responses 首次失败、复测通过 | 正式直连实施通过，5/5 项断言（API 上报型号匹配） |
| DeepSeek 官方直连 | `deepseek-v4-pro` | 需验 | 需验 | Anthropic 和 Responses 均通过 | 正式直连实施通过，5/5 项断言（API 上报型号匹配） |
| DeepSeek 官方直连 | `deepseek-flash` | — | 需验 | — | 正式直连实施通过，5/5 项断言（API 上报型号匹配） |

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

### 两条官方直连路径的 Responses 复测

Codex 原生会话在适配器提交 `6c5ff80` 上，用 Codex CLI `0.169.1` 从正式方案评审入口重新运行。两组使用与各自 Anthropic 验收相同的合成仓库基线、需求、方案和源文件。每组均在第一次尝试中读完清单阶段的 4 份材料和方案评审阶段的 6 份材料，两阶段退出码均为 0。主模型复核了日志指纹和两份报告。两份报告均指出空输入或全奇数应返回 `None`，方案错误地返回 `0`，所列测试也遗漏了这两个分支。

| 官方直连模型 | Responses 端点 | 清单 / 方案评审耗时 | 验收结果 |
|---|---|---|---|
| Kimi `kimi-k3` | `https://api.moonshot.cn/v1/responses` | 47.7 秒 / 88.6 秒 | 通过。此前工具结果缺失的错误本次未复现，原因尚未定位。 |
| DeepSeek `deepseek-v4-pro` | `https://api.deepseek.com/responses` | 31.3 秒 / 87.6 秒 | 通过。 |

截至本轮，Codex 主导的 7 条方案评审路径均至少有一次正式 Responses 验收通过。Responses 入口只记录请求型号（`model_evidence=requested_only`）。本轮本机证据在 `/private/tmp/ai-anti-responses-recheck.l2qqLTKQ`，其下 `kimi/` 和 `deepseek/` 各有 `manifest.json`、`status.json`、两阶段日志及 `report.md`。

## Codex Token Plan 实施验收 · 2026-11-24

这一阶段 Codex 实施通过 2/11 项。Codex 主导的正式 Token Plan 实施入口以 Codex CLI 0.169.1 请求
`glm-5.3` 和 `qwen4-max`，各在独立的 `first_even` 工作树完成写入。两个候选各只改动
`numbers_sample.py`，主模型独立复跑下文定义的 5 项断言均通过。两次运行均记录
`model_evidence=requested_only`，各从主订阅切至备用订阅一次。当前精确准入 0.160.0 / 0.163.0 / 0.169.1。
这两项通过计入实施列。

两份正式 manifest、配置和日志位于本机临时目录
`/private/tmp/ai-anti-token169.SfYAr1UA/live3-glm-5.3` 与
`/private/tmp/ai-anti-token169.SfYAr1UA/live3-qwen4-max`。适配器候选基于 `a098c73`。
[执行与隔离记录](COMPATIBILITY.md#2026-11-24--codex-01691-token-plan-独立实施)

### Codex Token Plan 配置准入

三条新增 Token Plan 路由分别通过正式 `implementation.py --prepare-only` 验证，覆盖配置字段与显式指定型号两种入口。隔离运行的状态记录均为 `prepared`、`completed=false`。该阶段仅完成离线准备。

### 三个新增型号的真实实施结果

在提交 `0835503` 上，经 Codex 主导的正式 `implementation.py` 入口和 Codex CLI `0.169.1`，每个型号各运行一次。三个独立工作树均基于 `86e2c4f`，均使用备用订阅，模型证据为 `requested_only`。底层 CLI 均以 0 退出，记录了 `turn.completed`；三个工作树各只改动 `numbers_sample.py`，主模型独立复跑各 5 项断言全部通过。三个工具进程均通过既定的环境变量、PATH 与环回网络隔离检查。

| 请求型号 | 耗时 | 对原始日志重跑正式校验的结果 |
|---|---:|---|
| `qwen3.8-max` | 48.3 秒 | 工具调用填写了 `justification`、缺少显式 `sandbox_permissions`。CLI 在 stderr 记录参数校验错误，实施校验器报 `unknown Codex stderr line`。 |
| `deepseek-v4-pro` | 54.7 秒 | 先遇到同一类 stderr 参数诊断。原始事件还包含该型号的元数据回退提示，现有实施规则仅对 GLM 的精确提示设有例外。 |
| `deepseek-v4.1-flash` | 14.4 秒 | CLI 在回合开始前以 error item 报告缺少该型号的元数据并使用回退元数据，实施校验器报 `implementation error item`。 |

当时三份正式状态均为 `failed`，入口退出码均为 1。该阶段 Token Plan 实施正式通过 **2/5**，Codex 全部实施路径正式通过 **2/11**。保留每个工作树与原始日志，未重放模型写入。本机临时目录为 `/private/tmp/ai-anti-token-live.RkI1SMMZ`，各型号的 `result-*` 包含 manifest、事件流和 stderr，`acceptance.json` 汇总独立验收结果。

### 诊断修复后的真实复测

Codex 原生会话在基于 `0835503` 的修复候选上，以 CLI `0.169.1` 从正式 Token Plan 实施入口各运行一次。新工作树均从合成基线 `86e2c4f` 创建。三个 CLI 均正常结束，均使用备用订阅，各自只改动 `numbers_sample.py`。主模型独立检查了 diff、5 项断言、工具环境中凭据变量缺失、固定 PATH 及工具进程访问宿主环回端口被拒绝。每份记录都只能确认请求型号（`requested_only`）。

| 请求型号 | 正式入口 | 主模型核验 |
|---|---|---|
| `qwen3.8-max` | 退出 0，49.6 秒 | 5/5 断言及隔离检查通过。此次运行没有触发工具参数警告。 |
| `deepseek-v4.1-flash` | 退出 0，14.0 秒 | 5/5 断言及隔离检查通过。回合开始前的同型号元数据提示保存在 warnings。 |
| `deepseek-v4-pro` | 退出 1，59.8 秒 | 5/5 断言及隔离检查通过。stderr 新出现 `write_stdin failed: Unknown process id 0`，正式实施仍失败。 |

这一轮 Token Plan 实施正式通过 **4/5**，Codex 全部实施路径正式通过 **4/11**。三份旧失败日志在候选上回放均通过，另外构造的 22 个反例均被拒绝。此前 Pro 的元数据和参数诊断已可处理，此次新错误由既有规则拦截。源码仅接受已观察到的型号、版本与完整诊断。参数诊断还要求同回合存在成对的成功命令。stdout 和 stderr 分开记录，无法证明被拒的工具请求后来成功。主模型单独核验实际交付。

本机证据目录为 `/private/tmp/ai-anti-impl-diagnostics.FszZ7hPA`。其中 `live/acceptance.json`、`live/exit.json` 和各型号的 `result-*/` 对应本轮新运行，`replay-accepted.json` 对应旧日志回放及反例。两阶段方案评审材料与逐项裁决分别位于 `review/` 和 `adjudication.md`。

### DeepSeek Pro 工具会话指令复测

第二轮候选只调整 Token Plan 的工具指令及对应测试，要求 `write_stdin` 使用本回合 `exec_command` 实际返回且仍在运行的正整数会话号，已完成命令直接使用工具返回的输出。主模型确认 Codex CLI 配置指向的 `instructions.txt` 包含新指令。旧 Pro 失败日志在候选上仍被拒绝，千问和 Flash 的两份真实日志回放均通过。本轮新发起的真实模型调用只有 Pro。

从合成基线 `86e2c4f` 创建独立工作树，经正式实施入口请求 `deepseek-v4-pro` 一次。CLI `0.169.1` 使用备用订阅，耗时 51.1 秒，模型证据为 `requested_only`。主模型核对了代码 diff、日志指纹及四次成功的命令执行。工作树仅改动 `numbers_sample.py`，5 项独立断言及工具隔离检查均通过。CLI 正常结束并记录 `turn.completed`，stderr 再次报告 `write_stdin failed: Unknown process id 0`，正式入口退出 1，状态为 `failed`。只在日志副本移除该行作对照，其余事件通过协议校验。

新增文字在本次实测中未能避免错误，第二轮候选保留于隔离工作树。Codex 主导的 Token Plan 实施正式通过 **4/5**，全部实施路径正式通过 **4/11**。本机第二轮证据位于 `/private/tmp/ai-anti-impl-diagnostics.FszZ7hPA/round2/`，其中 `live/acceptance.json` 保存独立验收，`live/result-deepseek-v4-pro/` 保存原始日志和实际指令，`diagnosis.json` 记录对照结果，`round2.patch` 保留本轮候选增量。

## Codex 厂商直连实施验证 · 2026-11-24

适配器提交 `c8a6a7b`。Codex 主模型从同一合成基线 `86e2c4f` 为千问、Kimi、DeepSeek 的六个服务与型号组合分别创建独立工作树，使用厂商自己的直连 Key 与 Chat Completions 端点。探针给模型一个固定目标文件的写入工具，写入后返回随机回执。主模型逐份核对改动范围、代码内容、文件哈希与五项独立断言。

| 厂商端点 | 请求型号 | 代码交付与工具回执 |
|---|---|---|
| `maas.qianwenaiapi.com/compatible-mode/v1/chat/completions` | `qwen4-max`、`qwen3.8-max` | 两个型号均写入并通过 5/5。`qwen4-max` 首次返回正确回执；`qwen3.8-max` 的确认阶段见下。 |
| `api.moonshot.cn/v1/chat/completions` | `kimi-k3.1-pro`、`kimi-k3` | 两个型号首次均写入、返回正确回执并通过 5/5。 |
| `api.deepseek.com/chat/completions` | `deepseek-v4-pro`、`deepseek-flash` | 两个型号首次均写入、返回正确回执并通过 5/5。 |

首轮六个型号均只改动 `numbers_sample.py`，写入与确认两次请求均返回 HTTP 200，API 返回的型号字段均与请求逐字相同。五个型号首次即返回工具发出的随机回执。`qwen3.8-max` 首次在确认阶段又请求了一次工具调用，探针拒绝第二次写入。在新工作树的单独对照中，只在确认请求设置 `tool_choice: "none"`；该次返回正确回执、正常结束，主模型独立复跑 5/5。两次结果均留存。

**在提交 `c8a6a7b` 上，正式实施入口为 0/6。** 对六组分别执行正式 `implementation.py --provider custom_api --model …`，均在 CLI 解析提供方时退出 2，工作树保持干净。当时正式入口只接受 `codex`、`token_plan`、`qoder`。该阶段厂商 API 样例覆盖受限的单文件写入和确认，当时 Codex 全部实施路径的正式通过数为 **4/11**。

原始证据位于本机临时目录 `/private/tmp/ai-anti-direct-impl.JNa3LgGs`。`official.json` 保存六组正式入口结果，`acceptance.json` 保存首轮 API 与独立验收，`qwen-confirm/acceptance.json` 保存千问确认阶段对照；各自的工作树与按型号的 API 状态均保留。

## Codex 正式厂商直连实施验收 · 2026-11-24

**六条厂商直连实施路径全部通过。Codex 正式方案评审为 7/7，正式实施为 10/11。** 余下一项是 Token Plan 的 `deepseek-v4-pro`，此前两次复测均触发无效工具会话号 0。DeepSeek 官方直连的 `deepseek-v4-pro` 在本轮通过。Claude Code 和 AGY 的验收由各自原生会话发起。

Codex 原生会话使用基于 `e1e4df8` 的候选，从生产 `implementation.py` 入口为每个组合执行预检和一次真实实施。每个独立工作树均来自样例基线 `86e2c4f`。通过 `--provider custom_api --model <型号> --file numbers_sample.py` 只授权一个文件。执行器使用厂商直连 Key 调用 Chat Completions，先接收变更提议，再通过关闭工具的请求确认随机回执。主模型核对了两次请求的 HTTP 200、端点及 API 上报型号、入口退出 0、manifest/status、日志和文件指纹，并读取每个工作树的 diff。六个工作树均只改动授权文件。

| 服务 | 请求型号 | 正式入口耗时 | 独立断言 |
|---|---|---:|---:|
| 千问按量直连 | `qwen4-max` | 5.0 秒 | 5/5 |
| 千问按量直连 | `qwen3.8-max` | 11.1 秒 | 5/5 |
| Kimi 官方直连 | `kimi-k3.1-pro` | 6.4 秒 | 5/5 |
| Kimi 官方直连 | `kimi-k3` | 12.2 秒 | 5/5 |
| DeepSeek 官方直连 | `deepseek-v4-pro` | 19.8 秒 | 5/5 |
| DeepSeek 官方直连 | `deepseek-flash` | 1.6 秒 | 5/5 |

模型证据为 `api_reported`，两阶段的响应型号均与请求逐字一致。厂商内部实际后端身份未知。验收范围为本机 Codex 主导的正式 CLI、显式文件授权和隔离工作树中的合成任务。该入口由宿主应用确认后的文本变更，模型没有 Shell 工具。

六次真实调用结束后，文件写入层补充了 Git 子进程的私有创建权限。以六份真实返回的代码离线复核最终写入路径，文件字节一致，已有私有文件及新文件权限均为 `0600`，五项断言分别通过。网络调用及响应校验代码与实测版本指纹一致。

本机证据目录为 `/private/tmp/ai-anti-direct-accept.KtWXgrVL`。在 `live/` 内，`prepare.json`、`run.json` 与 `acceptance.json` 汇总预检、正式运行和主模型独立验收；各 `run-<厂商>-<型号>/` 留存 manifest、状态及运行日志。另见 `review/report.md` 和 `adjudication.md` 中的接入方案评审与逐项裁决。

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
