"""Formal offline Qoder entry, isolation arguments and model selection."""
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import test_thin_review as thin

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/claude-review/scripts'
sys.path.insert(0, str(SCRIPTS))
from review_channels import resolve, child_environment
from review_runner import MODEL_FAMILIES, RunError
from review_qoder import capability
from ai_anti_config import ConfigError


class QoderReviewTests(thin.ThinReviewTests):
    def setUp(self):
        super().setUp()
        shutil.copyfile(Path(__file__).with_name('fake_qoder.py'), self.bindir / 'qodercli')
        (self.bindir / 'qodercli').chmod(0o700)
        self.write_config({'codex_relay': {'review_channel': 'qoder'}})

    def test_configured_limits_prepare_only(self):
        config=json.loads(self.config.read_text())
        config['review']={'timeout':234,'idle_timeout':56}
        self.write_config(config)
        r,out=self.run_review(extra=['--prepare-only'])
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(self.manifest(out)['limits']['timeout'],234)
        self.assertEqual(self.manifest(out)['limits']['idle_timeout'],56)
        self.assertEqual(self.calls(),[])

    def test_formal_plan_diff_and_alias(self):
        for version in ('1.1.74', '1.1.77'):
            for mode in ('plan', 'diff'):
                r, out = self.run_review(mode, extra=['--channel', 'qoder', '--model', 'kimi-k3.1-pro'],
                                         env_extra={'FAKE_VERSION': version, 'FAKE_INIT_VERSION': version})
                self.assert_success(r, out)
                m = self.manifest(out)
                for key, expected in {'channel': 'qoder', 'executor': 'qoder', 'wire_api': 'qoder-cli',
                                      'requested_model': 'kimi-k3.1-pro', 'configured_model': 'Kimi-K3.1-Pro',
                                      'cli_model': 'kmodel_latest', 'actual_model': 'Kimi-K3.1-Pro',
                                      'material_transport': 'material_ids'}.items():
                    self.assertEqual(m[key], expected)
                self.assertEqual(m['limits'], {'timeout': 1800, 'idle_timeout': 900,
                                              'max_output_bytes': 2 * 1024 * 1024})
                self.assertEqual(len(m['stages']), 2 if mode == 'plan' else 1)
                for stage in m['stages'].values():
                    self.assertEqual(stage['credits'], .25)
                    self.assertEqual(stage['read_recoveries'], [])
                    self.assertEqual(stage['visible_context']['agents'], ['Explore'])
                if mode == 'plan':
                    calls = self.calls()[-2:]
                    self.assertNotIn('Handle zero denominator', ''.join(calls[0]['files'].values()))
                    self.assertIn('Handle zero denominator', ''.join(calls[1]['files'].values()))
                    self.assertIn('FAKE REPORT', ''.join(calls[1]['files'].values()))

    def test_verified_isolation_flags_and_environment(self):
        r, out = self.run_review(env_extra={'ANTHROPIC_API_KEY': 'wrong', 'OPENAI_API_KEY': 'wrong',
                                           'QODER_TEST': 'keep', 'HTTPS_PROXY': 'http://proxy.invalid',
                                           'QODER_PERSONAL_ACCESS_TOKEN': 'SYNTHETIC_DO_NOT_LOG'})
        self.assert_success(r, out)
        call = self.calls()[0]
        args = call['argv']
        for flag, value in {'--tools': '', '--allowed-tools': 'mcp__materials__read_material',
                            '--model': 'kmodel_latest', '--setting-sources': '',
                            '--permission-mode': 'dont_ask', '--max-model-request-retries': '0'}.items():
            self.assertEqual(args[args.index(flag) + 1], value)
        for flag in ('--disable-builtin-skills', '--strict-mcp-config', '--no-session-persistence'):
            self.assertIn(flag, args)
        settings = json.loads(args[args.index('--settings') + 1])
        self.assertEqual(settings, {'disableAllHooks': True, 'skills': {'enabled': False,
                         'disableShellExecution': True}, 'autoMemoryEnabled': False})
        self.assertEqual(call['env'], {'QODER_TEST': 'keep', 'HTTPS_PROXY': 'http://proxy.invalid'})
        self.assertFalse(call['forbidden_provider_env_present'])
        self.assertNotIn('SYNTHETIC_DO_NOT_LOG', self.audit.read_text())

    def test_model_validation_and_missing_cli(self):
        for model in ('opus', 'auto', 'ultimate', 'Kimi-K3.1-Pro', ''):
            r, _ = self.run_review(extra=['--model', model, '--prepare-only'])
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('Qoder supports only', r.stderr)
        for alias in ('kimi-k3.1-pro', 'kimi-k3', 'kmodel_latest'):
            r, out = self.run_review(extra=['--model', alias, '--prepare-only'])
            self.assert_success(r, out, 'prepared')
        for model in ('kimi-k3', ''):
            r, _ = self.run_review(extra=['--channel', 'claude', '--model', model])
            self.assertNotEqual(r.returncode, 0)
        (self.bindir / 'qodercli').unlink()
        (self.bindir / 'git').symlink_to(shutil.which('git'))
        r, out = self.run_review(env_extra={'PATH': str(self.bindir)})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('qodercli not found on PATH', (out / 'status.json').read_text())

    def test_supported_177_regression(self):
        r, out = self.run_review(env_extra={'FAKE_VERSION': '1.1.77'})
        self.assert_success(r, out)

    def test_177_rejects_old_or_missing_model_and_unknown_init(self):
        for field, values in {'FAKE_INIT_MODEL': ('Kimi-K3', 'Other', ''),
                              'FAKE_ASSISTANT_MODEL': ('Kimi-K3', 'Other', ''),
                              'FAKE_INIT_VERSION': ('1.1.78', '', 'noise 1.1.77')}.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    r, out = self.run_review(env_extra={'FAKE_VERSION': '1.1.77', field: value})
                    self.assertNotEqual(r.returncode, 0)
                    self.assertEqual(self.manifest(out)['status'], 'failed')

    def test_version_pin(self):
        for version in ('', '1.1.75', '1.1.78', 'noise 1.1.74', '1.1.74\nnoise'):
            r, out = self.run_review(env_extra={'FAKE_VERSION': version})
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('unsupported Qoder CLI version', (out / 'status.json').read_text())
        self.assertFalse(self.calls())


for name in dir(thin.ThinReviewTests):
    if name.startswith('test_') and name not in QoderReviewTests.__dict__:
        setattr(QoderReviewTests, name, None)

if __name__ == '__main__':
    unittest.main()
