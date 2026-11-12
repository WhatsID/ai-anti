#!/usr/bin/env python3
"""Synthetic implementation CLI; no model calls or user configuration."""
import json
import os
from pathlib import Path
import sys
import time

if '--version' in sys.argv:
    print('1.1.77')
    sys.exit(0)
root = Path.cwd()
mode = (root/'fake-mode.txt').read_text() if (root/'fake-mode.txt').exists() else 'success'
count = root/'fake-count.txt'
count.write_text(str(int(count.read_text() if count.exists() else '0') + 1))
(root/'partial.txt').write_text('retained candidate\n')
(root/'fake-audit.json').write_text(json.dumps(dict(args=sys.argv[1:], env_keys=sorted(os.environ), home=os.environ.get('HOME'))))
def emit(e): print(json.dumps(e), flush=True)
init = dict(type='system', subtype='init', model='GLM-5.3', cwd=str(root), qodercli_version='1.1.77', protocol_version='1.5.0', tools=['Read','Write','Edit','Bash','Glob','Grep'], permissionMode='acceptEdits', apiKeySource='none', mcp_servers=[], plugins=[], skills=[])
emit(init)
if mode == 'wait': time.sleep(60)
if mode == 'flood':
    print('x' * (9*1024*1024), flush=True)
    sys.exit(0)
emit(dict(type='assistant', message=dict(role='assistant', model='GLM-5.3', stop_reason='end_turn', content=[dict(type='text',text='Candidate ready')])) )
emit(dict(type='result', subtype='success', is_error=False, permission_denials=[], result='Candidate ready', stop_reason='end_turn'))
sys.exit(1 if mode == 'nonzero' else 0)
