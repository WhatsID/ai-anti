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
        self.assertLess(view.index('Picker("评审模型ID"'), view.index('Picker("实施服务"'))
        self.assertLess(view.index('Picker("实施服务"'), view.index('modelPicker("实施模型 ID"'))
        self.assertIn('Picker("评审服务"', view)
        self.assertIn('SecureField("API密钥"', view)
        self.assertIn('text: $model.draft.apiKeyInput', view)
        self.assertIn('if model.draft.channel == "qwen"', view)
        self.assertIn('Picker("评审执行器"', view)
        self.assertIn('Picker("评审模型ID"', view)
        self.assertIn('登录状态未检测', view)
        self.assertNotIn('.disabled(model.dirty)', view)
        self.assertIn('Codex 评审失败即停止', view)
        app = (ROOT/'apps/macos/Sources/AIReviewBar/App.swift').read_text()
        self.assertIn('Menu("协作模式："', app)
        self.assertNotIn('评审模式', app)
    def test_ui_independent_service_groups(self):
        view = (ROOT/'apps/macos/Sources/AIReviewBar/SettingsView.swift').read_text()
        review = 'ServiceSettingsGroup("评审服务", summary: SettingsSummary.review(model.draft), isExpanded: $reviewExpanded)'
        implementation = 'ServiceSettingsGroup("实施服务", summary: SettingsSummary.implementation(model.draft), isExpanded: $implementationExpanded)'
        self.assertIn('@State private var reviewExpanded = true', view)
        self.assertIn('@State private var implementationExpanded = true', view)
        self.assertIn('accessibilityReduceMotion', view)
        self.assertIn('.easeInOut(duration: 0.2)', view)
        self.assertIn('$0.disablesAnimations = true', view)
        self.assertLess(view.index('Picker("协作模式"'), view.index(review))
        self.assertLess(view.index('空主模型表示保留已有默认值'), view.index(review))
        scroll = view.split('ScrollView {', 1)[1].split('            Divider()', 1)[0]
        self.assertIn('.frame(width: geometry.size.width - 8, alignment: .leading)', scroll)
        self.assertNotIn('Form {', view)
        self.assertNotIn('.background(', view)
        self.assertNotIn('.overlay(', view)
        self.assertIn('Spacer(minLength: 0)', view)
        self.assertIn('Text(title).accessibilityHidden(true)', view)
        self.assertIn('withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.2)) { isExpanded.toggle() }', view)
        self.assertIn('.accessibilityValue(isExpanded ? "展开" : "收起")', view)
        self.assertIn('Text(title).frame(width: SettingsColumns.labelWidth, alignment: .trailing)', view)
        self.assertIn('content().labelsHidden()', view)
        self.assertIn('spacing: SettingsColumns.spacing', view)
        self.assertIn('.padding(.leading, SettingsColumns.labelWidth + SettingsColumns.spacing)', view)
        self.assertIn('selection.labelsHidden().opacity(isExpanded ? 1 : 0)', view)
        self.assertIn('.allowsHitTesting(isExpanded).accessibilityHidden(!isExpanded)', view)
        self.assertIn('.opacity(isExpanded ? 0 : 1).accessibilityHidden(isExpanded)', view)
        self.assertIn('.rotationEffect(.degrees(isExpanded ? 90 : 0))', view)
        self.assertIn('}.frame(height: 26)', view)
        self.assertIn('.transition(.opacity)', view)
        for title in ('主模型', '协作模式', '评审模型 ID', '评审服务地址', '主账号API密钥', '备用账号API密钥', '评审执行器', '评审模型ID', '实施服务地址'):
            self.assertIn('field("' + title + '") {', view)
        self.assertNotIn('field("评审服务")', view)
        self.assertNotIn('field("实施服务")', view)
        picker = view.split('private func modelPicker', 1)[1].split('var body:', 1)[0]
        self.assertIn('field(title)', picker)
        review_body = view.split(review, 1)[1].split(implementation, 1)[0]
        implementation_body = view.split(implementation, 1)[1].split('            Divider()', 1)[0]
        for item in ('Picker("评审服务"', 'SecureField("API密钥"', 'Picker("评审执行器"', 'Picker("评审模型ID"', 'executorMigrationPending', 'selectedCredentialMessage', 'claudeFallbacks'):
            self.assertIn(item, review_body)
            self.assertNotIn(item, implementation_body)
        for item in ('Picker("实施服务"', 'modelPicker("实施模型 ID"', 'TextField("实施服务地址"', 'SecureField("主账号API密钥", text: $model.draft.implementationKeyInput)'):
            self.assertIn(item, implementation_body)
            self.assertNotIn(item, review_body)
        self.assertGreater(view.index('Button("检查配置"'), view.index(implementation))
        summary = view.split('enum SettingsSummary {', 1)[1].split('private struct ServiceSettingsGroup', 1)[0]
        self.assertIn('ReviewService.title(draft.service)', summary)
        self.assertIn('draft.tokenPlanImplementationModel', summary)
        self.assertIn('Claude Code 实施代理', summary)
        self.assertIn('CLI 默认', summary)
        self.assertIn('未指定', summary)
        self.assertIn('solo 已停用', summary)
        for private_field in ('secret', 'apiKeyInput', 'baseURL', 'credential', 'implementationURL'):
            self.assertNotIn('draft.' + private_field, summary)

    def test_project_git_root_from_subdir(self):
        subprocess.run(['/usr/bin/git','init'],cwd=self.repo,capture_output=True,check=True)
        (self.repo/'.claude').mkdir(); (self.repo/'.claude/relay-mode.local').write_text('auto\n')
        sub=self.repo/'sub'; sub.mkdir()
        result=subprocess.run(['bash',str(ROOT/'adapters/claude-code/hooks/session-start.sh')],cwd=sub,env=self.env,capture_output=True,text=True)
        self.assertIn('auto（来源：项目模式文件）',result.stdout)
    def test_swift_budget_covers_shared_maximum_and_cleanup(self):
        scripts=ROOT/'adapters/codex/skills/claude-review/scripts'
        sys.path.insert(0,str(scripts))
        from ai_anti_config import review_limits, ConfigError
        maximum=review_limits({'review':{'timeout':3600}})[0]
        with self.assertRaises(ConfigError): review_limits({'review':{'timeout':3601}})
        bridge=(ROOT/'apps/macos/Sources/AIReviewBar/Bridge.swift').read_text()
        outer=int(re.search(r'action == "real" \? (\d+)',bridge).group(1))
        self.assertEqual(outer,3700); self.assertGreaterEqual(outer,maximum+60)

    def test_codex_review_executable_model_snippet(self):
        command=(ROOT/'adapters/claude-code/commands/codex-review.md').read_text()
        code=next(x for x in re.findall(r'```python\n(.*?)```',command,re.S) if 'model_args' in x)
        scratch=self.root/'scratch'; scratch.mkdir(); (scratch/'review_prompt.txt').write_text('synthetic prompt')
        store=Store(self.root); atomic(store.paths['ai'],b'{"review":{"codex_model":"gpt-6-astra"}}')
        harness='from pathlib import Path\nreview_inputs=[]\nai_anti_root=Path('+repr(str(ROOT))+')\nscratchpad=Path('+repr(str(scratch))+')\n'+code
        fake=self.root/'codex'; fake.write_text('#!'+sys.executable+'\nimport sys,json\nfor e in [{"type":"thread.started","thread_id":"fixture"},{"type":"turn.started"},{"type":"item.completed","item":{"id":"final","type":"agent_message","text":json.dumps(sys.argv[1:])}},{"type":"turn.completed"}]:print(json.dumps(e))\n'); fake.chmod(0o700)
        env=dict(self.env,PATH=str(self.root)+':/usr/bin:/bin')
        process=subprocess.run([sys.executable,'-c',harness],env=env,capture_output=True,text=True)
        self.assertEqual(process.returncode,0,process.stderr)
        argv=json.loads((scratch/'review_out.txt').read_text()); self.assertEqual(argv[argv.index('--model')+1],'gpt-6-astra')
        for model in ('', 'x\ny', 4, None):
            atomic(store.paths['ai'],json.dumps({'review':{'codex_model':model}}).encode())
            process=subprocess.run([sys.executable,'-c',harness],env=env,capture_output=True,text=True)
            self.assertNotEqual(process.returncode,0)
        atomic(store.paths['ai'],b'{}')
        for output_file in scratch.glob('review*'):
            if output_file.name != 'review_prompt.txt': output_file.unlink()
        subprocess.run([sys.executable,'-c',harness],env=env,check=True)
        self.assertNotIn('--model',json.loads((scratch/'review_out.txt').read_text()))
        self.assertEqual(json.loads((scratch/'review_out.txt.warnings.json').read_text()), [])
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
        atomic(store.paths['secrets'],b'{"model_services":{"token_plan":{"value":"ONE_FIXTURE"},"custom_api":{"value":"TWO_FIXTURE"}}}')
        for service in ('token_plan','custom_api'):
            config = json.loads(store.raw('ai'))
            config['plugins']['qwen']['credential_key'] = 'model_services.'+service+'.value'
            atomic(store.paths['ai'],json.dumps(config).encode())
            chosen = list(argv); chosen[chosen.index('--out')+1] = str(self.root/('out-'+service))
            result = subprocess.run(chosen,env=self.env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            manifest = json.loads((self.root/('out-'+service)/'manifest.json').read_text())
            self.assertEqual(manifest['channel'],'qwen')
            self.assertNotIn('ONE_FIXTURE',json.dumps(manifest)); self.assertNotIn('TWO_FIXTURE',json.dumps(manifest))
        # The UI wrapper must match actual parameter names, not an invented runner.
        actual=parser().parse_args(argv[2:]); self.assertEqual(actual.channel,'qwen')
    def test_qwen_command_bounded_call_chain(self):
        command=(ROOT/'plugins/qwen/commands/qwen-review.md').read_text()
        shell=next(b for b in re.findall(r'```bash\n(.*?)```',command,re.S) if 'BOUNDED_REVIEW=' in b)
        store=Store(self.root)
        atomic(store.paths['secrets'],b'{"tokenplan":{"value":"offline-fixture"}}')
        q=dict(model='fixture',base_url='https://fixture.invalid',credential_paths=[str(store.paths['secrets'])])
        atomic(store.paths['ai'],json.dumps({'review':{'timeout':12,'idle_timeout':7},'plugins':{'qwen':q}}).encode())
        commands=self.root/'.claude/commands'; commands.mkdir(parents=True)
        (commands/'qwen-review.md').symlink_to(ROOT/'plugins/qwen/commands/qwen-review.md')
        scratch=self.root/'scratch'; scratch.mkdir(); (scratch/'qwen_prompt.txt').write_text('offline prompt')
        fake=self.root/'claude'
        fake.write_text('#!'+sys.executable+'\nimport os,sys,json\nassert sys.stdin.read()==""\nassert os.environ["ANTHROPIC_API_KEY"]=="offline-fixture"\nassert "ANTHROPIC_AUTH_TOKEN" not in os.environ\nassert os.environ["ANTHROPIC_DEFAULT_SONNET_MODEL"]=="fixture"\nprint(json.dumps({"type":"result","subtype":"success","result":"offline report","permission_denials":[],"argv":sys.argv[1:],"is_error":False}))\n')
        fake.chmod(0o700)
        env=dict(self.env,PATH=str(self.root)+':'+os.path.dirname(sys.executable)+':/usr/bin:/bin',CLAUDE_CONFIG_DIR=str(commands.parent),ANTHROPIC_API_KEY='must-remove')
        # Replace only the documented scratchpad placeholder, keeping the real template chain.
        material=self.repo/'input.txt'; material.write_text('declared source')
        shell=shell.replace('<scratchpad>',str(scratch)).replace('<declared-input-path>',str(material))
        run=subprocess.run(['bash','-c',shell],env=env,cwd=self.repo,capture_output=True,text=True,timeout=20)
        self.assertEqual(run.returncode,0,run.stderr)
        output=json.loads((scratch/'qwen_review.json').read_text())
        self.assertIn('offline prompt',output['argv']); self.assertIn('--allowedTools',output['argv'])
        self.assertIn('timeout=12 idle_timeout=7',run.stderr)
        self.assertEqual((scratch/'qwen_report.txt').read_text(),'offline report')

    def test_qwen_command_responses_branch_contract(self):
        from ai_anti_config import ConfigError, claude_qwen_executor
        from review import parser
        command = (ROOT/'plugins/qwen/commands/qwen-review.md').read_text()
        self.assertIn('claude_qwen_executor(ai_anti_config.load())', command)
        self.assertIn('审指定已提交 commit 时停止', command)
        for executor in (None, 'claude', 'codex'):
            config = {'plugins': {'qwen': {'claude_executor': executor}},
                      'codex_relay': {'qwen_executor': 'codex' if executor != 'codex' else 'claude'}}
            self.assertEqual(claude_qwen_executor(config), executor or 'claude')
        self.assertEqual(claude_qwen_executor({}), 'claude')
        for value in ('responses', False, {'provider': 'codex'}):
            with self.assertRaises(ConfigError):
                claude_qwen_executor({'plugins': {'qwen': {'claude_executor': value}}})
        import shlex
        block = next(b for b in re.findall(r'```bash\n(.*?)```', command, re.S)
                     if '--qwen-executor codex' in b)
        lines = block.replace('\\\n', '').splitlines()[1:]
        self.assertEqual(len(lines), 2)
        for mode, line in zip(('plan', 'diff'), lines):
            args = parser().parse_args(shlex.split(line)[2:-2])
            self.assertEqual((args.mode, args.channel, args.qwen_executor), (mode, 'qwen', 'codex'))
            self.assertEqual(args.plan, '<方案相对路径>' if mode == 'plan' else None)
            expected = '<选定源码相对路径>' if mode == 'plan' else '<本任务改动文件相对路径>'
            self.assertEqual(args.files, [expected])

    def test_qwen_command_shared_credential_resolver(self):
        command = (ROOT/'plugins/qwen/commands/qwen-review.md').read_text()
        self.assertIn('--token-plan-config',command)
        self.assertNotIn('TOKENPLAN_KEY=',command)
        from review_channels import resolve, child_environment
        store=Store(self.root)
        q=dict(model='fixture',base_url='https://fixture.invalid',credential_paths=[str(store.paths['secrets'])])
        atomic(store.paths['secrets'],b'{"tokenplan":{"value":"old"},"model_services":{"token_plan":{"value":"one"},"custom_api":{"value":"two"}}}')
        for key,expected in [('model_services.token_plan.value','one'),('model_services.custom_api.value','two'),('tokenplan.value','old')]:
            q['credential_key']=key
            route=resolve({'plugins':{'qwen':q}},'qwen',None,self.repo,{},executor='claude')
            self.assertEqual(child_environment(route)['ANTHROPIC_API_KEY'],expected)
        q['credential_key']='model_services.custom_api.value'
        atomic(store.paths['secrets'],b'{"tokenplan":{"value":"old"}}')
        with self.assertRaises(ValueError): child_environment(resolve({'plugins':{'qwen':q}},'qwen',None,self.repo,{},executor='claude'))

    def test_implementation_provider_backend_runner_and_ui_contract(self):
        import importlib.util
        path = ROOT/'adapters/codex/skills/codex-relay/scripts/implementation_config.py'
        spec = importlib.util.spec_from_file_location('desktop_implementation_config', path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        from config_store import implementation_provider, StoreError
        for value in (None, 'codex', 'token_plan'):
            self.assertEqual(implementation_provider(value), module.provider(value))
        for value in ('glm-5.3', [], {}, True, 3):
            with self.assertRaises(StoreError): implementation_provider(value)
            with self.assertRaises(ValueError): module.provider(value)
        self.assertEqual(module.MODEL, 'glm-5.3')
        model = (ROOT/'apps/macos/Sources/AIReviewBar/ModelCatalog.swift').read_text()
        self.assertIn('static let implementationGLM = "glm-5.3"', model)
        view = (ROOT/'apps/macos/Sources/AIReviewBar/SettingsView.swift').read_text()
        self.assertIn('.disabled(!model.draft.implementationEnabled)', view)
        self.assertIn('model.draft.host == "claude" ? $model.draft.claudeImplementationProvider : $model.draft.implementationProvider', view)
        self.assertIn('$model.draft.implementationKeyInput', view)
        self.assertIn('backend/implementation_models.py', (ROOT/'apps/macos/build-app.sh').read_text())
        import implementation_models
        self.assertEqual(implementation_models.parse('qwen4-max'), 'qwen4-max')
        self.assertEqual(implementation_models.parse(None), 'glm-5.3')
        with self.assertRaises(ValueError): implementation_models.parse('')
        # Existing package includes the single shared pure migration dependency.
        self.assertIn('backend/model_services.py', (ROOT/'apps/macos/build-app.sh').read_text())
        skill = (ROOT/'adapters/codex/skills/codex-relay/SKILL.md').read_text()
        for requirement in ('--prepare-only', 'requested_only', 'never `active_api`', 'primary acceptance'):
            self.assertIn(requirement, skill)

    def test_claude_routes_match_cli_validation(self):
        import ai_anti_config as cli
        import config_store as desktop

        implementation_samples = [
            {}, {'claude_relay': None}, {'claude_relay': []}, {'claude_relay': {}},
            *[{'claude_relay': {'implementation': value}} for value in (
                None, False, 'codex', [], {}, {'provider': 'wrong', 'model': 'valid'},
                {'provider': 'codex'}, {'provider': 'codex', 'model': 3},
                {'provider': 'codex', 'model': ''}, {'provider': 'codex', 'model': 'two words'},
                {'provider': 'codex', 'model': 'white\u2003space'},
                {'provider': 'codex', 'model': 'gpt-6.2-sol', 'future': {'keep': True}},
                {'provider': 'codex', 'model': 'custom/model:tag'},
            )],
        ]
        for sample in implementation_samples:
            with self.subTest(implementation=sample):
                try:
                    expected = cli.claude_implementation(sample)
                except cli.ConfigError:
                    with self.assertRaises(desktop.StoreError):
                        desktop.claude_implementation(sample)
                else:
                    self.assertEqual(desktop.claude_implementation(sample),
                                     ('claude-agent', '') if expected is None else ('codex', expected['model']))

        executor_samples = [
            {}, {'plugins': None}, {'plugins': 3}, {'plugins': {'qwen': None}}, {'plugins': {'qwen': []}},
            *[{'plugins': {'qwen': {'claude_executor': value}}} for value in (
                None, 'claude', 'codex', '', ' codex', 'Responses', True, 2, [], {},
            )],
        ]
        for sample in executor_samples:
            with self.subTest(executor=sample):
                try:
                    expected = cli.claude_qwen_executor(sample)
                except cli.ConfigError:
                    with self.assertRaises(desktop.StoreError):
                        desktop.claude_qwen_executor(sample)
                else:
                    self.assertEqual(desktop.claude_qwen_executor(sample), expected)

    def test_mode_skill_scope_and_incident_notes(self):
        skill=(ROOT/'adapters/codex/skills/codex-relay/SKILL.md').read_text()
        self.assertIn('relay.default_mode',skill); self.assertIn('never a complete relay',skill)
        rules=(ROOT/'adapters/claude-code/CLAUDE.global.md').read_text()
        for date in ('2026-08-06','08-07','08-11','08-12'):
            # Historical notes remain somewhere in their original mechanism files.
            all_mechanism=''.join(p.read_text() for p in (ROOT/'core').glob('*.md'))+rules
            self.assertIn(date,all_mechanism)

if __name__ == '__main__': unittest.main()
