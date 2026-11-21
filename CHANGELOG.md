# Changelog

## Unreleased

- AGY 主导双方案同步使用冻结提交的隔离规划工作树，Claude 规划封装器支持 `--source-repo` 脱敏，两侧拒绝将 `--out` 写入规划树或发起主树，包括大小写别名。
- 兼容清单加入 AGY CLI 1.5.14：Claude 主导双方案 `draft`/`synthesize` 经隔离规划工作树真实验证；AGY 主导路径在该版本未验证。
- Claude 主导双方案改用冻结提交的隔离工作树，隔离主树并发编辑；新增 `--source-repo` 脱敏发起工作树路径，双方案封装器拒绝 `--extra-file` 中的 `.git` 路径段。
- 双方案规划支持使用受跟踪的 `.ai-anti/plan-snapshot-exclude` 排除路径，并可按项目或通过 `--max-file-bytes` 启用单文件上限。提示词与 manifest 记录快照省略情况。

## 0.3.5 — 2026-11-21

- AIAnti 0.3.5（构建号 13）精简菜单栏下拉（移除检查配置、重新读取配置及检查结果行，菜单项「设置…」改为「设置」），设置窗口将「重新读取配置」移至「测试评审模型」左侧并移除按钮省略号。

- AGY 主导接力支持可选 Claude 双方案规划与综合（`agy_relay.dual_plan`，默认关闭）。只读快照与 Claude 主导规划共享，Claude 流校验读工具路径和实际模型。
- AIAnti 0.3.4（构建号 12）为 AGY 主模型提供双方案规划下拉与只读摘要。非 `solo` 启用时本地检查额外探测 Claude CLI 版本。双方案子节错误单独显示，其余设置仍可保存。
- 回滚到不支持 AGY 双方案的源码前，手工删除 `config.json` 中的 `agy_relay.dual_plan`，保留 `review` 和 `implementation`。切换为关闭会保留此键，`last-save.json` 仅保留最近一次保存的备份。
- AIAnti 0.3.3（构建号 11）支持 AGY 主模型、独立的 Codex 评审和实施模型设置，以及插件入口和 CLI 版本本地检查。AGY 主模型下停用桌面合成评审检测。
- AIAnti 为 Claude 主模型新增双方案规划下拉选项（enable/disable）及只读配置摘要，选择 Codex 评审服务时隐藏 Responses 地址，移除 Codex 评审的登录未检测说明，并移除 Qoder 暂不支持 Claude 主模型的通用提示。
- 新增 AGY 主导的单方案接力及 `--with-agy` 安装选项。Codex 使用独立 `agy_relay` 配置只读评审并在隔离工作树实施，AGY 独立终审。评审入口记录模型请求与材料证据，拒绝连接器事件及未恢复的必要材料读取失败。
- 规划关键设计在出稿前做初步验证，双方案由 Claude 在综合或降级稿落盘前复核采纳的设计并记录证据。
- AGY 草稿与综合提示词要求只读自查及仓库相对引用。发布方案时改写暂存链接和裸路径，manifest 记录改写次数。
- AGY 双方案支持 `--trigger user-message`，综合方可由当次消息指定。产物按任务隔离，每次调用使用新的输出目录，综合读取实际成功的草稿。提示词副本脱敏仓库绝对路径，并在启动前检查 120 KiB UTF-8 上限。
- AGY 规划启动的 `E2BIG` 返回 2，暂存清理失败返回 3 并落盘完整性终态，中断返回 130 或 143（完整性问题优先）。仅退出码 1 且 `fallback_allowed=true`、`integrity_ok=true` 时按 `on_failure` 处理。

- Claude 主导接力支持可选 AGY 双方案规划与综合定稿（`claude_relay.dual_plan`，默认关闭）：新增只读受限执行器 `adapters/claude-code/scripts/agy_plan.py`（`git archive` + 脏追踪叠加的隔离只读暂存、128 MiB 快照上限、R2 附录自动剥离、`draft`/`synthesize` 正文内联与 SHA-256 留证、四级退出码 `0`/`1`/`2`/`3` 与 `fallback_allowed` 门禁）、纯会话级临时禁用命令 `/disable-dual-plan` 及 `compat.json` `tested.agy: ["1.5.13"]`。一期不做 macOS App 界面，App 配置存储通过契约测试保留 `claude_relay.dual_plan`。
- Claude 主导的 Codex 评审和实施可分别配置思考深度（`review.codex_effort`、`claude_relay.implementation.effort`），缺失或为 `null` 时沿用 CLI 默认。评审汇报传入值，实施 manifest 记录 `requested_effort`。
- AIAnti 0.3.2（构建号 10）为主模型 ID 的空值标注「不修改（沿用当前设置）」，为 Claude 区分别名与固定版本。Claude 主导的 Codex 评审和实施继续独立配置，支持目录外及自定义 ID。
- AIAnti 设置窗口对两种主模型的 Qwen 评审统一使用 Codex CLI · Responses。Qwen 为首选或回退时，窗口只显示「评审服务地址（Responses）」。任一主模型的通道含 Qwen 时，保存需要填写 Responses 地址，保存保留通道顺序。发现旧执行器配置时，保存后才能检测。需要在 CLI 中使用 Anthropic 时，Claude 主模型手动设置 `plugins.qwen.claude_executor=claude`，Codex 主模型手动设置 `codex_relay.qwen_executor=claude`。再次用 AIAnti 保存会将两项都写回 `codex`。Responses 路径不支持评审已提交的 commit。 检查配置对 Claude 主导的 Codex 实施验证 CLI 版本，真实检测覆盖评审。

- Claude 主导的接力可配置指定 Codex 模型，在隔离工作树有界实施并保存验收证据。
- Codex 0.169.1 的已取证事件与 stderr 噪声按精确模板识别；等候主模型探针更新实测版本清单。
- 适配 Claude 2.1.326 的已取证事件、内置插件和连接器提示。Qwen 可显式走 Responses，Token Plan 本地代理转发 Claude CLI 的两个精确查询路径。
- Claude 2.1.326 stream-json 按终审样本接纳 init 后、result 前的精确 `system/thinking_tokens` 事件并记录警告。Codex 0.169.1 对带 UTC 时间戳和原样 `https://chatgpt.com/backend-api/ps/mcp` 的逐字 rmcp 消息记录 `remote_mcp_unavailable` 警告。

## 0.3.0 — 2026-11-16

- Token Plan 新增 Qwen4 独立评审与实施选择，实施模型不随评审模型切换；保留原生 Codex 与 Qoder 实施路径，模型证据仍按请求记录，不宣称实际服务端身份。
- Token Plan 支持主、备用双账号；仅遇到明确额度耗尽信号时，在同一次运行内切换并重放同一请求，保留两协议隔离、预算与工具只执行一次的约束。
- 公共配置及本机密钥统一使用 `model_services`，兼容读取 `review_services`；自有已知槽与引用同事务迁移，新旧冲突拒绝保存，外部或项目相对凭证保留原查找语义。
- 旧桌面包和旧独立导出 CLI 不支持新密钥名；使用迁移配置前应更新相应产物，回滚须配对恢复 config 与 secrets 后再运行旧版代码。
- Claude Code 2.1.322 的事件完整性限制仍存在，不将当前受限路径登记为完整兼容。
- AIAnti 应用版本更新为 0.3.0，构建号为 8。

## 0.2.9 — 2026-11-12

- Codex 主导新增 Qoder 订阅 GLM-5.3 实施支持，要求 qodercli 1.1.77；实施服务独立于评审选择，保留原生实施模型 ID。
- App 新增 Qoder 实施选项与版本检查，使用既有订阅登录；有界实施入口保留 CLI 模型证据、失败日志及部分候选，完成后仍须主模型验收。
- AIAnti 应用版本更新为 0.2.9，构建号为 7。

## 0.2.8 — 2026-11-12

- Codex 主导新增独立实施服务选择：原生 Codex 或 Token Plan 固定 glm-5.3，保留原生实施模型 ID；Claude Code 及 solo 行为保持原约定。
- App 共享 Token Plan 地址/凭证草稿，后台严格验证实施来源并检查当前实施路径；新增 prepare-only、有界写任务入口与离线协议/配置/UI 回归验证。
- GLM 证据仅确认请求模型，调用完成仍须主模型独立验收；本轮未追加真实 GLM 业务试跑。
- App 评审服务与实施服务可独立展开/收起，共享字段列对齐；展开时标题行显示服务选择，收起时显示服务与模型摘要。
- AIAnti 应用版本更新为 0.2.8，构建号为 6。

## 0.2.4 — 2026-10-23

- 外部 CLI 评审统一使用用户配置 `review.timeout` / `review.idle_timeout`，默认1800/900秒，上限3600/900秒；idle可先终止，输出上限保持各通道原值。
- Claude、Qoder默认总时长从600增至1800秒，App Codex检测从1200增至1800秒；plan每阶段独立计时。Claude主导命令与App检测共享配置解析，原生Opus子代理不强制计时。
- App正式runner外围为总时间+60秒，界面预算3700秒覆盖最大3600+60，保留取消；极慢本地准备仍可能触发外围预算。
- 设置窗口底部左侧显示当前版本号，与“检查配置”按钮左对齐，版本号自动读取应用包信息。
- AIAnti 应用版本更新为 0.2.4，构建号为 5。

## 0.2.2 — 2026-10-22

- 新增 Qoder CLI 评审通道，支持已验证的 Kimi-K3，并加固隔离与进程清理。
- 设置按评审服务选择模型，独立保存各 API 服务的地址与凭证草稿。
- 恢复传统 macOS 控件样式；调整服务顺序、字段对齐与只读模型信息展示。
- API 密钥改为单输入框，默认掩码显示 50 个圆点，支持保留、更新和清除。
- 修复旧配置缺少千问 Token Plan 地址时的默认值补全，保留已有地址与自定义 API 配置。
- AIAnti 应用版本更新为 0.2.2，构建号为 4。

## 0.2.1 — 2026-10-20

- Qwen Responses / Codex 精确支持 0.160.0 与 0.163.0；未知版本继续预检拒绝，路径级验证边界见兼容文档。
- `install.sh --status` 使用绿色、黄色和红色圆点标识通过、警告和失败，保留原健康判据、计数与退出行为。
- 设置窗口的检测结果增加绿色对号和红色叉号，菜单栏结果保持纯文字。
- Qwen 服务地址为空时，按主模型补入对应的 Responses 或 Anthropic 默认地址；保留自定义地址，草稿须保存后生效。
- 本地检查只显示结果和时间；真实检测保留请求模型及实际模型证据，无法确认服务端模型时明确提示未知。
- GitHub Actions 的 checkout 升级至 v5，使用 Node 24 运行时。
- AIAnti 应用版本更新为 0.2.1，构建号为 3。

## 0.2.0 — 2026-10-20

- 新增 AIAnti 菜单栏配置应用，支持主模型、实施模型、评审模型与执行器状态管理及合成检测。
- 应用图标去除外围灰底，使用透明背景；应用版本更新为 0.2.0。
- 不兼容变更：协作模式 `required` 更名为 `rules`，旧值不再接受；可选模式为 `solo / auto / rules / force`。

- 发布前核对确认仓库仍为私有，私密漏洞报告入口留待公开后启用与复核；安全说明补充 GitHub 功能适用范围，不将 API 404 记为已启用。

- 新增中文最小上手指南及双语 README 入口，串联安装、入口检查、首个小任务、完整接力前提、排查与卸载；明确隔离验收和真实账号验证的边界。

- 关闭桌面产品说明 Codex 模式旧句同步待办：由用户级共享入口读取模式并分流，需要接力时才加载 codex-relay。
- 完成新用户隔离安装、重装、状态诊断、缺失凭证阻断与卸载验收；登录失败仅为离线模拟，提示可定位性已登记，未新增真实通道验收。

- 统一支持与安全说明：区分路径级证据、配置范围与全链验证，补充外发材料、本地证据和桌面密钥备份边界。
- 关闭 Codex 0.160.0 兼容登记待办：依据既有正式 plan / scoped diff 及有限入口样本加入 compat.json；
  安装 status 清单归属同步变化，Qwen Responses 精确版本预检不变。本轮未扩大通道实测范围或新增功能。
- 发布前私密漏洞入口核对及路径兼容结构化登记等后续事项见 FEATURE_TODOS。

- 评审脚本空闲超时默认统一为900秒，保留显式覆盖和总时间预算；同步各执行器测试与现行文档。

- 设置字段改为“评审模型秘钥”；API 执行器固定跟随主模型，Claude→Codex 保留 Codex CLI，旧 Codex 执行器草稿须保存后生效。
- 设置统一三种主模型/评审组合布局，协作模式位于实施模型 ID 下；允许来回切换保留两组草稿，统一事务保存并保护菜单入口。

- Qwen 评审新增可选 Codex CLI Responses 执行器，保留默认 Claude CLI 与独立 Anthropic 地址。
- 新增编号材料完整性与 Codex JSONL 校验，显式 medium；模型证据为 requested_only，不宣称实际服务端身份。
- 显式 idle 上限支持900秒，当时默认仍300秒；900/1800秒预算下正式两阶段方案评审及33块真实增量评审通过，81项适配测试通过。
- README 中英文同步执行器选择与兼容边界，关闭对应文档同步待办；设置窗口固定显示对应执行器、双协议地址独立保存、有效执行器检测与 requested_only 展示。

- Codex 评审入口增加显式 Claude/Qwen 通道及用户默认配置，保留 Claude CLI 与旧 Claude 行为。
- Qwen 使用子进程环境隔离、精确模型/提示校验；默认每阶段 1800 秒，prepare-only 不读凭证。
- 增加离线通道回归测试；正式新入口在独立临时 Git 小仓库的真实 Qwen plan 两阶段与 scoped diff 验证通过。

### 用户级入口

- 两端共享 `core/WORKFLOW_ENTRY.md`；Codex opt-in 增加用户级显式读取入口。
- manifest v3 支持入口生命周期、字节保全与 fail-closed 预检，保留旧 manifest 升级。

### 共享调查与验证方法

- 新增按需共享 DEBUGGING / TESTING，直接做与 solo 同样可用。
- 两端方案、实施派发、容灾及主模型验收引用同源方法，不新增安装对象。
- 明确行为失败信号、候选来源、反证与未覆盖边界，不改变权限或评审裁决。

### 安装体检与项目诊断

- 共享源与托管路由完整性检查，安装前缺源停止，status 汇总缺口。
- 可选 `--status --project DIR` 只读诊断项目根、基线、模式与参数 fallback。
- 保持报告式 exit0，不自动初始化新项目或调用评审通道。

### 共享规划方法

- 新增按需 PLANNING：有限需求澄清、可验收方案与行为切片，供负责规划的主模型读取。
- 两工具接入同源方法，保留 DEBUGGING/TESTING 实施派发，不要求实施者重新规划。
- 安装与 status 共同检查 PLANNING 必要源；缺源安装在写入前失败，status 保持报告式退出。
- 不新增技能安装对象、每仓初始化、工单或代理调度；运行遵循仍需新会话证据。

### 共享模块设计方法

- 新增按需 DESIGN，供设计/规划主模型核对目标、职责、调用契约与依赖退出方向。
- 两工具与共享入口路由，独立实施者执行定稿契约，不重新设计。
- 安装及只读 status 检查 DESIGN 必要源，缺源仍可卸载；不新增安装对象或权限。
- 真实长期设计效果与 Claude 普通登录验收继续待取证，不将用户顺利反馈记为正式通过。

### 2026-10-16｜共享复盘与规则维护方法

- 新增按需 RETRO：证据分类、最小改进、规则归属与维护，不自动扩大授权或每任务复盘。
- 共用入口接入两工具；安装预检与只读 status 检查必要源，缺源仍可卸载。
- 新会话遵循与长期效果分开验证，Claude 普通登录及真实跨项目效果继续待验。

## 0.1.0 — 2026-09-24

初始版本标签；以下为该版本已有能力，不表示当时仓库已公开。

- **Claude → Codex**: `/codex-review` (plan or diff, Codex read-only sandbox) and `/codex-implement`
  (Codex takes over implementation when the implementer subagent is blocked by API failures).
- **Codex → Claude**: `$claude-review` (bounded, repeatable Claude review of a plan or a scoped diff)
  and `$codex-relay` (full plan → review → adjudication → implementation → acceptance relay).
- **Relay workflow** for Claude Code: four collaboration modes (`/relay-mode`), task-size routing,
  triage of review findings into must-fix / register / reject, a two-round review limit, and a
  review-channel fallback order with compensations for same-family review.
- **Review-channel plugins**: channel order comes from user config; `qwen` ships as the first plugin
  (`install.sh --with-plugin qwen`). Contract in `docs/PLUGINS.md`.
- **User config** `~/.config/ai-anti/config.json`: channel order, default deployment scale, model
  family table, secondary implementation model, plugin endpoints. Missing keys use built-in defaults;
  an unparseable file stops instead of silently falling back.
- **CLI compatibility**: `compat.json` lists CLI versions with path-scoped verification evidence; `install.sh --status`
  and `claude-review` warn on untested versions.
- **Installer**: idempotent install / status / uninstall into `~/.claude` (and `~/.agents/skills`
  with `--with-codex`), ownership manifest, fail-closed on conflicts, coexists with the user's own
  SessionStart hooks.
- **Release hygiene**: `scripts/export_public.sh`, `scripts/check_hygiene.sh` and CI.
