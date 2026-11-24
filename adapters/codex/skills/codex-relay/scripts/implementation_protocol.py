"""Validate a single implementation turn; completion is not primary acceptance."""
import json
import unicodedata
from codex_events import known_item_warning, known_reconnect, stderr_warnings
from review_runner import RunError


FALLBACK_METADATA = (
    'Model metadata for `{}` not found. Defaulting to fallback metadata; '
    'this can degrade performance and cause issues.'
)
METADATA_WARNING = FALLBACK_METADATA.format('glm-5.3')
SANDBOX_DIAGNOSTIC_MODELS = ('qwen3.8-max', 'deepseek-v4-pro')
FALLBACK_METADATA_MODELS = ('deepseek-v4-pro', 'deepseek-v4.1-flash')


def has_error_marker(record):
    return (any(record.get(key) for key in ('error', 'errors', 'is_error', 'permission_denials'))
            or record.get('subtype') == 'permission_denied')


def valid_thread_id(value):
    return (isinstance(value, str) and bool(value) and not value.startswith('-')
            and not any(char.isspace() or unicodedata.category(char) == 'Cc' for char in value))


def validate(logfile, code, requested_model=None, stderr_log=None, expected_thread_id=None, *, cli_version=None):
    completed = started = 0
    final = None
    thread_id = None
    thread_seen = False
    pending = set()
    token_plan_169 = cli_version == 'codex-cli 0.169.1'
    warnings = stderr_warnings(stderr_log, implementation=True,
                               tool_sandbox_parameters=token_plan_169 and requested_model in SANDBOX_DIAGNOSTIC_MODELS)
    sandbox_diagnostic = any(isinstance(warning, dict) and warning['kind'] == 'tool_sandbox_parameters'
                             for warning in warnings)
    fallback_metadata = token_plan_169 and requested_model in FALLBACK_METADATA_MODELS
    metadata_seen = False
    command_starts = set()
    command_succeeded = False
    try:
        lines = logfile.read_text('utf-8').splitlines()
        if lines and lines[0] == 'Reading additional input from stdin...':
            warnings.append(lines.pop(0))
        events = [json.loads(line) for line in lines]
    except (UnicodeError, ValueError) as exc:
        raise RunError('malformed implementation JSON stream') from exc
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get('type'), str):
            raise RunError('malformed implementation event')
        if (sandbox_diagnostic or fallback_metadata) and has_error_marker(event):
            raise RunError('implementation error or permission denial')
        kind = event['type']
        if kind == 'thread.started':
            candidate = event.get('thread_id')
            thread_id = candidate if not (thread_seen or started or completed) and valid_thread_id(candidate) else None
            thread_seen = True
            if expected_thread_id is not None and thread_id != expected_thread_id:
                raise RunError('unexpected implementation thread.started', kind='session_mismatch')
        if completed:
            raise RunError('content after implementation turn.completed')
        warning = known_item_warning(event) if started == 0 else None
        if (warning is not None and not event.get('error') and not event.get('permission_denials')
                and not event.get('is_error')):
            warnings.append(warning)
            continue
        warning = known_reconnect(event) if started == 1 else None
        if warning is not None:
            warnings.append(warning)
            continue
        if kind in ('error', 'turn.failed') or event.get('error') or event.get('permission_denials'):
            raise RunError('implementation error or turn.failed')
        if kind == 'turn.started':
            started += 1
            if started != 1:
                raise RunError('multiple implementation turns')
        elif kind == 'turn.completed':
            if pending:
                raise RunError('unfinished implementation item')
            completed += 1
        elif kind in ('item.started', 'item.updated', 'item.completed'):
            item = event.get('item')
            if not isinstance(item, dict):
                raise RunError('malformed implementation item')
            if item.get('permission_denials') or item.get('type') == 'permission_denied':
                raise RunError('implementation permission denial')
            if (sandbox_diagnostic or fallback_metadata) and has_error_marker(item):
                raise RunError('implementation item error or permission denial')
            if item.get('type') == 'error':
                if (fallback_metadata and not metadata_seen and thread_id is not None and started == 0
                        and kind == 'item.completed' and set(event) == {'type', 'item'}
                        and set(item) == {'id', 'type', 'message'}
                        and isinstance(item['id'], str) and item['id']
                        and item['message'] == FALLBACK_METADATA.format(requested_model)):
                    warnings.append({'kind': 'model_metadata', 'id': item['id'], 'message': item['message']})
                    metadata_seen = True
                    continue
                if requested_model == 'glm-5.3' and kind == 'item.completed' and started == 0 and item.get('message') == METADATA_WARNING and METADATA_WARNING not in warnings:
                    warnings.append(METADATA_WARNING)
                    continue
                raise RunError('implementation error item')
            if started != 1:
                raise RunError('implementation item before turn.started')
            if kind in ('item.started', 'item.updated'):
                final = None
                if not isinstance(item.get('id'), str) or not item['id']:
                    raise RunError('implementation item missing ID')
                pending.add(item['id'])
                if sandbox_diagnostic:
                    if item.get('type') != 'command_execution':
                        command_starts.discard(item['id'])
                    elif kind == 'item.started':
                        command_starts.add(item['id'])
            elif isinstance(item.get('id'), str):
                pending.discard(item['id'])
                if item['id'] in command_starts:
                    command_starts.remove(item['id'])
                    if (item.get('type') == 'command_execution' and item.get('status') == 'completed'
                            and type(item.get('exit_code')) is int and item['exit_code'] == 0):
                        command_succeeded = True
            if kind == 'item.completed' and item.get('type') == 'agent_message':
                if not isinstance(item.get('text'), str) or not item['text'].strip():
                    raise RunError('empty implementation final message')
                final = item['text']
            elif kind == 'item.completed':
                # A command may fail and be repaired within this turn.
                final = None
        elif kind != 'thread.started':
            raise RunError('unknown implementation event type: ' + kind)
        if set(event) - {'type', 'thread_id', 'usage', 'item'}:
            warnings.append('unverified event metadata fields: ' + ', '.join(sorted(set(event) - {'type', 'thread_id', 'usage', 'item'})))
    if expected_thread_id is not None and not thread_seen:
        raise RunError('missing implementation thread.started', kind='session_mismatch')
    if code or started != 1 or completed != 1 or final is None:
        raise RunError('implementation requires exit 0, one completed turn and a final agent_message')
    if sandbox_diagnostic and not command_succeeded:
        raise RunError('tool sandbox parameter diagnostic requires a successful command in the turn')
    return dict(result=final, warnings=warnings, thread_id=thread_id)
