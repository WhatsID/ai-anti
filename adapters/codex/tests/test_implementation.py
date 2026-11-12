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
import review_services

FAKE = ROOT / 'adapters/codex/tests/fake_implementation.py'

class ImplementationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / 'workspace'; self.workspace.mkdir()
        self.secret = self.root / 'fixture-secret.json'
        self.secret.write_text(json.dumps({'review_services': {'token_plan': {'value': 'OFFLINE_FAKE_TOKEN'}, 'custom_api': {'value': 'WRONG_CUSTOM_TOKEN'}}}))
        profiles = {key: review_services.blank(key) for key in review_services.IDS}
        profiles['token_plan']['credential_paths'] = [str(self.secret)]
        profiles['custom_api'].update(model='custom-model', responses_base_url='https://custom.invalid/v1', base_url='https://custom.invalid/anthropic', credential_paths=[str(self.secret)])
        self.config = dict(codex_relay=dict(implementation_provider='token_plan', secondary_model='old-native'),
                           review_services=dict(version=1, active_api='custom_api', profiles=profiles),
                           plugins=dict(qwen=copy.deepcopy(profiles['custom_api'])))
        self.path = self.root / 'config.json'; self.path.write_text(json.dumps(self.config))
        self.prompt = self.root / 'prompt.txt'; self.prompt.write_text('Implement authorized offline fixture scope.')
    def tearDown(self): self.temp.cleanup()
    def args(self, name='output', **changes):
        values = dict(workspace=self.workspace, prompt=self.prompt, out=self.root/name, config=self.path,
                      provider=None, model=None, timeout=None, idle_timeout=None, prepare_only=False, cli=str(FAKE))
        values.update(changes)
        return argparse.Namespace(**values)
    def test_provider_and_native_model(self):
        for value in (None, 'codex'): self.assertEqual(provider(value), 'codex')
        for value in ('', [], {}, True, 1, 'custom_api'):
            with self.assertRaises(ConfigError): provider(value)
        self.assertEqual(resolve({}, self.workspace)['provider'], 'codex')
        native = resolve(self.config, self.workspace, 'codex')
        self.assertEqual(native['requested_model'], 'old-native')
        with self.assertRaises(ConfigError): resolve(self.config, self.workspace, 'codex', MODEL)
        with self.assertRaises(ConfigError): resolve(self.config, self.workspace, 'token_plan', 'other-model')
        with self.assertRaises(ConfigError): implementation.run(self.args(provider='codex'))
        self.assertFalse((self.root/'output').exists())
    def test_active_review_is_not_implementation_source(self):
        route = resolve(self.config, self.workspace)
        self.assertEqual(route['requested_model'], 'glm-5.3')
        self.assertEqual(route['credential_key'], 'review_services.token_plan.value')
        self.assertEqual(route['base_url'], review_services.DEFAULTS['responses_base_url'])
        self.assertEqual(self.config['plugins']['qwen']['model'], 'custom-model')
        self.assertEqual(route['secondary_model'], 'old-native')
    def test_legacy_migration_key_is_not_stolen_from_custom_api(self):
        legacy = dict(codex_relay={'implementation_provider': 'token_plan'}, plugins={'qwen': dict(model='old', **review_services.DEFAULTS, credential_paths=[str(self.secret)])})
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
        self.assertIn('workspace-write', audit['args'])
        self.assertEqual(audit['options']['shell_environment_policy.inherit'], 'none')
        self.assertFalse(audit['options']['sandbox_workspace_write.network_access'])
        self.assertNotIn('features.shell_tool', audit['options'])
        for feature in implementation.DISABLED: self.assertFalse(audit['options']['features.'+feature])
        for path in (self.root/'output').rglob('*'):
            if path.is_file(): self.assertNotIn('OFFLINE_FAKE_TOKEN', path.read_text())
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
        self.secret.write_text(json.dumps({'review_services': {'custom_api': {'value': 'WRONG_CUSTOM_TOKEN'}}}))
        result = implementation.run(self.args())
        self.assertFalse(result['completed']); self.assertFalse((self.workspace/'fake-count.txt').exists())
    def test_observed_metadata_prefix_and_error_items(self):
        from implementation_protocol import METADATA_WARNING
        prefix = 'Reading additional input from stdin...\n' + json.dumps({'type': 'thread.started'}) + '\n'
        tail = '\n'.join(json.dumps(e) for e in [{'type': 'turn.started'}, {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'candidate'}}, {'type': 'turn.completed'}])
        log = self.root/'observed.jsonl'
        log.write_text(prefix + json.dumps({'type': 'item.completed', 'item': {'type': 'error', 'message': METADATA_WARNING}}) + '\n' + tail)
        self.assertIn(METADATA_WARNING, validate(log, 0)['warnings'])
        for message in ('permission denied', METADATA_WARNING + ' forged'):
            log.write_text(prefix + json.dumps({'type': 'item.completed', 'item': {'type': 'error', 'message': message}}) + '\n' + tail)
            with self.assertRaises(RunError): validate(log, 0)
        log.write_text('unexpected noise\n' + tail)
        with self.assertRaises(RunError): validate(log, 0)

    def test_unfinished_item_or_unknown_event_is_not_success(self):
        start = [{'type': 'turn.started'}, {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'not final'}}]
        log = self.root/'unfinished.jsonl'
        for middle in ({'type': 'item.started', 'item': {'id': 'pending-command', 'type': 'command_execution'}}, {'type': 'unknown.event'}):
            log.write_text('\n'.join(json.dumps(e) for e in start + [middle, {'type': 'turn.completed'}]))
            with self.assertRaises(RunError): validate(log, 0)

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

if __name__ == '__main__': unittest.main()
