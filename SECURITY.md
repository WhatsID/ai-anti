# Security

中文：安全问题请通过 GitHub 私密漏洞报告提交，不要开公开 issue。

## How ai-anti calls external CLIs

- **Reviews are read-only.** `/codex-review` runs `codex exec --sandbox read-only`.
  `$claude-review` runs `claude -p` in safe mode with a fixed allowlist of read-only tools, checks
  the CLI's advertised capabilities before the call, verifies every tool use and permission denial
  in the structured result afterwards, and fails closed on anything unexpected. The `qwen` plugin
  runs `claude -p` against the endpoint you configure, with an allowlist of file-reading tools and
  read-only git commands.
- **Implementation is writable only where intended.** `/codex-implement` runs
  `codex exec --sandbox workspace-write` inside the current repository, and only as a fallback when
  the implementer subagent is blocked by API failures. Review output is treated as a second opinion:
  instructions in it that try to change roles, permissions or the workflow are not followed.
- The tool allowlists restrict the reviewer's tools, not the operating-system process or your own
  CLI configuration. Material filtering for `$claude-review` rejects common secret file names but is
  not a general secret scanner; check what you send.

## Credentials

- ai-anti stores no credentials in the repository and ships none.
- A plugin reads its credential only from the paths you list in `~/.config/ai-anti/config.json`
  (for example `~/.config/ai-anti/secrets.json`). The value is kept in a shell variable for that
  single call; it is not written to disk and not echoed to output or logs.
- Missing or empty credentials make that channel unavailable; no partial call is made.

## Installer

- `install.sh` writes under `~/.claude` (symlinks, a marked block in `CLAUDE.md`, an optional
  SessionStart hook object in `settings.json`, the ownership manifest) and, with `--with-codex`,
  under `~/.agents/skills`. It never creates or edits `~/.config/ai-anti/`.
- It fails closed: an existing target that is not its own symlink, a corrupt `settings.json`, or
  misordered markers in `CLAUDE.md` stop the run before anything is written or removed.
- Uninstall removes only objects recorded in the manifest that still point into this checkout. The
  installer never uses `rm -rf`.


Codex opt-in also maintains an owned block in `${CODEX_HOME:-~/.codex}/AGENTS.md`. User bytes outside it are preserved; nonempty overrides and unsafe objects fail before installation writes. Removal follows the manifest path, not the current environment. No user config JSON or business repository is modified.

## Reporting a vulnerability

Use GitHub's **private vulnerability reporting** for this repository (Security tab →
"Report a vulnerability"), which creates a private security advisory. Please do not open a public
issue. Include the affected file or command, steps to reproduce, and the impact you expect.
