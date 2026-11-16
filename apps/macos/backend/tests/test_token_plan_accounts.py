import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config_store import Store, StoreError

class AccountContract(unittest.TestCase):
    def test_roundtrip_and_independent_secrets(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root).resolve())
            state = store.read()
            v = state['values']
            v['token_plan_accounts'] = {'version':1, 'auto_failover':True, 'backup':{'credential_paths':[], 'credential_key':'model_services.token_plan_backup.value'}}
            actions = {'token_plan':{'action':'replace','value':'primary-fixture'}, 'token_plan_backup':{'action':'replace','value':'backup-fixture'}}
            saved = store.save(v, state['fingerprints'], secret_actions=actions)
            self.assertTrue(saved['values']['token_plan_accounts']['auto_failover'])
            secrets = json.loads(store.paths['secrets'].read_text())
            self.assertEqual(secrets['model_services']['token_plan']['value'], 'primary-fixture')
            self.assertEqual(secrets['model_services']['token_plan_backup']['value'], 'backup-fixture')
            self.assertNotIn('primary-fixture', json.dumps(saved))
            old = copy.deepcopy(saved['values']); old.pop('token_plan_accounts')
            saved = store.save(old, saved['fingerprints'])
            self.assertTrue(saved['values']['token_plan_accounts']['auto_failover'])
            self.assertEqual(store.paths['secrets'].stat().st_mode & 0o777, 0o600)

    def test_invalid_public_config_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            s = Store(Path(root).resolve()); st = s.read(); v = st['values']
            v['token_plan_accounts'] = {'version':1, 'auto_failover':'yes', 'backup':{}}
            with self.assertRaises(StoreError): s.save(v, st['fingerprints'])

    def test_strict_structure_and_null_defaults(self):
        from token_plan_accounts import normalize
        default = normalize()
        self.assertEqual(normalize(None),default)
        bad = [[],{},dict(default,version=True),dict(default,version=2),dict(default,auto_failover=1),dict(default,unknown=True),dict(default,backup={'credential_paths':[],'credential_key':'model_services.custom_api.value'}),dict(default,backup={'credential_paths':['../x'],'credential_key':'model_services.token_plan_backup.value'})]
        for value in bad:
            with self.subTest(value=value),self.assertRaises(ValueError): normalize(value)

    def test_same_credentials_and_missing_backup_rejected_atomically(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root).resolve()); st=store.read();v=st['values']
            v['token_plan_accounts']['auto_failover']=True
            for actions in ({}, {'token_plan':{'action':'replace','value':'same'},'token_plan_backup':{'action':'replace','value':'same'}}):
                with self.assertRaises(StoreError): store.save(v,st['fingerprints'],secret_actions=actions)
                self.assertFalse(store.paths['ai'].exists());self.assertFalse(store.paths['secrets'].exists())

    def test_control_roundtrip_restore_disable_and_backup_signature(self):
        from control import dispatch
        with tempfile.TemporaryDirectory() as root:
            store=Store(Path(root).resolve());st=store.read();v=st['values']
            # Establish an old snapshot without dual-account configuration.
            original={'plugins': {'qwen': {'model':'old','base_url':'https://custom.invalid'}}}
            store.paths['ai'].parent.mkdir(parents=True)
            store.paths['ai'].write_text(json.dumps(original));st=store.read();v=st['values']
            v['token_plan_accounts']['auto_failover']=True
            saved=dispatch(store,dict(action='save',values=v,fingerprints=st['fingerprints'],secret_actions={'token_plan':{'action':'replace','value':'one'},'token_plan_backup':{'action':'replace','value':'two'}}))
            self.assertTrue(saved['values']['token_plan_accounts']['auto_failover'])
            restored=dispatch(store,dict(action='restore',fingerprints=saved['fingerprints']))
            self.assertFalse(restored['values']['token_plan_accounts']['auto_failover'])
            self.assertEqual(json.loads(store.raw('ai')),original)
            external=store.root/'backup.json';external.write_text('{}')
            v=restored['values'];v['token_plan_accounts']['backup']['credential_paths']=[str(external)]
            saved=store.save(v,restored['fingerprints']);external.write_text('{"changed":true}')
            self.assertNotEqual(saved['signature'],store.read()['signature'])
