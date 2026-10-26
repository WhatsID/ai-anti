#!/usr/bin/env python3
"""Offline Codex JSONL fixture. Uses frozen registry, never a network."""
import json
import os
from pathlib import Path
import sys
args = sys.argv[1:]
if '--version' in args:
    print(os.environ.get('FAKE_CODEX_VERSION', 'codex-cli 0.160.0'))
    raise SystemExit(int(os.environ.get('FAKE_CODEX_VERSION_EXIT', '0')))
if '--help' in args:
    print('--no-daemon --config --model --ignore-user-config --ignore-rules --ephemeral --skip-git-repo-check --sandbox --json'); raise SystemExit()
options = {}
for i, arg in enumerate(args):
    if arg == '-c':
        key, value = args[i + 1].split('=', 1); options[key] = json.loads(value)
audit = os.environ.get('FAKE_CODEX_AUDIT')
if audit:
    Path(audit).write_text(json.dumps({'args': args, 'env': {k: v for k, v in os.environ.items() if k.startswith(('CODEX_', 'OPENAI_', 'ANTHROPIC_', 'CLAUDE_')) or k in ('HOME', 'HTTPS_PROXY')}, 'token_present': os.environ.get('AI_ANTI_QWEN_TOKEN') == 'OFFLINE_FAKE_TOKEN'}))
registry = json.loads(Path(options['mcp_servers.materials.args'][1]).read_text())
mode = os.environ.get('FAKE_CODEX_MODE', '')
def emit(**event): print(json.dumps(event, ensure_ascii=False), flush=True)
emit(type='thread.started', thread_id='fixture-thread')
emit(type='turn.started')

# Fixture-only state; no production retry switches.
if os.environ.get('FAKE_RETRY_STATE'):
    state_path = Path(os.environ['FAKE_RETRY_STATE'])
    counts = json.loads(state_path.read_text()) if state_path.exists() else {}
    stage_name = Path.cwd().name
    counts[stage_name] = counts.get(stage_name, 0) + 1
    state_path.write_text(json.dumps(counts))
    target = os.environ.get('FAKE_RETRY_STAGE', stage_name)
    if stage_name == target and counts[stage_name] <= int(os.environ.get('FAKE_RETRY_FAILURES', '1')):
        emit(type='turn.failed', error={'type':os.environ.get('FAKE_RETRY_ERROR', 'overloaded_error')}); sys.exit(1)

emit(type='item.completed', item={'id': 'progress', 'type': 'agent_message', 'text': 'Progress only'})
for index, (mid, material) in enumerate(registry['materials'].items()):
    if mode == 'missing' and index == 0: continue
    item = dict(id='tool' + str(index), type='mcp_tool_call', server='materials', tool='read_material', arguments={'id': mid}, result=None, error=None, status='in_progress')
    if mode == 'extra_tool': item['type'] = 'command_execution'
    emit(type='item.started', item=item)
    raw = (Path(registry['root']) / material['path']).read_text()
    if mode == 'wrong_text': raw += 'forged'
    item.update(status='completed', result={'content': [{'type': 'text', 'text': raw}], 'structured_content': None})
    emit(type='item.completed', item=item)
    if mode == 'duplicate': emit(type='item.completed', item=item)

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

if mode != 'progress_only': emit(type='item.completed', item={'id': 'final', 'type': 'agent_message', 'text': problem_result if problem_prompt else 'FAKE REPORT 中文评审'})
if mode != 'no_terminal': emit(type='turn.completed', usage={'input_tokens': 1, 'output_tokens': 1})
if mode == 'duplicate_terminal': emit(type='turn.completed')
if mode == 'broken': print('{')
if mode == 'nonzero': raise SystemExit(1)
