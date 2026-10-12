# Changelog

## 0.1.0 — Unreleased

First public release.

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
