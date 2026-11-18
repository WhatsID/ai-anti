"""Exercise the Claude-side entry with a real linked worktree and an offline CLI."""
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

FAKE = """import json, os, pathlib, sys
assert sys.stdin.read() == ''
if sys.argv[1:] == ['--version']:
    print(os.environ['FIXTURE_CODEX_VERSION'])
    sys.exit(0)
with open(os.environ['FIXTURE_CODEX_CALLS'], 'a') as calls:
    calls.write(json.dumps(sys.argv[1:]) + '\\n')
print('Reading additional input from stdin...', file=sys.stderr)
print(json.dumps({'type': 'thread.started', 'thread_id': 'synthetic'}))
print(json.dumps({'type': 'turn.started'}))
pathlib.Path('target.txt').write_text('fixture change')
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

    def invoke(self, workspace=None, out=None, **extra_env):
        args = [sys.executable, str(ENTRY), '--workspace', str(workspace or self.worktree),
                '--prompt', str(self.prompt), '--out', str(out or self.out),
                '--config', str(self.config), '--timeout', '10', '--idle-timeout', '3']
        return subprocess.run(args, env=dict(self.env, **extra_env), capture_output=True, text=True, timeout=15)

    def manifest(self):
        return json.loads((self.out / 'manifest.json').read_text())

    def test_single_success_with_hashed_evidence(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = self.manifest()
        self.assertEqual(manifest, json.loads((self.out / 'status.json').read_text()))
        self.assertEqual(manifest['provider'], 'codex')
        self.assertEqual(manifest['requested_model'], 'gpt-6.2-sol')
        self.assertEqual(manifest['model_evidence'], 'requested_only')
        self.assertEqual(manifest['cli_version'], 'codex-cli 0.169.1')
        self.assertEqual(manifest['workspace'], str(self.worktree))
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


class ClaudeImplementationConfig(unittest.TestCase):
    def test_missing_null_and_invalid_implementation(self):
        for config in ({}, {'claude_relay': None}, {'claude_relay': {'implementation': None}}):
            self.assertIsNone(claude_implementation(config))
        for value in ('codex', {'provider': 'other', 'model': 'ok'}, {'provider': 'codex'},
                      *({'provider': 'codex', 'model': model} for model in ('', 'a b', 'a\nb', 4))):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                claude_implementation({'claude_relay': {'implementation': value}})
