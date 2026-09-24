"""Black-box contract tests; subprocess fixture is not a live reviewer."""
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

ROOT = Path(__file__).resolve().parents[3]
ENTRY = ROOT / 'adapters/codex/skills/claude-review/scripts/review.py'
FIXTURE = Path(__file__).with_name('fake_claude.py')


class ThinReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='thin review ')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / 'business repo'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Fixture')
        self.write('app.py', 'def divide(a, b):\n    return a / b\n')
        self.write('deleted.py', 'removed = True\n')
        self.write('context.md', 'Project context\n')
        self.git('add', 'app.py', 'deleted.py', 'context.md')
        self.git('commit', '-qm', 'fixture baseline')
        self.write('app.py', 'def divide(a, b):\n    return a // b\n')
        self.plan = self.base / 'task.md'
        self.plan.write_text('# Plan\nHandle zero denominator in app.py.\n')
        self.request = self.base / 'task.request.md'
        self.request.write_text('# Request\nReject zero denominator.\nKeep division precision.\n')
        self.bindir = self.base / 'bin'
        self.bindir.mkdir()
        shutil.copyfile(FIXTURE, self.bindir / 'claude')
        (self.bindir / 'claude').chmod(0o700)
        self.audit = self.base / 'audit.jsonl'
        self.heartbeat = self.base / 'heartbeat'
        # Isolated HOME: the user-level ai-anti config is never read from the real home.
        self.home = self.base / 'home'
        self.home.mkdir()
        self.config = self.home / '.config' / 'ai-anti' / 'config.json'
        self.env = dict(os.environ, PATH=str(self.bindir) + os.pathsep + os.environ['PATH'],
                        FAKE_AUDIT=str(self.audit), FAKE_HEARTBEAT=str(self.heartbeat),
                        PYTHONDONTWRITEBYTECODE='1', HOME=str(self.home))
        self.env.pop('FAKE_VERSION', None)
        self.serial = 0

    def tearDown(self):
        pidfile = Path(str(self.heartbeat) + ".pid")
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True,
                              capture_output=True, text=True).stdout

    def write(self, path, text):
        p = self.repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def write_config(self, value):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(value if isinstance(value, str) else json.dumps(value))

    def run_review(self, mode='diff', files=None, extra=(), fake='success', entry=ENTRY, out=None,
                   actual_model=None, plugins=None, env_extra=None):
        self.serial += 1
        out = out or self.base / ('run-' + str(self.serial))
        args = [sys.executable, str(entry), mode, '--repo', str(self.repo),
                '--files', *(files or ['app.py']), '--scale', 'single user local fixture', '--out', str(out)]
        if mode == 'plan':
            args += ['--plan', str(self.plan)]
        args += list(extra)
        env = dict(self.env, FAKE_MODE=fake)
        env.pop('FAKE_ACTUAL_MODEL', None)
        if actual_model is not None:
            env['FAKE_ACTUAL_MODEL'] = actual_model
        env.pop('FAKE_PLUGINS', None)
        if plugins is not None:
            env['FAKE_PLUGINS'] = json.dumps(plugins)
        env.update(env_extra or {})
        result = subprocess.run(args, cwd=self.base, env=env, text=True, capture_output=True, timeout=30)
        return result, out

    def manifest(self, out):
        return json.loads((out / 'manifest.json').read_text())

    def calls(self):
        return [json.loads(s) for s in self.audit.read_text().splitlines()] if self.audit.exists() else []

    def assert_success(self, result, out, status='completed'):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads((out / 'status.json').read_text())['status'], status)
        self.assertNotIn('approved', (out / 'status.json').read_text().lower())

    def test_plan_two_phases_and_shared_rules(self):
        r, out = self.run_review('plan')
        self.assert_success(r, out)
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        first = '\n'.join(calls[0]['files'].values())
        second = '\n'.join(calls[1]['files'].values())
        self.assertNotIn('Handle zero denominator', first)
        self.assertIn('Handle zero denominator', second)
        self.assertIn('Reject zero denominator', first)
        self.assertIn('FAKE REPORT', second)
        report = (out / 'report.md').read_text()
        self.assertEqual(report.count('FAKE REPORT'), 2)
        core = (ROOT / 'core/REVIEW_PRINCIPLES.md').read_text()
        for call in calls:
            prompt = call['argv'][call['argv'].index('-p') + 1]
            self.assertIn('主模型负责方案、裁决、需求核对、终审', prompt)
            self.assertIn('有没有新增整包依赖或首访即加载的大块？', prompt)
            self.assertEqual(call['stdin'], '')
            self.assertEqual(call['argv'][call['argv'].index('--tools') + 1], 'Read,Glob,Grep')
            self.assertNotIn('--add-dir', call['argv'])
            self.assertIn('--safe-mode', call['argv'])
        self.assertIn('## R1｜角色、边界与第二意见', core)
        # Full path inventory exposes a file not selected as content.
        self.assertIn('deleted.py', first)
        self.assertNotIn('removed = True', first)

    def test_diff_staged_untracked_deleted_and_other_session(self):
        self.git('add', 'app.py')
        self.write('new file.py', 'untracked_full_content = 42\n')
        (self.repo / 'deleted.py').unlink()
        self.write('other.py', 'OTHER_SESSION_CONTENT\n')
        r, out = self.run_review(files=['app.py', 'new file.py', 'deleted.py'])
        self.assert_success(r, out)
        self.assertEqual(len(self.calls()), 1)
        material = '\n'.join(self.calls()[0]['files'].values())
        self.assertIn('untracked_full_content = 42', material)
        self.assertIn('removed = True', material)
        self.assertIn('return a // b', material)
        self.assertIn('other.py', material)
        self.assertNotIn('OTHER_SESSION_CONTENT', material)

    def test_prepare_only_no_model_and_directory_exclusive(self):
        r, out = self.run_review(extra=['--prepare-only'])
        self.assert_success(r, out, 'prepared')
        self.assertFalse(self.calls())
        prompt = (out / "logs/review.prompt.txt").read_text()
        self.assertIn("## R5｜增量（diff）评审", prompt)
        self.assertEqual(out.stat().st_mode & 0o777, 0o700)
        r2, _ = self.run_review(extra=['--prepare-only'], out=out)
        self.assertNotEqual(r2.returncode, 0)
        self.assertEqual(json.loads((out / 'status.json').read_text())['status'], 'prepared')

    def test_fable_model_selection(self):
        r, out = self.run_review(extra=['--model', 'fable'])
        self.assert_success(r, out)
        argv = self.calls()[0]['argv']
        self.assertEqual(argv[argv.index('--model') + 1], 'fable')
        self.assertEqual(self.manifest(out)['actual_model'], 'claude-fable-5-1')

    def test_same_family_versions_accepted_and_actual_recorded(self):
        cases = (('opus', 'claude-opus-5'), ('opus', 'claude-opus-5-5'), ('opus', 'claude-opus-6'),
                 ('opus', 'claude-opus-5-5-20261001'), ('fable', 'claude-fable-5'),
                 ('fable', 'claude-fable-5-1'), ('fable', 'claude-fable-5-2'))
        for alias, actual in cases:
            with self.subTest(alias=alias, actual=actual):
                r, out = self.run_review(extra=['--model', alias], actual_model=actual)
                self.assert_success(r, out)
                manifest = self.manifest(out)
                self.assertEqual(manifest['requested_model'], alias)
                self.assertEqual(manifest['actual_model'], actual)
                self.assertEqual(manifest['stages']['review']['model'], actual)

    def test_actual_model_mismatch_fails_closed_without_prefix_match(self):
        cases = (('fable', 'claude-fable-5-1-extra'), ('fable', 'claude-opus-5'),
                 ('opus', 'claude-fable-5-1'), ('opus', 'claude-sonnet-5'), ('opus', 'claude-opus-'),
                 ('opus', 'claude-opus-5x'), ('opus', 'claude-opus-5-5[1m]'),
                 ('opus', 'CLAUDE-OPUS-5'), ('opus', 'claude-opus-５-５'))
        for alias, actual in cases:
            with self.subTest(alias=alias, actual=actual):
                r, out = self.run_review(extra=['--model', alias], actual_model=actual)
                self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
                status = json.loads((out / 'status.json').read_text())
                self.assertEqual(status['status'], 'failed')
                self.assertIn('unexpected actual model', status['error'])
                self.assertFalse((out / 'report.md').exists())

    def test_builtin_plugins_whitelist_accepted(self):
        agents = {'name': 'agents-md', 'path': 'builtin', 'source': 'agents-md@builtin'}
        telemetry = {'name': 'telemetry', 'path': 'builtin', 'source': 'telemetry@builtin'}
        for plugins in ([], [agents, telemetry], [agents], [telemetry]):
            with self.subTest(plugins=plugins):
                r, out = self.run_review(plugins=plugins)
                self.assert_success(r, out)
                self.assertEqual(self.manifest(out)['actual_model'], 'claude-opus-5-5')
                self.assertTrue((out / 'report.md').exists())

    def test_non_whitelisted_plugins_fail_closed(self):
        agents = {'name': 'agents-md', 'path': 'builtin', 'source': 'agents-md@builtin'}
        telemetry = {'name': 'telemetry', 'path': 'builtin', 'source': 'telemetry@builtin'}
        evil = {'name': 'evil', 'path': 'builtin', 'source': 'evil@builtin'}
        cases = {
            'non_dict': ['bad'],
            'unknown_builtin': [evil],
            'external_path': [dict(agents, path='/tmp/x')],
            'external_source': [dict(agents, source='agents-md@market')],
            'extra_unknown': [agents, telemetry, evil],
            'duplicate': [agents, agents],
            'missing_source': [{'name': 'agents-md', 'path': 'builtin'}],
            'not_list': {'agents-md': agents},
            'name_list': [{'name': [], 'path': 'builtin', 'source': '[]@builtin'}],
            'name_dict': [{'name': {}, 'path': 'builtin', 'source': '{}@builtin'}],
        }
        for label, plugins in cases.items():
            with self.subTest(case=label):
                r, out = self.run_review(plugins=plugins)
                self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
                status = json.loads((out / 'status.json').read_text())
                self.assertEqual(status['status'], 'failed')
                self.assertIn('unsafe or missing init capability set', status['error'])
                self.assertEqual(status['failure_kind'], 'execution_failed')
                self.assertFalse((out / 'report.md').exists())

    def test_config_overrides_and_adds_model_families(self):
        self.write_config({'claude_review': {'model_families': {
            'opus': 'claude-opus-7(?:-[0-9]+)*', 'sonnet': 'claude-sonnet-[0-9]+(?:-[0-9]+)*'}}})
        r, out = self.run_review(extra=['--model', 'sonnet'], actual_model='claude-sonnet-5-2')
        self.assert_success(r, out)
        argv = self.calls()[-1]['argv']
        self.assertEqual(argv[argv.index('--model') + 1], 'sonnet')
        self.assertEqual(self.manifest(out)['model_family_pattern'], 'claude-sonnet-[0-9]+(?:-[0-9]+)*')
        r, out = self.run_review(actual_model='claude-opus-7-1')
        self.assert_success(r, out)
        for alias, actual in (('opus', 'claude-opus-5-5'), ('sonnet', 'claude-sonnet-５'),
                              ('sonnet', 'claude-sonnet-5x'), ('fable', 'claude-fable-5-extra')):
            with self.subTest(alias=alias, actual=actual):
                r, out = self.run_review(extra=['--model', alias], actual_model=actual)
                self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn('unexpected actual model', json.loads((out / 'status.json').read_text())['error'])
        r, out = self.run_review(extra=['--model', 'fable'])
        self.assert_success(r, out)

    def test_model_choices_come_from_merged_table(self):
        r, _ = self.run_review(extra=['--model', 'sonnet', '--prepare-only'])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('invalid choice', r.stderr)
        self.write_config({'claude_review': {'model_families': {'sonnet': 'claude-sonnet-[0-9]+'}}})
        help_text = subprocess.run([sys.executable, str(ENTRY), '--help'], env=self.env, text=True,
                                   capture_output=True, timeout=30).stdout
        self.assertIn('fable,opus,sonnet', help_text)
        r, out = self.run_review(extra=['--model', 'sonnet', '--prepare-only'])
        self.assert_success(r, out, 'prepared')
        self.assertEqual(self.manifest(out)['requested_model'], 'sonnet')
        self.assertFalse(self.calls())

    def test_broken_config_fails_closed_before_model(self):
        cases = ('{"claude_review": ', '', '[]', '{"claude_review": {"model_families": ["opus"]}}',
                 '{"claude_review": {"model_families": {"opus": "claude-opus-("}}}',
                 '{"claude_review": {"model_families": {"-x": "claude-x"}}}',
                 '{"claude_review": {"model_families": {"opus": 5}}}', '{"claude_review": 3}')
        for text in cases:
            with self.subTest(config=text):
                self.write_config(text)
                r, out = self.run_review(extra=['--prepare-only'])
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn('ai-anti config', r.stderr)
                self.assertFalse(out.exists())
        self.assertFalse(self.calls())

    def test_missing_config_or_keys_use_builtin_defaults(self):
        for value in (None, {}, {'claude_review': None}, {'claude_review': {'model_families': None}},
                      {'claude_review': {}, 'review': {'channels': ['codex']}}):
            with self.subTest(config=value):
                if value is None:
                    self.config.unlink(missing_ok=True)
                else:
                    self.write_config(value)
                r, out = self.run_review()
                self.assert_success(r, out)
                manifest = self.manifest(out)
                self.assertEqual(manifest['requested_model'], 'opus')
                self.assertEqual(manifest['model_family_pattern'], 'claude-opus-[0-9]+(?:-[0-9]+)*')
                self.assertEqual(manifest['actual_model'], 'claude-opus-5-5')

    def test_manifest_records_cli_version_without_warning_when_tested(self):
        r, out = self.run_review()
        self.assert_success(r, out)
        self.assertEqual(self.manifest(out)['cli_version'], '2.1.258 (fixture)')
        self.assertEqual(json.loads((out / 'status.json').read_text())['warnings'], [])
        self.assertNotIn('警告', (out / 'report.md').read_text())

    def test_untested_cli_version_warns_but_does_not_block(self):
        for version in ('9.9.9 (fixture)', 'unversioned build'):
            with self.subTest(version=version):
                r, out = self.run_review('plan', env_extra={'FAKE_VERSION': version})
                self.assert_success(r, out)
                status = json.loads((out / 'status.json').read_text())
                self.assertEqual(len(status['warnings']), 1)
                self.assertEqual(self.manifest(out)['cli_version'], version)
                self.assertEqual(self.manifest(out)['warnings'], status['warnings'])
                self.assertTrue((out / 'report.md').read_text().startswith('> 警告：'))
                self.assertIn('warning:', r.stderr)
        r, out = self.run_review(fake='wrong_model', env_extra={'FAKE_VERSION': '9.9.9'})
        status = json.loads((out / 'status.json').read_text())
        self.assertEqual(status['status'], 'failed')
        self.assertEqual(len(status['warnings']), 1)

    def test_unknown_model_alias_rejected_before_model_call(self):
        r, _ = self.run_review(extra=['--model', 'fabel'])
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.calls())

    def test_request_missing_empty_and_appendix_only_stop_before_model(self):
        for content in (None, '', '# Request\n', '# Request\n## 附录：非需求（主模型推断，可质疑）\nGuesses\n'):
            with self.subTest(content=content):
                if content is None:
                    self.request.unlink(missing_ok=True)
                else:
                    self.request.write_text(content)
                r, _ = self.run_review('plan')
                self.assertNotEqual(r.returncode, 0)
                self.assertFalse(self.calls())

    def test_request_and_parent_symlinks_rejected(self):
        self.request.unlink()
        self.request.symlink_to(self.repo / 'app.py')
        r, _ = self.run_review('plan')
        self.assertNotEqual(r.returncode, 0)
        (self.repo / 'alias').symlink_to(self.repo, target_is_directory=True)
        r, _ = self.run_review(files=['alias/app.py'])
        self.assertNotEqual(r.returncode, 0)
        (self.repo / 'broken.py').symlink_to(self.base / 'absent')
        r, _ = self.run_review(files=['broken.py'])
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.calls())

    def test_traversal_sensitive_and_nonregular_inputs(self):
        self.write('config/secrets.json', '{"key":"fixture"}')
        self.write('.env', 'fixture=true')
        (self.repo / 'fifo').touch()
        (self.repo / 'fifo').unlink()
        os.mkfifo(self.repo / 'fifo')
        for path in ('../task.md', '.git/config', 'config/secrets.json', '.env', 'fifo'):
            with self.subTest(path=path):
                r, _ = self.run_review(files=[path])
                self.assertNotEqual(r.returncode, 0)
                self.assertFalse(self.calls())

    def test_binary_and_large_inputs_rejected_without_truncation(self):
        (self.repo / 'binary').write_bytes(b'ABC\x00DEF')
        self.write('large', 'A' * (256 * 1024 + 1))
        for path in ('binary', 'large'):
            r, _ = self.run_review(files=[path])
            self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.calls())

    def test_plan_must_not_appear_in_selected_snapshot(self):
        self.plan = self.write('task.md', '# Plan\nSensitive plan idea\n')
        self.request = self.write('task.request.md', '# Request\nHandle zero denominator\n')
        r, _ = self.run_review('plan', files=['app.py', 'task.md'])
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.calls())

    def test_out_inside_repo_rejected(self):
        r, _ = self.run_review(out=self.repo / 'review-result')
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.calls())

    def test_core_missing_fail_closed_from_copied_skill(self):
        fake_root = self.base / 'incomplete mechanism'
        relative = ENTRY.relative_to(ROOT)
        target = fake_root / relative
        shutil.copytree(ENTRY.parent, target.parent)
        r, _ = self.run_review(entry=target)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.calls())

    def test_protocol_failures_do_not_produce_success(self):
        for mode in ('nonzero', 'no_init', 'wrong_model', 'extra_tool', 'plugin', 'no_read',
                     'partial_read', 'tool_error', 'no_result', 'error', 'denied', 'empty',
                     'double_result', 'broken_json', 'old_cli', 'missing_error', 'missing_denials', 'bad_read_text'):
            with self.subTest(mode=mode):
                r, out = self.run_review('plan', fake=mode)
                self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
                if (out / 'status.json').exists():
                    self.assertEqual(json.loads((out / 'status.json').read_text())['status'], 'failed')
                self.assertFalse((out / 'report.md').exists())

    def test_process_timeout_and_exited_parent_kill_descendants(self):
        for mode in ('hang', 'child_exit'):
            with self.subTest(mode=mode):
                self.heartbeat.unlink(missing_ok=True)
                r, out = self.run_review(fake=mode, extra=['--timeout', '2', '--idle-timeout', '1'])
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(json.loads((out / 'status.json').read_text())['status'], 'failed')
                self.assertTrue(self.heartbeat.exists())
                n = self.heartbeat.stat().st_size
                time.sleep(.5)
                self.assertEqual(self.heartbeat.stat().st_size, n, 'descendant survived runner cleanup')

    def test_staged_deletion_including_empty_file(self):
        self.write('empty.py', '')
        self.git('add', 'empty.py')
        self.git('commit', '-qm', 'empty fixture')
        self.git('rm', 'deleted.py', 'empty.py')
        r, out = self.run_review(files=['deleted.py', 'empty.py'])
        self.assert_success(r, out)
        self.assertIn('removed = True', '\n'.join(self.calls()[0]['files'].values()))

    def test_git_pathspec_is_literal(self):
        self.write('[ab].py', 'literal_old = True\n')
        self.write('a.py', 'OUT_OF_SCOPE_OLD = True\n')
        self.git('--literal-pathspecs', 'add', '[ab].py', 'a.py')
        self.git('commit', '-qm', 'pathspec fixture')
        self.write('[ab].py', 'literal_new = True\n')
        self.write('a.py', 'OUT_OF_SCOPE_NEW = True\n')
        r, out = self.run_review(files=['[ab].py'])
        self.assert_success(r, out)
        contents = '\n'.join(self.calls()[0]['files'].values())
        self.assertIn('literal_old', contents)
        self.assertNotIn('OUT_OF_SCOPE_OLD', contents)
        self.assertNotIn('OUT_OF_SCOPE_NEW', contents)

    def test_missing_selected_plan_and_context_are_errors(self):
        r, _ = self.run_review('plan', files=['missing.py'])
        self.assertNotEqual(r.returncode, 0)
        r, _ = self.run_review(extra=['--context', 'missing.md'])
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.calls())

    def test_output_parent_symlink_and_traversal_cannot_target_repo(self):
        (self.base / 'alias').symlink_to(self.repo, target_is_directory=True)
        (self.base / 'x').mkdir()
        for output in (self.base / 'alias' / 'output', self.base / 'x' / '..' / 'business repo' / 'output'):
            r, _ = self.run_review(out=output)
            self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.calls())

    def test_combined_context_budget(self):
        text = ('a' * 900 + '\n') * 255
        names = [self.write('big' + str(i), text).name for i in range(10)]
        r, _ = self.run_review(files=names[:5], extra=['--context', *names[5:], '--prepare-only'])
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.calls())

    def test_sensitive_variants_and_blank_scale(self):
        for path in ('.env.production', 'config/app_secrets.json'):
            self.write(path, 'fake_sensitive_value\n')
            r, _ = self.run_review(files=[path])
            self.assertNotEqual(r.returncode, 0)
        r, _ = self.run_review(extra=['--scale', '  '])
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.calls())

    def test_unborn_repo_has_actionable_failure(self):
        unborn = self.base / 'unborn'
        unborn.mkdir()
        subprocess.run(['git', '-C', str(unborn), 'init', '-q'], check=True)
        (unborn / 'app.py').write_text('value = 1\n')
        r, out = self.run_review(extra=['--repo', str(unborn), '--prepare-only'])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('initial HEAD commit', (out / 'status.json').read_text())
        self.assertFalse(self.calls())

    def test_quota_failure_no_retry_or_second_phase(self):
        r, out = self.run_review('plan', fake='quota')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(json.loads((out / 'status.json').read_text())['failure_kind'], 'quota_exhausted')
        self.assertFalse((out / 'report.md').exists())

    def test_output_limit_stops_run(self):
        r, out = self.run_review(fake='flood', extra=['--max-output-bytes', '100000', '--timeout', '5'])
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(json.loads((out / 'status.json').read_text())['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
