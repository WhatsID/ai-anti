"""Real stdio MCP and independently mutated CLI 2.1.280 evidence contracts."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/claude-review/scripts'
sys.path.insert(0, str(SCRIPTS))
from review_materials import build_registry, load_registry, read_material, mcp_config, TOOL
from review_chunks import chunk_stage
from review_material import MaterialError
from review_runner import validate, RunError
import re


class MaterialIds(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.stage = self.root / 'stage'
        self.stage.mkdir()
        (self.stage / '中文.md').write_bytes('中文第一行\n第二行\n\n'.encode())
        (self.stage / 'empty.txt').write_bytes(b'')
        self.sources = chunk_stage(self.stage)
        self.path = self.root / 'registry.json'
        self.registry = build_registry(self.stage, self.sources, self.path)
        self.first = next(iter(self.registry['materials']))
        self.log = self.root / 'fixture.jsonl'

    def tearDown(self):
        self.temp.cleanup()

    def rpc(self, messages):
        result = subprocess.run([sys.executable, str(SCRIPTS / 'review_materials.py'), str(self.path)],
            input=''.join(json.dumps(v) + '\n' for v in messages), capture_output=True, text=True)
        return result, [json.loads(line) for line in result.stdout.splitlines()]

    def call(self, args, name='read_material'):
        return {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                'params': {'name': name, 'arguments': args}}

    def test_stdio_handshake_original_text_and_eof(self):
        q = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
             {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
             {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]
        mid = next(mid for mid in self.registry['materials'] if read_material(self.registry, mid))
        q.append(self.call({'id': mid}))
        result, responses = self.rpc(q)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(responses[0]['result']['capabilities'], {'tools': {}})
        tool = responses[1]['result']['tools'][0]
        self.assertEqual(tool['inputSchema']['properties']['id']['enum'], list(self.registry['materials']))
        text = responses[2]['result']['content']
        self.assertEqual(text, [{'type': 'text', 'text': '中文第一行\n第二行\n\n'}])
        self.assertNotEqual(text[0]['text'], json.dumps({'content': text[0]['text']}))

    def test_stdio_invalid_arguments_and_methods(self):
        for args in ({}, {'id': 1}, {'id': []}, {'id': '999'}, {'id': self.first, 'path': '/etc/passwd'}):
            result, responses = self.rpc([self.call(args)])
            self.assertEqual(result.returncode, 0)
            self.assertTrue(responses[0]['result']['isError'])
        _, response = self.rpc([self.call({'id': self.first}, name='Read')])
        self.assertTrue(response[0]['result']['isError'])
        _, response = self.rpc([{'jsonrpc': '2.0', 'id': 1, 'method': 'resources/read'}])
        self.assertEqual(response[0]['error']['code'], -32601)
        result, _ = self.rpc([{'method': 'tools/list'}])
        self.assertNotEqual(result.returncode, 0)
        result = subprocess.run([sys.executable, str(SCRIPTS / 'review_materials.py'), str(self.path)],
            input='x' * 65537 + '\n', capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_cli_protocol_metadata_is_not_a_material_argument(self):
        # Claude CLI 2.1.280 captured tools/call params include standard _meta.
        request = self.call({'id': self.first})
        request['params']['_meta'] = {'claudecode/toolUseId': 'toolu_316052299f054a6d94416cd4',
                                      'progressToken': 2}
        result, responses = self.rpc([request])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(responses[0]['result'], {'content': [
            {'type': 'text', 'text': read_material(self.registry, self.first)}]})
        for meta in ({}, {'client/key': {'arbitrary': True}}, {'progressToken': 'client-token'}):
            request['params']['_meta'] = meta
            _, responses = self.rpc([request])
            self.assertNotIn('isError', responses[0]['result'])
        for meta in (None, [], 'metadata', 2, True):
            request['params']['_meta'] = meta
            _, responses = self.rpc([request])
            self.assertTrue(responses[0]['result']['isError'])
        request['params']['_meta'] = {}
        for args in ({'id': self.first, 'path': '/etc/passwd'},
                     {'id': self.first, '_meta': {}}, {'id': '../secret'}):
            request['params']['arguments'] = args
            _, responses = self.rpc([request])
            self.assertTrue(responses[0]['result']['isError'])
        request['params']['arguments'] = {'id': self.first}
        request['params']['path'] = '/etc/passwd'
        _, responses = self.rpc([request])
        self.assertTrue(responses[0]['result']['isError'])

    def test_registry_structure_path_hash_size_and_symlink(self):
        original = copy.deepcopy(self.registry)
        cases = []
        for key, value in [('path', '../secret'), ('path', '/etc/passwd'), ('path', 'chunks/001/../../secret'),
                           ('bytes', -1), ('bytes', 12289), ('bytes', True), ('sha256', '0' * 64)]:
            changed = copy.deepcopy(original)
            changed['materials'][self.first][key] = value
            cases.append(changed)
        changed = copy.deepcopy(original); changed['extra'] = 1; cases.append(changed)
        changed = copy.deepcopy(original); changed['materials']['bad'] = changed['materials'].pop(self.first); cases.append(changed)
        for changed in cases:
            self.path.write_text(json.dumps(changed))
            with self.assertRaises((MaterialError, ValueError)):
                load_registry(self.path)
        self.path.write_text(json.dumps(original))
        target = self.stage / original['materials'][self.first]['path']
        target.unlink()
        target.symlink_to(self.root / 'outside')
        with self.assertRaises(MaterialError): load_registry(self.path)
        result, _ = self.rpc([self.call({'id': self.first})])
        self.assertNotEqual(result.returncode, 0)

    def test_nonregular_snapshot_rejected_without_blocking(self):
        import os
        target = self.stage / self.registry['materials'][self.first]['path']
        target.unlink()
        os.mkfifo(target)
        with self.assertRaises(MaterialError): read_material(self.registry, self.first)

    def test_changed_snapshot_rejected_on_each_read(self):
        target = self.stage / self.registry['materials'][self.first]['path']
        target.write_bytes(b'tampered')
        with self.assertRaises(MaterialError): read_material(self.registry, self.first)

    def events(self):
        events = [{'type': 'system', 'subtype': 'init', 'tools': [TOOL],
                   'mcp_servers': [{'name': 'materials', 'status': 'connected', 'source': 'dynamic'}],
                   'plugins': [{'name': 'agents-md', 'path': 'builtin', 'source': 'agents-md@builtin'},
                               {'name': 'telemetry', 'path': 'builtin', 'source': 'telemetry@builtin'}],
                   'skills': [], 'slash_commands': [], 'permissionMode': 'dontAsk', 'model': 'fixture.v1-max'}]
        for mid in self.registry['materials']:
            events.append({'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'id': 'tool-' + mid, 'name': TOOL, 'input': {'id': mid}}]}})
            content = [{'type': 'text', 'text': read_material(self.registry, mid)}]
            events.append({'type': 'user', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 'tool-' + mid, 'content': content}]}, 'tool_use_result': content})
        events.append({'type': 'result', 'subtype': 'success', 'is_error': False,
                       'permission_denials': [], 'result': '合成报告'})
        return events

    def check(self, events):
        noise = '[claude-code:unrecognized_model] {"model":"fixture.v1-max","query_source":"sdk"}\n'
        self.log.write_text(json.dumps(events[0]) + '\n' + noise + ''.join(json.dumps(e) + '\n' for e in events[1:]))
        return validate(self.log, 0, 'sonnet', self.stage, {'sonnet': re.compile('fixture\\.v1-max')},
                        expected_model='fixture.v1-max', required=[v['path'] for v in self.registry['materials'].values()], registry=self.registry)

    def test_evidence_complete_and_repeated_material(self):
        events = self.events()
        self.assertEqual(len(self.check(events)['complete_reads']), 2)
        pair = copy.deepcopy(events[1:3]); pair[0]['message']['content'][0]['id'] = 'again'
        pair[1]['message']['content'][0]['tool_use_id'] = 'again'
        events[-1:-1] = pair
        self.assertEqual(len(self.check(events)['complete_reads']), 2)

    def test_cli_empty_output_wrapper_is_only_for_verified_empty_material(self):
        # Real CLI 2.1.280 preserves the empty top-level text but wraps message output.
        events = self.events()
        empty_mid = next(mid for mid in self.registry['materials']
                         if read_material(self.registry, mid) == '')
        empty_result = next(event for event in events if event.get('type') == 'user'
                            and event['message']['content'][0]['tool_use_id'] == 'tool-' + empty_mid)
        wrapper = '(' + TOOL + ' completed with no output)'
        empty_result['message']['content'][0]['content'] = wrapper
        self.assertEqual(len(self.check(events)['complete_reads']), 2)
        for wrong in ('completed with no output', wrapper + ' ', ''):
            changed = copy.deepcopy(events)
            result = next(e for e in changed if e.get('type') == 'user'
                          and e['message']['content'][0]['tool_use_id'] == 'tool-' + empty_mid)
            result['message']['content'][0]['content'] = wrong
            with self.assertRaises(RunError): self.check(changed)
        for top in (None, [], [{'type': 'text', 'text': wrapper}], wrapper):
            changed = copy.deepcopy(events)
            result = next(e for e in changed if e.get('type') == 'user'
                          and e['message']['content'][0]['tool_use_id'] == 'tool-' + empty_mid)
            if top is None:
                result.pop('tool_use_result')
            else:
                result['tool_use_result'] = top
            with self.assertRaises(RunError): self.check(changed)
        changed = self.events()
        nonempty = next(e for e in changed if e.get('type') == 'user'
                        and e['tool_use_result'][0]['text'] != '')
        nonempty['message']['content'][0]['content'] = wrapper
        with self.assertRaises(RunError): self.check(changed)

    def test_evidence_forgery_missing_duplicate_and_unsafe_init(self):
        cases = []
        for key, value in [('tools', [TOOL, 'Read']), ('mcp_servers', []), ('skills', ['hidden']),
                           ('permissionMode', 'default'), ('model', 'fixtureXv1-max')]:
            events = self.events(); events[0][key] = value; cases.append(events)
        for key, value in [('name', 'Read'), ('input', {'id': '999'}), ('input', {'id': self.first, 'path': '../a'})]:
            events = self.events(); events[1]['message']['content'][0][key] = value; cases.append(events)
        events = self.events(); events.insert(1, {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'error', 'is_error': True}]}}); cases.append(events)
        events = self.events(); events[2]['tool_use_result'] = []; cases.append(events)
        events = self.events(); events[2]['message']['content'][0]['content'] = [{'type': 'text', 'text': 'forged'}]; cases.append(events)
        events = self.events(); events[2]['message']['content'][0]['is_error'] = True; cases.append(events)
        events = self.events(); events.insert(3, copy.deepcopy(events[2])); cases.append(events)
        events = self.events(); events.insert(3, copy.deepcopy(events[1])); cases.append(events)
        events = self.events(); del events[1:3]; cases.append(events)
        events = self.events(); del events[2]; cases.append(events)
        for events in cases:
            with self.assertRaises(RunError): self.check(events)

    def test_mcp_environment_erases_credentials(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {'ANTHROPIC_AUTH_TOKEN': 'secret', 'ANTHROPIC_BASE_URL': 'endpoint', 'ANTHROPIC_NEW_KEY': 'secret'}):
            env = mcp_config(self.path)['mcpServers']['materials']['env']
            self.assertEqual(env['ANTHROPIC_AUTH_TOKEN'], '')
            self.assertEqual(env['ANTHROPIC_BASE_URL'], '')
            self.assertEqual(env['ANTHROPIC_NEW_KEY'], '')
            self.assertNotIn('secret', json.dumps(mcp_config(self.path)))


if __name__ == '__main__':
    unittest.main()
