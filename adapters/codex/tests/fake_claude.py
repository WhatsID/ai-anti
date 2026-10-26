#!/usr/bin/env python3
"""Deterministic CLI fixture; not a model and never used by live runs."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

FLAGS = '-p --model --effort --safe-mode --bare --restricted --tools --allowedTools --permission-mode dontAsk --strict-mcp-config --mcp-config --disable-slash-commands --no-session-persistence --verbose --output-format stream-json'
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
qwen = '--bare' in sys.argv
if qwen:
    config = json.loads(sys.argv[sys.argv.index('--mcp-config') + 1])
    server = config['mcpServers']['materials']
    registry = json.loads(Path(server['args'][1]).read_text())
    required = [item['path'] for item in registry['materials'].values()]
    material_ids = {item['path']: mid for mid, item in registry['materials'].items()}
    mapping = {}
else:
    required = json.loads(next(line.removeprefix('Required Read files: ') for line in prompt.split('\n') if line.startswith('Required Read files: ')))
    mapping = json.loads(next(line.removeprefix('Source mapping: ') for line in prompt.split('\n') if line.startswith('Source mapping: ')))
files = {name: ((root / name).read_bytes().decode('utf-8') if qwen else (root / name).read_text()) for name in required}
chunk_names = [block['path'] for source in mapping.values() if len(source['blocks']) > 1 for block in source['blocks']]
middle = chunk_names[len(chunk_names) // 2] if chunk_names else None
audit = Path(os.environ['FAKE_AUDIT'])
with audit.open('a') as f:
    f.write(json.dumps({'argv': sys.argv[1:], 'cwd': str(root), 'files': files,
                       'stdin': sys.stdin.read(), 'env': {k: v for k, v in os.environ.items() if os.environ.get('FAKE_ENV_AUDIT') == '1' and k in ('ANTHROPIC_BASE_URL', 'ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_SONNET_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL', 'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY', 'HTTPS_PROXY')}, 'matches_fixture_token': os.environ.get('ANTHROPIC_API_KEY' if qwen else 'ANTHROPIC_AUTH_TOKEN') == 'OFFLINE_FAKE_TOKEN', 'conflicting_anthropic_present': any(k in os.environ for k in (('ANTHROPIC_AUTH_TOKEN' if qwen else 'ANTHROPIC_API_KEY'), 'ANTHROPIC_MODEL', 'ANTHROPIC_SMALL_FAST_MODEL'))}) + '\n')
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
actual = os.environ.get('FAKE_ACTUAL_MODEL') or (os.environ.get('ANTHROPIC_DEFAULT_SONNET_MODEL') if model == 'sonnet' else None) or actual
if mode.startswith('noise_') or mode == 'recover_noise_3':
    payload = {'model': actual, 'query_source': 'sdk'}
    if mode == 'noise_wrong': payload['model'] = 'wrong'
    if mode == 'noise_extra': payload['extra'] = True
    for _ in range(2 if mode == 'noise_repeat' else 1):
        print('[claude-code:unrecognized_model] ' + json.dumps(payload), flush=True)
    if mode == 'noise_unknown': print('unknown noise', flush=True)
    if mode == 'noise_truncated': print('[claude-code:unrecognized_model] {', flush=True)
plugins = ['bad'] if mode == 'plugin' else []
if 'FAKE_PLUGINS' in os.environ:
    plugins = json.loads(os.environ['FAKE_PLUGINS'])
if mode != 'no_init':
    emit({'type': 'system', 'subtype': 'init', 'model': 'wrong-model' if mode == 'wrong_model' else actual,
          'tools': (['mcp__materials__read_material'] if qwen else ['Read', 'Glob', 'Grep']) + (['Bash'] if mode == 'extra_tool' else []),
          'plugins': plugins, 'mcp_servers': ([{'name': 'materials', 'status': 'connected', 'source': 'dynamic'}] if qwen else []) + ([{'name': 'extra'}] if mode == 'extra_mcp' else []),
          'permissionMode': 'dontAsk', 'skills': [], 'slash_commands': [], 'cwd': str(root)})
# Fixture-only state; no production retry switches.
if os.environ.get('FAKE_RETRY_STATE'):
    state_path = Path(os.environ['FAKE_RETRY_STATE'])
    counts = json.loads(state_path.read_text()) if state_path.exists() else {}
    stage_name = Path.cwd().name
    counts[stage_name] = counts.get(stage_name, 0) + 1
    state_path.write_text(json.dumps(counts))
    target = os.environ.get('FAKE_RETRY_STAGE', stage_name)
    if stage_name == target and counts[stage_name] <= int(os.environ.get('FAKE_RETRY_FAILURES', '1')):
        emit({'type':'result','subtype':'error_during_execution','is_error':True,'permission_denials':[], 'errors':[os.environ.get('FAKE_RETRY_ERROR', 'Selected model is at capacity. Please try a different model')]}); sys.exit(1)


for i, (name, content) in enumerate(files.items()):
    if mode.startswith('recover_') and i == 0:
        for attempt in range(int(mode.rsplit('_', 1)[1])):
            uid = 'typo-' + str(attempt)
            message = 'File does not exist. Note: your current working directory is ' + str(root) + '.'
            emit({'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'name': 'Read', 'id': uid,
                 'input': {'file_path': 'chunks/999/missing-' + str(attempt) + '.txt'}}]}})
            emit({'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': uid, 'content': message, 'is_error': True}]},
                  'tool_use_result': 'Error: ' + message})
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
    if qwen:
        args = {'id': material_ids[name]}
        if mode == 'wrong_id': args['id'] = '999'
        if mode == 'path_parameter': args['path'] = name
        emit({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': uid,
             'name': 'Read' if mode == 'builtin_read' else 'mcp__materials__read_material', 'input': args}]}})
        text = content[:1] if mode == 'partial_read' else content
        payload = [{'type': 'text', 'text': text}]
        event = {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': uid,
                 'content': payload, 'is_error': mode == 'tool_error'}]}, 'tool_use_result': payload}
        if mode == 'forged_meta': event['tool_use_result'] = [{'type': 'text', 'text': 'forged'}]
        emit(event)
        if mode == 'duplicate_result': emit(event)
        if mode == 'repeat_material':
            new_id = uid + '-again'
            emit({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': new_id,
                 'name': 'mcp__materials__read_material', 'input': args}]}})
            event['message']['content'][0]['tool_use_id'] = new_id
            emit(event)
        continue
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

if problem_prompt:
    if 'Problem review stage: independent' in problem_prompt:
        problem_result = os.environ.get('FAKE_PROBLEM_INDEPENDENT', problem_result)
        if os.environ.get('FAKE_PROBLEM_MUTATE_ORIGIN'):
            Path(os.environ['FAKE_PROBLEM_MUTATE_ORIGIN']).write_text('CHANGED AFTER FREEZE')
    result['result'] = problem_result
if mode == 'insufficient_quota':
    result.update(subtype='error_during_execution', is_error=True, result='insufficient_quota')
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
