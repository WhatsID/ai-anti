# AIAnti 菜单栏应用

首期为本机菜单栏与小设置窗口，管理配置和检测。业务评审继续从 Claude Code / Codex 发起。
支持 Claude→Codex、Claude→Qwen、Codex→Qwen；既有 Codex→Claude 配置可读取，不加入首期新选列表。

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
Qwen 可能需要 10–20 分钟，显示耗时并支持取消。只用临时合成仓库，不读取业务材料，不重试、不降级。
本地检查不证明登录、额度、模型可调用或版本完整兼容；真实成功也只证明当次合成检测。
Qwen 使用现有正式 `review.py diff --channel qwen` 全部门禁；Codex 需要支持隔离检测参数的新版本，
验证 JSON 最终 turn 与随机合成文件读取得到的值，不能只凭退出码为零。实际模型没有结构化证据则标“未知”。
检测结果只保存在内存；重启归未检测，配置/端点/凭证文件 stat 改变后失效。

保存只改用户默认值，当前会话不会切换；项目模式、命令行、会话覆盖仍可能优先。
主模型选项决定编辑哪组默认配置，不切换当前会话。项目模式优先于全局默认；solo 同时关闭交叉评审及独立实施。
Codex 全局模式仅在加载 codex-relay 的工作流中生效。Claude 首选通道放首位，其他回退档位保留顺序。
空主模型表示保留已有值，保存后以回读值显示；空 Codex 评审模型恢复 CLI 默认（真实检测需明确 ID）。
已有主/实施模型不自动替换，用户可编辑任意合法 ID；新 ID 显示未验证。

密钥输入留空保留现状。新密钥写 `~/.config/ai-anti/secrets.json` 的 `tokenplan.value`，0600受限明文，
不是钥匙串；原其他键保留，用户级凭证路径提升到首位。桌面仅检查 `~/` 或绝对凭证路径，拒绝 `..` 及意外 symlink。
保留已有普通项目相对项与顺序，但无项目上下文时不解析、不读取，并提示业务项目可能优先使用；
合成检测仓库没有这些项目文件，不会复制业务凭证。URL 拒绝 userinfo、查询或片段，
不回显含凭证的旧地址。密钥只通过后台 stdin 输入，不进入 argv、日志、响应。

保存前重读指纹，与加载快照不符则要求重新读取配置；进程间锁、同目录原子替换、私有备份、最终回读。
多文件失败仅回滚仍等于本次输出的文件，遇外部修改报告部分状态；不宣称与外部编辑器构成强事务。
“恢复上次保存”同样检查指纹，不能覆盖后来手工修改。备份和锁位于
`~/.config/ai-anti/desktop/`（目录0700、文件0600），备份可能含敏感配置，应按密钥保管。
不自动清理备份；手工卸载只移除应用，若不再需要恢复可手动删除 desktop 目录；不要误删原配置或 secrets。

## 离线测试

AppModel 状态验收使用可手动完成请求的 fake client，不调用 Python 或模型。独立编译并运行：

```bash
mkdir -p /private/tmp/ai-anti-app-checks
swiftc -parse-as-library -module-cache-path /private/tmp/ai-anti-app-checks/module-cache \
  apps/macos/Sources/AIReviewBar/AppModel.swift \
  apps/macos/Sources/AIReviewBar/Bridge.swift apps/macos/AppModelChecks.swift \
  -o /private/tmp/ai-anti-app-checks/AppModelChecks
/private/tmp/ai-anti-app-checks/AppModelChecks
```

覆盖静默检查不发布 UI、单请求门禁、前台优先、过期成功/失败、草稿保护、保存回读、取消与退出清理。


```bash
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.11 -m unittest discover -s apps/macos/backend/tests -v
PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/python3.11 -m unittest discover -s tests -p 'test_desktop_contract.py' -v
```

测试只使用临时 fixture 和 fake CLI，并包含正式入口 prepare-only 契约，不代表线上模型实测。
产品范围、历史证据及后续边界见 [产品说明](../../docs/MACOS_REVIEW_APP.md)。
