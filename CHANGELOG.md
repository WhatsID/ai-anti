# Changelog

## 0.1.0 — Unreleased

First public release.

- 设置字段改为“评审模型秘钥”；API 执行器固定跟随主模型，Claude→Codex 保留 Codex CLI，旧 Codex 执行器草稿须保存后生效。
- 设置统一三种主模型/评审组合布局，协作模式位于实施模型 ID 下；允许来回切换保留两组草稿，统一事务保存并保护菜单入口。

- Qwen 评审新增可选 Codex CLI Responses 执行器，保留默认 Claude CLI 与独立 Anthropic 地址。
- 新增编号材料完整性与 Codex JSONL 校验，显式 medium；模型证据为 requested_only，不宣称实际服务端身份。
- 显式 idle 上限支持900秒，默认仍300秒；900/1800秒预算下正式两阶段方案评审及33块真实增量评审通过，81项适配测试通过。
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
- **CLI compatibility**: `compat.json` lists CLI versions verified end to end; `install.sh --status`
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
