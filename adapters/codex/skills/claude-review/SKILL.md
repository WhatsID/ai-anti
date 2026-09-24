---
name: claude-review
description: Run a bounded, repeatable Claude cross-model review of a plan or scoped Git working-tree diff. Use for the ai-anti review relay; do not use for implementation or deployment.
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

For a working-tree review, substitute `diff` and omit `--plan`. `--files` is the
task's explicit relative scope; the Git repository must already have a HEAD
commit. `--repo` is the actual Git root, not a subdirectory; `--context` adds read-only context. `--out` must
be a new directory outside the reviewed repository, with an existing parent.
Use real paths (no symlink components or `..`). Inputs are UTF-8 text only, at
most 2 MiB per file and per stage, and 2000 characters per LF-delimited line.
Large materials are frozen losslessly into blocks of at most 12 KiB and 120
LF-delimited physical lines. The manifest records source hashes, byte sizes, line
ranges and block hashes; every block must be fully Read, including inventory
generated between plan stages. Grep/Glob do not satisfy this requirement.
Oversized or long-line materials are rejected rather than summarized. Use `--prepare-only` to
freeze and inspect materials and the first-stage prompt without calling Claude.
The plan review prompt is generated only after its independent inventory exists. The default model is Opus;
choose Fable only with `--model fable`.
Actual model IDs must fully match the requested family with numeric version segments only
(`scripts/review_runner.py`); cross-family IDs and non-numeric suffixes fail closed.
`claude_review.model_families` in `~/.config/ai-anti/config.json` may override or add
aliases (`--model` choices come from the merged table). Each key must be a value the
Claude CLI itself accepts for `--model`; this entry does not map custom aliases to models.
Each pattern is used verbatim with `fullmatch` and ASCII matching, so it must accept only
that family with ASCII digit version segments. A missing file or key uses the built-in
table; an unparseable config or wrong type fails before any call.
The manifest records `cli_version`; a version outside `compat.json` adds a warning to
`status.json`, the manifest and the report but does not block (see `docs/COMPATIBILITY.md`).
The init capability gate admits no plugins except the CLI's built-in `agents-md` and
`telemetry` (exact `builtin` path and `<name>@builtin` source); any other plugin fails closed.

The project governance document supplies `--scale`. A plan review performs two
serial calls per round: an inventory call without the plan, then a fresh review
call with the frozen plan and original inventory. Each call has its own timeout
and bounded output (default 600 seconds total, 300 seconds without output,
2 MiB log per call; configure with `--timeout`, `--idle-timeout`,
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
