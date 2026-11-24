"""Public account parsing and one-run Token Plan proxy lifecycle."""
from contextlib import AbstractContextManager
import importlib.util
import json
from pathlib import Path
from ai_anti_config import ConfigError, lookup
from token_plan_proxy import Proxy

BACKEND = Path(__file__).resolve().parents[5] / 'apps/macos/backend'

def contract(config, repo):
    value = config.get('token_plan_accounts')
    path = BACKEND / 'token_plan_accounts.py'
    if not path.is_file():
        if value is None: return dict(version=1,auto_failover=False,backup=dict(credential_paths=[],credential_key='model_services.token_plan_backup.value'))
        raise ConfigError('Token Plan accounts requires the complete ai-anti source checkout')
    spec = importlib.util.spec_from_file_location('_aianti_accounts',path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.normalize(value,error=ConfigError)

def service_contract():
    path = BACKEND / 'model_services.py'
    if not path.is_file():
        raise ConfigError('model_services requires complete ai-anti checkout')
    spec = importlib.util.spec_from_file_location('_aianti_services', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_profiles(config):
    services = service_contract()
    def endpoint(value):
        from urllib.parse import urlsplit
        try:
            p = urlsplit(value)
            if value and (p.scheme not in ('http','https') or not p.hostname or p.port == 0 or p.username or p.password or p.query or p.fragment or any(c.isspace() for c in value)): raise ValueError()
        except (ValueError,TypeError): raise ConfigError('Invalid service endpoint') from None
    def model(value, optional=False):
        if not isinstance(value, str) or (not value and not optional) or any(c.isspace() for c in value):
            raise ConfigError('Invalid service model')
    def pathcheck(value):
        if not isinstance(value, str) or not value or '..' in Path(value).parts or any(c in value for c in '\r\n\x00'):
            raise ConfigError('Invalid credential path')
    qwen = lookup(config, 'plugins.qwen')
    if qwen is not None and not isinstance(qwen, dict):
        raise ConfigError('plugins.qwen must be an object')
    return services.load(config, qwen or {}, endpoint, model, pathcheck, ConfigError)


def attach(config, channel, repo, *, force=False):
    accounts = contract(config, repo)  # Public validation also happens in prepare-only.
    if channel.get('channel') != 'qwen':
        return channel
    if not force:
        if any(k in config for k in ('model_services', 'review_services')):
            force = load_profiles(config)['active_api'] == 'token_plan'
        else:
            field = 'responses_base_url' if channel['wire_api'] == 'responses' else 'base_url'
            configured = lookup(config, 'plugins.qwen.' + field)
            force = configured == service_contract().DEFAULTS[field]
    token_keys = ('model_services.token_plan.value', 'review_services.token_plan.value')
    key = channel.get('credential_key')
    if force or key in token_keys:
        service_contract().token_plan_destination(channel['base_url'], channel['wire_api'], ConfigError)
        if key not in ('tokenplan.value', *token_keys):
            raise ConfigError('Token Plan primary credential must use its own profile')
        if accounts['auto_failover'] and (not accounts['backup']['credential_paths'] or not channel.get('credential_paths')):
            raise ConfigError('Token Plan failover requires both account credential paths')
        channel['token_plan_accounts'] = accounts
        channel['token_plan_backup_paths'] = [Path(p).expanduser() if Path(p).expanduser().is_absolute() else Path(repo)/p for p in accounts['backup']['credential_paths']]
    return channel


def credential(paths,key):
    for path in paths:
        try: value = lookup(json.loads(path.read_text('utf-8')),key)
        except (OSError,UnicodeError,ValueError,TypeError): continue
        if isinstance(value,str) and value and not any(c.isspace() for c in value) and '\x00' not in value: return value
    raise ConfigError('Token Plan credential unavailable')

class Transport(AbstractContextManager):
    def __init__(self,channel,budget,idle_timeout=900):
        self.channel = channel
        self.budget = budget
        self.idle_timeout = idle_timeout
        self.proxy = None
        self.original = channel.get('base_url')
        self.state = dict(enabled=bool(channel.get('token_plan_accounts',{}).get('auto_failover')),used_slot='primary',switch_count=0,switches=[])
    def __enter__(self):
        accounts = self.channel.get('token_plan_accounts',{})
        if not accounts.get('auto_failover'): return self
        primary = credential(self.channel['credential_paths'],self.channel['credential_key'])
        backup = credential(self.channel['token_plan_backup_paths'],accounts['backup']['credential_key'])
        try:
            self.proxy = Proxy(self.original,primary,backup,self.channel['wire_api'],self.budget,idle_timeout=self.idle_timeout)
            self.proxy.__enter__()
        except ValueError as exc: raise ConfigError(str(exc)) from exc
        self.channel['base_url'] = self.proxy.base_url
        self.channel['_local_token'] = self.proxy.token
        return self
    def evidence(self): return self.proxy.evidence() if self.proxy else dict(self.state)
    def __exit__(self,*args):
        if self.proxy: self.proxy.__exit__(*args)
        self.channel.pop('_local_token',None)
        if self.original is not None: self.channel['base_url'] = self.original
