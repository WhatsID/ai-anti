# CLI 版本兼容性

## 当前支持摘要

机器可读版本集合为根 `compat.json`（schema 1），路径级证据以本文为准。
`install.sh --status` 打印实际版本与清单归属；“在实测清单内”不证明全部路径或安装健康。
正式评审把 CLI 版本记入 manifest；清单外警告本身不阻断，但执行器另有能力与协议门禁，
未列版本并不保证可运行。Qwen Responses / Codex 另有精确 0.160.0 版本预检，未放宽。

| 组件 / 路径 | 版本或前提 | 已验程度与边界 |
|-------------|------------|----------------|
| 基础脚本 / 安装器 | Python 3.9+；macOS 已测 | 标准库脚本；其他操作系统未验证 |
| 可选 macOS 菜单栏 | macOS 13+、Swift 6.1、Python 3.11+ | 本地 ad-hoc 构建；不是公证分发 |
| Claude 主导 → Codex CLI 评审 | Codex 0.156.0 | 历史 `/codex-review` 方案评审证据；不外推新版本全路径 |
| Codex 主导 → Claude reviewer | Claude 2.1.258 / 2.1.280 | 2.1.258 plan 两阶段与 diff；2.1.280 diff，既有内置插件白名单门禁 |
| Qwen 经 Claude CLI | Claude 2.1.280 | 正式 plan / scoped diff，含编号 MCP 正式增量；只支持记录中的模型与材料范围 |
| Qwen Responses 经 Codex | Codex 0.160.0 | 正式 plan 与历史 scoped diff；`actual_model=null`、`model_evidence=requested_only` |
| Codex 用户级入口 / 共享方法 | Codex 0.160.0 | 有限跨项目发现、隔离接力与方法样本；不是任意任务遵循保证 |
| Claude 普通登录主导完整链路 | 待补验 | 历史 Login expired；Qwen bare 鉴权不能代替普通登录验收 |
| DeepSeek | 历史端点 / 离线能力 | 不代表当前配置、桌面外部调用或完整接力通过 |

安装健康、模型遵循与完整接力是三类证据。完整接力还依赖主工具原生独立代理、
显式实施模型及可观察 runtime 证据；普通 CLI 不自动提供全部能力。
主模型和实施者的 runtime metadata 只支持该宿主运行身份；供应方内部模型路由是另一边界，
不能从 requested model 推断 actual model。历史模型 ID 不保证任意用户有权使用。

升级后先取得对应路径真实冒烟与失败门禁证据，再登记版本；失败时不要放松门禁。
Claude 也有 capability / 协议门禁，但并非 Responses 执行器的精确版本 pin。
历史 `claude -p` 非 Claude 模型直传曾受客户端注册表阻挡；插件使用档位别名及默认映射。
详细条件与失败记录见下文，维护待办见 [FEATURE_TODOS.md](FEATURE_TODOS.md)。

## 历史验收记录

以下为当时记录，旧清单、默认值与“本轮未扩大清单”等表述不是当前配置；
失败与补验边界原样保留，不因本次登记追溯改写。

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

## 2026-10-12 Qwen 编号 MCP 合成探针边界

CLI 2.1.280 的真实合成单编号探针使用 `--bare --restricted --tools ''`、
`--allowedTools mcp__materials__read_material`、dontAsk 和严格单 MCP 配置。
init 精确上报唯一编号工具、materials connected/source dynamic、空 skills/slash
commands、既有两项 builtin plugins，实际模型 `qwen3.8-max`。工具返回单个
text，message.content 与顶层 tool_use_result 均为 text 数组；API_KEY 认证成功，
结果 OK，原始日志保留完整 unrecognized_model 提示。

该探针只验证合成 MCP 形态与认证，未证明本轮新服务器、正式 review.py 编号
入口或此前 20 文件真实增量已完成。新实现的离线 stdio/篡改测试亦不证明模型
调用成功；正式 live 结果由独立主模型验收后追加。旧入口的 2026-10-10 completed
记录属于此前路径协议，不转算本轮编号协议验收。

## 2026-10-12 Qwen 编号 MCP 正式增量验收

修正后的正式 `review.py` 重新评审此前未完成的 20 文件增量，CLI 2.1.280、
实际模型 `qwen3.8-max`，状态 `completed`。59/59 冻结块通过读取及内容校验，
工具错误为零，无路径纠错；使用缺省 8 MiB 日志上限，原始日志完整保留。
此结果不外推到任意规模或其他 CLI 版本，也不代表范围外执行器源码已获增量评审。

此前两个正式合成调用失败记录原样保留：第一轮暴露 tools/call 附加对象
`_meta`，服务器现仅忽略该传输元数据；第二轮暴露空文本在 message.content
中被 CLI 改成固定无输出提示。仅当冻结材料为空且顶层结果精确为空 text 数组
时接受该提示，非空材料仍逐字校验。诊断回放不改写旧失败状态；上述全新调用
才是修正后完成证据。独立适配器测试 73 项、桌面契约测试 8 项通过；stdio
探针覆盖编号、篡改、符号链接与进程 EOF。

## 用户级入口验证边界

安装器生命周期在隔离用户目录验证。2026-10-12 真实安装后，Codex CLI 0.160.0
在空目录中新建 `--ephemeral --sandbox read-only` 会话，退出码为 0。提示词未提供
源码路径；结构化 `command_execution` 记录证明会话主动读取实际主仓库的
`adapters/codex/AGENTS.global.md` 和 `core/WORKFLOW_ENTRY.md`，并选择性读取
`relay.default_mode=rules`，最终正确复述普通小任务与重大任务的分流。

该冒烟仅证明本次入口发现与读取，不证明规则完整遵循（本次未执行 Git 根定位命令），
也不证明跨项目完整接力。Claude Code 本轮只有安装状态与离线回归证据，未做新会话
行为冒烟。本记录不扩大 `compat.json` 既有兼容版本清单。原始证据由本次主模型验收
保存于本机 `ai-anti-entry-evidence-20261012` 的 `smoke.jsonl`、提示词、结果与状态记录。

## 共享方法有限行为验证

2026-10-12 独立原生代理直接读取候选共享方法，在两个互不依赖的本地合成示例中
完成前向验证，未提供标准答案。A 分组核对八条 CSV 记录（A 三次超时、一次成功，
B 四次成功），用同进程、同出口对照排除「持续全面断网」的强解释；保留瞬时故障、
目标相关路径及服务端等未定解释，明确未执行网络探针、根因未证实。

B 给公共运费接口新增 5000 边界测试：原实现该项以 `AssertionError: 500 != 0`
失败，其余两项通过；最小修复后相同三项全过。主模型在独立目录对原版与候选
运行同一份新测试，分别退出 1 和 0，确认原缺陷红灯与候选绿灯及一致的测试指纹。
证据由本次验收保存于本机 `ai-anti-methods-evidence-20261012` 的
`forward-results/REPORT.md` 与 `primary-forward/verified.json`、原版及候选日志。

这些是直接提供候选规则的原生代理样本，仅支持所测案例；未验证自动发现全过程、
Claude Code 新会话行为或跨项目完整接力。离线链接与技能结构检查不证明模型遵循，
本记录不扩大 `compat.json` 版本清单，也不代表所有候选检查已完成。

## 安装体检验证边界

主模型在候选导出树独立运行：73 项适配测试、8 项用户入口测试通过；
修复坏 settings JSON 的旧提示文案回归后，14 项旧安装用例与 8 项新体检测试通过。
既有 3 处 hygiene 命中保留，不将全量导出说成全部通过。

落回 main 后，真实 plain status 与临时空项目 `--project` 报告完成：用户级入口、
hook 与托管链接正常，Codex 0.160.0 仅有不在既有实测版本清单中的警告。
只读证据核对相关用户配置与链接指纹未变，空项目未写入；Git 不可用和超时
探针如实报告未验证，并通过 source、用户目录和项目三根无写检查。
原始证据由本次主模型验收保存于本机 `ai-anti-health-evidence-20261012` 的
最终安装/体检日志、真实状态记录及 `real-readonly.json`。

这是只读安装与临时项目诊断验收，不证明模型遵循、通道联网或第4步跨业务项目
完整接力已验证，不扩大 `compat.json` 版本清单。plain status 不解析用户默认参数，
插件仅查链接，不查凭证和通道可调用性。

## 跨项目 Codex 侧验收（2026-10-12）

成熟业务仓库、早期产品仓库和空目录各启动一次 Codex CLI 0.160.0 只读新会话，
提示词没有提供基础设施源路径。三次均退出 0，实际模型 metadata 为 gpt-6-astra；
工具返回证明实际读取用户入口、共享入口及 DEBUGGING/TESTING 全文。空目录没有
复制项目入口，正确报告尚无 Git 基线。部分失败命令的完整返回仅在底层会话记录中
可见，核对时合并 CLI 事件与底层工具返回，不能仅采信最终文字声明。

三个上下文另在隔离临时 Git 副本运行受主模型编排的完整接力样本：Qwen 正式 plan
两阶段均 completed，实际模型 qwen3.8-max；原生独立实施者显式选择 gpt-6.1-sol
并核对运行 metadata，实际读取共享方法后修复同一类合成计数缺陷。主模型分别用
相同测试验证原版 AssertionError 红灯与候选绿灯，并核对两文件范围后落入临时
main。真实项目不写入，既有未提交上下文按原字节保留；不涉及产品功能或业务 CI。

这证明本轮的入口发现与隔离接力样本，不保证任意任务均能无人引导遵循规则。
CLI 入口冒烟与原生宿主接力分别留证，不据此扩大 compat.json 的全路径兼容声明。
旧项目仍发现 required 默认值、机制权威和部署边界描述漂移，已登记维护待办。
本机完整证据位于 ai-anti-cross-project-20261012：REPORT.md、discovery-runtime.json、
各样本 review/report.md、adjudication.md、primary/verified.json 及实现与测试日志。

Claude 正常会话返回 Login expired，仅 hook 播报成功。用户明确裁决先完成 Codex
侧、Claude 留待补验；Qwen bare 鉴权成功不代表 Claude 正常登录或主导接力通过。
补验时机及关闭判据见 [维护待办](FEATURE_TODOS.md)。

## 2026-10-15 Qwen Responses / Codex 执行器边界

新增显式 `--channel qwen --qwen-executor codex --responses-base-url URL` 路径，
默认仍为 Claude CLI。Codex 配置语义固定在 0.160.0，未知版本预检停止；
版本警告查询 `tested.codex`，本轮不扩大 compat.json 清单。指定端点为
`https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1`。

最小真实探针证明该端点能经 Responses 完成文本与编号材料调用；不等于
正式入口的 plan/diff 验收。离线 fixture 只验证协议与失败路径。正式 live
结果须由独立主模型追加，不在实施阶段宣称完成。Codex JSONL 无实际模型
身份，记录 `actual_model=null`、`model_evidence=requested_only`，不可借 requested
值证明服务端模型路由。官方 Model Studio Codex 文档提供 qwen3.8-max catalog
结构，执行器采用文本评审能力与显式输出预算；其他模型不伪造 catalog。

只读子进程不是父 Python 的 OS 隔离。关闭能力与宿主技能发现后仍可能有
全局 AGENTS、系统技能目录、request_user_input 和通用 MCP resource 工具；
协议拒绝所有非材料工具
执行，不能宣称空上下文或只可见一个工具。12KiB 中文传输边界需要本地真实
Responses 后续请求捕获；JSONL 内容匹配本身不能证明未被上下文截断。

本地真实请求发现 `user_instructions` 配置键在 0.160.0 被忽略并产生 error
事件，故不使用该键，也不宣称清除用户指令。仅通过专用设置关闭已知实验
功能提醒；未知配置/运行 error 仍失败，不将所有 error 降为普通日志。

初始候选沿用官方 catalog 的 default xhigh，实际本地请求也为 xhigh；旧
Claude runner 显式 medium，故这属于意外预算提升。现显式设置
`model_reasoning_effort=medium`，保留 catalog 默认元数据。独立本地捕获
已确认两次真实 HTTP 请求均为 medium、12288 字节中文材料完整。此证据
不证明线上静默由 xhigh 导致，也不证明线上时延已修复。

该轮用户明确允许更长等待后，显式 idle 上限从600秒扩大到900秒；当时默认仍为
300秒，单阶段 total 上限仍3600秒。本轮复验使用
`--idle-timeout 900 --timeout 1800`，这只是等待预算调整，不证明线上问题解决。

主模型独立验收结果：最终 medium 候选的正式两阶段 plan 完成，inventory
8块/38.99秒、review 10块/83.16秒，并识别故意放入的需求冲突；本次12文件
真实 diff 完成，33块/531.15秒，33次读取全部匹配、零恢复、单条最终报告。
两项均使用上述指定域名及900/1800秒预算。81项适配测试通过；此前同一
配置/回执改动的37项桌面后端与8项桌面契约测试通过。真实本地 Responses
捕获确认最终候选发送 medium，并完整传递12288字节中文材料。

此前 xhigh 候选的300秒及600秒实测均未完成，超时清理报宿主权限错误；
普通 Python 子进程也能复现该清理错误，检查未发现残留进程。成功样本
不消除这个宿主边界，也不能单独证明时延变化由 medium 引起。本轮真实
diff仍出现超过300秒无输出，当时默认300秒不保证适用大增量；显式900秒预算
仅证明本次完成，不是稳定性保证。原始失败、成功日志及逐项裁决保留在
本次外部运行记录中。仍不声称实际服务端模型身份或全局上下文完全隔离。

### 2026-10-16 空闲超时默认值调整

评审脚本所有通道和执行器的 idle 默认值统一为900秒，显式传参仍优先，上限仍为900秒。
Qwen 每阶段总时间默认1800秒，Claude 仍600秒；总时间上限仍3600秒。
这是基于上述长静默实测及用户要求的默认预算调整，不新增线上稳定性证据；历史实测预算保持原记录。

## 2026-10-16 支持与安全说明登记

本轮将 Codex 0.160.0 加入通用版本集合，保留 0.156.0 和 Claude 既有版本。
依据为上述用户级入口、跨项目有限样本及 Responses 正式验收记录，另已核对共享复盘
正式 plan 的 completed / CLI 0.160.0 / requested_only 留证；本轮没有新增付费模型探针。
通用评审清单外警告与安装 status 清单归属因此变化；严格 Responses 版本门禁保持不变。
安全数据流、凭证与本地证据保留边界见 [SECURITY.md](../SECURITY.md)。
