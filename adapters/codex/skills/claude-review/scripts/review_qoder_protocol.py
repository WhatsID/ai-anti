"""Qoder 1.1.74 evidence gates; never broaden Claude's wire protocol."""
import copy
import json
from pathlib import Path

from review_materials import MaterialAudit, TOOL, read_material
from review_runner import RunError, blocks


def validate(logfile, code, requested, cwd, families=None, expected_model=None,
             required=None, registry=None):
    if registry is None:
        raise RunError('Qoder requires material registry')
    audit = MaterialAudit(registry, Path(cwd).resolve(), required, RunError)
    try:
        events = [json.loads(line) for line in logfile.read_text('utf-8').splitlines()]
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RunError('truncated or malformed Qoder stream-json') from exc
    init = final = None
    assistants = 0
    for event in events:
        if not isinstance(event, dict):
            raise RunError('non-object stream event')
        kind, subtype = event.get('type'), event.get('subtype', '')
        if final is not None:
            raise RunError('unexpected event after terminal result')
        if ('hook' in str(kind).lower() or 'hook' in str(subtype).lower()
                or event.get('is_error') or event.get('error')
                or event.get('permission_denials') or subtype == 'permission_denied'):
            raise RunError('Qoder error, hook or permission denial')
        if kind == 'system' and subtype == 'init':
            if init is not None:
                raise RunError('expected exactly one init')
            init = event
            if (event.get('tools') != [TOOL]
                    or event.get('mcp_servers') != [{'name': 'materials', 'status': 'connected'}]
                    or event.get('skills') != [] or event.get('plugins') != []
                    or event.get('permissionMode') != 'dontAsk'
                    or event.get('qodercli_version') != '1.1.74'
                    or event.get('model') != 'Kimi-K3'):
                raise RunError('unsafe or missing Qoder init capability set')
            for key in ('slash_commands', 'agents'):
                if not isinstance(event.get(key), list) or any(not isinstance(x, str) for x in event[key]):
                    raise RunError('malformed Qoder visible metadata')
            continue
        if init is None:
            raise RunError('event before Qoder init')
        if kind == 'system' and subtype == 'artifacts_update' and event.get('artifacts') == []:
            continue
        if kind == 'result':
            if (event.get('subtype') != 'success' or event.get('is_error') is not False
                    or event.get('permission_denials') != [] or event.get('stop_reason') != 'end_turn'
                    or not isinstance(event.get('result'), str) or not event['result'].strip()):
                raise RunError('incomplete or malformed review result')
            if audit.pending or set(required or []) - audit.complete:
                raise RunError('mandatory material was not fully Read before result', 'incomplete_reads')
            final = event
            continue
        if kind not in ('assistant', 'user'):
            raise RunError('unexpected Qoder stream event')
        event_blocks = blocks(event)
        allowed = ('thinking', 'text', 'tool_use') if kind == 'assistant' else ('tool_result',)
        if not event_blocks or any(b.get('type') not in allowed for b in event_blocks):
            raise RunError('unexpected Qoder message block')
        if 'role' in event['message'] and event['message']['role'] != kind:
            raise RunError('unexpected Qoder message role')
        if kind == 'assistant':
            assistants += 1
            if event['message'].get('model') != 'Kimi-K3':
                raise RunError('unexpected assistant model')
        if any('is_error' in b and b['is_error'] is not False for b in event_blocks):
            raise RunError('incomplete material review: tool error')
        normalized = copy.deepcopy(event)
        normalized_blocks = blocks(normalized)
        for block in normalized_blocks:
            if block.get('type') == 'tool_result':
                uid = block.get('tool_use_id')
                if uid not in audit.pending:
                    raise RunError('unmatched or duplicate tool result')
                raw = read_material(registry, audit.pending[uid])
                expected = [{'type': 'text', 'text': raw}]
                message = '(' + TOOL + ' completed with no output)' if raw == '' else raw
                # Verify BOTH raw envelopes before normalization. Never replace forged text.
                if block.get('content') != message or event.get('tool_use_result') != expected:
                    raise RunError('material text does not match frozen snapshot')
                block['content'] = expected
        audit.consume(normalized, normalized_blocks)
    if code:
        raise RunError('nonzero CLI exit: ' + str(code))
    if init is None or final is None or not assistants:
        raise RunError('expected init, assistant and terminal result')
    if requested != 'kmodel_latest' or expected_model != 'Kimi-K3':
        raise RunError('unexpected requested Qoder model')
    return {'model': 'Kimi-K3', 'result': final['result'], 'complete_reads': set(audit.complete),
            'read_recoveries': [], 'read_recovery_count': 0, 'transport_warnings': [],
            'credits': final.get('total_credits'),
            'visible_context': {key: init[key] for key in ('slash_commands', 'agents')}}
