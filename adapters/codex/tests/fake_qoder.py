#!/usr/bin/env python3
"""Offline Qoder wire fixture; no live subscription access."""
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
           'qodercli_version': os.environ.get('FAKE_INIT_VERSION', os.environ.get('FAKE_VERSION', '1.1.74')),
           'model': os.environ.get('FAKE_INIT_MODEL', 'Kimi-K3.1-Pro'),
           'slash_commands': ['help', 'review'], 'agents': ['Explore']},
          {'type': 'system', 'subtype': 'artifacts_update', 'artifacts': []}]
for mid, raw in materials.items():
    events.append({'type': 'assistant', 'message': {'model': os.environ.get('FAKE_ASSISTANT_MODEL', 'Kimi-K3.1-Pro'), 'content': [
        {'type': 'tool_use', 'id': 'call-' + mid, 'name': tool, 'input': {'id': mid}}]}})
    events.append({'type': 'user', 'message': {'content': [{'type': 'tool_result',
        'tool_use_id': 'call-' + mid,
        'content': raw if raw else '(' + tool + ' completed with no output)'}]},
        'tool_use_result': [{'type': 'text', 'text': raw}]})

# Opt-in diagnostic fixture; existing plan/diff behavior remains unchanged.
problem_prompt = next((arg for arg in sys.argv if 'Problem review stage: ' in arg), '')
if problem_prompt:
    problem_result = ('INDEPENDENT: candidate alternatives and missing checks' if
                      'Problem review stage: independent' in problem_prompt else
                      json.dumps({'assessment': 'partially_supported', 'findings': [
                          {'id': 'F1', 'claim': 'Candidate cause', 'assessment': 'partially_supported',
                           'evidence_ids': ['E1'], 'reason': 'Visible excerpt supports only part of the claim',
                           'alternative': 'Another explanation remains', 'next_check': {
                               'needed': 'Additional trace', 'purpose': 'Separate candidates',
                               'distinguishing_observation': 'Observe a correlated event'}}],
                          'coverage': 'Declared excerpts only', 'limitations': ['No production verification']}))
    if os.environ.get('FAKE_PROBLEM_RESULT') and 'Problem review stage: review' in problem_prompt:
        problem_result = os.environ['FAKE_PROBLEM_RESULT']

events.append({'type': 'result', 'subtype': 'success', 'is_error': False,
               'permission_denials': [], 'stop_reason': 'end_turn',
               'result': problem_result if problem_prompt else 'FAKE REPORT', 'total_credits': 0.25})
for event in events:
    print(json.dumps(event, ensure_ascii=False))
