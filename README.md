# ai-anti

English | [简体中文](README.zh-CN.md)

Cross-model relay for AI-assisted coding: one model plans, a **different** model reviews it
adversarially, a separate implementer writes the code, and the primary model does the final check.

## What problem it solves

When a single model does "plan → write code → accept" on its own, its blind spots travel all the way
into the result. ai-anti splits that chain across roles with different stances:

1. **Plan**: the primary model writes a plan file and keeps your original request verbatim.
2. **Cross review**: another model reads the repository (read-only) from the stance "someone else
   wrote this plan" and looks for flaws that would cause rework.
3. **Triage**: the primary model rules on every finding as must-fix, register for later, or reject,
   so review rounds do not explode.
4. **Implement**: a separate implementer carries out the settled plan.
5. **Final check**: the primary model reviews the whole increment against the plan and reports.

The point is not more models doing more work, but a second model with a different stance finding the
mistakes the first one cannot see.

## How it works

### What the installer does

`install.sh` copies nothing into your projects. It only places **symlinks** and **a small import
block** in your home directory, all pointing back into this checkout:

| Where | Points to | Purpose |
|-------|-----------|---------|
| `ai-anti` marker block in `~/.claude/CLAUDE.md` | `@adapters/claude-code/CLAUDE.global.md` | Claude Code loads it in every session, which brings the rules into context |
| `~/.claude/commands/*.md` | `adapters/claude-code/commands/` | Slash commands: `/codex-review`, `/codex-implement`, `/opus-review`, `/relay-mode`, `/bug-review`, `/disable-dual-plan` |
| `~/.claude/agents/*.md` | `adapters/claude-code/agents/` | Subagents: `implementer`, `adversarial-reviewer` (same-family review) |
| Marker block in `${CODEX_HOME:-~/.codex}/AGENTS.md` (`--with-codex`) | `adapters/codex/AGENTS.global.md` | Codex user-level entry |
| `~/.agents/skills/*` (`--with-codex`) | `adapters/codex/skills/` | Codex skills: `$claude-review`, `$codex-relay`, `$bug-review` |
| `~/.gemini/config/plugins/ai-anti` (`--with-agy`) | `adapters/agy/plugin` | AGY plugin |

Every installed object is recorded in `~/.claude/ai-anti.manifest.json`, and uninstall removes only
what it recorded. Because these are symlinks, a `git pull` of this checkout takes effect in the **next
session**. After moving the checkout, run `./install.sh` again.

### How it takes effect in a session

Using Claude Code as an example:

```text
New session
  └─ Claude Code loads ~/.claude/CLAUDE.md
       └─ @import CLAUDE.global.md ── @import core/WORKFLOW_ENTRY.md
            └─ the model now knows: relay mode, task sizing, high-risk triggers
You make a request
  ├─ small task → done directly
  └─ large or high-risk task → relay:
       plan + verbatim request (.claude/plans/)
         (with dual planning: AGY drafts independently, then the drafts are merged)
       → /codex-review: the command's script reads ~/.config/ai-anti/config.json
         and runs codex exec in a read-only sandbox; results land in a scratch dir
       → triage → user-visible differences from your request are confirmed with you
       → implement: implementer subagent, or Codex in an isolated git worktree
       → final check of the increment → report (commits only when you say so)
```

Codex follows the same chain: `AGENTS.md` block → `AGENTS.global.md` → the same
`core/WORKFLOW_ENTRY.md`. When a relay is needed, it reads the `codex-relay` or `claude-review` skill,
whose scripts call Claude or Qwen for review. AGY loads the same entry through its plugin.

### Two layers of enforcement

- **Rules** (Markdown under `core/` and `adapters/`) tell the model when to run the relay and how
  each step works. They are **soft**: the model reads and follows them, so a successful install does
  not guarantee compliance.
- **Scripts** (`adapters/**/scripts/*.py`) wrap every external CLI call. They enforce read-only
  sandboxes, isolated worktrees, CLI version pins, timeouts and output caps, and event-stream
  validation, and fail closed on anything unexpected. This layer is **hard** and does not depend
  on the model.

Projects need no copied files. A project may override parameters in its own `CLAUDE.md`, such as
business high-risk triggers, where to register findings, or deployment scale; see the override table in
`adapters/claude-code/CLAUDE.global.md`.

**About language:** the rule texts (`core/`, `adapters/`) and the docs under `docs/` are written in
Chinese and are the only authority. The models read them without difficulty. This README is a summary.
`core/DESIGN_RATIONALE.md` records the real failure behind each rule.

## The relay

| Lead | Entry points | Review and implementation |
|------|--------------|---------------------------|
| Claude Code | `/codex-review plan <plan.md>`, `/codex-review diff` | Codex reviews in a read-only sandbox; the implementer subagent implements, or Codex in an isolated worktree if configured |
| Codex | `$claude-review`, `$codex-relay` | Claude or Qwen reviews; `$codex-relay` runs the full relay |
| AGY | `agy-relay`, `codex-review` | Codex reviews and implements in an isolated worktree; AGY does the final check |

- **Verbatim request.** Your words are kept in a sibling `.request.md`. Before implementation, every
  user-visible difference between the settled plan and your request is confirmed with you.
- **Triage.** Each finding must name a concrete failure scenario. A finding is *must-fix* only if it
  causes data loss or irreversible damage, breaks the core function of the request, or exposes data to
  unauthorized people. Other findings are *registered* and count as closed. Findings without a
  scenario are *rejected*.
- **Two-round limit.** Must-fix items still open after two rounds go to you.
- **Fallback.** Channels are tried in the configured order (default Codex → same-family).
  Same-family review runs only when every cross-model channel is unavailable, with extra compensating
  requirements. If none is available, ordinary tasks continue marked "not cross-reviewed", and
  high-risk tasks stop for you.

Full rules: `core/REVIEW_PRINCIPLES.md`.

### Dual planning (optional)

Review finds flaws after a plan exists. Dual planning brings in a second model while the plan is being
written, so the plan does not rest on a single perspective.

1. **Independent drafts.** The primary model writes its own draft while the partner drafts
   independently: AGY when Claude leads, Claude when AGY leads. The partner sees only your verbatim
   request, never the primary's draft or design leanings, and reads the code in an isolated read-only
   worktree frozen at the current commit, so your concurrent edits in the main tree cannot interfere.
2. **Synthesis.** The synthesizer (the primary by default, or the partner if configured) merges the
   drafts against repository facts and records which draft each design point came from and why. If the
   drafts differ substantively on product direction or user-visible behavior, it stops for your
   decision.
3. **Review as usual.** The merged plan then goes through cross review, triage and the rest.

Enable it with `claude_relay.dual_plan.enabled` (Claude-led) or `agy_relay.dual_plan.enabled`
(AGY-led). Optional settings cover the partner model, effort, synthesizer, and `on_failure`, which is
`stop` (ask you) or `fallback` (continue with a single plan). In Claude Code, `/disable-dual-plan` turns
it off for the current session, or you can ask for it on or off in your message. Dual planning costs one
extra partner call in quota and time; it pays off on tasks where direction matters or a plan could
easily go astray.

## Relay modes

| Mode | Behavior |
|------|----------|
| `solo` | No relay; the primary model does everything |
| `auto` | The primary model decides; the rules below are guidance only |
| `rules` (default) | Relay when any of the following holds: a new feature or module; an expected change to 3+ production files that affect runtime behavior (tests and pure docs excluded); a change spanning 2+ top-level subsystems; or a vague request that needs a plan first |
| `force` | Every substantive task uses the relay |

In both `rules` and `auto`, tasks that hit a **high-risk trigger** must use the relay. High-risk
triggers are changes to the relay mechanism itself, plus whatever a project lists in its `CLAUDE.md`,
such as database schema, deployment, or authentication.

Use `/relay-mode` to view or switch. The per-project mode lives in `.claude/relay-mode.local` at the
repository root; the global default is `relay.default_mode`.

## Install

### Step 1: install the model CLIs you want to use

ai-anti contains no models. It makes models collaborate by calling the command-line tools already
installed on your machine, **so a model whose CLI is not installed and logged in cannot take part.** You
need at least one tool to lead, and cross review needs a second model's CLI.

| Tool | Role in ai-anti | Install (macOS) | Then |
|------|-----------------|-----------------|------|
| [Claude Code](https://code.claude.com/docs/en/setup) | Leads; reviews when Codex leads | `curl -fsSL https://claude.ai/install.sh \| bash` | run `claude` to log in |
| [Codex CLI](https://github.com/openai/codex) | Leads; reviews and implements when Claude or AGY leads | `brew install --cask codex` or `npm install -g @openai/codex` | run `codex` to log in |
| [AGY (Antigravity CLI)](https://antigravity.google/docs/cli/install) | Leads; dual-planning partner when Claude leads | `curl -fsSL https://antigravity.google/cli/install.sh \| bash` | run `agy` to sign in with Google |

You also need macOS and Python 3.9+ (`python3 --version`). The official install pages linked above are
authoritative. The most common setup is "Claude Code leads, Codex reviews": install and log in to both.

### Step 2: install ai-anti

```bash
git clone https://github.com/WhatsID/ai-anti ~/ai-anti
cd ~/ai-anti
./install.sh                 # required: Claude Code as the primary model
./install.sh --with-codex    # optional: let Codex lead as well
./install.sh --with-agy      # optional: let AGY lead as well
./install.sh --with-hook     # optional: announce the relay mode at session start
./install.sh --status        # read-only health check
```

**`./install.sh` vs `--with-xxx`:** the installer installs the *rules a leading tool reads*, not the
models themselves.

- `./install.sh` installs rules, commands and subagents for **Claude Code** only, so Claude Code runs the
  relay when it leads.
- `--with-codex` / `--with-agy` add the same rules for **Codex / AGY**, so they run the relay when **you
  work in them directly**. If you only use Claude Code, you don't need them.
- If a tool is only **called** to review or implement (e.g. Codex reviewing for Claude) just needs its
  CLI from step 1; no `--with-xxx` required.
- Options can be added at any time; reinstalling keeps components already installed.

Then **open a new session**; the rules apply in any repository. First time? Walk through the
[minimal quickstart (Chinese)](docs/QUICKSTART.zh-CN.md). The installer's ownership rules, Codex and AGY
entry edge cases, what `--status` does and does not prove, and uninstall details are in
[install details (Chinese)](docs/INSTALL.zh-CN.md).

### Day-to-day use

**Talk to it exactly as you would to a single model; no special phrasing is needed.** For every task, the
leading tool decides from the relay mode and task size: small tasks are done directly, large or high-risk
tasks go through the relay automatically, and it tells you its decision before starting.

To change its decision for a particular run, just say so in your message:

| You say | Effect |
|---------|--------|
| "use the relay" | Always run the relay this time |
| "just change it" | Skip size-based relay; high-risk triggers are still reviewed |
| "skip review" | No review, even for high-risk tasks |

You can also call a single step by hand, e.g. `/codex-review plan <plan.md>` in Claude Code for one plan
review, or "Use $claude-review to review the plan ..." in Codex. These are optional.

## Configuration

The config file is optional: `~/.config/ai-anti/config.json` (template:
[config.example.json](config.example.json)). The installer never creates or edits it. Missing files or
keys mean built-in defaults; invalid JSON stops the workflow instead of silently falling back.
Credentials live outside the repository, e.g. `~/.config/ai-anti/secrets.json`.

| Key | Meaning | Default |
|-----|---------|---------|
| `relay.default_mode` | Global relay mode | `rules` |
| `review.channels` | Review channel order for the Claude-led path | `["codex", "same-family"]` |
| `review.codex_model` | Reviewer model for Claude-led `/codex-review` | Codex CLI default |
| `review.codex_effort` / `review.codex_high_risk_effort` | Reasoning effort for standard / high-risk reviews | CLI default / standard effort |
| `review.timeout` / `review.idle_timeout` | Total / idle timeout for external CLI reviews (seconds) | 1800 / 900 |
| `defaults.deployment_scale` | Deployment scale passed to reviewers when a project declares none | not declared |
| `claude_relay.implementation` | Let Codex implement in an isolated worktree on the Claude-led path, e.g. `{"provider":"codex","model":"…","effort":"high"}` | implementer subagent |
| `claude_relay.dual_plan` | Opt-in AGY dual planning on the Claude-led path; `/disable-dual-plan` turns it off for a session | off |
| `agy_relay.review` / `agy_relay.implementation` / `agy_relay.dual_plan` | AGY-led review, implementation and dual planning | see template |
| `codex_relay.review_channel` | Reviewer on the Codex-led path | `claude` |
| `codex_relay.implementation_provider` | Implementation source on the Codex-led path | `codex` |
| `plugins.<name>` | Plugin settings, e.g. qwen `model`, `base_url`, `credential_paths` | none |

Effort values are exactly `low`, `medium`, `high`, `xhigh`, `max`, or `ultra`; invalid values stop
before the CLI starts. When both tiers of a route are explicitly set, the high-risk tier must be at
least the standard tier. The review orchestrator picks the tier under R3; an omitted tier counts as
high-risk. All fields and values: the template and
`adapters/codex/skills/claude-review/scripts/ai_anti_config.py`.

## Review channels and plugins

A **review channel** is simply "who reviews the plan". Two channels are built in on the Claude-led path:

| Channel | Config name | Command | Notes |
|---------|-------------|---------|-------|
| Codex review | `codex` | `/codex-review` | Cross-model; first by default |
| Claude self-review | `same-family` | `/opus-review` | Several Claude subagents from different perspectives; same-family, so only when every cross-model channel is unavailable |

**What plugins are for:** they let models other than Codex act as reviewers. Without a plugin, the
Claude-led path can only ask Codex; when Codex is unavailable (not installed, out of quota) it falls back
to Claude reviewing itself. A plugin connects another vendor's model, such as Qwen, through any
Anthropic-compatible API, for two purposes:

- **Lower cost.** A reviewer only reads code and looks for flaws in the plan. Its value comes from a
  different stance and different blind spots, so a cheaper model is usually enough.
- **Backup.** When Codex runs out of quota, review falls back to the plugin first, instead of going
  straight to Claude self-review, which is the most expensive channel and only a same-family view.

Install with `./install.sh --with-plugin <name>`, then add the name to `review.channels`; its position is
its place in the fallback order. `["qwen", "codex", "same-family"]` puts cost first,
`["codex", "qwen", "same-family"]` puts quality first with the plugin as backup. The available plugin is
`qwen`; the contract for writing your own is in [docs/PLUGINS.md](docs/PLUGINS.md).

A plugin is only configuration and calling rules; requests are still sent by a CLI on your machine, so
**besides the API URL, model and key, you need an executor CLI installed**. By default the qwen plugin
borrows the Claude Code CLI (`claude -p`), redirecting it to your configured endpoint with your API key,
so it uses no Claude subscription quota. Alternatively set `plugins.qwen.claude_executor` to `codex` to
use Codex CLI's Responses executor (with `plugins.qwen.responses_base_url`). On the Claude-led path Claude
Code is already installed, so usually nothing extra is needed.

### Example: the qwen plugin

1. Install: `./install.sh --with-plugin qwen`
2. Configure `~/.config/ai-anti/config.json`:

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

3. Put the API key in `~/.config/ai-anti/secrets.json` and `chmod 600` it:

   ```json
   {"tokenplan": {"value": "<your API key>"}}
   ```

   The plugin uses the first non-empty `tokenplan.value` from `credential_paths`, in order; use
   absolute or `~`-prefixed paths. If config or credentials are missing, the channel counts as
   unavailable and review falls back to the next one, never making a half-configured call.

Verified reviewer models: [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md#实测过的评审模型).

## Advanced

Everything above is enough for daily use. This section covers four further needs: working mainly in
Codex, implementing with a cheaper model, getting a second opinion on an existing problem analysis, and
editing settings in a GUI. Skip it if none apply.

- **Codex-led path** (when you mainly work in Codex). The reviewer is chosen by `--channel claude|qwen`, then
  `codex_relay.review_channel`; failures never fall back automatically. Qwen can run through Claude CLI
  or Codex CLI's Responses executor (`codex_relay.qwen_executor`, `plugins.qwen.claude_executor`,
  `plugins.qwen.responses_base_url`). Usage and version pins: the
  [codex-relay skill](adapters/codex/skills/codex-relay/SKILL.md) and
  [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).
- **Codex-led implementation sources** (to write code with a cheaper model). Native Codex, Qwen Token Plan (`glm-5.3` / `qwen4-max` and other
  models), or a Qoder subscription. Qwen Token Plan supports a primary and a backup account with failover on quota exhaustion.
  Contracts and verification limits: the [codex-relay skill](adapters/codex/skills/codex-relay/SKILL.md)
  and [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).
- **Problem second opinions** (when debugging, to have another model check an existing analysis). This
  runs only when you explicitly ask for it; it never triggers on its own while debugging. After the model
  has produced a problem analysis, say "use /bug-review to check this analysis" in Claude Code, or "Use
  $bug-review to check ..." in Codex. The model packages your original words, its analysis and the
  evidence, and another model reviews them in two stages: first it sees only the problem and evidence and
  lists possible causes independently; then it checks the original analysis item by item as supported,
  needs verification, or rejected. The result is a second opinion and never changes code. The reviewer
  comes from `codex_relay.review_channel` (`claude`, `qwen` or `qoder`; default `claude`), not
  `review.channels`; if it is Claude while Claude Code leads, it counts only as same-family review. On
  the Codex side, run `./install.sh --with-codex` first to install the skill. Git is not required. See
  [docs/BUG_REVIEW.md](docs/BUG_REVIEW.md).
- **macOS menu bar app** (if you prefer not to edit JSON by hand). The optional [menu bar app](apps/macos/README.md) edits default models,
  review channels and relay mode, and runs local checks; business reviews still start from the CLI
  entries. See [the product notes](docs/MACOS_REVIEW_APP.md).

## Compatibility

| Component | Versions with path-scoped evidence |
|-----------|------------------------------------|
| Claude Code | 2.1.258, 2.1.280, 2.1.326 |
| Codex CLI | 0.156.0, 0.160.0, 0.163.0, 0.169.1 |
| AGY CLI | 1.5.13, 1.5.14 |
| Qoder CLI | 1.1.74, 1.1.77 |
| Base scripts | Python 3.9+; macOS tested, other systems unverified |
| Optional macOS menu bar app | macOS 13+, Swift 6.1, Python 3.11+ |

Different versions cover different paths. [compat.json](compat.json) is the machine-readable set; the
per-path evidence and limits in [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) are authoritative.
Unlisted versions may warn or be rejected by the wrappers' version pins.

## Cost

Every relayed task adds external model calls. On the Claude-led path each plan-review round calls Codex
once, and the final check may add a diff review. On the Codex-led path each plan-review round calls
the reviewer twice (an independent inventory, then the review). Review is capped at two rounds.
Same-family review runs several Claude subagents in parallel and is the most expensive channel.

## Uninstall

```bash
./install.sh --uninstall
```

Removes only objects recorded in the manifest that still point into this checkout, plus its own marker
blocks. To remove individual parts, see [install details (Chinese)](docs/INSTALL.zh-CN.md#卸载).

## Security

See [SECURITY.md](SECURITY.md) for per-path controls, data sent to model services, retained local
evidence, credentials, installer writes, and private vulnerability reporting.

## License

[Apache-2.0](LICENSE)
