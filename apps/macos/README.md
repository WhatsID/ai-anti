# AIAnti 菜单栏应用

首期为本机菜单栏与小设置窗口，管理配置和检测。业务评审继续从 Claude Code / Codex 发起。
配置范围为 Claude→Codex、Claude→Qwen/DeepSeek、Codex→Qwen/DeepSeek；既有 Codex→Claude 配置可读取，不加入首期新选列表。
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
Claude/Codex CLI 的发现同样覆盖 `/opt/homebrew/bin` 和 `/usr/local/bin`。

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

AIAnti 设置窗口的评审执行器固定且不可选：Codex→API 使用 Codex CLI，Claude→API 使用 Claude Code CLI，Claude→Codex 使用 Codex CLI。旧 Codex→Claude CLI 配置加载或切入时会生成待保存草稿；保存前不能检测，取消草稿后若仍处于该旧 Codex 配置也保留迁移提示，未保存不改磁盘。Anthropic `base_url` 与 Responses `responses_base_url` 独立保存，界面仅显示当前有效协议地址，不推导或替换域名；使用所选模型对应入口与密钥，DeepSeek 完整流程尚未验证。Codex 检测只确认请求模型，服务端实际模型未知。API 合成检测预算为运行1800秒、空闲900秒、输出8MiB，外围1860秒、界面1900秒留清理余量，可取消。

保存只改用户默认值，当前会话不会切换；项目模式、命令行、会话覆盖仍可能优先。
主模型选项决定编辑哪组默认配置，不切换当前会话。可在未保存时来回切换，两组主模型 ID 和评审通道各自保留草稿；API 模型、地址、密钥及协作模式等仍共享。一次保存统一写入两组实际改过的字段，失败保留全部草稿；菜单快捷切换仍要求先保存或放弃草稿。项目模式优先于全局默认；solo 同时关闭交叉评审及独立实施。
仅接受 `solo`、`auto`、`rules`、`force`；旧值 `required` 无效，不作兼容。

Codex 用户级共享入口读取全局模式并负责分流，需要接力时才加载 codex-relay。Claude 首选通道放首位，其他回退档位保留顺序。
空主模型表示保留已有值，保存后以回读值显示；空 Codex 评审模型恢复 CLI 默认（真实检测需明确 ID）。
主模型 ID、实施模型 ID、评审模型 ID 使用固定目录下拉；主模型可保留当前默认值，Codex 评审可选 CLI 默认，实施模型可选未指定；API 模型必须选择 ID。
已有目录外 ID 作为“已有配置”只读选项保留，切换家族再回来仍可选回加载原值。
目录包含 Claude、Codex、Qwen 与 DeepSeek，选项不代表可调用性验证。DeepSeek 复用现有兼容 API 执行器，
持久化仍为 `channel=qwen` 与 `plugins.qwen.model`，没有新增配置字段。服务地址与密钥不会自动改变，
须使用所选模型所属入口的当前协议兼容地址；菜单切换后提示确认支持情况。DeepSeek 尚未进行实际外部调用测试。
字段固定按主模型、主模型 ID、实施模型 ID、协作模式、评审模型及 ID、执行器、服务地址和密钥排列；固定执行器也显示，无 API 时地址和密钥禁用并标明不适用。

“取消”放弃两组全部草稿；检测中的“取消检测”取消检测进程。

密钥输入留空保留现状。新密钥写 `~/.config/ai-anti/secrets.json` 的 `tokenplan.value`，0600受限明文，
不是钥匙串；原其他键保留，用户级凭证路径提升到首位。桌面仅检查 `~/` 或绝对凭证路径，拒绝 `..` 及意外 symlink。
保留已有普通项目相对项与顺序，但无项目上下文时不解析、不读取，并提示业务项目可能优先使用；
合成检测仓库没有这些项目文件，不会复制业务凭证。URL 拒绝 userinfo、查询或片段，
不回显含凭证的旧地址。密钥只通过后台 stdin 输入，不进入 argv、日志、响应。

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
  apps/macos/Sources/AIReviewBar/ModelCatalog.swift \
  apps/macos/Sources/AIReviewBar/AppModel.swift \
  apps/macos/Sources/AIReviewBar/Bridge.swift apps/macos/AppModelChecks.swift \
  -o /private/tmp/ai-anti-app-checks/AppModelChecks
/private/tmp/ai-anti-app-checks/AppModelChecks
```

覆盖模型目录、已保存未知 ID 保留、API 家族切换与保存回读、静默检查不发布 UI、单请求门禁、前台优先、过期成功/失败、草稿保护、保存回读、取消与退出清理。

```bash
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.11 -m unittest discover -s apps/macos/backend/tests -v
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.11 -m unittest discover -s tests -p 'test_desktop_contract.py' -v
```

测试只使用临时 fixture 和 fake CLI，并包含正式入口 prepare-only 契约，不代表线上模型实测。
产品范围、历史证据及后续边界见 [产品说明](../../docs/MACOS_REVIEW_APP.md)。
