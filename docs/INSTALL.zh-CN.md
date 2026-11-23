# 安装细节与边界

[README](../README.zh-CN.md#工作原理) 讲了安装器做什么、规则如何生效；本文补充各种边界情况。
第一次使用请先看 [最小上手指南](QUICKSTART.zh-CN.md)。

## 安装器的所有权规则

- **逐文件托管符号链接**：链接的源始终是本仓库里的文件或目录，不做整目录链接。
- **只维护自己的标记块**：`~/.claude/CLAUDE.md` 与 `${CODEX_HOME:-~/.codex}/AGENTS.md` 里只改
  `ai-anti` 标记块，块外的用户内容按字节保留。
- **所有权 manifest**：每个成功安装的对象都记入 `~/.claude/ai-anti.manifest.json`；卸载只移除登记过、
  且仍指向本仓库的对象。
- **fail-closed**：目标已存在、但不属于本工具（不是指向本仓库的符号链接）时报错停止，不覆盖、不合并。
- **幂等**：重复执行结果一致。普通重装会继承已安装的可选组件，并升级旧 manifest。
- **不改用户数据**：用户配置 JSON 和业务仓库都不会被改写。只有在文件由工具创建、移除后为空时，
  才会删除该文件。

## Codex 入口

`./install.sh --with-codex` 同时安装技能（`claude-review`、`codex-relay`、`bug-review`）和
`${CODEX_HOME:-~/.codex}/AGENTS.md` 中的自有标记块。该块显式读取 `adapters/codex/AGENTS.global.md`。

- 非空的 `AGENTS.override.md` 会遮蔽入口，安装器拒绝安装，也不会自动改写它。
- `AGENTS.md`、override 或路径祖先中的不安全对象、损坏的标记，同样会在写入前被拒绝。
- 更换 `CODEX_HOME` 前，先运行 `./install.sh --without-codex`：按 manifest 记录的旧路径移除自有块，
  同时移除 Codex 技能。再在新环境里执行 `--with-codex`。

## AGY 插件

`./install.sh --with-agy` 把插件链接到 `~/.gemini/config/plugins/ai-anti`，装完后启动新的 AGY 会话。

- 插件加载共享工作流入口；`agy-relay` 跑完整接力，`codex-review` 单独审方案。
- 在 `~/.config/ai-anti/config.json` 中独立设置 `agy_relay.review` 与 `agy_relay.implementation`，
  超时仍共用 `review.timeout` 和 `review.idle_timeout`。实施必须显式指定 Codex 模型，失败即停，
  请用户裁决。
- 启用 `agy_relay.dual_plan.enabled` 后，可以让 Claude 独立起草，再由 AGY 或 Claude 综合两稿。
- `./install.sh --without-agy` 收回托管链接，以及由安装器创建的空目录。
- 验证边界见 [AGY 兼容性](COMPATIBILITY.md#2026-11-20-agy-led-relay)。

## 体检：`--status`

- 普通 `--status` 检查约定的共享源与已安装路由，不解析 relay/defaults 或完整用户配置。
- 对插件只检查托管链接，不检查 metadata、凭证和通道联网。
- 退出码 0 表示报告完成，不等于所有检查都健康，也不能证明模型会遵循规则或完整接力已执行；
  参数错误仍会非零退出。外部 CLI 的版本命令可能会产生该 CLI 自己的缓存。
- `./install.sh --status --project DIR` 可选地诊断显式给定的项目，且不写入项目。诊断内容：
  - Git 根，以及当前有效的 HEAD 提交基线（任意分支或 detached HEAD 均可）；
  - 项目入口文档是否可读；
  - 协作模式；
  - 全局部署规模的 fallback。

  入口或参数缺失只是提示，不代表安装损坏；项目正文里的覆写仍需由 AI 自己读取。不加 `--project` 时，
  不扫描任何业务目录。

## 移动仓库与更新

- 链接与导入都使用绝对路径。移动仓库位置后，重跑一次 `./install.sh` 刷新路径。
- 因为是符号链接，`git pull` 更新本仓库后，新开的会话就会读到新规则，无需重装。
- 入口只在新会话里读取；已开着的会话不会自动重载。

## 按需加载的共享方法

以下方法文件不需要单独安装，也不要求每个项目复制，通过已有的源码链接传播。主模型只在适用时读取，
不改变模式、权限、评审轮次和分诊规则。

| 文件 | 何时读取 |
|------|----------|
| `core/PLANNING.md` | 需求需要澄清、编制方案或切分可验证的行为切片时。明确的小任务不要求完整计划 |
| `core/DESIGN.md` | 调整模块职责、接口、依赖边界，或跨调用方的业务规则出现重复时。文案、小样式不触发完整设计 |
| `core/DEBUGGING.md` | 排障与根因取证 |
| `core/TESTING.md` | 行为验证与验收。不把所有任务都变成 TDD |
| `core/RETRO.md` | 用户要求复盘、维护规则，或出现有证据的重复协作问题时。先分类证据，优先修现有的确定性检查，项目经验留在项目里。普通成功任务不自动产出报告 |

直接做、solo 和接力都按适用性读取这些方法。派给实施者时，会给出明确的源文件路径，主模型再独立核对
实施者提交的证据。行为验证只覆盖记录中的案例，不代表跨项目全场景都已验收。

## 卸载

```bash
./install.sh --uninstall
```

只移除 manifest 登记、且仍指向本仓库的对象，以及 `~/.claude/CLAUDE.md` 与 Codex `AGENTS.md` 里
自己的标记块；你自己的文件与 hook 不受影响。如果只想移除某一部分，分别用 `--without-hook`、
`--without-codex`、`--without-agy`、`--remove-plugin <name>`。
