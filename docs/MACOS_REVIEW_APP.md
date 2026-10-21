# macOS 菜单栏配置与检测

用户确认的首期形态是菜单栏＋小设置窗口，评审业务继续在 Claude Code/Codex 里发起。
它围绕 ai-anti 的评审需求，不是通用模型代理或订阅管理器。主导工具与模型 ID 是两个字段。

## 首期功能

- 组合 Claude→Codex、Claude→API、Codex→API/Qoder；已有 Codex→Claude 能力保留，不增加到首期新选列表。
- 主模型默认值、独立 Codex 评审模型、Codex 实施模型、评审通道、协作默认模式、Qwen 模型/地址/凭证状态。
- 中文菜单快捷选择、设置草稿保存/放弃、免费检查、手动真实合成检测、刷新与恢复上次保存。
- 首次启动只读；不自动检测，不读取或展示密钥正文。窗口开着时监控配置指纹/凭证 stat，使旧检测失效。
- 检测最多一个，后台运行、有界超时/输出、耗时与取消，退出清理模型进程；原始错误与日志不交给界面。

所有默认配置影响后续启动或调用，当前会话不会切换；项目、命令行、会话设置可能覆盖。
菜单表示“当前选中配置”，不表示识别出正在运行的客户端。主导工具只选择编辑组。
Claude 保留已有回退列表；Codex 主导仍按现有正式流程失败停止。
设置内允许未保存时来回切换主模型，独立保留两组主模型 ID / 评审通道草稿；共享 API 与协作配置仍共享。一次保存两组实际修改，失败保留草稿；取消、刷新、恢复和保存成功后重建全部草稿。菜单快捷保存仍在有草稿、忙碌或未加载时拒绝。

## 配置契约

| 权威文件 | 最小写入 |
|---|---|
| `~/.config/ai-anti/config.json` | `relay.default_mode`、`review.codex_model`、`desktop.primary_host`、已有 review/codex_relay/plugins.qwen 配置 |
| Claude `settings.json` | 只改顶层 `model`，保留 permissions、MCP 等 |
| Codex `config.toml` | 只替换简单顶层单行 model 或在文件开头插入，前后 tomllib 验证其他语义一致 |
| ai-anti `secrets.json` | 新密钥时更新对应 `review_services.<服务>.value`，保留未知键、0600，并将用户级路径置首 |
| ai-anti `desktop/` | 进程间锁、上次保存私有备份；0700目录/0600文件 |

正常运行遵循 `CODEX_HOME` 与 `CLAUDE_CONFIG_DIR`。`--config-root` 测试根优先覆盖这些路径，
可解析凭证必须在该根内，不允许父目录跳转或意外 symlink。已有普通项目相对项保留，
桌面无项目上下文时跳过并提示未检查，业务项目可能优先使用；不按应用 cwd 解析或读取。已安装 skill symlink 仅用于只读来源发现。
损坏、空 JSON、错误类型、并发冲突或复杂 TOML 不覆盖。空主模型输入保留既有值；空独立评审模型表示CLI默认。
查看设置不读取凭据；“检查配置”才解析已有文件。凭证是已有执行器兼容的受限明文存储，不声称钥匙串。

模式优先级：当次明确用户指令 > 项目根 `.claude/relay-mode.local` > 全局 `relay.default_mode` > `rules`。
坏配置停止；`solo` 独立完成且同时停用交叉评审与独立实施。`auto` 自动判断但保留强制高风险，`rules` 按规模与高风险，
`force` 所有实质任务走流程。Codex 用户级共享入口读取模式并负责分流，需要接力时才加载 codex-relay。
模式权威见 [共享入口](../core/WORKFLOW_ENTRY.md)；入口加载不等于模型完整遵循或接力已验收。

仅接受 `solo`、`auto`、`rules`、`force`；旧值 `required` 无效，不作兼容。

保存先验证全部内容、比较源指纹、落私有备份，再原子写入各目标并回读。
外部编辑器不共享本应用锁，因此不是强跨文件事务；失败回滚仅覆盖仍等于本次输出的文件，
外部有变化则保留并报告部分状态。恢复也验证当前指纹。备份可能含凭证，不自动清理，手工卸载路径见应用 README。

## 检测与证据

本地检查格式、组合、CLI存在/有界version、已安装入口、凭证是否可解析。它不验证登录、额度、
当前模型可调用或所有CLI版本兼容。真实检测需用户手动确认额度消耗，不读取业务仓库。
Qwen 复用正式 `review.py diff --channel qwen`，临时仓库有 HEAD 和小改动，输出在仓库外，
检查 completed、通道/执行器/协议/请求模型；Claude 分支精确匹配实际模型，Codex 分支保留 requested_only，并保留原完整读取和权限门禁。耗时可能最多30分钟，无快速探针或自动降级。
Codex 使用隔离临时合成仓库、提示词文件、DEVNULL、文件日志和输出上限，要求最终turn完成、
无error且返回文件里的随机值。实际模型只有结构化字段才显示，否则未知；请求ID或exit=0不是实际模型证明。
评审服务先选来源，再选模型：千问 Token Plan 提供 Qwen3.8-Max（qwen3.8-max）和 DeepSeek-V4-Pro（deepseek-v4-pro）；Qoder 固定 Kimi-K3（kmodel_latest，已验证 CLI 1.1.74）；Codex 保留四个目录模型和 CLI 默认；自定义 API 手填模型 ID。目录外模型保留“已有配置”。目录选项不是可调用性验证，DeepSeek 完整流程尚未验证。Qoder 仅 Codex 主模型可新选，Codex 服务仅 Claude 主模型可新选；既有其他路由保留显示。

API 服务显示服务地址和“认证 / API 密钥”，CLI 服务隐藏这些字段，使用现有 CLI 登录并默认显示“登录状态未检测”。高级设置说明固定执行器及协议：Claude 使用 Anthropic，Codex 使用 Responses；不承诺 Chat Completions。API 合成检测最多 30 分钟；Qoder 正式入口使用 600 秒、外围 660 秒、2MiB 输出上限，保留正式 runner 全部门禁和实际 Kimi-K3 证据校验。

两个主导工具共享当前 API 服务及其模型、凭证；两种协议地址独立。每个服务独立保留模型、地址、未保存输入及认证动作，切回恢复；同服务换模型不会改密钥。千问新草稿预填官方两种协议地址，自定义留空。取消放弃全部服务与主导工具草稿，保存失败保留草稿，保存仅影响下次调用。菜单显示服务与模型，未完成配置时引导设置，不保存空地址。

认证动作默认为保留；更换后输入非空新密钥，空输入仍保留；清除须显式选择并保存。密钥按服务写入 secrets.json 的 review_services.token_plan.value / review_services.custom_api.value，0600受限明文。清除删除该服务应用管理槽的值、清空其凭证路径并切换为独立键，防止外部旧密钥复活；不删除外部文件，也不删除 legacy tokenplan.value。首次加载只查文件 stat，不读取密钥正文，因此“发现凭证文件”不表示已配置或已登录。动作通过独立 secret_actions 请求传递，不写入公共 values、argv 或日志。

review_services（version=1、active_api、固定 token_plan/custom_api profiles）是 API 配置真源，plugins.qwen 为同次事务更新的兼容投影；入口仍为 channel=qwen。每个 profile 包含 model/base_url/responses_base_url/credential_paths/credential_key，不存明文 token。credential_key 仅允许 tokenplan.value、review_services.token_plan.value、review_services.custom_api.value；指定键缺失不回退，只有旧配置缺字段时使用 legacy。旧客户端不懂新槽，请使用配套源码；不承诺新配置兼容旧 runner，新密钥不复制到 legacy。

旧配置首次加载只在内存迁移：所有非空端点必须精确等于已知千问默认地址才归千问，否则（包括空端点、混合地址、尾斜杠）归自定义 API 并保留模型/地址/路径顺序。只有迁移到的一个 profile 继承旧凭证键及路径；另一个没有 legacy 凭证。首次保存才持久化。未知版本、字段错误或 profile/投影冲突拒绝写入。冲突错误只列字段名；修复时备份配置，然后按 review_services.active_api 对应 profile 将 model、base_url、responses_base_url、credential_paths、credential_key 同步到 plugins.qwen，保留其他配置和密钥，再重新读取；不要删除配置或密钥来绕过错误。


检测结果仅内存，记录时间/配置签名与凭证文件stat，变化后作废，重启归未检测。

| 模型 | 历史证据 |
|---|---|
| qwen3.8-max / gpt-6-astra | 已有方案/diff完整流程记录 |
| qwen3.7-max / deepseek-v4-pro | 仅端点实测 |
| Qoder Kimi-K3 | CLI 1.1.74 已有完整方案/diff证据；本次桌面路由只做离线验证 |
| 其他新 ID / GLM / Kimi 服务 | 未证实本项目完整流程 |

历史证据不是当前账号可用性或服务端实际路由保证；本应用的合成检测不是业务评审全量验证。

## 构建与边界

[构建及使用](../apps/macos/README.md)：原生 SwiftUI/AppKit，Python3.11标准库，无第三方依赖。
产物为本地 ad-hoc 签名 `.app`，Finder可启动，后台Python/CLI发现不依赖交互shell配置。
不会安装到用户目录、登录自启或自动更新。检测依赖有效 ai-anti 源码及已装CLI。
第三方 ModelPeer、CodexBar、CC Switch 只作模块调查参考，首期不复制其源码，不为复用改变需求。
不做业务评审启动、任务历史、余额订阅看板、通用网关、全面厂商接入；钥匙串迁移及版本兼容清单增强另评估。
离线fake CLI与prepare-only用例覆盖边界，不冒充真实模型实测；GUI与实际账号合成检测由独立验收记录。

设置字段按主模型、主模型 ID、实施模型 ID、协作模式、评审服务、评审模型排列；API 才显示地址与认证。执行器和协议位于高级设置。`values.host_updates` 保留原两组主导工具独立草稿语义和并发保护，服务 profile 与密钥动作同次事务保存。

空主模型保留已有默认值；空 Codex 评审模型使用 CLI 默认（真实检测需明确 ID）。两组主导工具的模型 ID/通道草稿独立。模式合法值 solo、auto、rules、force，旧 required 拒绝；用户当次指令、项目 .claude/relay-mode.local、全局默认、rules 按此顺序优先，solo 停用交叉评审及独立实施。
