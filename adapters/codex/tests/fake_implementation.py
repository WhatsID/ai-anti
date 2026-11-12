#!/usr/bin/env python3
"""Offline write-capable CLI fixture; controlled only by workspace files."""
import json
import os
from pathlib import Path
import sys
import time

if '--version' in sys.argv:
    print('codex-cli 0.163.0'); sys.exit()
if '--help' in sys.argv:
    print('--no-daemon --config --model --ignore-user-config --ignore-rules --ephemeral --skip-git-repo-check --sandbox --json'); sys.exit()
workspace = Path.cwd()
mode = (workspace / 'fake-mode.txt').read_text() if (workspace / 'fake-mode.txt').is_file() else 'success'
count = workspace / 'fake-count.txt'
count.write_text(str(int(count.read_text()) + 1 if count.exists() else 1))
args = sys.argv[1:]
options = {}
for i, arg in enumerate(args):
    if arg == '-c':
        key, value = args[i + 1].split('=', 1); options[key] = json.loads(value)
(workspace / 'fake-audit.json').write_text(json.dumps(dict(args=args, options=options, env_keys=sorted(os.environ), token_present=os.environ.get('AI_ANTI_QWEN_TOKEN') == 'OFFLINE_FAKE_TOKEN', home=os.environ.get('CODEX_HOME'))))
def emit(kind, **values): print(json.dumps(dict(type=kind, **values)), flush=True)
emit('thread.started', thread_id='offline')
emit('turn.started')
if mode == 'wait': time.sleep(60)
if mode == 'command-repair':
    emit('item.completed', item=dict(type='command_execution', exit_code=1, status='failed'))
if mode == 'failed': emit('turn.failed', error={'message': 'offline error'})
if mode == 'multi-turn': emit('turn.started')
if mode == 'wait': time.sleep(60)
if mode == 'unknown': emit('item.completed', item=dict(type='reasoning', text='offline'), model='unverified-model')
if mode != 'no-final': emit('item.completed', item=dict(type='agent_message', text='Offline candidate complete; primary acceptance required'))
if mode != 'no-terminal': emit('turn.completed')
if mode == 'duplicate-terminal': emit('turn.completed')
if mode == 'nonzero': sys.exit(1)
