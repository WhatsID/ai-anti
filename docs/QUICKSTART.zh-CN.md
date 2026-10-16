# 最小上手指南

目标：安装一次，让已有项目和以后新开的项目都能读取共享工作流，再完成一个可核对的小任务。
本指南面向 macOS；基础脚本需要 Git、Python 3.9+。桌面应用可选，不是前置条件。
已验版本和各路径限制见 [兼容性说明](COMPATIBILITY.md)，材料外发与本地保留见 [安全说明](../SECURITY.md)。

## 1. 选择主导工具

| 你平时使用 | 安装选项 | 需要交叉评审时 |
|---|---|---|
| Claude Code | 默认安装 | 默认使用 Codex CLI，需相应工具与账号可用 |
| Codex | `--with-codex` | 默认使用 Claude CLI；也可配置 Qwen |
| 两种工具都使用 | `--with-codex` | 按当前主导工具选择各自评审通道 |

小任务不一定触发评审。先安装入口、确认能读取，再接通所选评审通道。
只有 Codex 且不使用 Claude 登录时，可选 Qwen Responses：需要 API 凭证和匹配的 CLI，
当前执行器精确要求 Codex CLI 0.160.0。配置步骤见 [Codex 主导路径](../README.zh-CN.md#codex-主导的路径)。
该路径无需安装 Qwen 插件；Claude 主导使用 Qwen 时才按 [插件接入示例](../README.zh-CN.md#接入示例qwen-插件) 安装。

## 2. 安装一次

将仓库放在准备长期保留的位置；安装后的链接指向这里。

```bash
git clone https://github.com/WhatsID/ai-anti ~/ai-anti
cd ~/ai-anti
./install.sh
```

使用 Codex 时，再运行：

```bash
./install.sh --with-codex
```

安装器维护 Claude 用户级入口；Codex 选项另外维护 `${CODEX_HOME:-~/.codex}/AGENTS.md`
和 `~/.agents/skills`。不需要在每个项目复制这套文件，也不会自动创建用户配置。
已有项目的业务约定继续留在项目内。移动 ai-anti 源码目录后，在新位置重跑安装。

默认模式为 `rules`：小任务直接做，重大任务和高风险改动走接力。
首次检查入口不必创建配置。需要更改默认值时，按 [配置说明](../README.zh-CN.md#配置)
编辑 `~/.config/ai-anti/config.json`，只合并需要的键，保留已有配置。
[完整示例](../config.example.json) 含端点和模型占位符，不能原样当作已接通的 API 配置。

## 3. 确认安装与入口

```bash
cd ~/ai-anti
./install.sh --status
```

查看托管链接、入口标记和 CLI 版本的逐项结果。退出码 0 只表示报告完成；
“全部正常”不证明登录、额度、API 凭证或模型可调用。
还可检查一个项目（将路径替换为实际项目路径）：

```bash
./install.sh --status --project /absolute/path/to/your-project
```

空目录缺少 Git 基线或项目入口是提示，不代表安装损坏；完整接力需要 Git 和 local main 基线。
接着在自己的项目中打开一个**新的** Claude Code 或 Codex 会话，发送：

> 只检查，不修改文件或调用外部评审。请实际读取 ai-anti 用户级入口和共享工作流，
> 列出读取的源路径、当前协作模式及来源，说明这个项目目前是否具备完整接力的前提。
> 读不到或无法确认的项目请明确标为未验证。

检查实际读取的工具记录，不只看模型声称“已加载”。应能看到对应的
`adapters/claude-code/CLAUDE.global.md` 或 `adapters/codex/AGENTS.global.md`，
以及 `core/WORKFLOW_ENTRY.md`。这一步只检查入口发现，不等于完整接力验收。

## 4. 完成第一个小任务

选择非生产项目中的一处明确文案错误，例如：

> 请修正 README 中“安装步骤”的一个错别字。先查看 Git 状态，只修改这一处，
> 保留其他未提交改动，完成后展示 diff；不要提交或推送。

确认实际改动符合范围、模型说明了如何验证。默认 `rules` 下，这类小任务通常不需要跨模型评审。
以后进入其他项目或新目录，继续使用用户级入口，不必重新建立协作设施；项目特有目标和约束仍需补充。

要验证完整接力，再选一个已授权、可回滚的小型代码任务，并接通评审通道。
Codex 中可明确发送 `使用 $codex-relay 完成以下任务：……`，同时指定账号可用的独立实施模型，
或预先设置 `codex_relay.secondary_model`。主工具还必须支持原生独立代理、显式模型选择及
可观察运行记录；只有 CLI 可执行文件不保证具备这些能力。Claude 主导则在需求中明确要求走接力。

完整接力应能提供方案与需求原文、评审及逐条裁决、独立实施和主模型验收证据。
缺少其中一项就不能算完整通过。评审会外发材料并可能消耗额度，开始前确认所选通道与材料范围。
历史已验路径不保证你的账号可用；Claude 普通登录及 DeepSeek 完整流程仍有待验边界。

## 5. 遇到问题

| 现象 | 下一步 |
|---|---|
| CLI 未安装或版本不匹配 | 按所选路径准备工具，对照兼容性说明；不要放宽门禁来跳过错误 |
| 新会话没有读取入口 | 查 status 的链接和标记；确认源码未移动、启动时的 CODEX_HOME / CLAUDE_CONFIG_DIR 与安装时一致 |
| 安装因 override、已有对象或坏 JSON 停止 | 按具体错误核对冲突，先保留自己的文件，不直接覆盖 |
| `credential unavailable` | 核对配置的凭证路径及文件中的 `tokenplan.value`；不要把密钥贴进 issue |
| 登录过期或额度不足 | 在对应工具中恢复账号可用性，再重试；status 不能代替登录检查 |
| `truncated or malformed stream-json` | 查看本次输出目录的 `status.json` 和原始 `logs/`；可能是 CLI 错误文本，不能只据摘要归因 |
| 缺少独立实施模型或代理能力 | 配置可用模型并确认宿主能力，按实际缺口处理，不将单模型执行当成完整接力 |

分享日志前检查敏感内容。安装、凭证缺失与卸载有隔离验收；登录失败提示仅做过离线模拟，
并非所有真实客户端错误都已验证。详细记录见 [兼容性说明](COMPATIBILITY.md)。

## 6. 卸载

在保留的源码目录执行：

```bash
cd ~/ai-anti
./install.sh --uninstall
```

卸载按所有权记录移除本工具入口和链接，保留用户配置。
它不会自动删除 API 密钥、桌面恢复备份或评审输出目录；这些内容按 [安全说明](../SECURITY.md) 自行决定保留范围。
