"""Offline AGY-led planning tests with an isolated repository and fake Claude CLI."""
import errno
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from test_claude_agy_plan import SNAPSHOT_CAPTURE, SnapshotOmissionsTests

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'adapters/agy/scripts/claude_plan.py'
FIXTURE = ROOT / 'adapters/codex/tests/fixtures/cli_20261118'
sys.path.insert(0, str(ROOT / 'adapters/codex/skills/claude-review/scripts'))
from ai_anti_config import ConfigError, agy_dual_plan, agy_dual_plan_defaults
from plan_staging import MAX_INPUT_READ_BYTES, read_regular_utf8
from review_runner import RunError

FLAGS = (
    '--safe-mode', '--restricted', '--tools', '--allowedTools', '--permission-mode',
    '--strict-mcp-config', '--mcp-config', '--disable-slash-commands',
    '--no-session-persistence', '--verbose', '--output-format', '--model', '--effort',
)
ACTUAL = 'claude-sonnet-4-6'
DEFAULTS = {'enabled': False, 'partner': 'claude', 'model': None, 'effort': None,
            'synthesizer': 'agy', 'on_failure': 'stop'}

FAKE_CLAUDE = r'''
import json, os, stat, sys, time
from pathlib import Path

assert sys.stdin.read() == ''
base = Path(os.environ['AGY_CLAUDE_FIXTURE'])
fixture = json.loads((base / 'scenario.json').read_text())
argv = sys.argv[1:]
cwd = Path.cwd()
assert cwd.name == '.staging'
with (base / 'calls.jsonl').open('a') as handle:
    print(json.dumps(argv), file=handle)
if argv == ['--version']:
    print(fixture.get('version', '2.1.326 (Claude Code)'))
    sys.exit(fixture.get('version_code', 0))
if argv == ['--help']:
    print(' '.join(fixture['help']))
    sys.exit(fixture.get('help_code', 0))

assert argv[0] == '-p'
assert not cwd.stat().st_mode & stat.S_IWUSR
assert not (cwd / 'tracked.txt').stat().st_mode & stat.S_IWUSR
assert not os.path.lexists(cwd / '.git')
''' + SNAPSHOT_CAPTURE + r'''
observed = {path.name: path.read_text() for path in cwd.glob('*.txt') if path.is_file()}
(base / 'snapshot.json').write_text(json.dumps(observed))
if fixture.get('mutation'):
    target = cwd / 'tracked.txt'
    target.chmod(0o600)
    target.write_text('mutated')
if fixture.get('chmod_only'):
    cwd.chmod(0o700)
if fixture.get('symlink'):
    cwd.chmod(0o700)
    (cwd / 'escape').symlink_to(base)
if fixture.get('stderr'):
    print(fixture['stderr'], file=sys.stderr)
if fixture.get('wait'):
    time.sleep(30)
for event in fixture['events']:
    line = json.dumps(event) if isinstance(event, dict) else event
    print(line.replace('@STAGING@', str(cwd)), flush=True)
sys.exit(fixture.get('code', 0))
'''


def init(model=ACTUAL, **overrides):
    return dict({'type': 'system', 'subtype': 'init', 'model': model, 'session_id': 'fixture',
                 'tools': ['Read', 'Glob', 'Grep'], 'mcp_servers': [], 'plugins': [],
                 'skills': [], 'slash_commands': [], 'permissionMode': 'dontAsk'}, **overrides)


def assistant(*content, **fields):
    return {'type': 'assistant', 'message': dict(role='assistant', content=list(content), **fields)}


def tool(name, inputs, uid='tool-1'):
    return {'type': 'tool_use', 'id': uid, 'name': name, 'input': inputs}


def tool_result(uid='tool-1', error=False, **fields):
    return dict({'type': 'user', 'message': {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': uid, 'is_error': error, 'content': 'fixture'},
    ]}}, **fields)


def terminal(text='# Fixture plan', **overrides):
    return dict({'type': 'result', 'subtype': 'success', 'is_error': False,
                 'permission_denials': [], 'result': text}, **overrides)


class ConfigTests(unittest.TestCase):
    def test_defaults_and_valid_values(self):
        self.assertEqual(agy_dual_plan_defaults(), DEFAULTS)
        for data in ({}, {'agy_relay': None}, {'agy_relay': {}}, {'agy_relay': {'dual_plan': None}}):
            self.assertIsNone(agy_dual_plan(data))
        for effort in ('low', 'medium', 'high', 'xhigh', 'max'):
            plan = dict(DEFAULTS, enabled=True, partner=None, model='haiku', effort=effort,
                        synthesizer='claude', on_failure='fallback')
            parsed = agy_dual_plan({'agy_relay': {'dual_plan': plan, 'review': False, 'implementation': []}})
            self.assertEqual(parsed, dict(plan, partner='claude'))
        self.assertEqual(agy_dual_plan({'agy_relay': {'dual_plan': dict.fromkeys(set(DEFAULTS) - {'enabled'})}}),
                         DEFAULTS)

    def test_invalid_values_fail_closed(self):
        for plan, field in (
            (False, 'must be an object'), ([], 'must be an object'), ({'enabeld': True}, 'has unknown keys'),
            ({'enabled': 1}, '.enabled'), ({'enabled': None}, '.enabled'), ({'enabled': 'true'}, '.enabled'),
            ({'partner': 'agy'}, '.partner'), ({'model': ''}, '.model'), ({'model': 1}, '.model'),
            ({'model': 'foo\u2003bar'}, '.model'), ({'effort': 'ultra'}, '.effort'),
            ({'synthesizer': 'codex'}, '.synthesizer'), ({'on_failure': 'retry'}, '.on_failure'),
        ):
            with self.subTest(plan=plan), self.assertRaisesRegex(ConfigError, r'agy_relay\.dual_plan.*' + field):
                agy_dual_plan({'agy_relay': {'dual_plan': plan}})
        for relay in ([], {'other': {}}):
            with self.assertRaisesRegex(ConfigError, 'agy_relay'):
                agy_dual_plan({'agy_relay': relay})

    def test_input_growing_after_stat_stays_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'material'
            path.write_bytes(b'a' * 8)
            read_sizes = []

            class GrowingFile(io.BytesIO):
                def read(self, size=-1):
                    read_sizes.append(size)
                    return super().read(size)
                def fileno(self):
                    return fd

            with path.open('rb') as real:
                fd = real.fileno()
                with patch('plan_staging.os.open', return_value=fd), \
                     patch('plan_staging.os.fdopen', return_value=GrowingFile(b'a' * 20)), \
                     self.assertRaisesRegex(ConfigError, 'input limit'):
                    read_regular_utf8(path, 'material', max_bytes=8)
            self.assertEqual(read_sizes, [9])


class ClaudePlanTests(SnapshotOmissionsTests, unittest.TestCase):
    snapshot_tracked = 'tracked.txt'
    snapshot_head_paths = ('safe-link', 'tracked.txt')

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='agy-claude-plan-test-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.repo = self.base / 'repo with spaces'
        self.repo.mkdir()
        self.home = self.base / 'home'
        self.home.mkdir()
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        cli = self.bin / 'claude'
        cli.write_text('#!' + sys.executable + '\n' + FAKE_CLAUDE)
        cli.chmod(0o700)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        HOME=str(self.home), AGY_CLAUDE_FIXTURE=str(self.base))
        for key in ('GIT_DIR', 'GIT_COMMON_DIR', 'GIT_WORK_TREE'):
            self.env.pop(key, None)
        self.git('init', '-q')
        (self.repo / 'tracked.txt').write_text('committed\n')
        (self.repo / 'safe-link').symlink_to('tracked.txt')
        self.git('add', '.')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture.invalid', 'commit', '-qm', 'fixture')
        (self.repo / 'tracked.txt').write_text('working tree\n')
        (self.repo / 'approved.txt').write_text('approved\n')
        (self.repo / 'unapproved.txt').write_text('unapproved\n')
        self.request = self.base / 'request.md'
        self.request.write_text(f'用户需求 {self.repo}/tracked.txt\n\n## 附录：非需求\nprivate appendix')
        self.config = self.base / 'config.json'
        self.configure()
        self.calls = self.base / 'calls.jsonl'
        self.out = self.base / 'out'
        self.scenario()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], env=self.env, stdin=subprocess.DEVNULL,
                              capture_output=True, check=True, timeout=8).stdout

    def configure(self, **plan):
        self.config.write_text(json.dumps({'agy_relay': {'dual_plan': dict({'enabled': True}, **plan)}}))

    def scenario(self, events=None, **fields):
        (self.base / 'scenario.json').write_text(json.dumps(
            dict({'events': [init(), terminal()] if events is None else events, 'help': FLAGS}, **fields)))

    def command(self, *, stage='draft', trigger='config', extra=()):
        return [sys.executable, str(ENTRY), '--repo', str(self.repo), '--out', str(self.out),
                '--request', str(self.request), '--config', str(self.config), '--stage', stage,
                '--trigger', trigger, *extra]

    def invoke(self, expected_code, **args):
        result = subprocess.run(self.command(**args), env=self.env, stdin=subprocess.DEVNULL, text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, expected_code, (result.stdout, result.stderr))
        summary = json.loads(result.stdout)
        self.assertEqual(summary['exit_code'], expected_code)
        if (self.out / 'manifest.json').is_file():
            manifest = self.assert_evidence(expected_code)
            for key, value in summary.items():
                self.assertEqual(manifest.get(key), value, key)
            return manifest
        return summary

    def assert_evidence(self, code):
        status = json.loads((self.out / 'status.json').read_text())
        self.assertEqual(status, json.loads((self.out / 'manifest.json').read_text()))
        self.assertEqual(status['exit_code'], code)
        self.assertEqual(status['completed'], code == 0)
        self.assertEqual(status['fallback_allowed'], code == 1)
        self.assertEqual(status['integrity_ok'], code != 3)
        self.assertEqual((self.out / 'plan.md').exists(), code == 0)
        self.assertFalse((self.out / '.staging').exists())
        return status

    def call_args(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def run_snapshot(self, code, args=()):
        capture = self.base / 'snapshot-tree.json'
        capture.unlink(missing_ok=True)
        with patch.dict(self.env, FIXTURE_SNAPSHOT_CAPTURE=str(capture)):
            evidence = self.invoke(code, extra=args)
        if code:
            self.assertFalse(evidence['fallback_allowed'])
            return evidence, None
        self.assertEqual(self.call_args()[-1][1], (self.out / 'prompt.txt').read_text())
        return evidence, json.loads(capture.read_text())

    def test_draft_restricts_cli_and_snapshots_approved_materials(self):
        before = self.git('status', '--porcelain=v1', '-z')
        evidence = self.invoke(0, extra=('--extra-file', './approved.txt', '--extra-file', 'approved.txt'))
        self.assertEqual(self.git('status', '--porcelain=v1', '-z'), before)
        self.assertEqual(evidence['actual_model'], ACTUAL)
        self.assertEqual(evidence['model_evidence'], 'actual')
        self.assertEqual(evidence['conversation_id'], 'fixture')
        self.assertEqual(evidence['cli_version'], '2.1.326')
        self.assertEqual(evidence['warnings'][0]['kind'], 'repo_path_redacted')
        self.assertEqual(evidence['plan_sha256'], hashlib.sha256(b'# Fixture plan\n').hexdigest())
        self.assertEqual(json.loads((self.base / 'snapshot.json').read_text()),
                         {'tracked.txt': 'working tree\n', 'approved.txt': 'approved\n'})
        version, help_call, argv = self.call_args()
        self.assertEqual((version, help_call), (['--version'], ['--help']))
        self.assertEqual(argv[0], '-p')
        self.assertEqual(argv[1], (self.out / 'prompt.txt').read_text())
        self.assertIn('<REPO_ROOT>/tracked.txt', argv[1])
        self.assertNotIn(str(self.repo), argv[1])
        self.assertNotIn('private appendix', argv[1])
        self.assertNotIn('BEGIN_AGY_DRAFT', argv[1])
        self.assertEqual(argv[2:], [
            '--safe-mode', '--restricted', '--tools', 'Read,Glob,Grep', '--allowedTools', 'Read,Glob,Grep',
            '--permission-mode', 'dontAsk', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
            '--disable-slash-commands', '--no-session-persistence', '--verbose', '--output-format', 'stream-json',
        ])

    def test_synthesis_requires_both_drafts_and_claude_trigger(self):
        claude = self.base / 'draft-claude.md'
        agy = self.base / 'draft-agy.md'
        claude.write_text('Claude draft\n')
        agy.write_text(f'AGY draft {self.repo}\n')
        inputs = ('--claude-draft', str(claude), '--agy-draft', str(agy), '--extra-file', 'approved.txt')
        self.assertIn('synthesizer', self.invoke(2, stage='synthesize', extra=inputs)['error'])
        self.configure(synthesizer='claude', effort='high', on_failure='fallback')
        self.assertIn('required', self.invoke(2, stage='synthesize')['error'])
        self.assertIn('forbidden', self.invoke(2, extra=inputs)['error'])
        self.assertFalse(self.out.exists())
        with self.subTest('config synthesis'):
            evidence = self.invoke(0, stage='synthesize', extra=inputs)
            self.assertEqual((evidence['requested_effort'], evidence['on_failure']), ('high', 'fallback'))
            argv = self.call_args()[-1]
            self.assertEqual(argv[-2:], ['--effort', 'high'])
            self.assertIn('<<<BEGIN_CLAUDE_DRAFT>>>\nClaude draft', argv[1])
            self.assertIn('<<<BEGIN_AGY_DRAFT>>>\nAGY draft <REPO_ROOT>', argv[1])
            self.assertEqual(set(json.loads((self.base / 'snapshot.json').read_text())),
                             {'tracked.txt', 'approved.txt'})
        self.configure(synthesizer='agy')
        self.out = self.base / 'synthesis-override'
        self.assertEqual(self.invoke(0, stage='synthesize', trigger='user-message', extra=inputs)['trigger'],
                         'user-message')

    def test_user_message_override_still_validates_config(self):
        for data in ({}, {'agy_relay': {'dual_plan': {'enabled': False}}}):
            self.config.write_text(json.dumps(data))
            self.assertIn('enabled=true', self.invoke(2)['error'])
        for data in ({}, {'agy_relay': {'dual_plan': {'enabled': False}}}):
            self.config.write_text(json.dumps(data))
            self.out = self.base / f'override-{bool(data)}'
            evidence = self.invoke(0, trigger='user-message')
            self.assertIsNone(evidence['requested_model'])
        self.out = self.base / 'invalid-override'
        self.configure(enabled=1)
        self.assertIn('must be a boolean', self.invoke(2, trigger='user-message')['error'])
        self.assertFalse(self.out.exists())

    def test_model_families_and_exact_model(self):
        for requested, actual in (
            ('opus', 'claude-opus-4-7'), ('fable', 'claude-fable-5'), ('sonnet', ACTUAL),
            ('claude-sonnet-4-6', ACTUAL), ('claude-sonnet-4-6.1', 'claude-sonnet-4-6.1'),
            ('local-alias', 'claude-custom-3'),
        ):
            with self.subTest(requested=requested):
                self.out = self.base / requested
                self.configure(model=requested)
                if requested == 'local-alias':
                    data = json.loads(self.config.read_text())
                    data['claude_review'] = {'model_families': {'local-alias': r'claude-custom-[0-9]+'}}
                    self.config.write_text(json.dumps(data))
                self.scenario([init(actual), assistant({'type': 'text', 'text': 'plan'}, model=actual), terminal()])
                evidence = self.invoke(0)
                self.assertEqual(evidence['actual_model'], actual)
                self.assertEqual(self.call_args()[-1][-2:], ['--model', requested])

    def test_replays_existing_claude_samples(self):
        for alias, fixture, expected in (
            ('haiku', 'haiku', 'claude-haiku-5-5'),
            ('opus', 'opus-thinking', None),
            ('opus', 'opus-ui_invalidate', None),
        ):
            with self.subTest(fixture=fixture):
                self.out = self.base / fixture
                self.configure(model=alias)
                events = [json.loads(line) for line in (FIXTURE / f'claude-stream-{fixture}.jsonl').read_text().splitlines()]
                if expected is None:
                    expected = next(event['model'] for event in events if event.get('subtype') == 'init')
                self.scenario(events)
                self.assertEqual(self.invoke(0)['actual_model'], expected)

    def test_unknown_alias_rejected_before_cli_or_output(self):
        for model in ('unknown', 'claude-', 'claude-sonnet', 'Claude-opus-4-7'):
            with self.subTest(model=model):
                self.configure(model=model)
                summary = self.invoke(2)
                self.assertEqual(summary['status'], 'invalid_config')
                self.assertIn('claude_review.model_families', summary['error'])
                self.assertFalse(self.calls.exists())
                self.assertFalse(self.out.exists())

    def test_model_mismatch_and_missing_identity(self):
        for index, (requested, actual, message_model, error) in enumerate((
            ('haiku', ACTUAL, ACTUAL, 'unexpected actual'),
            ('claude-sonnet-4-6', 'claude-sonnet-4-5', ACTUAL, 'unexpected actual'),
            (None, '', ACTUAL, 'unexpected actual'),
            (None, ACTUAL, 'claude-sonnet-4-5', 'unexpected assistant'),
            ('sonnet', ACTUAL, None, 'unexpected assistant'),
        )):
            with self.subTest(requested=requested, actual=actual, message_model=message_model):
                self.out = self.base / f'mismatch-{index}'
                self.configure(model=requested)
                self.scenario([init(actual), assistant({'type': 'text', 'text': 'plan'}, model=message_model), terminal()])
                self.assertIn(error, self.invoke(1)['error'])

    def test_capability_preflight_requires_exact_flags(self):
        self.configure(model='haiku', effort='low')
        for flag in ('--restricted', '--safe-mode', '--allowedTools', '--model', '--effort'):
            with self.subTest(flag=flag):
                self.out = self.base / flag[2:]
                self.scenario(help=[item if item != flag else item + '-other' for item in FLAGS])
                self.assertIn(flag, self.invoke(2)['error'])
        self.assertEqual(self.call_args(), [['--version'], ['--help']] * 5)
        self.out = self.base / 'invalid-version'
        self.scenario(version='bad version')
        self.assertIn('semver', self.invoke(2)['error'])
        self.out = self.base / 'untested-version'
        self.configure()
        self.scenario(version='9.8.7', help=[item for item in FLAGS if item not in ('--model', '--effort')])
        self.assertIn('untested_cli_version', [w['kind'] for w in self.invoke(0)['warnings']])

    def test_disallowed_tool_wins_over_nonzero_and_truncated_log(self):
        self.scenario([init(), '{truncated', assistant(tool('Bash', {'command': 'true'}))], code=9)
        evidence = self.invoke(3)
        self.assertEqual(evidence['error_kind'], 'integrity_violation')
        self.assertIn('disallowed tool invocation', evidence['error'])
        self.assertEqual(evidence['cli_exit_code'], 9)

    def test_outside_read_is_integrity_violation(self):
        self.scenario([init(), assistant(tool('Read', {'file_path': '../outside.txt'})),
                       tool_result(), terminal()])
        evidence = self.invoke(3)
        self.assertEqual(evidence['error_kind'], 'integrity_violation')
        self.assertIn('Read.file_path', evidence['error'])

    def test_other_tool_paths_never_leave_snapshot(self):
        scenarios = (
            ([assistant(tool('Glob', {'path': str(self.repo)}))], {}, 'Glob.path'),
            ([assistant(tool('Grep', {'path': '\0bad'}))], {}, 'Grep.path'),
            ([assistant(tool('Glob', {'path': '~/elsewhere'}))], {}, 'Glob.path'),
            ([assistant(tool('Read', {'file_path': 'escape/tracked.txt'}))], {'symlink': True}, 'Read.file_path'),
            ([assistant(tool('Read', {'file_path': 'tracked.txt'})),
              tool_result(tool_use_result={'file': {'filePath': str(self.request)}})], {}, 'tool_use_result.filePath'),
            ([assistant(tool('Read', {'file_path': 'tracked.txt'})),
              tool_result(tool_use_result={'filePath': str(self.request)})], {}, 'tool_use_result.filePath'),
            ([assistant(tool('Read', {'file_path': '@STAGING@/../.staging/tracked.txt'}))], {}, 'Read.file_path'),
        )
        for index, (events, scenario, field) in enumerate(scenarios):
            with self.subTest(field=field, index=index):
                self.out = self.base / f'outside-{index}'
                self.scenario([init(), *events, terminal()], **scenario)
                self.assertIn(field, self.invoke(3)['error'])

    def test_optional_reads_and_rewrites_staging_paths(self):
        parallel_results = {
            'type': 'user',
            'message': {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': 'tool-1', 'is_error': True, 'content': 'missing'},
                {'type': 'tool_result', 'tool_use_id': 'tool-2', 'is_error': False, 'content': 'ok'},
            ]},
            'tool_use_result': {'file': {'filePath': '@STAGING@/tracked.txt'}},
        }
        self.scenario([
            init(),
            assistant(tool('Read', {'file_path': 'missing.txt'}, 'tool-1'),
                      tool('Read', {'file_path': '@STAGING@/safe-link'}, 'tool-2')),
            parallel_results,
            assistant(tool('Grep', {'path': None}, 'tool-3')), tool_result('tool-3'),
            terminal('See [tracked.txt](file://@STAGING@/tracked.txt) and @STAGING@/approved.txt.'),
        ], stderr='fixture diagnostic')
        evidence = self.invoke(0)
        self.assertEqual(evidence['tool_warnings'], [{'kind': 'tool_error', 'tool': 'Read', 'tool_use_id': 'tool-1'}])
        self.assertIn({'kind': 'staging_path_rewritten', 'count': 2}, evidence['warnings'])
        self.assertEqual((self.out / 'plan.md').read_text(), 'See `tracked.txt` and approved.txt.\n')
        self.assertEqual((self.out / 'plan.stderr.txt').read_text(), 'fixture diagnostic\n')

    def test_invalid_streams_and_unsafe_init_fail_closed(self):
        use = assistant(tool('Read', {'file_path': 'tracked.txt'}))
        for index, events in enumerate((
            [init(plugins=[{'name': 'external'}]), terminal()],
            [init(tools=['Read', 'Glob']), terminal()],
            [init(permissionMode='acceptEdits'), terminal()],
            [init(), tool_result(), terminal()],
            [init(), use, use, tool_result(), terminal()],
            [init(), use, tool_result(error=1), terminal()],
            [init(), use, tool_result(), tool_result(), terminal()],
            [init(), use, terminal()],
            [init(), terminal(is_error=True)],
            [init(), terminal(permission_denials=['Read'])],
            [init(), terminal(' ')],
            [init(), terminal(subtype='error')],
            [init(), terminal(), terminal()],
            [init(), '{"type":"result"'],
        )):
            with self.subTest(events=events):
                self.out = self.base / f'protocol-{index}'
                self.scenario(events)
                self.invoke(1)

    def test_quota_exhaustion_and_allowed_rate_event(self):
        allowed = {'type': 'rate_limit_event', 'rate_limit_info': {'status': 'allowed'}}
        self.scenario([init(), allowed, terminal()])
        self.assertIn({'kind': 'rate_limit_event', 'status': 'allowed'}, self.invoke(0)['warnings'])
        for index, event in enumerate(({'error': 'rate_limit'},
                                       {'type': 'rate_limit_event', 'rate_limit_info': {'status': 'rejected'}})):
            self.out = self.base / f'quota-{index}'
            self.scenario([init(), event], code=1)
            self.assertEqual(self.invoke(1)['error_kind'], 'quota_exhausted')

    def test_input_and_prompt_limits_are_distinct(self):
        with self.request.open('wb') as handle:
            handle.truncate(MAX_INPUT_READ_BYTES + 1)
        self.assertIn('input limit (2097152 bytes)', self.invoke(2)['error'])
        self.request.write_text('甲' * 44000)
        self.assertIn('exceeds the limit (122880 bytes)', self.invoke(2)['error'])
        self.request.write_text('req')
        instructions = self.base / 'instructions.md'
        instructions.write_text(f'Inspect {self.repo}/tracked.txt')
        self.assertIn('--instructions must not expose', self.invoke(2, extra=('--instructions', str(instructions)))['error'])
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.out.exists())

    def test_snapshot_mutation_overrides_success(self):
        self.scenario(mutation=True)
        self.assertIn('staging snapshot changed', self.invoke(3)['error'])
        self.assertEqual((self.repo / 'tracked.txt').read_text(), 'working tree\n')
        self.out = self.base / 'changed-permissions'
        self.scenario(chmod_only=True)
        self.assertIn('staging snapshot changed', self.invoke(3)['error'])

    def test_snapshot_symlink_chain_cannot_escape_before_claude_starts(self):
        directory = self.repo / 'sub'
        directory.mkdir()
        (directory / 'alias').symlink_to('..')
        (directory / 'escape').symlink_to('alias/../..')
        self.git('add', 'sub')
        evidence = self.invoke(3)
        self.assertEqual(evidence['error_kind'], 'integrity_violation')
        self.assertIn('staging symlink', evidence['error'])
        self.assertFalse(self.calls.exists())

    def test_e2big_reports_exit_two_and_cleans_staging(self):
        spec = importlib.util.spec_from_file_location('fixture_claude_plan', ENTRY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        real_execute = module.execute

        def execute(argv, *args, **kwargs):
            if argv[1] == '-p':
                raise RunError('could not launch CLI') from OSError(errno.E2BIG, 'Argument list too long')
            return real_execute(argv, *args, **kwargs)

        with patch.object(module, 'execute', side_effect=execute), patch.dict(os.environ, self.env), \
             patch.object(sys, 'argv', self.command()[1:]), redirect_stdout(io.StringIO()):
            self.assertEqual(module.main(), 2)
        self.assertEqual(self.assert_evidence(2)['error_kind'], 'prompt_too_large')

    def test_sigint_and_sigterm_preserve_interrupted_evidence(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=sig):
                self.out = self.base / sig.name
                marker = self.base / 'snapshot.json'
                marker.unlink(missing_ok=True)
                self.scenario(wait=True)
                with subprocess.Popen(self.command(), env=self.env, stdin=subprocess.DEVNULL, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
                    try:
                        deadline = time.monotonic() + 5
                        while not marker.exists() and proc.poll() is None and time.monotonic() < deadline:
                            time.sleep(.05)
                        self.assertTrue(marker.exists(), 'fake CLI did not reach the planning call')
                        proc.send_signal(sig)
                        stdout, stderr = proc.communicate(timeout=5)
                        self.assertEqual(proc.returncode, 128 + sig, (stdout, stderr))
                        self.assertEqual(json.loads(stdout)['status'], 'interrupted')
                        self.assertEqual(self.assert_evidence(128 + sig)['error_kind'], 'interrupted')
                    finally:
                        if proc.poll() is None:
                            proc.kill()
                            proc.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
