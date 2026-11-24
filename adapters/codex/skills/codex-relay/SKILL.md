---
name: codex-relay
description: Run an authorized task through a bounded plan-review-implementation-acceptance relay. Use for requests for a complete relay or authorized tasks routed by user-level or project-level collaboration modes; do not use it to implement an investigation or plan without authorization.
---

# Codex relay

This is an instruction workflow, not a state machine or a tamper-proof gate.
A missing evidence item makes the result `partial`, `blocked`, or `plan-only`,
never `completed`; completion never includes commit, push, deployment, or
real-data writes.

## Start and preserve scope

Locate this source checkout before changing directory:

```python
from pathlib import Path
loaded_skill = Path("/absolute/path/to/ai-anti/adapters/codex/skills/codex-relay/SKILL.md").resolve()
root = loaded_skill.parents[4]
```

Replace the placeholder with the actually loaded `SKILL.md` path; Markdown is
not a Python module, so do not use `__file__` here.

Require readable regular files at `root/core/REVIEW_PRINCIPLES.md` and
`root/core/CLI_DISCIPLINE.md`, then resolve and read sibling
`../claude-review/SKILL.md`. Do not infer the root from the business cwd.

From the resolved `root`, read applicable `core/DEBUGGING.md` for investigation
or attribution and `core/TESTING.md` for behavior changes, verification or acceptance.
The primary also reads `core/PLANNING.md` while clarifying and preparing the plan.
For module responsibilities, interfaces, dependency boundaries or repeated business
rules across callers, the planning primary also reads `core/DESIGN.md` as applicable.
The independent implementer follows the settled plan and contracts; do not dispatch
PLANNING / DESIGN to make it replan or redesign. Applicable DEBUGGING/TESTING dispatch below remains unchanged.
Use them while preparing the plan; missing required sources are a loading gap,
never evidence that the method was followed. Do not guess from the business cwd.

Read the business repository's `AGENTS.md` and module authority. Record its
real repository root, exact current `HEAD^{commit}` SHA, full attached branch
reference from `git symbolic-ref --quiet HEAD` (exit 1 means detached; any other
error stops), dirty baseline and scoped fingerprints, complete implementation scope,
request, and plan. Any branch or detached HEAD with a valid commit is supported;
no commit or non-Git directory blocks a complete relay. Do not initialize,
commit, create a branch, or switch the business checkout to manufacture a baseline.
Preserve other sessions' work. The request is verbatim and follows R2; the plan
states scale, files and estimated sizes, rollback, and validation. Keep each
run record outside the business repository by default; use
[the template](references/run-record.md).

Distinguish that complete scope from the existing materials selected for plan review;
follow the sibling [claude-review parameter rules](../claude-review/SKILL.md) for
`--files` and `--context`. Record planned new paths as `absent` in the dispatched
baseline. If a same-named file appears concurrently before landing, stop under the
existing fingerprint gate rather than overwrite it.

Resolve every source-scope path before use: it must stay inside the business
repository and cannot escape through `..` or a symlink. Use argument arrays or
reliable shell quoting for commands, and pass Git pathspecs after `--`. This
does not constrain a separately chosen worktree location.

“Complete relay” or explicit use of this Skill requests the workflow, not more
implementation authority; automatic matching is not implementation authority.
Investigation or planning stops at its authorized boundary. A later
implementation authorization resumes its request/plan lineage only after
comparing repository/root, HEAD SHA, attached reference or detached state, scope,
and request/plan fingerprints. Stop on repository/root or branch identity/state
changes, even at the same SHA; a detached HEAD SHA change also stops for target
and authorization/lineage revalidation. On the same attached branch, record a
HEAD advance and continue only after confirming it is unrelated and scoped
identities/byte fingerprints are unchanged. Material request or source changes
follow R6 and may require a new review round. Never reset or switch the business
checkout to erase changes. “Continue” never overrides an explicit no-implement limit.
Explicit `solo`/skip-review instructions are honored and recorded as a partial
process, never a complete relay.

## Default collaboration mode

Read `root/core/WORKFLOW_ENTRY.md` for shared mode priority (including `relay.default_mode`) and scale routing.
The Skill itself does not register permanent rules; the installer’s user-level
Codex entry supplies that routing. Report the effective mode and source.
Explicit relay requests do not grant implementation authority. `solo` remains
primary-only and is reported as partial, never a complete relay.
`desktop.primary_host` is an editor preference, not executor or permission evidence.

## Activated workflow continuation

For the same task activated by a loaded trusted skill or project workflow, read
`root/core/WORKFLOW_CONTINUITY.md`. Required stages are not omitted for small
steps under rules/auto/force; existing solo and explicit skip priority remains.
A plan-only request runs plan preparation, actual plan review and adjudication,
then stops before implementation. Review completion grants no implementation authority.
On later explicit implementation authorization, read original artifacts and verify
review/adjudication and the baseline gates above. Reuse a still-valid plan/review
and resume at implementation; do not repeat investigation or review unconditionally.
Material changes follow R6. A workflow record describes state, never authorization.
Primary independent acceptance remains required; another external review is optional.

## Review and adjudicate

When the plan relies on shared method text that the reviewer needs to inspect,
`--context` accepts only relative files inside the reviewed repository. Use it for
methods within that repository; it does not accept external absolute paths or `..`.
For other projects, put applicable method text in this run's plan appendix with the
actual absolute source path and SHA256, then freeze that plan for review. This is
one-run transport, not a maintained copy in the business repository. Do not assume
inherited text or expand fixed runner injection. A diff review without a suitable
input carrying needed method text must report that gap; do not pretend arbitrary
external context is supported.

Before implementation, invoke the resolved `claude-review` `plan` entry with
the selected existing review materials and project scale, preserving the complete
implementation scope in the plan. Resolve the channel from explicit user instructions,
otherwise `codex_relay.review_channel` (default `claude`); invalid configuration stops.
Pass `--channel qwen` for Qwen and omit `--model`, using `plugins.qwen.model`.
Claude defaults to Opus; Fable requires explicit `--model fable`. This is the real
two-stage review, and neither path automatically falls back. Require `status.json: completed`, `manifest.json: mode=plan`, complete
inventory, successful review, actual review-model evidence, and matching task
request/plan/source fingerprints. Explicit exception: Qwen executor=codex has
`actual_model=null`, `model_evidence=requested_only`; accept completed material/protocol
validation with the requested-model warning, while reporting server routing identity
as unproven. It does not prove actual cross-model identity or qualify as the R9
same-family empty-context exception. Select via `codex_relay.qwen_executor=codex` or
`--qwen-executor codex`; configure the separate `plugins.qwen.responses_base_url`
(or per-call `--responses-base-url`). Default executor remains Claude. See
claude-review's Responses boundaries and pinned CLI capability preflight.
A diff cannot replace plan review; old task
receipts cannot prove this review. Observe the CLI discipline for external CLI
calls.

Read the entire report and adjudicate every finding under core R6. Check the
revised plan against the request. Continue authorized internal choices; handle
new user-visible differences under the business project's confirmation rules.
Do not implement before adjudication. A second review is only for the R6
conditions; unresolved mandatory items after it block implementation.

## Isolated implementation

Create an isolated worktree manually from the frozen exact HEAD commit SHA:
`git worktree add --detach <path> <frozen-sha>`, using the business project's
sparse-checkout pattern. Never substitute a moving branch, `main`, `master`, or
remote default branch for that SHA. Dispatch exactly one implementer through the selected
implementation provider below. Send it the settled plan, request, adjudication, worktree/base, scope/baseline, allowed
commands, and boundaries. Supply absolute paths from the resolved `root` for
applicable DEBUGGING/TESTING sources and require the implementer to read them first;
context inheritance is insufficient. Missing necessary inputs must be reported.
It must not recurse into this Skill, review its own
plan, commit, push, deploy, or write production/real data. Only that implementer
writes the candidate; the primary may prepare non-overlapping read-only checks.

If implementation needs an existing dirty input, prepare the worktree so that
the exact recorded input and fingerprint are present; a clean worktree must not
silently omit it. Resolve overlapping dirty work before dispatch.

Resolve `codex_relay.implementation_provider` from the user config: missing/null means
`codex`; only `codex`, `token_plan` and `qoder` are accepted, including when reading or restoring
configuration. Keep `codex_relay.secondary_model` as the native Codex model; selecting
Token Plan or Qoder never overwrites it. An explicit per-task provider/model instruction takes
priority as a coherent pair. Use [vendor API implementation](#vendor-api-implementation)
for the explicit `custom_api` override. Token Plan permits `glm-5.3`, `qwen4-max`, `qwen3.8-max`,
`deepseek-v4-pro` and `deepseek-v4.1-flash`. Qoder permits only logical `glm-5.3`.
Native Codex rejects those external IDs and uppercase `GLM-5.3`. Resolve any ambiguous
explicit request before dispatch. `solo` disables independent implementation;
Claude Code's implementer workflow is unchanged. Configuration selection is not permission
to send business materials externally: require the session's applicable authorization.

### Native Codex implementation

Request the secondary model by explicit tool field when the actual tool schema
supports it: a model the user specifies for this task, otherwise
`codex_relay.secondary_model` from the user config `~/.config/ai-anti/config.json`
(stop and report if that file exists but is not valid JSON). If neither is set,
no model can be explicitly selected: apply the rule below. When a model
field is specified, use a non-`all` fork context if the schema requires it. An
agent name or self-reported identity is not evidence. Record the agent ID,
creation parameters and tool receipt, plus runtime metadata associated with that
returned agent ID when exposed (for example its session `turn_context.model`).
Provider-internal routing that is not observable is `unknown`. If the model
cannot be explicitly selected or its runtime model cannot be evidenced, or quota
fails, stop and record `blocked`/`partial`: do not substitute a model, retry in a
loop, or implement it in the primary. For explicitly recoverable host model failures,
read same-source `core/MODEL_RETRY.md` and apply its bounded native reviewer/implementer
recovery (30/90 seconds, at most two retries). Confirm the previous writer stopped,
repo/root/branch/HEAD, scope, tracked/untracked partial diff and external side effects
before continuing the same agent; retain completed work and tests. A replacement
agent must keep the specified model and cumulative count. Unknown effects or concurrent
changes stop recovery. Fixed models never authorize fallback or primary takeover.
Native reviews retain R9 empty context; do not pass primary conclusions or prior bias. Explicit request plus evidence that an
independent agent performed the work and runtime model metadata are required for
the specified-secondary-model full-chain claim.

### Token Plan implementation

Use the small `scripts/implementation.py` entry from this loaded skill, which requires
the complete source checkout (the installer links the skill directory to it). It imports
the existing pure `apps/macos/backend/model_services.py` migration/validation. Use
`model_services.profiles.token_plan.responses_base_url` and its credential paths/key,
never `active_api` or the current review model. Legacy credentials migrate only when
existing URLs classify as Token Plan under the shared migration rules; arbitrary custom
API credentials cannot become subscription credentials.

Prepare the authorized isolated workspace and a regular prompt file carrying the complete
request, settled plan, adjudication, frozen baseline, single-writer scope and allowed
commands. Use a fresh output directory outside that workspace. Example (argument paths
must be real absolute paths; do not execute placeholders):

```bash
python3 /absolute/loaded/codex-relay/scripts/implementation.py \
  --config /absolute/config.json --provider token_plan --model glm-5.3 \
  --workspace /absolute/isolated-worktree --prompt /absolute/run/prompt.txt \
  --out /absolute/run/implementation-output --prepare-only
```

Preparation resolves public config and budgets but never reads credentials or runs a CLI.
After verifying authorization and the prepared evidence, invoke without `--prepare-only`
with a different fresh output directory. Native Codex is never executed by this CLI;
use host `spawn_agent` and its receipt above. No arbitrary API/model is supported.

The GLM entry uses Codex CLI Responses, isolated `CODEX_HOME`, ignored user config/rules,
no daemon or persistence, and a workspace-write sandbox with network access disabled.
It uses a non-login shell with environment inheritance `none` and a fixed base PATH.
Codex can prepend its packaged tool directory. Hooks, apps, recursive agents, and skill
discovery are disabled.
The model transport retains necessary proxy/certificate environment and injects only the
selected Token Plan token; it never puts the token in argv/config/logs. Host permission
restrictions remain in force. This is not hard isolation from same-user credential files:
prompt restrictions cannot prevent arbitrary shell file reads. Dispatch only into the
explicitly authorized workspace/materials and do not claim stronger isolation.

Limits reuse `review.timeout` / `review.idle_timeout` (1800/900 seconds by default,
3600/900 maxima), stdin DEVNULL, file logs and 8 MiB output. No automatic retry, fallback,
landing or commit. Failures retain evidence and partial writes. Before any authorized new
run, the primary must confirm the prior writer stopped and inspect partial changes and
side effects; native recovery rules above do not apply to this GLM write task.

Require `status.json` and `manifest.json` with `completed=true`, status `completed`,
requested route model, requested effort, executor `codex`, wire API `responses`, CLI capability/version, limits,
prompt/log hashes and elapsed time. Success requires exit 0, exactly one `turn.started`
and `turn.completed`, a final agent_message and no error/turn.failed; recoverable command
failures may be repaired within the turn. The precise known GLM fallback-metadata warning
is retained as a warning. Codex-led Token Plan on `codex-cli 0.169.1` also retains one
exact fallback-metadata item after thread start and before turn start for the matching
DeepSeek request (`deepseek-v4-pro` or `deepseek-v4.1-flash`). For `qwen3.8-max` and
`deepseek-v4-pro`, it counts exact sandbox parameter diagnostics when the completed turn
includes a paired command start and successful completion. That pair proves execution
in the turn. stdout and stderr cannot tie it to the rejected request. Other error items
fail. Actual model remains null with
`model_evidence=requested_only`, even if metadata mentions a model: do not infer actual
server identity. This is an explicit exception to native runtime-model evidence, not
proof of cross-model identity. `completed` means the invocation completed, never that
implementation passed primary acceptance. A review test is not implementation evidence.

### Qoder subscription implementation

Use the same bounded `scripts/implementation.py` entry and workspace/prompt/output
preparation contract above with `--provider qoder --model glm-5.3`. Public model IDs
are logical; the executor maps this one to CLI `GLM-5.3`. Preparation never discovers
a CLI, reads login or Token Plan credentials, or launches processes. The Qoder path
never loads Token Plan profiles. Keep native `secondary_model` unchanged.

The separate implementation executor pins **qodercli 1.1.77** with a bounded version
preflight and retained raw version log/hash. The Kimi reviewer capability gate is
not implementation evidence. Preserve subscription HOME and necessary PATH/proxy/cert
environment, strip injected API credentials/config, use disabled setting sources,
empty strict MCP config, disabled hooks/skills/memory, no session persistence and
zero request retries. Explicit per-run settings grant only Read/Write/Edit/Bash/Glob/Grep
under `accept_edits` (reported as `acceptEdits`). The settled prompt names the sole
worker, scope and prohibited actions. Native agent/slash metadata remains visible.
This is **not filesystem or network sandboxing**: Bash can read same-user credential
files or cause side effects. Follow the Qoder host networking permission rule in
`core/CLI_DISCIPLINE.md`; `accept_edits` does not authorize host network access.

Require exactly one safe init with matching CLI version/workspace/model, every assistant
model `GLM-5.3`, only the six tools with unique paired IDs and no pending calls, then
one nonempty success/end_turn terminal and final assistant text, exit 0. Unknown events,
hooks, denial (even with success), malformed/truncated streams and post-terminal data
fail closed; only proven empty artifacts updates, thinking segments and segmented tool messages with
null stop_reason (still requiring unique calls and matched results) are tolerated.
Ordinary tool command errors may be repaired within the same run. Retain raw logs/hashes,
version evidence, duration, partial writes and failed status on timeout, output cap or
SIGTERM. No write retry, fallback, commit or landing occurs. Confirm the previous writer
stopped and inspect partial work/side effects before any newly authorized run.

Completed evidence uses executor `qoder`, wire API `qoder-cli`, requested `glm-5.3`,
actual_model `GLM-5.3`, model_evidence `cli_reported`, backend_model `unknown`.
This only attests what the subscription CLI reports. Scratch Write/Bash probes establish
those tool paths on 1.1.77; they do not prove whole-project implementation or primary
acceptance. The primary independently checks candidate bytes, tests, scope and identity
before landing. The review test remains Kimi review and cannot validate GLM implementation.

### Vendor API implementation

Use `custom_api` only as an explicit task override with an exact `--model` and
one `--file` for every authorized workspace-relative text path. Persisted
`implementation_provider` values keep the existing contract. Use a detached
linked Git worktree and an exclusive output directory outside it. Include
the settled task in the prompt and only the files the user authorized for
vendor disclosure. A selected new file must have an existing parent directory.

```bash
python3 /absolute/loaded/codex-relay/scripts/implementation.py \
  --provider custom_api --model qwen4-max --file src/example.py \
  --workspace /absolute/isolated-worktree --prompt /absolute/run/prompt.txt \
  --out /absolute/run/direct-prepare --prepare-only
```

Supported model and service pairs are `qwen4-max` / `qwen3.8-max` for `qwen`,
`kimi-k3.1-pro` / `kimi-k3` for `kimi`, and `deepseek-v4-pro` /
`deepseek-flash` for `deepseek`. Each uses its fixed HTTPS Chat Completions
endpoint and the matching `<service>.value` slot in
`~/.config/ai-anti/secrets.json`. Inspect the prepared route and scope before
running with a fresh `--out` and without `--prepare-only`. Preparation checks
the local files and Git worktree without reading the credential or contacting
the vendor.

The model receives the prompt and selected text only. It can stage one proposal
with complete replacements for one or more selected files. The second streamed
request disables tool calls and requires an exact receipt. The host checks the
workspace baseline again and applies one Git patch. Rewritten files start
owner-only, then the host restores existing permission bits. New files stay
owner-only. The primary inspects all changed files and runs tests independently.
Timeouts and output budgets use the bounded implementation entry
settings. Failures retain safe diagnostics and require inspection of the
candidate before any new run. The workspace lock coordinates this tool's
writers. External writers and interruption during Git patch application or
permission restoration require inspection of the retained worktree.

The manifest, status, and hashed structured run log record the service and
endpoint, matched API-reported model, stage outcomes and file fingerprints.
For prepared runs `actual_model` is null and `model_evidence=requested_only`.
After both turns and confirmation validate, `actual_model` is the model
reported by the API with `model_evidence=api_reported`. The backend model
remains `unknown` and primary acceptance is recorded separately.

### Landing gates

Before landing, recheck the business repository/root, HEAD SHA, and full attached
branch reference or detached state against the dispatched baseline using the
same identity and HEAD-advance rules as authorization resumption above. Stop and
revalidate authorization and review lineage for identity changes or a detached
SHA change; never automatically reset or checkout. Any original task branch or
detached checkout may receive authorized checked increments without automatic
merge or commit. Compare each scoped target's current fingerprint and identity
(including absent, new, and deleted files) with the dispatched baseline. Any
change means stop and resolve the overlap; never copy a directory over it. Land
only checked increments while retaining concurrent changes. Keep a blocked or
partial worktree/branch until all increments are landed or persistently exported;
then clean only this task's worktree and branch under the project rules.

## Primary acceptance

Read applicable shared methods from the resolved `root` and independently check
candidate behavior and verification evidence. These methods supplement quality;
review adjudication still follows core R6.
Inspect every increment, including untracked files. Independently run the
settled checks and prove they used the candidate artifact; implementer self-test
is not acceptance. Return behavior defects to that implementer. The primary may
fix only mechanical documentation errors and must record them. Apply the
project's UI evidence and first-open review where relevant. A diff review is
optional and never replaces plan review or primary acceptance. Record results,
uncovered boundaries, landing, cleanup, and the strictly qualified conclusion in
the template.

The primary model is selected by the user/runtime, never dynamically switched by this
Skill. `codex_relay.secondary_model` selects only the native Codex independent implementer;
`implementation_provider=token_plan` uses the independent Token Plan model; `qoder` fixes implementation to logical glm-5.3. None selects the reviewer. Both review channels retain the same evidence and scope gates.

### Independent Token Plan model

`codex_relay.token_plan_implementation_model` accepts only `glm-5.3`, `qwen4-max`,
`qwen3.8-max`, `deepseek-v4-pro` and `deepseek-v4.1-flash`. See the repository's
`docs/MODEL_ROUTE_VALIDATION.zh-CN.md` for observed implementation results by date and route.
Missing/null means `glm-5.3`; empty, preview, case variants and unknown values fail
before credentials or CLI launch, even with another provider selected. Explicit model
instructions override this value only with a matching provider. Token Plan always copies
its own profile endpoint/credentials, never `active_api` or the review model. No default
switch or automatic retry/fallback is introduced.

Qwen4 uses high effort and a per-run absolute-path text-only model catalog. GLM retains
medium effort and its exact pre-turn metadata warning exception; that warning fails a
Qwen4 request. Responses evidence remains `actual_model=null` / `requested_only`;
metadata and a completed turn do not prove backend identity or primary acceptance.
Prepare-only writes the same model/effort/catalog without reading credentials or launching
CLI. For rollback, select GLM before using an older version; older runners fix Token Plan
to GLM and cannot preserve Qwen4 execution. No installation or user-config change is implicit.


Token Plan 双订阅例外（用户明确授权，2026-11-16）：配置双槽且启用自动切换时，仅对尚未向 CLI 发送响应的真实 HTTP429 明确额度耗尽，在同模型、同通道、同请求内切换一次凭证。本逻辑运行后续阶段/attempt 持续使用备用；下一任务重新主优先。普通限流、认证失败、SSE 内错误、断流、账户绑定响应引用均不切换。包装器最终失败才判定本档失败；本会话已耗尽槽不得因进程重跑再次尝试。此例外不改变模型/通道降级顺序，不授权写任务进程重试；已经完成的工具与文件操作不得重复执行。
