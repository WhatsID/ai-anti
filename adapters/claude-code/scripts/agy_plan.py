#!/usr/bin/env python3
"""Run one bounded, read-only AGY planning stage (draft or synthesize).

Python 3.9 standard library only. Reuses ai_anti_config and review_runner.
Protects the real repository by executing AGY inside an isolated read-only
staging snapshot exported from HEAD with tracked working-tree edits overlaid.
"""
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
SKILLS = ROOT / 'adapters' / 'codex' / 'skills'
sys.path[:0] = [str(SKILLS / 'claude-review' / 'scripts')]

from ai_anti_config import ConfigError, claude_dual_plan, claude_dual_plan_defaults, load, review_limits
from review_runner import RunError, execute
from plan_staging import (
    CAP, MAX_PROMPT_BYTES, SEMVER_RE, assemble_prompt, capture_integrity_state, diff_integrity_state,
    digest_bytes, digest_file, interruption_signal, is_cleanup_failure, make_tree_readonly,
    omission_notice, plan_overlay, planning_parser, prepare_materials, prepare_staging,
    remove_tree_readonly_safe, repo_path_variants, rewrite_staging_paths, run_git, validate_prompt,
    write_json, write_plan_atomic,
)


WRITE_TOOLS = frozenset({
    'write_file',
    'write_to_file',
    'edit_file',
    'replace_file_content',
    'multi_replace_file_content',
    'sed_file',
    'notebook_edit',
    'notebook_execution',
    'delete_knowledge',
})

TOOL_ERROR_STATUSES = frozenset({
    'error',
    'failed',
    'failure',
    'denied',
    'rejected',
    'permission_denied',
    'cancelled',
    'canceled',
    'aborted',
})


def normalize_tool_name(name):
    if not isinstance(name, str):
        return ''
    lower = name.strip().lower()
    if lower.startswith('cortex_step_type_'):
        lower = lower[len('cortex_step_type_'):]
    if '.' in lower:
        lower = lower.rsplit('.', 1)[-1]
    if ':' in lower:
        lower = lower.rsplit(':', 1)[-1]
    return lower


def extract_tool_records(event):
    """Extract (tool_name, status, error_text) tuples from a stream-json event."""
    records = []

    def inspect_obj(obj, parent_status=None, parent_err=None):
        if not isinstance(obj, dict):
            return
        candidates = []
        for key in ('tool_name', 'tool', 'name', 'action'):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                candidates.append(val.strip())
        # Only treat `type` / `step_type` as a tool name when it looks like a concrete tool/step type
        for key in ('step_type', 'type'):
            tval = obj.get(key)
            if isinstance(tval, str) and (
                tval.strip().lower().startswith('cortex_step_type_')
                or normalize_tool_name(tval) in WRITE_TOOLS
            ):
                candidates.append(tval.strip())

        status = obj.get('status')
        if not isinstance(status, str) or not status.strip():
            status = obj.get('state')
        if not isinstance(status, str) or not status.strip():
            status = parent_status
        err = obj.get('error')
        is_err = obj.get('is_error') is True or obj.get('denied') is True or obj.get('permission_denied') is True
        if err and not isinstance(err, str):
            err = json.dumps(err, ensure_ascii=False)
        eff_err = err if err else ('denied/error' if is_err else parent_err)

        for name in candidates:
            records.append((name, status, eff_err))

        for nested_key in ('step_update', 'tool_info', 'step', 'tool_call', 'call', 'data', 'payload'):
            nested = obj.get(nested_key)
            if isinstance(nested, dict):
                inspect_obj(nested, status, eff_err)
        for list_key in ('tool_calls', 'steps', 'content', 'calls'):
            items = obj.get(list_key)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        inspect_obj(item, status, eff_err)

    inspect_obj(event)
    return records


def scan_log_for_write_tools(logfile):
    """Best-effort scan of plan.jsonl for disallowed write tools even on partial/failed runs."""
    if not logfile.is_file():
        return None
    try:
        text = logfile.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get('event') in ('init', 'result'):
            continue
        for tool_name, _, _ in extract_tool_records(event):
            if normalize_tool_name(tool_name) in WRITE_TOOLS:
                return tool_name
    return None


def validate_stream(logfile, cli_exit_code):
    try:
        raw_lines = logfile.read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeError) as exc:
        raise RunError('unable to read AGY stream-json log: ' + str(exc)) from exc

    events = []
    for line in raw_lines:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError as exc:
            raise RunError('truncated or malformed AGY stream-json') from exc
        if not isinstance(obj, dict) or not isinstance(obj.get('event'), str):
            raise RunError('malformed AGY stream-json event envelope')
        events.append(obj)

    # First check every non-init/non-result event for write tools before any exit-code failure
    tool_warnings = []
    warning_index = {}
    for ev in events:
        kind = ev.get('event')
        if kind in ('init', 'result'):
            continue
        for tool_name, status, err_text in extract_tool_records(ev):
            norm = normalize_tool_name(tool_name)
            if norm in WRITE_TOOLS:
                raise RunError(f'disallowed write tool invocation: {tool_name}', kind='integrity_violation')
            status_lower = status.lower() if isinstance(status, str) else ''
            if status_lower in TOOL_ERROR_STATUSES or err_text:
                key = (kind, tool_name, status)
                if key not in warning_index:
                    warning_index[key] = len(tool_warnings)
                    tool_warnings.append({
                        'event': kind,
                        'tool': tool_name,
                        'status': status,
                        'error': err_text,
                    })
                elif err_text and not tool_warnings[warning_index[key]]['error']:
                    tool_warnings[warning_index[key]]['error'] = err_text

    if cli_exit_code != 0:
        raise RunError(f'nonzero AGY CLI exit: {cli_exit_code}')

    if len(events) < 2:
        raise RunError('incomplete AGY stream-json: expected init and terminal result')

    # Validate first event is init (real AGY 1.5.13 nests permission_mode & expanded_commands under init)
    first = events[0]
    if first.get('event') != 'init':
        raise RunError('AGY stream-json must begin with init event')
    init_payload = first.get('init')
    if not isinstance(init_payload, dict):
        raise RunError('malformed AGY init payload')
    if init_payload.get('permission_mode') != 'request-review':
        raise RunError('unsafe AGY init permission_mode: ' + repr(init_payload.get('permission_mode')))
    expanded = init_payload.get('expanded_commands')
    has_plan_cmd = (
        isinstance(expanded, list)
        and any(
            isinstance(cmd, dict) and cmd.get('name') == 'plan' and cmd.get('type') == 'system'
            for cmd in expanded
        )
    )
    if not has_plan_cmd:
        raise RunError('AGY init expanded_commands missing required system plan command')

    conversation_id = first.get('conversation_id') if isinstance(first.get('conversation_id'), str) else None

    result_events = []
    for idx, ev in enumerate(events):
        kind = ev.get('event')
        if idx > 0 and kind == 'init':
            raise RunError('duplicate AGY init event')
        if kind == 'error':
            raise RunError('AGY stream error event: ' + str(ev.get('error', ev.get('message', 'unknown'))))
        if kind == 'result':
            result_events.append((idx, ev))
        elif result_events:
            raise RunError('unexpected event after terminal AGY result')

    if len(result_events) != 1 or result_events[0][0] != len(events) - 1:
        raise RunError('AGY stream-json must end with a single terminal result event')

    final_ev = result_events[0][1]
    res = final_ev.get('result')
    if not isinstance(res, dict):
        raise RunError('malformed AGY result payload')
    if res.get('status') != 'SUCCESS':
        raise RunError('AGY result status is not SUCCESS: ' + repr(res.get('status')))
    denied_actions = res.get('denied_actions')
    if isinstance(denied_actions, list) and denied_actions:
        for item in denied_actions:
            tool_warnings.append({'event': 'result', 'denied_action': item})
    response = res.get('response')
    if not isinstance(response, str) or not response.strip():
        if isinstance(denied_actions, list) and denied_actions:
            raise RunError('AGY returned empty response after denied actions: ' + json.dumps(denied_actions, ensure_ascii=False))
        raise RunError('AGY returned empty response')
    if isinstance(res.get('conversation_id'), str) and res['conversation_id']:
        conversation_id = res['conversation_id']
    elif isinstance(final_ev.get('conversation_id'), str) and final_ev['conversation_id']:
        conversation_id = final_ev['conversation_id']

    return {
        'response': response,
        'conversation_id': conversation_id,
        'tool_warnings': tool_warnings,
    }


def check_compat_version(version):
    compat_path = ROOT / 'compat.json'
    try:
        data = json.loads(compat_path.read_text(encoding='utf-8'))
        tested = data.get('tested', {}).get('agy', [])
        if not isinstance(tested, list):
            raise ValueError('tested.agy is not a list')
    except (OSError, ValueError, TypeError) as exc:
        return [{'kind': 'compat_unavailable', 'detail': str(exc)}]
    if version not in tested:
        return [{'kind': 'untested_cli_version', 'cli': 'agy', 'version': version, 'tested': tested}]
    return []


def run(args, signal_state=None):
    if signal_state is None:
        signal_state = {}
    # 1. Validate numeric limits
    if type(args.max_snapshot_bytes) is not int or args.max_snapshot_bytes <= 0:
        raise ConfigError('--max-snapshot-bytes must be a positive integer')

    # 2. Validate repository
    try:
        repo = args.repo.resolve(strict=True)
    except OSError as exc:
        raise ConfigError(f'repository path does not exist: {args.repo}') from exc
    if not repo.is_dir():
        raise ConfigError(f'repository path must be a directory: {repo}')
    toplevel = Path(run_git(repo, 'rev-parse', '--show-toplevel').strip()).resolve()
    if repo != toplevel:
        raise ConfigError('repo must be the root of a Git repository')
    head = run_git(repo, 'rev-parse', '--verify', 'HEAD^{commit}').strip()

    # 3. Validate output directory (must not exist, must not be symlink, must be outside repo)
    if args.out.exists() or args.out.is_symlink():
        raise ConfigError(f'output directory already exists: {args.out}')
    out = args.out.resolve()
    if out == repo or repo in out.parents:
        raise ConfigError('output directory must be outside the repository')

    # 4. Load and validate config (fail closed)
    config = load(args.config)
    dual = claude_dual_plan(config)
    if args.trigger == 'config':
        if dual is None or not dual['enabled']:
            raise ConfigError('claude_relay.dual_plan must be configured with enabled=true')
        if args.stage == 'synthesize' and dual['synthesizer'] != 'agy':
            raise ConfigError('claude_relay.dual_plan.synthesizer must be agy for --stage synthesize')
    else:
        dual = dual or claude_dual_plan_defaults()
    timeout, idle = review_limits(config, args.timeout, args.idle_timeout)

    payload, input_paths, input_sha256, extra_files, warnings = prepare_materials(args, repo, assemble_prompt)
    prompt_bytes = validate_prompt(payload).encode('utf-8')
    if len(prompt_bytes) > MAX_PROMPT_BYTES:
        raise ConfigError(
            f'assembled prompt ({len(prompt_bytes)} bytes) exceeds the limit ({MAX_PROMPT_BYTES} bytes); '
            'shorten the drafts or synthesize with Claude'
        )

    delete_set, overlay_files, dirty_paths, omissions = plan_overlay(
        repo, extra_files, args.max_snapshot_bytes, args.max_file_bytes, warnings=warnings,
    )
    payload += omission_notice(
        omissions, MAX_PROMPT_BYTES - len(prompt_bytes), repo_path_variants(repo, args.repo), warnings,
    )
    prompt_bytes = payload.encode('utf-8')
    print_arg = '--print=' + payload

    # 9. Create --out and isolated read-only staging snapshot
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    staging = out / '.staging'
    prompt_path = out / 'prompt.txt'
    version_log = out / 'agy-version.txt'
    log_path = out / 'plan.jsonl'
    stderr_path = out / 'plan.stderr.txt'
    plan_path = out / 'plan.md'

    evidence = {
        'provider': 'agy',
        'stage': args.stage,
        'trigger': args.trigger,
        'requested_model': dual['model'],
        'requested_effort': dual['effort'],
        'model_evidence': 'requested_only',
        'cli_version': None,
        'conversation_id': None,
        'repo': str(repo),
        'head': head,
        'input_sha256': input_sha256,
        'prompt_bytes': len(prompt_bytes),
        'prompt_sha256': digest_bytes(prompt_bytes),
        'plan_file': None,
        'plan_sha256': None,
        'log': log_path.name,
        'log_sha256': None,
        'stderr_log': stderr_path.name,
        'stderr_sha256': None,
        'warnings': warnings,
        'snapshot_omissions': omissions,
        'tool_warnings': [],
        'on_failure': dual['on_failure'],
        'fallback_allowed': False,
        'integrity_ok': True,
        'duration_seconds': 0,
        'cli_exit_code': None,
        'exit_code': None,
        'status': 'running',
        'completed': False,
        'limits': {
            'timeout': timeout,
            'idle_timeout': idle,
            'max_output_bytes': CAP,
            'max_snapshot_bytes': args.max_snapshot_bytes,
            'max_file_bytes': omissions['max_file_bytes'],
            'max_prompt_bytes': MAX_PROMPT_BYTES,
        },
    }

    started = time.monotonic()
    manifest_written = False
    integrity_check_finished = False
    staging_cleanup_attempted = False
    staging_cleanup_error = None
    child_rc = None
    run_exc = None
    validated = None
    integrity_reasons = []

    def cleanup_staging():
        nonlocal staging_cleanup_attempted, staging_cleanup_error
        try:
            remove_tree_readonly_safe(staging)
        except OSError as exc:
            staging_cleanup_error = f'staging cleanup failed: {exc}'
        staging_cleanup_attempted = True

    def finalize(exc=None):
        signum = interruption_signal(signal_state, exc) or interruption_signal(signal_state, run_exc, child_rc)
        failure = run_exc if run_exc is not None else exc
        cause = failure.__cause__ if isinstance(failure, RunError) else None
        reasons = list(integrity_reasons)
        if staging_cleanup_error is not None:
            reasons.append(staging_cleanup_error)
            if run_exc is not None:
                evidence['original_error'] = {
                    'error_kind': getattr(run_exc, 'kind', 'execution_failed'),
                    'error': str(run_exc),
                }
        evidence.update(
            duration_seconds=round(time.monotonic() - started, 3),
            completed=False,
            fallback_allowed=False,
            plan_file=None,
            plan_sha256=None,
        )
        if reasons:
            evidence.update(
                status='integrity_aborted',
                integrity_ok=False,
                exit_code=3,
                error_kind='integrity_violation' if integrity_reasons else 'staging_cleanup_failed',
                error='; '.join(dict.fromkeys(reasons)),
            )
        elif signum is not None:
            evidence.update(
                status='interrupted',
                exit_code=128 + signum,
                error_kind='interrupted',
                error='AGY planning interrupted',
            )
        elif isinstance(cause, OSError) and cause.errno == errno.E2BIG:
            evidence.update(
                status='failed',
                exit_code=2,
                error_kind='prompt_too_large',
                error=f'AGY could not start with a {len(prompt_bytes)}-byte prompt: {failure}; '
                      'shorten the drafts or synthesize with Claude',
            )
        elif failure is not None:
            evidence.update(
                status='failed',
                fallback_allowed=True,
                exit_code=1,
                error_kind=getattr(failure, 'kind', 'execution_failed'),
                error=str(failure),
            )
        else:
            evidence.update(
                status='completed',
                completed=True,
                exit_code=0,
                plan_file=plan_path.name,
                plan_sha256=plan_sha256,
            )
        if not evidence['completed']:
            plan_path.unlink(missing_ok=True)

    try:
        prompt_path.write_bytes(prompt_bytes)
        os.chmod(prompt_path, 0o600)
        prepare_staging(repo, staging, delete_set, overlay_files, args.max_snapshot_bytes, omissions, extra_files)
        make_tree_readonly(staging)
        pre_integrity = capture_integrity_state(repo, dirty_paths, extra_files, input_paths, staging)
    except BaseException:
        for path in (staging, out):
            try:
                remove_tree_readonly_safe(path)
            except OSError as exc:
                print(f'agy_plan warning: preparation rollback failed for {path}: {exc}', file=sys.stderr)
        raise

    try:
        try:
            write_json(out / 'manifest.json', evidence)
            manifest_written = True
            write_json(out / 'status.json', evidence)

            try:
                cli = shutil.which('agy')
                if not cli:
                    raise RunError('AGY CLI unavailable')

                child_rc = execute([cli, '--version'], staging, version_log, 20, 15, 65536)
                if child_rc != 0:
                    raise RunError(f'AGY version probe failed with exit code {child_rc}')
                vtext = version_log.read_text(encoding='utf-8')
                match = SEMVER_RE.search(vtext)
                if not match:
                    raise RunError('could not parse semver from AGY --version output')
                cli_version = match.group(1)
                evidence['cli_version'] = cli_version
                evidence['warnings'].extend(check_compat_version(cli_version))

                argv = [
                    cli,
                    '--mode', 'plan',
                    '--sandbox',
                    '--output-format', 'stream-json',
                    '--print-timeout', f'{timeout}s',
                ]
                if dual['model'] is not None:
                    argv += ['--model', dual['model']]
                if dual['effort'] is not None:
                    argv += ['--effort', dual['effort']]
                argv.append(print_arg)

                child_rc = execute(argv, staging, log_path, timeout, idle, CAP, stderr_log=stderr_path)
                evidence['cli_exit_code'] = child_rc
                validated = validate_stream(log_path, child_rc)
                evidence['conversation_id'] = validated['conversation_id']
                evidence['tool_warnings'].extend(validated['tool_warnings'])
            except (RunError, OSError, UnicodeError, KeyboardInterrupt) as exc:
                run_exc = exc

            if stderr_path.is_file():
                try:
                    stderr_raw = stderr_path.read_bytes()
                    if stderr_raw.strip():
                        evidence['warnings'].append({'kind': 'non_empty_stderr', 'bytes': len(stderr_raw)})
                except OSError:
                    pass

            # Scan even on failed executions before relying on the post-run snapshot.
            write_tool = scan_log_for_write_tools(log_path)
            if write_tool is not None:
                integrity_reasons.append(f'disallowed write tool invocation: {write_tool}')
            if run_exc is not None and is_cleanup_failure(run_exc):
                integrity_reasons.append(str(run_exc))

            try:
                post_integrity = capture_integrity_state(repo, dirty_paths, extra_files, input_paths, staging)
                integrity_reasons.extend(diff_integrity_state(pre_integrity, post_integrity))
            except BaseException as exc:
                integrity_reasons.append('post-run integrity verification failed: ' + (str(exc) or type(exc).__name__))
            integrity_check_finished = True

            cleanup_staging()
            if (not integrity_reasons and staging_cleanup_error is None and run_exc is None
                    and interruption_signal(signal_state, child_rc=child_rc) is None):
                plan_text = validated['response']
                plan_text, rewritten = rewrite_staging_paths(plan_text, staging)
                if rewritten:
                    evidence['warnings'].append({'kind': 'staging_path_rewritten', 'count': rewritten})
                if not plan_text.endswith('\n'):
                    plan_text += '\n'
                plan_bytes = plan_text.encode('utf-8')
                plan_sha256 = digest_bytes(plan_bytes)
                write_plan_atomic(plan_path, plan_bytes)
        finally:
            if not staging_cleanup_attempted:
                cleanup_staging()
            if manifest_written:
                evidence.update(
                    log_sha256=digest_file(log_path),
                    stderr_sha256=digest_file(stderr_path),
                )
        finalize()
    except BaseException as exc:
        if not manifest_written:
            raise
        if not integrity_check_finished:
            integrity_reasons.append('post-run integrity verification incomplete: ' + (str(exc) or type(exc).__name__))
        finalize(exc)
    finally:
        if manifest_written:
            write_json(out / 'manifest.json', evidence)
            write_json(out / 'status.json', evidence)

    return evidence


def build_parser():
    return planning_parser(__doc__)


def main():
    signal_state = {}

    def interrupt(signum, frame):
        signal_state['signum'] = signum
        raise KeyboardInterrupt('AGY planning interrupted')

    handlers = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)}
    try:
        try:
            for signum in handlers:
                signal.signal(signum, interrupt)
            args = build_parser().parse_args()
            evidence = run(args, signal_state)
        except KeyboardInterrupt as exc:
            summary = {
                'status': 'interrupted',
                'completed': False,
                'fallback_allowed': False,
                'integrity_ok': True,
                'exit_code': 128 + interruption_signal(signal_state, exc),
                'error_kind': 'interrupted',
                'error': str(exc) or 'AGY planning interrupted',
            }
        except (ConfigError, OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
            summary = {
                'status': 'invalid_config',
                'completed': False,
                'fallback_allowed': False,
                'integrity_ok': True,
                'exit_code': 2,
                'error': str(exc),
            }
            print('agy_plan preparation failed: ' + str(exc), file=sys.stderr)
        else:
            summary = {
                key: evidence[key]
                for key in ('status', 'stage', 'completed', 'fallback_allowed', 'integrity_ok', 'exit_code', 'plan_file')
            }
            if 'error' in evidence:
                summary['error'] = evidence['error']
        print(json.dumps(summary, ensure_ascii=False))
        return summary['exit_code']
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)


if __name__ == '__main__':
    sys.exit(main())
