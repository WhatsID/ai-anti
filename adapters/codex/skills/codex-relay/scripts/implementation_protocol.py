"""Validate a single implementation turn; completion is not primary acceptance."""
import json
from review_runner import RunError


METADATA_WARNING = 'Model metadata for `glm-5.3` not found. Defaulting to fallback metadata; this can degrade performance and cause issues.'


def validate(logfile, code):
    completed = started = 0
    final = None
    pending = set()
    warnings = []
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
        kind = event['type']
        if completed:
            raise RunError('content after implementation turn.completed')
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
            if item.get('type') == 'error':
                if kind == 'item.completed' and started == 0 and item.get('message') == METADATA_WARNING and METADATA_WARNING not in warnings:
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
            elif isinstance(item.get('id'), str):
                pending.discard(item['id'])
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
    if code or started != 1 or completed != 1 or final is None:
        raise RunError('implementation requires exit 0, one completed turn and a final agent_message')
    return dict(result=final, warnings=warnings)
