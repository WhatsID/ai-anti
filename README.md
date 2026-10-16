# ai-anti

English | [简体中文](README.zh-CN.md)

Cross-model adversarial review, with implementation and acceptance split across models, for
AI-assisted software development.

## What it is and why

In AI-assisted coding, when a single model does "plan → write code → accept" on its own, any blind
spot it has travels all the way into the result. ai-anti splits that chain across roles with
different stances:

1. **Plan** — the primary model writes a plan file and keeps the user's original request verbatim.
2. **Cross review** — a *different* model reads the repository (read-only) and reviews the plan
   against the original request, from the stance "someone else wrote this; find what will cause
   rework".
3. **Triage** — the primary model rules on every finding: must-fix, register for later, or reject.
   This keeps review rounds from exploding.
4. **Implement** — a separate model carries out the settled plan.
5. **Final check** — the primary model reviews the whole increment against the plan and reports.

The point is not more models doing more work, but a second model with a different stance finding
the mistakes the first one cannot see — before they turn into hidden defects or late rework.

## Two main paths

Two relay paths are supported: Claude → Codex and Codex → Claude/Qwen. The left side is the primary
model that leads (plans, adjudicates, does the final check); the right side reviews or takes over
implementation.

| Direction | Entry points | What happens |
|-----------|--------------|--------------|
| Claude → Codex | `/codex-review plan <plan.md>`, `/codex-review diff`, `/codex-implement <plan.md>` | Claude Code leads; Codex reviews in a read-only sandbox, or takes over implementation when the implementer subagent is blocked by API failures |
| Codex → Claude/Qwen | `$claude-review`, `$codex-relay` | Codex leads; the selected Claude/Qwen model reviews a plan (two stages) or a scoped diff under fail-closed checks; `$codex-relay` runs the full relay |

## Four relay modes

- **solo**: the relay is disabled; the primary model does all the work.
- **auto**: the rules mode criteria are only guidance; the primary model decides whether a task is worth
  the relay. For example, many files changed by a mechanical replacement can be done directly,
  while a single file with subtle logic can still go through the relay. More flexible, but the
  outcome depends on the model's judgment at the time.
- **rules** (default): fixed rules decide whether a task goes through the relay — a new feature
  or module, an expected change to 3 or more production files that affect runtime behavior (tests
  and pure docs do not count), a change spanning two or more top-level subsystems, or a vague
  request that needs a plan first. Any one of these triggers the relay; everything else is done
  directly. The same change is always handled the same way, so the outcome is predictable.
- **force**: every substantive task goes through the relay.

In both rules and auto, tasks that hit a high-risk trigger (changes to the relay mechanism
itself, plus whatever a project lists in its own `CLAUDE.md`, such as database schema,
deployment, or authentication) must go through the relay; the two modes differ only on ordinary
tasks.

Only `solo`, `auto`, `rules`, `force` are accepted; the former `required` value is invalid.

Switch modes with the `/relay-mode` command.

## Relay essentials

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

**About language:** the rule texts (`core/`, `adapters/claude-code/CLAUDE.global.md`, command
templates) and the docs under `docs/` are written in Chinese and are the only authority; they are
read by the models, which handle Chinese fine. This README is a summary, not a translation.
`core/DESIGN_RATIONALE.md` records the real failure behind each rule.

## Install and use

Prerequisites: macOS, [Claude Code](https://docs.anthropic.com/en/docs/claude-code),
[Codex CLI](https://github.com/openai/codex), Python 3.9+, with both Claude Code and Codex CLI
logged in and working.

```bash
git clone https://github.com/WhatsID/ai-anti ~/ai-anti
cd ~/ai-anti
./install.sh                 # Claude Code side: commands, subagents, CLAUDE.md import
./install.sh --with-codex    # optional: Codex user-level entry and ~/.agents/skills
./install.sh --status        # read-only health check
```

To use a plugin review channel, also pass `--with-plugin <name>` and write
`~/.config/ai-anti/config.json` following [config.example.json](config.example.json) (see
"Model extensions" and "Configuration" below).

Then open Claude Code or Codex in any repository. Nothing needs to be copied into your projects;
Claude Code loads `~/.claude/CLAUDE.md`, and Codex loads
`${CODEX_HOME:-~/.codex}/AGENTS.md`. Projects may override
parameters (high-risk triggers, where to register findings, deployment scale, ...) in their own
`CLAUDE.md`; see the override table in `adapters/claude-code/CLAUDE.global.md`.

Both entries read `core/WORKFLOW_ENTRY.md`; review details and relay skills load only
when needed. `--with-codex` installs the skills and an owned instruction block that
explicitly reads `adapters/codex/AGENTS.global.md`. User content outside the block is
preserved byte for byte. Reinstallation inherits optional components and upgrades
legacy manifests; reinstall after moving the source checkout to refresh its paths.

A nonempty `AGENTS.override.md` shadows the entry, so installation stops and asks you
to resolve it manually. Unsafe instruction files or path ancestors and damaged markers
also stop installation before writes. Before changing `CODEX_HOME`, run
`./install.sh --without-codex`: it removes the owned block at the manifest's recorded
path and both Codex skills. Then run `--with-codex` with the new environment.
Uninstall keeps user content, deleting an empty file only when the installer created it.
It never edits user configuration JSON or business repositories.

The entry loads in a new session. Status checks installation, not model compliance;
external CLI version commands may create their own caches. Full multi-project relay
validation is outside this entry change.

The planning primary loads `core/PLANNING.md` for clarification, specifications and
verifiable behavior slices when needed. Clear small tasks do not require a full plan.
It preserves mode and permission boundaries and does not create tickets, agents or
per-project setup. Independent implementers follow the settled plan.

Shared methods also load on demand: `core/DEBUGGING.md` guides diagnosis and
root-cause evidence; `core/TESTING.md` guides behavioral verification and acceptance.
This applies to direct work and solo as well as relays. Implementers receive explicit
source paths, and the primary independently checks their evidence. No new command,
skill installation or per-project copy is required. The methods preserve authorization
and review adjudication; they do not make every task a TDD task. Behavioral validation
is limited to recorded examples, not a claim of full multi-project coverage.

`./install.sh --status --project DIR` optionally diagnoses an explicit project without
writing it: Git root/HEAD/local-main baseline, readable project entries, effective mode,
and global deployment-scale fallback. Missing project entries or parameters are hints,
not damaged installation; project prose overrides still require the AI to read them.
No project is scanned unless `--project` is supplied. Plain `--status` checks explicit
shared sources and installed routing, not `relay`/`defaults` or full user configuration.
It checks plugin links, not plugin metadata, credentials or channel connectivity.
Status remains a report: exit 0 means reporting finished, not that every check passed.
It does not prove model compliance or a complete relay. Parameter errors remain nonzero.

Codex → Claude/Qwen examples:

```text
Use $claude-review to review the plan .claude/plans/20260101-task.md
Use $codex-relay to complete the following task: ...
```

`./install.sh --with-hook` additionally adds a SessionStart hook that announces the current relay
mode. Links and imports are absolute paths: after moving the checkout, run `./install.sh` again.

## Model extensions

The reviewer is not limited to Codex. With Claude as the primary model, two review channels are
built in:

| Channel | Config name | Command | Notes |
|---------|-------------|---------|-------|
| Codex review | `codex` | `/codex-review` | Cross-model review; first by default |
| Claude self-review | `same-family` | `/opus-review` | Several Claude subagents review from different perspectives; same-family, so it runs only when every cross-model channel is unavailable |

Other models join as **plugins** under `plugins/<name>/`: install one with
`./install.sh --with-plugin <name>`, then add its name to `review.channels` in your config — its
position there is its place in the fallback order. The first plugin, `qwen`, drives any
Anthropic-compatible endpoint through `claude -p`; the model name and endpoint are set in config,
so switching models means editing config, not code. The contract for writing a plugin is in
[docs/PLUGINS.md](docs/PLUGINS.md).

### Using cheaper models for review

Review gets its value from **a different stance and a different set of blind spots**, not from the
strongest model. The reviewer only reads the repository and looks for flaws in the plan; it writes
no code, so a cheaper model or a flat-rate subscription is usually enough. Common setups:

- **Cost first:** put the plugin first, e.g. `["qwen", "codex", "same-family"]`, so everyday reviews
  use the cheaper model and fall back to Codex when it is unavailable.
- **Quality first, plugin as backup:** keep `["codex", "qwen", "same-family"]`, so when Codex runs
  out of quota the review falls back to the plugin instead of going straight to Claude
  self-review, which is the most expensive channel and only a same-family perspective.

Whatever the order, same-family review only runs when every cross-model channel is unavailable.

### Example: the qwen plugin

1. Install the plugin: `./install.sh --with-plugin qwen`
2. Add the plugin settings to `~/.config/ai-anti/config.json` and put `qwen` into the channel order:

   ```json
   {
     "review": {"channels": ["codex", "qwen", "same-family"]},
     "plugins": {
       "qwen": {
         "model": "qwen3.8-max",
         "base_url": "https://<your anthropic-compatible endpoint>",
         "credential_paths": ["~/.config/ai-anti/secrets.json"]
       }
     }
   }
   ```

3. Put the API key in the credential file `~/.config/ai-anti/secrets.json` and restrict it with
   `chmod 600`:

   ```json
   {"tokenplan": {"value": "<your API key>"}}
   ```

   The plugin reads `tokenplan.value` from each file in `credential_paths`, in order, and uses the
   first non-empty value. Use absolute or `~`-prefixed paths (relative paths resolve against the
   current directory at call time). If config or credentials are missing, the channel is marked
   unavailable and the review falls back to the next one; it never makes a half-configured call.

### Verified reviewer models

The list of verified reviewer models and how far each was tested is in
[docs/COMPATIBILITY.md](docs/COMPATIBILITY.md#实测过的评审模型); models only checked for endpoint
reachability are marked separately. Other vendors' Anthropic-compatible endpoints should work the
same way but have not been tested; do one minimal call before using one for real reviews.

### The Codex-led path

The `review.channels` order only routes the Claude-led path. Codex selects
`--channel claude|qwen`, then `codex_relay.review_channel`, defaulting to `claude`.
Failures never automatically fall back. Claude defaults to Opus; `--model fable` or
configured families remain supported. Qwen directly reuses `plugins.qwen` without
plugin installation, defaulting to Claude CLI with a sonnet alias mapped to the configured
model. Explicit `--model` is rejected for Qwen. Relative credential paths resolve
against `--repo`; the Claude plugin resolves them against invocation cwd. Prefer
absolute paths or `~`. `--prepare-only` reads no credentials and calls no CLI.
Qwen defaults to 1800 seconds per stage, Claude to 600, both to 300 seconds idle.
Explicit overrides win; caps are 3600 seconds total and 900 idle. Model evidence
and exact init matching do not prove endpoint-internal routing.

```bash
REVIEW_SOURCE=/absolute/path/to/ai-anti/adapters/codex/skills/claude-review/scripts/review.py
python3 "$REVIEW_SOURCE" diff --channel qwen --repo /path/to/repo \
  --files src/a.py --scale 'single user local tool' --out /private/tmp/qwen-review
```

Qwen can explicitly use Codex CLI's Responses executor without Claude CLI for that review:

```bash
python3 "$REVIEW_SOURCE" diff --channel qwen --qwen-executor codex \
  --responses-base-url https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1 \
  --idle-timeout 900 --timeout 1800 --repo /path/to/repo --files src/a.py \
  --scale 'single user local tool' --out /private/tmp/qwen-codex-review
```

Persist the selection with `codex_relay.qwen_executor=codex` and the separate
`plugins.qwen.responses_base_url`. The Anthropic `base_url` stays separate; model and
credential paths are reused. This executor requires verified Codex CLI 0.160.0 and
explicitly requests medium effort; other CLI versions stop at preflight. It records
`actual_model=null` and `model_evidence=requested_only`, not proven serving-model identity.
Formal plan and actual diff calls passed. Large diffs can still remain silent for minutes;
900/1800 seconds is the tested budget, not a latency guarantee. The macOS settings display a fixed executor for each supported combination; the CLI still supports explicit executor selection. See [verification and limits](docs/COMPATIBILITY.md).

AIAnti settings use Codex CLI for Codex-led API reviews, Claude Code CLI for Claude-led API reviews, and Codex CLI for Claude-to-Codex reviews. The executor field is disabled. Legacy Codex API executor changes are staged and require an explicit save before checks can run. Anthropic and Responses URLs are stored independently, with only the active protocol shown. Codex checks confirm the requested model only; the server's actual model remains unknown. Synthetic API checks allow 1800 seconds, 900 seconds idle and 8 MiB output, with 1860/1900-second outer cleanup budgets and cancellation. DeepSeek's full workflow remains unverified.

## Configuration

Optional user config: `~/.config/ai-anti/config.json` (template:
[config.example.json](config.example.json)). The installer never creates or edits it. A missing file
or key means the built-in default; a file that is not valid JSON stops the workflow instead of
silently falling back.

| Key | Meaning | Default |
|-----|---------|---------|
| `review.channels` | Claude-led review channel order only | `["codex", "same-family"]` |
| `defaults.deployment_scale` | Deployment scale passed to reviewers when a project does not declare one | unset → reviewers are told "deployment scale not declared" |
| `claude_review.model_families` (Claude channel only) | `--model` values accepted by `$claude-review` and the regex their actual model IDs must fully match | `opus`, `fable` |
| `codex_relay.review_channel` | Codex-led reviewer; explicit CLI selection wins | `claude` |
| `codex_relay.secondary_model` | Model requested for the implementer in `$codex-relay` | unset → specify per task |
| `codex_relay.qwen_executor` | CLI executor for Qwen review | `claude` |
| `plugins.qwen.responses_base_url` | Separate Responses URL for the Codex executor | none |
| `plugins.<name>` | Plugin settings, e.g. qwen `model`, `base_url`, `credential_paths` | none |

Credentials live outside the repository, e.g. `~/.config/ai-anti/secrets.json`; plugins only read
the paths you configure.

## Compatibility

Verified versions:

| Component | Verified versions |
|-----------|-------------------|
| Claude Code | 2.1.258, 2.1.280 |
| Codex CLI | 0.156.0 |
| Python | 3.9 or later |

[compat.json](compat.json) is the authoritative list, and `./install.sh --status` checks your
local versions against it. Details are in [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md). Other
versions generally produce a warning; the Qwen Responses executor separately requires
0.160.0, which is not yet in the general list. Only macOS has been verified.

## Cost

With the relay, every task that goes through it adds calls to external review models, so token
usage goes up:

- **Claude → Codex:** each plan-review round calls Codex once, and the final check may add one diff
  review. Falling back to a plugin or to Claude self-review uses that service's quota instead
  (Claude self-review runs several subagents in parallel and is the most expensive channel).
- **Codex → Claude/Qwen:** each `$claude-review` plan-review round calls the selected Claude/Qwen model twice (an independent
  inventory first, then the plan review); a diff review calls it once.

Both paths cap review at two rounds. You can switch the reviewer to a cheaper model; see
[Using cheaper models for review](#using-cheaper-models-for-review).

## Uninstall

```bash
./install.sh --uninstall
```

Removes only what the manifest (`~/.claude/ai-anti.manifest.json`) records and still points into
this checkout, plus its own marker blocks in `~/.claude/CLAUDE.md` and Codex `AGENTS.md`. Your own files and hooks are left
untouched. Individual parts: `--without-hook`, `--without-codex`, `--remove-plugin <name>`.

## Security

See [SECURITY.md](SECURITY.md): how external CLIs are sandboxed, how credentials are read, what the
installer writes, and how to report a vulnerability.

## License

[Apache-2.0](LICENSE)
