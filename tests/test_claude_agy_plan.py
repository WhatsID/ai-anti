"""Offline tests for claude_relay.dual_plan config and agy_plan.py (Python 3.9+)."""
from contextlib import redirect_stderr, redirect_stdout
import errno
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'adapters' / 'claude-code' / 'scripts' / 'agy_plan.py'
sys.path.insert(0, str(ROOT / 'adapters' / 'codex' / 'skills' / 'claude-review' / 'scripts'))

from ai_anti_config import (
    AGY_EFFORTS,
    ConfigError,
    DUAL_PLAN_ON_FAILURE,
    DUAL_PLAN_PARTNERS,
    DUAL_PLAN_SYNTHESIZERS,
    claude_dual_plan,
)
from review_runner import RunError


def load_agy_plan_module():
    spec = importlib.util.spec_from_file_location('agy_plan_under_test', ENTRY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FAKE_AGY = """import json, os, pathlib, signal, stat, subprocess, sys, time
assert sys.stdin.read() == ''
if sys.argv[1:] == ['--version']:
    pathlib.Path(os.environ['FIXTURE_AGY_CALLS'] + '.version').write_text('called')
    if os.environ.get('FIXTURE_AGY_VERSION_FAIL'):
        print('version boom', file=sys.stderr)
        sys.exit(2)
    print(os.environ.get('FIXTURE_AGY_VERSION', 'agy 1.5.13'))
    sys.exit(0)

cwd = pathlib.Path.cwd()
record = {
    'argv': sys.argv[1:],
    'cwd': str(cwd),
    'files': sorted(
        str(p.relative_to(cwd))
        for p in cwd.rglob('*')
        if p.is_file() or p.is_symlink()
    ),
}
if (cwd / 'app.py').is_file():
    record['app_py'] = (cwd / 'app.py').read_text(encoding='utf-8')
if (cwd / 'added.py').is_file():
    record['added_py'] = (cwd / 'added.py').read_text(encoding='utf-8')
if (cwd / 'extra.txt').is_file():
    record['extra_txt'] = (cwd / 'extra.txt').read_text(encoding='utf-8')

if os.environ.get('FIXTURE_CHECK_READONLY'):
    try:
        (cwd / 'app.py').write_text('should fail', encoding='utf-8')
        record['write_blocked'] = False
    except PermissionError:
        record['write_blocked'] = True
    try:
        (cwd / 'new_in_staging.txt').write_text('should fail', encoding='utf-8')
        record['create_blocked'] = False
    except PermissionError:
        record['create_blocked'] = True

with open(os.environ['FIXTURE_AGY_CALLS'], 'a', encoding='utf-8') as handle:
    handle.write(json.dumps(record, ensure_ascii=False) + '\\n')

if os.environ.get('FIXTURE_MUTATE_REAL_FILE'):
    p = pathlib.Path(os.environ['FIXTURE_MUTATE_REAL_FILE'])
    p.write_text('tampered outside staging\\n', encoding='utf-8')

if os.environ.get('FIXTURE_CREATE_UNTRACKED_IN'):
    p = pathlib.Path(os.environ['FIXTURE_CREATE_UNTRACKED_IN']) / 'sneaky.txt'
    p.write_text('sneaky\\n', encoding='utf-8')

if os.environ.get('FIXTURE_MUTATE_STAGING'):
    os.chmod(cwd, os.stat(cwd).st_mode | 0o700)
    target = cwd / 'app.py'
    os.chmod(target, os.stat(target).st_mode | 0o200)
    target.write_text('tampered inside staging\\n', encoding='utf-8')

if os.environ.get('FIXTURE_STDERR'):
    print(os.environ['FIXTURE_STDERR'], file=sys.stderr)

mode = os.environ.get('FIXTURE_MODE', 'ok')
init_event = {
    'event': 'init',
    'conversation_id': 'conv-fixture-001',
    'init': {
        'cwd': str(cwd),
        'tools': ['read_file', 'write_to_file'],
        'permission_mode': os.environ.get('FIXTURE_PERM_MODE', 'request-review'),
        'expanded_commands': json.loads(
            os.environ.get('FIXTURE_EXPANDED_COMMANDS', '[{"name": "plan", "type": "system"}]')
        ),
    },
}
print(json.dumps(init_event))

if os.environ.get('FIXTURE_TOOL_WARNING'):
    print(json.dumps({
        'event': 'step_update',
        'step_update': {
            'conversation_id': 'conv-fixture-001',
            'step_index': 1,
            'state': 'DENIED',
            'step_type': 'tool',
            'tool_name': 'read_file',
            'tool_info': {'name': 'read_file', 'error': 'out of scope'},
        },
    }))

write_tool = os.environ.get('FIXTURE_WRITE_TOOL')
if write_tool:
    if write_tool.startswith('CORTEX_STEP_TYPE_'):
        print(json.dumps({
            'event': 'step_update',
            'step_update': {'step_type': write_tool, 'state': 'DONE'},
        }))
    else:
        print(json.dumps({
            'event': 'step_update',
            'step_update': {
                'conversation_id': 'conv-fixture-001',
                'step_index': 2,
                'state': 'DONE',
                'step_type': 'tool',
                'tool_name': write_tool,
                'tool_info': {'name': write_tool},
            },
        }))
else:
    print(json.dumps({
        'event': 'step_update',
        'step_update': {
            'conversation_id': 'conv-fixture-001',
            'step_index': 2,
            'state': 'DONE',
            'step_type': 'tool',
            'tool_name': 'read_file',
            'tool_info': {'name': 'read_file', 'output': 'ok'},
        },
    }))

if mode == 'block':
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])

    def stop(signum, frame):
        child.wait(timeout=3)
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, stop)
    sys.stdout.flush()
    pathlib.Path(os.environ['FIXTURE_AGY_CALLS'] + '.ready').write_text(
        json.dumps([os.getpid(), child.pid]))
    time.sleep(60)
if mode == 'child_sigterm':
    os.kill(os.getpid(), signal.SIGTERM)
if mode == 'nonzero':
    sys.exit(1)
if mode == 'write_then_nonzero':
    sys.exit(1)
if mode == 'bad_status':
    print(json.dumps({
        'event': 'result',
        'result': {'status': 'FAILED', 'response': 'nope', 'conversation_id': 'conv-fixture-001'},
    }))
    sys.exit(0)
if mode == 'empty_response':
    print(json.dumps({
        'event': 'result',
        'result': {'status': 'SUCCESS', 'response': '   \\n', 'conversation_id': 'conv-fixture-001'},
    }))
    sys.exit(0)
if mode == 'trailing_event':
    print(json.dumps({
        'event': 'result',
        'result': {'status': 'SUCCESS', 'response': '# Plan', 'conversation_id': 'conv-fixture-001'},
    }))
    print(json.dumps({'event': 'step_update', 'tool_call': {'name': 'read_file', 'status': 'SUCCESS'}}))
    sys.exit(0)

response = os.environ.get('FIXTURE_RESPONSE', '# AGY Plan\\n\\n- Concrete step')
print(json.dumps({
    'event': 'result',
    'result': {'status': 'SUCCESS', 'response': response, 'conversation_id': 'conv-fixture-001'},
}))
"""


class ClaudeDualPlanConfigTests(unittest.TestCase):
    def test_missing_and_null_return_none(self):
        for cfg in ({}, {'claude_relay': None}, {'claude_relay': {}}, {'claude_relay': {'dual_plan': None}}):
            with self.subTest(cfg=cfg):
                self.assertIsNone(claude_dual_plan(cfg))

    def test_defaults_and_valid_combinations(self):
        self.assertEqual(AGY_EFFORTS, ('low', 'medium', 'high', 'xhigh', 'max'))
        self.assertEqual(DUAL_PLAN_PARTNERS, ('agy',))
        self.assertEqual(DUAL_PLAN_SYNTHESIZERS, ('claude', 'agy'))
        self.assertEqual(DUAL_PLAN_ON_FAILURE, ('stop', 'fallback'))

        self.assertEqual(
            claude_dual_plan({'claude_relay': {'dual_plan': {}}}),
            {
                'enabled': False,
                'partner': 'agy',
                'model': None,
                'effort': None,
                'synthesizer': 'claude',
                'on_failure': 'stop',
            },
        )
        for effort in (None, *AGY_EFFORTS):
            for synthesizer in DUAL_PLAN_SYNTHESIZERS:
                for on_failure in DUAL_PLAN_ON_FAILURE:
                    sample = {
                        'claude_relay': {
                            'dual_plan': {
                                'enabled': True,
                                'partner': 'agy',
                                'model': 'gemini-3-pro/plan:v1',
                                'effort': effort,
                                'synthesizer': synthesizer,
                                'on_failure': on_failure,
                                'future_extra': {'keep': True},
                            }
                        }
                    }
                    with self.subTest(effort=effort, synthesizer=synthesizer, on_failure=on_failure):
                        self.assertEqual(
                            claude_dual_plan(sample),
                            {
                                'enabled': True,
                                'partner': 'agy',
                                'model': 'gemini-3-pro/plan:v1',
                                'effort': effort,
                                'synthesizer': synthesizer,
                                'on_failure': on_failure,
                            },
                        )

    def test_invalid_fields_fail_closed_even_when_disabled(self):
        invalid_configs = [
            ([], r'parent is not an object'),
            ({'claude_relay': 'bad'}, r'parent is not an object'),
            ({'claude_relay': {'dual_plan': 'agy'}}, r'must be an object'),
            ({'claude_relay': {'dual_plan': []}}, r'must be an object'),
            ({'claude_relay': {'dual_plan': True}}, r'must be an object'),
            # Invalid enabled
            *[
                ({'claude_relay': {'dual_plan': {'enabled': bad}}}, r'dual_plan\.enabled')
                for bad in (None, 1, 0, 'true', 'false', [], {})
            ],
            # Invalid partner (even with enabled=False)
            *[
                ({'claude_relay': {'dual_plan': {'enabled': False, 'partner': bad}}}, r'dual_plan\.partner')
                for bad in ('codex', 'AGY', '', 'agy ', 1, True)
            ],
            # Invalid model
            *[
                ({'claude_relay': {'dual_plan': {'enabled': False, 'model': bad}}}, r'dual_plan\.model')
                for bad in ('', 'a b', 'a\nb', 'm\t1', 123, True, [])
            ],
            # Invalid effort (note 'ultra' is not valid for AGY)
            *[
                ({'claude_relay': {'dual_plan': {'enabled': False, 'effort': bad}}}, r'dual_plan\.effort')
                for bad in ('ultra', 'HIGH', 'high ', '', 1, True, [])
            ],
            # Invalid synthesizer
            *[
                ({'claude_relay': {'dual_plan': {'enabled': False, 'synthesizer': bad}}}, r'dual_plan\.synthesizer')
                for bad in ('codex', 'CLAUDE', 'AGY', '', 1, True)
            ],
            # Invalid on_failure
            *[
                ({'claude_relay': {'dual_plan': {'enabled': False, 'on_failure': bad}}}, r'dual_plan\.on_failure')
                for bad in ('retry', 'STOP', 'FALLBACK', '', 1, True)
            ],
        ]
        for cfg, pattern in invalid_configs:
            with self.subTest(cfg=cfg):
                with self.assertRaisesRegex(ConfigError, pattern):
                    claude_dual_plan(cfg)


class AgyPlanTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.repo = self.base / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')
        (self.repo / '.gitignore').write_text('.claude/plans/\nbuild/\n', encoding='utf-8')
        (self.repo / 'app.py').write_text('def greet():\n    return "v1"\n', encoding='utf-8')
        (self.repo / 'deleted.txt').write_text('to be deleted\n', encoding='utf-8')
        self.git('add', '.gitignore', 'app.py', 'deleted.txt')
        self.git('-c', 'user.email=fixture.invalid', '-c', 'user.name=Fixture', 'commit', '-qm', 'init')

        # Ignored request file inside repo (mirrors real .claude/plans/*.request.md)
        plans_dir = self.repo / '.claude' / 'plans'
        plans_dir.mkdir(parents=True)
        self.request = plans_dir / '20261120-demo.request.md'
        self.request.write_text(
            '- Requirement starting with dash\n'
            'Please add dual-plan support.\n\n'
            '## 附录：非需求（主模型推断，可质疑）\n'
            '- Secret Claude inference that AGY must never see.\n',
            encoding='utf-8',
        )
        self.claude_draft = self.base / 'plan-claude.md'
        self.claude_draft.write_text('# Claude Draft\n\nConstraint A: keep config strict.\n', encoding='utf-8')
        self.agy_draft = self.base / 'plan-agy.md'
        self.agy_draft.write_text('# AGY Draft\n\nConstraint B: isolate snapshot.\n', encoding='utf-8')
        self.instructions = self.base / 'instructions.md'
        self.instructions.write_text('Deployment scale: single-user local CLI.\n', encoding='utf-8')

        self.config = self.base / 'config.json'
        self.write_config()

        self.calls = self.base / 'calls.jsonl'
        self.out = self.base / 'out'
        self.bin_dir = self.base / 'bin'
        self.bin_dir.mkdir()
        cli = self.bin_dir / 'agy'
        cli.write_text('#!' + sys.executable + '\n' + FAKE_AGY, encoding='utf-8')
        cli.chmod(0o700)
        self.env = dict(
            os.environ,
            PATH=str(self.bin_dir) + os.pathsep + os.environ['PATH'],
            FIXTURE_AGY_CALLS=str(self.calls),
            HOME=str(self.base),
        )

    def git(self, *args):
        return subprocess.run(
            ['git', '-C', str(self.repo), *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def write_config(self, **overrides):
        dual = {
            'enabled': True,
            'partner': 'agy',
            'model': None,
            'effort': None,
            'synthesizer': 'claude',
            'on_failure': 'stop',
        }
        dual.update(overrides)
        self.config.write_text(json.dumps({'claude_relay': {'dual_plan': dual}}), encoding='utf-8')

    def command(self, stage='draft', out=None, extra_args=None, repo=None, request=None):
        cmd = [
            sys.executable,
            str(ENTRY),
            '--repo', str(repo if repo is not None else self.repo),
            '--stage', stage,
            '--request', str(request if request is not None else self.request),
            '--out', str(out if out is not None else self.out),
            '--config', str(self.config),
            '--timeout', '15',
            '--idle-timeout', '5',
        ]
        if extra_args:
            cmd.extend(extra_args)
        return cmd

    def invoke(self, extra_env=None, **kwargs):
        cmd = self.command(**kwargs)
        env = dict(self.env)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=20)

    def invoke_main(self, mod):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, 'argv', self.command()[1:]), \
             mock.patch.dict(os.environ, self.env), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            code = mod.main()
        return subprocess.CompletedProcess([], code, stdout.getvalue(), stderr.getvalue())

    def assert_no_cli(self):
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.calls.with_suffix('.jsonl.version').exists())

    def assert_terminal(self, result, code, status, error_kind=None, out=None):
        self.assertEqual(result.returncode, code, result.stderr)
        manifest = self.load_manifest(out)
        self.assertEqual(manifest['status'], status)
        self.assertEqual(manifest['error_kind'], error_kind)
        self.assertFalse(manifest['completed'])
        self.assertFalse(manifest['fallback_allowed'])
        self.assertEqual(manifest['integrity_ok'], code != 3)
        target = out or self.out
        self.assertFalse((target / 'plan.md').exists())
        self.assertFalse((target / '.plan.md.tmp').exists())
        log = target / 'plan.jsonl'
        if log.is_file():
            self.assertEqual(manifest['log_sha256'], hashlib.sha256(log.read_bytes()).hexdigest())
        for key, value in json.loads(result.stdout).items():
            self.assertEqual(value, manifest[key], key)
        return manifest

    def synthesize_args(self):
        return ['--claude-draft', str(self.claude_draft), '--agy-draft', str(self.agy_draft)]

    def load_manifest(self, out=None):
        target = out if out is not None else self.out
        manifest = json.loads((target / 'manifest.json').read_text(encoding='utf-8'))
        status = json.loads((target / 'status.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest, status)
        return manifest

    def test_user_message_trigger_uses_defaults_inherits_fields_and_overrides_synthesizer(self):
        for label, stage, config in (
            ('missing', 'draft', {}),
            ('disabled', 'draft', {'enabled': False, 'model': 'fixture-model', 'effort': 'high'}),
            ('claude', 'synthesize', {'enabled': True, 'synthesizer': 'claude'}),
            ('missing-synthesizer', 'synthesize', {}),
        ):
            with self.subTest(case=label):
                if config:
                    self.write_config(**config)
                else:
                    self.config.write_text('{}', encoding='utf-8')
                out = self.base / label
                args = ['--trigger', 'user-message']
                if stage == 'synthesize':
                    args.extend(self.synthesize_args())
                result = self.invoke(stage=stage, out=out, extra_args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                manifest = self.load_manifest(out)
                self.assertEqual(manifest['trigger'], 'user-message')
                self.assertEqual(manifest['requested_model'], config.get('model'))
                self.assertEqual(manifest['requested_effort'], config.get('effort'))
                self.assertEqual(manifest['on_failure'], 'stop')
                argv = json.loads(self.calls.read_text(encoding='utf-8').splitlines()[-1])['argv']
                if label == 'disabled':
                    self.assertEqual(argv[-5:-1], ['--model', 'fixture-model', '--effort', 'high'])
                else:
                    self.assertNotIn('--model', argv)
                    self.assertNotIn('--effort', argv)

    def test_user_message_trigger_still_validates_config_and_review_limits(self):
        for config, error in (
            ('{broken', 'invalid JSON'),
            ('{"claude_relay":{"dual_plan":{"enabled":false,"effort":"ultra"}}}', '.effort'),
            ('{"review":{"timeout":0}}', 'review.timeout'),
        ):
            with self.subTest(error=error):
                self.config.write_text(config, encoding='utf-8')
                result = self.invoke(extra_args=['--trigger', 'user-message'])
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(error, result.stderr)
                self.assertFalse(self.out.exists())
                self.assert_no_cli()

    def test_distinct_output_directories_preserve_evidence_and_reuse_is_rejected(self):
        first = self.invoke(extra_env={'FIXTURE_RESPONSE': '# First'})
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(self.load_manifest()['trigger'], 'config')
        first_files = {path.name: path.read_bytes() for path in self.out.iterdir()}
        second_out = self.base / 'out-2'
        second = self.invoke(out=second_out, extra_env={'FIXTURE_RESPONSE': '# Second'})
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual((second_out / 'plan.md').read_text(encoding='utf-8'), '# Second\n')
        self.assertEqual(self.load_manifest(second_out)['status'], 'completed')
        reused = self.invoke()
        self.assertEqual(reused.returncode, 2, reused.stderr)
        self.assertEqual({path.name: path.read_bytes() for path in self.out.iterdir()}, first_files)
        self.assertEqual(len(self.calls.read_bytes().splitlines()), 2)

    def test_oversize_prompt_is_rejected_before_output_and_cli(self):
        self.write_config(synthesizer='agy')
        for stage, content in (
            ('draft', 'a' * (120 * 1024 + 1)),
            ('synthesize', 'b' * (120 * 1024 + 1)),
            ('draft', '中' * (41 * 1024)),
        ):
            with self.subTest(stage=stage, char=content[0]):
                out = self.base / f'oversize-{stage}-{content[0]}'
                path = self.request if stage == 'draft' else self.claude_draft
                path.write_text(content, encoding='utf-8')
                args = self.synthesize_args() if stage == 'synthesize' else []
                result = self.invoke(stage=stage, out=out, extra_args=args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertRegex(result.stderr, r'prompt.* \([0-9]+ bytes\).*122880')
                self.assertIn('Claude', result.stderr)
                self.assertFalse(out.exists())
                self.assert_no_cli()

    def test_execute_e2big_is_nonfallback_with_terminal_evidence(self):
        self.write_config(on_failure='fallback')
        mod = load_agy_plan_module()
        execute = mod.execute

        def e2big(argv, *args, **kwargs):
            if argv[1:] == ['--version']:
                return execute(argv, *args, **kwargs)
            raise RunError('could not launch CLI') from OSError(errno.E2BIG, 'Argument list too long')

        with mock.patch.object(mod, 'execute', side_effect=e2big):
            self.assert_terminal(self.invoke_main(mod), 2, 'failed', 'prompt_too_large')
        self.assertFalse((self.out / '.staging').exists())

    def test_staging_cleanup_failure_after_success_or_failure_is_integrity_abort(self):
        self.write_config(on_failure='fallback')
        mod = load_agy_plan_module()
        for mode in ('ok', 'nonzero'):
            with self.subTest(mode=mode):
                self.out = self.base / mode
                self.addCleanup(mod.remove_tree_readonly_safe, self.out / '.staging')
                self.env['FIXTURE_MODE'] = mode
                with mock.patch.object(mod, 'remove_tree_readonly_safe',
                                       side_effect=PermissionError('fixture cleanup denied')) as cleanup:
                    result = self.invoke_main(mod)
                manifest = self.assert_terminal(result, 3, 'integrity_aborted', 'staging_cleanup_failed')
                cleanup.assert_called_once_with(self.out / '.staging')
                self.assertIn('fixture cleanup denied', manifest['error'])
                if mode == 'nonzero':
                    self.assertEqual(manifest['original_error']['error'], 'nonzero AGY CLI exit: 1')
                    self.assertEqual(manifest['original_error']['error_kind'], 'execution_failed')
                else:
                    self.assertNotIn('original_error', manifest)

    def test_preparation_rollback_cleanup_preserves_original_error(self):
        mod = load_agy_plan_module()
        with mock.patch.object(mod, 'prepare_staging', side_effect=ConfigError('original staging error')), \
             mock.patch.object(mod, 'remove_tree_readonly_safe',
                               side_effect=PermissionError('fixture cleanup denied')) as cleanup:
            result = self.invoke_main(mod)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)['error'], 'original staging error')
        self.assertIn('fixture cleanup denied', result.stderr)
        self.assertEqual(cleanup.call_args_list, [mock.call(self.out / '.staging'), mock.call(self.out)])
        self.assert_no_cli()

    def test_git_archive_can_finish_normally_after_tar_output_closes(self):
        git = self.bin_dir / 'git'
        real_git = shutil.which('git')
        marker = self.base / 'archive-finished'
        git.write_text(
            f'#!{sys.executable}\n'
            'import os, pathlib, subprocess, sys, time\n'
            f'command = [{real_git!r}, *sys.argv[1:]]\n'
            'if sys.argv[3] != "archive":\n'
            '    os.execv(command[0], command)\n'
            'code = subprocess.call(command)\n'
            'os.close(1)\n'
            'os.close(2)\n'
            'time.sleep(0.4)\n'
            f'pathlib.Path({str(marker)!r}).touch()\n'
            'sys.exit(code)\n',
            encoding='utf-8',
        )
        git.chmod(0o700)
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(marker.is_file())
        self.assertEqual(self.load_manifest()['status'], 'completed')

    def test_repository_paths_are_redacted_from_real_argv_without_changing_materials(self):
        self.write_config(synthesizer='agy')
        logical = self.base / 'logical-repo'
        logical.symlink_to(self.repo, target_is_directory=True)
        materials = {
            'request': (self.request, f'Open {self.repo}.\n-I{logical}/include\n'),
            'claude_draft': (self.claude_draft, f'Read file://{self.repo}/sub/file.\n'),
            'agy_draft': (self.agy_draft, f'Compare {logical}-other with {self.repo}/child.\n'),
        }
        appendix = f'## 附录：非需求\nIgnored {logical}/appendix.\n'
        for source, (path, content) in materials.items():
            path.write_bytes((content + (appendix if source == 'request' else '')).encode('utf-8'))
        pristine = {source: path.read_bytes() for source, (path, _) in materials.items()}
        for stage, count in (('draft', 2), ('synthesize', 3)):
            with self.subTest(stage=stage):
                out = self.base / stage
                args = self.synthesize_args() if stage == 'synthesize' else []
                result = self.invoke(repo=logical, stage=stage, out=out, extra_args=args)
                self.assertEqual(result.returncode, 0, result.stderr)
                argv = json.loads(self.calls.read_text(encoding='utf-8').splitlines()[-1])['argv']
                arg = next(item for item in argv if item.startswith('--print='))
                self.assertNotIn(str(self.repo), arg)
                self.assertNotIn(str(logical), arg)
                self.assertIn('Open <REPO_ROOT>.', arg)
                self.assertIn('-I<REPO_ROOT>/include', arg)
                warnings = [{'kind': 'repo_path_redacted', 'source': 'request', 'count': count}]
                request_bytes = materials['request'][1].encode('utf-8')
                if stage == 'synthesize':
                    self.assertIn('file://<REPO_ROOT>/sub/file', arg)
                    self.assertIn('<REPO_ROOT>-other', arg)
                    self.assertIn('<REPO_ROOT>/child', arg)
                    warnings.extend([
                        {'kind': 'repo_path_redacted', 'source': 'claude_draft', 'count': 1},
                        {'kind': 'repo_path_redacted', 'source': 'agy_draft', 'count': 2},
                    ])
                    request_bytes = pristine['request']
                manifest = self.load_manifest(out)
                self.assertEqual(manifest['warnings'], warnings)
                hashes = manifest['input_sha256']
                self.assertEqual(hashes['request_user'], hashlib.sha256(request_bytes).hexdigest())
                for source in ('claude_draft', 'agy_draft'):
                    expected = hashlib.sha256(pristine[source]).hexdigest() if stage == 'synthesize' else None
                    self.assertEqual(hashes[source], expected)
                payload = arg[len('--print='):].encode('utf-8')
                self.assertEqual((out / 'prompt.txt').read_bytes(), payload)
                self.assertEqual(manifest['prompt_sha256'], hashlib.sha256(payload).hexdigest())
                self.assertEqual(manifest['prompt_bytes'], len(payload))
                self.assertEqual(manifest['limits']['max_prompt_bytes'], 122880)
                for source, (path, _) in materials.items():
                    self.assertEqual(path.read_bytes(), pristine[source])

    def test_instructions_and_residual_repository_path_are_rejected_before_cli(self):
        logical = self.base / 'logical-repo'
        logical.symlink_to(self.repo, target_is_directory=True)
        for path in (self.repo, logical):
            with self.subTest(path=path):
                out = self.base / f'instructions-{path.name}'
                self.instructions.write_text(f'Use -I{path}/include.', encoding='utf-8')
                result = self.invoke(repo=logical, out=out,
                                     extra_args=['--instructions', str(self.instructions)])
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('--instructions', result.stderr)
                self.assertFalse(out.exists())
                self.assert_no_cli()
        mod = load_agy_plan_module()
        with mock.patch.object(mod, 'assemble_prompt', return_value=f'Open {self.repo}.'):
            result = self.invoke_main(mod)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('repository absolute path', result.stderr)
        self.assertFalse(self.out.exists())
        self.assert_no_cli()

    def interrupt_wrapper(self, signum, *, write_tool=False):
        self.write_config(on_failure='fallback')
        env = dict(self.env, FIXTURE_MODE='block')
        if write_tool:
            env['FIXTURE_WRITE_TOOL'] = 'write_file'
        ready = self.calls.with_suffix('.jsonl.ready')
        with subprocess.Popen(self.command(), env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            pids = None
            try:
                deadline = time.monotonic() + 10
                while not pids and process.poll() is None and time.monotonic() < deadline:
                    if ready.exists():
                        data = ready.read_text(encoding='utf-8')
                        if data:
                            pids = json.loads(data)
                    if not pids:
                        time.sleep(0.01)
                self.assertIsNotNone(pids, 'fake AGY did not reach the blocking stage')
                process.send_signal(signum)
                stdout, stderr = process.communicate(timeout=8)
                for pid in pids:
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
            finally:
                if process.poll() is None:
                    process.kill()
                if pids:
                    try:
                        os.killpg(pids[0], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        return subprocess.CompletedProcess([], process.returncode, stdout, stderr)

    def test_wrapper_signals_stop_process_group_and_forbid_fallback(self):
        for signum, code in ((signal.SIGTERM, 143), (signal.SIGINT, 130)):
            with self.subTest(signum=signum):
                self.out = self.base / str(code)
                self.calls.with_suffix('.jsonl.ready').unlink(missing_ok=True)
                result = self.interrupt_wrapper(signum)
                self.assert_terminal(result, code, 'interrupted', 'interrupted')
                self.assertFalse((self.out / '.staging').exists())

    def test_write_tool_takes_priority_over_interrupt(self):
        result = self.interrupt_wrapper(signal.SIGTERM, write_tool=True)
        manifest = self.assert_terminal(result, 3, 'integrity_aborted', 'integrity_violation')
        self.assertIn('write_file', manifest['error'])
        self.assertFalse((self.out / '.staging').exists())

    def test_pre_output_signals_are_interrupted_and_restore_handlers(self):
        mod = load_agy_plan_module()
        for signum, code in ((signal.SIGINT, 130), (signal.SIGTERM, 143)):
            with self.subTest(signum=signum):
                before = signal.getsignal(signum)
                with mock.patch.object(mod, 'load', side_effect=lambda _: os.kill(os.getpid(), signum)):
                    result = self.invoke_main(mod)
                self.assertEqual(signal.getsignal(signum), before)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual(json.loads(result.stdout)['status'], 'interrupted')
                self.assertFalse(self.out.exists())
                self.assert_no_cli()

    def test_child_sigterm_without_wrapper_signal_forbids_fallback(self):
        self.write_config(on_failure='fallback')
        result = self.invoke(extra_env={'FIXTURE_MODE': 'child_sigterm'})
        manifest = self.assert_terminal(result, 143, 'interrupted', 'interrupted')
        self.assertEqual(manifest['cli_exit_code'], -signal.SIGTERM)

    def test_wrapper_signal_takes_priority_over_runner_log_limit(self):
        self.write_config(on_failure='fallback')
        mod = load_agy_plan_module()
        execute = mod.execute

        def log_limit(argv, *args, **kwargs):
            if argv[1:] == ['--version']:
                return execute(argv, *args, **kwargs)
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except KeyboardInterrupt:
                raise RunError('log limit')

        with mock.patch.object(mod, 'execute', side_effect=log_limit):
            self.assert_terminal(self.invoke_main(mod), 143, 'interrupted', 'interrupted')

    def test_interrupt_during_log_scan_integrity_check_or_plan_write_has_terminal_evidence(self):
        mod = load_agy_plan_module()
        for phase in ('scan', 'integrity', 'write'):
            with self.subTest(phase=phase):
                self.out = self.base / phase
                capture = mod.capture_integrity_state
                write_bytes = Path.write_bytes
                write_text = Path.write_text

                def post_integrity(*args, **kwargs):
                    if (self.out / 'manifest.json').exists():
                        raise KeyboardInterrupt('post-integrity interrupted')
                    return capture(*args, **kwargs)

                def partial_write_bytes(path, data):
                    if path.name in ('plan.md', '.plan.md.tmp'):
                        write_bytes(path, data[:5])
                        raise KeyboardInterrupt('plan write interrupted')
                    return write_bytes(path, data)

                def partial_write_text(path, data, *args, **kwargs):
                    if path.name == 'plan.md':
                        write_text(path, data[:5], *args, **kwargs)
                        raise KeyboardInterrupt('plan write interrupted')
                    return write_text(path, data, *args, **kwargs)

                if phase == 'scan':
                    with mock.patch.object(mod, 'scan_log_for_write_tools', side_effect=KeyboardInterrupt):
                        result = self.invoke_main(mod)
                elif phase == 'integrity':
                    with mock.patch.object(mod, 'capture_integrity_state', side_effect=post_integrity):
                        result = self.invoke_main(mod)
                else:
                    with mock.patch.object(Path, 'write_bytes', partial_write_bytes), \
                         mock.patch.object(Path, 'write_text', partial_write_text):
                        result = self.invoke_main(mod)
                if phase == 'write':
                    self.assert_terminal(result, 130, 'interrupted', 'interrupted')
                else:
                    self.assert_terminal(result, 3, 'integrity_aborted', 'integrity_violation')
                self.assertFalse((self.out / '.staging').exists())

    def test_draft_success_strips_r2_appendix_handles_leading_dash_and_hashes_evidence(self):
        self.write_config(model='agy-plan-model', effort='high')
        result = self.invoke(stage='draft', extra_args=['--instructions', str(self.instructions)])
        self.assertEqual(result.returncode, 0, result.stderr)

        summary = json.loads(result.stdout.strip())
        self.assertEqual(summary['exit_code'], 0)
        self.assertTrue(summary['completed'])
        self.assertTrue(summary['integrity_ok'])
        self.assertFalse(summary['fallback_allowed'])

        manifest = self.load_manifest()
        self.assertEqual(manifest['provider'], 'agy')
        self.assertEqual(manifest['stage'], 'draft')
        self.assertEqual(manifest['requested_model'], 'agy-plan-model')
        self.assertEqual(manifest['requested_effort'], 'high')
        self.assertEqual(manifest['model_evidence'], 'requested_only')
        self.assertEqual(manifest['cli_version'], '1.5.13')
        self.assertEqual(manifest['conversation_id'], 'conv-fixture-001')
        self.assertEqual(manifest['head'], self.git('rev-parse', 'HEAD'))
        self.assertEqual(manifest['warnings'], [])
        self.assertEqual(manifest['tool_warnings'], [])
        self.assertEqual(manifest['plan_file'], 'plan.md')
        self.assertFalse((self.out / '.staging').exists())

        prompt_text = (self.out / 'prompt.txt').read_text(encoding='utf-8')
        self.assertIn('- Requirement starting with dash', prompt_text)
        self.assertIn('single-user local CLI', prompt_text)
        self.assertNotIn('## 附录：非需求', prompt_text)
        self.assertNotIn('Secret Claude inference', prompt_text)
        self.assertNotIn(str(self.repo), prompt_text)

        expected_user_req = '- Requirement starting with dash\nPlease add dual-plan support.\n\n'
        self.assertEqual(
            manifest['input_sha256']['request_user'],
            hashlib.sha256(expected_user_req.encode('utf-8')).hexdigest(),
        )
        self.assertIsNone(manifest['input_sha256']['claude_draft'])
        self.assertIsNone(manifest['input_sha256']['agy_draft'])
        self.assertEqual(manifest['prompt_sha256'], hashlib.sha256((self.out / 'prompt.txt').read_bytes()).hexdigest())
        self.assertEqual(manifest['plan_sha256'], hashlib.sha256((self.out / 'plan.md').read_bytes()).hexdigest())
        self.assertEqual(manifest['log_sha256'], hashlib.sha256((self.out / 'plan.jsonl').read_bytes()).hexdigest())
        self.assertEqual(manifest['stderr_sha256'], hashlib.sha256((self.out / 'plan.stderr.txt').read_bytes()).hexdigest())

        calls = [json.loads(line) for line in self.calls.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(calls), 1)
        argv = calls[0]['argv']
        self.assertEqual(
            argv[:-1],
            [
                '--mode', 'plan',
                '--sandbox',
                '--output-format', 'stream-json',
                '--print-timeout', '15s',
                '--model', 'agy-plan-model',
                '--effort', 'high',
            ],
        )
        self.assertTrue(argv[-1].startswith('--print='))
        self.assertEqual(argv[-1][len('--print='):], prompt_text)
        self.assertNotIn('--disable-slash-commands', argv)
        self.assertNotEqual(calls[0]['cwd'], str(self.repo))

    def test_synthesize_success_inlines_three_materials_and_hashes_evidence(self):
        self.write_config(synthesizer='agy')
        result = self.invoke(
            stage='synthesize',
            extra_args=[
                '--claude-draft', str(self.claude_draft),
                '--agy-draft', str(self.agy_draft),
                '--instructions', str(self.instructions),
            ],
            extra_env={'FIXTURE_RESPONSE': '# Synthesized Plan\n\nMerged A and B.'},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = self.load_manifest()
        self.assertEqual(manifest['stage'], 'synthesize')
        self.assertEqual(
            manifest['input_sha256']['request_user'],
            hashlib.sha256(self.request.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            manifest['input_sha256']['claude_draft'],
            hashlib.sha256(self.claude_draft.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            manifest['input_sha256']['agy_draft'],
            hashlib.sha256(self.agy_draft.read_bytes()).hexdigest(),
        )
        prompt_text = (self.out / 'prompt.txt').read_text(encoding='utf-8')
        self.assertIn('<<<BEGIN_USER_REQUEST>>>', prompt_text)
        self.assertIn('<<<BEGIN_CLAUDE_DRAFT>>>', prompt_text)
        self.assertIn('Constraint A: keep config strict.', prompt_text)
        self.assertIn('<<<BEGIN_AGY_DRAFT>>>', prompt_text)
        self.assertIn('Constraint B: isolate snapshot.', prompt_text)
        self.assertIn('Merged A and B.', (self.out / 'plan.md').read_text(encoding='utf-8'))

    def test_isolated_readonly_staging_protects_dirty_tracked_ignored_request_and_extra_file(self):
        # Dirty tracked modification, tracked deletion, staged new file, ignored huge artifact, extra file
        (self.repo / 'app.py').write_text('def greet():\n    return "v2-dirty"\n', encoding='utf-8')
        (self.repo / 'deleted.txt').unlink()
        (self.repo / 'added.py').write_text('ADDED = True\n', encoding='utf-8')
        self.git('add', 'added.py')
        (self.repo / 'extra.txt').write_text('untracked extra content\n', encoding='utf-8')
        build_dir = self.repo / 'build'
        build_dir.mkdir()
        (build_dir / 'huge.bin').write_bytes(b'X' * 50000)

        before_app = (self.repo / 'app.py').read_bytes()
        before_added = (self.repo / 'added.py').read_bytes()
        before_extra = (self.repo / 'extra.txt').read_bytes()
        before_req = self.request.read_bytes()

        # Cap smaller than build/huge.bin to prove .gitignore build/ is excluded from snapshot
        result = self.invoke(
            stage='draft',
            extra_args=['--extra-file', 'extra.txt', '--max-snapshot-bytes', '4096'],
            extra_env={'FIXTURE_CHECK_READONLY': '1'},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        calls = [json.loads(line) for line in self.calls.read_text(encoding='utf-8').splitlines()]
        rec = calls[0]
        self.assertEqual(rec['app_py'], 'def greet():\n    return "v2-dirty"\n')
        self.assertEqual(rec['added_py'], 'ADDED = True\n')
        self.assertEqual(rec['extra_txt'], 'untracked extra content\n')
        self.assertNotIn('deleted.txt', rec['files'])
        self.assertNotIn('build/huge.bin', rec['files'])
        self.assertNotIn('.claude/plans/20261120-demo.request.md', rec['files'])
        self.assertTrue(rec['write_blocked'])
        self.assertTrue(rec['create_blocked'])

        # Real originals and ignored request are byte-for-byte unchanged
        self.assertEqual((self.repo / 'app.py').read_bytes(), before_app)
        self.assertEqual((self.repo / 'added.py').read_bytes(), before_added)
        self.assertEqual((self.repo / 'extra.txt').read_bytes(), before_extra)
        self.assertEqual(self.request.read_bytes(), before_req)
        self.assertFalse((self.repo / 'deleted.txt').exists())

    def test_snapshot_size_limit_exceeded_exits_2_without_out_or_cli(self):
        result = self.invoke(stage='draft', extra_args=['--max-snapshot-bytes', '10'])
        self.assertEqual(result.returncode, 2)
        summary = json.loads(result.stdout.strip())
        self.assertFalse(summary['fallback_allowed'])
        self.assertEqual(summary['exit_code'], 2)
        self.assertIn('max-snapshot-bytes', result.stderr)
        self.assertFalse(self.out.exists())
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.calls.with_suffix('.jsonl.version').exists())

    def test_precondition_and_config_errors_exit_2_without_launching_cli(self):
        # 1. enabled=False
        self.write_config(enabled=False)
        r = self.invoke()
        self.assertEqual(r.returncode, 2)
        self.assertFalse(json.loads(r.stdout.strip())['fallback_allowed'])
        self.assertFalse(self.out.exists())
        self.assertFalse(self.calls.exists())

        # 2. Invalid config field
        self.config.write_text(json.dumps({'claude_relay': {'dual_plan': {'enabled': True, 'effort': 'ultra'}}}))
        r = self.invoke()
        self.assertEqual(r.returncode, 2)
        self.assertFalse(self.calls.exists())

        # 3. synthesize when synthesizer='claude'
        self.write_config(enabled=True, synthesizer='claude')
        r = self.invoke(
            stage='synthesize',
            extra_args=['--claude-draft', str(self.claude_draft), '--agy-draft', str(self.agy_draft)],
        )
        self.assertEqual(r.returncode, 2)
        self.assertFalse(self.calls.exists())

        # 4. synthesize missing or empty drafts
        self.write_config(enabled=True, synthesizer='agy')
        r = self.invoke(stage='synthesize', extra_args=['--claude-draft', str(self.claude_draft)])
        self.assertEqual(r.returncode, 2)
        empty_draft = self.base / 'empty.md'
        empty_draft.write_text('   \n', encoding='utf-8')
        r = self.invoke(
            stage='synthesize',
            extra_args=['--claude-draft', str(empty_draft), '--agy-draft', str(self.agy_draft)],
        )
        self.assertEqual(r.returncode, 2)

        # 5. draft forbids --claude-draft
        r = self.invoke(stage='draft', extra_args=['--claude-draft', str(self.claude_draft)])
        self.assertEqual(r.returncode, 2)

        # 6. Appendix-only request rejected in draft
        only_appendix = self.base / 'only-appendix.request.md'
        only_appendix.write_text('## 附录：非需求（主模型推断，可质疑）\n- only inference\n', encoding='utf-8')
        r = self.invoke(stage='draft', request=only_appendix)
        self.assertEqual(r.returncode, 2)
        self.assertIn('R2 appendix', r.stderr)

        # 7. Unusable --out (already exists / inside repo)
        existing = self.base / 'existing'
        existing.mkdir()
        r = self.invoke(out=existing)
        self.assertEqual(r.returncode, 2)
        r = self.invoke(out=self.repo / 'nested-out')
        self.assertEqual(r.returncode, 2)

        # 8. --extra-file traversal and symlink rejected
        r = self.invoke(extra_args=['--extra-file', '../config.json'])
        self.assertEqual(r.returncode, 2)
        sym = self.repo / 'sym.txt'
        sym.symlink_to(self.config)
        self.addCleanup(sym.unlink)
        r = self.invoke(extra_args=['--extra-file', 'sym.txt'])
        self.assertEqual(r.returncode, 2)

        # 9. Non-git repo rejected even with inherited GIT_DIR pointing at real repo
        nongit = self.base / 'nongit'
        nongit.mkdir()
        r = self.invoke(repo=nongit, extra_env={'GIT_DIR': str(self.repo / '.git')})
        self.assertEqual(r.returncode, 2)
        self.assertFalse(self.calls.exists())

    def test_leading_slash_prompt_rejected_with_exit_2(self):
        mod = load_agy_plan_module()
        with self.assertRaisesRegex(ConfigError, 'start with /'):
            mod.validate_prompt('/plan injected')
        with self.assertRaisesRegex(ConfigError, 'start with /'):
            mod.validate_prompt('   /plan injected')
        with self.assertRaisesRegex(ConfigError, 'NUL'):
            mod.validate_prompt('ok\0bad')

        parser = mod.build_parser()
        args = parser.parse_args([
            '--repo', str(self.repo),
            '--stage', 'draft',
            '--request', str(self.request),
            '--out', str(self.out),
            '--config', str(self.config),
        ])
        with mock.patch.object(mod, 'assemble_prompt', side_effect=lambda *a, **k: mod.validate_prompt('/slash')):
            with self.assertRaisesRegex(ConfigError, 'start with /'):
                mod.run(args)
        self.assertFalse(self.out.exists())
        self.assertFalse(self.calls.exists())

    def test_write_tool_in_stream_aborts_with_exit_3_even_on_fallback(self):
        self.write_config(on_failure='fallback')
        for idx, (tool, mode) in enumerate([
            ('write_file', 'ok'),
            ('edit_file', 'ok'),
            ('replace_file_content', 'ok'),
            ('CORTEX_STEP_TYPE_WRITE_TO_FILE', 'ok'),
            ('delete_knowledge', 'write_then_nonzero'),
        ]):
            out = self.base / f'out-write-{idx}'
            with self.subTest(tool=tool, mode=mode):
                result = self.invoke(
                    out=out,
                    extra_env={'FIXTURE_WRITE_TOOL': tool, 'FIXTURE_MODE': mode},
                )
                self.assertEqual(result.returncode, 3, result.stderr)
                manifest = self.load_manifest(out)
                self.assertFalse(manifest['completed'])
                self.assertFalse(manifest['integrity_ok'])
                self.assertFalse(manifest['fallback_allowed'])
                self.assertEqual(manifest['exit_code'], 3)
                self.assertEqual(manifest['error_kind'], 'integrity_violation')
                self.assertFalse((out / 'plan.md').exists())

    def test_mutated_real_repo_or_ignored_request_aborts_with_exit_3_even_on_fallback(self):
        self.write_config(on_failure='fallback')

        # Mutate ignored .request.md during execution
        out1 = self.base / 'out-mutate-req'
        r1 = self.invoke(out=out1, extra_env={'FIXTURE_MUTATE_REAL_FILE': str(self.request)})
        self.assertEqual(r1.returncode, 3, r1.stderr)
        m1 = self.load_manifest(out1)
        self.assertFalse(m1['integrity_ok'])
        self.assertFalse(m1['fallback_allowed'])
        self.assertFalse((out1 / 'plan.md').exists())

        # Create unexpected untracked file in real repo during execution
        out2 = self.base / 'out-mutate-status'
        r2 = self.invoke(out=out2, extra_env={'FIXTURE_CREATE_UNTRACKED_IN': str(self.repo)})
        self.assertEqual(r2.returncode, 3, r2.stderr)
        m2 = self.load_manifest(out2)
        self.assertFalse(m2['integrity_ok'])
        self.assertFalse(m2['fallback_allowed'])
        self.assertFalse((out2 / 'plan.md').exists())

    def test_mutated_staging_dir_aborts_with_exit_3_even_on_fallback(self):
        self.write_config(on_failure='fallback')
        result = self.invoke(extra_env={'FIXTURE_MUTATE_STAGING': '1'})
        self.assertEqual(result.returncode, 3, result.stderr)
        manifest = self.load_manifest()
        self.assertFalse(manifest['integrity_ok'])
        self.assertFalse(manifest['fallback_allowed'])
        self.assertEqual(manifest['exit_code'], 3)
        self.assertIn('staging', manifest['error'])
        self.assertFalse((self.out / 'plan.md').exists())
        self.assertFalse((self.out / '.staging').exists())

    def test_stop_group_cleanup_failure_aborts_with_exit_3_even_on_fallback(self):
        self.write_config(on_failure='fallback')
        mod = load_agy_plan_module()
        parser = mod.build_parser()
        args = parser.parse_args([
            '--repo', str(self.repo),
            '--stage', 'draft',
            '--request', str(self.request),
            '--out', str(self.out),
            '--config', str(self.config),
            '--timeout', '10',
            '--idle-timeout', '3',
        ])

        real_execute = mod.execute

        def flaky_execute(argv, *a, **k):
            if argv[1:] == ['--version']:
                return real_execute(argv, *a, **k)
            raise RunError('process group cleanup denied by host permissions')

        with mock.patch.dict(os.environ, self.env, clear=False), \
             mock.patch.object(mod, 'execute', side_effect=flaky_execute):
            evidence = mod.run(args)

        self.assertEqual(evidence['exit_code'], 3)
        self.assertFalse(evidence['completed'])
        self.assertFalse(evidence['integrity_ok'])
        self.assertFalse(evidence['fallback_allowed'])
        self.assertEqual(evidence['error_kind'], 'integrity_violation')
        self.assertIn('process group cleanup', evidence['error'])
        self.assertFalse((self.out / 'plan.md').exists())
        self.assertFalse((self.out / '.staging').exists())
        self.load_manifest()

    def test_ordinary_runtime_failures_exit_1_with_fallback_allowed(self):
        cases = [
            ('nonzero', {'FIXTURE_MODE': 'nonzero'}),
            ('bad_perm', {'FIXTURE_PERM_MODE': 'auto-approve'}),
            ('no_plan_cmd', {'FIXTURE_EXPANDED_COMMANDS': '[]'}),
            ('bad_status', {'FIXTURE_MODE': 'bad_status'}),
            ('empty_resp', {'FIXTURE_MODE': 'empty_response'}),
            ('trailing', {'FIXTURE_MODE': 'trailing_event'}),
            ('version_fail', {'FIXTURE_AGY_VERSION_FAIL': '1'}),
            ('no_cli', {'PATH': '/usr/bin:/bin'}),
        ]
        for label, extra_env in cases:
            out = self.base / f'out-fail-{label}'
            with self.subTest(case=label):
                result = self.invoke(out=out, extra_env=extra_env)
                self.assertEqual(result.returncode, 1, result.stderr)
                summary = json.loads(result.stdout.strip())
                self.assertEqual(summary['exit_code'], 1)
                self.assertFalse(summary['completed'])
                self.assertTrue(summary['integrity_ok'])
                self.assertTrue(summary['fallback_allowed'])
                manifest = self.load_manifest(out)
                self.assertTrue(manifest['fallback_allowed'])
                self.assertTrue(manifest['integrity_ok'])
                self.assertFalse((out / 'plan.md').exists())
                self.assertFalse((out / '.staging').exists())

    def test_untested_version_non_empty_stderr_and_tool_warnings_do_not_block(self):
        result = self.invoke(
            extra_env={
                'FIXTURE_AGY_VERSION': 'agy 9.9.9',
                'FIXTURE_STDERR': 'some benign diagnostic line',
                'FIXTURE_TOOL_WARNING': '1',
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = self.load_manifest()
        self.assertTrue(manifest['completed'])
        self.assertEqual(manifest['cli_version'], '9.9.9')
        warning_kinds = [w['kind'] for w in manifest['warnings']]
        self.assertIn('untested_cli_version', warning_kinds)
        self.assertIn('non_empty_stderr', warning_kinds)
        self.assertEqual(len(manifest['tool_warnings']), 1)
        self.assertEqual(manifest['tool_warnings'][0]['tool'], 'read_file')
        self.assertEqual(manifest['tool_warnings'][0]['status'], 'DENIED')


if __name__ == '__main__':
    unittest.main()
