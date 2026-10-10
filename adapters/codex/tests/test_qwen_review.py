"""Offline Qwen transport contracts; no endpoint or real credential is used."""
import json
import unittest
import test_thin_review as thin


class QwenReviewTests(thin.ThinReviewTests):
    # Reuse the isolated fixture helpers without inheriting the Claude test cases.
    def setUp(self):
        super().setUp()
        self.credential = self.repo / 'fixture-token.json'
        self.credential.write_text(json.dumps({'tokenplan': {'value': 'OFFLINE_FAKE_TOKEN'}}))
        self.qconfig = {'codex_relay': {'review_channel': 'qwen'}, 'plugins': {'qwen': {
            'model': 'fixture.v1-max', 'base_url': 'https://fixture.invalid/anthropic',
            'credential_paths': ['fixture-token.json']}}}
        self.write_config(self.qconfig)
        self.env['FAKE_ENV_AUDIT'] = '1'

    def test_qwen_plan_and_diff(self):
        for mode in ('plan', 'diff'):
            r, out = self.run_review(mode, fake='noise_repeat')
            self.assert_success(r, out)
            m = self.manifest(out)
            self.assertEqual(m['channel'], 'qwen')
            self.assertEqual(m['requested_model'], 'fixture.v1-max')
            self.assertEqual(m['configured_model'], 'fixture.v1-max')
            self.assertEqual(m['cli_model'], 'sonnet')
            self.assertEqual(m['actual_model'], 'fixture.v1-max')
            status = json.loads((out / 'status.json').read_text())
            self.assertEqual(status['actual_model'], m['actual_model'])
            self.assertEqual(status['limits'], m['limits'])
            self.assertEqual(m['model_family_pattern'], 'fixture\\.v1\\-max')
            self.assertEqual(m['limits']['timeout'], 1800)
            self.assertEqual(m['limits']['idle_timeout'], 300)
            self.assertEqual(len(m['stages']['review']['transport_warnings']), 2)
            self.assertNotIn('OFFLINE_FAKE_TOKEN', (out / 'manifest.json').read_text())
            if mode == 'plan':
                self.assertEqual((out / 'report.md').read_text().count('FAKE REPORT'), 2)

    def test_explicit_channel_model_and_limits(self):
        r, out = self.run_review(extra=['--channel', 'claude'])
        self.assert_success(r, out)
        self.assertEqual(self.manifest(out)['limits']['timeout'], 600)
        r, out = self.run_review(extra=['--timeout', '123', '--idle-timeout', '45'])
        self.assert_success(r, out)
        self.assertEqual(self.manifest(out)['limits']['timeout'], 123)
        self.assertEqual(self.manifest(out)['limits']['idle_timeout'], 45)
        r, _ = self.run_review(extra=['--model', 'opus'])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('plugins.qwen.model', r.stderr)

    def test_prepare_without_credentials_or_cli(self):
        self.credential.unlink()
        (self.bindir / 'claude').unlink()
        r, out = self.run_review(extra=['--prepare-only'])
        self.assert_success(r, out, 'prepared')
        self.assertFalse(self.calls())
        r, out = self.run_review()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('credential unavailable', (out / 'status.json').read_text())

    def test_child_environment(self):
        conflicts = ('ANTHROPIC_API_KEY', 'ANTHROPIC_MODEL', 'ANTHROPIC_SMALL_FAST_MODEL',
                     'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY')
        extra = {key: 'inherited' for key in conflicts}
        extra['HTTPS_PROXY'] = 'https://proxy.invalid'
        r, out = self.run_review(env_extra=extra)
        self.assert_success(r, out)
        env = self.calls()[0]['env']
        for key in conflicts: self.assertNotIn(key, env)
        self.assertTrue(self.calls()[0]['matches_fixture_token'])
        self.assertFalse(self.calls()[0]['conflicting_anthropic_present'])
        self.assertEqual(env['HTTPS_PROXY'], extra['HTTPS_PROXY'])
        for family in ('OPUS', 'SONNET', 'HAIKU'):
            self.assertEqual(env['ANTHROPIC_DEFAULT_' + family + '_MODEL'], 'fixture.v1-max')
        self.assertEqual(extra['ANTHROPIC_API_KEY'], 'inherited')

    def test_invalid_configuration(self):
        for key, values in {'model': [None, '', 3, 'qwen\nmax'],
                            'base_url': ['', 'ftp://fixture.invalid', 'https://', 'https://a:bad', 4],
                            'credential_paths': [None, [], 'file', [3], [''] ]}.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    config = json.loads(json.dumps(self.qconfig))
                    config['plugins']['qwen'][key] = value
                    self.write_config(config)
                    r, _ = self.run_review(extra=['--prepare-only'])
                    self.assertNotEqual(r.returncode, 0)
                    self.assertFalse(self.calls())
        for channel in (3, [], 'unknown'):
            self.write_config({'codex_relay': {'review_channel': channel}})
            r, _ = self.run_review(extra=['--prepare-only'])
            self.assertNotEqual(r.returncode, 0)

    def test_fail_closed_noise_models_and_gates(self):
        for fake in ('noise_wrong', 'noise_extra', 'noise_unknown', 'noise_truncated', 'wrong_model',
                     'denied', 'tool_error', 'partial_read', 'extra_tool', 'broken_json', 'insufficient_quota'):
            with self.subTest(fake=fake):
                before = len(self.calls())
                r, out = self.run_review('plan', fake=fake)
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(len(self.calls()) - before, 1)
                self.assertFalse((out / 'report.md').exists())
        r, out = self.run_review(actual_model='fixtureXv1-max')
        self.assertNotEqual(r.returncode, 0)
        r, out = self.run_review(extra=['--channel', 'claude'], fake='noise_repeat')
        self.assertNotEqual(r.returncode, 0)

    def test_credential_order_and_missing_value(self):
        missing = self.repo / 'missing-token.json'
        self.qconfig['plugins']['qwen']['credential_paths'] = [str(missing), 'fixture-token.json']
        self.write_config(self.qconfig)
        r, out = self.run_review()
        self.assert_success(r, out)
        for value in ({}, {'tokenplan': {'value': 3}}, {'tokenplan': {'value': ''}}, []):
            self.credential.write_text(json.dumps(value))
            r, out = self.run_review()
            self.assertNotEqual(r.returncode, 0)


# Remove inherited tests: this module owns only Qwen contracts, while the original
# module continues to execute the full Claude regression suite.
for name in dir(thin.ThinReviewTests):
    if name.startswith('test_') and name not in QwenReviewTests.__dict__:
        setattr(QwenReviewTests, name, None)

if __name__ == '__main__':
    unittest.main()
