import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import checks
from config_store import Store, atomic
ROOT = Path(__file__).resolve().parents[4]

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
    def api_store(self, host='codex'):
        store = Store(self.root/'home')
        snap = store.read(); v = snap['values']
        v.update(host=host, channel='qwen', qwen_model='qwen-fixture', qwen_executor='codex', base_url='https://example.invalid/anthropic', responses_base_url='https://example.invalid/v1')
        store.save(v, snap['fingerprints'], 'fixture-token')
        return store

    def test_local_effective_executor_dependencies(self):
        store = self.api_store()
        with patch('checks.discover', return_value='/usr/bin/true') as discover:
            checks.local(store, str(ROOT))
        self.assertEqual({call.args[0] for call in discover.call_args_list}, {'codex'})
        snap = store.read(); v = snap['values']; v['host'] = 'claude'; store.save(v, snap['fingerprints'])
        with patch('checks.discover', return_value='/usr/bin/true') as discover:
            checks.local(store, str(ROOT))
        self.assertEqual({call.args[0] for call in discover.call_args_list}, {'claude'})

    def test_formal_executor_limits_and_requested_only(self):
        for host in ('codex', 'claude'):
            store = self.api_store(host)
            data=json.loads(store.paths['ai'].read_text()); data['review'].update(timeout=321,idle_timeout=45)
            atomic(store.paths['ai'],json.dumps(data).encode())
            executor = 'codex' if host == 'codex' else 'claude'
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
        atomic(store.paths['ai'],json.dumps(data).encode())
        with patch('checks.discover', return_value='/usr/bin/true') as discover:
            checks.local(store, str(ROOT))
        self.assertEqual({c.args[0] for c in discover.call_args_list}, {'codex','qodercli'})
        good = dict(status='completed',completed=True,mode='diff',channel='qoder',executor='qoder',wire_api='qoder-cli',requested_model='kimi-k3',actual_model='Kimi-K3')
        def fake_run(argv, cwd, log, **kwargs):
            if '--channel' in argv:
                self.assertEqual(argv[argv.index('--channel')+1], 'qoder')
                self.assertEqual(argv[argv.index('--model')+1], 'kimi-k3')
                self.assertEqual(argv[argv.index('--timeout')+1], '321')
                self.assertEqual(argv[argv.index('--idle-timeout')+1], '45')
                self.assertEqual(kwargs['timeout'], 381); self.assertEqual(kwargs['limit'], 2*1024*1024)
                self.assertEqual(kwargs['env']['HOME'], str(store.root))
                out = Path(argv[argv.index('--out')+1]); out.mkdir()
                for name in ('status','manifest'): (out/(name+'.json')).write_text(json.dumps(good))
            return 0
        with patch('checks.discover', return_value='/usr/bin/true'), patch('checks.run', side_effect=fake_run):
            self.assertEqual(checks.real(store, str(ROOT))['actual_model'], 'Kimi-K3')
        for key, bad in [('completed',False),('mode','plan'),('status','failed'),('channel','qwen'),('executor','codex'),('wire_api','responses'),('requested_model','other'),('actual_model','other')]:
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
            snap=store.read(); v=snap['values']; v['review_services']['active_api']=service
            store.save(v, snap['fingerprints'])
            self.assertEqual(store.read()['values']['review_services']['active_api'],service)
            self.assertEqual(json.loads(store.paths['ai'].read_text())['review_services']['active_api'],service)
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
        atomic(store.paths['ai'],b'{"review":{"channels":["qwen"]},"plugins":{"qwen":{"model":"qwen-fixture","base_url":"https://example.invalid","credential_paths":["project/credentials.json","~/.config/ai-anti/secrets.json"]}}}')
        atomic(store.paths['secrets'],b'{"tokenplan":{"value":"fake-global"}}')
        with patch('checks.discover',return_value='/usr/bin/true'):
            value=checks.local(store,str(ROOT))
        self.assertEqual(value['state'],'passed')
    def test_real_result_invalidated_by_config_change(self):
        store=Store(self.root/'home'); snap=store.read(); v=snap['values']; v['reviewer_model']='chosen'; store.save(v,snap['fingerprints'])
        def fake_events(path,marker):
            atomic(store.paths['claude'],b'{"model":"external"}'); return 'unknown'
        with patch('checks.review_budgets',return_value=(10,900)), patch('checks.discover',return_value='/usr/bin/true'), patch('checks.run',return_value=0), patch('checks.codex_events',side_effect=fake_events), patch.object(Path,'read_text',return_value='--json --sandbox --ephemeral --ignore-user-config --ignore-rules'):
            with self.assertRaisesRegex(checks.CheckError,'作废'): checks.real(store,str(ROOT),timeout=10)

if __name__ == '__main__': unittest.main()
