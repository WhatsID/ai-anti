import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config_store import Store, StoreError, atomic

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name).resolve())
    def tearDown(self): self.temp.cleanup()
    def seed(self, qwen):
        atomic(self.store.paths['ai'], json.dumps({'plugins': {'qwen': qwen}}).encode())
    def test_migration_is_memory_only_and_preserves_unknown(self):
        self.seed(dict(model='old-vendor-model', base_url='https://unknown.invalid/v1', credential_paths=['~/external.json']))
        before = self.store.raw('ai')
        v = self.store.read()['values']
        self.assertIn('review_services', v)
        self.assertEqual(v['review_services']['active_api'], 'custom_api')
        p = v['review_services']['profiles']
        self.assertEqual(p['custom_api']['credential_key'], 'tokenplan.value')
        self.assertEqual(p['custom_api']['model'], 'old-vendor-model')
        self.assertEqual(p['token_plan']['credential_paths'], [])
        self.assertEqual(self.store.raw('ai'), before)
    def test_independent_replace_clear_no_legacy_resurrection(self):
        atomic(self.store.paths['secrets'], b'{"tokenplan":{"value":"legacy"}}')
        s = self.store.read(); v = s['values']
        self.assertIn('review_services', v)
        p = v['review_services']['profiles']
        p['token_plan']['model'] = 'qwen3.8-max'
        s = self.store.save(v, s['fingerprints'], secret_actions={'token_plan': {'action':'replace','value':'one'}, 'custom_api': {'action':'replace','value':'two'}})
        d = json.loads(self.store.raw('secrets'))
        self.assertEqual(d['review_services']['token_plan']['value'], 'one')
        self.assertEqual(d['review_services']['custom_api']['value'], 'two')
        s = self.store.save(s['values'], s['fingerprints'], secret_actions={'custom_api': {'action':'clear'}})
        p = s['values']['review_services']['profiles']
        self.assertEqual(p['custom_api']['credential_paths'], [])
        self.assertFalse(self.store.credential_status(p['custom_api']['credential_paths'], p['custom_api']['credential_key'])[0])
        self.assertEqual(json.loads(self.store.raw('secrets'))['tokenplan']['value'], 'legacy')
    def test_projection_conflict_and_future_version_fail_closed(self):
        s = self.store.read()
        self.assertIn('review_services', s['values'])
        s = self.store.save(s['values'], s['fingerprints'])
        a = json.loads(self.store.raw('ai')); a['plugins']['qwen']['model'] = 'mismatch'
        atomic(self.store.paths['ai'], json.dumps(a).encode())
        with self.assertRaisesRegex(StoreError, 'model.*同步'): self.store.read()
        a['review_services']['version'] = True
        atomic(self.store.paths['ai'], json.dumps(a).encode())
        with self.assertRaises(StoreError): self.store.read()
    def test_exact_endpoint_migration(self):
        for url, expected in [('https://token-plan.maas.qianwenaiapi.com/apps/anthropic','token_plan'),('https://token-plan.maas.qianwenaiapi.com/apps/anthropic/','custom_api'),('', 'custom_api')]:
            self.seed(dict(model='deepseek-v4-pro',base_url=url))
            self.assertEqual(self.store.read()['values'].get('review_services',{}).get('active_api'),expected)

    def test_profile_validation_actions_and_inactive_signature(self):
        s = self.store.read()
        baseline = self.store.raw('ai')
        for action in ([], {'unknown': {'action':'clear'}}, {'token_plan':{'action':'replace','value':''}}, {'token_plan':{'action':'clear','value':'secret'}}, {'token_plan':{'action':'bogus'}}):
            with self.subTest(action=action), self.assertRaises(StoreError): self.store.save(s['values'],s['fingerprints'],secret_actions=action)
            self.assertEqual(self.store.raw('ai'), baseline)
        for field, value in [('model',1), ('base_url','https://user:password@fixture.invalid'), ('credential_key','arbitrary.value'), ('credential_paths',['../escape'])]:
            v = copy.deepcopy(s['values']); v['review_services']['profiles']['token_plan'][field] = value
            with self.subTest(field=field), self.assertRaises(StoreError): self.store.save(v,s['fingerprints'],secret_actions={})
        # Inactive profile is covered by stat-only inspection too.
        external = self.store.root/'external.json'; external.write_text('{"tokenplan":{"value":"fake"}}')
        v = s['values']; v['review_services']['profiles']['token_plan']['credential_paths'] = [str(external)]
        s = self.store.save(v,s['fingerprints'],secret_actions={})
        from unittest.mock import patch
        original = Path.read_bytes
        def guarded(path):
            if path == external or path == self.store.paths['secrets']: raise AssertionError('inspect read secret bytes')
            return original(path)
        with patch.object(Path,'read_bytes',guarded): self.store.read()
        external.write_text('{"tokenplan":{"value":"changed-fixture"}}')
        self.assertNotEqual(s['signature'],self.store.read()['signature'])

    def test_restore_rejects_corrupt_service_before_writing(self):
        s = self.store.read(); s = self.store.save(s['values'],s['fingerprints'],secret_actions={})
        v = copy.deepcopy(s['values']); v['mode'] = 'force'
        s = self.store.save(v,s['fingerprints'],secret_actions={})
        backup = self.store.desktop/'last-save.json'; record = json.loads(backup.read_bytes())
        before = json.loads(bytes.fromhex(record['before']['ai']))
        before['review_services']['version'] = 2
        record['before']['ai'] = json.dumps(before).encode().hex(); atomic(backup,json.dumps(record).encode())
        unchanged = self.store.raw('ai')
        with self.assertRaises(StoreError): self.store.restore(s['fingerprints'])
        self.assertEqual(self.store.raw('ai'),unchanged)

    def test_old_secret_request_cannot_create_two_legacy_profiles(self):
        self.seed(dict(model='qwen3.8-max',base_url='https://token-plan.maas.qianwenaiapi.com/apps/anthropic',credential_paths=['~/external.json']))
        s = self.store.read(); s = self.store.save(s['values'],s['fingerprints'],secret_actions={})
        v = copy.deepcopy(s['values']); v['review_services']['active_api'] = 'custom_api'
        s = self.store.save(v,s['fingerprints'],secret_actions={})
        s = self.store.save(s['values'],s['fingerprints'],'new-fixture')
        p = s['values']['review_services']['profiles']
        self.assertEqual(p['token_plan']['credential_key'],'tokenplan.value')
        self.assertEqual(p['custom_api']['credential_key'],'review_services.custom_api.value')
        self.assertEqual(json.loads(self.store.raw('secrets'))['review_services']['custom_api']['value'],'new-fixture')
