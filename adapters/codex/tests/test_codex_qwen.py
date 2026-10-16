"""Offline Responses executor contracts; no external service."""
import json
import shutil
from pathlib import Path
import sys
import unittest
import test_qwen_review as qwen

class CodexQwenTests(qwen.QwenReviewTests):
    def setUp(self):
        super().setUp()
        shutil.copy(Path(__file__).with_name('fake_codex.py'), self.bindir / 'codex')
        (self.bindir / 'codex').chmod(0o755)
        self.qconfig['codex_relay']['qwen_executor'] = 'codex'
        self.qconfig['plugins']['qwen']['responses_base_url'] = 'https://fixture.invalid/v1'
        self.write_config(self.qconfig)

    def test_codex_prepare_contract(self):
        r, out = self.run_review(extra=['--prepare-only', '--qwen-executor', 'codex'])
        self.assert_success(r, out, 'prepared')
        self.assertEqual(self.manifest(out)['executor'], 'codex')

    def test_codex_plan_and_diff(self):
        for mode in ('plan', 'diff'):
            r, out = self.run_review(mode)
            self.assert_success(r, out)
            manifest = self.manifest(out)
            self.assertIsNone(manifest['actual_model'])
            self.assertEqual(manifest['model_evidence'], 'requested_only')
            self.assertEqual(manifest['cli_model'], 'fixture.v1-max')
            self.assertEqual(manifest['wire_api'], 'responses')
            self.assertIn('requested_only', (out / 'report.md').read_text())
            self.assertFalse(list((out / 'logs').glob('*.mcp.json')))
            for stage in manifest['stages'].values():
                self.assertIsNone(stage['model'])
                self.assertGreaterEqual(stage['duration_seconds'], 0)
            config = json.loads((out / 'logs/review.codex-config.json').read_text())
            self.assertEqual(config['mcp_servers.materials.env.AI_ANTI_QWEN_TOKEN'], '')
            self.assertEqual(config['approval_policy'], 'never')
            self.assertEqual(config['model_reasoning_effort'], 'medium')
            self.assertFalse(config['features.shell_tool'])
            self.assertNotIn('user_instructions', config)
            self.assertTrue(config['suppress_unstable_features_warning'])
            self.assertNotIn('OFFLINE_FAKE_TOKEN', json.dumps(config))

    def test_codex_fail_closed(self):
        for mode in ('missing', 'extra_tool', 'wrong_text', 'duplicate', 'progress_only',
                     'no_terminal', 'duplicate_terminal', 'broken', 'nonzero'):
            with self.subTest(mode=mode):
                r, out = self.run_review(env_extra={'FAKE_CODEX_MODE': mode})
                self.assertNotEqual(r.returncode, 0)
                self.assertFalse((out / 'report.md').exists())

    def test_codex_prepare_without_secret(self):
        self.credential.unlink()
        (self.bindir / 'codex').unlink()
        r, out = self.run_review(extra=['--prepare-only'])
        self.assert_success(r, out, 'prepared')

    def test_codex_config_boundaries(self):
        for value in ('', 'https://a:bad', 'https://u:p@a/v1', 'https://a?q=x',
                      'https://a/#x', 'https://a?', 'https://a#', 'https://[bad', 'https://a/a b', 'ftp://a'):
            r, _ = self.run_review(extra=['--prepare-only', '--responses-base-url', value])
            self.assertNotEqual(r.returncode, 0)
        for extra in (['--channel', 'claude', '--qwen-executor', 'codex'],
                      ['--qwen-executor', 'claude', '--responses-base-url', 'https://a']):
            r, _ = self.run_review(extra=extra + ['--prepare-only'])
            self.assertNotEqual(r.returncode, 0)

    def test_codex_environment_and_argv(self):
        audit = self.base / 'codex-audit.json'
        inherited = {'FAKE_CODEX_AUDIT': str(audit), 'OPENAI_API_KEY': 'parent-secret',
                     'ANTHROPIC_AUTH_TOKEN': 'parent-secret', 'CODEX_THREAD_ID': 'parent-thread',
                     'CODEX_HOME': '/fixture/existing/codex', 'HTTPS_PROXY': 'http://proxy.invalid'}
        r, out = self.run_review(env_extra=inherited)
        self.assert_success(r, out)
        data = json.loads(audit.read_text())
        self.assertTrue(data['token_present'])
        for key in ('OPENAI_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CODEX_THREAD_ID'):
            self.assertNotIn(key, data['env'])
        self.assertEqual(data['env']['CODEX_HOME'], inherited['CODEX_HOME'])
        self.assertEqual(data['env']['HOME'], self.env['HOME'])
        self.assertEqual(data['env']['HTTPS_PROXY'], inherited['HTTPS_PROXY'])
        self.assertIn('--no-daemon', data['args'])
        self.assertIn('--ignore-user-config', data['args'])
        self.assertIn('model_reasoning_effort="medium"', data['args'])
        self.assertNotIn('parent-secret', json.dumps(data))

    def test_protocol_mutations_and_boundary(self):
        sys.path.insert(0, str(qwen.thin.ENTRY.parent))
        from review_codex_protocol import validate
        from review_runner import RunError
        self.write('boundary.txt', ('中' * 511 + '\n') * 8 + '尾' * 5 + '\n')
        self.write('empty.txt', '')
        r, out = self.run_review(files=['boundary.txt', 'empty.txt'])
        self.assert_success(r, out)
        registry = self.manifest(out)['material_registries']['review']
        required = [x['path'] for x in registry['materials'].values()]
        events = [json.loads(x) for x in (out / 'logs/review.jsonl').read_text().splitlines()]
        tool_start = next(i for i, e in enumerate(events) if e['type'] == 'item.started')
        tool_end = tool_start + 1
        mutations = []
        def mutate(index, key, value):
            copied = json.loads(json.dumps(events)); copied[index]['item'][key] = value; mutations.append(copied)
        mutate(tool_start, 'server', 'external')
        mutate(tool_start, 'tool', 'other')
        mutate(tool_start, 'arguments', {'id': '001', 'path': '/outside'})
        mutate(tool_start, 'arguments', {'id': '999'})
        mutate(tool_end, 'id', 'wrong')
        mutate(tool_end, 'error', {'message': 'denied'})
        mutate(tool_end, 'status', 'failed')
        mutate(tool_end, 'result', {'content': [], 'structured_content': None})
        mutate(tool_start, 'type', 'file_change')
        mutate(tool_start, 'tool', 'read_mcp_resource')
        mutate(tool_start, 'tool', 'request_user_input')
        mutations += [[{'type': 'unknown'}] + events, events + [events[-1]], events[1:],
                      [events[0]] + events, events[:-1],
                      events[:1] + events[2:], events[:2] + [events[1]] + events[2:],
                      events[:1] + [{'type': 'item.completed', 'item': {
                          'id': 'config-error', 'type': 'error',
                          'message': 'Codex is ignoring 1 unrecognized configuration setting. Check for typos or deprecated settings.\n  session-flags: `user_instructions` is ignored.'}}] + events[1:]]
        log = self.base / 'mutated.jsonl'
        self.assertEqual(len(('中' * 511 + '\n').encode()) * 8 + len(('尾' * 5 + '\n').encode()), 12288)
        forged = json.loads(json.dumps(events))
        forged[-1]['actual_model'] = 'forged-provider-proof'
        log.write_text('\n'.join(json.dumps(x) for x in forged) + '\n')
        accepted = validate(log, 0, 'fixture.v1-max', registry['root'], required, registry)
        self.assertIsNone(accepted['model'])
        self.assertEqual(accepted['model_evidence'], 'requested_only')
        for stream in mutations:
            log.write_text('\n'.join(json.dumps(x) for x in stream) + '\n')
            with self.assertRaises(RunError):
                validate(log, 0, 'fixture.v1-max', registry['root'], required, registry)

    def test_explicit_resource_boundaries(self):
        for timeout in ('1800', '3600'):
            r, out = self.run_review(extra=['--prepare-only', '--idle-timeout', '900',
                                            '--timeout', timeout])
            self.assert_success(r, out, 'prepared')
            self.assertEqual(self.manifest(out)['limits']['idle_timeout'], 900)
            self.assertEqual(self.manifest(out)['limits']['timeout'], int(timeout))
        r, out = self.run_review(extra=['--prepare-only', '--idle-timeout', '45'])
        self.assert_success(r, out, 'prepared')
        self.assertEqual(self.manifest(out)['limits']['idle_timeout'], 45)
        for extra in (['--idle-timeout', '901'], ['--timeout', '3601']):
            r, _ = self.run_review(extra=['--prepare-only'] + extra)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('invalid bounded resource limits', r.stderr)
        r, out = self.run_review(extra=['--prepare-only'])
        self.assert_success(r, out, 'prepared')
        self.assertEqual(self.manifest(out)['limits']['idle_timeout'], 900)

for name in dir(qwen.QwenReviewTests):
    if name.startswith('test_') and name not in CodexQwenTests.__dict__:
        setattr(CodexQwenTests, name, None)
