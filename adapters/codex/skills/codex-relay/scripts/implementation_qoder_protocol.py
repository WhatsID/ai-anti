"""Fail-closed Qoder 1.1.77 implementation stream, independent of reviewer gates.

Model identity is CLI-reported only; this cannot attest the subscription backend.
Tool errors may be repaired, but permission denials and protocol errors may not.
"""
import json
from review_runner import RunError

VERSION = '1.1.77'
MODEL = 'GLM-5.3'
TOOLS = ('Read', 'Write', 'Edit', 'Bash', 'Glob', 'Grep')


def require(condition, message):
    if not condition:
        raise RunError('Qoder implementation: ' + message)


def validate(log, code, workspace, version=VERSION):
    require(code == 0, 'nonzero CLI exit')
    init = final = None
    final_text = ''
    pending, used = set(), set()
    pending_message = None
    recoveries = 0
    try:
        events = [json.loads(line) for line in log.read_text('utf-8').splitlines()]
    except (ValueError, UnicodeError) as exc:
        raise RunError('malformed or truncated Qoder stream') from exc
    for event in events:
        require(isinstance(event, dict), 'non-object event')
        require(final is None, 'data after terminal result')
        require(not event.get('error') and not event.get('errors') and not event.get('is_error') and not event.get('permission_denials'),
                'error or permission denial')
        kind = event.get('type')
        if kind == 'system' and event.get('subtype') == 'init':
            require(init is None, 'duplicate init')
            init = event
            tools = event.get('tools')
            require(isinstance(tools, list) and len(tools) == len(TOOLS) and all(isinstance(t, str) for t in tools) and set(tools) == set(TOOLS), 'unsafe tools')
            require(event.get('qodercli_version') == version == VERSION and event.get('protocol_version') == '1.5.0', 'version mismatch')
            require(event.get('model') == MODEL and event.get('cwd') == str(workspace), 'model or workspace mismatch')
            require(event.get('permissionMode') == 'acceptEdits' and event.get('apiKeySource') == 'none', 'unsafe permission or authentication mode')
            require(all(event.get(key) == [] for key in ('mcp_servers', 'plugins', 'skills')), 'unsafe extensions')
            # Native agent/slash metadata remains visible; it does not grant tools.
            continue
        require(init is not None, 'event before init')
        if kind == 'system':
            require(event.get('subtype') == 'artifacts_update' and event.get('artifacts') == [], 'unknown system event')
            continue
        if kind == 'result':
            require(not pending, 'pending tool calls')
            require(event.get('subtype') == 'success' and event.get('is_error') is False
                    and event.get('permission_denials') == [] and event.get('stop_reason') == 'end_turn'
                    and isinstance(event.get('result'), str) and event['result'].strip() and final_text.strip(), 'incomplete final')
            final = event
            continue
        require(kind in ('assistant', 'user'), 'unknown event')
        message = event.get('message')
        require(isinstance(message, dict) and message.get('role') == kind, 'invalid message role')
        blocks = message.get('content')
        require(isinstance(blocks, list) and blocks and all(isinstance(b, dict) for b in blocks), 'invalid content')
        if kind == 'assistant':
            require(message.get('model') == MODEL, 'assistant model mismatch')
            require('stop_reason' in message and message.get('stop_reason') in (None, 'tool_use', 'end_turn'), 'invalid assistant stop')
            if pending:
                require(isinstance(pending_message, str) and pending_message
                        and message.get('id') == pending_message
                        and message.get('stop_reason') in (None, 'tool_use'),
                        'new assistant turn before pending tool results')
            else:
                pending_message = message.get('id')
            final_text = ''
        for block in blocks:
            typ = block.get('type')
            if kind == 'assistant':
                require(not block.get('is_error') and not block.get('error') and not block.get('permission_denials'), 'assistant block error')
                require(typ in ('text', 'thinking', 'tool_use'), 'unexpected assistant block')
                if typ == 'thinking':
                    require(isinstance(block.get('thinking'), str), 'invalid thinking segment')
                if typ == 'text':
                    require(isinstance(block.get('text'), str), 'invalid assistant text')
                    if message.get('stop_reason') == 'end_turn':
                        final_text += block['text']
                if typ == 'tool_use':
                    uid = block.get('id')
                    require(message.get('stop_reason') in (None, 'tool_use') and isinstance(uid, str) and uid and uid not in used
                            and block.get('name') in TOOLS and isinstance(block.get('input'), dict), 'invalid tool call')
                    used.add(uid)
                    pending.add(uid)
            else:
                require(typ == 'tool_result', 'unexpected user block')
                uid = block.get('tool_use_id')
                require(isinstance(uid, str) and uid in pending, 'unmatched tool result')
                require('is_error' not in block or type(block['is_error']) is bool, 'invalid tool error flag')
                # Ordinary command/file failures are recoverable; denials never are.
                text = json.dumps(block.get('content', ''), ensure_ascii=False).lower()
                if block.get('is_error'):
                    require(not any(word in text for word in ('permission denied', 'permission_denied', 'not allowed', 'denied by', 'this action needs approval')), 'tool permission denial')
                if block.get('is_error'):
                    recoveries += 1
                metadata = event.get('tool_result_meta', [])
                require(isinstance(metadata, list) and all(isinstance(m, dict) for m in metadata), 'malformed tool metadata')
                require(not any(m.get('non_execution_kind') == 'permission-rule' for m in metadata), 'tool permission rule denial')
                meta = event.get('tool_use_result')
                if isinstance(meta, dict):
                    require(not meta.get('interrupted') and not meta.get('permission_denials')
                            and meta.get('kind') not in ('permission_denied', 'denied'), 'interrupted or denied tool')
                pending.remove(uid)
        if kind == 'assistant':
            require(message['stop_reason'] is None or (message['stop_reason'] == 'tool_use') == bool(pending), 'stop/tool mismatch')
    require(init is not None and final is not None and not pending, 'missing init or final')
    return dict(actual_model=MODEL, model_evidence='cli_reported', backend_model='unknown',
                result=final['result'], recovered_tool_errors=recoveries, warnings=[])
