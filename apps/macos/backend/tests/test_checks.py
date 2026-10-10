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
        original=checks.discover
        with patch('checks.discover',side_effect=lambda name: str(fake) if name=='codex' else original(name)):
            value=checks.real(store,str(ROOT),timeout=10)
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
        with patch('checks.discover',return_value='/usr/bin/true'), patch('checks.run',return_value=0), patch('checks.codex_events',side_effect=fake_events), patch.object(Path,'read_text',return_value='--json --sandbox --ephemeral --ignore-user-config --ignore-rules'):
            with self.assertRaisesRegex(checks.CheckError,'作废'): checks.real(store,str(ROOT),timeout=10)

if __name__ == '__main__': unittest.main()
