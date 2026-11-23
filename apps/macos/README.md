# AIAnti 菜单栏应用

应用通过本机菜单栏与设置窗口管理配置和检测。业务评审从 Claude Code、Codex 或 AGY 发起。
可配置 Claude→Codex、Claude→API、Codex→API/Qoder、AGY→Codex；既有 Codex→Claude 配置仍可读取。
配置范围不等于全链验证：DeepSeek 完整流程和 Claude 普通登录完整链路仍待补验。
路径级版本与证据见 [兼容性说明](../../docs/COMPATIBILITY.md)，数据外发、证据保留、凭证和恢复备份见
[安全说明](../../SECURITY.md)。

## 本地构建

需要 macOS 13+、Swift 6.1 工具链和 Python 3.11+；没有第三方依赖。

```bash
bash apps/macos/build-app.sh /private/tmp/ai-anti-app-build
```

输出 `AIAnti.app` 可在 Finder 双击启动；脚本只编译并本地 ad-hoc 签名，不安装、不修改用户配置。
自己本机使用不需要 iOS 式开发者账号；ad-hoc 签名不等于 Apple 公证，跨机器分发的签名/公证另行处理。
应用不会登录自启，也不自动更新。Python 不打包在应用中；Finder 启动时按显式 `--python`、Homebrew
常见绝对路径、PATH 候选顺序寻找并有界验证 Python 3.11+，缺失时设置窗口显示原因。
Claude、Codex、AGY（`agy`）和 Qoder（`qodercli`）按 `/opt/homebrew/bin`、`/usr/local/bin`、`PATH`、`~/.local/bin` 的顺序查找。

```bash
/private/tmp/ai-anti-app-build/AIAnti.app/Contents/MacOS/AIAnti \
  --settings --python /opt/homebrew/bin/python3.11 \
  --config-root /private/tmp/ai-anti-fixture \
  --source-root /绝对路径/ai-anti
```

`--settings` 直接打开设置；`--config-root` 是临时 HOME 语义根，覆盖 Claude、Codex、ai-anti、
凭证查找、备份及锁路径。fixture 模式拒绝越界凭证路径，不能拿它指向真实 HOME。
没有 `--source-root` 时，从已安装的 codex-relay skill、Claude codex-review 命令或 AGY 插件链接定位只读源码并验证 core 和正式入口。AGY 主模型优先使用插件所指向的检出。移动或删除源码后，检测需重新指定有效来源。

## 配置与检测

首次启动只读：不创建配置、目录或锁，不调用模型，不读取密钥正文。显示“发现凭证文件”只是文件状态，
需要点击设置窗口中的免费“检查配置”才验证凭证可解析，窗口内持续显示进度、耗时与结果。
“重新读取配置”从磁盘读取 Claude、Codex 和 ai-anti 默认配置，不调用模型；设置中有草稿时确认放弃。
应用常驻期间每 5 秒在后台检查配置指纹和凭证文件状态。没有手工草稿时，发现外部变化会自动重新读取，打开设置也会先读取最新快照，再开放表单编辑。已有手工草稿时，打开设置会在后台检查并保留草稿。若检测到保存时校验的文件发生变化，操作区会用红字列出文件标签。磁盘配置无法读取时也会显示红字提示。请先记下草稿改动，再点击「重新读取配置」。
真实“测试评审模型”会消耗额度，必须手动确认；
兼容 API 最多可能需要 30 分钟，显示耗时并支持取消。只用临时合成仓库，不读取业务材料，不重试、不降级。
本地检查不证明登录、额度、模型可调用或版本完整兼容；真实成功也只证明当次合成检测。
Qwen 使用现有正式 `review.py diff --channel qwen` 全部门禁；Codex 需要支持隔离检测参数的新版本，
验证 JSON 最终 turn 与随机合成文件读取得到的值，不能只凭退出码为零。实际模型没有结构化证据则标“未知”。
检测结果只保存在内存；重启归未检测，配置/端点/凭证文件 stat 改变后失效。

### 设置中的 CLI 安装

打开设置后，「主模型」下方显示当前草稿所需 CLI 的版本与状态，覆盖评审、实施、双方案规划及 Claude 评审回退。`solo` 仅显示主模型所需 CLI。旧配置中的 Qwen 执行器仍使用 Claude 时，保存前会标注「保存后改用 Codex」。

版本探测仅在打开设置、草稿引入尚未检测的 CLI 或点击「重新检测」时运行。菜单栏冷启动和后台配置轮询只读配置。探测只运行 `--version`，每个 CLI 限时 3 秒，终止宽限 1 秒。配置尚未读入时，「重新检测」会先重试读取。

缺失时可点「安装…」查看并复制命令、打开官方来源，或确认在系统「终端」中执行。安装完成后按回车启动登录，输入 `s` 可跳过，随后回到设置点「重新检测」。版本读取失败时可重试检测。

- Claude Code 使用 `curl -fsSL https://claude.ai/install.sh | bash`。[官方安装说明](https://code.claude.com/docs/en/setup)
- Codex 检测到 Homebrew 时使用 `brew install --cask codex`，其余情况使用 `curl -fsSL https://chatgpt.com/codex/install.sh | sh`。[官方安装说明](https://github.com/openai/codex)
- AGY 使用 `curl -fsSL https://antigravity.google/cli/install.sh | bash`。[官方安装说明](https://antigravity.google/docs/cli/install)
- Qoder CLI 缺失时可点「打开官方安装说明」，手动安装后点「重新检测」。[官方安装说明](https://docs.qoder.com/cli/installation)

安装取得官方当前版本，具体工作流的实测版本见兼容性说明。版本显示仅确认 CLI 可执行并返回可识别版本号。

评审服务先选来源，再选模型：千问 Token Plan 提供 Qwen4-Max（qwen4-max）、Qwen3.8-Max（qwen3.8-max）和 DeepSeek-V4-Pro（deepseek-v4-pro）；Qoder 固定 Kimi-K3.1-Pro（kmodel_latest；CLI 明确准入 1.1.74 / 1.1.77，1.1.77 已完成正式 bug-review 实测；桌面路由仅离线验证）；Codex 评审目录含 gpt-6.2-sol、gpt-6.2-astra 等六个模型，支持 CLI 默认及自定义模型 ID；自定义 API 手填模型 ID。目录外模型保留“已有配置”。目录选项不是可调用性验证，DeepSeek 完整流程尚未验证。Qoder 仅 Codex 主模型可新选，Claude 可选择 Codex 评审，AGY 固定使用 Codex 评审；既有其他路由保留显示。

API 服务显示服务地址和“认证 / API 密钥”，CLI 服务使用现有 CLI 登录，仅 Qoder 显示“登录状态未检测”。AIAnti 设置窗口对两种主模型的 Qwen 评审统一使用 Codex CLI · Responses。Qwen 为首选或回退时，窗口显示「评审服务地址（Responses）」，Claude 主模型选 Codex 评审服务时隐藏该字段。任一主模型的通道含 Qwen 时，保存需要填写 Responses 地址，保存保留通道顺序。两侧生效的执行器均为 `codex` 时，执行器配置不会阻断检测。任一侧生效值为 `claude` 时，保存后才能检测。需要在 CLI 中使用 Anthropic 时，Claude 主模型手动设置 `claude_relay.qwen_executor = "claude"` 并移除旧键，Codex 主模型手动设置 `codex_relay.qwen_executor = "claude"`。新版 AIAnti 保存会将两项都写回 `codex` 并移除旧键。Responses 路径不支持评审已提交的 commit。 所有真实合成检测统一读取用户配置 `review.timeout` / `review.idle_timeout`（默认1800/900秒，上限3600/900秒，先到者终止）。Qwen/Qoder正式入口外围为总时间+60秒，界面3700秒覆盖最大3600+60并支持取消；极慢本地准备仍可能触发外围预算。Qoder保留2MiB输出上限，保留正式 runner 全部门禁和实际 Kimi-K3.1-Pro 证据校验。

旧键 `plugins.qwen.claude_executor` 仍可读取，新旧键取值冲突时会报配置错误。迁移前先在仓库根目录用新源码运行 `bash apps/macos/build-app.sh <输出目录>`，并替换已安装的 AIAnti。旧版 App 保存时仍会写旧键为 `codex`，如果此时新键为 `claude` 就会冲突。

回滚前先手工把 `claude_relay.qwen_executor` 的值写回 `plugins.qwen.claude_executor` 并删除新键。确知上次 App 保存就是迁移时，也可用「恢复上次保存」。两种方式都须确认磁盘上的旧键等于期望值，再回退代码。若已替换新版 App，用回退后的源码重建并替换。

Claude 主模型的「双方案规划」提供 `enable` / `disable` 下拉选项，只修改 `claude_relay.dual_plan.enabled`，选择 `enable` 时下方显示当前配置摘要。协作方、模型、深度、综合方与失败处置在 `~/.config/ai-anti/config.json` 中编辑，编辑后重新读取配置。按 solo 运行的任务跳过双方案，当次指令与会话内 `/disable-dual-plan` 优先。

Claude 主模型选 Codex 评审服务且回退含 Qwen 时，如 Responses 地址为空，可先切到 API 评审服务补填，再切回 Codex 保存。

两个主导工具共享当前 API 服务及其模型、凭证；两种协议地址独立。每个服务独立保留模型、地址、未保存输入及认证动作，切回恢复；同服务换模型不会改密钥。千问新草稿预填官方两种协议地址，自定义留空。取消放弃全部服务与主导工具草稿，保存失败保留草稿，保存仅影响下次调用。菜单栏下拉显示当前配置（主模型、评审服务、实施服务、协作模式各一行）、设置与退出，读取或保存出错时另显示一行错误；具体设置在设置窗口中修改，不保存空地址。

认证动作默认为保留；更换后输入非空新密钥，空输入仍保留；清除须显式选择并保存。密钥按服务写入 secrets.json 的 model_services.token_plan.value / model_services.custom_api.value，0600受限明文。清除删除该服务应用管理槽的值、清空其凭证路径并切换为独立键，防止外部旧密钥复活；不删除外部文件，也不删除 legacy tokenplan.value。首次加载只查文件 stat，不读取密钥正文，因此“发现凭证文件”不表示已配置或已登录。动作通过独立 secret_actions 请求传递，不写入公共 values、argv 或日志。

model_services（version=1、active_api、固定 token_plan/custom_api profiles）是 API 配置真源。保存时，plugins.qwen 的五个公开字段 model/base_url/responses_base_url/credential_paths/credential_key 与活动 profile 同步，其他用户字段保留。入口仍为 channel=qwen，profile 不存明文 token。credential_key 仅允许 tokenplan.value、model_services.token_plan.value、model_services.custom_api.value；指定键缺失不回退，只有旧配置缺字段时使用 legacy。旧客户端不懂新槽，请使用配套源码；不承诺新配置兼容旧 runner，新密钥不复制到 legacy。

旧配置首次加载只在内存迁移：所有非空端点必须精确等于已知千问默认地址才归千问，否则（包括空端点、混合地址、尾斜杠）归自定义 API 并保留模型/地址/路径顺序。只有迁移到的一个 profile 继承旧凭证键及路径；另一个没有 legacy 凭证。首次保存才持久化。未知版本、字段错误或 profile/投影冲突拒绝写入。冲突错误只列字段名；修复时备份配置，然后按 model_services.active_api 对应 profile 将 model、base_url、responses_base_url、credential_paths、credential_key 同步到 plugins.qwen，保留其他配置和密钥，再重新读取；不要删除配置或密钥来绕过错误。

主模型选项决定编辑哪组默认配置。主模型草稿各自保留，一次保存统一写入实际修改。Claude 和 Codex 的空主模型表示保留已有默认值。空 Codex 评审模型表示 CLI 默认（桌面真实 Codex 检测需明确 ID）。协作模式仅接受 solo、auto、rules、force，旧 required 无效。用户当次指令 > 项目 .claude/relay-mode.local > 全局默认 > rules；solo 停用交叉评审及独立实施。菜单与检测要求先保存或取消草稿。

保存前在进程间锁内比对四个文件指纹。初检冲突会列出变化的文件标签（ai-anti 配置、Claude 设置、Codex 配置、凭证文件）并要求重新读取。保存失败时保留设置窗口的草稿，并在操作区显示红色图标和可复制的错误文字。保存使用同目录原子替换、私有备份和最终回读。
多文件失败仅回滚仍等于本次输出的文件，遇外部修改报告部分状态；不宣称与外部编辑器构成强事务。
“恢复上次保存”同样检查指纹，不能覆盖后来手工修改。备份和锁位于
`~/.config/ai-anti/desktop/`（目录0700、文件0600），备份可能含敏感配置，应按密钥保管。
备份原字节以可逆 hex 保存，不是加密；后续保存替换上次备份，恢复成功删除，不定时清理。
卸载脚本或删除应用不自动移除用户配置、密钥或备份，清理须自行明确选择；不要误删原配置或 secrets。

### AGY 主模型

在菜单栏或设置中选择 AGY，保存为 `desktop.primary_host = "agy"`。主模型 ID 由 AGY 设置管理。评审与独立实施固定使用 Codex，分别保存到 `agy_relay.review.model` 和 `agy_relay.implementation.model`。评审可选「CLI 默认」，实施需要指定模型，移除已保存的实施配置须手工编辑 `~/.config/ai-anti/config.json`。思考深度在设置中只读显示，保存模型时保留。配置段损坏时设置显示出错字段，修复并重新读取后可切入 AGY 或编辑接力模型。

「双方案规划」下拉选项只保存 `agy_relay.dual_plan.enabled`。启用时显示 Claude 的模型、深度和综合方摘要。细项及失败处置在 `~/.config/ai-anti/config.json` 中编辑。该子节损坏时下拉停用并显示错误字段，其他设置仍可保存。

「检查配置」验证由 `install.sh --with-agy` 管理的插件链接及必需入口，并将 `agy --version` 与实测版本清单比对。非 `solo` 还要求已配置实施模型，并按关联工作树实施入口的版本要求检查 Codex CLI。非 `solo` 且启用双方案时额外检查 `claude --version`。AGY 和 Claude 的清单外版本只提示。AGY 的桌面合成评审按钮停用，可从 AGY 发起评审。

## 离线测试

AppModel 状态验收使用可手动完成请求的 fake client 和终端替身，覆盖探测状态与安装确认。独立编译并运行：

```bash
mkdir -p /private/tmp/ai-anti-app-checks
swiftc -parse-as-library -module-cache-path /private/tmp/ai-anti-app-checks/module-cache \
  apps/macos/Sources/AIReviewBar/CLITool.swift \
  apps/macos/Sources/AIReviewBar/ReviewService.swift \
  apps/macos/Sources/AIReviewBar/ModelCatalog.swift \
  apps/macos/Sources/AIReviewBar/AppModel.swift \
  apps/macos/Sources/AIReviewBar/Bridge.swift apps/macos/AppModelChecks.swift \
  -o /private/tmp/ai-anti-app-checks/AppModelChecks
/private/tmp/ai-anti-app-checks/AppModelChecks
```

覆盖模型目录、已保存未知 ID 保留、API 服务独立草稿切换与保存回读、AGY 首次切换与单字段保存、静默检查不发布 UI、单请求门禁、前台优先、过期成功/失败、草稿保护、保存回读、取消与退出清理。

```bash
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.11 -m unittest discover -s apps/macos/backend/tests -v
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.11 -m unittest discover -s tests -p 'test_desktop_contract.py' -v
```

测试只使用临时 fixture 和 fake CLI，并包含正式入口 prepare-only 契约，不代表线上模型实测。
产品范围、历史证据及后续边界见 [产品说明](../../docs/MACOS_REVIEW_APP.md)。

Codex 主模型新增独立实施服务：Codex 原生、千问 Token Plan（`glm-5.3` / `qwen4-max`）或 Qoder 订阅固定逻辑 `glm-5.3`。
Claude 主模型可选 Claude Code 实施代理或 Codex（指定模型）。评审和实施的 Codex ID 独立保存，均支持自定义；检查配置按 Claude 实施入口的版本钉检查 Codex CLI。

原生模型 ID 切换后保留；Token Plan 实施共享 Token Plan Responses 地址和凭证草稿，但不改变当前
评审服务/模型。solo 停用评审与独立实施控件。允许保存未完成认证的草稿，本地检查仅对
当前实施路由报告缺配置；评审模型测试不证明实施可用。新 GLM 入口需独立业务外发授权，
Token Plan 引入时只进行离线 fake 验证和构建，不自动安装或执行真实实施。详见 codex-relay skill。

Qoder 实施选择保存为 `implementation_provider=qoder`，固定显示逻辑 glm-5.3（CLI
GLM-5.3），不显示地址/密钥输入，使用已有 Qoder 订阅登录，要求 qodercli 1.1.77。
保留原生 secondary_model，独立于评审服务及 Kimi 模型；不要求 Token Plan URL/密钥。
检查配置只验证 CLI 存在及版本，不发付费模型请求、不验证登录或实施。solo 停用独立
实施。CLI 身份不证明后端路由；工具限制不提供文件/网络硬隔离。

Token Plan 独立实施字段为 `codex_relay.token_plan_implementation_model`，仅允许
`glm-5.3` / `qwen4-max`；缺失或 null 默认 GLM，空串、大小写变体、preview 和未知值拒绝，
非当前服务也校验。桌面保存/刷新/恢复保留该选择，旧客户端省略字段时保留既有值。
它不改变评审模型或 native `secondary_model`，仍共享 Token Plan profile 的地址与凭证。
Qwen4 评审和实施请求 high effort；GLM 与既有模型维持 medium。Responses 只记录请求模型，
不证明服务端身份；目录选项和评审测试都不证明实施可用。回滚前先选择 GLM；旧版本固定 GLM。
本次不自动启用新模型，不安装、不改真实用户配置。账号/地域与真实模型工具往返需单独验收。


## Token Plan 双订阅槽位

Token Plan 评审和实施共享主账号、备用账号及“主账号额度用尽时自动切换”开关；自定义 API、原生 Codex、Qoder 不参与。主账号仍使用 `model_services.profiles.token_plan` 的凭证接口（旧配置可继续使用 `tokenplan.value`）；备用独立保存到 `secrets.json` 的 `model_services.token_plan_backup.value`，文件权限 0600，不回传密钥到 UI。公开配置为：

```json
{"token_plan_accounts":{"version":1,"auto_failover":false,"backup":{"credential_paths":[],"credential_key":"model_services.token_plan_backup.value"}}}
```

启用前两个槽必须配置有效且不同的密钥。每次新任务主账号优先；真实 HTTP429 的明确额度耗尽 JSON 才允许同一模型、同一 URL、同一请求字节换备用认证重发一次，当次任务的后续阶段与有限暂态重试持续使用备用。CLI 会话及已完成工具操作不重启，备用耗尽不循环。普通429限流、401/403、500、超时、断流、已开始的 SSE、账户绑定的 `previous_response_id` / `conversation` 均不跨账号重放。SSE 内错误暂不切换。

启用时只在本次运行内启动有本地认证的 loopback 转发器，真实密钥只保留于父进程内存；上游要求 HTTPS 并验证证书，保留代理及证书环境。CLI 到 localhost 绕过代理。请求及成功响应各上限8MiB，错误 JSON 上限64KiB，最多4个连接；不支持压缩请求与未知传输编码。超限明确失败，不扩大上下文或另起进程。关闭开关保留备用密钥；回滚旧版前先关闭开关，旧版不提供自动切换。manifest/status 的账号槽与切换原因只是此次运行证据，不是实时余额。prepare-only仅验证公开配置，不读取密钥或联网。
