---
name: bug-review
description: Explicit independent second opinion on an existing problem analysis, using a caller-prepared local text bundle. Does not collect evidence, repair, implement, or replace plan review.
---

# Bug review

Resolve this installed Skill symlink to its actual source checkout first. From
`adapters/codex/skills/bug-review/SKILL.md`, the repository is four parent
directories above the containing directory. Read that checkout's
`core/BUG_REVIEW.md`, `core/CLI_DISCIPLINE.md` and `docs/BUG_REVIEW.md`.
Do not assume the business cwd contains ai-anti or initialize Git there.

Prepare a bundle with verbatim user problem, separate analysis and explicitly
selected evidence. Record source, original locator, coverage/omissions, environment,
limitations and domain checks. Never reconstruct user words from inference.
Run the resolved checkout's
`adapters/codex/skills/claude-review/scripts/bug_review.py --bundle ABS --out NEW_ABS`.
This entry uses `codex_relay.review_channel` for both hosts, **not** `review.channels`;
`--channel claude|qwen|qoder` overrides it. It reuses existing service configuration.
`--config-base ABS` defaults to cwd and only anchors relative credential paths.
No Git or repository is required. `--prepare-only` reads no credentials or models.

Two fresh processes perform independent analysis without the analysis file, then
review with its original output. Do not run additional calls on failure or to fix
JSON. Read status first and the entire report/raw evidence; disclose failures and
identity limits. Same or unknown model identity cannot establish cross-model review.
completed means protocol success; supported means support within visible evidence,
not proven root cause or a deployed fix.

Adjudicate every finding as accepted, needs verification or rejected with evidence.
The caller may investigate within existing authorization; this entry never executes
next_check. Default one round; only important new facts or a material root-cause
change justify round two with `--round 2 --previous FIRST_COMPLETED_OUT` and a newer
revision. Maximum two rounds; retain lineage, bundles and adjudication. Unresolved
gaps stay unknown. Stop at the report before human authorization to repair, then
handoff conclusion, evidence, gaps and original failure checks to existing plan review.
Do not change business skills, global routing or verified patterns automatically.
