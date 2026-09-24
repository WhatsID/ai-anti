# ai-anti

English | [简体中文](README.zh-CN.md)

Adversarial cross-model review for AI coding agents. Install once, then use it from Claude Code
or Codex in any repository.

## What it is and why

When one model plans, writes and checks its own work, its blind spots travel all the way to the
result. ai-anti splits that chain across roles with different stances:

1. **Plan** — the primary model writes a plan file and keeps the user's original request verbatim.
2. **Cross review** — a *different* model reads the repository (read-only) and reviews the plan
   against the original request, from the stance "someone else wrote this; find what will cause
   rework".
3. **Triage** — the primary model rules on every finding: must-fix, register for later, or reject.
   This keeps review rounds from exploding.
4. **Implement** — a separate implementer carries out the settled plan.
5. **Final check** — the primary model reviews the whole increment against the plan and reports.

The name comes from **a**dversarial: the point is not more models doing more work, but a second
model with a different stance looking for the mistakes the first one cannot see.

## Two main paths

| Direction | Entry points | What happens |
|-----------|--------------|--------------|
| Claude → Codex | `/codex-review plan <plan.md>`, `/codex-review diff`, `/codex-implement <plan.md>` | Claude Code leads; Codex reviews in a read-only sandbox, or takes over implementation when the implementer subagent is blocked by API failures |
| Codex → Claude | `$claude-review`, `$codex-relay` | Codex leads; Claude reviews a plan (two stages) or a scoped diff under fail-closed checks; `$codex-relay` runs the full relay |

In Claude Code the relay runs automatically for large or high-risk tasks; `/relay-mode` switches
between `solo`, `required` (default), `auto` and `force`.

## Quick start

Prerequisites: macOS, [Claude Code](https://docs.anthropic.com/en/docs/claude-code),
[Codex CLI](https://github.com/openai/codex), Python 3.9+.

```bash
git clone https://github.com/WhatsID/ai-anti ~/ai-anti
cd ~/ai-anti
./install.sh                 # Claude Code side: commands, subagents, CLAUDE.md import
./install.sh --with-codex    # optional: Codex skills into ~/.agents/skills
./install.sh --status        # read-only health check
```

Then open Claude Code or Codex in any repository. Nothing needs to be copied into your projects;
the workflow is loaded from your user-level `~/.claude/CLAUDE.md`. Projects may override
parameters (high-risk triggers, where to register findings, deployment scale, ...) in their own
`CLAUDE.md`; see the override table in `adapters/claude-code/CLAUDE.global.md`.

Codex → Claude examples:

```text
Use $claude-review to review the plan .claude/plans/20260101-task.md
Use $codex-relay to complete the following task: ...
```

`./install.sh --with-hook` additionally adds a SessionStart hook that announces the current
collaboration mode. Links and imports are absolute paths: after moving the checkout, run
`./install.sh` again.

## Plugins

Two review channels are built in: `codex` (`/codex-review`) and `same-family` (`/opus-review`,
a multi-perspective review by Claude subagents). Other channels are plugins under `plugins/<name>/`.
Install one with `./install.sh --with-plugin <name>` and add its name to `review.channels` in your
config. The first plugin is `qwen` (an Anthropic-compatible endpoint driven through `claude -p`).
The contract for writing a plugin is in [docs/PLUGINS.md](docs/PLUGINS.md).

## Configuration

Optional user config: `~/.config/ai-anti/config.json` (template:
[config.example.json](config.example.json)). The installer never creates or edits it. Missing file
or keys mean built-in defaults; a file that is not valid JSON stops the workflow instead of
silently falling back.

| Key | Meaning | Default |
|-----|---------|---------|
| `review.channels` | Review channel order | `["codex", "same-family"]` |
| `defaults.deployment_scale` | Deployment scale passed to reviewers when a project does not declare one | unset → reviewers are told "deployment scale not declared" |
| `claude_review.model_families` | `--model` values accepted by `$claude-review` and the regex their actual model IDs must fully match | `opus`, `fable` |
| `codex_relay.secondary_model` | Model requested for the implementer in `$codex-relay` | unset → specify per task |
| `plugins.<name>` | Plugin settings, e.g. qwen `model`, `base_url`, `credential_paths` | none |

Credentials live outside the repository, e.g. `~/.config/ai-anti/secrets.json`; plugins only read
the paths you configure.

## Compatibility

Versions verified end to end are listed in [compat.json](compat.json) and explained in
[docs/COMPATIBILITY.md](docs/COMPATIBILITY.md). Other versions produce a warning, not a failure;
the fail-closed checks still decide. Only macOS has been verified.

## Cost

Every review and implementation call runs on your own accounts and consumes the corresponding
service quota (Claude, Codex, or a plugin endpoint). A plan review through `$claude-review` makes
two model calls per round; the workflow caps review at two rounds.

## Uninstall

```bash
./install.sh --uninstall
```

Removes only what the manifest (`~/.claude/ai-anti.manifest.json`) records and still points into
this checkout, plus its own marker block in `~/.claude/CLAUDE.md`. Your own files and hooks are left
untouched. Individual parts: `--without-hook`, `--without-codex`, `--remove-plugin <name>`.

## Security

See [SECURITY.md](SECURITY.md): how external CLIs are sandboxed, how credentials are read, what the
installer writes, and how to report a vulnerability.

## Language and mechanism overview

The rule texts (`core/`, `adapters/claude-code/CLAUDE.global.md`, command templates) are written
in Chinese and are the only authority; they are loaded by the models, which read Chinese fine.
This overview is a summary, not a translation.

- **Five-step relay.** Plan (with the user's request kept verbatim in a sibling `.request.md`) →
  cross-model review → triage → implementation by a separate implementer → final check of the
  increment by the primary model, then a report. Before implementation, every user-visible
  difference between the settled plan and the original request is confirmed with the user.
- **Triage.** Each finding must name a concrete failure scenario (input or state → wrong result).
  *Must-fix* only if it causes data loss or irreversible damage, breaks the core function of the
  current request, or lets unauthorized people get data or access. Everything else is
  *registered* in a ledger and counts as closed. Findings without a concrete scenario are
  *rejected* with a reason.
- **Two-round limit.** Adopting must-fix items does not trigger another review. A second round
  happens only if the architecture is overturned or a blocker-level new fact appears. After two
  rounds, any unresolved must-fix item goes to the user.
- **Fallback order.** Channels are tried in the configured order (default Codex → same-family).
  Each channel reports only its own result; the primary model moves to the next one. A channel
  whose quota is exhausted is not retried in the same session. If every channel is unavailable,
  ordinary tasks continue marked "not cross-reviewed", while high-risk tasks stop for the user.
- **Same-family compensation.** Review by the same model family is a restricted exception, used
  only when every cross-model channel is unavailable, and only counts if it has all of: empty
  context, a fixed adversarial stance, 3–5 distinct perspectives, its own read-only probes, and an
  independent rebuttal of every blocker.

`core/DESIGN_RATIONALE.md` records the real failure behind each rule.

## Repository layout

```
core/                     shared review principles, CLI discipline, rule rationale
adapters/claude-code/     Claude Code workflow, commands, subagents, hook
adapters/codex/skills/    claude-review and codex-relay skills for Codex
adapters/codex/tests/     offline tests for the Codex skills
plugins/                  review-channel plugins (qwen)
scripts/                  release hygiene and public export
tests/                    installer tests
install.sh                installer (bash 3.2, no dependencies beyond python3)
```

## License

[Apache-2.0](LICENSE)
