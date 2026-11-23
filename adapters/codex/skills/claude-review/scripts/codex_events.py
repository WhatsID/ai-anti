"""Narrow matches for Codex exec noise observed on 0.169.1."""
from datetime import datetime
import re

STDIN_NOTICE = 'Reading additional input from stdin...'
STDERR_MESSAGES = (
    ('model_refresh', 'codex_models_manager::manager: failed to refresh available models: request timed out'),
    ('model_catalog', 'codex_models_manager::manager: failed to initialize model catalog: request timed out'),
    ('responses_websocket', 'codex_api::endpoint::responses_websocket: failed to connect to Responses WebSocket stage=Unknown'),
    ('remote_mcp_unavailable',
     'rmcp::transport::worker: worker quit with fatal: Transport channel closed, when Client(HttpRequest(HttpRequest('
     '"http/request failed: error sending request for url (https://chatgpt.com/backend-api/ps/mcp)")))'),
)
TIMESTAMP = re.compile(r'([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.[0-9]+)?Z')
TRACING_PREFIX = re.compile(TIMESTAMP.pattern + r' (?:TRACE|DEBUG|INFO|WARN|ERROR) ')
WRITE_STDIN = re.compile(r'codex_core::tools::router: error=write_stdin failed: Unknown process id [1-9][0-9]*')
APPLY_PATCH = re.compile(
    r'codex_core::tools::router: error=apply_patch verification failed: Failed to find expected lines in /[^\r\n]*:'
)
PANIC = re.compile(r"thread '.*' panicked at ")
DIAGNOSTIC = re.compile(r'(?:TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL)\b|(?:error|warning|Error|Warning)(?:\[|:)')
LOADED = re.compile(
    r'Loaded only ([0-9]+) of ([0-9]+) bytes from `([^`\r\n]+)` in environment `local` '
    r'under the 32768-byte project instruction budget\. Trim your project instructions to fit the budget\. '
    r'If you need to keep them all, increase `project_doc_max_bytes`\.'
)
COMBINED = re.compile(
    r'Combined user instructions use ([0-9]+) bytes, above the 32768-byte `project_doc_max_bytes` setting\. '
    r'The total includes global and thread instructions\. The limit applies only to project files\.'
)
RECONNECT_REASONS = (
    'tls handshake eof',
    'IO error: peer closed connection without sending TLS close_notify: '
    'https://docs.rs/rustls/latest/rustls/manual/_03_howto/index.html#unexpected-eof',
)
RECONNECT = re.compile(
    r'Reconnecting\.\.\. ([0-9]+)/([0-9]+) \(stream disconnected before completion: (?:'
    + '|'.join(map(re.escape, RECONNECT_REASONS)) + r')\)'
)


def stderr_path(logfile):
    return logfile.with_name(logfile.name + '.stderr.txt')


def stderr_warnings(logfile, *, implementation=False):
    from review_runner import RunError
    if logfile is None:
        return []
    counts = {}
    known = {message: kind for kind, message in STDERR_MESSAGES}
    context_lines = 0
    block_has_text = None
    try:
        with logfile.open(encoding='utf-8') as stream:
            for raw in stream:
                line = raw.rstrip('\n')
                if block_has_text is not None:
                    # Recognize tracing syntax here so the top-level parser can check the timestamp.
                    if line == STDIN_NOTICE or TRACING_PREFIX.match(line):
                        if not block_has_text:
                            raise RunError('unknown Codex stderr line')
                        block_has_text = None
                    else:
                        if PANIC.match(line) or DIAGNOSTIC.match(line):
                            raise RunError('unknown Codex stderr line')
                        context_lines += 1
                        block_has_text = block_has_text or bool(line.strip())
                        continue
                if not line:
                    continue
                if line == STDIN_NOTICE:
                    kind = 'stdin_notice'
                else:
                    stamp, separator, message = line.partition(' ERROR ')
                    match = TIMESTAMP.fullmatch(stamp)
                    if not separator or not match:
                        raise RunError('unknown Codex stderr line')
                    datetime.fromisoformat(match[1])
                    kind = known.get(message)
                    if implementation and WRITE_STDIN.fullmatch(message):
                        kind = 'write_stdin_unknown_process'
                    elif implementation and APPLY_PATCH.fullmatch(message):
                        kind = 'apply_patch_verification'
                        block_has_text = False
                    if kind is None:
                        raise RunError('unknown Codex stderr line')
                counts[kind] = counts.get(kind, 0) + 1
        if block_has_text is False:
            raise RunError('unknown Codex stderr line')
    except (UnicodeError, ValueError) as exc:
        raise RunError('unknown Codex stderr line') from exc
    warnings = []
    for kind, count in counts.items():
        warning = {'kind': kind, 'stream': 'stderr', 'count': count}
        if kind == 'apply_patch_verification':
            warning['context_lines'] = context_lines
        warnings.append(warning)
    return warnings


def known_item_warning(event):
    item = event.get('item')
    if (event.get('type') != 'item.completed' or not isinstance(item, dict)
            or set(item) != {'id', 'type', 'message'} or item['type'] != 'error'
            or not isinstance(item['id'], str) or not item['id'] or not isinstance(item['message'], str)):
        return None
    if LOADED.fullmatch(item['message']) or COMBINED.fullmatch(item['message']):
        return {'kind': 'project_doc_budget', 'id': item['id'], 'message': item['message']}
    return None


def known_reconnect(event):
    if set(event) != {'type', 'message'} or event['type'] != 'error' or not isinstance(event['message'], str):
        return None
    match = RECONNECT.fullmatch(event['message'])
    if match and 1 <= int(match[1]) < int(match[2]):
        return {'kind': 'reconnect', 'message': event['message']}
    return None
