"""Exercise the Claude-side entry with a real linked worktree and an offline CLI."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'adapters/claude-code/scripts/codex_implement.py'
sys.path.insert(0, str(ROOT / 'adapters/codex/skills/claude-review/scripts'))
from ai_anti_config import ConfigError, claude_implementation
import ai_anti_config

FAKE = """import json, os, pathlib, sys
assert sys.stdin.read() == ''
if sys.argv[1:] == ['--version']:
    with open(os.environ['FIXTURE_CODEX_CALLS'] + '.version', 'a') as calls:
        calls.write('version\\n')
    print(os.environ['FIXTURE_CODEX_VERSION'])
    sys.exit(0)
with open(os.environ['FIXTURE_CODEX_CALLS'], 'a') as calls:
    calls.write(json.dumps(sys.argv[1:]) + '\\n')
if 'FIXTURE_CODEX_RESUME_STDERR' in os.environ:
    sys.stderr.write(os.environ['FIXTURE_CODEX_RESUME_STDERR'])
    sys.stdout.write(os.environ.get('FIXTURE_CODEX_RESUME_JSONL', ''))
    sys.exit(int(os.environ.get('FIXTURE_CODEX_RESUME_EXIT', '1')))
print('Reading additional input from stdin...', file=sys.stderr)
thread_id = os.environ.get('FIXTURE_CODEX_THREAD_ID', 'synthetic')
if thread_id:
    print(json.dumps({'type': 'thread.started', 'thread_id': thread_id}))
print(json.dumps({'type': 'turn.started'}))
target = pathlib.Path('target.txt')
if 'resume' in sys.argv[1:]:
    with target.open('a') as handle:
        handle.write(' + rework')
else:
    target.write_text('fixture change')
if os.environ.get('FIXTURE_CODEX_FAIL'):
    print(json.dumps({'type': 'turn.failed', 'error': {'type': 'overloaded_error'}}))
    sys.exit(1)
print(json.dumps({'type': 'item.completed', 'item': {'id': 'final', 'type': 'agent_message', 'text': 'done'}}))
print(json.dumps({'type': 'turn.completed'}))
"""


class ClaudeCodexImplement(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.repo = self.base / 'main'
        self.repo.mkdir()
        self.worktree = self.base / 'linked'
        self.git('init', '-q')
        (self.repo / 'target.txt').write_text('original')
        self.git('add', 'target.txt')
        self.git('-c', 'user.email=fixture.invalid', '-c', 'user.name=Fixture',
                 'commit', '-qm', 'test: 建立合成测试基线')
        self.git('worktree', 'add', '--detach', str(self.worktree), 'HEAD')
        self.prompt = self.base / 'prompt.txt'
        self.prompt.write_text('Implement "\u793a\u4f8b"\n')
        self.config = self.base / 'config.json'
        self.config.write_text(json.dumps({'claude_relay': {
            'implementation': {'provider': 'codex', 'model': 'gpt-6.2-sol'}}}))
        self.calls = self.base / 'calls.jsonl'
        self.version_calls = self.calls.with_suffix('.jsonl.version')
        self.out = self.base / 'out'
        cli = self.base / 'codex'
        cli.write_text('#!' + sys.executable + '\n' + FAKE)
        cli.chmod(0o700)
        self.env = dict(os.environ, PATH=str(self.base) + os.pathsep + os.environ['PATH'],
                        FIXTURE_CODEX_VERSION='codex-cli 0.169.1', FIXTURE_CODEX_CALLS=str(self.calls),
                        HOME=str(self.base))
        self.env.pop('FIXTURE_CODEX_FAIL', None)

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def invoke(self, workspace=None, out=None, relay=None, resume_from=None, **extra_env):
        args = [sys.executable, str(ENTRY), '--workspace', str(workspace or self.worktree),
                '--prompt', str(self.prompt), '--out', str(out or self.out),
                '--config', str(self.config), '--timeout', '10', '--idle-timeout', '3']
        if relay is not None:
            args.extend(('--relay', relay))
        if resume_from is not None:
            args.extend(('--resume-from', str(resume_from)))
        return subprocess.run(args, cwd=self.base, stdin=subprocess.DEVNULL,
                              env=dict(self.env, **extra_env), capture_output=True, text=True, timeout=15)

    def manifest(self, out=None):
        return json.loads(((out or self.out) / 'manifest.json').read_text())

    def git_dir(self):
        return Path(self.git('-C', str(self.worktree), 'rev-parse', '--path-format=absolute', '--git-dir'))

    def call_counts(self):
        return tuple(len(path.read_text().splitlines()) if path.exists() else 0
                     for path in (self.version_calls, self.calls))

    def change_evidence(self, evidence):
        for name in ('manifest.json', 'status.json'):
            (self.out / name).write_text(json.dumps(evidence))

    def first_run(self, **kwargs):
        result = self.invoke(**kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.manifest()

    def assert_preflight_failure(self, error, source=None, out=None, **kwargs):
        before = self.call_counts()
        out = out or self.base / 'out2'
        result = self.invoke(out=out, resume_from=source or self.out, **kwargs)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(error, result.stderr)
        self.assertFalse(out.exists())
        self.assertEqual(self.call_counts(), before)

    def test_single_success_with_hashed_evidence(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = self.manifest()
        self.assertEqual(manifest, json.loads((self.out / 'status.json').read_text()))
        self.assertEqual(manifest['provider'], 'codex')
        self.assertEqual(manifest['relay'], 'claude')
        self.assertEqual(manifest['requested_model'], 'gpt-6.2-sol')
        self.assertIsNone(manifest['requested_effort'])
        self.assertEqual(manifest['model_evidence'], 'requested_only')
        self.assertEqual(manifest['cli_version'], 'codex-cli 0.169.1')
        self.assertEqual(manifest['workspace'], str(self.worktree))
        self.assertEqual(manifest['thread_id'], 'synthetic')
        self.assertEqual(manifest['git_dir'], str(self.git_dir()))
        self.assertEqual(manifest['git_common_dir'], self.git('rev-parse', '--path-format=absolute', '--git-common-dir'))
        self.assertIsNone(manifest['resumed_from'])
        self.assertIsNone(manifest['requested_thread_id'])
        self.assertRegex(manifest['worktree_token'], r'^[0-9a-f]{32}$')
        state = json.loads((self.git_dir() / 'ai-anti-implementation.json').read_text())
        self.assertEqual(state, {'token': manifest['worktree_token'], 'last_out': str(self.out)})
        self.assertEqual(json.loads(result.stdout), {'status': 'completed', 'completed': True, 'accepted': False})
        self.assertEqual(manifest['head'], self.git('rev-parse', 'HEAD'))
        self.assertGreater(manifest['duration_seconds'], 0)
        self.assertEqual(manifest['exit_code'], 0)
        self.assertTrue(manifest['completed'])
        self.assertFalse(manifest['accepted'])
        self.assertEqual(manifest['result'], 'done')
        self.assertEqual(manifest['warnings'], [{'kind': 'stdin_notice', 'stream': 'stderr', 'count': 1}])
        self.assertEqual(manifest['prompt_sha256'], hashlib.sha256(self.prompt.read_bytes()).hexdigest())
        for name, field in [('implementation.jsonl', 'log_sha256'),
                            ('implementation.stderr.txt', 'stderr_sha256')]:
            self.assertEqual(manifest[field], hashlib.sha256((self.out / name).read_bytes()).hexdigest())
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(calls, [['exec', '--json', '--sandbox', 'workspace-write', '--model',
                                  'gpt-6.2-sol', '-C', str(self.worktree), '--', self.prompt.read_text()]])
        self.assertEqual((self.repo / 'target.txt').read_text(), 'original')
        self.assertEqual((self.worktree / 'target.txt').read_text(), 'fixture change')

    def configure_effort(self, effort):
        data = json.loads(self.config.read_text())
        data['claude_relay']['implementation']['effort'] = effort
        self.config.write_text(json.dumps(data))

    def test_configured_effort_sets_argv_and_evidence(self):
        self.configure_effort('high')
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        self.assertEqual(calls, [['exec', '--json', '--sandbox', 'workspace-write', '--model',
                                  'gpt-6.2-sol', '-c', 'model_reasoning_effort="high"',
                                  '-C', str(self.worktree), '--', self.prompt.read_text()]])
        self.assertEqual(self.manifest()['requested_effort'], 'high')

    def test_agy_implementation_uses_only_the_agy_route(self):
        self.config.write_text(json.dumps({
            'agy_relay': {'implementation': {'provider': 'codex', 'model': 'agy-fixture', 'effort': 'xhigh'}},
            'claude_relay': {'implementation': {'provider': 'claude', 'model': 'ignored'}},
        }))
        result = self.invoke(relay='agy')
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = self.manifest()
        self.assertEqual(manifest, json.loads((self.out / 'status.json').read_text()))
        self.assertEqual((manifest['relay'], manifest['requested_model'], manifest['requested_effort']),
                         ('agy', 'agy-fixture', 'xhigh'))
        args = json.loads(self.calls.read_text())
        self.assertEqual(args[args.index('--model') + 1], 'agy-fixture')
        self.assertIn('model_reasoning_effort="xhigh"', args)
        self.assertEqual((self.repo / 'target.txt').read_text(), 'original')

    def test_agy_missing_implementation_stops_before_codex_starts(self):
        result = self.invoke(relay='agy')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('agy_relay.implementation must explicitly select codex', result.stderr)
        self.assertFalse(self.out.exists())
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.version_calls.exists())

    def test_agy_official_implementation_rejected_by_native_entry(self):
        self.config.write_text(json.dumps({'agy_relay': {'implementation': {
            'provider': 'official_api', 'service': 'token_plan', 'model': 'qwen4-max',
        }}}))
        result = self.invoke(relay='agy')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('implementation.py --relay agy', result.stderr)
        self.assertFalse(self.out.exists())
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.version_calls.exists())

    def test_invalid_effort_stops_before_codex_starts(self):
        self.configure_effort('XHIGH')
        result = self.invoke()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('claude_relay.implementation.effort', result.stderr)
        self.assertFalse(self.calls.with_suffix('.jsonl.version').exists())
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.out.exists())

    def test_rejects_before_exec(self):
        nongit = self.base / 'nongit'
        nongit.mkdir()
        for workspace, out, error in [(nongit, self.out, 'linked Git worktree'),
                                      (self.repo, self.out, 'outside the main worktree'),
                                      (self.worktree, self.worktree / 'out', 'outside the implementation workspace'),
                                      (self.worktree, self.repo, 'already exists')]:
            with self.subTest(error=error):
                result = self.invoke(workspace, out)
                self.assertEqual(result.returncode, 2)
                self.assertIn(error, result.stderr)
                self.assertFalse(self.calls.exists())
        override = (self.worktree / '.git').read_text().split(': ', 1)[1].strip()
        result = self.invoke(self.repo, GIT_DIR=override, GIT_WORK_TREE=str(self.repo))
        self.assertEqual(result.returncode, 2)
        self.assertIn('outside the main worktree', result.stderr)
        self.assertFalse(self.calls.exists())
        self.config.write_text('{"claude_relay":{"implementation":{"provider":"other","model":"example"}}}')
        result = self.invoke()
        self.assertEqual(result.returncode, 2)
        self.assertIn('provider codex', result.stderr)
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.out.exists())

    def test_rejects_version_before_exec(self):
        result = self.invoke(FIXTURE_CODEX_VERSION='codex-cli 0.163.0')
        self.assertEqual(result.returncode, 1)
        self.assertIn('verified only', self.manifest()['error'])
        self.assertEqual(self.manifest()['cli_version'], 'codex-cli 0.163.0')
        self.assertFalse(self.calls.exists())

    def test_failed_turn_retains_logs_and_partial_write_without_retry(self):
        result = self.invoke(FIXTURE_CODEX_FAIL='1')
        self.assertEqual(result.returncode, 1)
        manifest = self.manifest()
        self.assertFalse(manifest['completed'])
        self.assertEqual(manifest['exit_code'], 1)
        self.assertIn('turn.failed', (self.out / 'implementation.jsonl').read_text())
        self.assertIn('Reading additional input', (self.out / 'implementation.stderr.txt').read_text())
        self.assertEqual((self.worktree / 'target.txt').read_text(), 'fixture change')
        self.assertEqual(len(self.calls.read_text().splitlines()), 1)

    def test_resume_argv_and_chain_accumulate_changes_for_both_relays(self):
        self.config.write_text(json.dumps({
            relay + '_relay': {'implementation': {'provider': 'codex', 'model': relay + '-fixture'}}
            for relay in ('claude', 'agy')
        }))
        for relay in ('claude', 'agy'):
            with self.subTest(relay=relay):
                first = self.base / (relay + '-out1')
                result = self.invoke(out=first, relay=relay)
                self.assertEqual(result.returncode, 0, result.stderr)
                previous = first
                for number, options in ((2, []), (3, ['-c', 'model_reasoning_effort="low"'])):
                    if options:
                        config = json.loads(self.config.read_text())
                        config[relay + '_relay']['implementation']['effort'] = 'low'
                        self.config.write_text(json.dumps(config))
                    self.prompt.write_text('-rework this\n')
                    out = self.base / f'{relay}-out{number}'
                    result = self.invoke(out=out, relay=relay, resume_from=previous)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(self.calls.read_text().splitlines()[-1]),
                                     ['exec', '--json', '--sandbox', 'workspace-write', '-C',
                                      str(self.worktree), 'resume', '--model', relay + '-fixture',
                                      *options, '--', 'synthetic', '-rework this\n'])
                    manifest = self.manifest(out)
                    self.assertEqual(manifest, json.loads((out / 'status.json').read_text()))
                    self.assertEqual(manifest['resumed_from'], str(previous))
                    self.assertEqual(manifest['requested_thread_id'], 'synthetic')
                    self.assertEqual(manifest['thread_id'], 'synthetic')
                    self.assertEqual(manifest['worktree_token'], self.manifest(first)['worktree_token'])
                    self.assertEqual(manifest['requested_effort'], 'low' if options else None)
                    self.assertEqual((self.worktree / 'target.txt').read_text(),
                                     'fixture change' + ' + rework' * (number - 1))
                    previous = out
                self.assertEqual((self.repo / 'target.txt').read_text(), 'original')
        self.assertEqual(self.call_counts(), (6, 6))

    def test_resume_rejects_mismatched_or_missing_thread_once(self):
        for thread_id in ('unexpected', ''):
            with self.subTest(thread_id=thread_id):
                first = self.base / ('first-' + thread_id)
                out = self.base / ('resume-' + thread_id)
                self.assertEqual(self.invoke(out=first).returncode, 0)
                version_count, exec_count = self.call_counts()
                result = self.invoke(out=out, resume_from=first, FIXTURE_CODEX_THREAD_ID=thread_id)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(self.manifest(out)['error_kind'], 'session_mismatch')
                self.assertEqual(self.call_counts(), (version_count + 1, exec_count + 1))

    def test_resume_requires_previous_completed_attempt(self):
        self.assertEqual(self.invoke(FIXTURE_CODEX_FAIL='1').returncode, 1)
        # Isolate the completion gate from the missing ID in failed-turn evidence.
        failed = dict(self.manifest(), thread_id='synthetic')
        self.change_evidence(failed)
        self.assert_preflight_failure('completed')
        for changes in ({'status': 'completed'}, {'completed': True}, {'status': 'completed', 'completed': 1}):
            with self.subTest(changes=changes):
                self.change_evidence(dict(failed, **changes))
                self.assert_preflight_failure('completed')

    def test_resume_rejects_inconsistent_or_nonregular_evidence(self):
        manifest = self.first_run()
        status = self.out / 'status.json'
        status.write_text(json.dumps(dict(manifest, accepted=True)))
        self.assert_preflight_failure('manifest.json and status.json')
        self.change_evidence([])
        self.assert_preflight_failure('JSON object')
        self.change_evidence(manifest)
        for name in ('manifest.json', 'status.json'):
            with self.subTest(name=name):
                path = self.out / name
                path.unlink()
                os.mkfifo(path)
                self.assert_preflight_failure('regular file')
                path.unlink()
                self.change_evidence(manifest)

    def test_resume_rejects_missing_or_invalid_thread(self):
        manifest = self.first_run()
        del manifest['thread_id']
        self.change_evidence(manifest)
        self.assert_preflight_failure('cold start')
        for value in (None, '', '-option', 'two words', 'bad\nline', 'bad\x00id', 'bad\x7fid', 1):
            with self.subTest(value=value):
                self.change_evidence(dict(manifest, thread_id=value))
                self.assert_preflight_failure('thread_id')

    def test_resume_rejects_other_worktree_and_changed_head(self):
        self.first_run()
        other = self.base / 'other-linked'
        self.git('worktree', 'add', '--detach', str(other), 'HEAD')
        self.assert_preflight_failure('workspace', workspace=other)
        self.git('-c', 'user.email=fixture.invalid', '-c', 'user.name=Fixture',
                 'commit', '--allow-empty', '-qm', 'test: 更新合成测试基线')
        self.git('-C', str(self.worktree), 'checkout', '--detach', self.git('rev-parse', 'HEAD'))
        self.assert_preflight_failure('HEAD')

    def test_resume_rejects_changed_route_and_unsupported_previous_version(self):
        manifest = self.first_run()
        config = json.loads(self.config.read_text())
        config['agy_relay'] = config['claude_relay']
        self.config.write_text(json.dumps(config))
        self.assert_preflight_failure('relay', relay='agy')
        config['claude_relay']['implementation']['model'] = 'other-model'
        self.config.write_text(json.dumps(config))
        self.assert_preflight_failure('requested_model')
        config['claude_relay']['implementation']['model'] = manifest['requested_model']
        self.config.write_text(json.dumps(config))
        self.change_evidence(dict(manifest, provider='other'))
        self.assert_preflight_failure('provider')
        self.change_evidence(dict(manifest, cli_version='codex-cli 0.163.0'))
        self.assert_preflight_failure('cli_version')

    def test_resume_rejects_bad_source_paths_and_overlapping_output(self):
        self.first_run()
        alias = self.base / 'alias'
        alias.symlink_to(self.out, target_is_directory=True)
        for source in (alias, self.base / 'absent', self.prompt):
            with self.subTest(source=source):
                self.assert_preflight_failure('resume-from', source=source)
        self.assert_preflight_failure('overlap', out=self.out / 'child')
        before = self.call_counts()
        result = self.invoke(out=self.out, resume_from=self.out)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.call_counts(), before)

    def test_resume_rejects_worktree_recreated_at_same_path_and_head(self):
        first = self.first_run()
        self.git('worktree', 'remove', '--force', str(self.worktree))
        self.git('worktree', 'add', '--detach', str(self.worktree), 'HEAD')
        out = self.base / 'new-first'
        self.assertEqual(self.invoke(out=out).returncode, 0)
        second = self.manifest(out)
        for key in ('workspace', 'git_dir', 'git_common_dir', 'head'):
            self.assertEqual(first[key], second[key])
        self.assertNotEqual(first['worktree_token'], second['worktree_token'])
        # Give the test state the old last_out so this rejection depends on the token.
        state = self.git_dir() / 'ai-anti-implementation.json'
        state.write_text(json.dumps({'token': second['worktree_token'], 'last_out': str(self.out)}))
        self.assert_preflight_failure('worktree_token')

    def test_resume_cannot_skip_more_recent_failed_attempt(self):
        self.first_run()
        out = self.base / 'out2'
        result = self.invoke(out=out, resume_from=self.out, FIXTURE_CODEX_FAIL='1')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(self.manifest(out)['completed'])
        self.assertEqual((self.worktree / 'target.txt').read_text(), 'fixture change + rework')
        self.assert_preflight_failure('last_out', out=self.base / 'out3')

    def test_resume_probes_changed_binary_version_once_without_exec(self):
        self.first_run()
        out = self.base / 'out2'
        result = self.invoke(out=out, resume_from=self.out, FIXTURE_CODEX_VERSION='codex-cli 0.163.0')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.call_counts(), (2, 1))
        self.assertEqual(self.manifest(out)['cli_version'], 'codex-cli 0.163.0')
        self.assertIn('verified only', self.manifest(out)['error'])

    @unittest.skipIf(os.geteuid() == 0, 'read-only directory permissions require an unprivileged user')
    def test_readonly_git_dir_cold_start_without_state_cannot_resume(self):
        git_dir = self.git_dir()
        git_dir.chmod(0o500)
        self.addCleanup(git_dir.chmod, 0o700)
        manifest = self.first_run()
        self.assertIsNone(manifest['worktree_token'])
        self.assertFalse((git_dir / 'ai-anti-implementation.json').exists())
        self.assert_preflight_failure('worktree_token')

    @unittest.skipIf(os.geteuid() == 0, 'read-only file permissions require an unprivileged user')
    def test_readonly_existing_state_stops_cold_start(self):
        self.first_run()
        state = self.git_dir() / 'ai-anti-implementation.json'
        state.chmod(0o400)
        out = self.base / 'out2'
        result = self.invoke(out=out)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('state', result.stderr)
        self.assertFalse(out.exists())
        self.assertEqual(self.call_counts(), (1, 1))
        self.assertEqual(json.loads(state.read_text())['last_out'], str(self.out))

    def test_nonregular_state_stops_cold_start_without_blocking(self):
        os.mkfifo(self.git_dir() / 'ai-anti-implementation.json')
        result = self.invoke()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('regular file', result.stderr)
        self.assertEqual(self.call_counts(), (0, 0))
        self.assertFalse(self.out.exists())

    def test_concurrent_resume_allows_one_attempt_from_the_same_source(self):
        self.first_run()
        outputs = (self.base / 'out2', self.base / 'out3')
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.invoke, out=out, resume_from=self.out) for out in outputs]
            results = [future.result() for future in futures]
        self.assertEqual(sorted(result.returncode for result in results), [0, 2])
        rejected = next(out for out, result in zip(outputs, results) if result.returncode == 2)
        self.assertFalse(rejected.exists())
        self.assertEqual(self.call_counts(), (2, 2))

    def test_session_not_found_requires_exact_stderr_empty_jsonl_and_nonzero_exit(self):
        message = ('Error: thread/resume: thread/resume failed: '
                   'no rollout found for thread id synthetic (code -32600)\n')
        cases = (
            ('exact', message, '', '1', 'session_not_found'),
            ('jsonl', message, '{"type":"thread.started","thread_id":"synthetic"}\n', '1', 'execution_failed'),
            ('wrong_id', message.replace('synthetic', 'other-id'), '', '1', 'execution_failed'),
            ('extra_line', message + 'other error\n', '', '1', 'execution_failed'),
            ('extra_space', ' ' + message, '', '1', 'execution_failed'),
            ('exit_zero', message, '', '0', 'execution_failed'),
        )
        for label, stderr, jsonl, code, kind in cases:
            with self.subTest(label=label):
                first, out = self.base / (label + '-first'), self.base / (label + '-resume')
                self.assertEqual(self.invoke(out=first).returncode, 0)
                version_count, exec_count = self.call_counts()
                result = self.invoke(out=out, resume_from=first, FIXTURE_CODEX_RESUME_STDERR=stderr,
                                     FIXTURE_CODEX_RESUME_JSONL=jsonl, FIXTURE_CODEX_RESUME_EXIT=code)
                self.assertEqual(result.returncode, 1, result.stderr)
                manifest = self.manifest(out)
                self.assertEqual((manifest['status'], manifest['error_kind'], manifest['exit_code']),
                                 ('failed', kind, int(code)))
                self.assertEqual(manifest, json.loads((out / 'status.json').read_text()))
                self.assertEqual((out / 'implementation.stderr.txt').read_text(), stderr)
                self.assertEqual((out / 'implementation.jsonl').read_text(), jsonl)
                self.assertEqual(self.call_counts(), (version_count + 1, exec_count + 1))


class ClaudeImplementationConfig(unittest.TestCase):
    def test_implementation_effort_optional_and_strict(self):
        config = {'provider': 'codex', 'model': 'gpt-6.2-sol'}
        for fields, expected in [({}, None), *[({'effort': value}, value) for value in
                (None, 'low', 'medium', 'high', 'xhigh', 'max', 'ultra')]]:
            with self.subTest(fields=fields):
                actual = claude_implementation({'claude_relay': {'implementation': dict(config, **fields)}})
                self.assertEqual(actual, dict(config, effort=expected))
        for value in ('XHIGH', 'x high', 1, 'minimal', 'high ', '', [], {}, True):
            with self.subTest(value=value), self.assertRaisesRegex(ConfigError, r'claude_relay\.implementation\.effort'):
                claude_implementation({'claude_relay': {'implementation': dict(config, effort=value)}})

    def test_review_effort_optional_and_strict(self):
        for config in ({}, {'review': {}}, {'review': {'codex_effort': None}}):
            with self.subTest(config=config):
                self.assertIsNone(ai_anti_config.review_codex_effort(config))
        for value in ('low', 'medium', 'high', 'xhigh', 'max', 'ultra'):
            with self.subTest(value=value):
                self.assertEqual(ai_anti_config.review_codex_effort({'review': {'codex_effort': value}}), value)
        for key in ('codex_effort', 'codex_high_risk_effort'):
            for value in ('XHIGH', 'x high', 1, 'minimal', 'high ', '', [], {}, True):
                for tier in (None, 'standard', 'high_risk'):
                    with self.subTest(key=key, value=value, tier=tier):
                        with self.assertRaisesRegex(ConfigError, rf'review\.{key} must be one of '):
                            ai_anti_config.review_codex_effort({'review': {key: value}}, risk_tier=tier)

    def test_review_effort_selects_tier_and_handles_null(self):
        for base, elevated, standard, high_risk in (
            ('high', 'xhigh', 'high', 'xhigh'),
            ('high', None, 'high', 'high'),
            (None, 'low', None, 'low'),
            ('high', 'high', 'high', 'high'),
            (None, None, None, None),
        ):
            config = {'review': {'codex_effort': base, 'codex_high_risk_effort': elevated}}
            for tier, expected in (('standard', standard), ('high_risk', high_risk), (None, high_risk)):
                with self.subTest(base=base, elevated=elevated, tier=tier):
                    self.assertEqual(ai_anti_config.review_codex_effort(config, risk_tier=tier), expected)

    def test_review_effort_rejects_inverted_or_invalid_tier(self):
        config = {'review': {'codex_effort': 'xhigh', 'codex_high_risk_effort': 'high'}}
        for tier in (None, 'standard', 'high_risk'):
            with self.subTest(tier=tier):
                with self.assertRaises(ConfigError) as error:
                    ai_anti_config.review_codex_effort(config, risk_tier=tier)
                self.assertEqual(str(error.exception), 'ai-anti config: review.codex_high_risk_effort '
                                 'must not be lower than review.codex_effort')
        for tier in ('bogus', '', [], 1):
            with self.subTest(tier=tier), self.assertRaisesRegex(ConfigError, '^review risk tier must be one of '):
                ai_anti_config.review_codex_effort({}, risk_tier=tier)

    def test_missing_null_and_invalid_implementation(self):
        for config in ({}, {'claude_relay': None}, {'claude_relay': {'implementation': None}}):
            self.assertIsNone(claude_implementation(config))
        for value in ('codex', {'provider': 'other', 'model': 'ok'}, {'provider': 'codex'},
                      *({'provider': 'codex', 'model': model} for model in ('', 'a b', 'a\nb', 4))):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                claude_implementation({'claude_relay': {'implementation': value}})


if __name__ == '__main__':
    unittest.main()
