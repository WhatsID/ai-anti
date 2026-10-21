"""Offline credential-key contract: no real HOME or CLI invocation."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'skills/claude-review/scripts'))
from ai_anti_config import ConfigError
from review_channels import resolve, child_environment

class CredentialKeys(unittest.TestCase):
    def test_slots_missing_and_legacy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            secret = root/'secret.json'
            secret.write_text(json.dumps({'tokenplan': {'value': 'legacy'}, 'review_services': {'token_plan': {'value':'one'}, 'custom_api': {'value':'two'}}}))
            q = dict(model='fixture', base_url='https://fixture.invalid', credential_paths=[str(secret)])
            for key, expected in [('review_services.token_plan.value','one'),('review_services.custom_api.value','two'),('tokenplan.value','legacy'),(None,'legacy')]:
                fields = dict(q)
                if key is not None: fields['credential_key'] = key
                channel = resolve({'plugins': {'qwen': fields}}, 'qwen', None, root, {})
                self.assertEqual(child_environment(channel)['ANTHROPIC_API_KEY'], expected)
            secret.write_text('{"tokenplan":{"value":"legacy"}}')
            fields = dict(q, credential_key='review_services.custom_api.value')
            with self.assertRaises(ConfigError): child_environment(resolve({'plugins':{'qwen': fields}},'qwen',None,root,{}))
            for invalid in ('', 'other.value', None, [], 2):
                with self.assertRaises(ConfigError): resolve({'plugins':{'qwen':dict(q,credential_key=invalid)}}, 'qwen', None, root, {})
