"""Single-owner, bounded retries for validated model failures, never arbitrary execute()."""
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

from codex_events import known_item_warning, known_reconnect, stderr_path, stderr_warnings

MAX_RETRIES = 2
BACKOFF = (30, 90)
CONNECTORS_WARNING = (
    '⚠ claude.ai connectors are disabled because ANTHROPIC_API_KEY or another auth source is set '
    "and takes precedence over your claude.ai login · Unset it to load your organization's connectors"
)
UNRECOGNIZED_MODEL = '[claude-code:unrecognized_model] '
COMMAND_DENIALS = ('permission denied', 'operation not permitted', 'read-only file system',
                   'sandbox', 'not permitted', 'denied by')
CAPACITY = 'Selected model is at capacity. Please try a different model'


def classify(error, source=None):
    """Only call with an error envelope established by a protocol validator."""
    if not isinstance(error, dict):
        return 'unknown_upstream'
    fields = {k: error[k] for k in ('type', 'code', 'error_code', 'message', 'errors', 'error', 'status', 'status_code') if k in error}
    text = json.dumps(fields, ensure_ascii=False).lower()
    if any(x in text for x in ('insufficient_quota', 'quota_exhausted', 'quota exceeded', 'quota exhausted', 'credits exhausted', 'insufficient credits', 'insufficient balance', 'credit balance', '余额不足', '配额耗尽')):
        return 'quota_exhausted'
    if any(type(error.get(k)) is int and error[k] in (400, 401, 403, 404, 413, 422) for k in ('code', 'error_code', 'status', 'status_code')):
        return 'permanent_upstream'
    if any(x in text for x in ('authentication_error', 'invalid_api_key', 'permission_denied', 'permission_error', 'invalid_request_error', 'model_not_found', 'context_length_exceeded')):
        return 'permanent_upstream'
    codes = [error.get(k) for k in ('code', 'error_code', 'status', 'status_code')]
    types = [error.get(k) for k in ('type', 'code')]
    if any(x in ('server_error', 'internal_server_error', 'overloaded_error', 'rate_limit_error', 'temporary_capacity') for x in types):
        return 'retryable_upstream'
    messages = [error.get('message')] + (error.get('errors') if isinstance(error.get('errors'), list) else [])
    if source == 'qoder' and type(error.get('error_code')) is int and error['error_code'] == 500:
        if (CAPACITY not in messages and not any(type(x) is int and x == 10500 for x in codes)
                and not any(type(error.get(k)) is int and error[k] in (500, 502, 503, 504, 529)
                            for k in ('status', 'status_code'))):
            return 'ambiguous_model_error'
    if any(type(x) is int and x in (10500, 500, 502, 503, 504, 529) for x in codes):
        return 'retryable_upstream'
    messages = [error.get('message')] + (error.get('errors') if isinstance(error.get('errors'), list) else [])
    if CAPACITY in messages:
        return 'retryable_upstream'
    return 'unknown_upstream'


def upstream_error(error, code=None, source=None):
    from review_runner import RunError
    kind = classify(error, source)
    label = 'model CLI failure' if kind == 'ambiguous_model_error' else 'model upstream failure'
    exc = RunError(label + ' (' + kind + ')', kind)
    exc.error_source = source
    exc.model_error = error
    exc.exit_code = code
    return exc


def fingerprint(paths):
    result = {}
    for value in paths:
        path = Path(value).absolute()
        entries = [path] + sorted(path.rglob('*')) if path.is_dir() else [path]
        for item in entries:
            if item.is_symlink():
                raise ValueError('retry input contains symlink')
            if item.is_file():
                result[str(item)] = hashlib.sha256(item.read_bytes()).hexdigest()
            elif item.is_dir():
                result[str(item)] = 'directory'
            else:
                raise ValueError('retry input missing or unsupported')
    return result


def retry(operation, logfile, timeout, max_retries=MAX_RETRIES, inputs=(), clock=time.monotonic, sleep=time.sleep):
    """operation(attempt_log, remaining_seconds); snapshots checked before every request.

    Time/sleep injection is an ordinary Python API for deterministic callers/tests.
    No environment knobs alter production retry policy.
    """
    from review_runner import RunError
    if type(max_retries) is not int or max_retries not in range(3):
        raise ValueError('max_retries must be 0, 1 or 2')
    logfile = Path(logfile)
    evidence = logfile.with_suffix('.attempts.json')
    if logfile.exists() or evidence.exists():
        raise RunError('retry output already exists')
    started = clock()
    records = []
    state = {'status': 'running', 'attempts': records, 'attempt_count': 0, 'retry_count': 0}
    def save(status):
        state.update(status=status, attempt_count=len(records), retry_count=max(0, len(records) - 1))
        evidence.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
    try:
        frozen = fingerprint(inputs)
        for index in range(max_retries + 1):
            if fingerprint(inputs) != frozen:
                raise RunError('retry input snapshot changed', 'snapshot_changed')
            remaining = timeout - (clock() - started)
            if remaining <= 0:
                raise RunError('stage timeout budget exhausted', 'timeout')
            raw = logfile.with_name(logfile.stem + '.attempt' + str(index + 1) + logfile.suffix)
            if raw.exists():
                raise RunError('attempt output already exists')
            record = {'exit_code': None, 'attempt': index + 1, 'log': raw.name, 'started_at': time.time(), 'wait_seconds': 0}
            records.append(record)
            save('running')
            try:
                result = operation(raw, remaining)
                if clock() - started >= timeout:
                    raise RunError('stage timeout budget exhausted', 'timeout')
                if fingerprint(inputs) != frozen:
                    raise RunError('retry input snapshot changed', 'snapshot_changed')
                record['kind'] = 'success'
                record['exit_code'] = result.get('exit_code', 0)
                record['finished_at'] = time.time()
            except (RunError, KeyboardInterrupt) as exc:
                record['kind'] = 'cancelled' if isinstance(exc, KeyboardInterrupt) else exc.kind
                record['error'] = str(exc)
                record['exit_code'] = getattr(exc, 'exit_code', None)
                for field in ('error_source', 'model_error', 'network_diagnostic'):
                    if hasattr(exc, field):
                        record[field] = getattr(exc, field)
                record.setdefault('finished_at', time.time())
                record['log_sha256'] = hashlib.sha256(raw.read_bytes()).hexdigest() if raw.exists() else None
                if record['kind'] != 'retryable_upstream' or index == max_retries:
                    raise
                delay = BACKOFF[index]
                if timeout - (clock() - started) <= delay:
                    raise RunError('stage timeout budget insufficient for retry', 'timeout') from exc
                print(logfile.stem + ': temporary model failure; retry in ' + str(delay) + 's', file=sys.stderr)
                record['wait_seconds'] = delay
                save('waiting')
                # Small interruptible waits preserve cancellation and progress opportunities.
                record['wait_started_at'] = time.time()
                wait_clock = clock()
                until = wait_clock + delay
                while clock() < until:
                    sleep(min(1, until - clock()))
                record['wait_finished_at'] = time.time()
            else:
                shutil.copyfile(raw, logfile)
                result.update(attempt_count=len(records), retry_count=len(records) - 1, attempts_log=evidence.name)
                save('completed')
                return result
            finally:
                if 'wait_started_at' in record:
                    record['wait_elapsed_seconds'] = clock() - wait_clock
                    record.setdefault('wait_finished_at', time.time())
                record.setdefault('finished_at', time.time())
                record['log_sha256'] = hashlib.sha256(raw.read_bytes()).hexdigest() if raw.exists() else None
                stderr_log = stderr_path(raw)
                if stderr_log.is_file():
                    record['stderr_log'] = stderr_log.name
                    record['stderr_sha256'] = hashlib.sha256(stderr_log.read_bytes()).hexdigest()
                save(state['status'])
    except (RunError, KeyboardInterrupt, OSError, ValueError) as exc:
        state['error_kind'] = 'cancelled' if isinstance(exc, KeyboardInterrupt) else getattr(exc, 'kind', 'input_error')
        state['error'] = str(exc)
        if records:
            last = logfile.parent / records[-1]['log']
            if last.exists(): shutil.copyfile(last, logfile)
        save('failed')
        raise


def stage_runner(runner, max_retries=MAX_RETRIES):
    def run(cli, cwd, prompt, logfile, model, timeout, idle, cap, *args, **kwargs):
        kwargs = dict(kwargs)
        kwargs['env'] = dict(os.environ if kwargs.get('env') is None else kwargs['env'])
        prompt_file = Path(logfile).with_suffix('.prompt.txt')
        prompt_file.write_text(prompt, encoding='utf-8')
        inputs = [cwd, prompt_file]
        if kwargs.get('registry_path') is not None:
            inputs.append(kwargs['registry_path'])
        return retry(lambda raw, remaining: runner(cli, cwd, prompt, raw, model, remaining,
                     min(idle, remaining), cap, *args, **kwargs), logfile, timeout, max_retries, inputs)
    return run


def attempt_summary(logs):
    return {p.stem.removesuffix('.attempts'): json.loads(p.read_text('utf-8'))
            for p in Path(logs).glob('*.attempts.json')}


def direct_result(logfile, code, protocol, stderr_log=None):
    """Extract a validated direct readonly report; raw remains the R10 evidence."""
    from review_runner import RunError
    text = Path(logfile).read_text('utf-8')
    try:
        if protocol == 'claude-json':
            lines = text.splitlines()
            warnings = []
            if lines and lines[0] == CONNECTORS_WARNING:
                lines.pop(0)
                if not lines or not lines[0].startswith(UNRECOGNIZED_MODEL):
                    raise RunError('unknown transport warning')
                warnings.append({'kind': 'connectors_disabled', 'message': CONNECTORS_WARNING})
            if lines and lines[0].startswith(UNRECOGNIZED_MODEL):
                warning = json.loads(lines.pop(0)[len(UNRECOGNIZED_MODEL):])
                if (not isinstance(warning, dict) or set(warning) != {'model', 'query_source'} or warning['query_source'] != 'sdk'
                        or not isinstance(warning['model'], str)):
                    raise RunError('unknown transport warning')
                warnings.append(warning)
                if lines and lines[0].startswith(UNRECOGNIZED_MODEL):
                    raise RunError('unknown transport warning')
            event = json.loads('\n'.join(lines))
            if not isinstance(event, dict) or event.get('type') != 'result' or type(event.get('is_error')) is not bool:
                raise RunError('invalid direct JSON result')
            if event.get('is_error') is True:
                if event.get('permission_denials'):
                    raise RunError('permission denial')
                if event.get('subtype') != 'error_during_execution':
                    raise RunError('unsupported direct failure')
                error = dict(event)
                # API JSON inside result is an error envelope only because is_error=true.
                if isinstance(event.get('result'), str):
                    try:
                        payload = json.loads(event['result'])
                    except ValueError:
                        payload = {'message': event['result']}
                    if isinstance(payload, dict):
                        error.update(payload.get('error', payload) if isinstance(payload.get('error', payload), dict) else {})
                raise upstream_error(error, code)
            if code or event.get('subtype') != 'success' or not isinstance(event.get('permission_denials'), list):
                raise RunError('unsuccessful direct JSON result')
            report = event.get('result')
            if not isinstance(report, str) or not report.strip():
                raise RunError('empty direct report')
            return {'result': report, 'permission_denials': event['permission_denials'], 'transport_warnings': warnings}
        if protocol != 'codex-jsonl':
            raise RunError('unsupported direct retry protocol')
        warnings = stderr_warnings(stderr_log)
        thread = turn = terminal = reconnect = False
        candidates, pending, seen = [], {}, set()
        failure = None
        for line in text.splitlines():
            event = json.loads(line)
            if not isinstance(event, dict) or terminal:
                raise RunError('invalid direct event or content after terminal')
            kind = event.get('type')
            if event.get('permission_denials') or event.get('is_error'):
                raise RunError('direct error or permission denial')
            warning = known_item_warning(event) if not turn and thread else None
            if warning is not None and warning['id'] not in seen and not event.get('error'):
                seen.add(warning['id'])
                warnings.append(warning)
                continue
            warning = known_reconnect(event) if turn else None
            if warning is not None:
                reconnect = True
                warnings.append(warning)
                continue
            if kind == 'thread.started':
                if thread or not isinstance(event.get('thread_id'), str) or not event['thread_id']:
                    raise RunError('invalid direct thread')
                thread = True
            elif kind == 'turn.started':
                if not thread or turn:
                    raise RunError('invalid direct turn')
                turn = True
            elif kind in ('item.started', 'item.completed'):
                item = event.get('item')
                if not turn or not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id']:
                    raise RunError('invalid direct item')
                uid, typ = item['id'], item.get('type')
                if typ in ('command_execution', 'mcp_tool_call', 'web_search'):
                    if item.get('error') or item.get('permission_denials') or item.get('is_error'):
                        raise RunError('direct tool error or permission denial')
                    expected_status = 'in_progress' if kind == 'item.started' else 'completed'
                    failed_command = (typ == 'command_execution' and kind == 'item.completed'
                                      and item.get('status') == 'failed')
                    statusless_search = typ == 'web_search' and 'status' not in item
                    if not statusless_search and item.get('status') != expected_status and not failed_command:
                        raise RunError('invalid direct tool status')
                    if typ == 'command_execution':
                        if not isinstance(item.get('command'), str) or not isinstance(item.get('aggregated_output'), str):
                            raise RunError('invalid direct command evidence')
                        if kind == 'item.completed':
                            exit_code = item.get('exit_code')
                            if type(exit_code) is not int or failed_command != (exit_code != 0):
                                raise RunError('failed direct command')
                            if failed_command:
                                output = item['aggregated_output'].lower()
                                if any(marker in output for marker in COMMAND_DENIALS):
                                    raise RunError('failed direct command')
                                warnings.append({'kind': 'failed_command', 'id': uid, 'exit_code': exit_code,
                                                 'command': item['command'][:200]})
                    elif typ == 'mcp_tool_call':
                        if (not isinstance(item.get('server'), str) or not isinstance(item.get('tool'), str)
                                or not isinstance(item.get('arguments'), dict)
                                or (kind == 'item.completed' and not isinstance(item.get('result'), dict))):
                            raise RunError('invalid direct MCP evidence')
                    elif statusless_search:
                        action = item.get('action')
                        if not isinstance(action, dict):
                            raise RunError('invalid direct search evidence')
                        if kind == 'item.started':
                            if action.get('type') != 'other' or item.get('query') != '' or 'results' in item:
                                raise RunError('invalid direct search evidence')
                        elif (action.get('type') not in ('search', 'open_page', 'find_in_page')
                              or not isinstance(item.get('query'), str)
                              or not isinstance(item.get('results'), list)):
                            raise RunError('invalid direct search evidence')
                    elif not isinstance(item.get('query'), str):
                        raise RunError('invalid direct search evidence')
                    candidates.clear()
                    shape = (typ, statusless_search)
                    if kind == 'item.started':
                        if uid in seen or uid in pending:
                            raise RunError('duplicate direct tool')
                        pending[uid] = shape
                    else:
                        if pending.get(uid) != shape:
                            raise RunError('unmatched direct tool result')
                        del pending[uid]
                        seen.add(uid)
                elif typ in ('reasoning', 'agent_message'):
                    if kind != 'item.completed' or uid in seen or uid in pending or not isinstance(item.get('text'), str):
                        raise RunError('invalid direct message')
                    seen.add(uid)
                    if typ == 'agent_message' and item['text'].strip():
                        candidates.append(item['text'])
                else:
                    raise RunError('unknown direct item')
            elif kind == 'turn.failed':
                if reconnect or not turn or not isinstance(event.get('error'), dict):
                    raise RunError('invalid direct failed turn')
                terminal = True
                failure = upstream_error(event['error'], code)
            elif kind == 'turn.completed':
                if not turn or pending or len(candidates) != 1:
                    raise RunError('incomplete or ambiguous direct report')
                terminal = True
            else:
                raise RunError('unknown direct event')
        if not terminal:
            raise RunError('missing direct terminal')
        if failure is not None:
            raise failure
        if code:
            raise RunError('nonzero direct CLI exit')
        return {'result': candidates[0], 'permission_denials': [], 'transport_warnings': warnings}
    except (UnicodeError, ValueError, TypeError) as exc:
        raise RunError('malformed direct protocol') from exc


def readonly(command, cwd, logfile, report, timeout, idle, cap, protocol, inputs, max_retries=MAX_RETRIES, env=None):
    from review_runner import execute, RunError
    if not inputs:
        raise RunError('explicit retry inputs required')
    report = Path(report)
    warnings_file = report.with_name(report.name + '.warnings.json') if protocol == 'codex-jsonl' else None
    if report.exists() or (warnings_file is not None and (warnings_file.exists() or warnings_file.is_symlink())):
        raise RunError('report output already exists')
    env = dict(os.environ if env is None else env)
    def operation(raw, remaining):
        options = {'stderr_log': stderr_path(raw)} if protocol == 'codex-jsonl' else {}
        code = execute(command, cwd, raw, remaining, min(idle, remaining), cap, env=env, **options)
        return direct_result(raw, code, protocol, **options)
    result = retry(operation, logfile, timeout, max_retries, inputs)
    if warnings_file is not None:
        warnings_file.write_text(json.dumps(result['transport_warnings'], ensure_ascii=False, indent=2) + '\n',
                                 encoding='utf-8')
    report.write_text(result['result'], encoding='utf-8')
    return result
