"""Real entry/config contract checks; no model calls or real user config."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'adapters/codex/skills/claude-review/scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / 'apps/macos/backend'))
from config_store import Store, atomic
from review import parser

class DesktopContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.env = dict(os.environ, HOME=str(self.root), AI_ANTI_PYTHON=sys.executable, PYTHONDONTWRITEBYTECODE='1', PYTHONPYCACHEPREFIX=str(self.root/'cache'))
        self.repo = self.root/'repo'; self.repo.mkdir()
    def tearDown(self): self.temp.cleanup()
    def hook(self):
        return subprocess.run(['bash',str(ROOT/'adapters/claude-code/hooks/session-start.sh')],cwd=self.repo,env=self.env,capture_output=True,text=True)
    def test_mode_defaults_and_global_and_project(self):
        value=self.hook(); self.assertEqual(value.returncode,0); self.assertIn('rules（来源：内置默认）',value.stdout)
        store=Store(self.root); atomic(store.paths['ai'],b'{"relay":{"default_mode":"solo"}}')
        value=self.hook(); self.assertIn('solo（来源：全局默认）',value.stdout)
        (self.repo/'.claude').mkdir(); (self.repo/'.claude/relay-mode.local').write_text('force\n')
        value=self.hook(); self.assertIn('force（来源：项目模式文件）',value.stdout)
    def test_broken_global_and_project_fail_closed(self):
        store=Store(self.root)
        for value in (b'', b'[]', b'{"relay":[]}',b'{"relay":{"default_mode":"oops"}}'):
            atomic(store.paths['ai'],value); result=self.hook(); self.assertNotEqual(result.returncode,0); self.assertIn('停止',result.stderr)
        (self.repo/'.claude').mkdir(); (self.repo/'.claude/relay-mode.local').write_text('nonsense')
        self.assertNotEqual(self.hook().returncode,0)
    def test_all_modes_priority_and_read_only(self):
        store = Store(self.root)
        project = self.repo/'.claude/relay-mode.local'
        project.parent.mkdir()
        for mode in ('solo', 'auto', 'rules', 'force'):
            atomic(store.paths['ai'], json.dumps({'relay': {'default_mode': mode}}).encode())
            before = store.paths['ai'].read_bytes()
            result = self.hook()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(mode+'（来源：全局默认）', result.stdout)
            self.assertEqual(store.paths['ai'].read_bytes(), before)
            project.write_text(mode+'\n')
            atomic(store.paths['ai'], b'{broken')
            result = self.hook()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(mode+'（来源：项目模式文件）', result.stdout)
            self.assertEqual(project.read_text(), mode+'\n')
            project.unlink()
        for mode in ('required', '', None, [], {}, 1, True):
            atomic(store.paths['ai'], json.dumps({'relay': {'default_mode': mode}}).encode())
            before = store.paths['ai'].read_bytes()
            result = self.hook()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('solo/auto/rules/force', result.stderr)
            self.assertEqual(store.paths['ai'].read_bytes(), before)
        for mode in ('required', '', 'oops'):
            project.write_text(mode)
            result = self.hook()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('solo/auto/rules/force', result.stderr)
            self.assertEqual(project.read_text(), mode)
    def test_ui_mode_order_and_location(self):
        view = (ROOT/'apps/macos/Sources/AIReviewBar/SettingsView.swift').read_text()
        self.assertLess(view.index('Picker("主模型"'), view.index('modelPicker("主模型 ID"'))
        self.assertLess(view.index('modelPicker("主模型 ID"'), view.index('Picker("协作模式"'))
        self.assertLess(view.index('Picker("协作模式"'), view.index('solo 同时停用'))
        self.assertLess(view.index('modelPicker("实施模型 ID"'), view.index('Picker("协作模式"'))
        self.assertIn('Picker("评审服务"', view)
        self.assertIn('认证 / API 密钥', view)
        self.assertIn('SecureField("新 API 密钥', view)
        self.assertIn('if model.draft.channel == "qwen"', view)
        self.assertIn('DisclosureGroup("高级设置")', view)
        self.assertIn('登录状态未检测', view)
        self.assertNotIn('.disabled(model.dirty)', view)
        self.assertIn('Codex 评审失败即停止', view)
        app = (ROOT/'apps/macos/Sources/AIReviewBar/App.swift').read_text()
        self.assertIn('Menu("协作模式："', app)
        self.assertNotIn('评审模式', app)
    def test_project_git_root_from_subdir(self):
        subprocess.run(['/usr/bin/git','init'],cwd=self.repo,capture_output=True,check=True)
        (self.repo/'.claude').mkdir(); (self.repo/'.claude/relay-mode.local').write_text('auto\n')
        sub=self.repo/'sub'; sub.mkdir()
        result=subprocess.run(['bash',str(ROOT/'adapters/claude-code/hooks/session-start.sh')],cwd=sub,env=self.env,capture_output=True,text=True)
        self.assertIn('auto（来源：项目模式文件）',result.stdout)
    def test_codex_review_executable_model_snippet(self):
        command=(ROOT/'adapters/claude-code/commands/codex-review.md').read_text()
        code=next(x for x in re.findall(r'```python\n(.*?)```',command,re.S) if 'model_args' in x)
        scratch=self.root/'scratch'; scratch.mkdir(); (scratch/'review_prompt.txt').write_text('synthetic prompt')
        store=Store(self.root); atomic(store.paths['ai'],b'{"review":{"codex_model":"gpt-6-astra"}}')
        harness='from pathlib import Path\nscratchpad=Path('+repr(str(scratch))+')\n'+code
        fake=self.root/'codex'; fake.write_text('#!'+sys.executable+'\nimport sys,json\nprint(json.dumps(sys.argv[1:]))\n'); fake.chmod(0o700)
        env=dict(self.env,PATH=str(self.root)+':/usr/bin:/bin')
        process=subprocess.run([sys.executable,'-c',harness],env=env,capture_output=True,text=True)
        self.assertEqual(process.returncode,0,process.stderr)
        argv=json.loads((scratch/'review_out.txt').read_text()); self.assertEqual(argv[argv.index('--model')+1],'gpt-6-astra')
        for model in ('', 'x\ny', 4, None):
            atomic(store.paths['ai'],json.dumps({'review':{'codex_model':model}}).encode())
            process=subprocess.run([sys.executable,'-c',harness],env=env,capture_output=True,text=True)
            self.assertNotEqual(process.returncode,0)
        atomic(store.paths['ai'],b'{}')
        subprocess.run([sys.executable,'-c',harness],env=env,check=True)
        self.assertNotIn('--model',json.loads((scratch/'review_out.txt').read_text()))
    def test_qwen_formal_entry_prepare_contract(self):
        # Real parser/preflight/snapshot gates, with fixture HOME and a new output.
        store=Store(self.root)
        atomic(store.paths['ai'],b'{"plugins":{"qwen":{"model":"qwen-fixture","base_url":"https://example.invalid/anthropic","credential_paths":["~/.config/ai-anti/secrets.json"]}}}')
        atomic(store.paths['secrets'],b'{"tokenplan":{"value":"FAKE_OFFLINE_KEY"}}')
        (self.repo/'sample.txt').write_text('baseline\n')
        for argv in (['init'],['add','sample.txt'],['-c','user.name=Fixture','-c','user.email=fixture.invalid','commit','-m','合成检测基线']):
            subprocess.run(['/usr/bin/git',*argv],cwd=self.repo,capture_output=True,check=True)
        (self.repo/'sample.txt').write_text('changed\n')
        out=self.root/'out'
        argv=[sys.executable,str(SCRIPTS/'review.py'),'diff','--repo',str(self.repo),'--files','sample.txt','--scale','单用户本机合成检测，无部署','--out',str(out),'--channel','qwen','--prepare-only']
        result=subprocess.run(argv,env=self.env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        status=json.loads((out/'status.json').read_text()); self.assertEqual(status['status'],'prepared')
        manifest=json.loads((out/'manifest.json').read_text()); self.assertEqual(manifest['mode'],'diff')
        self.assertNotIn('FAKE_OFFLINE_KEY',json.dumps(manifest))
        # Both service slots also pass the formal parser/preflight boundary.
        atomic(store.paths['secrets'],b'{"review_services":{"token_plan":{"value":"ONE_FIXTURE"},"custom_api":{"value":"TWO_FIXTURE"}}}')
        for service in ('token_plan','custom_api'):
            config = json.loads(store.raw('ai'))
            config['plugins']['qwen']['credential_key'] = 'review_services.'+service+'.value'
            atomic(store.paths['ai'],json.dumps(config).encode())
            chosen = list(argv); chosen[chosen.index('--out')+1] = str(self.root/('out-'+service))
            result = subprocess.run(chosen,env=self.env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            manifest = json.loads((self.root/('out-'+service)/'manifest.json').read_text())
            self.assertEqual(manifest['channel'],'qwen')
            self.assertNotIn('ONE_FIXTURE',json.dumps(manifest)); self.assertNotIn('TWO_FIXTURE',json.dumps(manifest))
        # The UI wrapper must match actual parameter names, not an invented runner.
        actual=parser().parse_args(argv[2:]); self.assertEqual(actual.channel,'qwen')
    def test_qwen_command_credential_key_snippets(self):
        command = (ROOT/'plugins/qwen/commands/qwen-review.md').read_text()
        blocks = re.findall(r"python3 - <<'PY'\n(.*?)\nPY", command, re.S)
        self.assertGreaterEqual(len(blocks),2)
        store = Store(self.root)
        q = dict(model='fixture',base_url='https://fixture.invalid',credential_paths=[str(store.paths['secrets'])])
        atomic(store.paths['secrets'], b'{"tokenplan":{"value":"old"},"review_services":{"token_plan":{"value":"one"},"custom_api":{"value":"two"}}}')
        for key, expected in [('review_services.token_plan.value','one'),('review_services.custom_api.value','two'),('tokenplan.value','old')]:
            q['credential_key'] = key
            atomic(store.paths['ai'], json.dumps({'plugins':{'qwen':q}}).encode())
            config = subprocess.run([sys.executable,'-c',blocks[0]],env=self.env,capture_output=True,text=True)
            self.assertEqual(config.returncode,0,config.stderr)
            fields = config.stdout.splitlines(); self.assertEqual(fields[2],key)
            env = dict(self.env,QWEN_CREDENTIAL_KEY=fields[2],QWEN_PATHS='\n'.join(fields[3:]))
            token = subprocess.run([sys.executable,'-c',blocks[1]],env=env,capture_output=True,text=True)
            self.assertEqual(token.stdout.strip(),expected)
        atomic(store.paths['secrets'],b'{"tokenplan":{"value":"old"}}')
        env = dict(self.env,QWEN_CREDENTIAL_KEY='review_services.custom_api.value',QWEN_PATHS=str(store.paths['secrets']))
        token = subprocess.run([sys.executable,'-c',blocks[1]],env=env,capture_output=True,text=True)
        self.assertNotEqual(token.returncode,0); self.assertEqual(token.stdout,'')

    def test_mode_skill_scope_and_incident_notes(self):
        skill=(ROOT/'adapters/codex/skills/codex-relay/SKILL.md').read_text()
        self.assertIn('relay.default_mode',skill); self.assertIn('never a complete relay',skill)
        rules=(ROOT/'adapters/claude-code/CLAUDE.global.md').read_text()
        for date in ('2026-08-06','08-07','08-11','08-12'):
            # Historical notes remain somewhere in their original mechanism files.
            all_mechanism=''.join(p.read_text() for p in (ROOT/'core').glob('*.md'))+rules
            self.assertIn(date,all_mechanism)

if __name__ == '__main__': unittest.main()
