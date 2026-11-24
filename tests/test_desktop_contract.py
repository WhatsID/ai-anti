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
        self.assertIn('model.draft.activeOfficialReview == nil ? $model.draft.apiKeyInput : $model.draft.implementationKeyInput', view)
        self.assertIn('SecureField("API密钥", text: keyInput)', view)
        self.assertIn('if model.draft.channel == "qwen"', view)
        self.assertIn('Picker("评审执行器"', view)
        self.assertIn('Picker("评审模型ID"', view)
        self.assertIn('登录状态未检测', view)
        self.assertNotIn('使用现有 Codex CLI 登录', view)
        self.assertIn('} else if model.draft.channel == "qoder" {', view)
        self.assertIn('使用现有 Qoder CLI 登录；登录状态未检测。当前固定 Kimi-K3.1-Pro；仅支持 Codex 主模型。', view)
        self.assertNotIn('.disabled(model.dirty)', view)
        self.assertIn('Codex 评审失败即停止', view)
        app = (ROOT/'apps/macos/Sources/AIReviewBar/App.swift').read_text()
        # 0.3.8 起菜单栏下拉只保留当前配置、设置与退出；具体设置项只在设置窗口中修改。
        menu = app.split('MenuBarExtra {', 1)[1].split('} label:', 1)[0]
        for item in ('Menu("主模型")', 'Menu("评审服务")', 'Menu("协作模式：', 'Text(model.message)'):
            self.assertNotIn(item, menu)
        # 0.3.9 起「当前配置」按主模型、评审服务、实施服务、协作模式分行显示。
        rows = ['Text("当前配置")', 'Text("主模型：" + model.menuPrimary)', 'Text("评审服务：" + model.menuReview)',
                'Text("实施服务：" + model.menuImplementation)', 'Text("协作模式：" + model.saved.mode)']
        self.assertEqual([menu.index(row) for row in rows], sorted(menu.index(row) for row in rows))
        self.assertLess(menu.index(rows[-1]), menu.index('Button("设置")'))
        self.assertIn('if model.messageIsError { Text("⚠︎ " + model.message) }', menu)
        self.assertEqual(menu.count('Button('), 2)
        self.assertLess(menu.index('Button("设置")'), menu.index('Button("退出"'))
        self.assertNotIn('评审模式', app)
    def test_cli_status_is_under_primary_picker_and_uses_settings_lifecycle(self):
        sources = ROOT / 'apps/macos/Sources/AIReviewBar'
        view = (sources / 'SettingsView.swift').read_text()
        app = (sources / 'App.swift').read_text()
        cli = (sources / 'CLIInstallView.swift').read_text()
        rows = ('Picker("主模型"', 'CLIStatusSection(model: model)', 'modelPicker("主模型 ID"')
        positions = [view.index(row) for row in rows]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('SettingsColumns.labelWidth + SettingsColumns.spacing', cli)
        self.assertIn('.sheet(item: $installTarget)', view)
        self.assertIn('.onChange(of: model.requiredCLINames) { _ in model.ensureCLIStatus() }', view)
        self.assertEqual(len(re.findall(r'model\.refreshOnOpen\(\)\s+model\.probeOnSettingsOpen\(\)', app)), 2)
        self.assertIn('windowWillClose', app)
        self.assertIn('Text(installedVersions).font(.caption).foregroundStyle(.secondary)', cli)
        self.assertIn('RequiredCLI.legacyQwenReviewRole', cli)
        self.assertIn('尚未检测当前配置所需的 CLI', cli)
        self.assertIn('配置尚未读入，无法检测 CLI', cli)
        self.assertIn('已找到，版本读取失败', cli)
        self.assertIn('if status.offersInstallation', cli)
        self.assertIn('Link("打开官方安装说明", destination: tool.docsURL)', cli)
        self.assertIn('Button("重新检测", action: model.recheckCLIs)', cli)

    def test_cli_install_uses_documented_official_commands_and_system_terminal(self):
        sources = ROOT / 'apps/macos/Sources/AIReviewBar'
        catalog = (sources / 'CLITool.swift').read_text()
        sheet = (sources / 'CLIInstallView.swift').read_text()
        readme = (ROOT / 'apps/macos/README.md').read_text()
        general = (ROOT / 'README.zh-CN.md').read_text().replace(r'\|', '|')
        commands = (
            'curl -fsSL https://claude.ai/install.sh | bash',
            'brew install --cask codex',
            'curl -fsSL https://chatgpt.com/codex/install.sh | sh',
            'curl -fsSL https://antigravity.google/cli/install.sh | bash',
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(f'"{command}"', catalog)
                self.assertIn(f'`{command}`', readme)
        for command in (commands[0], commands[1], commands[3]):
            self.assertIn(command, general)
        self.assertIn('"Qoder CLI", "https://docs.qoder.com/cli/installation", methods: []', catalog)
        self.assertNotIn('npm', catalog + sheet)
        for removed in ('osascript', 'rm -rf', 'NSAppleEventsUsageDescription'):
            self.assertNotIn(removed, catalog + sheet + (ROOT / 'apps/macos/build-app.sh').read_text())
        self.assertIn('mkdtemp(', catalog)
        self.assertIn('O_CREAT | O_EXCL | O_WRONLY, 0o700', catalog)
        self.assertIn('withBundleIdentifier: "com.apple.Terminal"', catalog)
        self.assertIn('withApplicationAt: terminal', catalog)
        self.assertIn('Link(tool.docsURL.absoluteString, destination: tool.docsURL)', sheet)
        self.assertIn('NSPasteboard.general.setString(method.command, forType: .string)', sheet)
        self.assertIn('Button("在终端中打开并安装", action: launch)', sheet)
        self.assertIn('Text(launchError).foregroundStyle(.red)', sheet)

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
        self.assertLess(view.index('solo 同时停用'), view.index(review))
        for removed in ('空主模型表示保留已有默认值', '已配置回退列表', '请直接编辑 ~/.config/ai-anti/config.json'):
            self.assertNotIn(removed, view)
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
        for title in ('主模型', '协作模式', '评审模型 ID', '评审服务地址（Responses）', '主账号API密钥', '备用账号API密钥', '评审执行器', '评审模型ID', '实施服务地址'):
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
        self.assertRegex(review_body, r'if model\.showsReviewAddress \{\s*field\("评审服务地址（Responses）"\)')
        self.assertIn('TextField("评审服务地址（Responses）", text: $model.draft.responsesBaseURL, axis: .vertical)', review_body)
        for body in (review_body, implementation_body):
            self.assertLess(body.index('Text(ReviewService.title("qoder")).tag("qoder")'), body.rindex('Text("API 直连").tag("api_direct")'))
        self.assertIn('Text(verbatim: baseURL + "/responses")', review_body)
        executor_field = review_body.split('field("评审执行器")', 1)[1].split('field("评审模型ID")', 1)[0]
        self.assertIn('selection: .constant(model.draft.channel)', executor_field)
        self.assertIn('Text(model.reviewExecutorLabel)', executor_field)
        self.assertIn('.disabled(true)', executor_field)
        for removed in ('Picker("Qwen 评审执行器"', 'claudeQwenExecutor', 'Anthropic', '$model.draft.baseURL'):
            self.assertNotIn(removed, review_body)
        self.assertIn('Qwen 评审经 Codex CLI · Responses 执行', review_body)
        for item in ('Picker("实施服务"', 'modelPicker("实施模型 ID"', 'field("实施服务地址"', 'SecureField("主账号API密钥", text: $model.draft.implementationKeyInput)'):
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

    def _codex_review_snippet(self):
        command=(ROOT/'adapters/claude-code/commands/codex-review.md').read_text()
        code=next(x for x in re.findall(r'```python\n(.*?)```',command,re.S) if 'model_args' in x)
        store=Store(self.root)
        fake = self.root/'codex'
        fake.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
from pathlib import Path
Path(os.environ['FIXTURE_CODEX_CALLED']).touch()
for event in [
    {'type': 'thread.started', 'thread_id': 'fixture'},
    {'type': 'turn.started'},
    {'type': 'item.completed', 'item': {'id': 'final', 'type': 'agent_message', 'text': json.dumps(sys.argv[1:])}},
    {'type': 'turn.completed'},
]:
    print(json.dumps(event))
''')
        fake.chmod(0o700)
        env=dict(self.env,PATH=str(self.root)+':/usr/bin:/bin')
        env.pop('risk_tier', None)

        def invoke(config, tier=None, env_tier=None):
            case = Path(tempfile.mkdtemp(dir=self.root))
            (case/'review_prompt.txt').write_text('synthetic prompt')
            atomic(store.paths['ai'], json.dumps(config).encode())
            harness = f'from pathlib import Path\nreview_inputs=[]\nai_anti_root=Path({str(ROOT)!r})\nscratchpad=Path({str(case)!r})\n'
            if tier is not None:
                harness += f'risk_tier = {tier!r}\n'
            case_env = dict(env, FIXTURE_CODEX_CALLED=str(case/'cli-called'))
            if env_tier is not None:
                case_env['risk_tier'] = env_tier
            result = subprocess.run([sys.executable, '-c', harness + code], env=case_env,
                                    capture_output=True, text=True)
            return result, case
        return invoke

    def test_codex_review_executable_model_and_effort_snippet(self):
        invoke = self._codex_review_snippet()
        model_args = ['--model', 'gpt-6-astra']
        effort_args = ['-c', 'model_reasoning_effort="xhigh"']
        for config, expected in (
            ({}, []),
            ({'review': {'codex_effort': None}}, []),
            ({'review': {'codex_model': 'gpt-6-astra'}}, model_args),
            ({'review': {'codex_effort': 'xhigh'}}, effort_args),
            ({'review': {'codex_model': 'gpt-6-astra', 'codex_effort': 'xhigh'}}, model_args + effort_args),
        ):
            with self.subTest(config=config):
                process, case = invoke(config)
                self.assertEqual(process.returncode, 0, process.stderr)
                argv = json.loads((case/'review_out.txt').read_text())
                self.assertEqual(argv, ['exec', '--json', '--sandbox', 'read-only', *expected, 'synthetic prompt'])
                self.assertEqual(json.loads((case/'review_out.txt.warnings.json').read_text()), [])
        for key, invalid_values in (('codex_model', ('', 'x\ny', 4, None)),
                                    ('codex_effort', ('XHIGH', 'x high', 1, 'minimal'))):
            for value in invalid_values:
                with self.subTest(key=key, value=value):
                    process, case = invoke({'review': {key: value}})
                    self.assertNotEqual(process.returncode, 0)
                    self.assertFalse((case/'review_raw.jsonl').exists())

    def test_codex_review_snippet_uses_python_risk_tier(self):
        invoke = self._codex_review_snippet()
        config = {'review': {'codex_effort': 'high', 'codex_high_risk_effort': 'xhigh'}}
        for tier, env_tier, expected in (
            ('standard', None, 'high'), ('high_risk', None, 'xhigh'),
            (None, None, 'xhigh'), (None, 'standard', 'xhigh'),
        ):
            with self.subTest(tier=tier, env_tier=env_tier):
                process, case = invoke(config, tier=tier, env_tier=env_tier)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertTrue((case/'cli-called').exists())
                self.assertEqual(json.loads((case/'review_out.txt').read_text()), [
                    'exec', '--json', '--sandbox', 'read-only', '-c',
                    'model_reasoning_effort=' + json.dumps(expected), 'synthetic prompt',
                ])
        process, case = invoke(config, tier='bogus')
        self.assertNotEqual(process.returncode, 0)
        self.assertIn('review risk tier must be one of standard, high_risk', process.stderr)
        self.assertFalse((case/'cli-called').exists())

    def test_codex_review_snippet_preserves_argv_without_high_risk_effort(self):
        invoke = self._codex_review_snippet()
        for tier in ('standard', 'high_risk', None):
            with self.subTest(tier=tier):
                process, case = invoke({'review': {'codex_effort': 'xhigh'}}, tier=tier)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(json.loads((case/'review_out.txt').read_text()), [
                    'exec', '--json', '--sandbox', 'read-only', '-c',
                    'model_reasoning_effort="xhigh"', 'synthetic prompt',
                ])

    def test_codex_review_snippet_rejects_inverted_effort_before_cli(self):
        invoke = self._codex_review_snippet()
        config = {'review': {'codex_effort': 'xhigh', 'codex_high_risk_effort': 'high'}}
        for tier in ('standard', 'high_risk', None):
            with self.subTest(tier=tier):
                process, case = invoke(config, tier=tier)
                self.assertNotEqual(process.returncode, 0)
                self.assertIn('review.codex_high_risk_effort must not be lower than review.codex_effort', process.stderr)
                self.assertFalse((case/'cli-called').exists())

    def test_app_save_preserves_claude_codex_efforts(self):
        store = Store(self.root)
        atomic(store.paths['ai'], json.dumps({
            'review': {'codex_model': 'gpt-6-astra', 'codex_effort': 'high', 'codex_high_risk_effort': 'xhigh'},
            'claude_relay': {'implementation': {'provider': 'codex', 'model': 'gpt-6.2-sol', 'effort': 'high'}},
        }).encode())
        for updates in ({'primary_model': 'claude-test-primary'},
                        {'reviewer_model': 'gpt-6.2-astra', 'claude_implementation_model': 'gpt-6.2-astra'}):
            with self.subTest(updates=updates):
                state = store.read()
                saved = store.save(dict(state['values'], **updates), state['fingerprints'])
                for key, value in updates.items():
                    self.assertEqual(saved['values'][key], value)
                config = json.loads(store.raw('ai'))
                self.assertEqual(config['review']['codex_effort'], 'high')
                self.assertEqual(config['review']['codex_high_risk_effort'], 'xhigh')
                self.assertEqual(config['claude_relay']['implementation']['effort'], 'high')

    def test_app_save_preserves_agy_relay(self):
        store = Store(self.root)
        agy_relay = {
            'review': {'provider': 'codex', 'model': 'agy-review-model', 'effort': 'high', 'high_risk_effort': 'xhigh'},
            'implementation': {'provider': 'codex', 'model': 'agy-implementation-model', 'effort': 'high'},
        }
        atomic(store.paths['ai'], json.dumps({'agy_relay': agy_relay}).encode())
        state = store.read()
        self.assertFalse(state['values']['agy_relay_error'])
        saved = store.save(dict(state['values'], mode='auto', reviewer_model='gpt-6.2-astra'), state['fingerprints'])
        self.assertEqual(saved['values']['mode'], 'auto')
        self.assertEqual(saved['values']['reviewer_model'], 'gpt-6.2-astra')
        self.assertEqual(json.loads(store.raw('ai'))['agy_relay'], agy_relay)

    def test_app_save_and_switch_off_codex_preserves_claude_dual_plan(self):
        store = Store(self.root)
        dual_plan = {
            'enabled': True,
            'partner': 'agy',
            'model': 'agy-plan-model',
            'effort': 'high',
            'synthesizer': 'agy',
            'on_failure': 'fallback',
        }
        atomic(store.paths['ai'], json.dumps({
            'review': {'codex_model': 'gpt-6-astra'},
            'claude_relay': {
                'implementation': {'provider': 'codex', 'model': 'gpt-6.2-sol', 'effort': 'high'},
                'dual_plan': dual_plan,
            },
        }).encode())

        # Ordinary save preserves claude_relay.dual_plan verbatim
        state = store.read()
        values = dict(state['values'], mode='auto', reviewer_model='gpt-6.2-astra')
        values.pop('dual_plan_enabled')
        store.save(values, state['fingerprints'])
        config = json.loads(store.raw('ai'))
        self.assertEqual(config['claude_relay']['dual_plan'], dual_plan)

        # Switching Claude implementation away from Codex removes implementation but keeps dual_plan
        state = store.read()
        values = dict(state['values'], claude_implementation_provider='claude-agent')
        values.pop('dual_plan_enabled')
        store.save(values, state['fingerprints'])
        config = json.loads(store.raw('ai'))
        self.assertNotIn('implementation', config['claude_relay'])
        self.assertEqual(config['claude_relay']['dual_plan'], dual_plan)
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
            config['plugins']['qwen']['base_url'] = (
                'https://token-plan.maas.qianwenaiapi.com/apps/anthropic'
                if service == 'token_plan' else 'https://example.invalid/anthropic')
            atomic(store.paths['ai'],json.dumps(config).encode())
            chosen = list(argv); chosen[chosen.index('--out')+1] = str(self.root/('out-'+service))
            result = subprocess.run(chosen,env=self.env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            manifest = json.loads((self.root/('out-'+service)/'manifest.json').read_text())
            self.assertEqual(manifest['channel'],'qwen')
            self.assertNotIn('ONE_FIXTURE',json.dumps(manifest)); self.assertNotIn('TWO_FIXTURE',json.dumps(manifest))
        config['plugins']['qwen']['credential_key'] = 'model_services.token_plan.value'
        atomic(store.paths['ai'], json.dumps(config).encode())
        rejected = list(argv)
        rejected[rejected.index('--out') + 1] = str(self.root / 'refused')
        result = subprocess.run(rejected, env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('官方 HTTPS', result.stderr)
        self.assertFalse((self.root / 'refused').exists())
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
        from ai_anti_config import claude_qwen_executor
        from review import parser
        command = (ROOT/'plugins/qwen/commands/qwen-review.md').read_text()
        self.assertIn('claude_qwen_executor(ai_anti_config.load())', command)
        self.assertIn('claude_relay.qwen_executor', command)
        self.assertIn('审指定已提交 commit 时停止', command)
        for executor in (None, 'claude', 'codex'):
            config = {'claude_relay': {'qwen_executor': executor},
                      'codex_relay': {'qwen_executor': 'codex' if executor != 'codex' else 'claude'}}
            self.assertEqual(claude_qwen_executor(config), executor or 'claude')
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
            q['base_url'] = ('https://token-plan.maas.qianwenaiapi.com/apps/anthropic'
                             if key == 'model_services.token_plan.value' else 'https://fixture.invalid')
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
        for value in (None, 'codex', 'token_plan', 'custom_api'):
            self.assertEqual(implementation_provider(value), module.provider(value))
        for value in ('glm-5.3', [], {}, True, 3):
            with self.assertRaises(StoreError): implementation_provider(value)
            with self.assertRaises(ValueError): module.provider(value)
        self.assertEqual(module.MODEL, 'glm-5.3')
        model = (ROOT/'apps/macos/Sources/AIReviewBar/ModelCatalog.swift').read_text()
        self.assertIn('static let implementationGLM = "glm-5.3"', model)
        view = (ROOT/'apps/macos/Sources/AIReviewBar/SettingsView.swift').read_text()
        self.assertIn('.disabled(!model.draft.implementationEnabled)', view)
        self.assertEqual(view.count('Text("API 直连").tag("api_direct")'), 3)
        self.assertIn('vendorPicker(reviewService, custom: true)', view)
        self.assertIn('vendorPicker($model.draft.implementationService, custom: false)', view)
        self.assertIn('return $model.draft.claudeImplementationProvider', view)
        self.assertIn('return serviceGroup($model.draft.implementationService, preferred: model.draft.directImplementation?.service)', view)
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
        for sample in [None, [], *implementation_samples]:
            with self.subTest(implementation=sample):
                try:
                    expected = cli.claude_implementation(sample)
                except cli.ConfigError:
                    with self.assertRaises(desktop.StoreError):
                        desktop.claude_implementation(sample)
                else:
                    self.assertEqual(desktop.claude_implementation(sample),
                                     ('claude-agent', '') if expected is None else ('codex', expected['model']))

        # Executor function contract. Store.read() also validates the full Qwen section.
        new_key = 'claude_relay.qwen_executor'
        old_key = 'plugins.qwen.claude_executor'
        new_codex = {'claude_relay': {'qwen_executor': 'codex'}}
        old_codex = {'plugins': {'qwen': {'claude_executor': 'codex'}}}
        both_null = {'claude_relay': {'qwen_executor': None}, 'plugins': {'qwen': {'claude_executor': None}}}
        null_qwen = dict(new_codex, plugins={'qwen': None})
        valid_samples = [
            (None, 'claude'), ({}, 'claude'), ({'claude_relay': {}}, 'claude'),
            ({'claude_relay': None, 'plugins': None}, 'claude'), (both_null, 'claude'),
            (null_qwen, 'codex'),
            (dict(new_codex, plugins=None), 'codex'),
            (dict(old_codex, claude_relay=None), 'codex'),
        ]
        for value in ('claude', 'codex'):
            new = {'claude_relay': {'qwen_executor': value}}
            old = {'plugins': {'qwen': {'claude_executor': value}}}
            valid_samples.extend([
                (new, value), (old, value), ({**new, **old}, value),
                (dict(both_null, **new), value), (dict(both_null, **old), value),
            ])
        invalid_samples = [
            ([], (new_key,)),
            (dict(old_codex, claude_relay=[]), (new_key,)),
            (dict(new_codex, plugins=3), (old_key,)),
            (dict(new_codex, plugins={'qwen': []}), (old_key,)),
            (dict(old_codex, claude_relay={'qwen_executor': 'claude'}), (new_key, old_key)),
            (dict(new_codex, plugins={'qwen': {'claude_executor': 'claude'}}), (new_key, old_key)),
        ]
        for value in ('', ' ', ' codex', 'CODEX', 'responses', False, 2, [], {}):
            invalid_samples.extend([
                (dict(old_codex, claude_relay={'qwen_executor': value}), (new_key,)),
                (dict(new_codex, plugins={'qwen': {'claude_executor': value}}), (old_key,)),
            ])
        for resolve_executor, error in ((cli.claude_qwen_executor, cli.ConfigError),
                                        (desktop.claude_qwen_executor, desktop.StoreError)):
            for sample, expected in valid_samples:
                with self.subTest(parser=resolve_executor.__module__, executor=sample):
                    self.assertEqual(resolve_executor(sample), expected)
            for sample, keys in invalid_samples:
                with self.subTest(parser=resolve_executor.__module__, executor=sample):
                    with self.assertRaises(error) as raised:
                        resolve_executor(sample)
                    for key in keys:
                        self.assertIn(key, str(raised.exception))

        store = Store(self.root)
        atomic(store.paths['ai'], json.dumps(null_qwen).encode())
        with self.assertRaisesRegex(desktop.StoreError, '配置节必须为对象：qwen'):
            store.read()

    def test_dual_plan_matches_cli_validation(self):
        import ai_anti_config as cli
        import config_store as desktop
        import dual_plan

        samples = [None, [], {}, {'claude_relay': None}, {'claude_relay': []}, {'claude_relay': {}}]
        samples.extend({'claude_relay': {'dual_plan': plan}} for plan in (
            None, False, [], {}, {'enabled': True}, {'enabled': 1}, {'enabled': 'true'}, {'enabled': None},
            {'partner': 'AGY'}, {'partner': None}, {'partner': ''}, {'model': ''}, {'model': 'a b'},
            {'model': 'a\u2003b'}, {'model': 'a\u3000b'}, {'model': 3}, {'model': 'gemini-4-neon'},
            {'effort': 'XHIGH'}, {'effort': 'max'}, {'effort': ''}, {'synthesizer': 'codex'},
            {'synthesizer': 'agy'}, {'on_failure': 'fallback'}, {'on_failure': 'retry'},
            {'enabled': True, 'future': {'k': 1}},
        ))
        for sample in samples:
            with self.subTest(sample=sample):
                try:
                    expected = cli.claude_dual_plan(sample)
                except cli.ConfigError as error:
                    with self.assertRaises(desktop.StoreError) as caught:
                        dual_plan.parse(sample, desktop.StoreError)
                    for field in ('effort', 'model', 'enabled'):
                        key = 'claude_relay.dual_plan.' + field
                        if key in str(error):
                            self.assertIn(key, str(caught.exception))
                else:
                    self.assertEqual(dual_plan.parse(sample, desktop.StoreError), expected)

    def test_dual_plan_settings_view_and_package_contract(self):
        view = (ROOT/'apps/macos/Sources/AIReviewBar/SettingsView.swift').read_text()
        start = view.index('solo 同时停用')
        end = view.index('ServiceSettingsGroup("评审服务"', start)
        section = view[start:end]
        self.assertRegex(section, r'if model\.draft\.host == "claude"\s*\{\s*field\("双方案规划"\)\s*\{')
        self.assertNotIn('isSolo', section)
        self.assertIn('Picker("双方案规划", selection: $model.draft.dualPlanEnabled)', section)
        enable = 'Text("enable").tag(true)'
        disable = 'Text("disable").tag(false)'
        self.assertIn(enable, section)
        self.assertIn(disable, section)
        self.assertLess(section.index(enable), section.index(disable))
        self.assertIn('.disabled(!model.draft.dualPlanError.isEmpty)', section)
        self.assertNotIn('Toggle(', section)
        self.assertNotIn('toggleStyle', section)
        self.assertRegex(section, r'\}\s*else if model\.draft\.dualPlanEnabled \{\s*Text\(model\.draft\.dualPlanDetail\)')
        self.assertEqual(section.count('dualPlanDetail'), 1)
        self.assertNotIn('if model.reviewUsesQwen {', view)
        self.assertNotIn('Qoder 暂不支持 Claude 主模型。', view)
        self.assertIn('backend/dual_plan.py', (ROOT/'apps/macos/build-app.sh').read_text())

    def test_agy_relay_matches_cli_validation(self):
        import agy_relay as desktop
        import ai_anti_config as cli
        from config_store import StoreError

        samples = [None, [], False, '', 1, {}, {'agy_relay': None}, {'agy_relay': []},
                   {'agy_relay': False}, {'agy_relay': {}}, {'agy_relay': {'unknown': True}}]
        routes = [None, [], False, {}, 'codex', {'provider': 'codex'},
                  {'provider': 'codex', 'model': 'custom/model:tag', 'unknown': 1}]
        routes.extend({'provider': provider, 'model': 'model'} for provider in (None, 'codex', 'claude', False, []))
        routes.extend({'provider': 'codex', 'model': model} for model in (
            None, '', False, [], 3, 'white space', 'a\tb', 'a\u2003b', 'a\u3000b', 'custom/model:tag'))
        routes.extend({'provider': 'codex', 'model': 'model', 'effort': effort} for effort in (
            None, *cli.EFFORTS, 'XHIGH', '', [], 2))
        routes.extend({'provider': 'codex', 'model': 'model', 'effort': effort, 'high_risk_effort': elevated}
                      for effort, elevated in ((None, 'low'), ('high', None), ('high', 'high'),
                                               ('high', 'xhigh'), ('xhigh', 'high'),
                                               ('high', 'XHIGH'), ('high', []), ('high', 2)))
        official = dict(provider='official_api', service='token_plan', model='qwen4-max')
        routes.extend((official, dict(official, service='qwen'), dict(official, service='custom_api'),
                       dict(official, model='deepseek-v4.1-flash'), dict(official, effort='high'),
                       dict(official, credential_key='unsafe'), dict(official, service=[])))
        for route in ('review', 'implementation'):
            samples.extend({'agy_relay': {route: value}} for value in routes)
        samples.extend({'agy_relay': {'review': official, 'codex_defaults': defaults}} for defaults in (
            {'review': {'effort': 'high', 'high_risk_effort': 'max'}, 'implementation': {'effort': 'low'}},
            {'review': {'effort': 'xhigh', 'high_risk_effort': 'low'}},
            {'implementation': {'high_risk_effort': 'xhigh'}}, {'review': []}, {'unknown': {}}))
        mixed = (
            {'agy_relay': {'review': {'model': 'valid'}, 'implementation': {'future': True}}},
            {'agy_relay': {'review': {'future': True}, 'implementation': {'provider': 'codex', 'model': 'valid'}}},
        )
        samples.extend(mixed)
        for expected_parser, actual_parser in ((cli.agy_review, desktop.parse_review),
                                               (cli.agy_implementation, desktop.parse_implementation)):
            for sample in samples:
                with self.subTest(parser=expected_parser.__name__, sample=sample):
                    try:
                        expected = expected_parser(sample)
                    except cli.ConfigError as expected_error:
                        with self.assertRaises(StoreError) as actual_error:
                            actual_parser(sample, StoreError)
                        self.assertEqual(str(actual_error.exception), str(expected_error))
                    else:
                        self.assertEqual(actual_parser(sample, StoreError), expected)
        self.assertEqual(desktop.parse_review(mixed[0], StoreError)['model'], 'valid')
        self.assertEqual(desktop.parse_implementation(mixed[1], StoreError)['model'], 'valid')
        with self.assertRaisesRegex(StoreError, r"agy_relay\.implementation has unknown keys: 'high_risk_effort'"):
            desktop.parse_implementation({'agy_relay': {'implementation': {
                'provider': 'codex', 'model': 'model', 'high_risk_effort': 'xhigh',
            }}}, StoreError)
        for sample, bad in zip(mixed, ('implementation', 'review')):
            with self.subTest(bad=bad), self.assertRaisesRegex(StoreError, rf'agy_relay\.{bad} has unknown keys'):
                desktop.parse(sample, StoreError)

    def test_agy_settings_view_and_package_contract(self):
        sources = ROOT / 'apps/macos/Sources/AIReviewBar'
        view = (sources / 'SettingsView.swift').read_text()
        app = (sources / 'App.swift').read_text()
        build = (ROOT / 'apps/macos/build-app.sh').read_text()
        self.assertIn('Text("AGY").tag("agy")', view)
        plan = re.search(r'if model\.draft\.host == "agy"\s*\{\s*field\("双方案规划"\).*?'
                         r'(?=if model\.draft\.host == "agy" &&)', view, re.S).group()
        self.assertIn('Picker("双方案规划", selection: $model.draft.agyDualPlanEnabled)', plan)
        self.assertLess(plan.index('Text("enable").tag(true)'), plan.index('Text("disable").tag(false)'))
        self.assertIn('.disabled(!model.draft.agyDualPlanError.isEmpty)', plan)
        self.assertRegex(plan, r'else if model\.draft\.agyDualPlanEnabled\s*\{\s*Text\(model\.draft\.agyDualPlanDetail\)')
        self.assertNotIn('isSolo', plan)
        self.assertNotIn('Toggle(', plan)
        self.assertRegex(view, r'modelPicker\("主模型 ID"[^\n]+\n\s*\.disabled\(model\.draft\.host == "agy"\)')
        self.assertIn('if model.draft.host == "agy" { return model.draft.agyReviewSelectedModel }', view)
        review = view.split('ServiceSettingsGroup("评审服务"', 1)[1].split('ServiceSettingsGroup("实施服务"', 1)[0]
        implementation = view.split('ServiceSettingsGroup("实施服务"', 1)[1]
        self.assertIn('if model.draft.host == "claude" || model.draft.host == "agy"', review)
        self.assertIn('vendorPicker(reviewService, custom: false)', review)
        self.assertIn('model.draft.host == "agy" ? model.draft.agyOfficialReview : model.draft.officialReview', view)
        self.assertIn('custom: $customAgyReview', review)
        self.assertIn('.disabled(isSolo || !model.draft.agyRelayError.isEmpty)', review)
        self.assertIn('vendorPicker($model.draft.agyImplementationService, custom: false)', implementation)
        self.assertIn('$model.draft.agyImplementationSelectedModel', implementation)
        self.assertIn('model.draft.agyImplementationSelectedModel == "deepseek-v4-pro"', implementation)
        self.assertIn('正式实施复测中未通过协议校验', implementation)
        self.assertIn('custom: $customAgyImplementation', implementation)
        self.assertIn('agyImplementationEffort', implementation)
        self.assertRegex(view, r'Button\("测试评审模型"[^\n]*\n\s*\.disabled\([^\n]+\(model\.draft\.host == "agy" && !model\.draft\.agyRelayError\.isEmpty\)\)')
        self.assertIn('saved.agyReviewDescription(separator: " · ")', app)
        self.assertIn('saved.agyImplementationDescription(separator: " · ")', app)
        self.assertNotIn('model.menuHost(', app)
        self.assertIn('"$source_dir/backend/agy_relay.py"', build)
        self.assertIn('"$source_dir/backend/relay_routes.py"', build)
        self.assertIn('<key>CFBundleVersion</key><string>18</string>', build)
        self.assertIn('<key>CFBundleShortVersionString</key><string>0.3.11</string>', build)

    def test_agy_official_review_discloses_metadata_and_records_followups(self):
        for skill in ('agy-relay', 'codex-review'):
            text = (ROOT / f'adapters/agy/plugin/skills/{skill}/SKILL.md').read_text()
            for term in ('official_api', 'tracked-paths.txt', 'git-status.txt', '未跟踪文件名', '接收方', '外发授权'):
                self.assertIn(term, text, (skill, term))
        todos = (ROOT / 'docs/FEATURE_TODOS.md').read_text().split('### AGY 全渠道评审与实施后续', 1)[1]
        for term in ('scoped metadata', '无自动重试', '--max-retries', 'MODEL_ROUTE_VALIDATION.zh-CN.md', '19 项'):
            self.assertIn(term, todos)

    def test_agy_dual_plan_matches_cli_validation(self):
        import ai_anti_config as cli
        import dual_plan as desktop
        from config_store import StoreError

        samples = [None, [], False, 1, '', {}, {'agy_relay': None}, {'agy_relay': []},
                   {'agy_relay': False}, {'agy_relay': {}}, {'agy_relay': {'unknown': True}},
                   {'agy_relay': {'review': [], 'implementation': {'typo': 0}, 'dual_plan': {'enabled': True}}}]
        plans = [None, False, [], 7, '', {}, {'enabeld': True}, {'extra': 1, 'typo': 2}]
        for key, choices in {
            'enabled': (False, True, None, 0, 'false', {}),
            'partner': ('claude', 'agy', None, False, []),
            'model': ('sonnet', 'claude-sonnet-4-6', 'custom', None, False, [], '', 'a b', 'a\u3000b'),
            'effort': ('low', 'medium', 'high', 'xhigh', 'max', 'ultra', 'XHIGH', None, [], 2),
            'synthesizer': ('agy', 'claude', 'codex', None, {}, False),
            'on_failure': ('stop', 'fallback', 'retry', None, False, []),
        }.items():
            plans.extend({key: value} for value in choices)
        samples.extend({'agy_relay': {'dual_plan': plan}} for plan in plans)
        for sample in samples:
            with self.subTest(sample=sample):
                try:
                    expected = cli.agy_dual_plan(sample)
                except cli.ConfigError as expected_error:
                    with self.assertRaises(StoreError) as actual_error:
                        desktop.parse_agy(sample, StoreError)
                    self.assertEqual(str(actual_error.exception), str(expected_error))
                else:
                    self.assertEqual(desktop.parse_agy(sample, StoreError), expected)

    def test_mode_skill_scope_and_incident_notes(self):
        skill=(ROOT/'adapters/codex/skills/codex-relay/SKILL.md').read_text()
        self.assertIn('relay.default_mode',skill); self.assertIn('never a complete relay',skill)
        rules=(ROOT/'adapters/claude-code/CLAUDE.global.md').read_text()
        for date in ('2026-08-06','08-07','08-11','08-12'):
            # Historical notes remain somewhere in their original mechanism files.
            all_mechanism=''.join(p.read_text() for p in (ROOT/'core').glob('*.md'))+rules
            self.assertIn(date,all_mechanism)

if __name__ == '__main__': unittest.main()
