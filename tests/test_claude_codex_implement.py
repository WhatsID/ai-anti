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
import ai_anti_config

FAKE = """import json, os, pathlib, sys
assert sys.stdin.read() == ''
if sys.argv[1:] == ['--version']:
    pathlib.Path(os.environ['FIXTURE_CODEX_CALLS'] + '.version').touch()
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

    def invoke(self, workspace=None, out=None, relay=None, **extra_env):
        args = [sys.executable, str(ENTRY), '--workspace', str(workspace or self.worktree),
                '--prompt', str(self.prompt), '--out', str(out or self.out),
                '--config', str(self.config), '--timeout', '10', '--idle-timeout', '3']
        if relay is not None:
            args.extend(('--relay', relay))
        return subprocess.run(args, env=dict(self.env, **extra_env), capture_output=True, text=True, timeout=15)

    def manifest(self):
        return json.loads((self.out / 'manifest.json').read_text())

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
        self.assertFalse(self.calls.with_suffix('.version').exists())

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
