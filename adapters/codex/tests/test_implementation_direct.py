"""Offline production-entry coverage, substituting only the vendor connection."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/codex-relay/scripts'
sys.path.insert(0, str(SCRIPTS))
import implementation
from implementation_config import ConfigError
from implementation_direct_files import workspace_lock

ROUTES = {
    'qwen4-max': ('qwen', 'maas.qianwenaiapi.com', '/compatible-mode/v1/chat/completions'),
    'qwen3.8-max': ('qwen', 'maas.qianwenaiapi.com', '/compatible-mode/v1/chat/completions'),
    'kimi-k3.1-pro': ('kimi', 'api.moonshot.cn', '/v1/chat/completions'),
    'kimi-k3': ('kimi', 'api.moonshot.cn', '/v1/chat/completions'),
    'deepseek-v4-pro': ('deepseek', 'api.deepseek.com', '/chat/completions'),
    'deepseek-flash': ('deepseek', 'api.deepseek.com', '/chat/completions'),
}
KEYS = {service: f'unique-fixture-{service}-value' for service, _, _ in ROUTES.values()}
REASONING = 'fixture-private-reasoning'


def event(model, delta=None, finish=None):
    body = dict(model=model, choices=[dict(index=0, delta=delta or {}, finish_reason=finish)])
    return b'data: ' + json.dumps(body).encode() + b'\r\n\r\n'


class Response(io.BytesIO):
    def __init__(self, data, status=200):
        super().__init__(data)
        self.status = status

    def getheader(self, name):
        return 'text/event-stream; charset=utf-8'


class DirectImplementation(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name).resolve()
        self.home = root / 'home'
        local = self.home / '.config/ai-anti'
        local.mkdir(parents=True)
        self.secrets = local / 'secrets.json'
        self.secrets.write_text(json.dumps({s: {'value': k} for s, k in KEYS.items()}))
        fake_home = patch.dict('os.environ', {'HOME': str(self.home)})
        fake_home.start()
        self.addCleanup(fake_home.stop)
        repo = root / 'repo'
        repo.mkdir()
        self.git(repo, 'init', '-q')
        (repo / 'src').mkdir()
        (repo / 'src/a.txt').write_bytes(b'before\r\nlast')
        (repo / 'unrelated.txt').write_text('fixture-no-disclosure')
        self.git(repo, 'add', '.')
        self.git(repo, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture')
        self.workspace = root / 'work'
        self.git(repo, 'worktree', 'add', '-q', '--detach', str(self.workspace))
        self.first = self.workspace / 'src/a.txt'
        self.second = self.workspace / 'src/b Café.txt'
        self.first.chmod(0o640)
        self.prompt = root / 'prompt'
        self.prompt.write_text('Replace authorized text')
        self.config = root / 'config.json'
        self.config.write_text('{"codex_relay":{"implementation_provider":"invalid"}}')
        self.root = root
        self.requests = []

    def git(self, workspace, *args):
        subprocess.run(['/usr/bin/git', '-C', str(workspace), *args], check=True, capture_output=True)

    def run_entry(self, chosen='qwen4-max', mode=None, **overrides):
        out = self.root / ('out-' + str(len(list(self.root.glob('out-*')))))
        defaults = dict(workspace=self.workspace, prompt=self.prompt, config=self.config, out=out,
                        provider='custom_api', model=chosen, file=['src/a.txt', 'src/b Café.txt'],
                        prepare_only=False, cli=None, timeout=20, idle_timeout=3)
        defaults.update(overrides)
        self.requests.clear()
        self.mode = mode
        with patch('http.client.HTTPSConnection', side_effect=self.connect):
            result = implementation.run(argparse.Namespace(**defaults))
        self.assertEqual(result, json.loads((out / 'manifest.json').read_text()))
        self.assertEqual(result, json.loads((out / 'status.json').read_text()))
        self.assertFalse(result['accepted'])
        for artifact in out.iterdir():
            data = artifact.read_bytes()
            for key in [*KEYS.values(), REASONING, 'fixture-no-disclosure']:
                self.assertNotIn(key.encode(), data)
        if 'log' in result:
            self.assertEqual(result['log_sha256'], hashlib.sha256((out / result['log']).read_bytes()).hexdigest())
        return result

    def connect(self, host, timeout):
        test = self

        class Connection:
            sock = None

            def connect(self):
                pass

            def request(self, method, path, body, headers):
                self.request_data = json.loads(body)
                test.requests.append((host, timeout, method, path, headers, self.request_data))

            def getresponse(self):
                request = self.request_data
                model = request['model']
                if test.mode == 'http':
                    return Response(KEYS['qwen'].encode(), status=302)
                if test.mode == 'network':
                    raise OSError(KEYS['qwen'])
                if test.mode == 'timeout':
                    raise TimeoutError
                if test.mode == 'model':
                    model = 'unexpected-' + KEYS['qwen']
                done = b'data: [DONE]\r\n\r\n' if test.mode != 'truncated' else b''
                if test.mode == 'malformed':
                    return Response(b'data: {\n\n' + done)
                if test.mode == 'output':
                    return Response(b'data: ' + b'x' * 300 + b'\n' + done)
                if 'tool_choice' in request:
                    receipt = request['messages'][-1]['content']
                    if test.mode == 'concurrent':
                        test.first.write_bytes(b'external edit')
                    if test.mode == 'receipt':
                        receipt += ' wrong'
                    if test.mode == 'confirm_model':
                        model = 'wrong-confirmation-model'
                    if test.mode == 'confirm_tool':
                        return Response(event(model, {'tool_calls': [{'index': 0, 'id': 'other'}]},
                                              'tool_calls') + done)
                    test.assertEqual(request['messages'][-2]['reasoning_content'], REASONING)
                    return Response(event(model, {'content': receipt}, 'stop') + done)
                changes = [dict(path='src/a.txt', content='next\nlast'),
                           dict(path='src/b Café.txt', content='new\r\n')]
                if test.mode == 'scope':
                    changes[1]['path'] = 'unrelated.txt'
                if test.mode == 'duplicate_path':
                    changes[1]['path'] = changes[0]['path']
                args = json.dumps(dict(changes=changes))
                first = dict(index=0, id='fixture-tool', type='function',
                             function=dict(name='propose_file_changes', arguments=args[:12]))
                second = dict(index=1 if test.mode == 'duplicate_call' else 0,
                              function=dict(arguments=args[12:]))
                return Response(event(model, {'reasoning_content': REASONING, 'tool_calls': [first]})
                                + event(model, {'tool_calls': [second]}, 'tool_calls') + done)

            def close(self):
                pass

        return Connection()

    def test_saved_route_still_requires_each_file_for_run_and_prepare(self):
        self.config.write_text(json.dumps({'codex_relay': {
            'implementation_provider': 'custom_api',
            'direct_implementation': {'service': 'kimi', 'model': 'kimi-k3'},
        }}))
        with patch.object(implementation.implementation_direct, 'credential', side_effect=AssertionError('credential read')):
            for prepare_only in (False, True):
                with self.assertRaises(ConfigError):
                    self.run_entry(provider=None, model=None, file=None, prepare_only=prepare_only)
            prepared = self.run_entry(provider=None, model=None, prepare_only=True)
            self.assertEqual(prepared['status'], 'prepared')
            self.assertEqual(prepared['endpoint'], 'https://api.moonshot.cn/v1/chat/completions')
            self.assertEqual(self.requests, [])
        completed = self.run_entry(provider=None, model=None)
        self.assertEqual((completed['status'], completed['requested_model']), ('completed', 'kimi-k3'))
        self.assertEqual(self.requests[0][4]['Authorization'], 'Bearer ' + KEYS['kimi'])

    def test_exact_routes_credentials_and_files(self):
        for model, (service, host, path) in ROUTES.items():
            with self.subTest(model=model):
                result = self.run_entry(model)
                self.assertEqual(result['status'], 'completed', result)
                self.assertEqual((result['actual_model'], result['model_evidence'], result['backend_model']),
                                 (model, 'api_reported', 'unknown'))
                self.assertEqual(result['endpoint'], 'https://' + host + path)
                self.assertEqual(len(self.requests), 2)
                for used_host, timeout, method, used_path, headers, body in self.requests:
                    self.assertEqual((used_host, timeout, method, used_path), (host, 3, 'POST', path))
                    self.assertEqual(headers['Authorization'], 'Bearer ' + KEYS[service])
                    self.assertEqual(body['model'], model)
                    self.assertNotIn('fixture-no-disclosure', json.dumps(body))
                self.assertNotIn('tool_choice', self.requests[0][-1])
                self.assertEqual(self.requests[1][-1]['tool_choice'], 'none')
                self.assertEqual(self.first.read_bytes(), b'next\nlast')
                self.assertEqual(self.first.stat().st_mode & 0o777, 0o640)
                self.assertEqual(self.second.read_bytes(), b'new\r\n')
                self.assertEqual(result['files_after']['src/b Café.txt'], hashlib.sha256(b'new\r\n').hexdigest())
                self.first.write_bytes(b'before\r\nlast')
                self.second.unlink()

    def test_private_file_and_new_file_remain_owner_only(self):
        self.first.chmod(0o600)
        restore = implementation.implementation_direct.restore_modes

        def verify_before_restore(*args):
            self.assertEqual(self.first.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.second.stat().st_mode & 0o777, 0o600)
            return restore(*args)

        with patch('implementation_direct.restore_modes', side_effect=verify_before_restore) as observer:
            result = self.run_entry()
        self.assertEqual(result['status'], 'completed', result)
        observer.assert_called_once()
        self.assertEqual(self.first.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.second.stat().st_mode & 0o777, 0o600)

    def test_prepare_does_not_read_credentials_or_connect(self):
        with patch('implementation_direct.credential', side_effect=AssertionError):
            result = self.run_entry(prepare_only=True)
        self.assertEqual((result['status'], result['model_evidence'], result['actual_model']),
                         ('prepared', 'requested_only', None))
        self.assertEqual(result['files_before']['src/b Café.txt'], None)
        self.assertEqual(self.requests, [])
        self.assertFalse(self.second.exists())

    def test_refuses_invalid_route_flags_and_path_aliases(self):
        for overrides in (
            dict(model=None), dict(model='deepseek-v4-flash'), dict(cli='other'), dict(file=[]),
            dict(file=['src/a.txt', 'SRC/A.txt']), dict(file=['./src/a.txt']), dict(file=['../outside']),
            dict(file=['.GIT/config']), dict(file=['src/.ENV']), dict(file=['/absolute']),
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ConfigError):
                self.run_entry(**overrides)

    def test_missing_key_symlinks_and_workspace_lock_fail_early(self):
        self.secrets.unlink()
        result = self.run_entry()
        self.assertEqual(result.get('failure_stage'), 'credential')
        self.assertEqual(self.requests, [])
        self.second.symlink_to(self.prompt)
        self.assertEqual(self.run_entry().get('failure_stage'), 'preflight')
        self.second.unlink()
        with workspace_lock(self.workspace, exclusive=True):
            self.assertEqual(self.run_entry().get('failure_stage'), 'preflight')

    def test_rejects_failed_network_or_protocol_without_applying(self):
        failures = {'http': 'http_status', 'network': 'network_error', 'timeout': 'idle_timeout',
                    'model': 'model_mismatch', 'malformed': 'invalid_stream', 'truncated': 'incomplete_stream',
                    'duplicate_call': 'multiple_or_invalid_tool_calls', 'scope': 'invalid_proposal',
                    'duplicate_path': 'invalid_proposal', 'receipt': 'invalid_confirmation',
                    'confirm_model': 'model_mismatch', 'confirm_tool': 'invalid_confirmation',
                    'output': 'output_limit'}
        for mode, reason in failures.items():
            with self.subTest(mode=mode), patch('implementation.CAP', 250 if mode == 'output' else 8 * 1024 * 1024):
                result = self.run_entry(mode=mode)
                self.assertEqual((result['status'], result['error_kind']), ('failed', reason))
                self.assertEqual(result['actual_model'], None)
                self.assertLessEqual(len(self.requests), 2)
                self.assertEqual(self.first.read_bytes(), b'before\r\nlast')
                self.assertFalse(self.second.exists())

    def test_concurrent_modification_does_not_apply_any_changes(self):
        result = self.run_entry(mode='concurrent')
        self.assertEqual(result.get('failure_stage'), 'apply')
        self.assertEqual(self.first.read_bytes(), b'external edit')
        self.assertFalse(self.second.exists())


if __name__ == '__main__':
    unittest.main()
