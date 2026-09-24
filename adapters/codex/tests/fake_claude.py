#!/usr/bin/env python3
"""Deterministic CLI fixture; not a model and never used by live runs."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

FLAGS = '-p --model --effort --safe-mode --restricted --tools --allowedTools --permission-mode dontAsk --strict-mcp-config --mcp-config --disable-slash-commands --no-session-persistence --verbose --output-format stream-json'
mode = os.environ.get('FAKE_MODE', 'success')
if '--version' in sys.argv:
    print(os.environ.get('FAKE_VERSION', '2.1.258 (fixture)'))
    sys.exit(0)
if '--help' in sys.argv:
    print(FLAGS if mode != 'old_cli' else '-p --model')
    sys.exit(0)

def emit(event):
    print(json.dumps(event), flush=True)

root = Path.cwd()
prompt = sys.argv[sys.argv.index('-p') + 1]
required = json.loads(next(line.removeprefix('Required Read files: ') for line in prompt.split('\n') if line.startswith('Required Read files: ')))
files = {name: (root / name).read_text() for name in required}
chunk_names = [name for name in required if name.startswith('chunks/')]
middle = chunk_names[len(chunk_names) // 2] if chunk_names else None
audit = Path(os.environ['FAKE_AUDIT'])
with audit.open('a') as f:
    f.write(json.dumps({'argv': sys.argv[1:], 'cwd': str(root), 'files': files,
                       'stdin': sys.stdin.read()}) + '\n')
if mode == 'quota':
    emit({'type': 'rate_limit_event', 'rate_limit_info': {'status': 'rejected', 'rateLimitType': 'seven_day'}})
    sys.exit(1)
if mode == 'nonzero':
    sys.exit(4)
if mode in ('hang', 'child_exit'):
    heartbeat = os.environ['FAKE_HEARTBEAT']
    child = subprocess.Popen([sys.executable, '-c',
        'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);'
        'f=open(' + repr(heartbeat) + ',"a");'
        '\nwhile True: f.write("x");f.flush();time.sleep(.1)'])
    Path(heartbeat + '.pid').write_text(str(child.pid))
    if mode == 'child_exit':
        time.sleep(.2)
        sys.exit(0)
    time.sleep(60)
if mode == 'flood':
    print('x' * 2000000, flush=True)
    time.sleep(60)
model = sys.argv[sys.argv.index('--model') + 1]
actual = 'claude-opus-5-5' if model == 'opus' else 'claude-fable-5-1'
actual = os.environ.get('FAKE_ACTUAL_MODEL') or actual
plugins = ['bad'] if mode == 'plugin' else []
if 'FAKE_PLUGINS' in os.environ:
    plugins = json.loads(os.environ['FAKE_PLUGINS'])
if mode != 'no_init':
    emit({'type': 'system', 'subtype': 'init', 'model': 'wrong-model' if mode == 'wrong_model' else actual,
          'tools': ['Read', 'Glob', 'Grep'] + (['Bash'] if mode == 'extra_tool' else []),
          'plugins': plugins, 'mcp_servers': [],
          'permissionMode': 'dontAsk', 'skills': [], 'slash_commands': [], 'cwd': str(root)})
for i, (name, content) in enumerate(files.items()):
    if mode == 'no_read':
        break
    if mode == 'missing_chunk' and name == middle:
        continue
    if mode == 'tamper_chunk' and name == middle:
        (root / name).write_text(content + 'tampered')
        content += 'tampered'
    if mode == 'bad_chunk_evidence' and name == middle:
        content += 'forged'
    path = str(root / name)
    uid = 'read-' + str(i)
    emit({'type': 'assistant', 'message': {'role': 'assistant', 'content': [
        {'type': 'tool_use', 'name': 'Read', 'id': uid, 'input': {'file_path': path}}]}})
    lines = content.split('\n')
    n = len(lines)
    if mode == 'partial_read':
        lines = lines[:1]
    emit({'type': 'user', 'message': {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': uid, 'content': '\n'.join(
            str(j + 1) + '\t' + line for j, line in enumerate(lines)),
         'is_error': mode == 'tool_error'}]},
        'tool_use_result': {'type': 'text', 'file': {'filePath': path,
            'content': '\n'.join(lines), 'startLine': 1, 'numLines': len(lines), 'totalLines': n}}})
if mode == 'no_result':
    sys.exit(0)
result = {'type': 'result', 'subtype': 'success', 'is_error': mode == 'error',
          'permission_denials': [{'tool_name': 'Read'}] if mode == 'denied' else [],
          'result': '' if mode == 'empty' else 'FAKE REPORT: 必修：除零输入会失败。\n安全一：无\n安全二：无\n资源一：无\n资源二：无'}
if mode == 'large_inventory' and root.name == 'inventory':
    result['result'] = ('INVENTORY 原文保持\r\n' * 500) + 'inventory-tail'
if mode == 'long_inventory' and root.name == 'inventory':
    result['result'] = 'x' * 2001
if mode == 'overflow_inventory' and root.name == 'inventory':
    result['result'] = ('x' * 1000 + '\n') * 1000
if mode == 'missing_error':
    result.pop('is_error')
if mode == 'missing_denials':
    result.pop('permission_denials')
if mode == 'bad_read_text':
    emit({'type': 'user', 'message': {'content': 'invalid envelope'}})
emit(result)
if mode == 'double_result':
    emit(result)
if mode == 'broken_json':
    print('{unfinished', flush=True)
