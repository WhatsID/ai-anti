"""One proposal and one confirmation over the fixed vendor HTTPS route."""
import http.client
import json
from pathlib import Path
import secrets
import socket
import subprocess
from threading import Timer
import time
from urllib.parse import urlsplit

from ai_anti_config import ConfigError
from implementation_direct_files import (
    fingerprint, git, make_patch, restore_modes, snapshot, text, unchanged, validate_paths, workspace_lock,
)
from review_material import MAX_STAGE_BYTES, sha

TOOL_NAME = 'propose_file_changes'
TOOL = {'type': 'function', 'function': {
    'name': TOOL_NAME,
    'description': 'Stage complete UTF-8 replacements for selected authorized paths.',
    'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['changes'],
                   'properties': {'changes': {'type': 'array', 'items': {
                       'type': 'object', 'additionalProperties': False,
                       'required': ['path', 'content'],
                       'properties': {'path': {'type': 'string'}, 'content': {'type': 'string'}},
                   }}}},
}}
INSTRUCTIONS = '''Implement the authorized task using only the supplied files.
A null file denotes a new file. Call propose_file_changes exactly once with
complete replacement text for each file you need to change. Preserve all
intentional newlines. After the tool returns, reply with exactly its receipt
as plain text. The host writes the staged changes only after confirmation.'''


class DirectError(Exception):
    pass


def credential(service):
    try:
        path = Path.home() / '.config' / 'ai-anti' / 'secrets.json'
        with path.open('rb') as source:
            contents = source.read(MAX_STAGE_BYTES + 1)
        value = json.loads(text(contents, MAX_STAGE_BYTES))[service]['value']
        if (not isinstance(value, str) or not value
                or any(not 33 <= ord(char) <= 126 for char in value)):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise DirectError('credential_unavailable') from None
    return value


def read_stream(response, model, meta, remaining, deadline):
    message = {'role': 'assistant', 'content': []}
    call = None
    finish = None
    done = False
    while not done:
        if time.monotonic() >= deadline:
            raise DirectError('timeout')
        line = response.readline(remaining + 1)
        if not line:
            break
        remaining -= len(line)
        if remaining < 0:
            raise DirectError('output_limit')
        if not line.startswith(b'data:'):
            continue
        data = line[5:].strip()
        if data == b'[DONE]':
            done = True
            continue
        if not data:
            continue
        event = json.loads(data)
        if event.get('error'):
            raise DirectError('stream_error')
        reported = event.get('model')
        if reported is not None and reported != '':
            if reported != model:
                meta['model_mismatch'] = True
                raise DirectError('model_mismatch')
            meta['response_model'] = reported
        for choice in event.get('choices', []):
            if choice.get('index', 0) != 0 or finish is not None:
                raise DirectError('unexpected_choice')
            delta = choice.get('delta', {})
            if delta.get('function_call'):
                raise DirectError('unexpected_tool_call')
            for field in ('content', 'reasoning_content'):
                chunk = delta.get(field)
                if chunk:
                    if not isinstance(chunk, str):
                        raise DirectError('invalid_stream')
                    message.setdefault(field, []).append(chunk)
            for part in delta.get('tool_calls') or []:
                if part.get('index', 0) != 0 or part.get('type', 'function') != 'function':
                    raise DirectError('multiple_or_invalid_tool_calls')
                if call is None:
                    call = {'id': '', 'type': 'function', 'function': {'name': [], 'arguments': []}}
                if part.get('id'):
                    if call['id'] or not isinstance(part['id'], str):
                        raise DirectError('multiple_or_invalid_tool_calls')
                    call['id'] = part['id']
                for field in ('name', 'arguments'):
                    chunk = (part.get('function') or {}).get(field)
                    if chunk:
                        if not isinstance(chunk, str):
                            raise DirectError('invalid_stream')
                        call['function'][field].append(chunk)
            finish = choice.get('finish_reason')
            if finish:
                meta['finish_reason'] = finish if finish in (
                    'stop', 'tool_calls', 'length', 'content_filter', 'function_call') else 'unknown'
    if not done or finish not in ('stop', 'tool_calls'):
        raise DirectError('incomplete_stream')
    if meta.get('response_model') != model:
        raise DirectError('missing_response_model')
    for field in ('content', 'reasoning_content'):
        if field in message:
            message[field] = ''.join(message[field])
    if call is not None:
        call['function'] = {field: ''.join(chunks) for field, chunks in call['function'].items()}
        message['tool_calls'] = [call]
    return message, remaining


def stop_socket(sock):
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def turn(route, key, messages, meta, remaining, deadline, idle):
    model = route['requested_model']
    endpoint = urlsplit(route['endpoint'])
    started = time.monotonic()
    if started >= deadline:
        raise DirectError('timeout')
    request = dict(model=model, messages=messages, stream=True, tools=[TOOL], max_tokens=4096)
    if meta['phase'] == 'confirmation':
        request['tool_choice'] = 'none'
    connection = http.client.HTTPSConnection(endpoint.hostname, timeout=min(idle, deadline - started))
    watchdog = None
    try:
        connection.connect()
        left = deadline - time.monotonic()
        if left <= 0:
            raise DirectError('timeout')
        watchdog = Timer(left, stop_socket, args=(connection.sock,))
        watchdog.daemon = True
        watchdog.start()
        connection.request('POST', endpoint.path, json.dumps(request).encode('utf-8'),
                           {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                            'Accept': 'text/event-stream'})
        with connection.getresponse() as response:
            meta['http_status'] = response.status
            if response.status != 200:
                raise DirectError('http_status')
            content_type = response.getheader('Content-Type') or ''
            if content_type.split(';', 1)[0].strip().lower() != 'text/event-stream':
                raise DirectError('unexpected_content_type')
            return read_stream(response, model, meta, remaining, deadline)
    except (OSError, http.client.HTTPException, DirectError) as exc:
        if time.monotonic() >= deadline:
            raise DirectError('timeout') from None
        if isinstance(exc, DirectError):
            raise
        raise DirectError('idle_timeout' if isinstance(exc, TimeoutError) else 'network_error') from None
    except (ValueError, AttributeError, TypeError, RecursionError):
        raise DirectError('invalid_stream') from None
    finally:
        if watchdog is not None:
            watchdog.cancel()
        connection.close()
        meta['duration_seconds'] = round(time.monotonic() - started, 3)


def reject_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise DirectError('invalid_proposal')
        value[key] = item
    return value


def proposal(answer, files):
    calls = answer.get('tool_calls', [])
    if len(calls) != 1 or not calls[0]['id'].strip() or calls[0]['function']['name'] != TOOL_NAME:
        raise DirectError('invalid_proposal')
    try:
        arguments = json.loads(calls[0]['function']['arguments'], object_pairs_hook=reject_duplicate_keys)
    except (ValueError, RecursionError):
        raise DirectError('invalid_proposal') from None
    if (not isinstance(arguments, dict) or arguments.keys() != {'changes'}
            or not isinstance(arguments['changes'], list) or not arguments['changes']):
        raise DirectError('invalid_proposal')
    changes = {}
    size = 0
    for change in arguments['changes']:
        if (not isinstance(change, dict) or change.keys() != {'path', 'content'}
                or not isinstance(change['path'], str) or not isinstance(change['content'], str)):
            raise DirectError('invalid_proposal')
        path, content = change['path'], change['content']
        if path not in files or path in changes or content == files[path][0] or '\0' in content:
            raise DirectError('invalid_proposal')
        size += len(content.encode('utf-8'))
        if size > MAX_STAGE_BYTES:
            raise DirectError('proposal_limit')
        changes[path] = content
    return changes


def apply(root, fd, before, changes):
    patch = make_patch(before, changes)
    options = ('-c', 'apply.ignoreWhitespace=false', 'apply', '--whitespace=nowarn',
               '--unidiff-zero', '--no-recount', '-')
    if git(root, *options[:-1], '--check', '-', patch=patch).returncode:
        raise DirectError('patch_check_failed')
    if not unchanged(before, snapshot(fd, before)):
        raise DirectError('workspace_changed')
    if git(root, *options, patch=patch).returncode:
        raise DirectError('patch_apply_failed')
    expected = {path: changes.get(path, source) for path, (source, _, _) in before.items()}
    after = snapshot(fd, before, limit=2 * MAX_STAGE_BYTES)
    if any(source != expected[path] for path, (source, _, _) in after.items()):
        raise DirectError('patch_verification_failed')
    restore_modes(fd, before, after, changes)
    return after


def run(route, workspace, prompt, out, paths, prepare_only, limits, cap, write, digest):
    validate_paths(paths)
    started = time.monotonic()
    evidence = dict(route, actual_model=None, backend_model='unknown', model_evidence='requested_only',
                    status='prepared', completed=False, accepted=False, workspace=str(workspace),
                    paths=paths, turns=[], duration_seconds=0,
                    limits=dict(timeout=limits[0], idle_timeout=limits[1], max_output_bytes=cap,
                                max_input_bytes=MAX_STAGE_BYTES),
                    isolation=dict(detached_worktree=True, shell_access=False,
                                   model_network_access=False, scoped_text_proposal=True))
    phase = 'preflight'
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    log = out / 'implementation.jsonl'
    try:
        with workspace_lock(workspace, exclusive=not prepare_only) as fd:
            with prompt.open('rb') as source:
                raw = source.read(MAX_STAGE_BYTES + 1)
            payload = text(raw, MAX_STAGE_BYTES)
            files = snapshot(fd, paths, MAX_STAGE_BYTES - len(raw))
            evidence.update(prompt_sha256=sha(raw), files_before={
                path: fingerprint(source) for path, (source, _, _) in files.items()})
            if not prepare_only:
                phase = 'credential'
                key = credential(route['service'])
                messages = [{'role': 'system', 'content': INSTRUCTIONS},
                            {'role': 'user', 'content': json.dumps(
                                {'task': payload, 'files': {path: row[0] for path, row in files.items()}},
                                ensure_ascii=False)}]
                phase = 'proposal'
                meta = {'phase': phase}
                evidence['turns'].append(meta)
                deadline = started + limits[0]
                answer, remaining = turn(route, key, messages, meta, cap, deadline, limits[1])
                changes = proposal(answer, files)
                receipt = 'staged-' + secrets.token_hex(16)
                messages.extend([answer, {'role': 'tool', 'tool_call_id': answer['tool_calls'][0]['id'],
                                          'content': receipt}])
                phase = 'confirmation'
                meta = {'phase': phase}
                evidence['turns'].append(meta)
                final, _ = turn(route, key, messages, meta, remaining, deadline, limits[1])
                if final.get('tool_calls') or meta['finish_reason'] != 'stop' or final['content'] != receipt:
                    raise DirectError('invalid_confirmation')
                evidence.update(model_evidence='api_reported', actual_model=meta['response_model'])
                phase = 'apply'
                after = apply(workspace, fd, files, changes)
                evidence.update(status='completed', completed=True, files_after={
                    path: fingerprint(source) for path, (source, _, _) in after.items()})
    except (ConfigError, DirectError, OSError, UnicodeError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        reason = str(exc) if isinstance(exc, DirectError) else {
            'preflight': 'invalid_or_busy_workspace_or_input',
            'apply': 'workspace_or_patch_error',
        }.get(phase, 'execution_failed')
        if isinstance(exc, KeyboardInterrupt):
            reason = 'interrupted'
        evidence.update(status='failed', failure_stage=phase, error_kind=reason)
    finally:
        evidence['duration_seconds'] = round(time.monotonic() - started, 3)
        if not prepare_only or evidence['status'] == 'failed':
            log.write_text(json.dumps(evidence, ensure_ascii=True) + '\n', encoding='utf-8')
            log.chmod(0o600)
            evidence.update(log_sha256=digest(log), log=log.name)
        write(out / 'manifest.json', evidence)
        write(out / 'status.json', evidence)
    return evidence
