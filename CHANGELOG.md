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
