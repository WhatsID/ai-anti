# Changelog

## 0.1.0 — Unreleased

First public release.

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

- Codex 评审入口增加显式 Claude/Qwen 通道及用户默认配置，保留 Claude CLI 与旧 Claude 行为。
- Qwen 使用子进程环境隔离、精确模型/提示校验；默认每阶段 1800 秒，prepare-only 不读凭证。
- 增加离线通道回归测试；正式新入口在独立临时 Git 小仓库的真实 Qwen plan 两阶段与 scoped diff 验证通过。

## 用户级入口

- 两端共享 `core/WORKFLOW_ENTRY.md`；Codex opt-in 增加用户级显式读取入口。
- manifest v3 支持入口生命周期、字节保全与 fail-closed 预检，保留旧 manifest 升级。

## 共享调查与验证方法

- 新增按需共享 DEBUGGING / TESTING，直接做与 solo 同样可用。
- 两端方案、实施派发、容灾及主模型验收引用同源方法，不新增安装对象。
- 明确行为失败信号、候选来源、反证与未覆盖边界，不改变权限或评审裁决。

## 安装体检与项目诊断

- 共享源与托管路由完整性检查，安装前缺源停止，status 汇总缺口。
- 可选 `--status --project DIR` 只读诊断项目根、基线、模式与参数 fallback。
- 保持报告式 exit0，不自动初始化新项目或调用评审通道。

## 共享规划方法

- 新增按需 PLANNING：有限需求澄清、可验收方案与行为切片，供负责规划的主模型读取。
- 两工具接入同源方法，保留 DEBUGGING/TESTING 实施派发，不要求实施者重新规划。
- 安装与 status 共同检查 PLANNING 必要源；缺源安装在写入前失败，status 保持报告式退出。
- 不新增技能安装对象、每仓初始化、工单或代理调度；运行遵循仍需新会话证据。

## 共享模块设计方法

- 新增按需 DESIGN，供设计/规划主模型核对目标、职责、调用契约与依赖退出方向。
- 两工具与共享入口路由，独立实施者执行定稿契约，不重新设计。
- 安装及只读 status 检查 DESIGN 必要源，缺源仍可卸载；不新增安装对象或权限。
- 真实长期设计效果与 Claude 普通登录验收继续待取证，不将用户顺利反馈记为正式通过。

## 2026-10-16｜共享复盘与规则维护方法

- 新增按需 RETRO：证据分类、最小改进、规则归属与维护，不自动扩大授权或每任务复盘。
- 共用入口接入两工具；安装预检与只读 status 检查必要源，缺源仍可卸载。
- 新会话遵循与长期效果分开验证，Claude 普通登录及真实跨项目效果继续待验。
