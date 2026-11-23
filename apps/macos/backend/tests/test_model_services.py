"""Canonical public naming, private migration, and historical-client contracts."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config_store import Store, StoreError, atomic, encode
import model_services
import review_services

class NamespaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name).resolve())

    def seed(self, namespace='review_services', mixed=None):
        s = self.store
        profiles = {k: model_services.blank(k) for k in model_services.IDS}
        for key, p in profiles.items():
            p['credential_key'] = namespace+'.'+key+'.value'
            p['credential_paths'] = [str(s.paths['secrets'])]
        if mixed is not None: profiles['token_plan']['credential_paths'] = mixed
        c = {namespace: dict(version=1, active_api='token_plan', profiles=profiles),
             'plugins': {'qwen': copy.deepcopy(profiles['token_plan'])},
             'token_plan_accounts': dict(version=1,auto_failover=False,backup=dict(credential_paths=[str(s.paths['secrets'])],credential_key=namespace+'.token_plan_backup.value'))}
        secret = {namespace:{k:dict(value='fixture-'+k) for k in (*model_services.IDS,'token_plan_backup')},'unrelated':{'keep':True}}
        secret[namespace]['token_plan']['metadata'] = 'keep'
        atomic(s.paths['ai'],encode(c)); atomic(s.paths['secrets'],encode(secret))
        return c,secret

    def test_old_module_is_same_implementation(self):
        self.assertIs(review_services.load, model_services.load)

    def test_owned_three_slots_and_all_references_move_together_restore_pair(self):
        before, secrets = self.seed()
        old_ai, old_secret = self.store.raw('ai'),self.store.raw('secrets')
        state = self.store.read()
        self.assertIn('model_services',state['values'])
        self.assertNotIn('review_services',state['values'])
        state = self.store.save(state['values'],state['fingerprints'])
        c, d = json.loads(self.store.raw('ai')),json.loads(self.store.raw('secrets'))
        self.assertNotIn('review_services',c)
        self.assertEqual(d['unrelated'], secrets['unrelated'])
        self.assertEqual(d['review_services'],{'token_plan':{'metadata':'keep'}})
        for k in (*model_services.IDS,'token_plan_backup'):
            self.assertEqual(d['model_services'][k]['value'],'fixture-'+k)
        for k,p in c['model_services']['profiles'].items():
            self.assertEqual(p['credential_key'],'model_services.'+k+'.value')
        self.assertEqual(c['plugins']['qwen'], c['model_services']['profiles']['token_plan'])
        self.assertEqual(c['claude_relay']['qwen_executor'], 'codex')
        self.assertEqual(c['token_plan_accounts']['backup']['credential_key'],'model_services.token_plan_backup.value')
        self.assertNotIn('fixture-',json.dumps(state))
        self.assertEqual(self.store.paths['secrets'].stat().st_mode & 0o777,0o600)
        self.store.restore(state['fingerprints'])
        self.assertEqual(self.store.raw('ai'),old_ai)
        self.assertEqual(self.store.raw('secrets'),old_secret)

    def test_invalid_and_conflicting_aliases_do_not_write(self):
        c,_ = self.seed()
        for value in (None, [], dict(c['review_services'],active_api='custom_api')):
            bad = dict(c,model_services=value)
            atomic(self.store.paths['ai'],encode(bad))
            before = self.store.raw('ai'),self.store.raw('secrets')
            with self.assertRaises(StoreError): self.store.read()
            self.assertEqual(before,(self.store.raw('ai'),self.store.raw('secrets')))
        both = dict(c,model_services=copy.deepcopy(c['review_services']))
        atomic(self.store.paths['ai'],encode(both))
        state=self.store.read()
        bad=copy.deepcopy(state['values']);bad['review_services']=copy.deepcopy(bad['model_services']);bad['review_services']['active_api']='custom_api'
        with self.assertRaises(StoreError): self.store.outputs(bad,'')

    def test_secret_conflict_is_fail_closed(self):
        _,d=self.seed();d['model_services']={'token_plan':{'value':'different-fixture'}}
        atomic(self.store.paths['secrets'],encode(d));s=self.store.read()
        before=self.store.raw('ai'),self.store.raw('secrets')
        with self.assertRaisesRegex(StoreError,'凭证冲突'):self.store.save(s['values'],s['fingerprints'])
        self.assertEqual(before,(self.store.raw('ai'),self.store.raw('secrets')))

    def test_mixed_paths_keep_declared_external_lookup(self):
        for paths in ([str(self.store.root/'external.json'),str(self.store.paths['secrets'])], [str(self.store.paths['secrets']),'config/secrets.json']):
            with self.subTest(paths=paths):
                self.seed(mixed=paths)
                s=self.store.read();self.store.save(s['values'],s['fingerprints'])
                c=json.loads(self.store.raw('ai'));d=json.loads(self.store.raw('secrets'))
                p=c['model_services']['profiles']['token_plan']
                self.assertEqual(p['credential_paths'],paths)
                self.assertEqual(p['credential_key'],'review_services.token_plan.value')
                self.assertEqual(d['review_services']['token_plan']['value'],'fixture-token_plan')

    def test_old_payload_and_bare_secret_use_active_profile(self):
        self.seed('model_services');s=self.store.read();v=s['values']
        v['review_services']=v.pop('model_services')
        s=self.store.save(v,s['fingerprints'],'replacement-fixture')
        d=json.loads(self.store.raw('secrets'))
        self.assertNotIn('tokenplan',d)
        self.assertEqual(d['model_services']['token_plan']['value'],'replacement-fixture')
        s=self.store.save(s['values'],s['fingerprints'],secret_actions={'token_plan':{'action':'clear'}})
        s=self.store.save(s['values'],s['fingerprints'])
        p=s['values']['model_services']['profiles']['token_plan']
        self.assertEqual(p['credential_paths'],[])
        self.assertFalse(self.store.credential_status(p['credential_paths'],p['credential_key'])[0])

    def test_transaction_failure_rolls_back_both_and_concurrent_save_refused(self):
        self.seed();s=self.store.read();before=self.store.raw('ai'),self.store.raw('secrets')
        real=atomic
        def broken(path,raw,permissions=0o600):
            if path==self.store.paths['ai']:raise OSError('fixture failure')
            return real(path,raw,permissions)
        with patch('config_store.atomic',side_effect=broken),self.assertRaises(StoreError):
            self.store.save(s['values'],s['fingerprints'])
        self.assertEqual(before,(self.store.raw('ai'),self.store.raw('secrets')))
        atomic(self.store.paths['secrets'],self.store.raw('secrets'))
        with self.assertRaisesRegex(StoreError,'其他程序'):self.store.save(s['values'],s['fingerprints'])
        self.assertEqual(before,(self.store.raw('ai'),self.store.raw('secrets')))

    def test_legacy_secret_and_backup_migration_share_pending_document(self):
        s=self.store
        c={'plugins':{'qwen':{'model':'qwen3.8-max','base_url':model_services.DEFAULTS['base_url'],'credential_paths':[str(s.paths['secrets'])]}},'token_plan_accounts':dict(version=1,auto_failover=False,backup=dict(credential_paths=[str(s.paths['secrets'])],credential_key='review_services.token_plan_backup.value'))}
        atomic(s.paths['ai'],encode(c))
        atomic(s.paths['secrets'],encode({'tokenplan':{'value':'old-fixture'},'review_services':{'token_plan_backup':{'value':'backup-fixture'}}}))
        state=s.read();s.save(state['values'],state['fingerprints'],'new-fixture')
        d=json.loads(s.raw('secrets'))
        self.assertEqual(d['tokenplan']['value'],'new-fixture')
        self.assertEqual(d['model_services']['token_plan_backup']['value'],'backup-fixture')

    def test_canonical_profile_still_rejects_old_secret_conflict(self):
        _,d=self.seed('model_services')
        d['review_services']={'token_plan':{'value':'conflicting-fixture'}}
        atomic(self.store.paths['secrets'],encode(d));s=self.store.read()
        with self.assertRaisesRegex(StoreError,'凭证冲突'):self.store.save(s['values'],s['fingerprints'])

    def test_mixed_path_explicit_clear_replace_remove_stale_local_alias(self):
        for action in ('clear','replace'):
            with self.subTest(action=action):
                self.seed(mixed=[str(self.store.paths['secrets']),'project/secret.json'])
                state=self.store.read();request={'action':action}
                if action=='replace':request['value']='different-fixture'
                state=self.store.save(state['values'],state['fingerprints'],secret_actions={'token_plan':request})
                state=self.store.save(state['values'],state['fingerprints'])
                d=json.loads(self.store.raw('secrets'))
                self.assertNotIn('value',d.get('review_services',{}).get('token_plan',{}))
                self.assertEqual(d['model_services']['token_plan'].get('value'), 'different-fixture' if action=='replace' else None)
