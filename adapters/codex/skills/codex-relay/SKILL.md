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
error stops), dirty baseline and scoped fingerprints, explicit file scope,
request, and plan. Any branch or detached HEAD with a valid commit is supported;
no commit or non-Git directory blocks a complete relay. Do not initialize,
commit, create a branch, or switch the business checkout to manufacture a baseline.
Preserve other sessions' work. The request is verbatim and follows R2; the plan
states scale, files and estimated sizes, rollback, and validation. Keep each
run record outside the business repository by default; use
[the template](references/run-record.md).

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
the explicit scope and project scale. Resolve the channel from explicit user instructions,
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
remote default branch for that SHA. Send one native subagent
the settled plan, request, adjudication, worktree/base, scope/baseline, allowed
commands, and boundaries. Supply absolute paths from the resolved `root` for
applicable DEBUGGING/TESTING sources and require the implementer to read them first;
context inheritance is insufficient. Missing necessary inputs must be reported.
It must not recurse into this Skill, review its own
plan, commit, push, deploy, or write production/real data. Only that implementer
writes the candidate; the primary may prepare non-overlapping read-only checks.

If implementation needs an existing dirty input, prepare the worktree so that
the exact recorded input and fingerprint are present; a clean worktree must not
silently omit it. Resolve overlapping dirty work before dispatch.

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
loop, or implement it in the primary. Explicit request plus evidence that an
independent agent performed the work and runtime model metadata are required for
the specified-secondary-model full-chain claim.

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
Skill. `codex_relay.secondary_model` selects only the independent implementer; it does
not select the reviewer. Both review channels retain the same evidence and scope gates.
