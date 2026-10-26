# AIAnti 菜单栏应用

首期为本机菜单栏与小设置窗口，管理配置和检测。业务评审继续从 Claude Code / Codex 发起。
配置范围为 Claude→Codex、Claude→API、Codex→API/Qoder；既有 Codex→Claude 配置可读取，不加入首期新选列表。
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
Claude/Codex/Qoder（qodercli）CLI 的发现同样覆盖 `/opt/homebrew/bin` 和 `/usr/local/bin`。

```bash
/private/tmp/ai-anti-app-build/AIAnti.app/Contents/MacOS/AIAnti \
  --settings --python /opt/homebrew/bin/python3.11 \
  --config-root /private/tmp/ai-anti-fixture \
  --source-root /绝对路径/ai-anti
```

`--settings` 直接打开设置；`--config-root` 是临时 HOME 语义根，覆盖 Claude、Codex、ai-anti、
凭证查找、备份及锁路径。fixture 模式拒绝越界凭证路径，不能拿它指向真实 HOME。
没有 `--source-root` 时从已安装的 codex-relay skill 或 Claude codex-review 命令解析只读源码 symlink，
验证 core 和正式入口；不会把当前业务 cwd 当机制源码。移动/删除源码后检测需重新指定有效来源。

## 配置与检测

首次启动只读：不创建配置、目录或锁，不调用模型，不读取密钥正文。显示“发现凭证文件”只是文件状态，
需要点击免费“检查配置”才验证凭证可解析。菜单入口会打开设置窗口，持续显示进度、耗时与结果。
“重新读取配置”从磁盘读取 Claude、Codex 和 ai-anti 默认配置，不调用模型；设置中有草稿时确认放弃。
后台每 5 秒静默检查文件指纹，不禁用设置窗口；外部变化使检测失效，字段需手动重新读取。真实“测试评审模型”会消耗额度，必须手动确认；
兼容 API 最多可能需要 30 分钟，显示耗时并支持取消。只用临时合成仓库，不读取业务材料，不重试、不降级。
本地检查不证明登录、额度、模型可调用或版本完整兼容；真实成功也只证明当次合成检测。
Qwen 使用现有正式 `review.py diff --channel qwen` 全部门禁；Codex 需要支持隔离检测参数的新版本，
验证 JSON 最终 turn 与随机合成文件读取得到的值，不能只凭退出码为零。实际模型没有结构化证据则标“未知”。
检测结果只保存在内存；重启归未检测，配置/端点/凭证文件 stat 改变后失效。

评审服务先选来源，再选模型：千问 Token Plan 提供 Qwen3.8-Max（qwen3.8-max）和 DeepSeek-V4-Pro（deepseek-v4-pro）；Qoder 固定 Kimi-K3.1-Pro（kmodel_latest；CLI 明确准入 1.1.74 / 1.1.77，1.1.77 已完成正式 bug-review 实测；桌面路由仅离线验证）；Codex 保留四个目录模型和 CLI 默认；自定义 API 手填模型 ID。目录外模型保留“已有配置”。目录选项不是可调用性验证，DeepSeek 完整流程尚未验证。Qoder 仅 Codex 主模型可新选，Codex 服务仅 Claude 主模型可新选；既有其他路由保留显示。

API 服务显示服务地址和“认证 / API 密钥”，CLI 服务隐藏这些字段，使用现有 CLI 登录并默认显示“登录状态未检测”。高级设置说明固定执行器及协议：Claude 使用 Anthropic，Codex 使用 Responses；不承诺 Chat Completions。所有真实合成检测统一读取用户配置 `review.timeout` / `review.idle_timeout`（默认1800/900秒，上限3600/900秒，先到者终止）。Qwen/Qoder正式入口外围为总时间+60秒，界面3700秒覆盖最大3600+60并支持取消；极慢本地准备仍可能触发外围预算。Qoder保留2MiB输出上限，保留正式 runner 全部门禁和实际 Kimi-K3.1-Pro 证据校验。

两个主导工具共享当前 API 服务及其模型、凭证；两种协议地址独立。每个服务独立保留模型、地址、未保存输入及认证动作，切回恢复；同服务换模型不会改密钥。千问新草稿预填官方两种协议地址，自定义留空。取消放弃全部服务与主导工具草稿，保存失败保留草稿，保存仅影响下次调用。菜单显示服务与模型，未完成配置时引导设置，不保存空地址。

认证动作默认为保留；更换后输入非空新密钥，空输入仍保留；清除须显式选择并保存。密钥按服务写入 secrets.json 的 review_services.token_plan.value / review_services.custom_api.value，0600受限明文。清除删除该服务应用管理槽的值、清空其凭证路径并切换为独立键，防止外部旧密钥复活；不删除外部文件，也不删除 legacy tokenplan.value。首次加载只查文件 stat，不读取密钥正文，因此“发现凭证文件”不表示已配置或已登录。动作通过独立 secret_actions 请求传递，不写入公共 values、argv 或日志。

review_services（version=1、active_api、固定 token_plan/custom_api profiles）是 API 配置真源，plugins.qwen 为同次事务更新的兼容投影；入口仍为 channel=qwen。每个 profile 包含 model/base_url/responses_base_url/credential_paths/credential_key，不存明文 token。credential_key 仅允许 tokenplan.value、review_services.token_plan.value、review_services.custom_api.value；指定键缺失不回退，只有旧配置缺字段时使用 legacy。旧客户端不懂新槽，请使用配套源码；不承诺新配置兼容旧 runner，新密钥不复制到 legacy。

旧配置首次加载只在内存迁移：所有非空端点必须精确等于已知千问默认地址才归千问，否则（包括空端点、混合地址、尾斜杠）归自定义 API 并保留模型/地址/路径顺序。只有迁移到的一个 profile 继承旧凭证键及路径；另一个没有 legacy 凭证。首次保存才持久化。未知版本、字段错误或 profile/投影冲突拒绝写入。冲突错误只列字段名；修复时备份配置，然后按 review_services.active_api 对应 profile 将 model、base_url、responses_base_url、credential_paths、credential_key 同步到 plugins.qwen，保留其他配置和密钥，再重新读取；不要删除配置或密钥来绕过错误。

主模型选项决定编辑哪组默认配置；两组主模型 ID 和评审通道保留独立草稿，一次保存统一写入实际修改。空主模型表示保留已有默认值，空 Codex 评审模型表示 CLI 默认（真实检测需明确 ID）。协作模式仅接受 solo、auto、rules、force，旧 required 无效。用户当次指令 > 项目 .claude/relay-mode.local > 全局默认 > rules；solo 停用交叉评审及独立实施。菜单与检测要求先保存或取消草稿。

保存前重读指纹，与加载快照不符则要求重新读取配置；进程间锁、同目录原子替换、私有备份、最终回读。
多文件失败仅回滚仍等于本次输出的文件，遇外部修改报告部分状态；不宣称与外部编辑器构成强事务。
“恢复上次保存”同样检查指纹，不能覆盖后来手工修改。备份和锁位于
`~/.config/ai-anti/desktop/`（目录0700、文件0600），备份可能含敏感配置，应按密钥保管。
备份原字节以可逆 hex 保存，不是加密；后续保存替换上次备份，恢复成功删除，不定时清理。
卸载脚本或删除应用不自动移除用户配置、密钥或备份，清理须自行明确选择；不要误删原配置或 secrets。

## 离线测试

AppModel 状态验收使用可手动完成请求的 fake client，不调用 Python 或模型。独立编译并运行：

```bash
mkdir -p /private/tmp/ai-anti-app-checks
swiftc -parse-as-library -module-cache-path /private/tmp/ai-anti-app-checks/module-cache \
  apps/macos/Sources/AIReviewBar/ReviewService.swift \
  apps/macos/Sources/AIReviewBar/ModelCatalog.swift \
  apps/macos/Sources/AIReviewBar/AppModel.swift \
  apps/macos/Sources/AIReviewBar/Bridge.swift apps/macos/AppModelChecks.swift \
  -o /private/tmp/ai-anti-app-checks/AppModelChecks
/private/tmp/ai-anti-app-checks/AppModelChecks
```

覆盖模型目录、已保存未知 ID 保留、API 服务独立草稿切换与保存回读、静默检查不发布 UI、单请求门禁、前台优先、过期成功/失败、草稿保护、保存回读、取消与退出清理。

```bash
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.11 -m unittest discover -s apps/macos/backend/tests -v
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.11 -m unittest discover -s tests -p 'test_desktop_contract.py' -v
```

测试只使用临时 fixture 和 fake CLI，并包含正式入口 prepare-only 契约，不代表线上模型实测。
产品范围、历史证据及后续边界见 [产品说明](../../docs/MACOS_REVIEW_APP.md)。
