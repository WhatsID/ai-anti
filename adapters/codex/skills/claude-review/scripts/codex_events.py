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
LOADED = re.compile(
    r'Loaded only ([0-9]+) of ([0-9]+) bytes from `([^`\r\n]+)` in environment `local` '
    r'under the 32768-byte project instruction budget\. Trim your project instructions to fit the budget\. '
    r'If you need to keep them all, increase `project_doc_max_bytes`\.'
)
COMBINED = re.compile(
    r'Combined user instructions use ([0-9]+) bytes, above the 32768-byte `project_doc_max_bytes` setting\. '
    r'The total includes global and thread instructions\. The limit applies only to project files\.'
)
RECONNECT = re.compile(r'Reconnecting\.\.\. ([0-9]+)/([0-9]+) \(stream disconnected before completion: tls handshake eof\)')


def stderr_path(logfile):
    return logfile.with_name(logfile.name + '.stderr.txt')


def stderr_warnings(logfile):
    from review_runner import RunError
    if logfile is None:
        return []
    counts = {}
    known = {message: kind for kind, message in STDERR_MESSAGES}
    try:
        with logfile.open(encoding='utf-8') as stream:
            for raw in stream:
                line = raw.rstrip('\n')
                if not line:
                    continue
                if line == STDIN_NOTICE:
                    kind = 'stdin_notice'
                else:
                    stamp, _, message = line.partition(' ERROR ')
                    match = TIMESTAMP.fullmatch(stamp)
                    if not match or message not in known:
                        raise RunError('unknown Codex stderr line')
                    datetime.fromisoformat(match[1])
                    kind = known[message]
                counts[kind] = counts.get(kind, 0) + 1
    except (UnicodeError, ValueError) as exc:
        raise RunError('unknown Codex stderr line') from exc
    return [{'kind': kind, 'stream': 'stderr', 'count': count} for kind, count in counts.items()]


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
