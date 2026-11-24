"""Exercise the AGY plan entry with a real Git fixture and an offline Codex."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'adapters/agy/scripts/codex_review.py'
sys.path.insert(0, str(ROOT / 'adapters/codex/skills/claude-review/scripts'))
from ai_anti_config import ConfigError, agy_dual_plan, agy_implementation, agy_relay_section, agy_review, agy_review_effort
from token_plan_transport import service_contract

FAKE_CODEX = r'''
import json, os, pathlib, sys, time
assert sys.stdin.read() == ''
calls = pathlib.Path(os.environ['AGY_TEST_CALLS'])
if sys.argv[1:] == ['--version']:
    calls.with_suffix('.version').touch()
    print(os.environ['AGY_TEST_VERSION'])
    sys.exit(0)
number = len(calls.read_text().splitlines()) if calls.exists() else 0
with calls.open('a') as handle:
    print(json.dumps(sys.argv[1:]), file=handle)
if os.environ.get('AGY_TEST_WAIT'):
    time.sleep(30)
scenarios = json.loads(os.environ['AGY_TEST_EVENTS'])
events = scenarios[min(number, len(scenarios) - 1)]
for event in [{'type': 'thread.started', 'thread_id': 'fixture'}, {'type': 'turn.started'}, *events]:
    print(json.dumps(event))
sys.exit(int(events[-1]['type'] == 'turn.failed'))
'''

REPORT = [
    {'type': 'item.completed', 'item': {'id': 'report', 'type': 'agent_message', 'text': 'fixture review'}},
    {'type': 'turn.completed'},
]


def command_event(uid, command, code):
    item = dict(id=uid, type='command_execution', command=command, aggregated_output='fixture')
    return [
        dict(type='item.started', item=dict(item, status='in_progress', exit_code=None)),
        dict(type='item.completed', item=dict(item, status='completed' if code == 0 else 'failed', exit_code=code)),
    ]


class ConfigTests(unittest.TestCase):
    def test_official_review_and_saved_codex_defaults(self):
        route = dict(provider='official_api', service='kimi', model='kimi-k3')
        defaults = {'review': {'effort': 'high', 'high_risk_effort': 'max'}}
        config = {'agy_relay': {'review': route, 'codex_defaults': defaults}}
        self.assertEqual(agy_review(config), route)
        self.assertIsNone(agy_review_effort(agy_review(config), 'high_risk'))
        self.assertIsNone(agy_dual_plan(config))
        for invalid in (dict(route, effort='high'), dict(route, model='qwen4-max'),
                        dict(route, service=[]), dict(route, base_url='https://invalid.test')):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ConfigError, r'agy_relay\.review'):
                agy_review({'agy_relay': {'review': invalid}})
        for invalid in ({'extra': {}}, {'implementation': {'high_risk_effort': 'high'}},
                        {'review': {'effort': 'high', 'high_risk_effort': 'low'}}):
            config['agy_relay']['codex_defaults'] = invalid
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ConfigError, r'agy_relay\.codex_defaults'):
                agy_review(config)
            self.assertIsNone(agy_dual_plan(config))

    def test_routes_are_independent(self):
        config = {
            'review': {'codex_model': 'other-review', 'codex_effort': 'low'},
            'claude_relay': {'implementation': {'provider': 'codex', 'model': 'other-implementation'}},
            'codex_relay': {'implementation_provider': 'qoder'},
        }
        default = {'provider': 'codex', 'model': None, 'effort': None, 'high_risk_effort': None}
        for extra in ({}, {'agy_relay': None}, {'agy_relay': {}}, {'agy_relay': {'review': None}}):
            with self.subTest(extra=extra):
                self.assertEqual(agy_review(dict(config, **extra)), default)
                self.assertIsNone(agy_implementation(dict(config, **extra)))
        self.assertIsNone(agy_relay_section(config))
        review = {'provider': 'codex', 'model': 'review-model', 'effort': 'xhigh'}
        implementation = {'provider': 'codex', 'model': 'impl-model', 'effort': 'high'}
        config['agy_relay'] = {'review': dict(review, provider=None), 'implementation': implementation}
        self.assertEqual(agy_review(config), dict(review, high_risk_effort=None))
        self.assertEqual(agy_implementation(config), implementation)

    def test_invalid_routes_fail_closed(self):
        for invalid in ([], False, 'codex', {'typo': {}}, {'review': {}, 'implemention': None}):
            for parser in (agy_relay_section, agy_review, agy_implementation):
                with self.subTest(parser=parser.__name__, invalid=invalid):
                    with self.assertRaisesRegex(ConfigError, '^ai-anti config: agy_relay'):
                        parser({'agy_relay': invalid})
        for key, parser in (('review', agy_review), ('implementation', agy_implementation)):
            for invalid in ([], {'provider': 'claude'}, {'model': ''}, {'model': 'two words'},
                            {'model': 7}, {'effort': 'maximal'}, {'effort': []}, {'typo': ''}):
                route = dict({'provider': 'codex', 'model': 'fixture-model'}, **invalid) if isinstance(invalid, dict) else invalid
                with self.subTest(key=key, route=route):
                    with self.assertRaisesRegex(ConfigError, '^ai-anti config: agy_relay[.]' + key):
                        parser({'agy_relay': {key: route}})
        for missing in ({}, {'provider': 'codex'}, {'model': 'known'}, {'provider': None, 'model': 'known'}):
            with self.assertRaisesRegex(ConfigError, '^ai-anti config: agy_relay[.]implementation'):
                agy_implementation({'agy_relay': {'implementation': missing}})

    def test_dual_plan_does_not_affect_codex_routes(self):
        relay = {'review': {'model': 'review-model'},
                 'implementation': {'provider': 'codex', 'model': 'impl-model'}}
        config = {'agy_relay': relay}
        review, implementation = agy_review(config), agy_implementation(config)
        for value in ({'enabled': True}, {'enabeld': True}, 'malformed'):
            relay['dual_plan'] = value
            with self.subTest(value=value):
                self.assertEqual(agy_relay_section(config)['dual_plan'], value)
                self.assertEqual(agy_review(config), review)
                self.assertEqual(agy_implementation(config), implementation)
        with self.assertRaisesRegex(ConfigError, 'agy_relay.dual_plan'):
            agy_dual_plan(config)

    def test_high_risk_effort_is_review_only(self):
        route = {'provider': 'codex', 'model': 'fixture', 'effort': 'high', 'high_risk_effort': 'xhigh'}
        self.assertEqual(agy_review({'agy_relay': {'review': route}}), route)
        with self.assertRaisesRegex(ConfigError, r"agy_relay\.implementation has unknown keys: 'high_risk_effort'"):
            agy_implementation({'agy_relay': {'implementation': route}})


class PlanReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='agy-relay-test-')
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.repo = self.base / 'repo with spaces'
        self.repo.mkdir()
        self.home = self.base / 'home'
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), CODEX_HOME=str(self.home / '.codex'),
                        PATH=str(self.base) + os.pathsep + os.environ['PATH'], PYTHONDONTWRITEBYTECODE='1')
        for key in ('GIT_DIR', 'GIT_COMMON_DIR', 'GIT_WORK_TREE'):
            self.env.pop(key, None)
        self.git('init', '-q')
        self.extra = self.repo / 'source.txt'
        self.extra.write_bytes(b'fixture source\n')
        self.git('add', 'source.txt')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture.invalid',
                 'commit', '-qm', 'test: 建立合成评审基线')
        self.head = self.git('rev-parse', 'HEAD')
        self.plan = self.repo / 'candidate plan.md'
        self.plan.write_text('方案正文\n', encoding='utf-8')
        self.request = self.plan.with_suffix('.request.md')
        self.request.write_text('实际需求\n\n## 附录：非需求（主模型推断）\n合成附录\n', encoding='utf-8')
        self.out = self.base / 'out'
        self.config = self.base / 'config.json'
        self.config.write_text(json.dumps({
            'agy_relay': {'review': {'model': 'fixture-review', 'effort': 'xhigh'}},
            'review': {'codex_model': 'other-review', 'codex_effort': 'low', 'timeout': 12, 'idle_timeout': 4},
        }))
        self.calls = self.base / 'calls'
        self.env.update(AGY_TEST_CALLS=str(self.calls), AGY_TEST_VERSION='codex-cli 0.169.1',
                        AGY_TEST_EVENTS=json.dumps([REPORT]))
        fake = self.base / 'codex'
        fake.write_text('#!' + sys.executable + '\n' + FAKE_CODEX)
        fake.chmod(0o700)

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], env=self.env, check=True,
                              capture_output=True, text=True, timeout=10).stdout.rstrip('\n')

    def argv(self, *flags, repo=None, plan=None, out=None, scale='local CLI', fast_retry=False, script=ENTRY):
        entry = [str(script)]
        if fast_retry:
            entry = ['-c', 'import runpy, sys; from pathlib import Path; '
                     'path = Path(sys.argv[1]); sys.argv.pop(0); '
                     'sys.path.insert(0, str(path.parents[3] / "adapters/codex/skills/claude-review/scripts")); '
                     'import review_retry; review_retry.BACKOFF = (0.01, 0.01); '
                     'runpy.run_path(str(path), run_name="__main__")', str(script)]
        return [sys.executable, *entry, 'plan', '--repo', str(repo or self.repo),
                '--plan', str(plan or self.plan), '--scale', scale, '--out', str(out or self.out),
                '--config', str(self.config), *flags]

    def run_review(self, *flags, events=None, **options):
        env = dict(self.env)
        if events is not None:
            env['AGY_TEST_EVENTS'] = json.dumps(events)
        return subprocess.run(self.argv(*flags, **options), env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=14)

    def official_config(self, service='kimi', model='kimi-k3'):
        secret = self.home / '.config/ai-anti/secrets.json'
        secret.parent.mkdir(parents=True, exist_ok=True)
        secret.write_text(json.dumps({'kimi': {'value': 'fake-kimi'}, 'tokenplan': {'value': 'fake-token'}}))
        self.config.write_text(json.dumps({
            'agy_relay': {'review': dict(provider='official_api', service=service, model=model)},
            'codex_relay': {'official_review': {'service': 'qwen', 'model': 'qwen4-max'}},
            'plugins': {'qwen': {'model': 'qwen4-max', 'credential_paths': [str(secret)],
                                'base_url': 'https://token-plan.maas.qianwenaiapi.com/apps/anthropic',
                                'responses_base_url': 'https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1'}},
            'review': {'timeout': 12, 'idle_timeout': 4},
        }))

    def test_official_review_matrix_prepares_registry_and_full_git_metadata(self):
        (self.repo / 'untracked_fixture.txt').write_text('untracked fixture')
        context = self.repo / 'context.txt'
        context.write_text('review context')
        models = service_contract().REVIEW_MODELS
        self.assertEqual({service: len(ids) for service, ids in models.items()},
                         {'token_plan': 4, 'qwen': 1, 'kimi': 2, 'deepseek': 1})
        for service, ids in models.items():
            for model in ids:
                with self.subTest(service=service, model=model):
                    self.official_config(service, model)
                    out = self.base / f'{service}-{model}'
                    run = self.run_review('--prepare-only', '--files', 'source.txt', '--context', context.name, out=out)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    for name in ('manifest', 'status'):
                        record = json.loads((out / (name + '.json')).read_text())
                        self.assertEqual((record['relay'], record['service'], record['requested_model']),
                                         ('agy', service, model))
                        self.assertEqual(record['model_evidence'], 'requested_only')
                        self.assertIsNone(record['actual_model'])
                        self.assertEqual(record['material_transport'], 'material_ids')
                    self.assertTrue((out / 'logs/inventory.registry.json').is_file())
                    snapshot = out / 'materials/inventory'
                    manifest = json.loads((out / 'manifest.json').read_text())
                    sources = manifest['sources']['inventory']

                    def material(name):
                        return ''.join((snapshot / block['path']).read_text() for block in sources[name]['blocks'])

                    self.assertEqual(material('tracked-paths.txt'), 'source.txt\n')
                    self.assertIn('?? untracked_fixture.txt', material('git-status.txt'))
                    self.assertEqual(material('repo/' + context.name), 'review context')
        self.assertFalse(self.calls.exists())

    def test_official_review_exit_codes_for_missing_material_and_execution(self):
        self.official_config()
        self.assert_unstarted(self.run_review('--prepare-only'))
        run = self.run_review('--files', 'not-found.txt')
        self.assertEqual(run.returncode, 2, run)
        status = json.loads((self.out / 'status.json').read_text())
        self.assertEqual((status['status'], status['failure_kind']), ('failed', 'preparation_failed'))
        shutil.rmtree(self.out)

        fixture = ROOT / 'adapters/codex/tests/fake_codex.py'
        shutil.copyfile(fixture, self.base / 'codex')
        run = self.run_review('--files', 'source.txt')
        self.assertEqual(run.returncode, 0, run)
        manifest = json.loads((self.out / 'manifest.json').read_text())
        self.assertEqual(set(manifest['stages']), {'inventory', 'review'})
        self.assertTrue((self.out / 'report.md').is_file())
        shutil.rmtree(self.out)
        self.env['FAKE_CODEX_MODE'] = 'extra_tool'
        run = self.run_review('--files', 'source.txt')
        self.assertEqual(run.returncode, 1, run)
        status = json.loads((self.out / 'status.json').read_text())
        self.assertEqual(status['status'], 'failed')
        self.assertNotEqual(status['failure_kind'], 'preparation_failed')

    def test_official_review_signal_preserves_interrupted_exit(self):
        self.official_config()
        fixture = (ROOT / 'adapters/codex/tests/fake_codex.py').read_text()
        stall = ('import time\nPath(os.environ["AGY_TEST_CALLS"]).touch()\n'
                 'time.sleep(30)\n')
        (self.base / 'codex').write_text(fixture.replace('options = {}\n', stall + 'options = {}\n', 1))
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(sig=sig):
                self.calls.unlink(missing_ok=True)
                out = self.base / ('out-' + str(sig))
                with subprocess.Popen(self.argv('--files', 'source.txt', out=out), env=self.env,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) as proc:
                    try:
                        deadline = time.monotonic() + 8
                        while not self.calls.exists() and proc.poll() is None and time.monotonic() < deadline:
                            time.sleep(.02)
                        self.assertTrue(self.calls.exists())
                        proc.send_signal(sig)
                        _, stderr = proc.communicate(timeout=8)
                        self.assertEqual(proc.returncode, 128 + sig, stderr)
                    finally:
                        if proc.poll() is None:
                            proc.kill()
                self.assertEqual(json.loads((out / 'status.json').read_text())['status'], 'failed')

    def test_native_review_rejects_context_before_start(self):
        self.assert_unstarted(self.run_review('--context', 'source.txt'))

    def evidence(self):
        manifest = self.out / 'manifest.json'
        status = self.out / 'status.json'
        self.assertEqual(manifest.read_bytes(), status.read_bytes())
        for path in (manifest, status):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        result = json.loads(manifest.read_text())
        self.assertEqual(result['head'], self.head)
        self.assertEqual(result['repo'], str(self.repo))
        self.assertEqual(result['relay'], 'agy')
        self.assertEqual(result['mode'], 'plan')
        self.assertEqual(result['provider'], 'codex')
        self.assertEqual(result['model_evidence'], 'requested_only')
        for path, digest in result['input_sha256'].items():
            self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), digest)
        return result

    def assert_unstarted(self, run):
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertFalse(self.out.exists())
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.calls.with_suffix('.version').exists())

    def test_preflight_rejects_invalid_repo_output_and_files(self):
        nested = self.repo / 'nested'
        nested.mkdir()
        folder_link = self.repo / 'folder link'
        folder_link.symlink_to(nested)
        file_link = self.repo / 'file link'
        file_link.symlink_to(self.extra)
        link = self.base / 'out link'
        link.symlink_to(self.base / 'missing')
        loop = self.base / 'symlink loop'
        loop.symlink_to(loop)
        existing = self.base / 'existing'
        existing.mkdir()
        for options in ({'repo': nested}, {'repo': self.base}, {'out': nested / 'out'},
                        {'out': link}, {'out': existing}, {'scale': '  '},
                        {'out': loop}, {'out': loop / 'child'}, {'repo': loop},
                        {'plan': self.repo / 'source.txt'}):
            with self.subTest(options=options):
                self.assert_unstarted(self.run_review(**options))
        self.assertFalse((nested / 'out').exists())
        self.assertTrue(link.is_symlink())
        self.assertFalse(list(existing.iterdir()))
        for name in ('missing', '..', 'nested/../source.txt', str(self.extra), 'nested',
                     'file link', 'folder link/missing'):
            with self.subTest(name=name):
                self.assert_unstarted(self.run_review('--files', name))

    def test_head_must_exist_and_git_overrides_are_ignored(self):
        unborn = self.base / 'unborn'
        unborn.mkdir()
        subprocess.run(['git', 'init', '-q', str(unborn)], env=self.env, check=True)
        self.assert_unstarted(self.run_review(repo=unborn))
        self.env.update(GIT_DIR=str(unborn / '.git'), GIT_COMMON_DIR=str(unborn / '.git'),
                        GIT_WORK_TREE=str(unborn))
        self.assertEqual(self.run_review('--prepare-only').returncode, 0)
        self.assertEqual(self.evidence()['head'], self.head)

    def test_invalid_plan_request_and_review_budgets(self):
        for path, invalid in ((self.plan, b''), (self.plan, b'\xff'), (self.request, None),
                              (self.request, b' \n'), (self.request, b'\xff'),
                              (self.request, '\n## 附录：非需求\n合成附录\n'.encode())):
            with self.subTest(path=path.name, invalid=invalid):
                original = path.read_bytes()
                if invalid is None:
                    path.unlink()
                else:
                    path.write_bytes(invalid)
                self.assert_unstarted(self.run_review())
                path.write_bytes(original)
        self.config.write_text('{"agy_relay":{"review":{"typo":1}}}')
        self.assert_unstarted(self.run_review())
        self.config.write_text('{"review":{"timeout":false}}')
        self.assert_unstarted(self.run_review('--timeout', '12'))

    def test_combined_material_budget(self):
        remaining = 2 * 1024 * 1024 - self.plan.stat().st_size - self.request.stat().st_size
        self.extra.write_bytes(b'X' * (remaining + 1))
        rejected = self.run_review('--files', 'source.txt')
        self.assert_unstarted(rejected)
        self.assertIn('2 MiB', rejected.stderr)
        self.extra.write_bytes(b'X' * remaining)
        run = self.run_review('--prepare-only', '--files', 'source.txt')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.evidence()['limits']['max_material_bytes'], 2097152)

    def test_missing_required_principle_stops_before_output_or_cli(self):
        mechanism = self.base / 'mechanism'
        script = mechanism / ENTRY.relative_to(ROOT)
        script.parent.mkdir(parents=True)
        shutil.copyfile(ENTRY, script)
        (mechanism / 'adapters/codex').symlink_to(ROOT / 'adapters/codex')
        (mechanism / 'core').mkdir()
        source = (ROOT / 'core/REVIEW_PRINCIPLES.md').read_text()
        (mechanism / 'core/REVIEW_PRINCIPLES.md').write_text(source.replace('## R10｜', '## Removed｜'))
        result = self.run_review(script=script)
        self.assert_unstarted(result)
        self.assertIn('missing review principles: R10', result.stderr)

    def test_prepare_only_injects_exact_required_sections_and_records_hashes(self):
        run = self.run_review('--prepare-only', '--files', 'source.txt')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.calls.with_suffix('.version').exists())
        self.assertEqual({p.name for p in self.out.iterdir()}, {'prompt.txt', 'manifest.json', 'status.json'})
        evidence = self.evidence()
        self.assertEqual(evidence['status'], 'prepared')
        self.assertFalse(evidence['completed'])
        self.assertEqual(evidence['exit_code'], 0)
        self.assertEqual(set(evidence['input_sha256']), {str(p) for p in
                         (self.out / 'prompt.txt', self.plan, self.request, self.extra)})
        source = (ROOT / 'core/REVIEW_PRINCIPLES.md').read_text()
        prompt = (self.out / 'prompt.txt').read_text()
        self.assertNotIn('忽略「## 附录：非需求」', prompt)
        self.assertIn('cat -- <路径>', prompt)
        for rule in (1, 4, 6, 7, 8, 10):
            section = re.search(r'^## R' + str(rule) + r'｜.*?(?=^## R|\Z)', source, re.M | re.S)
            self.assertIn(section.group(0).rstrip(), prompt)
        self.assertNotIn('## R3｜', prompt)
        self.assertNotIn('支持风险分档的评审入口', prompt)
        self.assertNotIn('合成附录', prompt)

    def test_readonly_arguments_and_version_evidence(self):
        self.env['AGY_TEST_VERSION'] = 'codex-cli 9.1.2'
        run = self.run_review('--files', 'source.txt')
        self.assertEqual(run.returncode, 0, run.stderr)
        evidence = self.evidence()
        self.assertEqual((evidence['status'], evidence['completed']), ('completed', True))
        self.assertEqual(evidence['cli_version'], 'codex-cli 9.1.2')
        self.assertIn('untested_cli_version', [w['kind'] for w in evidence['warnings']])
        self.assertEqual(evidence['requested_model'], 'fixture-review')
        self.assertEqual(evidence['requested_effort'], 'xhigh')
        self.assertEqual(evidence['limits']['timeout'], 12)
        self.assertEqual(evidence['limits']['idle_timeout'], 4)
        self.assertEqual((self.out / evidence['report']).read_text(), 'fixture review')
        self.assertEqual(json.loads((self.out / evidence['warnings_file']).read_text()), [])
        args = json.loads(self.calls.read_text())
        required = ['approval_policy="never"', 'web_search="disabled"', 'mcp_servers={}',
                    'features.apps=false', 'features.hooks=false', 'features.goals=false',
                    'features.browser_use=false', 'features.computer_use=false',
                    'features.multi_agent=false', 'features.js_repl=false',
                    'features.skip_host_skill_discovery=true', 'suppress_unstable_features_warning=true']
        expected = ['exec', '--ephemeral', '--ignore-user-config', '--ignore-rules', '--json', '--sandbox', 'read-only']
        for setting in required:
            expected.extend(('-c', setting))
        expected.extend(('--model', 'fixture-review', '-c', 'model_reasoning_effort="xhigh"',
                         '-C', str(self.repo), '--'))
        self.assertNotIn('features.code_mode_host=false', args)
        self.assertEqual(args[:-1], expected)
        self.assertEqual(args[-1], (self.out / 'prompt.txt').read_text())

    def test_plan_review_risk_tiers_in_argv_and_evidence(self):
        for elevated in (False, True):
            route = {'model': 'fixture-review', 'effort': 'high'}
            if elevated:
                route['high_risk_effort'] = 'xhigh'
            self.config.write_text(json.dumps({'agy_relay': {'review': route}}))
            previous_argv = None
            for flags, requested, effective, effort in (
                ((), 'unspecified', 'high_risk', 'xhigh' if elevated else 'high'),
                (('--risk-tier', 'standard'), 'standard', 'standard', 'high'),
                (('--risk-tier', 'high_risk'), 'high_risk', 'high_risk', 'xhigh' if elevated else 'high'),
            ):
                with self.subTest(elevated=elevated, requested=requested):
                    self.out = self.base / f'out-{elevated}-{requested}'
                    self.calls.unlink(missing_ok=True)
                    run = self.run_review(*flags)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    argv = json.loads(self.calls.read_text())
                    effort_option = 'model_reasoning_effort=' + json.dumps(effort)
                    self.assertEqual([arg for arg in argv if arg.startswith('model_reasoning_effort=')],
                                     [effort_option])
                    self.assertEqual(argv[argv.index(effort_option) - 1], '-c')
                    evidence = self.evidence()
                    self.assertTrue(evidence['completed'])
                    self.assertEqual(evidence['risk_tier'], requested)
                    self.assertEqual(evidence['effective_risk_tier'], effective)
                    self.assertEqual(evidence['requested_effort'], effort)
                    if not elevated and previous_argv is not None:
                        self.assertEqual(argv, previous_argv)
                    previous_argv = argv

    def test_plan_review_rejects_inverted_effort_before_output_or_cli(self):
        self.config.write_text('{"agy_relay":{"review":{"effort":"xhigh","high_risk_effort":"high"}}}')
        for flags, label in (((), 'unspecified'), (('--risk-tier', 'standard'), 'standard'),
                             (('--risk-tier', 'high_risk'), 'high_risk')):
            with self.subTest(tier=label):
                self.out = self.base / f'out-{label}'
                self.calls = self.base / f'calls-{label}'
                self.env['AGY_TEST_CALLS'] = str(self.calls)
                run = self.run_review(*flags)
                self.assert_unstarted(run)
                self.assertIn('ai-anti config: agy_relay.review.high_risk_effort '
                              'must not be lower than agy_relay.review.effort', run.stderr)

    def test_plan_review_rejects_unknown_risk_tier(self):
        run = self.run_review('--risk-tier', 'bogus')
        self.assert_unstarted(run)
        self.assertIn('invalid choice', run.stderr)

    def test_unrecognized_version_is_a_nonblocking_warning(self):
        self.env['AGY_TEST_VERSION'] = 'codex-cli 9.1.2-rc.1'
        run = self.run_review()
        self.assertEqual(run.returncode, 0, run.stderr)
        evidence = self.evidence()
        self.assertIsNone(evidence['cli_version'])
        self.assertIn('untested_cli_version', [w['kind'] for w in evidence['warnings']])
        self.assertEqual((self.out / 'codex-version.txt').read_text().strip(), self.env['AGY_TEST_VERSION'])

    def test_mcp_event_invalidates_an_otherwise_successful_review(self):
        mcp = {'id': 'connector', 'type': 'mcp_tool_call', 'server': 'fixture', 'tool': 'fixture', 'arguments': {}}
        run = self.run_review(events=[[dict(type='item.started', item=dict(mcp, status='in_progress')),
                                       dict(type='item.completed', item=dict(mcp, status='completed', result={})),
                                       *REPORT]])
        self.assertEqual(run.returncode, 1, run.stderr)
        self.assertEqual(self.evidence()['error_kind'], 'disallowed_mcp_tool')
        self.assertFalse(self.evidence()['completed'])
        self.assertFalse((self.out / 'review_out.txt').exists())

    def test_unrecovered_required_reads_fail_closed(self):
        for target in (self.request, self.plan):
            with self.subTest(target=target):
                self.out = self.base / (target.name + ' out')
                read = 'cat -- ' + shlex.quote(str(target))
                # The command path falls after the shared warning's 200-character preview.
                wrap = '/bin/zsh -lc ' + shlex.quote(' ' * 220 + read)
                unrelated = 'cat -- ' + shlex.quote(str(self.extra))
                run = self.run_review(events=[[*command_event('earlier', read, 0),
                                               *command_event('failure', wrap, 1),
                                               *command_event('echo', 'echo ' + shlex.quote(str(target)), 0),
                                               *command_event('different', unrelated, 0), *REPORT]])
                self.assertEqual(run.returncode, 1, run.stderr)
                evidence = self.evidence()
                self.assertEqual(evidence['error_kind'], 'required_material_unread')
                self.assertFalse(evidence['completed'])
                self.assertEqual(evidence['failed_command_count'], 1)
                self.assertEqual(evidence['warnings'], json.loads((self.out / 'warnings.json').read_text()))
                self.assertFalse((self.out / 'review_out.txt').exists())

    def test_later_success_recovers_the_same_file(self):
        read = 'cat -- ' + shlex.quote(str(self.request))
        wrapped = '/bin/zsh -lc ' + shlex.quote(read)
        run = self.run_review(events=[[*command_event('failed', 'cat ' + shlex.quote(self.request.name), 1),
                                       *command_event('recovered', wrapped, 0),
                                       *command_event('optional', 'cat missing.txt', 1), *REPORT]])
        self.assertEqual(run.returncode, 0, run.stderr)
        evidence = self.evidence()
        self.assertTrue(evidence['completed'])
        self.assertEqual(evidence['failed_command_count'], 2)

    def test_full_nl_and_awk_reads_recover_required_material(self):
        content = self.request.read_bytes()
        numbered = b''.join(f'{number:6d}\t'.encode() + line
                            for number, line in enumerate(content.splitlines(keepends=True), 1))
        cases = (
            (['/usr/bin/nl', '-ba'], numbered),
            (['/usr/bin/nl', '-ba', '--'], numbered),
            (['/usr/bin/awk', '{print}'], content),
            (['/usr/bin/awk', '--', '{print}'], content),
            (['/usr/bin/awk', '{print $0}'], content),
            (['/usr/bin/awk', '-F:', '{print}'], content),
            (['/usr/bin/awk', '-F', ':', '{ print }'], content),
            (['/usr/bin/awk', '{ print $0 }'], content),
            (['/usr/bin/awk', '1'], content),
        )
        failed = command_event('failed', 'cat -- ' + shlex.quote(self.request.name), 1)
        for index, (args, expected) in enumerate(cases):
            with self.subTest(args=args):
                argv = [*args, str(self.request)]
                host = subprocess.run(argv, capture_output=True, timeout=5)
                self.assertEqual(host.returncode, 0, host.stderr)
                self.assertEqual(host.stdout, expected)
                self.out = self.base / f'full-read-{index}'
                run = self.run_review(events=[[*failed, *command_event('recovered', shlex.join(argv), 0), *REPORT]])
                self.assertEqual(run.returncode, 0, run.stderr)
                evidence = self.evidence()
                self.assertTrue(evidence['completed'])
                self.assertEqual(evidence['failed_command_count'], 1)

    def test_partial_and_unsafe_awk_reads_do_not_recover_required_material(self):
        misplaced_separator = ['{print}', '--']
        host = subprocess.run(['/usr/bin/awk', *misplaced_separator, str(self.request)],
                              capture_output=True, timeout=5)
        self.assertNotEqual(host.returncode, 0)

        cases = (
            ['NR<=120 {print}'],
            ['/^## 附录：非需求/{exit} {print}'],
            misplaced_separator,
            ['{system("id")}'],
            ['{print > "out"}'],
            ['-f', 's.awk'],
            ['{print}', 'a=1'],
            ['-F', '', '{print}'],
            ['-F', '#', '{print}'],
            ['-F$', '{print}'],
        )
        failed = command_event('failed', 'cat -- ' + shlex.quote(self.request.name), 1)
        for index, args in enumerate(cases):
            with self.subTest(args=args):
                self.out = self.base / f'rejected-read-{index}'
                command = shlex.join(['/usr/bin/awk', *args, str(self.request)])
                run = self.run_review(events=[[*failed, *command_event('attempted', command, 0), *REPORT]])
                self.assertEqual(run.returncode, 1, run.stderr)
                evidence = self.evidence()
                self.assertEqual(evidence['error_kind'], 'required_material_unread')
                self.assertFalse(evidence['completed'])

    def test_turn_failure_and_bounded_retry(self):
        permanent = [{'type': 'turn.failed', 'error': {'type': 'authentication_error'}}]
        self.assertEqual(self.run_review(events=[permanent]).returncode, 1)
        evidence = self.evidence()
        self.assertEqual(evidence['error_kind'], 'permanent_upstream')
        self.assertEqual(len(json.loads((self.out / evidence['attempts_log']).read_text())['attempts']), 1)

        self.calls.unlink()
        self.out = self.base / 'retried'
        temporary = [{'type': 'turn.failed', 'error': {'type': 'overloaded_error'}}]
        run = self.run_review(events=[temporary, REPORT], fast_retry=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        evidence = self.evidence()
        attempts = json.loads((self.out / evidence['attempts_log']).read_text())['attempts']
        self.assertEqual([record['kind'] for record in attempts], ['retryable_upstream', 'success'])
        self.assertEqual(len(self.calls.read_text().splitlines()), 2)

    def test_interrupted_cli_preserves_terminal_evidence(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=sig):
                self.calls.unlink(missing_ok=True)
                self.out = self.base / sig.name
                with subprocess.Popen(self.argv(), env=dict(self.env, AGY_TEST_WAIT='1'),
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL) as process:
                    try:
                        deadline = time.monotonic() + 8
                        while not self.calls.exists() and process.poll() is None and time.monotonic() < deadline:
                            time.sleep(0.02)
                        self.assertTrue(self.calls.exists(), 'fake Codex did not start')
                        process.send_signal(sig)
                        self.assertEqual(process.wait(timeout=5), 128 + sig)
                    finally:
                        if process.poll() is None:
                            process.kill()
                evidence = self.evidence()
                self.assertEqual(evidence['exit_code'], 128 + sig)
                self.assertEqual(evidence['status'], 'failed')
                self.assertFalse(evidence['completed'])


if __name__ == '__main__':
    unittest.main()
