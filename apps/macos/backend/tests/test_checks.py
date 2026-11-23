import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import call, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import checks
from config_store import Store, StoreError, atomic
ROOT = Path(__file__).resolve().parents[4]
AGY_PLUGIN = Path('adapters/agy/plugin')

class CheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        checks._cancelled = False
    def tearDown(self):
        checks.stop(); checks._cancelled = False
        self.temp.cleanup()
    def script(self, text):
        p = self.root/'fake.py'; p.write_text(text); return p
    def api_store(self, host='codex', *, home='home'):
        store = Store(self.root / home)
        snap = store.read(); v = snap['values']
        v.update(host=host, channel='qwen', qwen_model='qwen-fixture', qwen_executor='codex', base_url='https://example.invalid/anthropic', responses_base_url='https://example.invalid/v1')
        store.save(v, snap['fingerprints'], 'fixture-token')
        return store

    def agy_link(self, store, checkout):
        link = store.root / '.gemini/config/plugins/ai-anti'
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(checkout / AGY_PLUGIN, target_is_directory=True)
        return link

    def test_agy_local_bin_discovery(self):
        local = self.root / '.local/bin/agy'
        local.parent.mkdir(parents=True)
        local.write_text('#!/bin/sh\nexit 0\n')
        local.chmod(0o700)
        with patch('checks.shutil.which', return_value=None), patch('checks.Path.home', return_value=self.root), patch('checks.os.path.isfile', side_effect=lambda value: value == str(local)):
            self.assertEqual(checks.discover('agy'), str(local))

    def test_claude_local_bin_discovery(self):
        for name in ('claude', 'codex'):
            with self.subTest(name=name):
                local = self.root / '.local/bin' / name
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_text('#!/bin/sh\nexit 0\n')
                local.chmod(0o700)
                with patch('checks.Path.home', return_value=self.root), \
                     patch('checks.os.path.isfile', side_effect=lambda path: path == str(local)), \
                     patch('checks.shutil.which', return_value=None):
                    self.assertEqual(checks.discover(name), str(local))

    def test_cli_status_versions_missing_and_brew(self):
        cli = self.root / 'fake-codex'
        cli.write_text('''#!/bin/sh
[ "$#:$1" = "1:--version" ] || exit 11
if read -r input; then exit 12; fi
echo 'warning: cannot update PATH aliases (os error 1)' >&2
echo 'codex-cli 0.169.1'
''')
        cli.chmod(0o700)
        brew = self.root / 'fake-brew'
        brew.write_text('#!/bin/sh\ntouch "$0.called"\n')
        brew.chmod(0o700)
        path = {'codex': str(cli), 'brew': str(brew)}
        is_file = os.path.isfile
        with patch('checks.Path.home', return_value=self.root), \
             patch('checks.os.path.isfile', side_effect=lambda candidate: not candidate.startswith(('/opt/', '/usr/local/')) and is_file(candidate)), \
             patch('checks.shutil.which', side_effect=path.get) as which:
            status = checks.cli_status(['codex', 'claude', 'codex'])
        self.assertEqual(list(status['items']), ['codex', 'claude'])
        self.assertEqual(status, {'items': {'codex': {'state': 'installed', 'version': '0.169.1'},
                                           'claude': {'state': 'missing'}},
                                  'package_managers': {'brew': True}})
        self.assertEqual(which.call_args_list, [call('codex'), call('claude'), call('brew')])
        self.assertFalse(brew.with_suffix('.called').exists())

    def test_cli_status_rejects_unlisted_names_before_discovery(self):
        with patch('checks.discover') as discover, patch('checks.run') as run:
            for names in (None, [], 'claude', {'claude': True}, ['claude', 1],
                          ['claude', []], ['claude', 'git'], ['../claude']):
                with self.subTest(names=names), self.assertRaisesRegex(StoreError, 'CLI 名称列表无效'):
                    checks.cli_status(names)
            discover.assert_not_called()
            run.assert_not_called()

    def test_cli_status_redacts_errors_and_keeps_probing(self):
        def probe(argv, cwd, log, **kwargs):
            self.assertEqual(argv[1:], ['--version'])
            self.assertEqual(kwargs, {'timeout': 3, 'limit': 65536, 'termination_grace': 1})
            self.assertEqual(Path(log).parent, Path(cwd))
            Path(log).write_text('private-fixture-path and output')
            name = Path(argv[0]).name
            if name == 'claude':
                raise checks.CheckError('超时 private-fixture-path')
            if name == 'codex':
                raise OSError('private-fixture-path')
            if name == 'agy':
                raise checks.CheckError('超过上限 private-fixture-path')
            return 7

        with patch('checks.discover', side_effect=lambda name: name), \
             patch('checks.shutil.which', return_value=None), patch('checks.os.path.isfile', return_value=False), \
             patch('checks.run', side_effect=probe) as run:
            result = checks.cli_status(['claude', 'codex', 'agy', 'qodercli'])
            self.assertEqual(result['items'], {
                'claude': {'state': 'error', 'reason': '版本检测超时'},
                'codex': {'state': 'error', 'reason': '无法执行 CLI'},
                'agy': {'state': 'error', 'reason': '版本输出超过上限'},
                'qodercli': {'state': 'error', 'reason': '版本命令退出异常'},
            })
            self.assertEqual(result['package_managers'], {'brew': False})
            logs = {entry.args[2] for entry in run.call_args_list}
            self.assertTrue(all(not Path(log).exists() for log in logs))
            def no_version(argv, cwd, log, **kwargs):
                Path(log).write_bytes(b'no-version\xff')
                return 0

            run.side_effect = no_version
            self.assertEqual(checks.cli_status(['qodercli'])['items']['qodercli'],
                             {'state': 'error', 'reason': '无法识别版本号'})

    def test_cli_status_cancellation_stops_discovery_and_remaining_probes(self):
        with patch('checks.discover', side_effect=lambda name: name) as discover, \
             patch('checks.run', side_effect=checks.CheckError('检测已取消')):
            with self.assertRaisesRegex(checks.CheckError, '检测已取消'):
                checks.cli_status(['claude', 'codex'])
            discover.assert_called_once_with('claude')
            discover.reset_mock()
            checks.stop()
            with self.assertRaisesRegex(checks.CheckError, '检测已取消'):
                checks.cli_status(['codex'])
            discover.assert_not_called()

    def test_agy_source_discovery_prioritizes_plugin_checkout(self):
        store = Store(self.root / 'home')
        codex_checkout = self.root / 'codex-checkout'
        for path in ('core/REVIEW_PRINCIPLES.md', 'core/CLI_DISCIPLINE.md',
                     'adapters/codex/skills/claude-review/scripts/review.py',
                     'adapters/claude-code/commands/codex-review.md',
                     'adapters/codex/skills/codex-relay/SKILL.md'):
            target = codex_checkout / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('fixture')
        codex_link = store.root / '.agents/skills/codex-relay'
        codex_link.parent.mkdir(parents=True)
        codex_link.symlink_to(codex_checkout / 'adapters/codex/skills/codex-relay')
        self.agy_link(store, ROOT)
        self.assertEqual(checks.source_root(store, host='agy'), ROOT)
        self.assertEqual(checks.source_root(store, host='claude'), codex_checkout)
        self.assertEqual(checks.source_root(store, explicit=codex_checkout, host='agy'), codex_checkout)
        with self.assertRaisesRegex(checks.CheckError, '指向其他 ai-anti 源码'):
            checks.agy_entry(store, codex_checkout)
        codex_link.unlink()
        self.assertEqual(checks.source_root(store, host='codex'), ROOT)

    def test_agy_entry_requires_all_regular_files_and_exact_symlink(self):
        store = Store(self.root / 'home')
        checkout = self.root / 'checkout'
        required = [checkout / AGY_PLUGIN / name for name in (
            'plugin.json', 'rules/AGENTS.md', 'skills/agy-relay/SKILL.md', 'skills/codex-review/SKILL.md')]
        required.append(checkout / 'adapters/agy/scripts/codex_review.py')
        required.append(checkout / 'adapters/agy/scripts/claude_plan.py')
        for path in required:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture')
        link = self.agy_link(store, checkout)
        checks.agy_entry(store, checkout)
        for path in required:
            with self.subTest(path=path):
                path.unlink()
                with self.assertRaisesRegex(checks.CheckError, '入口不完整'):
                    checks.agy_entry(store, checkout)
                path.write_text('fixture')
        script = required[-1]
        script.write_text(' \n')
        with self.assertRaisesRegex(checks.CheckError, '入口不完整'):
            checks.agy_entry(store, checkout)
        script.unlink()
        script.symlink_to(ROOT / 'adapters/agy/scripts/claude_plan.py')
        with self.assertRaisesRegex(checks.CheckError, '入口不完整'):
            checks.agy_entry(store, checkout)
        script.unlink()
        script.write_text('fixture')
        link.unlink()
        link.symlink_to(self.root / 'missing')
        with self.assertRaisesRegex(checks.CheckError, '入口不完整'):
            checks.agy_entry(store, checkout)
        link.unlink()
        link.mkdir()
        with self.assertRaisesRegex(checks.CheckError, '入口不完整'):
            checks.agy_entry(store, checkout)

    def test_agy_local_versions_and_routes(self):
        store = Store(self.root / 'home')
        self.agy_link(store, ROOT)
        atomic(store.paths['ai'], b'{"desktop":{"primary_host":"agy"},"agy_relay":{"review":{"model":"review-id"},"implementation":{"provider":"codex","model":"impl-id"}}}')
        version_logs = {'agy': 'Antigravity CLI 1.5.13\n', 'codex': 'codex-cli 0.169.1\n'}

        def fake_run(argv, cwd, log):
            self.assertEqual(argv[1:], ['--version'])
            log.write_text(version_logs[argv[0]])
            return 0

        with patch('checks.discover', side_effect=lambda name: name) as discover, patch('checks.run', side_effect=fake_run):
            reply = checks.local(store)
            self.assertCountEqual([call.args[0] for call in discover.call_args_list], ['agy', 'codex'])
            self.assertEqual(reply['state'], 'passed')
            self.assertIn('实施：Codex · impl-id · 关联工作树实施（AGY 接力）', reply['message'])
            self.assertIn('评审：Codex · review-id', reply['message'])
            self.assertIn('AGY 1.5.13（在实测清单内）', reply['message'])
            self.assertIn('评审检测不能替代实施证据', reply['message'])
            version_logs['agy'] = 'Antigravity CLI 8.0.1\n'
            self.assertIn('AGY 8.0.1（未在实测清单）', checks.local(store)['message'])
            version_logs['agy'] = 'no version'
            with self.assertRaisesRegex(checks.CheckError, 'AGY CLI 版本输出无效'):
                checks.local(store)
            version_logs['agy'] = '1.5.13'
            version_logs['codex'] = 'codex-cli 0.163.0'
            with self.assertRaisesRegex(checks.CheckError, r'Codex 实施要求 codex-cli 0\.169\.1'):
                checks.local(store)

    def test_agy_local_requires_implementation_only_outside_solo(self):
        store = Store(self.root / 'home')
        self.agy_link(store, ROOT)
        atomic(store.paths['ai'], b'{"desktop":{"primary_host":"agy"}}')
        with patch('checks.discover') as discover, patch('checks.run') as run:
            with self.assertRaisesRegex(checks.CheckError, r'未配置 agy_relay\.implementation'):
                checks.local(store)
            discover.assert_not_called()
            run.assert_not_called()
        atomic(store.paths['ai'], b'{"desktop":{"primary_host":"agy"},"relay":{"default_mode":"solo"},"agy_relay":{"dual_plan":{"enabled":true}}}')

        def version_only(argv, cwd, log):
            self.assertEqual(argv, ['agy', '--version'])
            log.write_text('1.5.13')
            return 0

        with patch('checks.discover', side_effect={'agy': 'agy'}.__getitem__) as discover, patch('checks.run', side_effect=version_only):
            reply = checks.local(store)
        discover.assert_called_once_with('agy')
        self.assertIn('实施：停用独立实施；评审：Codex · CLI 默认', reply['message'])

    def test_agy_local_probes_claude_only_when_dual_plan_enabled(self):
        store = Store(self.root / 'home')
        self.agy_link(store, ROOT)
        dual = {'enabled': True, 'model': 'haiku'}
        data = {'desktop': {'primary_host': 'agy'}, 'agy_relay': {
            'implementation': {'provider': 'codex', 'model': 'impl-id'}, 'dual_plan': dual}}
        versions = {'agy': '1.5.13', 'codex': 'codex-cli 0.169.1', 'claude': 'Claude Code 2.1.326'}

        def fake_run(argv, cwd, log):
            self.assertEqual(argv[1:], ['--version'])
            log.write_text(versions[argv[0]])
            return 4 if versions[argv[0]] == 'nonzero' else 0

        atomic(store.paths['ai'], json.dumps(data).encode())
        with patch('checks.discover', side_effect=lambda name: name), patch('checks.run', side_effect=fake_run):
            self.assertIn('双方案：Claude · haiku（Claude 2.1.326 在实测清单内）', checks.local(store)['message'])
            dual.pop('model')
            atomic(store.paths['ai'], json.dumps(data).encode())
            versions['claude'] = '9.8.7'
            self.assertIn('双方案：Claude · CLI 默认（Claude 9.8.7 未在实测清单）', checks.local(store)['message'])
            for version, error in (('garbage', 'Claude CLI 版本输出无效'), ('nonzero', 'claude --version 失败')):
                versions['claude'] = version
                with self.assertRaisesRegex(checks.CheckError, error):
                    checks.local(store)
            dual['enabled'] = False
            atomic(store.paths['ai'], json.dumps(data).encode())
            versions.pop('claude')
            self.assertNotIn('双方案', checks.local(store)['message'])

    def test_bad_agy_dual_plan_stops_agy_before_any_cli(self):
        store = Store(self.root / 'home')
        atomic(store.paths['ai'], b'{"desktop":{"primary_host":"agy"},"agy_relay":{"dual_plan":{"enabled":1}}}')
        with patch('checks.discover') as discover, patch('checks.run') as run:
            with self.assertRaisesRegex(checks.CheckError, r'agy_relay\.dual_plan\.enabled'):
                checks.local(store, ROOT)
        discover.assert_not_called()
        run.assert_not_called()

    def test_bad_agy_dual_plan_does_not_block_claude_or_codex_checks(self):
        evidence = dict(status='completed', completed=True, mode='diff', channel='qwen',
                        executor='codex', wire_api='responses', requested_model='qwen-fixture',
                        actual_model=None, model_evidence='requested_only')

        def fake_run(argv, cwd, log, **kwargs):
            if '--qwen-executor' in argv:
                out = Path(argv[argv.index('--out') + 1])
                out.mkdir()
                for name in ('status', 'manifest'):
                    (out / (name + '.json')).write_text(json.dumps(evidence))
            return 0

        for host in ('claude', 'codex'):
            with self.subTest(host=host):
                store = self.api_store(host, home=host)
                data = json.loads(store.raw('ai'))
                data['agy_relay'] = {'dual_plan': {'enabled': 'bad'}}
                atomic(store.paths['ai'], json.dumps(data).encode())
                values = store.read()['values']
                self.assertEqual(values['agy_relay_error'], '')
                self.assertIn('agy_relay.dual_plan.enabled', values['agy_dual_plan_error'])
                with patch('checks.discover', return_value='/usr/bin/true'), patch('checks.run', side_effect=fake_run):
                    self.assertEqual(checks.local(store, ROOT)['state'], 'passed')
                    self.assertEqual(checks.real(store, ROOT)['state'], 'passed')

    def test_local_isolates_soft_errors_across_hosts(self):
        store = Store(self.root / 'home')
        self.agy_link(store, ROOT)
        atomic(store.paths['ai'], b'{"agy_relay":{"review":{"future":true}}}')
        with patch('checks.discover', return_value='/usr/bin/true'):
            self.assertEqual(checks.local(store, ROOT)['state'], 'passed')
        atomic(store.paths['ai'], b'{"desktop":{"primary_host":"agy"},"claude_relay":{"dual_plan":{"enabled":1}},"agy_relay":{"implementation":{"provider":"codex","model":"impl"}}}')
        self.assertIn('claude_relay.dual_plan.enabled', store.read()['values']['dual_plan_error'])

        def version_only(argv, cwd, log):
            log.write_text({'agy': '1.5.13', 'codex': 'codex-cli 0.169.1'}[argv[0]])
            return 0

        with patch('checks.discover', side_effect=lambda name: name), patch('checks.run', side_effect=version_only):
            self.assertEqual(checks.local(store)['state'], 'passed')
        atomic(store.paths['ai'], b'{"desktop":{"primary_host":"agy"},"agy_relay":{"review":{"future":true}}}')
        with patch('checks.discover') as discover, patch('checks.run') as run:
            with self.assertRaisesRegex(checks.CheckError, r'agy_relay\.review has unknown keys'):
                checks.local(store)
            discover.assert_not_called()
            run.assert_not_called()

    def test_agy_real_rejects_before_source_or_cli(self):
        store = Store(self.root / 'home')
        atomic(store.paths['ai'], b'{"desktop":{"primary_host":"agy"}}')
        with patch('checks.source_root') as source, patch('checks.discover') as discover, patch('checks.run') as run:
            with self.assertRaisesRegex(checks.CheckError, 'AGY 主模型暂不支持桌面端合成评审检测'):
                checks.real(store)
            source.assert_not_called()
            discover.assert_not_called()
            run.assert_not_called()

    def test_implementation_checks_independent_token_profile(self):
        store = self.api_store()
        snap = store.read(); v = snap['values']
        v['implementation_provider'] = 'token_plan'
        store.save(v, snap['fingerprints'])
        # Legacy custom API credentials cannot fulfill implementation Token Plan.
        with patch('checks.discover', return_value='/usr/bin/true'):
            with self.assertRaisesRegex(checks.CheckError, '实施 Token Plan 凭证'):
                checks.local(store, str(ROOT))
        snap = store.read(); v = snap['values']
        store.save(v, snap['fingerprints'], secret_actions={'token_plan': {'action': 'replace', 'value': 'fixture-implementation-key'}})
        with patch('checks.discover', return_value='/usr/bin/true'):
            result = checks.local(store, str(ROOT))
        self.assertIn('Token Plan · glm-5.3 · Responses', result['message'])
        snap = store.read(); values = snap['values']; values['token_plan_implementation_model'] = 'qwen4-max'
        store.save(values, snap['fingerprints'])
        with patch.object(checks, 'discover', return_value='/usr/bin/true'):
            qwen4 = checks.local(store, ROOT)
        self.assertIn('Token Plan · qwen4-max · Responses', qwen4['message'])
        self.assertIn('未验证', qwen4['message'])
        self.assertIn('评审检测不能替代实施证据', result['message'])
        for host, mode in (('claude', 'rules'), ('codex', 'solo')):
            snap = store.read(); v = snap['values']; v.update(host=host, mode=mode)
            store.save(v, snap['fingerprints'], secret_actions={'token_plan': {'action': 'clear'}})
            with patch('checks.discover', return_value='/usr/bin/true'):
                self.assertEqual(checks.local(store, str(ROOT))['state'], 'passed')

    def test_qoder_implementation_checks_version_without_token_plan_or_model_call(self):
        store = self.api_store()
        snap = store.read(); values = snap['values']; values['implementation_provider'] = 'qoder'
        store.save(values, snap['fingerprints'])
        def fake_run(argv, cwd, log, **kwargs):
            self.assertEqual(argv[1:], ['--version'])
            Path(log).write_text('1.1.77\n')
            return 0
        with patch('checks.discover', side_effect=lambda name: '/fake/' + name) as discover, patch('checks.run', side_effect=fake_run):
            reply = checks.local(store, str(ROOT))
        self.assertEqual({c.args[0] for c in discover.call_args_list}, {'codex', 'qodercli'})
        self.assertIn('Qoder 订阅 · GLM-5.3', reply['message'])
        def wrong_run(argv, cwd, log, **kwargs):
            Path(log).write_text('1.1.78\n'); return 0
        with patch('checks.discover', return_value='/fake/cli'), patch('checks.run', side_effect=wrong_run):
            with self.assertRaisesRegex(checks.CheckError, '1.1.77'): checks.local(store, str(ROOT))
        snap = store.read(); values = snap['values']; values['mode'] = 'solo'; store.save(values, snap['fingerprints'])
        with patch('checks.discover', return_value='/usr/bin/true') as discover:
            self.assertEqual(checks.local(store, str(ROOT))['state'], 'passed')
        self.assertEqual({c.args[0] for c in discover.call_args_list}, {'codex'})

    def test_qoder_local_bin_discovery(self):
        local = self.root / '.local/bin/qodercli'; local.parent.mkdir(parents=True)
        local.write_text('#!/bin/sh\nexit 0\n'); local.chmod(0o700)
        with patch('checks.shutil.which', return_value=None), patch('checks.Path.home', return_value=self.root), patch('checks.os.path.isfile', side_effect=lambda value: value == str(local)):
            self.assertEqual(checks.discover('qodercli'), str(local))

    def test_local_effective_executor_dependencies(self):
        store = self.api_store()
        with patch('checks.discover', return_value='/usr/bin/true') as discover:
            checks.local(store, str(ROOT))
        self.assertEqual({call.args[0] for call in discover.call_args_list}, {'codex'})
        snap = store.read(); v = snap['values']; v.update(host='claude', channel='qwen'); store.save(v, snap['fingerprints'])
        with patch('checks.discover', return_value='/usr/bin/true') as discover:
            checks.local(store, str(ROOT))
        self.assertEqual({call.args[0] for call in discover.call_args_list}, {'claude', 'codex'})

    def test_claude_codex_implementation_version_pin(self):
        store = self.api_store('claude')
        snap = store.read()
        v = dict(snap['values'], claude_implementation_provider='codex',
                 claude_implementation_model='gpt-6.2-sol')
        store.save(v, snap['fingerprints'])
        fake = self.root / 'codex'
        for output, error in (
            ('codex-cli 0.169.1\n', None),
            ('codex-cli 0.163.0\n', '0.169.1'),
        ):
            with self.subTest(output=output):
                fake.write_text('#!' + sys.executable + '\nimport sys\nassert sys.argv[1:] == ["--version"]\nprint(' + repr(output) + ', end="")\n')
                fake.chmod(0o700)
                with patch('checks.discover', side_effect=lambda name: str(fake) if name == 'codex' else '/usr/bin/true') as discover:
                    if error:
                        with self.assertRaisesRegex(checks.CheckError, error):
                            checks.local(store, ROOT)
                    else:
                        self.assertIn('Codex · gpt-6.2-sol · 关联工作树实施', checks.local(store, ROOT)['message'])
                        self.assertEqual({call.args[0] for call in discover.call_args_list}, {'claude', 'codex'})
        with self.assertRaisesRegex(checks.CheckError, '无法定位 ai-anti 源码，未检查实施版本'):
            checks.local(store, self.root / 'missing')
        snap = store.read()
        store.save(dict(snap['values'], mode='solo'), snap['fingerprints'])
        with patch('checks.discover', return_value='/usr/bin/true') as discover, patch('checks.check_claude_codex_version') as version_check:
            self.assertIn('停用独立实施', checks.local(store, ROOT)['message'])
        self.assertEqual({call.args[0] for call in discover.call_args_list}, {'claude', 'codex'})
        version_check.assert_not_called()

    def test_claude_codex_implementation_version_parser_rejects_noise(self):
        store = self.api_store('claude')
        snap = store.read()
        store.save(dict(snap['values'], claude_implementation_provider='codex',
                        claude_implementation_model='gpt-6.2-sol'), snap['fingerprints'])
        fake = self.root / 'codex'
        warning = 'WARNING: proceeding, even though we could not create PATH aliases: Operation not permitted (os error 1)'
        for line in (warning, 'unrelated diagnostic'):
            with self.subTest(line=line):
                fake.write_text('#!' + sys.executable + '\nimport sys\nassert sys.argv[1:] == ["--version"]\nprint(' + repr(line) + ', file=sys.stderr)\nprint("codex-cli 0.169.1")\n')
                fake.chmod(0o700)
                with patch('checks.discover', side_effect=lambda name: str(fake) if name == 'codex' else '/usr/bin/true'):
                    if line == warning:
                        self.assertEqual(checks.local(store, ROOT)['state'], 'passed')
                    else:
                        with self.assertRaisesRegex(checks.CheckError, '版本输出无效'):
                            checks.local(store, ROOT)

    def test_formal_executor_limits_and_requested_only(self):
        for host, executor in (('codex', 'codex'), ('claude', 'claude'), ('claude', 'codex')):
            store = self.api_store(host)
            data=json.loads(store.paths['ai'].read_text()); data['review'].update(timeout=321,idle_timeout=45)
            data['claude_relay']['qwen_executor'] = 'claude'
            data['codex_relay']['qwen_executor'] = 'claude'
            atomic(store.paths['ai'],json.dumps(data).encode())
            if executor == 'codex':
                snap = store.read()
                store.save(snap['values'], snap['fingerprints'])
                with patch('checks.discover', return_value='/usr/bin/true') as discover:
                    self.assertEqual(checks.local(store, str(ROOT))['state'], 'passed')
                self.assertEqual({call.args[0] for call in discover.call_args_list}, {host, 'codex'})
            def fake_run(argv, cwd, log, **kwargs):
                if '--qwen-executor' in argv:
                    self.assertEqual(argv[argv.index('--qwen-executor')+1], executor)
                    if executor == 'codex':
                        self.assertEqual(argv[argv.index('--responses-base-url')+1], 'https://example.invalid/v1')
                    else:
                        self.assertNotIn('--responses-base-url', argv)
                    self.assertEqual(argv[argv.index('--timeout')+1], '321')
                    self.assertEqual(argv[argv.index('--idle-timeout')+1], '45')
                    self.assertEqual(argv[argv.index('--max-output-bytes')+1], str(8*1024*1024))
                    self.assertEqual(kwargs['timeout'], 381); self.assertEqual(kwargs['limit'], 8*1024*1024)
                    out = Path(argv[argv.index('--out')+1]); out.mkdir()
                    evidence = dict(status='completed', completed=True, mode='diff', channel='qwen', executor=executor, wire_api='responses' if executor == 'codex' else 'anthropic', requested_model='qwen-fixture', actual_model=None if executor == 'codex' else 'qwen-fixture')
                    if executor == 'codex': evidence['model_evidence'] = 'requested_only'
                    for name in ('status', 'manifest'): (out/(name+'.json')).write_text(json.dumps(evidence))
                return 0
            with patch('checks.discover', return_value='/usr/bin/true'), patch('checks.run', side_effect=fake_run):
                reply = checks.real(store, str(ROOT))
            self.assertEqual(reply['actual_model'], None if executor == 'codex' else 'qwen-fixture')
            self.assertEqual(reply['model_evidence'], 'requested_only' if executor == 'codex' else 'actual')

    def test_evidence_rejects_mismatch_and_claimed_actual(self):
        values = dict(host='codex', qwen_executor='codex', qwen_model='qwen-fixture')
        good = dict(status='completed', completed=True, mode='diff', channel='qwen', executor='codex', wire_api='responses', requested_model='qwen-fixture', actual_model=None, model_evidence='requested_only')
        self.assertIsNone(checks.qwen_evidence(good, good, values))
        for key, bad in [('status', 'failed'), ('completed', False), ('channel', 'claude'), ('executor', 'claude'), ('wire_api', 'anthropic'), ('requested_model', 'other'), ('actual_model', 'qwen-fixture'), ('model_evidence', 'actual')]:
            for side in (0, 1):
                if key == 'completed' and side == 1: continue
                pair = [dict(good), dict(good)]; pair[side][key] = bad
                with self.subTest(key=key, side=side), self.assertRaises(ValueError): checks.qwen_evidence(*pair, values)
        values['host'] = 'claude'
        claude = dict(good, executor='claude', wire_api='anthropic', actual_model='qwen-fixture')
        self.assertEqual(checks.qwen_evidence(claude, claude, values), 'qwen-fixture')
        with self.assertRaises(ValueError): checks.qwen_evidence(dict(claude, actual_model='other'), claude, values)

    def test_solo_rejects_model_detection_before_cli(self):
        store = self.api_store()
        s = store.read(); v = s['values']; v['mode'] = 'solo'; store.save(v,s['fingerprints'])
        with patch('checks.run') as run:
            with self.assertRaisesRegex(checks.CheckError,'solo'): checks.real(store,str(ROOT))
        run.assert_not_called()

    def test_qoder_formal_route_and_strict_evidence(self):
        store = Store(self.root/'qoder-home')
        snap = store.read(); v = snap['values']; v.update(host='codex', channel='qoder')
        store.save(v, snap['fingerprints'])
        data=json.loads(store.paths['ai'].read_text()); data['review'].update(timeout=321,idle_timeout=45)
        data['agy_relay'] = {'review': {'future': True}}
        atomic(store.paths['ai'],json.dumps(data).encode())
        with patch('checks.discover', return_value='/usr/bin/true') as discover:
            checks.local(store, str(ROOT))
        self.assertEqual({c.args[0] for c in discover.call_args_list}, {'codex','qodercli'})
        good = dict(status='completed',completed=True,mode='diff',channel='qoder',executor='qoder',wire_api='qoder-cli',requested_model='kimi-k3.1-pro',actual_model='Kimi-K3.1-Pro')
        def fake_run(argv, cwd, log, **kwargs):
            if '--channel' in argv:
                self.assertEqual(argv[argv.index('--channel')+1], 'qoder')
                self.assertEqual(argv[argv.index('--model')+1], 'kimi-k3.1-pro')
                self.assertEqual(argv[argv.index('--timeout')+1], '321')
                self.assertEqual(argv[argv.index('--idle-timeout')+1], '45')
                self.assertEqual(kwargs['timeout'], 381); self.assertEqual(kwargs['limit'], 2*1024*1024)
                self.assertEqual(kwargs['env']['HOME'], str(store.root))
                out = Path(argv[argv.index('--out')+1]); out.mkdir()
                for name in ('status','manifest'): (out/(name+'.json')).write_text(json.dumps(good))
            return 0
        with patch('checks.discover', return_value='/usr/bin/true'), patch('checks.run', side_effect=fake_run):
            self.assertEqual(checks.real(store, str(ROOT))['actual_model'], 'Kimi-K3.1-Pro')
        for key, bad in [('completed',False),('mode','plan'),('status','failed'),('channel','qwen'),('executor','codex'),('wire_api','responses'),('requested_model','other'),('requested_model','kimi-k3'),('actual_model','other'),('actual_model','Kimi-K3'),('actual_model',None)]:
            for side in (0,1):
                if (key == 'mode' and side == 0) or (key == 'completed' and side == 1): continue
                pair = [dict(good),dict(good)]; pair[side][key] = bad
                with self.subTest(key=key, side=side), self.assertRaises(ValueError): checks.qoder_evidence(*pair)
        def no_evidence(argv, cwd, log, **kwargs): return 0
        with patch('checks.discover',return_value='/usr/bin/true'), patch('checks.run',side_effect=no_evidence):
            with self.assertRaisesRegex(checks.CheckError,'证据'): checks.real(store,str(ROOT),timeout=1)

    def test_isolated_store_budgets_save_and_profile_roundtrip(self):
        store = self.api_store()
        data = json.loads(store.paths['ai'].read_text())
        data['review'].update(timeout=321, idle_timeout=45, future_field='keep')
        atomic(store.paths['ai'], json.dumps(data).encode())
        with patch.dict(os.environ, HOME=str(self.root/'unrelated')):
            self.assertEqual(checks.review_budgets(store, ROOT), (321,45))
            self.assertEqual(checks.review_budgets(store, ROOT, 123), (123,45))
        for service in ('custom_api', 'token_plan'):
            snap=store.read(); v=snap['values']; v['model_services']['active_api']=service
            store.save(v, snap['fingerprints'])
            self.assertEqual(store.read()['values']['model_services']['active_api'],service)
            self.assertEqual(json.loads(store.paths['ai'].read_text())['model_services']['active_api'],service)
            self.assertEqual(json.loads(store.paths['ai'].read_text())['review']['timeout'],321)
            self.assertEqual(json.loads(store.paths['ai'].read_text())['review']['idle_timeout'],45)
            self.assertEqual(json.loads(store.paths['ai'].read_text())['review']['future_field'],'keep')

    def test_bad_budgets_stop_local_and_real_before_cli(self):
        store=self.api_store()
        data=json.loads(store.paths['ai'].read_text()); data['review']['timeout']=True
        atomic(store.paths['ai'],json.dumps(data).encode())
        with patch('checks.run') as run, patch('checks.discover') as discover:
            for check in (checks.local, checks.real):
                with self.assertRaisesRegex(checks.CheckError,'review.timeout'): check(store,str(ROOT))
            with self.assertRaises(checks.CheckError): checks.real(store,str(ROOT),timeout=1)
            run.assert_not_called(); discover.assert_not_called()

    def test_old_source_clear_error(self):
        store=Store(self.root/'home')
        for content in (None, 'def load(path): return {}'):
            scripts=self.root/'old/adapters/codex/skills/claude-review/scripts'; scripts.mkdir(parents=True,exist_ok=True)
            if content is not None: (scripts/'ai_anti_config.py').write_text(content)
            with self.assertRaisesRegex(checks.CheckError,'review_limits.*更新'):
                checks.review_budgets(store,self.root/'old')

    def test_native_idle_and_continuing_output_total(self):
        cases=[('import time; time.sleep(20)',2,.2,'空闲超时'),
               ('import time\nwhile True:\n print("p",flush=True); time.sleep(.05)',.3,.2,'检测超时')]
        for source,total,idle,message in cases:
            with self.assertRaisesRegex(checks.CheckError,message):
                checks.run([sys.executable,'-c',source],self.root,self.root/'log',timeout=total,idle_timeout=idle)

    def test_version_nonzero_and_timeout(self):
        for text, timeout, expected in [('import sys; sys.exit(4)',2,4), ('import time; time.sleep(30)',.2,None)]:
            p=self.script(text)
            if expected is None:
                with self.assertRaisesRegex(checks.CheckError,'超时'): checks.run([sys.executable,str(p)], self.root,self.root/'log',timeout=timeout)
            else: self.assertEqual(checks.run([sys.executable,str(p)],self.root,self.root/'log',timeout=timeout),expected)
    def test_output_limit(self):
        p=self.script('print("x"*10000)')
        with self.assertRaisesRegex(checks.CheckError,'上限'): checks.run([sys.executable,str(p)],self.root,self.root/'log',limit=100)
    def test_pre_cancel_does_not_start(self):
        checks._cancelled=True
        with self.assertRaisesRegex(checks.CheckError,'取消'): checks.run([sys.executable,'-c','print(1)'],self.root,self.root/'log')
        self.assertFalse((self.root/'log').exists())
    def test_nested_formal_runner_group_cleanup(self):
        # Use the actual runner execute/finally path; nested child has its own session.
        pidfile=self.root/'child.pid'
        child=self.root/'child.py'
        child.write_text('import os,time\nfrom pathlib import Path\nPath('+repr(str(pidfile))+').write_text(str(os.getpid()))\ntime.sleep(60)\n')
        runner=self.root/'wrapper.py'
        runner.write_text('import sys\nfrom pathlib import Path\nsys.path.insert(0,'+repr(str(ROOT/'adapters/codex/skills/claude-review/scripts'))+')\nfrom review_runner import execute\nexecute([sys.executable,'+repr(str(child))+'],Path('+repr(str(self.root))+'),Path('+repr(str(self.root/'nested.log'))+'),60,60,100000)\n')
        with self.assertRaisesRegex(checks.CheckError,'超时'): checks.run([sys.executable,str(runner)],self.root,self.root/'wrapper.log',timeout=1)
        pid=int(pidfile.read_text())
        for _ in range(20):
            try: os.kill(pid,0)
            except ProcessLookupError: break
            time.sleep(.05)
        else: self.fail('nested model process survived cancellation')
    def test_codex_json_needs_final_and_material_token(self):
        path=self.root/'events'
        def events(values): path.write_text('\n'.join(json.dumps(x) for x in values))
        base=[{'type':'item.completed','item':{'type':'agent_message','text':'TOKEN'}}]
        for values in (base, [{'type':'turn.completed'}], base+[{'type':'error','message':'private'},{'type':'turn.completed'}]):
            events(values)
            with self.assertRaises(checks.CheckError): checks.codex_events(path,'TOKEN')
        events(base+[{'type':'turn.completed'}]); self.assertEqual(checks.codex_events(path,'TOKEN'),'未知')
        events([{'type':'thread.started','model':'actual'},*base,{'type':'turn.completed'}]); self.assertEqual(checks.codex_events(path,'TOKEN'),'actual')
    def test_source_not_inferred_from_cwd(self):
        with self.assertRaises(checks.CheckError): checks.source_root(Store(self.root))
        self.assertEqual(checks.source_root(Store(self.root),str(ROOT)),ROOT)
    def test_real_codex_with_fake_cli(self):
        store=Store(self.root/'home')
        snap=store.read(); v=snap['values']; v['reviewer_model']='chosen'
        store.save(v,snap['fingerprints'])
        fake=self.root/'codex'
        fake.write_text('#!'+sys.executable+'\nimport sys,json\nfrom pathlib import Path\nif "--help" in sys.argv:\n print("--json --sandbox --ephemeral --ignore-user-config --ignore-rules")\nelse:\n assert sys.argv[sys.argv.index("--model")+1]=="chosen"\n marker=Path("sample.txt").read_text().split("READ_TOKEN=")[1].strip()\n print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":marker}}))\n print(json.dumps({"type":"turn.completed"}))\n')
        fake.chmod(0o700)
        data=json.loads(store.paths['ai'].read_text()); data['review'].update(timeout=321,idle_timeout=45)
        atomic(store.paths['ai'],json.dumps(data).encode())
        original_run=checks.run
        original=checks.discover
        with patch('checks.run',wraps=original_run) as runner, patch('checks.discover',side_effect=lambda name: str(fake) if name=='codex' else original(name)):
            value=checks.real(store,str(ROOT))
        model_call=next(c for c in runner.call_args_list if c.kwargs.get('idle_timeout') is not None)
        self.assertEqual(model_call.kwargs['timeout'],321)
        self.assertEqual(model_call.kwargs['idle_timeout'],45)
        self.assertEqual(value['state'],'passed'); self.assertEqual(value['actual_model'],'未知')
    def test_local_does_not_claim_login(self):
        store=Store(self.root/'home')
        with patch('checks.discover',return_value='/usr/bin/true'):
            value=checks.local(store,str(ROOT))
        self.assertIn('未验证登录',value['message'])
    def test_local_qwen_skips_relative_and_uses_global(self):
        store=Store(self.root/'home')
        atomic(store.paths['ai'],b'{"review":{"channels":["qwen"]},"claude_relay":{"qwen_executor":"codex"},"plugins":{"qwen":{"model":"qwen-fixture","responses_base_url":"https://example.invalid/v1","credential_paths":["project/credentials.json","~/.config/ai-anti/secrets.json"]}}}')
        atomic(store.paths['secrets'],b'{"tokenplan":{"value":"fake-global"}}')
        with patch('checks.discover',return_value='/usr/bin/true'):
            value=checks.local(store,str(ROOT))
        self.assertEqual(value['state'],'passed')

    def test_qwen_checks_accept_legacy_executor_and_reject_conflict(self):
        store = self.api_store('claude')
        config = json.loads(store.raw('ai'))
        config.pop('claude_relay', None)
        config['plugins']['qwen']['claude_executor'] = 'codex'
        atomic(store.paths['ai'], json.dumps(config).encode())
        with patch('checks.discover', return_value='/usr/bin/true') as discover:
            self.assertEqual(checks.local(store, str(ROOT))['state'], 'passed')
        self.assertEqual({call.args[0] for call in discover.call_args_list}, {'claude', 'codex'})

        config['claude_relay'] = {'qwen_executor': 'claude'}
        raw = json.dumps(config).encode()
        atomic(store.paths['ai'], raw)
        conflict = r'claude_relay\.qwen_executor.*plugins\.qwen\.claude_executor.*冲突'
        for check in (checks.local, checks.real):
            with self.subTest(check=check.__name__), patch('checks.discover', side_effect=AssertionError('unexpected CLI discovery')) as discover:
                with self.assertRaisesRegex(StoreError, conflict):
                    check(store, str(ROOT))
                discover.assert_not_called()
        self.assertEqual(store.raw('ai'), raw)

    def test_real_result_invalidated_by_config_change(self):
        store=Store(self.root/'home'); snap=store.read(); v=snap['values']; v['reviewer_model']='chosen'; store.save(v,snap['fingerprints'])
        def fake_events(path,marker):
            atomic(store.paths['claude'],b'{"model":"external"}'); return 'unknown'
        with patch('checks.review_budgets',return_value=(10,900)), patch('checks.discover',return_value='/usr/bin/true'), patch('checks.run',return_value=0), patch('checks.codex_events',side_effect=fake_events), patch.object(Path,'read_text',return_value='--json --sandbox --ephemeral --ignore-user-config --ignore-rules'):
            with self.assertRaisesRegex(checks.CheckError,'作废'): checks.real(store,str(ROOT),timeout=10)

if __name__ == '__main__': unittest.main()
