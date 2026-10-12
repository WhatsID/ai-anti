import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config_store import Store, StoreError, atomic, edit_toml
from control import dispatch

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.store = Store(self.root)
    def tearDown(self):
        self.temp.cleanup()
    def snapshot(self):
        return self.store.read()
    def values(self, **changes):
        v = self.snapshot()['values']; v.update(changes); return v
    def write(self, key, value):
        atomic(self.store.paths[key], value if isinstance(value,bytes) else json.dumps(value).encode())
    def save(self, **changes):
        snap = self.snapshot(); v = snap['values']; v.update(changes)
        return self.store.save(v, snap['fingerprints'])
    def test_first_load_is_read_only(self):
        reply = dispatch(self.store, {'action':'load'})
        self.assertEqual(reply['values']['mode'], 'rules')
        self.assertEqual(list(self.root.iterdir()), [])
    def test_load_does_not_read_secret(self):
        self.write('secrets', {'tokenplan': {'value':'SENTINEL'}})
        original = Path.read_bytes
        def denied(path):
            if path == self.store.paths['secrets']:
                self.fail('display read credential bytes')
            return original(path)
        with patch.object(Path, 'read_bytes', denied):
            value = self.snapshot()
        self.assertTrue(value['values']['credential_present'])
        self.assertNotIn('SENTINEL', json.dumps(value))
    def test_unknown_keys_and_fallback_order_preserved(self):
        self.write('ai', {'custom':{'x':1}, 'review': {'channels':['same-family','codex','qwen'], 'other':2}})
        self.save(channel='qwen', qwen_model='qwen-fixture', base_url='https://example.invalid/anthropic')
        value = json.loads(self.store.raw('ai'))
        self.assertEqual(value['custom'], {'x':1})
        self.assertEqual(value['review']['other'], 2)
        self.assertEqual(value['review']['channels'], ['qwen','same-family','codex'])
    def test_primary_only_selected_tool(self):
        self.write('claude', {'model':'old', 'permissions':{'x':True}})
        self.write('codex', b'model = "codex-old"\n[mcp_servers.foo]\ncommand = "safe"\n')
        self.save(primary_model='claude-new')
        self.assertEqual(json.loads(self.store.raw('claude'))['permissions'], {'x':True})
        self.assertEqual(tomllib.loads(self.store.raw('codex').decode())['model'], 'codex-old')
    def test_empty_primary_retains_and_readback(self):
        self.write('claude', {'model':'old'})
        reply = self.save(primary_model='')
        self.assertEqual(reply['values']['primary_model'], 'old')
    def test_empty_json_rejected(self):
        for key in ('ai','claude'):
            with self.subTest(key=key):
                self.write(key, b'')
                with self.assertRaises(StoreError): self.snapshot()
                self.store.paths[key].unlink()
    def test_damaged_json_and_wrong_section_rejected(self):
        for content in (b'{broken', b'[]', b'{"relay":null}', b'{"review":[]}'):
            self.write('ai', content)
            with self.assertRaises(StoreError): self.snapshot()
            self.assertEqual(self.store.raw('ai'), content)
    def test_empty_secret_not_replaced(self):
        self.write('secrets', b'')
        snap = self.snapshot()
        with self.assertRaises(StoreError): self.store.save(snap['values'], snap['fingerprints'], 'new')
        self.assertEqual(self.store.raw('secrets'), b'')
    def test_bad_mode(self):
        self.write('ai', {'relay': {'default_mode':'mystery'}})
        with self.assertRaises(StoreError): self.snapshot()
    def test_canonical_modes_and_invalid_values(self):
        from config_store import MODES
        self.assertEqual(MODES, ('solo', 'auto', 'rules', 'force'))
        for mode in MODES:
            self.assertEqual(self.save(mode=mode)['values']['mode'], mode)
            self.assertEqual(json.loads(self.store.raw('ai'))['relay']['default_mode'], mode)
        for mode in ('required', 'mystery', '', None, [], {}, 1, True):
            with self.subTest(mode=mode):
                snap = self.snapshot()
                with self.assertRaisesRegex(StoreError, 'solo/auto/rules/force'):
                    self.store.save(dict(snap['values'], mode=mode), snap['fingerprints'])
                self.write('ai', {'relay': {'default_mode': mode}})
                raw = self.store.raw('ai')
                with self.assertRaisesRegex(StoreError, 'solo/auto/rules/force'): self.snapshot()
                self.assertEqual(self.store.raw('ai'), raw)
                self.write('ai', {'relay': {'default_mode': 'rules'}})
    def test_restore_invalid_mode_preserves_all_bytes(self):
        for mode in ('required', '', None, [], {}, 1, True):
            with self.subTest(mode=mode):
                reply = self.save(mode='rules', primary_model='fixture')
                backup = self.store.desktop/'last-save.json'
                record = json.loads(backup.read_bytes())
                record['before']['ai'] = json.dumps({'relay': {'default_mode': mode}}).encode().hex()
                backup.write_text(json.dumps(record))
                before = {k: p.read_bytes() if p.exists() else None for k,p in self.store.paths.items()}
                backup_bytes = backup.read_bytes()
                with patch('config_store.atomic', side_effect=AssertionError('unexpected write')):
                    with self.assertRaisesRegex(StoreError, 'solo/auto/rules/force.*新保存.*恢复点'):
                        self.store.restore(reply['fingerprints'])
                self.assertEqual({k: p.read_bytes() if p.exists() else None for k,p in self.store.paths.items()}, before)
                self.assertEqual(backup.read_bytes(), backup_bytes)
    def test_conflict_no_overwrite(self):
        snap = self.snapshot(); self.write('claude', {'external':1})
        with self.assertRaisesRegex(StoreError, '刷新'): self.store.save(snap['values'], snap['fingerprints'])
        self.assertEqual(json.loads(self.store.raw('claude')), {'external':1})
    def test_new_key_first_private_and_unknown_preserved(self):
        self.write('ai', {'plugins':{'qwen': {'credential_paths':['~/first.json', '~/.config/ai-anti/secrets.json', '~/other.json']}}})
        self.write('secrets', {'unrelated':123, 'tokenplan':{'extra':True, 'value':'old'}})
        snap = self.snapshot()
        reply = self.store.save(snap['values'], snap['fingerprints'], 'NEW_SECRET')
        data = json.loads(self.store.raw('secrets'))
        self.assertEqual(data, {'unrelated':123, 'tokenplan': {'extra':True,'value':'NEW_SECRET'}})
        self.assertEqual(reply['values']['credential_paths'], ['~/.config/ai-anti/secrets.json','~/first.json','~/other.json'])
        self.assertNotIn('NEW_SECRET', json.dumps(reply))
        self.assertEqual(self.store.paths['secrets'].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.desktop.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.store.desktop/'last-save.json').stat().st_mode & 0o777, 0o600)
    def test_restore_and_second_restore_denied(self):
        self.write('claude', {'model':'before'})
        reply = self.save(primary_model='after')
        restored = self.store.restore(reply['fingerprints'])
        self.assertEqual(restored['values']['primary_model'], 'before')
        self.assertFalse(restored['can_restore'])
        with self.assertRaises(StoreError): self.store.restore(restored['fingerprints'])
    def test_secret_restore(self):
        self.write('secrets', {'tokenplan': {'value':'old'}})
        snap = self.snapshot(); after = self.store.save(snap['values'],snap['fingerprints'],'new')
        self.store.restore(after['fingerprints'])
        self.assertEqual(json.loads(self.store.raw('secrets'))['tokenplan']['value'], 'old')
    def test_restore_denies_external_edit(self):
        reply = self.save(primary_model='after')
        self.write('claude', {'model':'external'})
        with self.assertRaises(StoreError): self.store.restore(self.snapshot()['fingerprints'])
        self.assertEqual(json.loads(self.store.raw('claude'))['model'], 'external')
    def test_multi_file_failure_rolls_back(self):
        self.write('ai', {'custom':1}); self.write('claude', {'model':'old'})
        snap = self.snapshot(); original_ai = self.store.raw('ai')
        real_atomic = atomic
        def fail(path, raw, permissions=0o600):
            if path == self.store.paths['claude']: raise OSError('sensitive error')
            real_atomic(path,raw,permissions)
        with patch('config_store.atomic', side_effect=fail):
            with self.assertRaisesRegex(StoreError,'已回滚'): self.store.save(self.values(primary_model='new'), snap['fingerprints'])
        self.assertEqual(self.store.raw('ai'), original_ai)
        self.assertEqual(json.loads(self.store.raw('claude'))['model'],'old')
    def test_failure_does_not_clobber_external_mutation(self):
        self.write('ai', {'custom':1}); self.write('claude', {'model':'old'})
        snap = self.snapshot()
        real_atomic = atomic
        def fail(path, raw, permissions=0o600):
            if path == self.store.paths['claude']:
                real_atomic(self.store.paths['ai'], b'{"external":true}')
                raise OSError('error')
            real_atomic(path,raw,permissions)
        with patch('config_store.atomic', side_effect=fail):
            with self.assertRaisesRegex(StoreError,'部分状态'): self.store.save(self.values(primary_model='new'), snap['fingerprints'])
        self.assertEqual(json.loads(self.store.raw('ai')), {'external':True})
    def test_symlink_target_and_parent_refused(self):
        actual = self.root/'actual'; actual.write_text('{}')
        self.store.paths['ai'].parent.mkdir(parents=True)
        self.store.paths['ai'].symlink_to(actual)
        with self.assertRaises(StoreError): self.snapshot()
        self.store.paths['ai'].unlink()
        self.store.paths['claude'].parent.symlink_to(self.root)
        with self.assertRaises(StoreError): self.snapshot()
    def test_url_userinfo_and_query_never_displayed(self):
        for url in ('https://SECRET@localhost','https://example.invalid?token=SECRET'):
            self.write('ai', {'plugins': {'qwen': {'base_url':url}}})
            with self.assertRaises(StoreError) as ctx: self.snapshot()
            self.assertNotIn('SECRET', str(ctx.exception))
    def test_invalid_combination_and_model(self):
        for changes in ({'host':'codex','channel':'codex'}, {'reviewer_model':'x\ny'}, {'mode':'bad'}):
            with self.subTest(changes=changes), self.assertRaises(StoreError): self.save(**changes)
    def test_fixture_external_credentials_denied(self):
        self.write('ai', {'plugins': {'qwen': {'credential_paths':['/outside/secret.json']}}})
        with self.assertRaises(StoreError): self.snapshot()
    def test_credential_parent_escape_denied(self):
        for path in ('~/../outside.json', str(self.root)+'/../outside.json'):
            with self.subTest(path=path), self.assertRaises(StoreError): self.store.credential_path(path)
    def test_relative_credentials_preserved_and_not_read_from_cwd(self):
        self.write('ai', {'plugins': {'qwen': {'credential_paths': ['project/credentials.json', '~/.config/ai-anti/secrets.json']}}})
        self.write('secrets', {'tokenplan': {'value': 'global'}})
        original = Path.read_bytes
        def reject_relative(path):
            if path.name == 'credentials.json': self.fail('relative credentials read from cwd')
            return original(path)
        with patch.object(Path, 'read_bytes', reject_relative):
            snap = self.snapshot()
            self.assertTrue(snap['values']['relative_credentials'])
            self.assertTrue(snap['values']['credential_present'])
            self.assertEqual(self.store.credential_status(snap['values']['credential_paths']), (True,self.store.paths['secrets']))
            self.store.save(snap['values'],snap['fingerprints'])
        self.assertEqual(json.loads(self.store.raw('ai'))['plugins']['qwen']['credential_paths'][0], 'project/credentials.json')
        self.assertIsNone(self.store.credential_path('project/credentials.json'))
    def test_signature_invalidates_credentials_stat(self):
        self.write('secrets', {'tokenplan': {'value':'old'}})
        before=self.snapshot()['signature']; self.write('secrets', {'tokenplan': {'value':'changed'}})
        self.assertNotEqual(before,self.snapshot()['signature'])
    def test_toml_root_insertion_preserves_other_semantics(self):
        raw=b'# comment\n[model_providers.test]\nname = "test"\nmodel = "nested"\n'
        output=edit_toml(raw, 'gpt-6-astra')
        self.assertTrue(output.startswith(b'model = "gpt-6-astra"'))
        data=tomllib.loads(output.decode()); self.assertEqual(data['model_providers']['test']['model'],'nested')
    def test_toml_replace_comments_quoting_and_complex_reject(self):
        out=edit_toml(b'model = "old" # keep\n[x]\na = 1\n','new"quote')
        self.assertIn(b'# keep',out); self.assertEqual(tomllib.loads(out.decode())['model'],'new"quote')
        for raw in (b'model = """multi\nline"""\n', b'model = 1\n', b'model = "bad\n'):
            with self.assertRaises(StoreError): edit_toml(raw,'new')

if __name__ == '__main__': unittest.main()
