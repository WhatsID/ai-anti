"""Bounded CLI execution and evidence validation (Python 3.9, no shell)."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time


class RunError(RuntimeError):
    def __init__(self, message, kind="execution_failed"):
        super().__init__(message)
        self.kind = kind


ALLOWED = {'Read', 'Glob', 'Grep'}
# Built-in accepted actual model IDs per requested alias: same family, numeric version
# segments only (optional 8-digit date). Cross-family or non-numeric suffixes fail closed.
# claude_review.model_families in the user config may override or add aliases.
MODEL_FAMILIES = {
    'opus': re.compile(r'claude-opus-[0-9]+(?:-[0-9]+)*'),
    'fable': re.compile(r'claude-fable-[0-9]+(?:-[0-9]+)*'),
}
# Built-in plugins the CLI reports even under --safe-mode (2.1.280: "built-in tools and
# plugins ... work normally"). Only these exact builtin entries are tolerated; anything else fails closed.
ALLOWED_BUILTIN_PLUGINS = frozenset({'agents-md', 'telemetry'})
FLAGS = [
    '--effort', 'medium', '--safe-mode', '--restricted',
    '--tools', 'Read,Glob,Grep', '--allowedTools', 'Read,Glob,Grep',
    '--permission-mode', 'dontAsk', '--strict-mcp-config', '--mcp-config',
    '{"mcpServers":{}}', '--disable-slash-commands', '--no-session-persistence',
    '--verbose', '--output-format', 'stream-json',
]


def stop_group(process):
    """Kill descendants even when the CLI parent already exited."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        except PermissionError as exc:
            raise RunError('process group cleanup denied by host permissions') from exc
        if sig == signal.SIGTERM:
            time.sleep(.15)
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired as exc:
        raise RunError('CLI did not stop after process group cleanup') from exc


def builtin_plugins_only(plugins):
    if not isinstance(plugins, list):
        return False
    names = []
    for item in plugins:
        if not isinstance(item, dict):
            return False
        name = item.get('name')
        if (not isinstance(name, str) or name not in ALLOWED_BUILTIN_PLUGINS or item.get('path') != 'builtin'
                or item.get('source') != f'{name}@builtin'):
            return False
        names.append(name)
    return len(names) == len(set(names))


def execute(args, cwd, logfile, timeout, idle, cap):
    """Full stdout/stderr on disk; bounded monitoring, no pipe buffering."""
    started = time.monotonic()
    progress_at = last_output = started
    size = 0
    reason = None
    with logfile.open('wb') as log:
        try:
            process = subprocess.Popen(
                args, cwd=str(cwd), stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
        except OSError as exc:
            raise RunError('could not launch CLI: ' + str(exc)) from exc
        try:
            while True:
                now = time.monotonic()
                new_size = logfile.stat().st_size
                if new_size != size:
                    size, last_output = new_size, now
                if size > cap:
                    reason = 'log limit'
                    break
                if process.poll() is not None:
                    break
                if now - started >= timeout:
                    reason = 'timeout'
                    break
                if now - last_output >= idle:
                    reason = 'idle timeout'
                    break
                if now - progress_at >= 15:
                    print(logfile.stem + ': running, ' + str(size) + ' log bytes', file=sys.stderr)
                    progress_at = now
                time.sleep(.1)
        except KeyboardInterrupt:
            reason = 'interrupted'
        finally:
            stop_group(process)
    if logfile.stat().st_size > cap:
        reason = 'log limit'
    if reason:
        raise RunError(reason)
    return process.returncode


def capability(claude, out):
    result = {}
    for flag in ('--version', '--help'):
        path = out / (flag[2:] + '.txt')
        code = execute([claude, flag], out, path, 20, 15, 65536)
        data = path.read_bytes()
        result[flag[2:]] = {
            'exit': code, 'sha256': hashlib.sha256(data).hexdigest(),
        }
        if code:
            raise RunError('CLI capability preflight failed: ' + flag)
        text = data.decode('utf-8', errors='strict')
        if flag == '--version':
            result['cli_version'] = text.strip()
        else:
            needed = ['--model'] + [x for x in FLAGS if x.startswith('--')]
            if any(item not in text for item in needed):
                raise RunError('CLI capability mismatch: required flags unavailable')
    result['review_flags'] = FLAGS
    return result


def run_stage(claude, cwd, prompt, logfile, model, timeout, idle, cap, families=None):
    # Prompt is outside material cwd: no accidental recursive reviewer input.
    prompt_file = logfile.with_suffix('.prompt.txt')
    prompt_file.write_text(prompt, encoding='utf-8')
    args = [claude, '-p', prompt_file.read_text(encoding='utf-8'), '--model', model] + FLAGS
    print(logfile.stem + ': started', file=sys.stderr)
    started = time.time()
    code = execute(args, cwd, logfile, timeout, idle, cap)
    result = validate(logfile, code, model, cwd, families)
    result['started_at'] = started
    result['finished_at'] = time.time()
    result['log_sha256'] = hashlib.sha256(logfile.read_bytes()).hexdigest()
    return result


def blocks(event):
    message = event.get('message', {})
    if not isinstance(message, dict):
        raise RunError('malformed message envelope')
    content = message.get('content', [])
    if not isinstance(content, list) or any(not isinstance(x, dict) for x in content):
        raise RunError('malformed message content')
    return content


def record_read(event, pending, cwd, coverage):
    """Require matching tool id, path, actual file text and covered line range."""
    meta = event.get('tool_use_result')
    if not isinstance(meta, dict) or meta.get('type') != 'text':
        return
    file = meta.get('file')
    if not isinstance(file, dict):
        return
    ids = [x.get('tool_use_id') for x in blocks(event) if x.get('type') == 'tool_result']
    read = next((pending[x] for x in ids if x in pending), None)
    if read is None:
        return
    try:
        given = Path(read['file_path'])
        given = (cwd / given).resolve() if not given.is_absolute() else given.resolve()
        path = Path(file['filePath'])
        path = (cwd / path).resolve() if not path.is_absolute() else path.resolve()
        relative = str(path.relative_to(cwd))
        if path != given:
            raise ValueError('Read metadata path mismatch')
        source = path.read_text(encoding='utf-8')
        lines = source.split('\n')
        start, count, total = (file[k] for k in ('startLine', 'numLines', 'totalLines'))
        if any(type(x) is not int for x in (start, count, total)):
            raise ValueError('invalid Read range')
        if source == '' and count == total == 0:
            coverage[relative] = {0}
            return
        if total != len(lines) or start < 1 or count < 0 or start - 1 + count > total:
            raise ValueError('Read range does not match snapshot')
        if file.get('content') != '\n'.join(lines[start - 1:start - 1 + count]):
            raise ValueError('Read text does not match snapshot')
        coverage.setdefault(relative, set()).update(range(start - 1, start - 1 + count))
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise RunError('invalid Read evidence: ' + str(exc)) from exc


def validate(logfile, code, requested, cwd, families=None):
    inits, finals, pending, coverage = [], [], {}, {}
    try:
        events = [json.loads(line) for line in logfile.read_text('utf-8').splitlines()]
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RunError('truncated or malformed stream-json') from exc
    for event in events:
        if isinstance(event, dict) and (event.get('error') == 'rate_limit' or (
                event.get('type') == 'rate_limit_event'
                and event.get('rate_limit_info', {}).get('status') == 'rejected')):
            raise RunError('Claude quota exhausted; no retry or fallback performed', 'quota_exhausted')
        if not isinstance(event, dict):
            raise RunError('non-object stream event')
        kind = event.get('type')
        if finals and kind in ('assistant', 'user', 'result'):
            raise RunError('unexpected content after terminal result')
        if kind == 'system' and event.get('subtype') == 'init':
            inits.append(event)
        if kind == 'result':
            finals.append(event)
        if event.get('is_error') or event.get('permission_denials') or event.get('subtype') == 'permission_denied':
            raise RunError('incomplete review: error or permission denial')
        for block in blocks(event):
            if block.get('is_error'):
                raise RunError('incomplete review: tool error')
            if block.get('type') == 'tool_use':
                if block.get('name') not in ALLOWED:
                    raise RunError('unexpected tool invocation')
                if block.get('name') == 'Read':
                    pending[block.get('id')] = block.get('input', {})
        record_read(event, pending, cwd, coverage)
    if code:
        raise RunError('nonzero CLI exit: ' + str(code))
    if len(inits) != 1 or len(finals) != 1:
        raise RunError('expected exactly one init and terminal result')
    init, final = inits[0], finals[0]
    if (set(init.get('tools', [])) != ALLOWED or init.get('mcp_servers') != []
            or not builtin_plugins_only(init.get('plugins')) or init.get('permissionMode') != 'dontAsk'
            or init.get('skills') != [] or init.get('slash_commands') != []):
        raise RunError('unsafe or missing init capability set')
    actual = init.get('model', '')
    family = (MODEL_FAMILIES if families is None else families).get(requested)
    if family is None:
        raise RunError('unknown requested model alias: ' + str(requested))
    if not isinstance(actual, str) or not family.fullmatch(actual):
        raise RunError('unexpected actual model: ' + str(actual))
    if (final.get('is_error') is not False or final.get('permission_denials') != []
            or not isinstance(final.get('result'), str) or not final['result'].strip()
            or final.get('subtype') != 'success'):
        raise RunError('incomplete or malformed review result')
    complete = set()
    for relative, indices in coverage.items():
        count = len((cwd / relative).read_text(encoding='utf-8').split('\n'))
        if indices == set(range(count)):
            complete.add(relative)
    return {'model': actual, 'result': final['result'], 'complete_reads': complete}
