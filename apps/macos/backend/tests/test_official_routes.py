import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parents[2]
sys.path[:0] = [
    str(BACKEND),
    str(ROOT / 'adapters/codex/skills/claude-review/scripts'),
    str(ROOT / 'adapters/codex/skills/codex-relay/scripts'),
]
import checks
import implementation
from ai_anti_config import ConfigError
from config_store import Store, StoreError, atomic, encode
from implementation_config import resolve as resolve_implementation
from review_channels import child_environment, resolve as resolve_review


class OfficialRoutes(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        self.store = Store(self.home)
        env = patch.dict(os.environ, HOME=str(self.home))
        env.start()
        self.addCleanup(env.stop)
        self.legacy = {
            'model': 'historical-vendor-id',
            'base_url': 'https://legacy.invalid/anthropic',
            'responses_base_url': 'https://legacy.invalid/v1',
            'credential_paths': ['~/.config/ai-anti/secrets.json'],
            'credential_key': 'tokenplan.value',
            'extension': {'keep': True},
        }
        atomic(self.store.paths['ai'], encode({
            'desktop': {'primary_host': 'codex'},
            'codex_relay': {'review_channel': 'qwen', 'qwen_executor': 'codex', 'secondary_model': 'gpt-6.2-sol'},
            'claude_relay': {'qwen_executor': 'codex'},
            'review': {'channels': ['qwen', 'codex']},
            'plugins': {'qwen': self.legacy},
        }))
        atomic(self.store.paths['secrets'], encode({
            'tokenplan': {'value': 'legacy-fixture'},
            'qwen': {'value': 'old-qwen-fixture'},
            'kimi': {'value': 'old-kimi-fixture'},
            'deepseek': {'value': 'old-deepseek-fixture', 'metadata': 'keep'},
            'unrelated': {'value': 'unrelated-fixture'},
        }))

    def read_config(self):
        return json.loads(self.store.raw('ai'))

    def save(self, actions=None, **edits):
        original = self.store.read()
        return self.store.save(dict(original['values'], **edits), original['fingerprints'], secret_actions=actions)

    def test_routes_and_credentials_are_independent_through_save_restore(self):
        review = {'service': 'kimi', 'model': 'kimi-k3'}
        direct = {'service': 'deepseek', 'model': 'deepseek-flash'}
        original_secrets = self.store.raw('secrets')
        result = self.save(
            channel='official_api', official_review=review,
            implementation_provider='custom_api', direct_implementation=direct,
            actions={'kimi': {'action': 'replace', 'value': 'fixture-kimi'},
                     'deepseek': {'action': 'replace', 'value': 'fixture-deepseek'}},
        )
        self.assertEqual(result['values']['official_review'], review)
        self.assertEqual(result['values']['direct_implementation'], direct)
        self.assertNotIn('fixture-kimi', json.dumps(result))
        self.assertNotIn('fixture-deepseek', json.dumps(result))
        config = self.read_config()
        self.assertEqual(config['plugins']['qwen'], self.legacy)
        self.assertEqual(config['review']['channels'], ['qwen', 'codex'])
        route = resolve_review(config, None, None, self.home, {})
        self.assertEqual(route['base_url'], 'https://api.moonshot.cn/v1')
        self.assertEqual(route['configured_model'], 'kimi-k3')
        self.assertEqual(child_environment(route)['AI_ANTI_QWEN_TOKEN'], 'fixture-kimi')
        explicit = resolve_review(config, 'qwen', None, self.home, {}, executor='claude')
        claude = resolve_review(config, None, None, self.home, {}, allow_official=False)
        self.assertEqual(claude['base_url'], self.legacy['responses_base_url'])
        self.assertEqual(child_environment(explicit)['ANTHROPIC_API_KEY'], 'legacy-fixture')
        self.assertEqual(child_environment(claude)['AI_ANTI_QWEN_TOKEN'], 'legacy-fixture')
        implementation_route = resolve_implementation(config, self.home)
        self.assertEqual(implementation_route['endpoint'], 'https://api.deepseek.com/chat/completions')
        self.assertEqual(implementation.implementation_direct.credential(implementation_route['service']), 'fixture-deepseek')
        self.assertEqual(resolve_implementation(config, self.home, 'codex')['requested_model'], 'gpt-6.2-sol')
        self.assertEqual(resolve_implementation(config, self.home, 'custom_api', 'qwen3.8-max')['service'], 'qwen')
        self.assertEqual(resolve_implementation(config, self.home, explicit_model='deepseek-v4-pro')['service'], 'deepseek')
        with self.assertRaises(ConfigError):
            resolve_implementation(config, self.home, explicit_model='kimi-k3')
        self.store.restore(result['fingerprints'])
        self.assertEqual(self.store.raw('secrets'), original_secrets)
        self.assertNotIn('official_review', self.read_config()['codex_relay'])

    def test_rejected_pairs_and_endpoint_overrides_do_not_read_credentials_or_write(self):
        snapshot = self.store.read()
        before = {key: self.store.raw(key) for key in self.store.paths}
        for bad in ({'service': 'kimi', 'model': 'deepseek-v4-pro'},
                    {'service': 'kimi', 'model': 'kimi-k3', 'base_url': 'https://external.invalid'},
                    {'service': 'qwen', 'model': 'qwen4-max', 'credential_key': 'deepseek.value'}):
            with self.subTest(bad=bad):
                values = dict(snapshot['values'], channel='official_api', official_review=bad)
                with self.assertRaises(StoreError):
                    self.store.save(values, snapshot['fingerprints'],
                                    secret_actions={'kimi': {'action': 'clear'}})
                self.assertEqual({key: self.store.raw(key) for key in self.store.paths}, before)
        config = self.read_config()
        config['codex_relay'].update(review_channel='official_api', official_review={'service': 'kimi', 'model': 'kimi-k3'})
        with patch.object(Path, 'read_text', side_effect=AssertionError('unexpected read')):
            for args in (
                {'responses_url': 'https://external.invalid/v1'}, {'executor': 'claude'},
                {'model': 'qwen4-max'}, {'channel': 'official_api', 'allow_official': False},
            ):
                call = dict(config=config, channel=None, model=None, repo=self.home, families={})
                with self.assertRaises(ConfigError):
                    resolve_review(**dict(call, **args))
            config['plugins']['qwen'].update(
                base_url='https://token-plan.maas.qianwenaiapi.com/apps/anthropic',
                responses_base_url='https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1')
            with self.assertRaisesRegex(ConfigError, '官方 HTTPS'):
                resolve_review(config, 'qwen', None, self.home, {}, executor='codex',
                               responses_url='https://external.invalid/v1')

    def test_clear_only_removes_target_slot_and_old_clients_retain_selection(self):
        self.save(implementation_provider='custom_api',
                  direct_implementation={'service': 'deepseek', 'model': 'deepseek-v4-pro'})
        snapshot = self.store.read()
        values = snapshot['values']
        del values['direct_implementation']
        values['channel'] = 'qoder'
        after = self.store.save(values, snapshot['fingerprints'],
                                secret_actions={'deepseek': {'action': 'clear'}})
        self.assertEqual(after['values']['direct_implementation'], {'service': 'deepseek', 'model': 'deepseek-v4-pro'})
        secrets = json.loads(self.store.raw('secrets'))
        self.assertEqual(secrets['deepseek'], {'metadata': 'keep'})
        self.assertEqual(secrets['qwen']['value'], 'old-qwen-fixture')
        self.assertEqual(secrets['unrelated']['value'], 'unrelated-fixture')
        with self.assertRaisesRegex(checks.CheckError, '实施厂商凭证'):
            checks.local(self.store, ROOT)

    def test_synthetic_review_carries_official_service_and_no_endpoint_override(self):
        self.save(channel='official_api', official_review={'service': 'kimi', 'model': 'kimi-k3'})
        evidence = dict(status='completed', completed=True, mode='diff', channel='qwen',
                        service='kimi', executor='codex', wire_api='responses', requested_model='kimi-k3',
                        actual_model=None, model_evidence='requested_only')

        def fixture_run(argv, cwd, log, **kwargs):
            if 'review.py' in Path(argv[1]).name:
                self.assertEqual(argv[argv.index('--channel') + 1], 'official_api')
                self.assertNotIn('--responses-base-url', argv)
                out = Path(argv[argv.index('--out') + 1])
                out.mkdir()
                for name in ('status', 'manifest'):
                    (out / (name + '.json')).write_text(json.dumps(evidence))
            return 0

        with patch('checks.run', side_effect=fixture_run), patch('checks.discover', return_value='/usr/bin/git'):
            result = checks.real(self.store, ROOT)
        self.assertEqual(result['requested_model'], 'kimi-k3')
        self.assertEqual(result['model_evidence'], 'requested_only')
        values = self.store.read()['values']
        with self.assertRaises(ValueError):
            checks.qwen_evidence(dict(evidence, service='deepseek'), evidence, values)
