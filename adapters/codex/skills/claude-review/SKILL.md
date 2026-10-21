---
name: claude-review
description: Run a bounded, repeatable Claude or Qwen cross-model review of a plan or scoped Git working-tree diff. Use for the ai-anti review relay; do not use for implementation or deployment.
---

# Claude review

For an authorized task requiring the whole plan-review-implementation-acceptance
relay, use the sibling [codex-relay](../codex-relay/SKILL.md) Skill when
available. This Skill remains a single plan or scoped-diff review and does not
implement or orchestrate the relay.

Read the shared [review principles](../../../../core/REVIEW_PRINCIPLES.md) and
[CLI discipline](../../../../core/CLI_DISCIPLINE.md) from the resolved source
checkout before preparing materials. For `task.md`, supply its sibling
`task.request.md` containing the verbatim request per R2; never reconstruct it
from inference. The script injects the required core sections at runtime.

Use the repository source entry directly; it is not an installed global command.
Resolve the source path before changing to the business repository, for example:

```bash
REVIEW_SOURCE=/absolute/path/to/ai-anti/adapters/codex/skills/claude-review/scripts/review.py
python3 "$REVIEW_SOURCE" plan \
  --repo /path/to/repo --plan /path/to/task.md --files src/a.py \
  --scale 'project deployment scale' --out /private/tmp/review-output
```

For a working-tree review, substitute `diff` and omit `--plan`. The Git repository
must already have a HEAD commit. `--repo` is the actual Git root, not a subdirectory.

For `plan`, `--files` selects current review materials, not the complete implementation
scope: it requires at least one existing, relevant UTF-8 regular file. Record the
complete scope (existing, planned new, and planned deleted paths) in the plan. For
new files, specify their paths, purposes, interfaces, expected behavior and acceptance.
Keep `task.request.md` verbatim; do not add inferred requirements to it. Exclude
not-yet-created files only from material selection, never from the plan's scope or
authorization. A missing file that should exist is a gap, not silently a planned addition.
Select real relevant README, configuration or code files. If none are suitable, report
this CLI limitation; do not create placeholder/empty files, omit required `--files`,
or copy the plan into the business repository to bypass validation.

For example, in a repository containing only `README.md`, a plan to add `average.py`
and `test_average.py` keeps both additions in the plan and uses `plan --files README.md`,
not `--files average.py test_average.py`. `--plan` and its request sibling are loaded
separately; do not select them again through `--files` or `--context`. `--context`
adds existing read-only relative files inside the repository, never external absolute
paths or `..`; it must not overlap `--files`. These existence restrictions on planned
additions apply to `plan`: `diff` still accepts actual new untracked files and actual
deletions, and must retain the complete diff scope. Report failures under the user's
stop-on-failure instruction; this guidance grants no automatic retry or external call.

`--out` must
be a new directory outside the reviewed repository, with an existing parent.
Use real paths (no symlink components or `..`). Inputs are UTF-8 text only, at
most 2 MiB per file and per stage, and 2000 characters per LF-delimited line.
All materials, including single blocks, are frozen at short relative paths such as
`chunks/001/001.txt`; source ids increase in sorted source order and appended
inventory uses the next unused id. Materials are frozen losslessly into blocks of at most 12 KiB and 120
LF-delimited physical lines. The manifest records source hashes, byte sizes, line
ranges and block hashes; every block must be fully delivered and verified, including inventory
generated between plan stages. Grep/Glob do not satisfy this requirement.
Oversized or long-line materials are rejected rather than summarized. Use `--prepare-only` to
freeze and inspect materials and the first-stage prompt without reading credentials or calling the CLI.
The plan review prompt is generated only after its independent inventory exists. The default Claude-channel model is Opus;
choose Fable only with `--model fable`.
On the Claude channel, actual model IDs must fully match the requested family with numeric version segments only
(`scripts/review_runner.py`); cross-family IDs and non-numeric suffixes fail closed.
`claude_review.model_families` in `~/.config/ai-anti/config.json` may override or add
aliases (`--model` choices come from the merged table). Each key must be a value the
Claude CLI itself accepts for `--model`; this entry does not map custom aliases to models.
Each pattern is used verbatim with `fullmatch` and ASCII matching, so it must accept only
that family with ASCII digit version segments. A missing file or key uses the built-in
table; an unparseable config or wrong type fails before any call.
The manifest records `cli_version`; a version outside `compat.json` adds a warning to
`status.json`, the manifest and the report but does not block (see `docs/COMPATIBILITY.md`).
For the Claude executor, the init capability gate admits no plugins except the CLI's built-in `agents-md` and
`telemetry` (exact `builtin` path and `<name>@builtin` source); any other plugin fails closed.

The project governance document supplies `--scale`. A plan review performs two
serial calls per round: an inventory call without the plan, then a fresh review
call with the frozen plan and original inventory. Each call has its own timeout
and bounded output (default 600 seconds total for Claude or 1800 for Qwen, 900 seconds without output,
2 MiB log per Claude call or 8 MiB per Qwen call; configure with `--timeout`, `--idle-timeout`,
`--max-output-bytes`). The entry does not retry a failed call or silently fall back
to another model. New material or a material change requires a new review round;
the shared two-round limit still applies.

The entry validates the request sibling for plans, material paths and size,
authority text, the CLI's advertised capabilities, and structured results. It
writes `status.json`, `manifest.json`, stream logs, frozen material, and, only
after a verified call, `report.md`. `completed` proves only a verified review
call. Under R1/R6/R7, the main model adjudicates every finding and retains all
implementation authority.

The allowed Claude tools limit review tools, not the operating-system process or
all user configuration. Input filtering is bounded and conservative, not a
universal secret scanner. The entry uses only Python 3.9+ standard library;
independent checks live in `adapters/codex/tests/`.

Read `status.json` first. On failure, inspect the complete logs; a quota failure
must not trigger same-session retries. On completion, read the full report and
present every finding and its disposition under R7. Unselected paths appear only
in the inventory/status: missing content remains unverified, not assumed safe.
See [compatibility](../../../../docs/COMPATIBILITY.md) for the CLI versions verified
live; offline fixtures do not establish live success.

## Channel selection

The Skill name remains `claude-review`. Select `--channel claude|qwen`; explicit CLI
selection wins over `codex_relay.review_channel`, whose missing/null default is `claude`.
`review.channels` only routes the Claude-led workflow. Invalid types or values fail closed.
Qwen reuses `plugins.qwen.model`, `base_url`, and `credential_paths`; it requires a valid
HTTP(S) URL and non-empty single-line model. Qwen rejects explicit `--model`: its model
comes from configuration, while the retained Claude CLI receives `--model sonnet` and
three default-model mappings. Init must report the configured model exactly.
Credential paths expand `~`; relative paths resolve against `--repo`, regardless of cwd.
The first non-empty string at `tokenplan.value` is used only in a child environment;
prepare-only validates public configuration without reading these files. Missing Qwen
configuration or credentials fails without trying Claude. Network proxy/certificate
environment is preserved; conflicting Anthropic keys and Bedrock/Vertex/Foundry switches
are removed from the child copy. The Qwen child receives the token only as
`ANTHROPIC_API_KEY`; inherited `ANTHROPIC_AUTH_TOKEN` is removed for bare-mode authentication.

Qwen uses `--bare --restricted --tools ''` with a strict single local MCP server,
`materials`, exposing only `mcp__materials__read_material`. Its only argument is
`{"id":"001"}` with an enum of the stage's frozen mandatory blocks. Source paths
are review location labels, never tool arguments. Claude retains safe-mode and
Read/Glob/Grep. Registry and MCP configuration live under logs, outside materials;
prepare-only generates them without reading credentials. The MCP child explicitly
clears Anthropic credentials, service URLs and provider switches in its environment.
The standard-library server checks registry structure, regular files, paths, byte
limits and SHA-256 on every read, returning one text block containing the original
UTF-8 text, including actual newlines and Chinese (no second JSON envelope).

Init must report exactly that tool and one connected dynamic `materials` server,
`dontAsk`, empty skills/slash commands and only the existing builtin-plugin whitelist.
Each result must bind to a unique call ID and material ID, match both the message
content and top-level `tool_use_result`, and equal the current frozen bytes/hash.
For verified empty material only, CLI 2.1.280 may replace message content with
`(mcp__materials__read_material completed with no output)`; the top-level result
must still be exactly one empty text block. Non-empty materials never admit this wrapper.
MCP tools/call accepts optional object `_meta` transport metadata, ignored by the
server; tool arguments remain exactly `{id}`.
Duplicate call IDs/results, extra parameters, errors and missing blocks fail closed.
Re-reading a material with a new call ID is allowed. Plan stages generate independent
registries; the review registry includes the newly frozen inventory. Qwen records
empty `read_recoveries` and count zero. Delivery evidence does not prove understanding.

Only Qwen admits complete standalone `[claude-code:unrecognized_model]` lines with
exact payload `{"model": <configured model>, "query_source": "sdk"}`. Repeated valid lines
are transport warnings recorded per stage; raw logs remain unchanged. Unknown noise,
wrong model, malformed payload and permission denials still fail closed.
On the Claude channel, the sole tool-error exception is at most three cumulative Read missing-file errors
per stage (never reset by successful Reads). The CLI 2.1.280 evidence uses exactly
`File does not exist.` with an optional ` Note: your current working directory is <stage cwd>.`
suffix and matching `tool_use_result` string prefixed by `Error: `. Other wording,
unknown ids, duplicate results, existing paths, `..`, outside paths and symlinks
fail closed. Each error must precede a successful full Read of a declared mandatory
block, bound to its unique tool id and actual snapshot content; all mandatory blocks
must still be fully read and the snapshot unchanged. Direct validation without a
mandatory set remains strict. Corrections are recorded per stage in `read_recoveries`
and counted in manifest/status/report separately from transport warnings. Raw logs
remain unchanged; there are no aliases, automatic CLI restarts or extra model calls.
Manifest/status record selected channel and configured/requested/CLI model; manifest
records actual init model and effective limits. These fields do not prove endpoint-internal
routing. Explicit resource overrides win; upper bounds remain 3600 seconds total,
900 seconds idle, and 8 MiB output per call. The idle default is 900 seconds;
explicit `--idle-timeout` values still take precedence within that cap. Qwen total defaults to 1800 seconds.


## Qwen Responses executor (explicit opt-in)

`codex_relay.qwen_executor` selects `claude|codex` (default `claude`);
`--qwen-executor` overrides it for this invocation. Codex requires a separate
`plugins.qwen.responses_base_url`; `--responses-base-url` overrides it for this
invocation only. Use `--channel qwen --qwen-executor codex --responses-base-url
https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1`. Reuse the configured
Qwen model and credential paths. An Anthropic URL is never rewritten. URL overrides
are rejected outside Qwen Codex; URLs forbid credentials, whitespace, query and fragment.
The macOS settings show a disabled executor: Codex hosts use Codex CLI for API review, Claude hosts use Claude Code CLI for API review, and Claude-to-Codex review uses Codex CLI. Legacy Codex API executor changes require an explicit save; Anthropic and Responses URLs remain independent. The CLI executor override remains available.

Codex configuration semantics are pinned to `codex-cli 0.160.0` or `codex-cli 0.163.0`; unknown versions
stop at preflight. Compatibility warnings use `tested.codex`, independently of
Claude versions. Codex runs ephemeral, no-daemon, ignore-user-config/ignore-rules,
read-only, approval never, stdin DEVNULL, bounded direct logs and process-group
cleanup. It disables shell/snapshot, apps/hooks, browser/computer use, image viewing,
goals, multi-agent, JS REPL and host skill discovery. Global AGENTS and system skill
catalog may remain visible; request_user_input and generic MCP resource tools
(list_mcp_resources, list_mcp_resource_templates, read_mcp_resource) may remain available. Every executed
non-material tool is rejected by the protocol gate; this is not empty context or
proof that only one tool is visible. The reviewer instructions prohibit recursive relay. No `user_instructions` override
is used: that key is unrecognized by this CLI. The known unstable-feature notice
is suppressed through its dedicated setting; all other error items still fail closed.

Only explicitly approved `materials.read_material` with exact `{id}` is accepted.
Independent `*.codex-config.json` records configuration, never a Claude mcp.json.
A dedicated child token is erased in the MCP environment; HOME/CODEX_HOME are
preserved; inherited session/model credential environment variables are removed.
Same-user credential files under CODEX_HOME may remain readable; this is not
filesystem credential isolation. Official Model Studio
qwen3.8-max catalog metadata is narrowed to text review; unknown models use the
CLI fallback with its exact warning. Global and per-tool output limits are explicit.
No automatic retry or fallback occurs. Plan still has independent inventory and review.

Codex exec JSONL supplies no actual model identity: `actual_model=null` and
`model_evidence=requested_only` are mandatory, and the report warns that server routing
identity is unproven. `cli_model` records the actual requested CLI model, not sonnet.
This is an explicit acceptance exception to actual-model evidence in this Skill and
codex-relay; never describe it as equivalent to Claude init or proven cross-model
identity. A final report must follow complete exact frozen-material reads and a
single successful turn termination. Ordinary reasoning/progress is not final evidence.
Raw JSONL hashes and stage durations are recorded; idle timeout defaults to
900 seconds, so longer Responses silence may still fail. Do not invent maximum-silence data.

The Codex executor explicitly sets `model_reasoning_effort=medium`, matching the
existing Claude review effort. It preserves the official catalog's default xhigh
metadata. Initially omitting the explicit setting caused observed requests to use
xhigh, an unintended budget increase; local wire capture confirms the explicit
medium override and complete 12KiB material transport. This does not establish
that reasoning effort caused observed live silence, or that live latency is fixed.

### Codex 版本预检告警边界（2026-10-21）

`--version` 只接受唯一完整版本行；支持集合仍为 0.160.0 / 0.163.0。允许前后
至多一条精确 `WARNING: proceeding, even though we could not create PATH aliases: Operation not permitted (os error 1)`。
告警进入 capabilities 与既有 warnings 输出，原始日志及哈希保留；其他噪声、重复或
非零退出仍拒绝。此例外不用于正式 JSONL，也不证明受限环境的 exec 可运行。

## Qoder subscription reviewer (explicit opt-in)

Use `--channel qoder --model kimi-k3` with the formal `review.py plan|diff`
entry. Existing `codex_relay.review_channel=qoder` also selects it; no new user
configuration keys are needed. Omitted `--model` defaults to `kmodel_latest`;
only `kimi-k3` and `kmodel_latest` are accepted, both requesting CLI model
`kmodel_latest`. Init and every assistant message must report exactly `Kimi-K3`.
These are CLI identity claims, not proof of internal backend routing. Qoder uses
the existing subscription login; it neither reads Qwen credentials nor changes
the user's Qoder default model or login configuration. No automatic fallback or retry.

Only `qodercli` 1.1.74 is admitted. This version has verified hidden isolation flags;
the runtime init must still show empty skills/plugins, `dontAsk`, and exactly one
connected materials server and `mcp__materials__read_material` tool. Hook events,
unknown execution tools, permission denials and mismatched models fail closed.
Native slash-command and agent names can remain visible; stage evidence records
them. This is neither empty context nor operating-system filesystem isolation.
Settings disable hooks, skills, skill shell execution and automatic memory; builtin
skills and setting sources are disabled, with an explicit read-only system prompt.

Both plan phases and frozen diff use ID-only materials. Qoder's original string
tool response and top-level text blocks must independently match frozen bytes
before normalization into the shared material audit. Every required block must
be fully read before a unique successful final result; post-final events fail.
Raw logs/hashes, timing, byte counts and credits are retained. Defaults are 600
seconds total, 900 seconds idle and 2 MiB output per stage; total timeout wins.
Cleanup targets only the new process group: poll before TERM and after its grace,
then KILL remaining descendants even if the leader exited. An EPERM race is
retried only if polling newly reaps the leader; real permission failures remain
failures. Independent formal plan/diff acceptance passed on 1.1.74 with Kimi-K3;
see `docs/COMPATIBILITY.md` for the measured evidence and its boundaries.
