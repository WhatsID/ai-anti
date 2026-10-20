"""Strict supported Codex exec JSONL evidence; model routing is unproven."""
import json
from pathlib import Path
from review_runner import RunError
from review_materials import load_registry, read_material


def validate(logfile, code, requested, cwd, required, registry):
    if code:
        raise RunError('nonzero Codex CLI exit: ' + str(code))
    if Path(registry['root']).resolve() != Path(cwd).resolve() or set(required) != {x['path'] for x in registry['materials'].values()}:
        raise RunError('registry does not match stage requirements')
    thread = turn = terminal = False
    pending, seen, complete = {}, set(), set()
    report = None
    warnings = []
    try:
        for line in Path(logfile).read_text('utf-8').splitlines():
            if line == 'Reading additional input from stdin...' and not thread:
                if warnings: raise RunError('duplicate stdin notice')
                warnings.append({'kind': 'stdin_notice'})
                continue
            event = json.loads(line)
            if not isinstance(event, dict) or terminal:
                raise RunError('invalid event or content after terminal')
            kind = event.get('type')
            if kind == 'thread.started':
                if thread or not isinstance(event.get('thread_id'), str) or not event['thread_id']:
                    raise RunError('duplicate or invalid thread')
                thread = True
            elif kind == 'turn.started':
                if not thread or turn:
                    raise RunError('invalid turn lifecycle')
                turn = True
            elif kind in ('item.started', 'item.completed'):
                item = event.get('item')
                if not thread or not isinstance(item, dict):
                    raise RunError('invalid item envelope')
                uid, typ = item.get('id'), item.get('type')
                if not isinstance(uid, str) or not uid:
                    raise RunError('invalid item id')
                if typ == 'error':
                    expected = 'Model metadata for `' + requested + '` not found. Defaulting to fallback metadata; this can degrade performance and cause issues.'
                    if turn or kind != 'item.completed' or item.get('message') != expected or uid in seen:
                        raise RunError('Codex error item')
                    if any(x['kind'] == 'fallback_metadata' for x in warnings):
                        raise RunError('duplicate metadata warning')
                    seen.add(uid)
                    warnings.append({'kind': 'fallback_metadata', 'message': expected})
                    continue
                if not turn:
                    raise RunError('item outside turn')
                if typ == 'mcp_tool_call':
                    args = item.get('arguments')
                    if (item.get('server') != 'materials' or item.get('tool') != 'read_material'
                            or not isinstance(args, dict) or set(args) != {'id'}
                            or not isinstance(args['id'], str) or args['id'] not in registry['materials']
                            or item.get('error', 'missing') is not None):
                        raise RunError('invalid material tool invocation')
                    if kind == 'item.started':
                        if uid in seen or item.get('status') != 'in_progress' or item.get('result', 'missing') is not None:
                            raise RunError('duplicate or malformed tool start')
                        seen.add(uid)
                        pending[uid] = args['id']
                    else:
                        if uid not in pending or pending[uid] != args['id'] or item.get('status') != 'completed':
                            raise RunError('unmatched or failed tool result')
                        raw = read_material(registry, args['id'])
                        if item.get('result') != {'content': [{'type': 'text', 'text': raw}], 'structured_content': None}:
                            raise RunError('material text does not match frozen snapshot')
                        complete.add(registry['materials'][pending.pop(uid)]['path'])
                        report = None
                elif typ in ('reasoning', 'agent_message'):
                    if kind != 'item.completed' or uid in seen or not isinstance(item.get('text'), str):
                        raise RunError('invalid message item')
                    seen.add(uid)
                    if typ == 'agent_message' and set(required) <= complete and not pending and item['text'].strip():
                        report = item['text']
                else:
                    raise RunError('unexpected Codex item: ' + str(typ))
            elif kind == 'turn.completed':
                if not turn or pending or not report or set(required) - complete:
                    raise RunError('incomplete Codex review', 'incomplete_reads')
                terminal = True
            else:
                raise RunError('unexpected Codex event: ' + str(kind))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RunError('truncated or malformed Codex JSONL') from exc
    if not terminal:
        raise RunError('missing terminal Codex result')
    return {'model': None, 'model_evidence': 'requested_only', 'result': report,
            'complete_reads': complete, 'transport_warnings': warnings,
            'read_recoveries': [], 'read_recovery_count': 0}
