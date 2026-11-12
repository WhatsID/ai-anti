"""Qoder implementation contracts, synthetic CLI and captured scratch-stream replay."""
import argparse
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'adapters/codex/skills/codex-relay/scripts'))
import implementation
import implementation_qoder as qoder
from implementation_config import resolve, ConfigError
from implementation_qoder_protocol import validate
from review_runner import RunError
FAKE = ROOT/'adapters/codex/tests/fake_implementation_qoder.py'

class QoderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve()
        self.work = self.root/'work'; self.work.mkdir()
        self.prompt = self.root/'prompt'; self.prompt.write_text('Synthetic authorized implementation')
        self.config = self.root/'config'; self.config.write_text(json.dumps({'codex_relay': {'implementation_provider':'qoder'}}))
    def tearDown(self): self.temp.cleanup()
    def args(self, name='out', **changes):
        values = dict(workspace=self.work, prompt=self.prompt, out=self.root/name, config=self.config, provider=None, model=None, prepare_only=False, cli=str(FAKE), timeout=None, idle_timeout=None)
        values.update(changes); return argparse.Namespace(**values)
    def stream(self):
        return [dict(type='system', subtype='init', model='GLM-5.3', cwd=str(self.work), qodercli_version='1.1.77', protocol_version='1.5.0', tools=list(qoder.TOOLS), permissionMode='acceptEdits', apiKeySource='none', mcp_servers=[],plugins=[],skills=[]),
                dict(type='assistant',message=dict(role='assistant',model='GLM-5.3',stop_reason='end_turn',content=[dict(type='text',text='done')])),
                dict(type='result',subtype='success',is_error=False,permission_denials=[],stop_reason='end_turn',result='done')]
    def check(self, events, code=0):
        path=self.root/'events'; path.write_text('\n'.join(json.dumps(e) for e in events)+'\n')
        return validate(path,code,self.work)
    def test_routing_without_token_profile_or_credentials(self):
        config={'codex_relay':dict(implementation_provider='qoder', secondary_model='native'), 'plugins':{'qwen':False},'review_services':False}
        with patch('implementation_config.review_services.load',side_effect=AssertionError('profile accessed')):
            route=resolve(config,self.work)
        self.assertEqual(route['requested_model'],'glm-5.3'); self.assertNotIn('credential_key',route)
        for provider, model in [('qoder','GLM-5.3'),('qoder','other'),('codex','glm-5.3'),('codex','GLM-5.3')]:
            with self.assertRaises(ConfigError): resolve(config,self.work,provider,model)
        config['codex_relay']['implementation_provider']='broken'
        with self.assertRaises(ConfigError): resolve(config,self.work,'qoder')
    def test_prepare_has_no_cli_or_auth_side_effect(self):
        with patch.object(qoder,'execute',side_effect=AssertionError('launch')), patch.object(qoder.shutil,'which',side_effect=AssertionError('lookup')), patch.object(implementation,'child_environment',side_effect=AssertionError('credentials')):
            result=implementation.run(self.args(prepare_only=True))
        self.assertEqual(result['status'],'prepared'); self.assertIsNone(result['actual_model'])
        self.assertEqual(result['isolation']['sandbox'],'none'); self.assertTrue((self.root/'out/qoder-config.json').exists())
        self.assertFalse((self.work/'fake-count.txt').exists())
    def test_formal_success_argv_environment_and_evidence(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'SYNTHETIC_SECRET','QODER_API_KEY':'SYNTHETIC_SECRET','ANTHROPIC_BASE_URL':'https://invalid','CODEX_HOME':'/invalid','HOME':str(self.root)}):
            result=implementation.run(self.args())
        self.assertTrue(result['completed']);self.assertFalse(result['accepted']);self.assertEqual(result['actual_model'],'GLM-5.3')
        self.assertEqual(result['backend_model'],'unknown');self.assertTrue(result['warnings'])
        audit=json.loads((self.work/'fake-audit.json').read_text()); args=audit['args']
        self.assertEqual(audit['home'],str(self.root));self.assertFalse(set(audit['env_keys']) & {'OPENAI_API_KEY','QODER_API_KEY','ANTHROPIC_BASE_URL','CODEX_HOME'})
        for flag,value in [('--model','GLM-5.3'),('--permission-mode','accept_edits'),('--max-model-request-retries','0'),('--setting-sources','')]: self.assertEqual(args[args.index(flag)+1],value)
        self.assertEqual(json.loads(args[args.index('--settings')+1])['permissions']['allow'],list(qoder.TOOLS))
        self.assertEqual((self.work/'fake-count.txt').read_text(),'1')
        for p in (self.root/'out').iterdir():
            if p.is_file(): self.assertNotIn('SYNTHETIC_SECRET',p.read_text())
    def test_subscription_cli_discovery_from_local_bin(self):
        local = self.root / '.local/bin/qodercli'; local.parent.mkdir(parents=True)
        local.write_bytes(FAKE.read_bytes()); local.chmod(0o700)
        with patch.object(qoder.shutil, 'which', return_value=None), patch.object(qoder.Path, 'home', return_value=self.root):
            result = implementation.run(self.args(cli=None))
        self.assertTrue(result['completed'])
        local.chmod(0o600)
        with patch.object(qoder.shutil, 'which', return_value=None), patch.object(qoder.Path, 'home', return_value=self.root):
            unavailable = implementation.run(self.args(name='no-executable', cli=None))
        self.assertEqual(unavailable['status'], 'failed')
        with patch.object(qoder.shutil, 'which', side_effect=AssertionError('explicit CLI must win')), patch.object(qoder.Path, 'home', side_effect=AssertionError('explicit CLI must win')):
            explicit = implementation.run(self.args(name='explicit'))
        self.assertTrue(explicit['completed'])

    def test_protocol_mutations_fail_closed(self):
        mutations=[]
        for key,value in [('model','other'),('qodercli_version','1.1.78'),('permissionMode','dontAsk'),('tools',['Bash']),('tools',[{}]*6),('mcp_servers',[{}]),('plugins',[{}]),('skills',['x']),('apiKeySource','env'),('protocol_version','other')]:
            events=self.stream();events[0][key]=value;mutations.append(events)
        events=self.stream();events[1]['message']['model']='other';mutations.append(events)
        for key, value in [('is_error', True), ('error', 'failure'), ('permission_denials', [{}])]:
            events=self.stream();events[1]['message']['content'][0][key]=value;mutations.append(events)
        for key,value in [('permission_denials',[{}]),('subtype','error'),('result',''),('stop_reason','tool_use')]:
            events=self.stream();events[-1][key]=value;mutations.append(events)
        mutations += [self.stream()+[self.stream()[-1]], self.stream()+[dict(type='system',subtype='artifacts_update',artifacts=[])], [self.stream()[0]]+self.stream(), self.stream()[:-1], self.stream()[:1]+[dict(type='system',subtype='hooks')]+self.stream()[1:]]
        for i,events in enumerate(mutations):
            with self.subTest(i=i),self.assertRaises(RunError):self.check(events)
        with self.assertRaises(RunError):self.check(self.stream(),1)
        log=self.root/'bad';log.write_text('{');
        with self.assertRaises(RunError):validate(log,0,self.work)
    def test_tool_pairing_recovery_denial_and_thinking(self):
        base=self.stream()
        thinking=dict(type='assistant',message=dict(role='assistant',model='GLM-5.3',stop_reason=None,content=[dict(type='thinking',thinking='Consider safe change')]))
        use=dict(type='assistant',message=dict(role='assistant',model='GLM-5.3',stop_reason='tool_use',content=[dict(type='tool_use',id='one',name='Bash',input={'command':'false'})]))
        result=dict(type='user',message=dict(role='user',content=[dict(type='tool_result',tool_use_id='one',is_error=True,content='exit status 1')]))
        events=base[:1]+[thinking,use,result]+base[1:]
        self.assertEqual(self.check(events)['recovered_tool_errors'],1)
        for mutation in ('denial','duplicate','pending','unknown','unmatched'):
            bad=copy.deepcopy(events)
            if mutation=='denial':bad[3]['message']['content'][0]['content']='Permission denied'
            elif mutation=='duplicate':bad.insert(4,copy.deepcopy(result))
            elif mutation=='pending':bad.pop(3)
            elif mutation=='unknown':bad[2]['message']['content'][0]['name']='Agent'
            else:bad[3]['message']['content'][0]['tool_use_id']='other'
            with self.subTest(mutation=mutation),self.assertRaises(RunError):self.check(bad)
    def test_segmented_tools_and_successful_permission_source_text(self):
        base = self.stream()
        call = dict(type='assistant', message=dict(id='segmented', role='assistant', model='GLM-5.3', stop_reason=None, content=[dict(type='tool_use', id='edit', name='Edit', input={})]))
        reply = dict(type='user', message=dict(role='user', content=[dict(type='tool_result', tool_use_id='edit', content="source: permission denied; not allowed; this action needs approval")]))
        self.assertEqual(self.check(base[:1] + [call, reply] + base[1:])['actual_model'], 'GLM-5.3')
        for denial in ('This action needs approval', 'Permission denied'):
            bad = copy.deepcopy(reply); bad['message']['content'][0].update(is_error=True, content=denial)
            with self.assertRaises(RunError): self.check(base[:1] + [call, bad] + base[1:])
        bad = copy.deepcopy(reply); bad['tool_result_meta'] = [dict(non_execution_kind='permission-rule')]
        with self.assertRaises(RunError): self.check(base[:1] + [call, bad] + base[1:])

    def test_segmented_parallel_calls_same_message_only(self):
        base = self.stream()
        def use(uid, stop):
            return dict(type='assistant', message=dict(id='same-message', role='assistant', model='GLM-5.3', stop_reason=stop, content=[dict(type='tool_use', id=uid, name='Write', input={})]))
        def reply(uid):
            return dict(type='user', message=dict(role='user', content=[dict(type='tool_result', tool_use_id=uid, content='ok')]))
        progress = dict(type='assistant', message=dict(role='assistant', model='GLM-5.3', stop_reason=None, content=[dict(type='text', text='Working')]))
        self.assertEqual(self.check(base[:1] + [progress] + base[1:])['actual_model'], 'GLM-5.3')
        with self.assertRaises(RunError): self.check(base[:1] + [progress, base[-1]])
        for middle in ([use('one', None), reply('one'), use('two', 'tool_use'), reply('two')],
                       [use('one', None), use('two', 'tool_use'), reply('one'), reply('two')]):
            self.assertEqual(self.check(base[:1] + middle + base[1:])['actual_model'], 'GLM-5.3')
        bad = use('two', 'tool_use'); bad['message']['id'] = 'different-turn'
        for middle in ([use('one', None), bad, reply('one'), reply('two')], [use('one', None)] + base[1:]):
            with self.assertRaises(RunError): self.check(base[:1] + middle + base[1:])

    def test_version_gate_never_launches_write_task(self):
        def version_only(argv,cwd,log,*args,**kwargs):log.write_text('1.1.78\n');return 0
        with patch.object(qoder,'execute',side_effect=version_only) as execute:
            result=implementation.run(self.args())
        self.assertEqual(execute.call_count,1);self.assertFalse(result['completed']);self.assertTrue(result['version_preflight']['sha256'])
    def test_failure_retains_partial_write_no_retry(self):
        for mode in ('nonzero','flood','wait'):
            (self.work/'fake-mode.txt').write_text(mode);(self.work/'fake-count.txt').unlink(missing_ok=True)
            errors = []
            original = qoder.execute
            def observed_execute(*args, **kwargs):
                try:
                    return original(*args, **kwargs)
                except RunError as exc:
                    errors.append(str(exc)); raise
            with patch.object(qoder, 'execute', side_effect=observed_execute):
                result=implementation.run(self.args(mode, timeout=1,idle_timeout=1))
            if mode == 'flood': self.assertEqual(errors, ['log limit'])
            if mode == 'wait': self.assertTrue(errors and errors[0] in ('timeout', 'idle timeout'))
            self.assertEqual(result['status'],'failed');self.assertFalse(result['completed'])
            self.assertEqual((self.work/'partial.txt').read_text(),'retained candidate\n');self.assertEqual((self.work/'fake-count.txt').read_text(),'1')
            self.assertIsNotNone(result['log_sha256']);self.assertEqual(json.loads((self.root/mode/'status.json').read_text())['status'],'failed')
    def test_sigterm_preserves_partial_status(self):
        (self.work/'fake-mode.txt').write_text('wait')
        script=ROOT/'adapters/codex/skills/codex-relay/scripts/implementation.py'
        process=subprocess.Popen([sys.executable,str(script),'--workspace',str(self.work),'--prompt',str(self.prompt),'--out',str(self.root/'signal'),'--config',str(self.config),'--cli',str(FAKE)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+10
            while not (self.work/'partial.txt').exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue((self.work/'partial.txt').exists());process.send_signal(signal.SIGTERM);process.communicate(timeout=8)
            self.assertEqual(process.returncode,1)
            status=json.loads((self.root/'signal/status.json').read_text());self.assertEqual(status['status'],'failed');self.assertIsNotNone(status['log_sha256'])
            self.assertEqual((self.work/'fake-count.txt').read_text(),'1')
        finally:
            if process.poll() is None:process.kill();process.wait()

if __name__=='__main__':unittest.main()
