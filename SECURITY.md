# Security

中文概要：评审会向所配置的服务发送材料；只读不等于数据不出机器，也不等于操作系统隔离。
本地证据与桌面恢复备份可能含敏感内容，分享前须人工检查。漏洞请优先私密报告，具体入口见末节。

## Components and trust boundaries

ai-anti is a local, single-user CLI workflow with an optional macOS configuration app.
It does not operate a server or supply credentials. The following paths have different controls:

- The formal `review.py` runner freezes selected materials before invoking a reviewer.
  The Claude executor checks advertised capabilities, constrains tools and validates the
  structured protocol, material reads and permission failures. Qwen uses numbered frozen
  materials with read verification; this is not a general repository browsing permission.
- Qwen through Codex Responses is separately pinned to Codex CLI 0.160.0. It validates
  material-tool events and rejects non-material tool execution. Global AGENTS, system skill
  directories and advertised general tools may still be present. Rejection of those events
  does not prove an empty context or complete OS isolation. Codex JSONL does not establish
  the provider's actual model: evidence remains `actual_model=null`, `requested_only`.
- Claude-led agent reviews, `/codex-review` and the Qwen plugin shell template have their
  own instructions, tool restrictions and sandbox settings. They do not inherit the formal
  runner's frozen numbered-material guarantees. An agent may read repository content
  available to it. `/codex-review` uses a read-only Codex sandbox.
- Implementation is writable. `/codex-implement` uses `workspace-write` in the repository
  as the API-failure fallback for the implementer subagent. Review controls are not write
  controls for implementation. Review output is a second opinion, not authorization to
  change roles, permissions or workflow.
- Desktop checks and configuration writes are distinct operations, described below.

Tool restrictions do not isolate same-user credential files. A read-only child sandbox does
not protect the parent Python process. Your CLI configuration, host permissions and chosen
endpoint remain part of the trust boundary. Read-only operation can still transmit data.

## Data sent to model services

The primary model, implementer and reviewer CLIs send prompts and read content to their
configured services. Formal `review.py` materials include the original request, plan, selected
`files` / `context` bodies, diff, Git status and repository-wide tracked-path metadata, shared
rules and derived inventory. Source outside the selected scope is not automatically submitted
in full, but its file names may be visible through metadata. Agent-style reviews can read
other repository content within their available access; they do not share the same frozen
material-ID boundary.

The endpoint and CLI services you choose determine provider identity, retention, training
policies and downstream routing. This repository cannot guarantee those service policies or
infer an actual provider model from a requested ID. Check the policies applicable to your account.

## Local evidence and preparation

`--prepare-only` freezes and generates local materials without reading channel credentials
or starting a model. Those local artifacts can still be sensitive.

A formal `--out` directory retains requests, plans, source snapshots, diffs, prompts, manifests,
status, full logs and reports. It is not automatically cleaned. The output root is restricted
to mode 0700 and selected generated files are explicitly 0600; this is not a claim that every
artifact is individually chmod 0600. CLI processes may additionally write their own sessions
or caches. An ephemeral flag on one CLI does not imply zero persistence by every component.

Material filtering rejects common sensitive file names, not secrets embedded in arbitrary
content. A mistakenly selected secret may reach both the service and local evidence. Review
and redact artifacts before sharing; logs are not guaranteed to be automatically sanitized.

## Credential lifecycle

Users supply API credentials through configured paths, typically outside this repository in
`~/.config/ai-anti/secrets.json`. Missing or empty credentials make that channel unavailable.
The formal Python runner reads `tokenplan.value` at call time and passes it in the child
environment: `ANTHROPIC_API_KEY` for the Claude executor or `AI_ANTI_QWEN_TOKEN` for Codex.
It does not intentionally place the authentication value in prompts or reports.

Plugin shell templates set authentication through `env`; do not interpret this as a guarantee
that authentication values never occur in any process argument list on every path. Child
environment filtering preserves relevant home-directory behavior, including HOME and
CODEX_HOME; it does not isolate all user configuration or credential files. No guarantee is
made about CLI internals or endpoint handling of credentials.

## Installer and diagnosis

`install.sh` writes its managed links, marker block, optional hook and ownership manifest under
`~/.claude`. Codex opt-in also installs `~/.agents/skills` objects and an owned block in
`${CODEX_HOME:-~/.codex}/AGENTS.md`, preserving user bytes outside it. It does not write
ai-anti user configuration or business repositories. Conflicting objects, malformed settings
and unsafe markers fail before installation writes. Uninstall removes only its owned objects,
using the manifest and ownership checks; it never uses `rm -rf`.

Status checks a bounded source list and installed routing without repair. Optional `--project`
uses timed Git plumbing rather than project scripts/hooks and reads relevant configuration
keys, not secret files. Status is report-only: exit 0 does not mean healthy, authenticated or
model-callable. Temporary scratch space and external CLI version commands may cause disk
activity, including CLI-owned caches.

## Optional macOS app

First load is read-only: no model call or credential-body read. Local configuration checking
parses credentials without calling a model; it does not prove login, quota or service access.
Real detection requires user confirmation and uses a temporary synthetic repository rather
than business source. Formal API detection still submits shared rules, synthetic Git metadata
and other formal-runner materials. Success proves that synthetic call only. Temporary check
directories are normally removed on exit; abnormal termination and CLI-owned persistence
cannot be guaranteed clean.

Saving may update ai-anti `config.json`, Claude `settings.json`, Codex `config.toml` and
`secrets.json`. The app respects `CLAUDE_CONFIG_DIR` and `CODEX_HOME` for tool configuration.
New keys are restricted plaintext (0600), not Keychain entries. Secret input travels to the
backend over stdin; this desktop input boundary is not a claim about every later CLI path.

`~/.config/ai-anti/desktop/last-save.json` stores the previous raw bytes as hex in `before`.
Hex is reversible encoding, not encryption, and may contain old keys or other sensitive
configuration. The desktop directory is 0700 and backup/lock files are 0600. The last-save
backup is replaced by a later save and deleted after successful restoration; there is no
scheduled cleanup. Treat it with the same care as credentials.

Fingerprints, process locks, atomic single-file replacement and conditional rollback reduce
conflicts but do not provide a cross-file transaction with external editors. A failure may
leave a partial state that must be inspected. Uninstalling CLI integration or deleting the app
does not automatically delete user keys, configuration or recovery backups. Cleanup requires
your explicit choice of what to retain; do not remove original configuration merely to remove
an app backup. See [the app README](apps/macos/README.md) for component behavior.

## Reporting a vulnerability

If enabled for this repository, use GitHub's Security tab → **Report a vulnerability** to
submit a private report. The repository's live reporting settings have not been verified here.
If the button is unavailable, open only a request for a private contact channel, without
publishing vulnerability details, keys or logs. Do not put exploit details in a public issue.
A private report should include the affected file/command, reproduction steps and expected
impact, with sensitive evidence redacted.
