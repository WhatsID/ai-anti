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
