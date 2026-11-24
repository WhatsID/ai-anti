"""Offline implementation contracts; never reads real config or calls a model."""
import argparse
import copy
import json
from pathlib import Path
import sys
import signal
import subprocess
import time
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'adapters/codex/skills/codex-relay/scripts'))
from implementation_config import ConfigError, MODEL, provider, resolve
import implementation
from implementation_protocol import validate
from review_runner import RunError
import model_services

FAKE = ROOT / 'adapters/codex/tests/fake_implementation.py'

class ImplementationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / 'workspace'; self.workspace.mkdir()
        self.secret = self.root / 'fixture-secret.json'
        self.secret.write_text(json.dumps({'model_services': {'token_plan': {'value': 'OFFLINE_FAKE_TOKEN'}, 'custom_api': {'value': 'WRONG_CUSTOM_TOKEN'}}}))
        profiles = {key: model_services.blank(key) for key in model_services.IDS}
        profiles['token_plan']['credential_paths'] = [str(self.secret)]
        profiles['custom_api'].update(model='custom-model', responses_base_url='https://custom.invalid/v1', base_url='https://custom.invalid/anthropic', credential_paths=[str(self.secret)])
        self.config = dict(codex_relay=dict(implementation_provider='token_plan', secondary_model='old-native'),
                           model_services=dict(version=1, active_api='custom_api', profiles=profiles),
                           plugins=dict(qwen=copy.deepcopy(profiles['custom_api'])))
        self.path = self.root / 'config.json'; self.path.write_text(json.dumps(self.config))
        self.prompt = self.root / 'prompt.txt'; self.prompt.write_text('Implement authorized offline fixture scope.')
    def tearDown(self): self.temp.cleanup()
    def args(self, name='output', **changes):
        values = dict(workspace=self.workspace, prompt=self.prompt, out=self.root/name, config=self.path,
                      provider=None, model=None, timeout=None, idle_timeout=None, prepare_only=False, cli=str(FAKE))
        values.update(changes)
        return argparse.Namespace(**values)
    def test_invalid_accounts_rejected_on_native_and_qoder_prepare(self):
        for selected in ('codex','qoder'):
            config=copy.deepcopy(self.config);config['token_plan_accounts']={'version':2}
            with self.assertRaises(ConfigError): resolve(config,self.workspace,selected)

    def test_provider_and_native_model(self):
        for value in (None, 'codex'): self.assertEqual(provider(value), 'codex')
        for value in ('', [], {}, True, 1, 'custom_api'):
            with self.assertRaises(ConfigError): provider(value)
        default = resolve({}, self.workspace)
        self.assertEqual(default['provider'], 'codex')
        self.assertIsNone(default['requested_model'])
        native = resolve(self.config, self.workspace, 'codex')
        self.assertEqual(native['requested_model'], 'old-native')
        with self.assertRaises(ConfigError): resolve(self.config, self.workspace, 'codex', MODEL)
        with self.assertRaises(ConfigError): resolve(self.config, self.workspace, 'token_plan', 'other-model')
        with self.assertRaises(ConfigError): implementation.run(self.args(provider='codex'))
        self.assertFalse((self.root/'output').exists())
        prepared = implementation.run(self.args(provider='codex', prepare_only=True))
        self.assertEqual(prepared['isolation']['shell_environment'], 'none; fixed PATH')
        self.assertNotIn('login_shell', prepared['isolation'])
    def test_active_review_is_not_implementation_source(self):
        route = resolve(self.config, self.workspace)
        self.assertEqual(route['requested_model'], 'glm-5.3')
        self.assertEqual(route['credential_key'], 'model_services.token_plan.value')
        self.assertEqual(route['base_url'], model_services.DEFAULTS['responses_base_url'])
        self.assertEqual(self.config['plugins']['qwen']['model'], 'custom-model')
        self.assertEqual(route['secondary_model'], 'old-native')
    def test_legacy_migration_key_is_not_stolen_from_custom_api(self):
        legacy = dict(codex_relay={'implementation_provider': 'token_plan'}, plugins={'qwen': dict(model='old', **model_services.DEFAULTS, credential_paths=[str(self.secret)])})
        self.assertEqual(resolve(legacy, self.workspace)['credential_key'], 'tokenplan.value')
        legacy['plugins']['qwen']['responses_base_url'] = 'https://custom.invalid/v1'
        with self.assertRaises(ConfigError): resolve(legacy, self.workspace)
    def test_prepare_never_authenticates_or_launches(self):
        self.secret.unlink()
        with patch.object(implementation, 'child_environment', side_effect=AssertionError('credential read')), patch.object(implementation, 'execute', side_effect=AssertionError('process launch')), patch.object(implementation, 'capability', side_effect=AssertionError('capability launch')):
            result = implementation.run(self.args(prepare_only=True))
        self.assertEqual(result['status'], 'prepared')
        self.assertIsNone(result['cli_version'])
        self.assertFalse((self.root/'output/codex-home').exists())
        with self.assertRaises(FileExistsError): implementation.run(self.args(prepare_only=True))
        with self.assertRaises(ConfigError): implementation.run(self.args(out=self.workspace/'output', prepare_only=True))
    def test_fake_success_is_requested_only_and_isolated(self):
        result = implementation.run(self.args())
        self.assertTrue(result['completed']); self.assertFalse(result['accepted'])
        self.assertIsNone(result['actual_model']); self.assertEqual(result['model_evidence'], 'requested_only')
        self.assertEqual(result['cli_version'], 'codex-cli 0.163.0')
        audit = json.loads((self.workspace/'fake-audit.json').read_text())
        self.assertTrue(audit['token_present']); self.assertNotIn('OFFLINE_FAKE_TOKEN', json.dumps(audit))
        self.assertFalse(set(audit['env_keys']) & {'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'})
        self.assertEqual(Path(audit['home']), self.root/'output/codex-home')
        self.assertEqual(audit['args'][audit['args'].index('--sandbox') + 1], 'workspace-write')
        self.assertEqual(Path(audit['args'][audit['args'].index('-C') + 1]), self.workspace)
        self.assertIn('--ignore-user-config', audit['args'])
        self.assertEqual(audit['options']['shell_environment_policy.inherit'], 'none')
        self.assertFalse(audit['options']['sandbox_workspace_write.network_access'])
        self.assertNotIn('features.shell_tool', audit['options'])
        for feature in implementation.DISABLED: self.assertFalse(audit['options']['features.'+feature])
        for path in (self.root/'output').rglob('*'):
            if path.is_file(): self.assertNotIn('OFFLINE_FAKE_TOKEN', path.read_text())
    def test_exact_token_plan_cli_versions(self):
        fixture = FAKE.read_text()
        for version, model in (('0.160.0', MODEL), ('0.163.0', MODEL),
                               ('0.169.1', MODEL), ('0.169.1', 'qwen4-max'),
                               ('0.169.0', MODEL), ('0.169.2', MODEL)):
            with self.subTest(version=version, model=model):
                name = version + '-' + model
                cli = self.root / ('codex-' + name)
                cli.write_text(fixture.replace('codex-cli 0.163.0', 'codex-cli ' + version))
                cli.chmod(0o700)
                audit = self.workspace / 'fake-audit.json'
                count = self.workspace / 'fake-count.txt'
                audit.unlink(missing_ok=True)
                count.unlink(missing_ok=True)
                result = implementation.run(self.args(name=name, model=model, cli=str(cli)))
                if version in ('0.169.0', '0.169.2'):
                    self.assertEqual(result['status'], 'failed')
                    self.assertFalse(audit.exists())
                    self.assertFalse(count.exists())
                    self.assertIsNone(result['log_sha256'])
                    self.assertEqual((self.root / name / 'codex-version.txt').read_text().strip(),
                                     'codex-cli ' + version)
                    continue
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(result['cli_version'], 'codex-cli ' + version)
                self.assertEqual(result['requested_model'], model)
                self.assertEqual(result['model_evidence'], 'requested_only')
                self.assertIs(result['isolation']['login_shell'], False)
                self.assertIn('Codex tool aliases', result['isolation']['shell_environment'])
                options = json.loads(audit.read_text())['options']
                self.assertNotIn('features.shell_tool', options)
                self.assertEqual(options['shell_environment_policy.inherit'], 'none')
                self.assertEqual(options['shell_environment_policy.set.PATH'],
                                 '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin')
                self.assertFalse(options['sandbox_workspace_write.network_access'])
                self.assertEqual(options['approval_policy'], 'never')
                self.assertIs(options.get('allow_login_shell'), False)
                self.assertIs(options.get('features.code_mode_host'), False)

    def test_invalid_protocol_and_nonzero_do_not_retry(self):
        for mode in ('failed', 'no-final', 'no-terminal', 'duplicate-terminal', 'multi-turn', 'nonzero'):
            with self.subTest(mode=mode):
                (self.workspace/'fake-mode.txt').write_text(mode)
                (self.workspace/'fake-count.txt').unlink(missing_ok=True)
                result = implementation.run(self.args(name=mode))
                self.assertFalse(result['completed']); self.assertEqual(result['status'], 'failed')
                self.assertEqual((self.workspace/'fake-count.txt').read_text(), '1')
                self.assertIsNotNone(result['log_sha256'])
                self.assertEqual(json.loads((self.root/mode/'status.json').read_text())['status'], 'failed')
    def test_failed_command_can_be_repaired_and_unknown_metadata_warns(self):
        for mode in ('command-repair', 'unknown'):
            (self.workspace/'fake-mode.txt').write_text(mode)
            result = implementation.run(self.args(name=mode))
            self.assertTrue(result['completed'])
            if mode == 'unknown': self.assertTrue(result['warnings'])
    def test_budget_inheritance_and_invalid_config(self):
        self.config['review'] = dict(timeout=37, idle_timeout=19)
        self.path.write_text(json.dumps(self.config))
        result = implementation.run(self.args(prepare_only=True))
        self.assertEqual(result['limits'], dict(timeout=37, idle_timeout=19, max_output_bytes=8388608))
        self.config['review']['idle_timeout'] = 901; self.path.write_text(json.dumps(self.config))
        with self.assertRaises(ConfigError): implementation.run(self.args(name='bad', prepare_only=True))
    def test_wrong_credential_slot_fails_without_cli(self):
        self.secret.write_text(json.dumps({'model_services': {'custom_api': {'value': 'WRONG_CUSTOM_TOKEN'}}}))
        result = implementation.run(self.args())
        self.assertFalse(result['completed']); self.assertFalse((self.workspace/'fake-count.txt').exists())
    def test_observed_metadata_prefix_and_error_items(self):
        from implementation_protocol import METADATA_WARNING
        prefix = 'Reading additional input from stdin...\n' + json.dumps({'type': 'thread.started'}) + '\n'
        tail = '\n'.join(json.dumps(e) for e in [{'type': 'turn.started'}, {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'candidate'}}, {'type': 'turn.completed'}])
        log = self.root/'observed.jsonl'
        log.write_text(prefix + json.dumps({'type': 'item.completed', 'item': {'type': 'error', 'message': METADATA_WARNING}}) + '\n' + tail)
        self.assertIn(METADATA_WARNING, validate(log, 0, 'glm-5.3')['warnings'])
        with self.assertRaisesRegex(RunError, 'implementation error item'):
            validate(log, 0)
        for message in ('permission denied', METADATA_WARNING + ' forged'):
            log.write_text(prefix + json.dumps({'type': 'item.completed', 'item': {'type': 'error', 'message': message}}) + '\n' + tail)
            with self.assertRaises(RunError): validate(log, 0)
        log.write_text('unexpected noise\n' + tail)
        with self.assertRaises(RunError): validate(log, 0)

    def test_unfinished_item_unknown_event_or_permission_denial_is_not_success(self):
        start = [{'type': 'turn.started'}, {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'not final'}}]
        log = self.root/'unfinished.jsonl'
        for middle, error in (
                ({'type': 'item.started', 'item': {'id': 'pending-command', 'type': 'command_execution'}},
                 'unfinished implementation item'),
                ({'type': 'unknown.event'}, 'unknown implementation event'),
                ({'type': 'item.completed', 'item': {'id': 'denied', 'type': 'permission_denied'}},
                 'implementation permission denial')):
            with self.subTest(event=middle):
                log.write_text('\n'.join(json.dumps(e) for e in start + [middle, {'type': 'turn.completed'}]))
                with self.assertRaisesRegex(RunError, error):
                    validate(log, 0)

    def test_sigterm_retains_failed_status_and_partial_evidence(self):
        (self.workspace/'fake-mode.txt').write_text('wait')
        cli = ROOT/'adapters/codex/skills/codex-relay/scripts/implementation.py'
        process = subprocess.Popen([sys.executable, str(cli), '--config', str(self.path), '--workspace', str(self.workspace), '--prompt', str(self.prompt), '--out', str(self.root/'signal-output'), '--cli', str(FAKE)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not (self.workspace/'fake-count.txt').exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue((self.workspace/'fake-count.txt').exists(), 'fake implementation started')
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=8)
            self.assertEqual(process.returncode, 1, stderr.decode())
            status = json.loads((self.root/'signal-output/status.json').read_text())
            self.assertEqual(status['status'], 'failed'); self.assertFalse(status['completed'])
            self.assertIsNotNone(status['log_sha256'])
        finally:
            if process.poll() is None: process.kill(); process.wait()

    def test_terminal_alone_is_not_success(self):
        log = self.root/'bad.jsonl'; log.write_text('{"type":"turn.completed"}\n')
        with self.assertRaises(RunError): validate(log, 0)

    def test_qwen4_route_and_execution(self):
        self.config['codex_relay']['token_plan_implementation_model'] = 'qwen4-max'
        self.path.write_text(json.dumps(self.config))
        route = resolve(self.config, self.workspace)
        self.assertEqual((route['requested_model'], route['cli_model']), ('qwen4-max', 'qwen4-max'))
        self.assertEqual(route['credential_key'], 'model_services.token_plan.value')
        result = implementation.run(self.args())
        self.assertTrue(result['completed'])
        self.assertEqual(result['requested_effort'], 'high')
        options = json.loads((self.workspace/'fake-audit.json').read_text())['options']
        self.assertEqual(options['model'], 'qwen4-max')
        self.assertEqual(options['model_reasoning_effort'], 'high')
        catalog = Path(options['model_catalog_json'])
        self.assertTrue(catalog.is_absolute())
        entry = json.loads(catalog.read_text())['models'][0]
        self.assertEqual(entry['slug'], 'qwen4-max')
        self.assertEqual(entry['input_modalities'], ['text'])
        self.assertFalse(entry['supports_parallel_tool_calls'])
        self.assertFalse(entry['supports_reasoning_summaries'])
        self.assertEqual(resolve(self.config, self.workspace, 'qoder')['requested_model'], MODEL)
        self.assertEqual(resolve(self.config, self.workspace, 'token_plan', MODEL)['requested_model'], MODEL)
        with self.assertRaises(ConfigError): resolve(self.config, self.workspace, 'codex', 'qwen4-max')
        with self.assertRaises(ConfigError): resolve(self.config, self.workspace, 'qoder', 'qwen4-max')
        self.assertEqual(self.config['plugins']['qwen']['model'], 'custom-model')

    def test_qwen4_prepare_catalog_without_credentials(self):
        self.config['codex_relay']['token_plan_implementation_model'] = 'qwen4-max'
        self.path.write_text(json.dumps(self.config)); self.secret.unlink()
        with patch.object(implementation, 'child_environment', side_effect=AssertionError('credential read')), patch.object(implementation, 'execute', side_effect=AssertionError('launch')):
            result = implementation.run(self.args(prepare_only=True))
        self.assertEqual(result['requested_model'], 'qwen4-max')
        self.assertEqual(result['requested_effort'], 'high')
        config = json.loads((self.root/'output/codex-config.json').read_text())
        self.assertEqual(config['model'], result['cli_model'])
        self.assertTrue(Path(config['model_catalog_json']).is_file())
        for mode in ('failed', 'no-final', 'no-terminal', 'duplicate-terminal', 'multi-turn', 'nonzero'):
            self.secret.write_text(json.dumps({'model_services': {'token_plan': {'value': 'OFFLINE_FAKE_TOKEN'}}}))
            (self.workspace/'fake-mode.txt').write_text(mode)
            failed = implementation.run(self.args(name='qwen4-' + mode))
            self.assertEqual(failed['status'], 'failed')
            self.assertFalse(failed['completed'])

    def test_new_token_plan_models_prepare_from_config_and_override(self):
        script = ROOT / 'adapters/codex/skills/codex-relay/scripts/implementation.py'
        self.secret.unlink()
        relay = self.config['codex_relay']
        for requested in ('qwen3.8-max', 'deepseek-v4-pro', 'deepseek-v4.1-flash'):
            for override in (False, True):
                with self.subTest(requested=requested, override=override):
                    relay['implementation_provider'] = 'codex' if override else 'token_plan'
                    relay['token_plan_implementation_model'] = MODEL if override else requested
                    self.path.write_text(json.dumps(self.config))
                    out = self.root / f'{requested}-{override}'
                    command = [
                        sys.executable, str(script), '--config', str(self.path), '--workspace',
                        str(self.workspace), '--prompt', str(self.prompt), '--out', str(out), '--prepare-only',
                    ]
                    if override:
                        command.extend(['--provider', 'token_plan', '--model', requested])
                    process = subprocess.run(command, capture_output=True, text=True)
                    self.assertEqual(process.returncode, 0, process.stderr)
                    status = json.loads((out / 'status.json').read_text())
                    self.assertEqual(status['provider'], 'token_plan')
                    self.assertEqual(status['wire_api'], 'responses')
                    self.assertEqual(status['requested_model'], requested)
                    self.assertIs(status['completed'], False)
                    prepared = json.loads((out / 'codex-config.json').read_text())
                    self.assertEqual(prepared['model'], requested)

    def test_known_external_models_are_rejected_for_native_codex(self):
        relay = self.config['codex_relay']
        relay['implementation_provider'] = 'codex'
        for external in (MODEL, 'GLM-5.3', 'qwen4-max', 'qwen3.8-max', 'deepseek-v4-pro', 'deepseek-v4.1-flash'):
            with self.subTest(external=external):
                relay['secondary_model'] = external
                with self.assertRaisesRegex(ConfigError, 'requires its matching provider'):
                    resolve(self.config, self.workspace)
                relay['secondary_model'] = 'old-native'
                with self.assertRaisesRegex(ConfigError, 'requires its matching provider'):
                    resolve(self.config, self.workspace, 'codex', external)

    def test_new_token_plan_models_do_not_change_qoder_fixed_model(self):
        for external in ('qwen3.8-max', 'deepseek-v4-pro', 'deepseek-v4.1-flash'):
            with self.subTest(external=external):
                self.config['codex_relay']['token_plan_implementation_model'] = external
                self.assertEqual(resolve(self.config, self.workspace, 'qoder')['requested_model'], MODEL)
                with self.assertRaisesRegex(ConfigError, 'only logical glm-5.3'):
                    resolve(self.config, self.workspace, 'qoder', external)

    def test_strict_token_model_even_for_inactive_provider(self):
        for index, bad in enumerate(('', 'Qwen4-Max', 'qwen4-max-preview', 'DeepSeek-v4-pro', [], {}, True, 4)):
            with self.subTest(bad=bad):
                self.config['codex_relay']['token_plan_implementation_model'] = bad
                self.path.write_text(json.dumps(self.config))
                with patch.object(implementation, 'child_environment', side_effect=AssertionError('credential read')), patch.object(implementation, 'execute', side_effect=AssertionError('launch')):
                    with self.assertRaises(ConfigError): implementation.run(self.args(name='bad-' + str(index), prepare_only=True, provider='codex'))
                self.assertFalse((self.root/('bad-' + str(index))).exists())
        self.config['codex_relay']['token_plan_implementation_model'] = None
        self.assertEqual(resolve(self.config, self.workspace)['requested_model'], MODEL)

    def test_qwen4_rejects_glm_metadata_warning(self):
        from implementation_protocol import METADATA_WARNING
        events = [{'type': 'thread.started'}, {'type': 'item.completed', 'item': {'type': 'error', 'message': METADATA_WARNING}}, {'type': 'turn.started'}, {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'candidate'}}, {'type': 'turn.completed'}]
        log = self.root/'metadata.jsonl'
        log.write_text('\n'.join(json.dumps(e) for e in events))
        self.assertTrue(validate(log, 0, 'glm-5.3')['result'])
        with self.assertRaises(RunError): validate(log, 0, 'qwen4-max')

if __name__ == '__main__': unittest.main()
