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
            secret.write_text(json.dumps({'tokenplan': {'value': 'legacy'}, 'model_services': {'token_plan': {'value':'one'}, 'custom_api': {'value':'two'}}}))
            q = dict(model='fixture', base_url='https://fixture.invalid', credential_paths=[str(secret)])
            for key, expected in [('model_services.token_plan.value','one'),('model_services.custom_api.value','two'),('tokenplan.value','legacy'),(None,'legacy')]:
                fields = dict(q)
                if key is not None: fields['credential_key'] = key
                channel = resolve({'plugins': {'qwen': fields}}, 'qwen', None, root, {})
                self.assertEqual(child_environment(channel)['ANTHROPIC_API_KEY'], expected)
            secret.write_text('{"tokenplan":{"value":"legacy"}}')
            fields = dict(q, credential_key='model_services.custom_api.value')
            with self.assertRaises(ConfigError): child_environment(resolve({'plugins':{'qwen': fields}},'qwen',None,root,{}))
            for invalid in ('', 'other.value', None, [], 2):
                with self.assertRaises(ConfigError): resolve({'plugins':{'qwen':dict(q,credential_key=invalid)}}, 'qwen', None, root, {})

    def test_public_contract_validated_before_inactive_routes(self):
        for channel in ('claude','qoder'):
            with self.assertRaises(ConfigError):
                resolve({'token_plan_accounts':{'version':2}},channel,None,Path('/offline'),{})

    def test_prepare_public_accounts_without_credentials(self):
        from unittest.mock import patch
        from token_plan_transport import Transport
        config = {'plugins':{'qwen':dict(model='glm-5.3',base_url='https://token-plan.maas.qianwenaiapi.com/apps/anthropic',credential_paths=['/offline/primary.json'],credential_key='tokenplan.value')},'token_plan_accounts':{'version':1,'auto_failover':True,'backup':{'credential_paths':['/offline/backup.json'],'credential_key':'model_services.token_plan_backup.value'}}}
        with patch.object(Path,'read_text',side_effect=AssertionError('prepare read secret')):
            route=resolve(config,'qwen',None,Path('/offline'),{})
        self.assertTrue(route['token_plan_accounts']['auto_failover'])
        self.assertEqual(route['base_url'],config['plugins']['qwen']['base_url'])
        with self.assertRaises(ConfigError):
            with Transport(route,1): pass
        config['token_plan_accounts']['backup']['credential_paths']=[]
        with self.assertRaises(ConfigError): resolve(config,'qwen',None,Path('/offline'),{})
        config['plugins']['qwen']['base_url']='https://custom.invalid'
        route=resolve(config,'qwen',None,Path('/offline'),{})
        self.assertNotIn('token_plan_accounts',route)

    def test_local_route_clears_inherited_provider_credentials(self):
        from unittest.mock import patch
        import os
        route={'channel':'qwen','executor':'claude','configured_model':'fixture','base_url':'http://127.0.0.1:9','credential_paths':[], '_local_token':'local-only'}
        with patch.dict(os.environ, {'OPENAI_API_KEY':'inherited-secret','AI_ANTI_QWEN_TOKEN':'inherited-secret','ANTHROPIC_AUTH_TOKEN':'inherited-secret'}):
            env=child_environment(route)
        self.assertEqual(env['ANTHROPIC_API_KEY'],'local-only')
        self.assertNotIn('OPENAI_API_KEY',env);self.assertNotIn('AI_ANTI_QWEN_TOKEN',env);self.assertNotIn('ANTHROPIC_AUTH_TOKEN',env)
        self.assertIn('127.0.0.1',env['NO_PROXY'])
