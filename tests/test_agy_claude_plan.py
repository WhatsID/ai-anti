"""Offline AGY-led planning tests with an isolated repository and fake Claude CLI."""
import errno
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
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
import json, os, stat, subprocess, sys, time
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
if fixture.get('rewrite_file'):
    path, content = fixture['rewrite_file']
    Path(path).write_text(content)
if fixture.get('create_untracked_in'):
    (Path(fixture['create_untracked_in']) / 'concurrent.txt').write_text('source only\n')
if fixture.get('commit_in'):
    subprocess.run(
        ['git', '-C', fixture['commit_in'], '-c', 'user.name=Fixture', '-c', 'user.email=fixture.invalid',
         'commit', '-qm', 'concurrent change', '--', 'tracked.txt'], check=True,
    )
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

    def git(self, *args, repo=None):
        return subprocess.run(['git', '-C', str(repo or self.repo), *args], env=self.env, stdin=subprocess.DEVNULL,
                              capture_output=True, check=True, timeout=8).stdout

    def planning_worktree(self, name='plan-worktree'):
        head = self.git('rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
        worktree = self.base / name
        self.git('worktree', 'add', '--detach', str(worktree), head)
        return worktree, head

    def configure(self, **plan):
        self.config.write_text(json.dumps({'agy_relay': {'dual_plan': dict({'enabled': True}, **plan)}}))

    def scenario(self, events=None, **fields):
        (self.base / 'scenario.json').write_text(json.dumps(
            dict({'events': [init(), terminal()] if events is None else events, 'help': FLAGS}, **fields)))

    def command(self, *, stage='draft', trigger='config', extra=(), repo=None):
        return [sys.executable, str(ENTRY), '--repo', str(repo or self.repo), '--out', str(self.out),
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

    def run_snapshot(self, code, args=(), **kwargs):
        capture = self.base / 'snapshot-tree.json'
        capture.unlink(missing_ok=True)
        with patch.dict(self.env, FIXTURE_SNAPSHOT_CAPTURE=str(capture)):
            evidence = self.invoke(code, extra=args, **kwargs)
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

    def test_source_repo_redacts_materials_and_omissions_in_both_stages(self):
        source, _ = self.planning_worktree('source-worktree')
        alias = self.base / 'source-alias'
        alias.symlink_to(source, target_is_directory=True)
        worktree, _ = self.planning_worktree()
        redact_paths = f'{source}/tracked.txt {alias}/tracked.txt {worktree}/tracked.txt'
        redacted = ' '.join(['<REPO_ROOT>/tracked.txt'] * 3)
        claude, agy = self.base / 'claude.md', self.base / 'agy.md'
        materials = {path: f'{path.stem}: {redact_paths}\n'.encode() for path in (self.request, claude, agy)}
        for path, data in materials.items():
            path.write_bytes(data)
        excluded = f'fixture{source}/skip.txt'
        (worktree / self.declaration).parent.mkdir()
        (worktree / self.declaration).write_text(excluded + '\n')
        (worktree / excluded).parent.mkdir(parents=True)
        (worktree / excluded).write_text('omitted\n')
        self.git('add', '--', self.declaration, excluded, repo=worktree)
        self.configure(synthesizer='claude')
        source_args = ('--source-repo', str(alias))
        draft_args = ('--claude-draft', str(claude), '--agy-draft', str(agy))
        for stage, paths, extra in (('draft', (self.request,), ()), ('synthesize', materials, draft_args)):
            with self.subTest(stage=stage):
                self.out = self.base / stage
                evidence = self.invoke(0, stage=stage, repo=worktree, extra=source_args + extra)
                self.assertEqual(evidence['repo'], str(worktree))
                prompt = (self.out / 'prompt.txt').read_text()
                self.assertEqual(self.call_args()[-1][1], prompt)
                for path in paths:
                    self.assertIn(f'{path.stem}: {redacted}\n', prompt)
                self.assertIn('- Excluded path: "fixture<REPO_ROOT>/skip.txt"', prompt)
                self.assertEqual(evidence['snapshot_omissions']['omitted_files'], 1)
                for root in (source, alias, worktree):
                    self.assertNotIn(str(root), prompt)
                for path, data in materials.items():
                    self.assertEqual(path.read_bytes(), data)

    def test_full_options_without_source_repo_match_baseline_bytes(self):
        self.configure(synthesizer='claude')
        alias = self.base / 'repo-alias'
        alias.symlink_to(self.repo, target_is_directory=True)
        claude, agy = self.base / 'claude.md', self.base / 'agy.md'
        claude.write_bytes(f'Claude saw {alias}/tracked.txt.\n'.encode())
        agy.write_bytes(f'AGY saw {self.repo}/tracked.txt.\n'.encode())
        materials = {path: path.read_bytes() for path in (self.request, claude, agy)}
        self.write_snapshot_file(self.declaration, b'skip.txt\n')
        self.write_snapshot_file('skip.txt', b'skip\n')
        self.commit_snapshot_paths(self.declaration, 'skip.txt')
        extras = ('--extra-file', 'approved.txt')
        # Prompt hashes captured by running both stages at 688135a with these inputs.
        for stage, digest in (
            ('draft', 'f258075a80dbc85cf32a423bb7ca98d21da2d3b355c80b86a861641b1a1d01d9'),
            ('synthesize', 'e88fd3366e8269fb86bc55c45c379514b385ecff503e4fab8680a2651a103dea'),
        ):
            with self.subTest(stage=stage):
                self.out = self.base / stage
                drafts = ('--claude-draft', str(claude), '--agy-draft', str(agy)) if stage == 'synthesize' else ()
                _, snapshot = self.run_snapshot(0, extras + drafts, stage=stage, repo=alias)
                self.assertEqual(hashlib.sha256((self.out / 'prompt.txt').read_bytes()).hexdigest(), digest)
                self.assertEqual(set(snapshot), {'.ai-anti', self.declaration, 'safe-link', 'tracked.txt', 'approved.txt'})
                self.assertEqual(snapshot['safe-link'], ['symlink', 'tracked.txt'])
                self.assert_snapshot_bytes(snapshot, self.declaration, b'skip.txt\n')
                self.assert_snapshot_bytes(snapshot, 'tracked.txt', b'working tree\n')
                self.assert_snapshot_bytes(snapshot, 'approved.txt', b'approved\n')
                for path, data in materials.items():
                    self.assertEqual(path.read_bytes(), data)

    def assert_rejected_before_cli(self, error, **kwargs):
        summary = self.invoke(2, **kwargs)
        self.assertIn(error, summary['error'])
        self.assertFalse(self.out.exists())
        self.assertFalse(self.calls.exists())

    def test_source_repo_paths_in_instructions_are_rejected_before_cli(self):
        worktree, _ = self.planning_worktree()
        alias = self.base / 'source-alias'
        alias.symlink_to(self.repo, target_is_directory=True)
        instructions = self.base / 'instructions.md'
        for root in (self.repo, alias):
            with self.subTest(root=root):
                instructions.write_text(f'Read {root}/tracked.txt.\n')
                self.assert_rejected_before_cli(
                    '--instructions must not expose real repository absolute path', repo=worktree,
                    extra=('--source-repo', str(alias), '--instructions', str(instructions)),
                )

    def test_source_repo_must_be_a_worktree_root_of_the_same_repository(self):
        worktree, _ = self.planning_worktree()
        subdir = self.repo / 'nested'
        subdir.mkdir()
        foreign = self.base / 'foreign'
        foreign.mkdir()
        self.git('init', '-q', repo=foreign)
        for root, error in (
            (subdir, f'--source-repo must be the root of a Git working tree: {subdir}'),
            (foreign, '--source-repo must share a Git common directory with --repo'),
        ):
            with self.subTest(root=root):
                self.assert_rejected_before_cli(error, repo=worktree, extra=('--source-repo', str(root)))

    def test_out_inside_source_repo_is_rejected_before_cli(self):
        worktree, _ = self.planning_worktree()
        self.out = self.repo / 'new' / 'out'
        self.assert_rejected_before_cli('output directory must be outside the repository',
                                        repo=worktree, extra=('--source-repo', str(self.repo)))
        self.assertFalse(self.out.parent.exists())

    def test_out_inside_case_variant_of_either_repo_is_rejected_before_cli(self):
        worktree, _ = self.planning_worktree()
        for root in (self.repo, worktree):
            alias = root.with_name(root.name.upper())
            if not alias.exists() or not os.path.samefile(alias, root):
                self.skipTest('fixture volume is case-sensitive')
            with self.subTest(root=root):
                self.out = alias / 'new' / 'out'
                self.assert_rejected_before_cli('output directory must be outside the repository',
                                                repo=worktree, extra=('--source-repo', str(self.repo)))
                self.assertFalse(self.out.parent.exists())

    def test_git_path_segments_in_extra_files_are_rejected_before_cli(self):
        worktree, _ = self.planning_worktree()
        self.assertTrue((worktree / '.git').is_file())
        nested_git = worktree / 'nested/.git'
        nested_git.mkdir(parents=True)
        (nested_git / 'config').write_text('fixture')
        for path in ('.git', 'nested/.git/config'):
            with self.subTest(path=path):
                self.assert_rejected_before_cli('--extra-file must not contain a .git path segment',
                                                repo=worktree, extra=('--extra-file', path))

    def test_linked_worktree_isolates_same_status_tracked_edit(self):
        worktree, head = self.planning_worktree()
        tracked, mirror = self.repo / 'tracked.txt', worktree / 'tracked.txt'
        dirty_a, dirty_b = b'confirmed A\n', b'concurrent B\n'
        self.scenario(rewrite_file=(str(tracked), dirty_b.decode()))
        for repo, extra, code in ((self.repo, (), 3), (worktree, ('--source-repo', str(self.repo)), 0)):
            with self.subTest(repo=repo):
                tracked.write_bytes(dirty_a)
                before = self.git('status', '--porcelain=v1', '-z')
                self.assertIn(b' M tracked.txt\0', before)
                self.assertEqual(self.git('rev-parse', 'HEAD').decode().strip(), head)
                shutil.copy2(tracked, mirror)
                self.out = self.base / (repo.name + '-out')
                evidence = self.invoke(code, repo=repo, extra=extra)
                self.assertEqual(self.git('status', '--porcelain=v1', '-z'), before)
                self.assertEqual(self.git('rev-parse', 'HEAD').decode().strip(), head)
                self.assertEqual(tracked.read_bytes(), dirty_b)
                self.assertEqual(json.loads((self.base / 'snapshot.json').read_text())['tracked.txt'].encode(), dirty_a)
                if code == 3:
                    self.assertEqual(evidence['status'], 'integrity_aborted')
                    self.assertIn('tracked working-tree file contents changed', evidence['error'])
                else:
                    self.assertEqual(evidence['head'], head)
                    self.assertEqual(mirror.read_bytes(), dirty_a)

    def test_linked_worktree_isolates_source_untracked_file_and_commit(self):
        worktree, head = self.planning_worktree()
        self.scenario(create_untracked_in=str(self.repo), commit_in=str(self.repo))
        evidence, snapshot = self.run_snapshot(0, ('--source-repo', str(self.repo)), repo=worktree)
        self.assertEqual((evidence['status'], evidence['head']), ('completed', head))
        self.assertNotEqual(self.git('rev-parse', 'HEAD').decode().strip(), head)
        self.assertEqual(self.git('show', 'HEAD:tracked.txt'), b'working tree\n')
        self.assertIn(b'?? concurrent.txt\0', self.git('status', '--porcelain=v1', '-z'))
        self.assertNotIn('concurrent.txt', snapshot)
        self.assert_snapshot_bytes(snapshot, 'tracked.txt', b'committed\n')

    def test_linked_worktree_still_guards_tracked_file_request_staging_and_reads(self):
        worktree, _ = self.planning_worktree()
        tracked = worktree / 'tracked.txt'
        tracked.write_text('dirty before invocation\n')
        self.configure(on_failure='fallback')
        for name, hooks, error in (
            ('tracked', {'rewrite_file': (str(tracked), 'changed')}, 'tracked working-tree file contents changed'),
            ('request', {'rewrite_file': (str(self.request), 'changed')}, 'input file contents changed'),
            ('staging', {'mutation': True}, 'staging snapshot changed'),
            ('source-read', {'events': [init(), assistant(tool('Read', {'file_path': str(self.repo / tracked.name)})),
                                        tool_result(), terminal()]}, 'Read.file_path'),
            ('plan-read', {'events': [init(), assistant(tool('Read', {'file_path': str(tracked)})),
                                      tool_result(), terminal()]}, 'Read.file_path'),
        ):
            with self.subTest(name=name):
                self.out = self.base / name
                self.scenario(**hooks)
                evidence = self.invoke(3, repo=worktree, extra=('--source-repo', str(self.repo)))
                self.assertEqual((evidence['status'], evidence['on_failure']), ('integrity_aborted', 'fallback'))
                self.assertIn(error, evidence['error'])

    def test_linked_worktree_reuses_mirrored_inputs_for_both_stages(self):
        self.configure(synthesizer='claude')
        self.write_snapshot_file('.gitignore', b'.ai-anti/\n')
        self.write_snapshot_file('large/corpus.txt', b'x' * 8192)
        self.write_snapshot_file('deleted.txt', b'remove me\n')
        self.commit_snapshot_paths('.gitignore', 'large', 'deleted.txt')
        worktree, head = self.planning_worktree()
        contents = {'tracked.txt': b'confirmed\n', 'added.txt': b'added\n',
                    'approved.txt': b'approved\n', self.declaration: b'large/\n'}
        for rel, data in contents.items():
            target = worktree / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.write_snapshot_file(rel, data), target)
        for repo in (self.repo, worktree):
            self.git('add', '--', 'added.txt', repo=repo)
            self.git('add', '-f', '--', self.declaration, repo=repo)
            (repo / 'deleted.txt').unlink()
        args = ('--source-repo', str(self.repo), '--extra-file', 'approved.txt', '--max-snapshot-bytes', '4096')
        self.out = self.base / 'draft'
        draft, snapshot = self.run_snapshot(0, args, repo=worktree)
        claude = self.out / 'plan.md'
        agy = self.base / 'agy.md'
        agy.write_text('AGY draft\n')
        self.out = self.base / 'synthesize'
        synthesis, actual = self.run_snapshot(0, args + ('--claude-draft', str(claude), '--agy-draft', str(agy)),
                                              stage='synthesize', repo=worktree)
        self.assertEqual(actual, snapshot)
        self.assertEqual(set(snapshot), {*contents, '.ai-anti', '.gitignore', 'safe-link'})
        for rel, data in contents.items():
            self.assert_snapshot_bytes(snapshot, rel, data)
        for evidence in (draft, synthesis):
            self.assertEqual((evidence['repo'], evidence['head']), (str(worktree), head))

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
