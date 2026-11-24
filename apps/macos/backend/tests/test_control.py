import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config_store import Store, StoreError, atomic, edit_toml, encode
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

    def test_inspect_returns_signature_and_fingerprints(self):
        self.write('ai', {})
        self.write('secrets', {'tokenplan': {'value': 'fixture-secret'}})
        snap = self.snapshot()
        with patch.object(Path, 'read_bytes', autospec=True, side_effect=Path.read_bytes) as read:
            inspected = dispatch(self.store, {'action': 'inspect'})
        self.assertEqual(inspected, {k: snap[k] for k in ('signature', 'fingerprints')})
        self.assertTrue(inspected['fingerprints']['secrets'].startswith('stat:'))
        self.assertNotIn(self.store.paths['secrets'], [call.args[0] for call in read.call_args_list])

    def test_read_reuses_bytes_for_values_fingerprints_and_signature_during_external_edit(self):
        original = b'{"relay":{"default_mode":"rules"}}'
        external = b'{"relay":{"default_mode":"solo"}}'
        self.write('ai', original)
        self.write('claude', {'model': 'fixture-claude'})
        self.write('codex', b'model = "fixture-codex"\n')
        before = self.snapshot()
        read_bytes = Path.read_bytes
        reads = []

        def read_then_change(path):
            raw = read_bytes(path)
            if path == self.store.paths['ai'] and path not in reads:
                self.write('ai', external)
            reads.append(path)
            return raw

        with patch.object(Path, 'read_bytes', read_then_change):
            snap = self.snapshot()
        with self.assertRaisesRegex(StoreError, '配置已被其他程序修改.*ai-anti 配置.*请刷新后重试'):
            self.store.save(dict(snap['values'], mode='force'), snap['fingerprints'])
        self.assertEqual(self.store.raw('ai'), external)
        self.assertCountEqual(reads, [self.store.paths[k] for k in ('ai', 'claude', 'codex')])
        self.assertEqual(snap['values']['mode'], 'rules')
        self.assertEqual(snap['fingerprints']['ai'], hashlib.sha256(original).hexdigest())
        self.assertEqual(snap['fingerprints'], before['fingerprints'])
        self.assertEqual(snap['signature'], before['signature'])

    def test_missing_and_empty_config_have_different_fingerprints(self):
        missing = self.snapshot()
        self.assertTrue(all(value is None for value in missing['fingerprints'].values()))
        self.write('codex', b'')
        empty = self.snapshot()
        self.assertEqual(empty['values']['codex_model'], missing['values']['codex_model'])
        self.assertEqual(empty['fingerprints']['codex'], hashlib.sha256(b'').hexdigest())
        self.assertNotEqual(empty['signature'], missing['signature'])
        for key in ('ai', 'claude'):
            with self.subTest(key=key):
                self.write(key, b'')
                with self.assertRaisesRegex(StoreError, 'JSON 配置损坏'):
                    self.snapshot()
                self.store.paths[key].unlink()

    def test_agy_host_roundtrip_keeps_other_host_files(self):
        self.write('claude', b'{"model":"fixture-claude","permissions":{}}\n')
        self.write('codex', b'model = "fixture-codex" # retain\n')
        initial = self.snapshot()
        for host, channel, primary in (('agy', 'codex', ''), ('codex', 'claude', 'fixture-codex'),
                                       ('claude', 'codex', 'fixture-claude')):
            with self.subTest(host=host):
                saved = self.save(host=host, channel=channel, primary_model=primary)
                self.assertEqual((saved['values']['host'], saved['values']['channel'],
                                  saved['values']['primary_model']), (host, channel, primary))
                self.assertEqual(json.loads(self.store.raw('ai'))['desktop']['primary_host'], host)
                self.assertNotIn('agy_relay', json.loads(self.store.raw('ai')))
                for key in ('claude', 'codex'):
                    self.assertEqual(saved['fingerprints'][key], initial['fingerprints'][key])
        for changes, message in (
            ({'channel': 'qwen'}, 'AGY 主模型仅支持 Codex 评审'),
            ({'primary_model': 'external-agy-id'}, 'AGY 主模型 ID 由 AGY 设置管理'),
            ({'host_updates': {'agy': {'channel': 'codex'}}}, 'host_updates 主模型无效'),
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(StoreError, message):
                self.save(**({'host': 'agy', 'channel': 'codex', 'primary_model': ''} | changes))
        updated = self.save(host='agy', channel='codex', primary_model='',
                            host_updates={'codex': {'primary_model': 'edited-codex'}})
        self.assertEqual(updated['values']['codex_model'], 'edited-codex')
        self.assertEqual(updated['fingerprints']['claude'], initial['fingerprints']['claude'])

    def test_agy_single_key_edits_preserve_other_route_and_efforts(self):
        review = {'provider': None, 'model': 'old-review', 'effort': 'xhigh', 'high_risk_effort': 'ultra'}
        implementation = {'provider': 'codex', 'model': 'old-impl', 'effort': 'xhigh'}
        self.write('ai', {'agy_relay': {'review': review, 'implementation': implementation}})
        for changed_key, model_key, new_model in (
            ('review', 'agy_review_model', 'new-review'),
            ('implementation', 'agy_implementation_model', 'new-impl'),
            ('review', 'agy_review_model', ''),
        ):
            with self.subTest(model_key=model_key, new_model=new_model):
                snap = self.snapshot()
                values = {key: val for key, val in snap['values'].items()
                          if key not in ('agy_review_model', 'agy_implementation_model')}
                saved = self.store.save(dict(values, **{model_key: new_model}), snap['fingerprints'])
                route = review if changed_key == 'review' else implementation
                if new_model:
                    route['model'] = new_model
                else:
                    route.pop('model')
                self.assertEqual(saved['values'][model_key], new_model)
                self.assertEqual(json.loads(self.store.raw('ai'))['agy_relay'],
                                 {'review': review, 'implementation': implementation})
        before = self.snapshot()
        raw = self.store.raw('ai')
        with self.assertRaisesRegex(StoreError, r'应用不清空 AGY 实施配置.*agy_relay\.implementation'):
            self.save(agy_implementation_model='', host_updates={'codex': {'primary_model': 'must-not-write'}})
        self.assertEqual(self.snapshot()['fingerprints'], before['fingerprints'])
        self.assertEqual(self.store.raw('ai'), raw)

    def test_agy_new_routes_and_noop_apply(self):
        import agy_relay

        ai = {'agy_relay': {'review': {'provider': None, 'model': None, 'effort': 'high'},
                            'implementation': {'provider': 'codex', 'model': 'old', 'effort': None}}}
        original = encode(ai)
        self.assertIs(agy_relay.apply(ai, {'agy_review_model': '', 'agy_implementation_model': 'old'}, StoreError), False)
        self.assertEqual(encode(ai), original)
        for initial in ({}, {'agy_relay': None}):
            with self.subTest(initial=initial):
                data = copy.deepcopy(initial)
                self.assertIs(agy_relay.apply(data, {'agy_implementation_model': ''}, StoreError), False)
                self.assertEqual(data, initial)
                self.write('ai', initial)
                snap = self.snapshot()
                for key, route in (('agy_implementation_model', 'implementation'), ('agy_review_model', 'review')):
                    values = {k: v for k, v in snap['values'].items()
                              if k not in ('agy_review_model', 'agy_implementation_model')}
                    snap = self.store.save(dict(values, **{key: route + '-id'}), snap['fingerprints'])
                self.assertEqual(json.loads(self.store.raw('ai'))['agy_relay'], {
                    'implementation': {'provider': 'codex', 'model': 'implementation-id'},
                    'review': {'provider': 'codex', 'model': 'review-id'},
                })
        for changes, message in (
            ({'unknown': 'model'}, '变更字段无效'),
            ({'agy_review_model': None}, 'agy_review_model'),
            ({'agy_implementation_model': False}, 'agy_implementation_model'),
            ({'agy_review_model': 'valid', 'agy_implementation_model': 'two words'}, r'agy_relay\.implementation\.model'),
            ({'agy_review_model': 'white\u2003space'}, r'agy_relay\.review\.model'),
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(StoreError, message):
                agy_relay.apply(ai, changes, StoreError)
            self.assertEqual(encode(ai), original)

    def test_agy_soft_error_allows_unrelated_save_and_byte_exact_restore(self):
        raw = b'{ "agy_relay" : {"review": {"model": "valid"}, "implementation": {"future": true}} }\n'
        self.write('ai', raw)
        snap = self.snapshot()
        values = dict(snap['values'], mode='auto')
        self.assertEqual(values.pop('agy_review_model'), '')
        self.assertEqual(values.pop('agy_implementation_model'), '')
        self.assertIn('agy_relay.implementation', values['agy_relay_error'])
        self.assertEqual(values['agy_relay']['review'],
                         {'provider': 'codex', 'model': None, 'effort': None, 'high_risk_effort': None})
        self.assertIsNone(values['agy_relay']['implementation'])
        with self.assertRaisesRegex(StoreError, '不能切换为 AGY 主模型'):
            self.store.save(dict(values, host='agy'), snap['fingerprints'])
        with self.assertRaisesRegex(StoreError, r'AGY 接力配置无效.*config\.json.*agy_relay'):
            self.store.save(dict(values, agy_review_model='new'), snap['fingerprints'])
        self.assertEqual(self.store.raw('ai'), raw)
        saved = self.store.save(values, snap['fingerprints'])
        self.assertEqual(saved['values']['mode'], 'auto')
        self.assertEqual(json.loads(self.store.raw('ai'))['agy_relay'], json.loads(raw)['agy_relay'])
        restored = self.store.restore(saved['fingerprints'])
        self.assertEqual(self.store.raw('ai'), raw)
        self.assertEqual(restored['values']['agy_relay_error'], values['agy_relay_error'])

    def test_restore_agy_host_and_reject_unknown_host(self):
        raw = b'{ "desktop" : {"primary_host": "agy"}, "agy_relay": [] }\n'
        self.write('ai', raw)
        snap = self.snapshot()
        values = {k: v for k, v in snap['values'].items()
                  if k not in ('agy_review_model', 'agy_implementation_model', 'agy_dual_plan_enabled')}
        saved = self.store.save(dict(values, host='claude'), snap['fingerprints'])
        backup = self.store.desktop / 'last-save.json'
        record = json.loads(backup.read_bytes())
        invalid = copy.deepcopy(record)
        invalid['before']['ai'] = raw.replace(b'"agy"', b'"unknown"').hex()
        atomic(backup, encode(invalid))
        before = self.store.raw('ai')
        with self.assertRaisesRegex(StoreError, '恢复记录主模型无效'):
            self.store.restore(saved['fingerprints'])
        self.assertEqual(self.store.raw('ai'), before)
        atomic(backup, encode(record))
        restored = self.store.restore(saved['fingerprints'])
        self.assertEqual(restored['values']['host'], 'agy')
        self.assertTrue(restored['values']['agy_relay_error'])
        self.assertEqual(self.store.raw('ai'), raw)

    def test_agy_dual_plan_updates_only_enabled_and_creates_defaults(self):
        defaults = {'enabled': False, 'partner': 'claude', 'model': None, 'effort': None,
                    'synthesizer': 'agy', 'on_failure': 'stop'}
        review = {'provider': 'codex', 'model': 'review-id'}
        implementation = {'provider': 'codex', 'model': 'implementation-id', 'effort': 'high'}
        relay = {'review': review, 'implementation': implementation}
        self.write('ai', {'agy_relay': relay})
        self.assertEqual(self.values()['agy_dual_plan'], defaults)
        self.save(host='agy', agy_dual_plan_enabled=False)
        self.assertEqual(json.loads(self.store.raw('ai'))['agy_relay'], relay)
        enabled = self.save(agy_dual_plan_enabled=True)
        self.assertIs(enabled['values']['agy_dual_plan_enabled'], True)
        self.assertEqual(json.loads(self.store.raw('ai'))['agy_relay'],
                         dict(relay, dual_plan=dict(defaults, enabled=True)))

        plan = dict(defaults, enabled=True, model='haiku', effort='xhigh', synthesizer='claude', on_failure='fallback')
        self.write('ai', {'agy_relay': dict(relay, dual_plan=plan)})
        disabled = self.save(agy_dual_plan_enabled=False)
        self.assertIs(disabled['values']['agy_dual_plan_enabled'], False)
        self.assertEqual(json.loads(self.store.raw('ai'))['agy_relay'],
                         dict(relay, dual_plan=dict(plan, enabled=False)))
        for invalid in (0, 'true', None):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(StoreError, 'agy_dual_plan_enabled'):
                self.save(agy_dual_plan_enabled=invalid)

    def test_agy_dual_plan_error_isolated_and_restore_keeps_raw_bytes(self):
        raw = b'{ "agy_relay" : {"review":{"model":"review-id"},"dual_plan":{"enabeld":true}} }\n'
        self.write('ai', raw)
        snapshot = self.snapshot()
        values = dict(snapshot['values'], host='agy', mode='auto')
        self.assertIs(values.pop('agy_dual_plan_enabled'), False)
        self.assertIn('agy_relay.dual_plan has unknown keys', values['agy_dual_plan_error'])
        self.assertEqual(values['agy_relay_error'], '')
        self.assertEqual(values['agy_review_model'], 'review-id')
        with self.assertRaisesRegex(StoreError, 'agy_relay.dual_plan'):
            self.store.save(dict(values, agy_dual_plan_enabled=True), snapshot['fingerprints'])
        self.assertEqual(self.store.raw('ai'), raw)
        saved = self.store.save(values, snapshot['fingerprints'])
        self.assertEqual(saved['values']['host'], 'agy')
        self.assertEqual(json.loads(self.store.raw('ai'))['agy_relay'], json.loads(raw)['agy_relay'])
        restored = self.store.restore(saved['fingerprints'])
        self.assertEqual(restored['values']['agy_dual_plan_error'], values['agy_dual_plan_error'])
        self.assertEqual(self.store.raw('ai'), raw)

    def test_dual_plan_absent_or_null_reads_disabled(self):
        for data in ({}, {'claude_relay': None}, {'claude_relay': {}}, {'claude_relay': {'dual_plan': None}}):
            with self.subTest(data=data):
                self.write('ai', data)
                values = self.values(mode='auto')
                self.assertIs(values.pop('dual_plan_enabled'), False)
                self.assertEqual(values['dual_plan_error'], '')
                self.assertEqual(values['dual_plan'], {
                    'enabled': False, 'partner': 'agy', 'model': None, 'effort': None,
                    'synthesizer': 'claude', 'on_failure': 'stop',
                })
                self.store.save(values, self.snapshot()['fingerprints'])
                config = json.loads(self.store.raw('ai'))
                self.assertEqual(config['claude_relay'], dict(data.get('claude_relay') or {}, qwen_executor='codex'))

    def test_invalid_dual_plan_allows_unrelated_save_and_byte_exact_restore(self):
        self.write('claude', {'model': 'old-claude'})
        self.write('codex', b'model = "old-codex"\n')
        for key, invalid in (('enabled', 1), ('effort', 'XHIGH'), ('model', 'a b')):
            with self.subTest(key=key):
                plan = {'model': 'fixture-model', 'future': {'preserve': True}, key: invalid}
                self.write('ai', {'claude_relay': {'dual_plan': plan}})
                original = self.store.raw('ai')
                state = self.snapshot()
                values = dict(state['values'], mode='auto')
                self.assertIs(values.pop('dual_plan_enabled'), False)
                self.assertIn('claude_relay.dual_plan.' + key, values['dual_plan_error'])
                saved = self.store.save(values, state['fingerprints'])
                self.assertEqual(saved['values']['mode'], 'auto')
                self.assertEqual(json.loads(self.store.raw('ai'))['claude_relay'],
                                 {'dual_plan': plan, 'qwen_executor': 'codex'})
                before = {k: self.store.raw(k) for k in ('ai', 'claude', 'codex')}
                edits = {'claude': {'primary_model': 'new-claude'}, 'codex': {'primary_model': 'new-codex'}}
                with self.assertRaisesRegex(StoreError, r'双方案配置无效.*claude_relay\.dual_plan'):
                    self.store.save(dict(values, dual_plan_enabled=True, primary_model='new-claude', host_updates=edits),
                                    saved['fingerprints'])
                self.assertEqual({k: self.store.raw(k) for k in before}, before)
                restored = self.store.restore(saved['fingerprints'])
                self.assertEqual(self.store.raw('ai'), original)
                self.assertIn('claude_relay.dual_plan.' + key, restored['values']['dual_plan_error'])

    def test_enable_dual_plan_and_switch_to_claude_agent_in_one_save(self):
        for relay in (None, {'implementation': {'provider': 'codex', 'model': 'fixture'}}):
            with self.subTest(relay=relay):
                self.write('ai', {} if relay is None else {'claude_relay': relay})
                saved = self.save(claude_implementation_provider='claude-agent', dual_plan_enabled=True)
                plan = {'enabled': True, 'partner': 'agy', 'model': None, 'effort': None,
                        'synthesizer': 'claude', 'on_failure': 'stop'}
                self.assertEqual(json.loads(self.store.raw('ai'))['claude_relay'],
                                 {'dual_plan': plan, 'qwen_executor': 'codex'})
                self.assertEqual(saved['values']['dual_plan'], plan)
                self.assertIs(saved['values']['dual_plan_enabled'], True)

    def test_disabling_dual_plan_preserves_model_and_unknown_keys(self):
        plan = {'enabled': True, 'model': 'custom-model', 'future': {'preserve': True}}
        self.write('ai', {'claude_relay': {'dual_plan': plan}})
        self.save(dual_plan_enabled=False)
        self.assertEqual(json.loads(self.store.raw('ai'))['claude_relay']['dual_plan'], dict(plan, enabled=False))

    def test_switch_to_claude_agent_preserves_dual_plan_and_sibling(self):
        plan = {'enabled': True, 'model': 'custom-model'}
        kept = {'dual_plan': plan, 'future': {'preserve': True}}
        self.write('ai', {'claude_relay': dict(kept, implementation={'provider': 'codex', 'model': 'fixture'})})
        state = self.snapshot()
        values = dict(state['values'], claude_implementation_provider='claude-agent')
        values.pop('dual_plan_enabled')
        self.store.save(values, state['fingerprints'])
        self.assertEqual(json.loads(self.store.raw('ai'))['claude_relay'], dict(kept, qwen_executor='codex'))

    def test_dual_plan_save_rejects_non_boolean(self):
        for invalid in (1, 'true', None):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(StoreError, 'dual_plan_enabled'):
                self.save(dual_plan_enabled=invalid)

    def test_claude_implementation_and_executor_roundtrip_restore(self):
        route = {'provider': 'codex', 'model': 'gpt-6.2-sol', 'future': {'preserve': True}}
        data = {'claude_relay': {'implementation': route}, 'review': {'codex_model': 'gpt-6.2-astra'},
                'plugins': {'qwen': {'claude_executor': 'codex'}}}
        self.write('ai', encode(data))
        before = self.store.raw('ai')
        values = self.snapshot()['values']
        self.assertEqual((values['claude_implementation_provider'], values['claude_implementation_model'],
                          values['claude_qwen_executor'], values['reviewer_model']),
                         ('codex', 'gpt-6.2-sol', 'codex', 'gpt-6.2-astra'))
        self.assertEqual(values['codex_models'],
                         ['gpt-6.2-sol', 'gpt-6.2-astra', 'gpt-6.1-sol', 'gpt-6-astra', 'gpt-6-sol', 'gpt-6-luna'])
        saved = self.save(reviewer_model='custom-review')
        written = json.loads(self.store.raw('ai'))
        self.assertEqual(written['claude_relay']['implementation'], route)
        self.assertEqual(written['claude_relay']['qwen_executor'], 'codex')
        self.assertNotIn('claude_executor', written['plugins']['qwen'])
        self.assertEqual(saved['values']['reviewer_model'], 'custom-review')
        self.store.restore(saved['fingerprints'])
        saved = self.save(claude_implementation_model='custom-implementation', claude_qwen_executor='claude')
        written = json.loads(self.store.raw('ai'))
        self.assertEqual(written['claude_relay']['implementation'], dict(route, model='custom-implementation'))
        self.assertEqual(written['claude_relay']['qwen_executor'], 'codex')
        self.assertNotIn('claude_executor', written['plugins']['qwen'])
        self.assertEqual(saved['values']['reviewer_model'], 'gpt-6.2-astra')
        restored = self.store.restore(saved['fingerprints'])
        self.assertEqual(self.store.raw('ai'), before)
        self.assertEqual(restored['values']['claude_implementation_model'], 'gpt-6.2-sol')
        self.assertEqual(restored['values']['claude_qwen_executor'], 'codex')
        self.assertEqual(self.save(claude_implementation_provider='claude-agent')['values']['claude_implementation_model'], '')
        self.assertEqual(json.loads(self.store.raw('ai'))['claude_relay'], {'qwen_executor': 'codex'})

    def test_legacy_claude_qwen_executor_migrates_and_restores_bytes(self):
        original = (b'{ "plugins": {"qwen":{"claude_executor":"claude",'
                    b'"responses_base_url":"https://example.invalid/v1","custom":7}} }\n')
        self.write('ai', original)
        saved = self.save(mode='auto')
        config = json.loads(self.store.raw('ai'))
        self.assertEqual(saved['values']['claude_qwen_executor'], 'codex')
        self.assertEqual(config.get('claude_relay', {}).get('qwen_executor'), 'codex')
        self.assertNotIn('claude_executor', config['plugins']['qwen'])
        self.assertEqual(config['plugins']['qwen']['custom'], 7)
        self.assertEqual(config['plugins']['qwen']['responses_base_url'], 'https://example.invalid/v1')
        restored = self.store.restore(saved['fingerprints'])
        self.assertEqual(restored['values']['claude_qwen_executor'], 'claude')
        self.assertEqual(self.store.raw('ai'), original)

    def test_claude_qwen_conflict_rejects_read_and_save(self):
        saved = self.save()
        config = json.loads(self.store.raw('ai'))
        config['claude_relay'] = {'qwen_executor': 'codex'}
        config['plugins']['qwen']['claude_executor'] = 'claude'
        self.write('ai', config)
        before = {key: self.store.raw(key) for key in self.store.paths}
        expected = dict(saved['fingerprints'], ai=hashlib.sha256(before['ai']).hexdigest())
        backup = self.store.desktop / 'last-save.json'
        backup_bytes = backup.read_bytes()
        conflict = r'claude_relay\.qwen_executor.*plugins\.qwen\.claude_executor.*冲突'
        with self.assertRaisesRegex(StoreError, conflict):
            self.store.read()
        with self.assertRaisesRegex(StoreError, conflict):
            self.store.save(saved['values'], expected)
        self.assertEqual({key: self.store.raw(key) for key in self.store.paths}, before)
        self.assertEqual(backup.read_bytes(), backup_bytes)

    def test_claude_legacy_save_preserves_implementation_and_channels(self):
        channels = ['codex', 'qwen', 'same-family']
        route = {'provider': 'codex', 'model': 'saved-model', 'future': 9}
        self.write('ai', {'review': {'channels': channels},
                          'claude_relay': {'implementation': route, 'other': 5},
                          'plugins': {'qwen': {'claude_executor': 'codex', 'responses_base_url': 'https://fixture.invalid/v1'}}})
        snap = self.snapshot()
        legacy = {k: v for k, v in snap['values'].items() if k not in (
            'claude_implementation_provider', 'claude_implementation_model', 'claude_qwen_executor', 'codex_models')}
        self.store.save(dict(legacy, reviewer_model='review-edited'), snap['fingerprints'])
        config = json.loads(self.store.raw('ai'))
        self.assertEqual(config['claude_relay'], {'implementation': route, 'other': 5, 'qwen_executor': 'codex'})
        self.assertNotIn('claude_executor', config['plugins']['qwen'])
        self.save(claude_qwen_executor='claude')
        self.assertEqual(json.loads(self.store.raw('ai'))['review']['channels'], channels)
        self.save(claude_implementation_provider='claude-agent')
        self.assertEqual(json.loads(self.store.raw('ai'))['claude_relay'], {'other': 5, 'qwen_executor': 'codex'})

    def test_claude_implementation_rejects_invalid_stored_provider(self):
        self.write('ai', {'claude_relay': {'implementation': {'provider': 'bad', 'model': 'valid'}}})
        with self.assertRaisesRegex(StoreError, 'provider'):
            self.snapshot()

    def test_claude_route_rejects_invalid_drafts(self):
        for fields in (
            {'claude_implementation_provider': 'bad'},
            {'claude_implementation_provider': 'codex', 'claude_implementation_model': ''},
            {'claude_implementation_provider': 'codex', 'claude_implementation_model': 'white space'},
            {'claude_qwen_executor': 'bad'},
            {'claude_qwen_executor': None},
        ):
            with self.subTest(fields=fields), self.assertRaises(StoreError):
                self.save(**fields)
        self.assertFalse(self.store.paths['ai'].exists())

    def test_save_forces_both_qwen_executors_preserving_channels_and_empty_model(self):
        channels = ['codex', 'qwen', 'same-family']
        for host in ('claude', 'codex'):
            with self.subTest(host=host):
                self.write('claude', b'{ "model": "opus", "custom": true }\n')
                self.write('codex', b'# keep comment\nmodel = "existing-codex"\n')
                self.write('ai', {'review': {'channels': channels}, 'desktop': {'primary_host': host},
                                  'codex_relay': {'review_channel': 'qwen', 'qwen_executor': 'claude'},
                                  'plugins': {'qwen': {'claude_executor': 'claude', 'model': 'fixture-api',
                                                       'responses_base_url': 'https://fixture.invalid/v1'}}})
                before = {key: self.store.raw(key) for key in ('claude', 'codex')}
                self.save(primary_model='')
                config = json.loads(self.store.raw('ai'))
                self.assertEqual(config['claude_relay']['qwen_executor'], 'codex')
                self.assertNotIn('claude_executor', config['plugins']['qwen'])
                self.assertEqual(config['codex_relay']['qwen_executor'], 'codex')
                self.assertEqual(config['review']['channels'], channels)
                self.assertEqual(config['codex_relay']['review_channel'], 'qwen')
                self.assertEqual({key: self.store.raw(key) for key in before}, before)

    def test_qwen_fallback_requires_responses_for_model_only_save(self):
        channels = ['codex', 'qwen']
        self.write('ai', {'review': {'channels': channels},
                          'plugins': {'qwen': {'claude_executor': 'claude', 'model': 'fixture-api',
                                               'base_url': 'https://fixture.invalid/anthropic'}}})
        self.write('claude', b'{ "model": "opus" }\n')
        before = {key: self.store.raw(key) for key in self.store.paths}
        with self.assertRaisesRegex(StoreError, r'Qwen 评审使用 Responses，请填写评审服务地址（Responses）'):
            self.save(primary_model='sonnet')
        self.assertEqual({key: self.store.raw(key) for key in before}, before)
        self.assertFalse(self.store.desktop.exists())
        self.save(primary_model='sonnet', responses_base_url='https://fixture.invalid/v1')
        self.assertEqual(json.loads(self.store.raw('ai'))['review']['channels'], channels)
        self.assertEqual(json.loads(self.store.raw('claude'))['model'], 'sonnet')

    def test_legacy_qwen_save_uses_responses_with_explicit_or_omitted_executor(self):
        for explicit in (True, False):
            for address in ('responses_base_url', 'base_url'):
                with self.subTest(explicit=explicit, address=address):
                    self.write('ai', {'review': {'channels': ['qwen', 'codex']},
                                      'plugins': {'qwen': {'claude_executor': 'claude', 'model': 'fixture-api',
                                                           address: 'https://fixture.invalid/api'}}})
                    before = self.store.raw('ai')
                    snap = self.snapshot()
                    values = dict(snap['values'])
                    if not explicit:
                        values.pop('claude_qwen_executor')
                        values.pop('qwen_executor')
                    if address == 'responses_base_url':
                        saved = self.store.save(values, snap['fingerprints'])
                        self.assertEqual(saved['values']['claude_qwen_executor'], 'codex')
                        self.assertEqual(saved['values']['qwen_executor'], 'codex')
                    else:
                        with self.assertRaisesRegex(StoreError, '请填写评审服务地址'):
                            self.store.save(values, snap['fingerprints'])
                        self.assertEqual(self.store.raw('ai'), before)

    def test_batch_qwen_address_validation_uses_updated_channels(self):
        self.write('ai', {'codex_relay': {'review_channel': 'qwen'},
                          'plugins': {'qwen': {'model': 'fixture-api'}}})
        with self.assertRaisesRegex(StoreError, '请填写评审服务地址'):
            self.save(primary_model='sonnet')
        self.save(primary_model='sonnet', host_updates={'codex': {'channel': 'qoder'}})
        with self.assertRaisesRegex(StoreError, '请填写评审服务地址'):
            self.save(host_updates={'codex': {'channel': 'qwen'}})

    def test_implementation_provider_roundtrip_and_restore_validation(self):
        self.assertEqual(self.snapshot()['values']['implementation_provider'], 'codex')
        for bad in ('custom_api', '', [], {}, 1, True):
            with self.subTest(provider=bad):
                with self.assertRaises(StoreError): self.save(implementation_provider=bad)
                self.assertFalse(self.store.paths['ai'].exists())
        saved = self.save(implementation_provider='token_plan', secondary_model='historical-native-id')
        self.assertEqual(saved['values']['secondary_model'], 'historical-native-id')
        saved = self.save(implementation_provider='codex')
        restored = self.store.restore(saved['fingerprints'])
        self.assertEqual(restored['values']['implementation_provider'], 'token_plan')
        self.assertEqual(restored['values']['secondary_model'], 'historical-native-id')
        # Damaged stored values fail on load, before save and before restore writes.
        for bad in ('custom_api', [], {}, True):
            self.write('ai', {'codex_relay': {'implementation_provider': bad}})
            with self.assertRaises(StoreError): self.snapshot()
        self.write('ai', {'codex_relay': {'implementation_provider': None}})
        self.assertEqual(self.snapshot()['values']['implementation_provider'], 'codex')
        saved = self.save(implementation_provider='token_plan')
        backup = self.store.desktop / 'last-save.json'
        record = json.loads(backup.read_text())
        record['before']['ai'] = json.dumps({'codex_relay': {'implementation_provider': 'broken'}}).encode().hex()
        backup.write_text(json.dumps(record))
        before = self.store.raw('ai')
        with self.assertRaises(StoreError): self.store.restore(saved['fingerprints'])
        self.assertEqual(self.store.raw('ai'), before)

    def test_qoder_implementation_roundtrip_and_restore(self):
        saved = self.save(implementation_provider='qoder', secondary_model='native-history')
        self.assertEqual(saved['values']['implementation_provider'], 'qoder')
        switched = self.save(implementation_provider='codex')
        restored = self.store.restore(switched['fingerprints'])
        self.assertEqual(restored['values']['implementation_provider'], 'qoder')
        self.assertEqual(restored['values']['secondary_model'], 'native-history')

    def test_batch_hosts_dispatch_restore_and_unchanged_bytes(self):
        self.write('claude', b'{ "model": "claude-old", "custom": 1 }\n')
        self.write('codex', b'# keep comment\nmodel = "codex-old"\n')
        self.write('ai', {'review': {'channels': ['codex', 'same-family', 'qwen']},
                          'plugins': {'qwen': {'responses_base_url': 'https://fixture.invalid/v1'}}})
        before = {k: self.store.raw(k) for k in ('ai', 'claude', 'codex')}
        snap = self.snapshot(); v = snap['values']
        v.update(primary_model='claude-new', host_updates={'claude': {'primary_model': 'claude-new'}, 'codex': {'primary_model': 'codex-new', 'channel': 'qwen'}}, qwen_model='fixture-api', base_url='https://fixture.invalid/anthropic')
        reply = dispatch(self.store, dict(action='save', values=v, fingerprints=snap['fingerprints']))
        self.assertEqual(reply['values']['claude_model'], 'claude-new')
        self.assertEqual(reply['values']['codex_model'], 'codex-new')
        self.assertEqual(reply['values']['codex_channel'], 'qwen')
        self.store.restore(reply['fingerprints'])
        self.assertEqual({k: self.store.raw(k) for k in before}, before)
        reply = self.save(primary_model='claude-new')
        self.assertEqual(self.store.raw('codex'), before['codex'])
        self.assertEqual(json.loads(self.store.raw('ai'))['review']['channels'], ['codex', 'same-family', 'qwen'])

    def test_host_updates_validation_and_no_writes(self):
        snap = self.snapshot()
        invalid = [None, [], {'other': {}}, {'claude': []}, {'claude': {'unexpected': 'x'}}, {'codex': {'primary_model': 1}}, {'codex': {'channel': 'codex'}}, {'claude': {'primary_model': 'conflict'}}, {'claude': {'channel': 'qwen'}}]
        for update in invalid:
            with self.subTest(update=update):
                v = dict(snap['values'], host_updates=update)
                with self.assertRaises(StoreError): self.store.save(v, snap['fingerprints'])
                self.assertEqual(list(self.root.iterdir()), [])

    def test_batch_legacy_id_only_and_api_validation(self):
        self.write('ai', {'review': {'channels': ['same-family', 'codex']}, 'codex_relay': {'review_channel': 'claude'}})
        self.save(primary_model='new-claude', host_updates={'codex': {'primary_model': 'new-codex'}})
        value = self.snapshot()['values']
        self.assertEqual(value['claude_channel'], 'same-family')
        self.assertEqual(value['codex_channel'], 'claude')
        self.save(host='codex', primary_model='another-codex', channel='claude')
        self.assertEqual(self.snapshot()['values']['codex_channel'], 'claude')
        v = self.values(host='claude', channel='same-family', host_updates={'codex': {'channel': 'qwen'}}, qwen_model='fixture-api', qwen_executor='codex', base_url='https://fixture.invalid/anthropic')
        with self.assertRaisesRegex(StoreError, '请填写评审服务地址'): self.store.outputs(v, '')
        v['responses_base_url'] = 'https://fixture.invalid/v1'
        self.store.outputs(v, '')
        v['host_updates'] = {'claude': {'channel': 'same-family'}}
        with self.assertRaises(StoreError): self.store.outputs(v, '')

    def test_batch_bad_toml_conflict_and_midwrite_rollback(self):
        self.write('claude', {'model': 'old'})
        self.write('codex', b'model = "old"\n')
        snap = self.snapshot(); v = dict(snap['values'], primary_model='new', host_updates={'codex': {'primary_model': 'new'}})
        before = {k: self.store.raw(k) for k in ('claude', 'codex')}
        self.write('codex', b'broken = [')
        with self.assertRaisesRegex(StoreError, 'TOML 损坏'): self.store.save(v, snap['fingerprints'])
        self.assertEqual(self.store.raw('claude'), before['claude'])
        self.write('codex', before['codex']); snap = self.snapshot()
        self.write('claude', {'model': 'external'})
        with self.assertRaisesRegex(StoreError, '其他程序'): self.store.save(v, snap['fingerprints'])
        self.assertEqual(json.loads(self.store.raw('claude'))['model'], 'external')
        snap = self.snapshot(); before = {k: self.store.raw(k) if self.store.paths[k].exists() else None for k in self.store.paths}
        original = atomic
        def fail_codex(path, raw, *args):
            if path == self.store.paths['codex']: raise OSError('synthetic write failure')
            return original(path, raw, *args)
        with patch('config_store.atomic', fail_codex):
            with self.assertRaisesRegex(StoreError, '已回滚'): self.store.save(v, snap['fingerprints'])
        self.assertEqual({k: self.store.raw(k) if self.store.paths[k].exists() else None for k in self.store.paths}, before)

    def test_executor_endpoints_dispatch_restore_and_legacy_preservation(self):
        self.write('ai', {'plugins': {'qwen': {'base_url': 'https://old.invalid/anthropic', 'responses_base_url': 'https://old.invalid/v1', 'custom': 7}}, 'codex_relay': {'qwen_executor': 'codex'}})
        before = self.store.raw('ai')
        snap = self.snapshot(); v = snap['values']
        v.update(host='codex', channel='qwen', qwen_model='qwen-fixture', responses_base_url='https://new.invalid/v1')
        reply = dispatch(self.store, dict(action='save', values=v, fingerprints=snap['fingerprints']))
        self.assertEqual(reply['values']['base_url'], 'https://old.invalid/anthropic')
        self.assertEqual(reply['values']['responses_base_url'], 'https://new.invalid/v1')
        self.assertNotEqual(reply['signature'], snap['signature'])
        v = reply['values']; v.pop('responses_base_url'); v.pop('qwen_executor')
        outputs = json.loads(self.store.outputs(v, '')['ai'])
        self.assertEqual(outputs['plugins']['qwen']['responses_base_url'], 'https://new.invalid/v1')
        self.assertEqual(outputs['codex_relay']['qwen_executor'], 'codex')
        self.assertEqual(outputs['plugins']['qwen']['custom'], 7)
        self.store.restore(reply['fingerprints']); self.assertEqual(self.store.raw('ai'), before)

    def test_qwen_responses_only_endpoint_for_both_hosts(self):
        v = self.values(host='codex', channel='qwen', qwen_model='qwen-fixture', qwen_executor='codex', responses_base_url='https://example.invalid/v1')
        self.store.outputs(v, '')
        v['host'] = 'claude'
        self.store.outputs(v, '')

    def test_endpoint_and_executor_validation(self):
        for key, value in [('qwen_executor', 'other'), ('qwen_executor', 9), ('responses_base_url', 7), ('responses_base_url', 'https://secret:password@example.invalid'), ('base_url', 'https://example.invalid?secret'), ('responses_base_url', 'https://[broken')]:
            with self.subTest(key=key, value=value):
                v = self.values(); v[key] = value
                with self.assertRaises(StoreError) as error: self.store.outputs(v, '')
                self.assertNotIn('password', str(error.exception))
        self.write('ai', {'codex_relay': {'qwen_executor': 'other'}})
        with self.assertRaises(StoreError): self.snapshot()

    def test_restore_rejects_invalid_new_fields_before_write(self):
        for invalid in ({'codex_relay': {'qwen_executor': 'bad'}}, {'plugins': {'qwen': {'responses_base_url': 'https://secret:password@example.invalid'}}}):
            self.save()
            backup = self.store.desktop / 'last-save.json'
            record = json.loads(backup.read_bytes())
            record['before']['ai'] = json.dumps(invalid).encode().hex()
            atomic(backup, json.dumps(record).encode())
            before = self.store.raw('ai'); snap = self.snapshot()
            with self.assertRaises(StoreError): self.store.restore(snap['fingerprints'])
            self.assertEqual(self.store.raw('ai'), before)

    def test_restore_rejects_invalid_claude_qwen_executor_before_write(self):
        saved = self.save(primary_model='fixture')
        backup = self.store.desktop / 'last-save.json'
        record = json.loads(backup.read_bytes())
        before = {key: self.store.raw(key) for key in self.store.paths}
        for new, old, message in (
            ('codex', 'claude', r'claude_relay\.qwen_executor.*plugins\.qwen\.claude_executor.*冲突'),
            ('invalid', 'codex', r'claude_relay\.qwen_executor.*无效'),
            ('codex', False, r'plugins\.qwen\.claude_executor.*无效'),
        ):
            with self.subTest(new=new, old=old):
                invalid = {'claude_relay': {'qwen_executor': new}, 'plugins': {'qwen': {'claude_executor': old}}}
                record['before']['ai'] = encode(invalid).hex()
                backup_bytes = encode(record)
                atomic(backup, backup_bytes)
                with patch('config_store.atomic', side_effect=AssertionError('unexpected write')):
                    with self.assertRaisesRegex(StoreError, message):
                        self.store.restore(saved['fingerprints'])
                self.assertEqual({key: self.store.raw(key) for key in self.store.paths}, before)
                self.assertEqual(backup.read_bytes(), backup_bytes)

    def test_first_load_is_read_only(self):
        with patch('checks.subprocess.Popen', side_effect=AssertionError('unexpected CLI process')):
            reply = dispatch(self.store, {'action':'load'})
            dispatch(self.store, {'action': 'inspect'})
        self.assertEqual(reply['values']['mode'], 'rules')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cli_status_dispatch_does_not_open_configuration_or_secrets(self):
        self.write('ai', b'invalid JSON')
        self.write('secrets', b'fixture-secret')
        before = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        with patch.object(self.store, 'read', side_effect=AssertionError('unexpected config read')), \
             patch.object(Path, 'read_bytes', side_effect=AssertionError('unexpected file read')), \
             patch('checks.discover', return_value='/fixture/claude'), \
             patch('checks.run', return_value=1), patch('checks.shutil.which', return_value=None):
            result = dispatch(self.store, {'action': 'cli_status', 'names': ['claude']})
        self.assertEqual(result['items'], {'claude': {'state': 'error', 'reason': '版本命令退出异常'}})
        self.assertEqual({path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}, before)
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
        self.save(channel='qwen', qwen_model='qwen-fixture', responses_base_url='https://example.invalid/v1')
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
        snap = self.snapshot()
        self.write('claude', {'external': 1})
        self.write('secrets', {'tokenplan': {'value': 'fixture-secret'}})
        with self.assertRaises(StoreError) as ctx:
            self.store.save(snap['values'], snap['fingerprints'])
        message = str(ctx.exception)
        self.assertIn('配置已被其他程序修改', message)
        self.assertIn('Claude 设置、凭证文件', message)
        self.assertIn('请刷新后重试', message)
        for path in self.store.paths.values():
            self.assertNotIn(str(path), message)
            self.assertNotIn(path.name, message)
        self.assertNotIn('fixture-secret', message)
        self.assertEqual(json.loads(self.store.raw('claude')), {'external': 1})
        self.assertEqual(json.loads(self.store.raw('secrets')), {'tokenplan': {'value': 'fixture-secret'}})
        self.assertFalse(self.store.paths['ai'].exists())
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

    def test_new_token_plan_implementation_models_roundtrip(self):
        for requested in ('qwen3.8-max', 'deepseek-v4-pro', 'deepseek-v4.1-flash'):
            with self.subTest(requested=requested):
                saved = self.save(token_plan_implementation_model=requested)
                self.assertEqual(saved['values']['token_plan_implementation_model'], requested)
                snapshot = self.snapshot()
                self.assertEqual(snapshot['values']['token_plan_implementation_model'], requested)
                switched = self.save(token_plan_implementation_model='glm-5.3')
                restored = self.store.restore(switched['fingerprints'])
                self.assertEqual(restored['values']['token_plan_implementation_model'], requested)
                self.assertEqual(self.snapshot()['values']['token_plan_implementation_model'], requested)

    def test_token_plan_implementation_model_persistence(self):
        self.assertEqual(self.snapshot()['values'].get('token_plan_implementation_model'), 'glm-5.3')
        self.write('ai', {'codex_relay': {'token_plan_implementation_model': None}})
        self.assertEqual(self.snapshot()['values']['token_plan_implementation_model'], 'glm-5.3')
        # Old UI opens an old configuration, changes only review URL and saves.
        snap = self.snapshot(); values = snap['values']; values.pop('token_plan_implementation_model')
        values['base_url'] = 'https://fixture.invalid/anthropic'
        saved = self.store.save(values, snap['fingerprints'])
        self.assertEqual(saved['values']['token_plan_implementation_model'], 'glm-5.3')
        saved = self.save(token_plan_implementation_model='qwen4-max')
        self.assertEqual(saved['values']['token_plan_implementation_model'], 'qwen4-max')
        old = saved['values'].copy(); old.pop('token_plan_implementation_model')
        saved = self.store.save(old, saved['fingerprints'])
        self.assertEqual(saved['values']['token_plan_implementation_model'], 'qwen4-max')
        switched = self.save(implementation_provider='qoder', token_plan_implementation_model='glm-5.3')
        restored = self.store.restore(switched['fingerprints'])
        self.assertEqual(restored['values']['token_plan_implementation_model'], 'qwen4-max')
        for bad in ('', 'Qwen4-Max', 'qwen4-max-preview', 'DeepSeek-v4-pro', [], {}, True):
            before = self.store.raw('ai')
            with self.assertRaises(StoreError): self.save(token_plan_implementation_model=bad)
            self.assertEqual(self.store.raw('ai'), before)
            self.write('ai', {'codex_relay': {'token_plan_implementation_model': bad}})
            with self.assertRaises(StoreError): self.snapshot()
            self.write('ai', json.loads(before))
        saved = self.save(token_plan_implementation_model='glm-5.3')
        backup = self.store.desktop/'last-save.json'
        record = json.loads(backup.read_text())
        record['before']['ai'] = json.dumps({'codex_relay': {'token_plan_implementation_model': 'bad'}}).encode().hex()
        backup.write_text(json.dumps(record))
        before = self.store.raw('ai')
        with self.assertRaises(StoreError): self.store.restore(saved['fingerprints'])
        self.assertEqual(self.store.raw('ai'), before)

if __name__ == '__main__': unittest.main()
