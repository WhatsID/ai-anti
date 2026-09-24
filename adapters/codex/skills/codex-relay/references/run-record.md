# Codex relay run record

> Store beside the task, outside the business repository by default. This is a
> primary-model record, not script-generated truth. Replace bracketed fields;
> preserve paths and artifact hashes.

## Identity and scope

| Field | Value |
| --- | --- |
| Task / date | [task] / [date] |
| Business repository and root | [path] |
| Local `main` base / current HEAD | [SHA] / [SHA] |
| Explicit scope | [relative paths] |
| Dirty baseline | [status and fingerprints] |
| Authorization boundary | [investigate / plan / implement] |

## Request, plan, and review

| Item | Evidence / result |
| --- | --- |
| Verbatim request path and fingerprint | [path, SHA-256] |
| Pre-review plan path and fingerprint | [path, SHA-256] |
| Final plan and revision relation | [path; changes after review] |
| Review output / manifest / status | [paths] |
| Two-stage inventory and review | [success/failure with evidence] |
| Actual review model evidence | [metadata or `unknown`] |

## Adjudication and requirement check

| Finding / difference | R6 disposition and concrete reason | Action / user decision |
| --- | --- | --- |
| [item] | [mandatory / register / reject] | [result] |

## Implementation

| Field | Value |
| --- | --- |
| Agent ID and creation-tool receipt | [evidence] |
| Requested model | [value or n/a] |
| Observable runtime model | [runtime metadata or `unknown`] |
| Worktree / exact base / dispatched fingerprints | [evidence] |
| Delivered files and fingerprints | [evidence] |
| Scope fingerprint comparison before landing | [result, including new/deleted/absent] |

## Primary acceptance and closure

| Field | Evidence / result |
| --- | --- |
| Independent checks / logs / candidate identity | [commands and artifacts] |
| UI evidence, if applicable | [desktop/mobile observation] |
| Uncovered boundaries | [items and reason] |
| Landing / cleanup or retained artifacts | [paths and result] |
| Conclusion | [`completed`, `partial`, `blocked`, or `plan-only`; precise reason] |

`completed` requires evidenced plan review, an independently implemented
candidate, and independent primary verification. If a requested secondary model
has no observable runtime metadata, record `unknown` and do not claim the
specified-model full chain.
