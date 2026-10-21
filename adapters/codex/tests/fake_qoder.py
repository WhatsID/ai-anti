#!/usr/bin/env python3
"""Offline Qoder 1.1.74 wire fixture; no live subscription access."""
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
if '--version' in args:
    print(os.environ.get('FAKE_VERSION', '1.1.74'))
    raise SystemExit(0)

def option(name):
    return args[args.index(name) + 1]

config = json.loads(option('--mcp-config'))
registry = json.loads(Path(config['mcpServers']['materials']['args'][-1]).read_text())
materials = {mid: (Path(registry['root']) / item['path']).read_text()
             for mid, item in registry['materials'].items()}
with open(os.environ['FAKE_AUDIT'], 'a') as audit:
    audit.write(json.dumps({'argv': args, 'files': materials,
        'env': {key: os.environ[key] for key in ('QODER_TEST', 'HTTPS_PROXY') if key in os.environ},
        'forbidden_provider_env_present': any(key.startswith(('ANTHROPIC_', 'OPENAI_'))
                                               for key in os.environ)}) + '\n')
tool = 'mcp__materials__read_material'
events = [{'type': 'system', 'subtype': 'init', 'tools': [tool],
           'mcp_servers': [{'name': 'materials', 'status': 'connected'}],
           'skills': [], 'plugins': [], 'permissionMode': 'dontAsk',
           'qodercli_version': '1.1.74', 'model': 'Kimi-K3',
           'slash_commands': ['help', 'review'], 'agents': ['Explore']},
          {'type': 'system', 'subtype': 'artifacts_update', 'artifacts': []}]
for mid, raw in materials.items():
    events.append({'type': 'assistant', 'message': {'model': 'Kimi-K3', 'content': [
        {'type': 'tool_use', 'id': 'call-' + mid, 'name': tool, 'input': {'id': mid}}]}})
    events.append({'type': 'user', 'message': {'content': [{'type': 'tool_result',
        'tool_use_id': 'call-' + mid,
        'content': raw if raw else '(' + tool + ' completed with no output)'}]},
        'tool_use_result': [{'type': 'text', 'text': raw}]})
events.append({'type': 'result', 'subtype': 'success', 'is_error': False,
               'permission_denials': [], 'stop_reason': 'end_turn',
               'result': 'FAKE REPORT', 'total_credits': 0.25})
for event in events:
    print(json.dumps(event, ensure_ascii=False))
