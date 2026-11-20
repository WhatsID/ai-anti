#!/usr/bin/env python3
"""Run one bounded, read-only Claude planning stage in a repository snapshot."""
from collections import deque
from functools import partial
import errno
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'adapters/codex/skills/claude-review/scripts'))

from ai_anti_config import ConfigError, agy_dual_plan, agy_dual_plan_defaults, load, model_families, review_limits
from plan_staging import (
    CAP, MAX_PROMPT_BYTES, SEMVER_RE, assemble_prompt,
    capture_integrity_state, diff_integrity_state, digest_bytes, digest_file, git_env,
    interruption_signal, is_cleanup_failure, make_tree_readonly, plan_overlay, planning_parser,
    prepare_materials, prepare_staging, remove_tree_readonly_safe, rewrite_staging_paths,
    run_git, validate_prompt, write_json, write_plan_atomic,
)
from review_runner import ALLOWED, MODEL_FAMILIES, RunError, blocks, builtin_plugins_only, execute

PLAN_MODEL_FAMILIES = {
    **MODEL_FAMILIES,
    'sonnet': re.compile(r'claude-sonnet-[0-9]+(?:-[0-9]+)*'),
    'haiku': re.compile(r'claude-haiku-[0-9]+(?:-[0-9]+)*'),
}
FULL_CLAUDE_MODEL_RE = re.compile(r'claude-[a-z0-9]+(?:-[a-z0-9._]+)+', re.ASCII)
FLAGS = [
    '--safe-mode', '--restricted', '--tools', 'Read,Glob,Grep',
    '--allowedTools', 'Read,Glob,Grep', '--permission-mode', 'dontAsk',
    '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
    '--disable-slash-commands', '--no-session-persistence', '--verbose',
    '--output-format', 'stream-json',
]


def preflight(out, staging, flags, evidence):
    cli = shutil.which('claude')
    if not cli:
        raise ConfigError('Claude CLI unavailable')
    cli = str(Path(cli).absolute())
    for flag in ('--version', '--help'):
        log = out / ('claude-' + flag[2:] + '.txt')
        code = execute([cli, flag], staging, log, 20, 15, 65536, env=git_env())
        if code:
            raise ConfigError(f'Claude capability preflight failed: {flag} (exit {code})')
        output = log.read_text(encoding='utf-8')
        if flag == '--version':
            match = SEMVER_RE.search(output)
            if not match:
                raise ConfigError('could not parse semver from Claude --version output')
            evidence['cli_version'] = match.group(1)
        else:
            advertised = set(re.findall(r'(?<![\w-])--[\w-]+', output))
            missing = [item for item in flags if item.startswith('--') and item not in advertised]
            if missing:
                raise ConfigError('Claude capability mismatch: missing ' + ', '.join(missing))

    try:
        tested = json.loads((ROOT / 'compat.json').read_text(encoding='utf-8'))['tested']['claude']
        if not isinstance(tested, list):
            raise ValueError('tested.claude is not a list')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        evidence['warnings'].append({'kind': 'compat_unavailable', 'detail': str(exc)})
    else:
        if evidence['cli_version'] not in tested:
            evidence['warnings'].append({'kind': 'untested_cli_version', 'cli': 'claude',
                                         'version': evidence['cli_version'], 'tested': tested})
    return cli


def check_tool_path(raw, staging, field):
    def violation():
        return RunError(f'{field} is invalid or escapes the staging snapshot: {raw!r}', 'integrity_violation')

    if not isinstance(raw, str) or not raw or '\0' in raw or raw.startswith('~'):
        raise violation()
    try:
        path = Path(raw)
        if path.is_absolute():
            path = path.relative_to(staging)
        parts = deque(path.parts)
        current = staging
        symlinks = 0
        # Check each hop so a path cannot leave staging and then re-enter it.
        while parts:
            part = parts.popleft()
            if part == '..':
                if current == staging:
                    raise violation()
                current = current.parent
                continue
            candidate = current / part
            if candidate.is_symlink():
                symlinks += 1
                if symlinks > 40:
                    raise violation()
                target = Path(os.readlink(candidate))
                if target.is_absolute():
                    target = target.relative_to(staging)
                    current = staging
                parts.extendleft(reversed(target.parts))
            else:
                current = candidate
    except (OSError, ValueError) as exc:
        raise violation() from exc


def check_tool_boundaries(event, staging):
    pending = [event]
    while pending:
        entry = pending.pop()
        if isinstance(entry, list):
            pending.extend(entry)
        if not isinstance(entry, dict):
            continue
        if entry.get('type') == 'tool_use':
            name = entry.get('name')
            if not isinstance(name, str) or name not in ALLOWED:
                raise RunError(f'disallowed tool invocation: {name!r}', 'integrity_violation')
            inputs = entry.get('input')
            if not isinstance(inputs, dict):
                raise RunError(f'invalid {name} input', 'integrity_violation')
            field = 'file_path' if name == 'Read' else 'path'
            path = inputs.get(field)
            if name == 'Read' or (path is not None and path != ''):
                check_tool_path(path, staging, f'{name}.{field}')
        result = entry.get('tool_use_result')
        if isinstance(result, dict):
            for item in (result, result.get('file')):
                if isinstance(item, dict) and 'filePath' in item:
                    check_tool_path(item['filePath'], staging, 'tool_use_result.filePath')
        pending.extend(entry.values())


def stream_events(logfile, *, tolerate_malformed=False):
    with logfile.open('rb') as handle:
        while True:
            line = handle.readline(CAP + 1)
            if not line:
                return
            if len(line) > CAP:
                if not tolerate_malformed:
                    raise RunError('oversized stream-json event')
                while line and not line.endswith(b'\n'):
                    line = handle.readline(CAP + 1)
                continue
            try:
                yield json.loads(line.decode('utf-8'))
            except (ValueError, RecursionError) as exc:
                if not tolerate_malformed:
                    raise RunError('truncated or malformed stream-json') from exc


def scan_tool_boundaries(logfile, staging):
    if logfile.is_file():
        for event in stream_events(logfile, tolerate_malformed=True):
            check_tool_boundaries(event, staging)


def check_init(event, requested, family):
    tools = event.get('tools')
    if (not isinstance(tools, list) or any(not isinstance(tool, str) for tool in tools)
            or set(tools) != ALLOWED or event.get('mcp_servers') != []
            or not builtin_plugins_only(event.get('plugins'))
            or event.get('permissionMode') != 'dontAsk'
            or event.get('skills') != [] or event.get('slash_commands') != []):
        raise RunError('unsafe or missing init capability set')
    actual = event.get('model')
    if (not isinstance(actual, str) or not actual.strip()
            or (family is not None and not family.fullmatch(actual))
            or (family is None and requested is not None and actual != requested)):
        raise RunError(f'unexpected actual model: {actual!r}')
    return actual


def record_message(event, actual, family, seen_ids, pending, tool_warnings):
    kind = event['type']
    message_blocks = blocks(event)
    allowed = ('text', 'thinking', 'tool_use') if kind == 'assistant' else ('tool_result',)
    if (not message_blocks or event['message'].get('role', kind) != kind
            or any(block.get('type') not in allowed for block in message_blocks)):
        raise RunError('unexpected message block or role')
    if kind == 'assistant' and 'model' in event['message']:
        model = event['message']['model']
        if not isinstance(model, str) or not (family.fullmatch(model) if family is not None else model == actual):
            raise RunError(f'unexpected assistant model: {model!r}')
    for block in message_blocks:
        if block['type'] == 'tool_use':
            uid = block.get('id')
            if not isinstance(uid, str) or not uid or uid in seen_ids:
                raise RunError('invalid or duplicate tool id')
            seen_ids.add(uid)
            pending[uid] = block['name']
        if block['type'] == 'tool_result':
            uid = block.get('tool_use_id')
            if not isinstance(uid, str) or uid not in pending:
                raise RunError('unmatched or duplicate tool result')
            name = pending.pop(uid)
            error = block.get('is_error', False)
            if type(error) is not bool:
                raise RunError('malformed tool error flag')
            if error:
                tool_warnings.append({'kind': 'tool_error', 'tool': name, 'tool_use_id': uid})


def validate_stream(logfile, code, staging, evidence, requested, family):
    actual = response = None
    ui_invalidated = False
    seen_ids = set()
    pending = {}
    for event in stream_events(logfile):
        if not isinstance(event, dict):
            raise RunError('non-object stream event')
        check_tool_boundaries(event, staging)
        kind, subtype = event.get('type'), event.get('subtype')
        info = event.get('rate_limit_info')
        status = info.get('status') if isinstance(info, dict) else event.get('status')
        if event.get('error') == 'rate_limit' or (kind == 'rate_limit_event' and status == 'rejected'):
            raise RunError('Claude quota exhausted', 'quota_exhausted')
        if response is not None:
            raise RunError('unexpected event after terminal result')
        if event.get('is_error') or event.get('permission_denials'):
            raise RunError('incomplete planning: error or permission denial')
        if kind == 'system' and subtype == 'ui_invalidate' and actual is None and not ui_invalidated:
            if event.get('event') != 'ui.render':
                raise RunError('unknown system event')
            ui_invalidated = True
        elif kind == 'system' and subtype == 'init' and actual is None:
            actual = check_init(event, requested, family)
            evidence.update(actual_model=actual, model_evidence='actual', conversation_id=event.get('session_id'))
        elif actual is None:
            raise RunError('missing or invalid init event')
        elif kind == 'rate_limit_event' and status == 'allowed':
            evidence['warnings'].append({'kind': 'rate_limit_event', 'status': 'allowed'})
        elif kind == 'system' and subtype == 'thinking_tokens':
            estimates = (event.get('estimated_tokens'), event.get('estimated_tokens_delta'))
            if any(type(value) is not int or value < 0 for value in estimates):
                raise RunError('invalid thinking tokens event')
            evidence['warnings'].append({'kind': 'thinking_tokens'})
        elif kind in ('assistant', 'user'):
            record_message(event, actual, family, seen_ids, pending, evidence['tool_warnings'])
        elif kind == 'result':
            response = event.get('result')
            if (subtype != 'success' or event.get('is_error') is not False
                    or event.get('permission_denials') != [] or not isinstance(response, str)
                    or not response.strip() or pending):
                raise RunError('incomplete or malformed planning result')
        else:
            raise RunError('unknown stream event')
    if code:
        raise RunError(f'nonzero CLI exit: {code}')
    if actual is None or response is None:
        raise RunError('expected exactly one init and terminal result')
    return response


def resolve_run_paths(args):
    if args.max_snapshot_bytes <= 0:
        raise ConfigError('--max-snapshot-bytes must be a positive integer')
    repo = args.repo.resolve(strict=True)
    if not repo.is_dir():
        raise ConfigError(f'repository path must be a directory: {repo}')
    top = Path(run_git(repo, 'rev-parse', '--show-toplevel').strip()).resolve()
    if repo != top:
        raise ConfigError('repo must be the root of a Git repository')
    if args.out.exists() or args.out.is_symlink():
        raise ConfigError(f'output directory already exists: {args.out}')
    out = args.out.resolve()
    if out == repo or repo in out.parents:
        raise ConfigError('output directory must be outside the repository')
    return repo, out


def run(args, signal_state):
    config = load(args.config)
    dual = agy_dual_plan(config)
    if args.trigger == 'config':
        if dual is None or not dual['enabled']:
            raise ConfigError('agy_relay.dual_plan must be configured with enabled=true')
        if args.stage == 'synthesize' and dual['synthesizer'] != 'claude':
            raise ConfigError('agy_relay.dual_plan.synthesizer must be claude for --stage synthesize')
    dual = dual or agy_dual_plan_defaults()
    requested = dual['model']
    families = model_families(config, PLAN_MODEL_FAMILIES)
    family = families.get(requested)
    if requested is not None and family is None and not FULL_CLAUDE_MODEL_RE.fullmatch(requested):
        raise ConfigError('agy_relay.dual_plan.model must be a supported alias ('
                          + ', '.join(sorted(families)) + '), a full claude-... model ID, '
                          'or an alias registered in claude_review.model_families')
    timeout, idle = review_limits(config, args.timeout, args.idle_timeout)
    repo, out = resolve_run_paths(args)
    head = run_git(repo, 'rev-parse', '--verify', 'HEAD^{commit}').strip()
    prompt, inputs, hashes, extras, warnings = prepare_materials(args, repo, partial(assemble_prompt, claude=True))
    prompt_bytes = validate_prompt(prompt).encode('utf-8')
    if len(prompt_bytes) > MAX_PROMPT_BYTES:
        raise ConfigError(
            f'assembled prompt ({len(prompt_bytes)} bytes) exceeds the limit ({MAX_PROMPT_BYTES} bytes); '
            'shorten the drafts or synthesize with AGY'
        )
    deletes, overlays, dirty = plan_overlay(repo, extras, args.max_snapshot_bytes)

    started = time.monotonic()
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    staging, log, stderr, plan = (out / name for name in ('.staging', 'plan.jsonl', 'plan.stderr.txt', 'plan.md'))
    evidence = {
        'provider': 'claude', 'stage': args.stage, 'trigger': args.trigger,
        'requested_model': requested, 'requested_effort': dual['effort'],
        'actual_model': None, 'model_evidence': 'requested_only', 'cli_version': None,
        'conversation_id': None, 'repo': str(repo), 'head': head, 'input_sha256': hashes,
        'prompt_bytes': len(prompt_bytes), 'prompt_sha256': digest_bytes(prompt_bytes),
        'plan_file': None, 'plan_sha256': None, 'log': log.name, 'log_sha256': None,
        'stderr_log': stderr.name, 'stderr_sha256': None, 'warnings': warnings, 'tool_warnings': [],
        'on_failure': dual['on_failure'], 'fallback_allowed': False, 'integrity_ok': True,
        'duration_seconds': 0, 'cli_exit_code': None, 'exit_code': None,
        'status': 'running', 'completed': False,
        'limits': {'timeout': timeout, 'idle_timeout': idle, 'max_output_bytes': CAP,
                   'max_snapshot_bytes': args.max_snapshot_bytes, 'max_prompt_bytes': MAX_PROMPT_BYTES},
    }

    def write_evidence():
        write_json(out / 'manifest.json', evidence)
        write_json(out / 'status.json', evidence)

    before = response = failure = child_rc = None
    phase = 'preparation'
    integrity = []
    try:
        prompt_path = out / 'prompt.txt'
        prompt_path.write_bytes(prompt_bytes)
        prompt_path.chmod(0o600)
        prepare_staging(repo, staging, deletes, overlays, args.max_snapshot_bytes)
        make_tree_readonly(staging)
        before = capture_integrity_state(repo, dirty, extras, inputs, staging)
        for path, (kind, _) in before['staging'].items():
            if kind == 'symlink':
                check_tool_path('./' + path, staging, 'staging symlink')
        write_evidence()

        phase = 'preflight'
        flags = list(FLAGS)
        if requested is not None:
            flags += ['--model', requested]
        if dual['effort'] is not None:
            flags += ['--effort', dual['effort']]
        cli = preflight(out, staging, flags, evidence)

        phase = 'execution'
        child_rc = execute([cli, '-p', prompt, *flags], staging, log, timeout, idle, CAP,
                           env=git_env(), stderr_log=stderr)
        evidence['cli_exit_code'] = child_rc
        response = validate_stream(log, child_rc, staging, evidence, requested, family)
    except (RunError, OSError, ValueError, KeyboardInterrupt, subprocess.TimeoutExpired) as exc:
        failure = exc
        if is_cleanup_failure(exc):
            integrity.append(str(exc))
    finally:
        try:
            scan_tool_boundaries(log, staging)
        except (RunError, OSError, KeyboardInterrupt) as exc:
            integrity.append(f'Claude tool boundary verification failed: {exc}')
        if before is not None:
            try:
                after = capture_integrity_state(repo, dirty, extras, inputs, staging)
                integrity.extend(diff_integrity_state(before, after, label='Claude'))
            except (OSError, ValueError, KeyboardInterrupt, subprocess.TimeoutExpired) as exc:
                integrity.append(f'post-run integrity verification failed: {exc}')
        try:
            remove_tree_readonly_safe(staging)
        except (OSError, KeyboardInterrupt) as exc:
            integrity.append(f'staging cleanup failed: {exc}')

    try:
        evidence.update(log_sha256=digest_file(log), stderr_sha256=digest_file(stderr))
        if stderr.is_file() and stderr.stat().st_size:
            evidence['warnings'].append({'kind': 'non_empty_stderr', 'bytes': stderr.stat().st_size})
        if not integrity and failure is None and interruption_signal(signal_state, child_rc=child_rc) is None:
            response, rewritten = rewrite_staging_paths(response, staging)
            if rewritten:
                warnings.append({'kind': 'staging_path_rewritten', 'count': rewritten})
            data = (response if response.endswith('\n') else response + '\n').encode('utf-8')
            write_plan_atomic(plan, data)
            evidence.update(plan_file=plan.name, plan_sha256=digest_bytes(data))
    except (OSError, ValueError, KeyboardInterrupt) as exc:
        failure = failure or exc

    signum = interruption_signal(signal_state, failure, child_rc)
    cause = failure.__cause__ if isinstance(failure, RunError) else failure
    if integrity:
        evidence.update(status='integrity_aborted', integrity_ok=False, exit_code=3,
                        error_kind='integrity_violation', error='; '.join(dict.fromkeys(integrity)))
    elif signum is not None:
        evidence.update(status='interrupted', exit_code=128 + signum,
                        error_kind='interrupted', error='Claude planning interrupted')
    elif isinstance(cause, OSError) and cause.errno == errno.E2BIG:
        evidence.update(status='failed', exit_code=2, error_kind='prompt_too_large',
                        error=f'Claude could not start with a {len(prompt_bytes)}-byte prompt: {failure}; '
                              'shorten the drafts or synthesize with AGY')
    elif failure is not None and phase != 'execution':
        evidence.update(status='invalid_config', exit_code=2, error_kind='invalid_config', error=str(failure))
    elif failure is not None:
        evidence.update(status='failed', exit_code=1, fallback_allowed=True,
                        error_kind=getattr(failure, 'kind', 'execution_failed'), error=str(failure))
    else:
        evidence.update(status='completed', exit_code=0, completed=True)
    if not evidence['completed']:
        plan.unlink(missing_ok=True)
        evidence.update(plan_file=None, plan_sha256=None)
    evidence['duration_seconds'] = round(time.monotonic() - started, 3)
    write_evidence()
    return evidence


def build_parser():
    return planning_parser(__doc__)


def main():
    signal_state = {}

    def interrupt(signum, _frame):
        if not signal_state:
            signal_state['signum'] = signum
            raise KeyboardInterrupt('Claude planning interrupted')

    handlers = {signum: signal.signal(signum, interrupt) for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        try:
            evidence = run(build_parser().parse_args(), signal_state)
        except KeyboardInterrupt as exc:
            evidence = dict(status='interrupted', error_kind='interrupted', error=str(exc),
                            exit_code=128 + interruption_signal(signal_state, exc))
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            evidence = dict(status='invalid_config', error_kind='invalid_config', error=str(exc), exit_code=2)
        summary = dict(completed=False, fallback_allowed=False, integrity_ok=True, plan_file=None)
        summary.update({key: evidence[key] for key in (
            'status', 'stage', 'completed', 'fallback_allowed', 'integrity_ok', 'exit_code',
            'plan_file', 'actual_model', 'model_evidence', 'error_kind', 'error',
        ) if key in evidence})
        print(json.dumps(summary, ensure_ascii=False))
        return summary['exit_code']
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)


if __name__ == '__main__':
    sys.exit(main())
