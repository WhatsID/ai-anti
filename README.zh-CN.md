# ai-anti

[English](README.md) | 简体中文

面向 AI 编程开发过程中的跨模型对抗评审及分模型实施验收机制。

## 是什么，为什么

AI 编程过程中，单个模型独立完成「出方案 → 写代码 → 验收」时，如果有单点盲区会一路带到结果里。ai-anti
把这条链拆开，交给立场不同的角色接力：

1. **出方案**——主模型写方案文件，并逐字保留用户的原始需求；
2. **交叉评审**——**另一个**模型只读仓库，对照原始需求审方案，立场是「这是别人做的方案，
   挑会导致返工的硬伤」；
3. **分诊**——主模型逐条裁决评审意见：必修、登记备查、或驳回，避免评审轮次膨胀；
4. **实施**——由独立的模型按定稿方案实施落地；
5. **终审**——主模型对照方案审查全部增量，再向用户汇报。

这套机制重点不是让更多模型分担更多工作，而是让立场不同的第二个模型去找第一个模型看不见的错，避免单模型盲区导致的隐藏问题或后期返工。

## 两条主路径

目前支持两条接力路径：Claude → Codex 与 Codex → Claude/Qwen。箭头左边是主导的主模型（出方案、裁决、终审），右边是评审或接手实施的模型。

| 方向 | 入口 | 做什么 |
|------|------|--------|
| Claude → Codex | `/codex-review plan <方案.md>`、`/codex-review diff`、`/codex-implement <方案.md>` | Claude Code 主导；Codex 在只读沙箱里评审，或在实施子代理因 API 故障受阻时接手实施 |
| Codex → Claude/Qwen | `$claude-review`、`$codex-relay` | Codex 主导；选定的 Claude/Qwen 模型在 fail-closed 检查下评审方案（两阶段）或指定范围的 diff；`$codex-relay` 跑完整接力 |

## 四级接力档位

跨模型接力等级分为四级：

- solo：禁用接力机制，全部工作都由主模型完成
- auto：rules 档规则只作参考，由主模型综合判断任务是否值得走流程。例如改动文件多但都是机械替换时可以直接做，只改一个文件但逻辑微妙时也可以主动走流程。更灵活，但结果取决于模型当时的判断
- rules（默认）：按固定规则判断是否走接力流程——新功能或新模块、预计改动 3 个及以上会影响运行行为的生产文件（测试与纯文档不计）、跨两个及以上顶层子系统、需求含糊需要先定方案，满足任一即走流程，其余任务直接做。同样的改动总是同样处理，结果可预期
- force：每个任务都强制走接力流程

无论 rules 还是 auto，命中高风险触发器的任务（接力机制自身的改动，以及项目在自己 `CLAUDE.md` 里列出的数据库结构、部署、认证等）都必须走流程；两者的区别只在一般任务上。

仅接受 `solo`、`auto`、`rules`、`force`；旧值 `required` 无效，不作兼容。

可以使用 `/relay-mode` 命令在四种模式之间进行切换。

## 接力要点

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

## 安装使用

第一次使用？先看 [最小上手指南](docs/QUICKSTART.zh-CN.md)：选路径、安装、检查入口、完成小任务及排查。

前置条件：基础脚本需要 macOS、Python 3.9+；按所选路径准备相应的
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) 和/或
[Codex CLI](https://github.com/openai/codex)，以及所需登录或 API 凭证。
Qwen Responses 评审需要 Codex 与所配置 API 凭证，不要求 Claude 登录。
完整接力还需主工具原生独立代理、显式实施模型及可观察 runtime 证据；普通 CLI
不自动具备完整接力能力。文中的模型 ID 不代表任意用户都有使用权限。

```bash
git clone https://github.com/WhatsID/ai-anti ~/ai-anti
cd ~/ai-anti
./install.sh                 # Claude Code 侧：命令、子代理、CLAUDE.md 导入
./install.sh --with-codex    # 可选：Codex 用户级入口及 ~/.agents/skills 技能
./install.sh --status        # 只读体检
```

需要插件评审通道时再加 `--with-plugin <name>`，并按 [config.example.json](config.example.json) 写好
`~/.config/ai-anti/config.json`（见下文「模型扩展」「配置」）。

之后在任意仓库打开 Claude Code 或 Codex 即可。项目里不需要复制任何东西，Claude Code 由用户级
`~/.claude/CLAUDE.md` 加载，Codex 由 `${CODEX_HOME:-~/.codex}/AGENTS.md` 加载。项目可以在自己的 `CLAUDE.md` 里覆写参数（高风险触发器、评审意见
登记去处、部署规模等），可覆写项见 `adapters/claude-code/CLAUDE.global.md` 的覆写表。

Claude Code 的用户级 `@import` 与 Codex 的用户级指令共同读取
`core/WORKFLOW_ENTRY.md`；评审细节和接力技能只在需要时加载。
`./install.sh --with-codex` 同时安装技能及 `${CODEX_HOME:-~/.codex}/AGENTS.md`
自有标记块，显式读取 `adapters/codex/AGENTS.global.md`，块外用户内容按字节保留。
普通重装继承组件并升级旧 manifest；源仓库移动后重装刷新路径。
非空 `AGENTS.override.md` 会遮蔽入口，安装器拒绝安装，不自动改写它。
`AGENTS.md`、override 或路径祖先中的不安全对象、损坏标记也会在写入前拒绝。

更换 `CODEX_HOME` 前先运行 `./install.sh --without-codex`；移除根据 manifest
记录的旧路径处理，同时移除原有 Codex 技能。再以新环境执行 `--with-codex`。
`--uninstall` 仅移除自有块，用户内容保留；仅工具创建且移除后为空的文件才删除。
用户配置 JSON 和业务仓库均不改写。入口在新会话读取；status 检查安装而非证明模型遵循，
外部 CLI 的版本命令可能产生该 CLI 自有缓存。多项目完整接力验收不在此次入口改动范围。

调整模块职责、接口、依赖边界或跨调用方重复业务规则时，负责设计或规划的主模型
按需读取 `core/DESIGN.md`，核对项目目标、调用契约与实质取舍；实施者按定稿契约实现。
文案、小样式不触发完整设计；无需每仓初始化或新增安装对象，既有源码链接传播共享方法。

用户要求复盘、维护规则，或当前任务出现有证据的重复协作问题时，主模型按需读取
`core/RETRO.md`：先分类证据，优先修现有确定性检查，项目经验留项目。实际改规则
遵循现有授权；普通成功任务不自动产出报告。新会话样本仅验证有界行为，不代表
长期效果或 Claude 普通登录已经验收；不新增安装对象。

负责规划的主模型按需读取 `core/PLANNING.md`，用于有限澄清、方案与可验证行为切片。
明确小任务不要求完整计划；方法保持模式和权限边界，不自动发工单、派代理或要求
每仓初始化。独立实施者按定稿方案执行。

共享方法也按需加载：`core/DEBUGGING.md` 指导排障与根因取证，
`core/TESTING.md` 指导行为验证与验收。直接做、solo 和接力都按适用性读取；
实施者获得明确的源路径，主模型独立核对证据。不新增命令或技能安装对象，
不要求每个项目复制文件，也不把所有任务变成 TDD。方法保持授权和评审裁决边界；
行为验证仅支持记录中的案例，不代表跨项目全场景已经验收。

`./install.sh --status --project DIR` 可选诊断显式给定的项目：Git 根、HEAD/local main
基线、项目入口可读性、协作模式及全局部署规模 fallback，不写项目。入口或参数缺失
是提示，不是安装损坏；项目正文覆写仍需 AI 读取。不加 `--project` 不扫描业务目录。
普通 `--status` 检查约定共享源与已安装路由，不解析 relay/defaults 或完整用户配置；
插件仅检查托管链接，不检查 metadata、凭证及通道联网。退出 0 表示报告完成，
不等于所有检查健康，也不证明模型遵循或完整接力；参数错误仍非零退出。

Codex → Claude/Qwen 用法示例：

```text
使用 $claude-review 评审方案 .claude/plans/20260101-task.md
使用 $codex-relay 完成下面的任务：……
```

`./install.sh --with-hook` 会额外加一个 SessionStart hook，开会话时播报当前协作模式。链接与导入
都是绝对路径：移动仓库位置后重跑一次 `./install.sh`。

## 模型扩展

评审模型不限于 Codex。以 Claude 作为主模型为例，内置两个评审通道：

| 通道 | 配置名 | 命令 | 说明 |
|------|--------|------|------|
| Codex 评审 | `codex` | `/codex-review` | 跨模型评审，默认第一档 |
| Claude 自评审 | `same-family` | `/opus-review` | 由多个 Claude 子代理做多视角评审；属于同族评审，只在全部跨模型通道都不可用时启用 |

其他模型支持以**插件**形式接入评审：放在 `plugins/<name>/`，用 `./install.sh --with-plugin <name>` 安装，
再把插件名写进配置的 `review.channels`，排在第几位就是第几档。首个插件 `qwen` 通过 `claude -p`
驱动任意 anthropic 兼容端点，模型名与端点地址都在配置里填写，换模型只改配置，不改代码。
编写新插件的契约见 [docs/PLUGINS.md](docs/PLUGINS.md)。

### 用更经济的模型做评审

评审的价值主要来自**换一个立场、换一套盲区**，而不是用最强的模型。评审方只读仓库、挑方案硬伤，
不写代码，用价格更低的模型或包月订阅通常就够。常见用法：

- **成本优先**：把插件通道排在最前，例如 `["qwen", "codex", "same-family"]`，日常评审走更便宜的模型，
  它不可用时再降到 Codex；
- **质量优先、插件兜底**：保持 `["codex", "qwen", "same-family"]`，Codex 额度用完时自动降到插件通道，
  而不是直接落到成本最高、且只是同族视角的 Claude 自评审。

无论哪种顺序，同族评审都只在全部跨模型通道不可用时启用。

### 接入示例：qwen 插件

1. 安装插件：`./install.sh --with-plugin qwen`
2. 在 `~/.config/ai-anti/config.json` 里填写插件配置，并把 `qwen` 加进通道顺序：

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

3. 把 API key 写进凭证文件 `~/.config/ai-anti/secrets.json`，并执行 `chmod 600` 限制权限：

   ```json
   {"tokenplan": {"value": "<你的 API key>"}}
   ```

   插件按 `credential_paths` 的顺序读取每个文件里的 `tokenplan.value`，取第一个非空值。凭证路径
   请写绝对路径或 `~` 开头的路径（相对路径按调用时的当前目录解析）。配置或凭证缺失时，这一档
   判为不可用并降到下一档，不会发起半配置的调用。

### 实测过的评审模型

实测清单和实测程度见 [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md#实测过的评审模型)；只验证过端点可调用的模型也在其中单独标注。其他厂商的 anthropic 兼容端点理论上可以同样接入，但尚未实测，接入后建议先做一次最小调用再用于真实评审。

### Codex 主导的路径

上述 `review.channels` 只路由 Claude 主导路径。Codex 主导路径由 `--channel claude|qwen`
选择，显式参数优先，其次 `codex_relay.review_channel`，缺省 `claude`；失败不自动降级。
Claude 默认 Opus，可用 `--model fable` 或配置家族。Qwen 直接复用 `plugins.qwen`，
无需安装插件；默认保留 Claude CLI，用 sonnet 别名映射配置模型，拒绝显式 `--model`。
相对凭证路径按 `--repo` 解析，推荐绝对路径或 `~`；Claude 插件按调用 cwd 解析。
`--prepare-only` 不读凭证、不调用 CLI。Qwen 默认每阶段 1800 秒，Claude 600 秒，
idle 均 900 秒；显式覆盖优先，上限为总时间 3600 秒、idle 900 秒。
manifest/status 留证通道与模型，init 模型精确校验不证明端点内部路由。

```bash
REVIEW_SOURCE=/absolute/path/to/ai-anti/adapters/codex/skills/claude-review/scripts/review.py
python3 "$REVIEW_SOURCE" diff --channel qwen --repo /path/to/repo \
  --files src/a.py --scale 'single user local tool' --out /private/tmp/qwen-review
```

Qwen 也可显式选择 Codex CLI 的 Responses 执行器，不依赖 Claude CLI 发起该次评审：

```bash
python3 "$REVIEW_SOURCE" diff --channel qwen --qwen-executor codex \
  --responses-base-url https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1 \
  --idle-timeout 900 --timeout 1800 --repo /path/to/repo --files src/a.py \
  --scale 'single user local tool' --out /private/tmp/qwen-codex-review
```

持久选择使用 `codex_relay.qwen_executor=codex` 和独立的
`plugins.qwen.responses_base_url`；不改写原 Anthropic `base_url`，复用模型及凭证路径。
新执行器精确支持 Codex CLI 0.160.0 和 0.163.0，显式使用 medium；其他 CLI 版本预检停止。
该路径记录 `actual_model=null`、`model_evidence=requested_only`，不证明服务端实际模型身份。
0.160.0 历史正式 plan 与真实 diff 已通过；0.163.0 于2026-10-20通过合成 plan 两阶段验收，未验证新版业务 diff 或用户级入口全流程；大增量仍可能长时间无输出，900/1800秒仅为本次通过的预算。
macOS 设置窗口固定显示各组合的执行器，不可选择；CLI 仍支持显式选择执行器。详见[验证记录与边界](docs/COMPATIBILITY.md)。

AIAnti 设置窗口固定执行器：Codex→API 使用 Codex CLI，Claude→API 使用 Claude Code CLI，Claude→Codex 使用 Codex CLI。旧 Codex API 执行器配置会生成待保存草稿，保存后才能检测。Anthropic `base_url` 与 Responses `responses_base_url` 独立保存，界面仅显示当前有效协议地址，不推导或替换域名；使用所选模型对应入口与密钥，DeepSeek 完整流程尚未验证。Codex 检测只确认请求模型，服务端实际模型未知。API 合成检测预算为运行1800秒、空闲900秒、输出8MiB，外围1860秒、界面1900秒留清理余量，可取消。

## 配置

可选的用户配置：`~/.config/ai-anti/config.json`（模板见 [config.example.json](config.example.json)）。
安装器不创建也不改写它。文件或键缺失即用内置默认；文件不是合法 JSON 时停下报告，不静默回落默认。

| 键 | 含义 | 默认 |
|----|------|------|
| `review.channels` | 仅 Claude 主导路径的评审通道顺序 | `["codex", "same-family"]` |
| `defaults.deployment_scale` | 项目未声明时传给评审方的部署规模 | 未设置 → 评审提示写明「未声明部署规模」 |
| `claude_review.model_families`（仅 Claude 通道） | `$claude-review` 接受的 `--model` 取值，及实际模型 ID 必须全匹配的正则 | `opus`、`fable` |
| `codex_relay.review_channel` | Codex 主导的评审通道；显式参数优先 | `claude` |
| `codex_relay.secondary_model` | `$codex-relay` 为实施者请求的模型 | 未设置 → 每次任务指定 |
| `codex_relay.qwen_executor` | Qwen 评审的 CLI 执行器 | `claude` |
| `plugins.qwen.responses_base_url` | Codex 执行器独立的 Responses 地址 | 无 |
| `plugins.<name>` | 插件配置，如 qwen 的 `model`、`base_url`、`credential_paths` | 无 |

凭证放在仓库之外，例如 `~/.config/ai-anti/secrets.json`；插件只读取你配置的路径。

## 兼容性

以下版本有路径限定的证据：

| 组件 | 已登记版本 / 要求 |
|------|------------------|
| Claude Code | 2.1.258、2.1.280（评审路径） |
| Codex CLI | 0.156.0、0.160.0、0.163.0（已验路径不同） |
| 基础脚本 | Python 3.9+；macOS 已测，其他系统未验 |
| 可选 macOS 菜单栏 | macOS 13+、Swift 6.1、Python 3.11+；ad-hoc 构建，非公证分发 |

[compat.json](compat.json) 为机器可读版本集合，详细路径与证据边界以
[docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) 为准。安装 status 只报告清单归属，
不证明全路径兼容或健康；未列版本可能警告，也可能被独立能力/协议门禁拒绝。
Qwen Responses 精确限定 0.160.0 或 0.163.0，两版本均登记通用集合。
安装健康、模型遵循与完整接力分别取证；Claude 普通登录及 DeepSeek 完整流程仍待补验。

## 成本

采用模型接力机制后，每个走流程的需求都会增加外部模型的评审调用，token 消耗会有所上升：

- **Claude → Codex**：每轮方案评审调用一次 Codex，终审时可再做一次增量评审；降到插件或 Claude
  自评审时，消耗对应服务的额度（Claude 自评审会并发多个子代理，是最贵的一档）。
- **Codex → Claude/Qwen**：`$claude-review` 的方案评审每轮调用两次选定的 Claude/Qwen 模型（先独立列清单，再审方案），
  增量评审每次调用一次。

两条路径的评审都最多两轮。评审模型可以换成更经济的模型，见[用更经济的模型做评审](#用更经济的模型做评审)。

## 卸载

```bash
./install.sh --uninstall
```

只移除 manifest（`~/.claude/ai-anti.manifest.json`）登记且仍指向本仓库的对象，以及
`~/.claude/CLAUDE.md` 与 Codex `AGENTS.md` 里自己的标记块；你自己的文件与 hook 不受影响。单独移除某部分：
`--without-hook`、`--without-codex`、`--remove-plugin <name>`。

## 安全

见 [SECURITY.md](SECURITY.md)：分路径控制、模型外发数据、本地证据保留、凭证与桌面恢复备份、安装写入和私密漏洞报告。

## 许可证

[Apache-2.0](LICENSE)

## macOS 菜单栏配置工具（首期）

[ai-anti 菜单栏应用](apps/macos/README.md) 提供菜单栏＋小设置窗口，管理默认模型、评审通道、协作模式与检测。
可配置 Claude→Codex、Claude→Qwen/DeepSeek、Codex→Qwen/DeepSeek；配置范围不代表各路径均已验证，业务评审仍从现有入口发起。
主导工具只是编辑哪组配置，默认值影响后续调用，当前会话不会切换；既有项目模式优先。
`solo` 同时停用评审和独立实施，Codex 用户级共享入口读取模式并负责分流，接力时再加载 codex-relay。

首次启动只读，不自动调用模型或读密钥正文；免费本地检查不证明登录/额度，手动真实合成检测消耗额度。
密钥复用已有文件或存入受限明文 secrets.json，不是钥匙串；保存有指纹、备份与冲突检查，支持恢复上次保存。
构建交付本地签名 `.app`，不自动安装/登录自启；Finder启动不依赖交互shell PATH，需要 Python3.11+ 和 CLI。
功能边界、配置契约、历史证据与检测限制见 [产品说明](docs/MACOS_REVIEW_APP.md)。
