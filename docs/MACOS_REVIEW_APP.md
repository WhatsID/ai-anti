# macOS 菜单栏配置与检测

用户确认的首期形态是菜单栏＋小设置窗口，评审业务继续在 Claude Code/Codex 里发起。
它围绕 ai-anti 的评审需求，不是通用模型代理或订阅管理器。主导工具与模型 ID 是两个字段。

## 首期功能

- 组合 Claude→Codex、Claude→Qwen、Codex→Qwen；已有 Codex→Claude 能力保留，不增加到首期新选列表。
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
| ai-anti `secrets.json` | 新密钥时更新 `tokenplan.value`，保留未知键、0600，并将用户级路径置首 |
| ai-anti `desktop/` | 进程间锁、上次保存私有备份；0700目录/0600文件 |

正常运行遵循 `CODEX_HOME` 与 `CLAUDE_CONFIG_DIR`。`--config-root` 测试根优先覆盖这些路径，
可解析凭证必须在该根内，不允许父目录跳转或意外 symlink。已有普通项目相对项保留，
桌面无项目上下文时跳过并提示未检查，业务项目可能优先使用；不按应用 cwd 解析或读取。已安装 skill symlink 仅用于只读来源发现。
损坏、空 JSON、错误类型、并发冲突或复杂 TOML 不覆盖。空主模型输入保留既有值；空独立评审模型表示CLI默认。
查看设置不读取凭据；“检查配置”才解析已有文件。凭证是已有执行器兼容的受限明文存储，不声称钥匙串。

模式优先级：当次明确用户指令 > 项目根 `.claude/relay-mode.local` > 全局 `relay.default_mode` > `rules`。
坏配置停止；`solo` 独立完成且同时停用交叉评审与独立实施。`auto` 自动判断但保留强制高风险，`rules` 按规模与高风险，
`force` 所有实质任务走流程。Codex 只在加载 codex-relay 的工作流中应用这些默认，不注册全会话规则。

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
AIAnti 设置窗口的评审执行器固定且不可选：Codex→API 使用 Codex CLI，Claude→API 使用 Claude Code CLI，Claude→Codex 使用 Codex CLI。旧 Codex→Claude CLI 配置加载或切入时会生成待保存草稿；保存前不能检测，取消草稿后若仍处于该旧 Codex 配置也保留迁移提示，未保存不改磁盘。Anthropic `base_url` 与 Responses `responses_base_url` 独立保存，界面仅显示当前有效协议地址，不推导或替换域名；使用所选模型对应入口与密钥，DeepSeek 完整流程尚未验证。Codex 检测只确认请求模型，服务端实际模型未知。API 合成检测预算为运行1800秒、空闲900秒、输出8MiB，外围1860秒、界面1900秒留清理余量，可取消。

检测结果仅内存，记录时间/配置签名与凭证文件stat，变化后作废，重启归未检测。

| 模型 | 历史证据 |
|---|---|
| qwen3.8-max / gpt-6-astra | 已有方案/diff完整流程记录 |
| qwen3.7-max / deepseek-v4-pro | 仅端点实测 |
| 新 ID / GLM / Kimi | 未证实本项目完整流程 |

历史证据不是当前账号可用性或服务端实际路由保证；本应用的合成检测不是业务评审全量验证。

## 构建与边界

[构建及使用](../apps/macos/README.md)：原生 SwiftUI/AppKit，Python3.11标准库，无第三方依赖。
产物为本地 ad-hoc 签名 `.app`，Finder可启动，后台Python/CLI发现不依赖交互shell配置。
不会安装到用户目录、登录自启或自动更新。检测依赖有效 ai-anti 源码及已装CLI。
第三方 ModelPeer、CodexBar、CC Switch 只作模块调查参考，首期不复制其源码，不为复用改变需求。
不做业务评审启动、任务历史、余额订阅看板、通用网关、全面厂商接入；钥匙串迁移及版本兼容清单增强另评估。
离线fake CLI与prepare-only用例覆盖边界，不冒充真实模型实测；GUI与实际账号合成检测由独立验收记录。

设置统一字段顺序，协作模式位于实施模型 ID 下；评审 ID 仅一组，固定 CLI 执行器同样显示。无 API 时地址、密钥保留行并禁用。`values.host_updates` 可选对象仅接受 claude/codex 的 primary_model/channel，和当前字段冲突拒绝；各目标先验证再单次事务写入，未改通道与模型文件保留。未主动修改的旧通道不阻止仅改模型 ID。
