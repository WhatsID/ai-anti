"""Public implementation routing; no credentials or process/file side effects."""
import copy
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[5]
REVIEW = ROOT / 'adapters/codex/skills/claude-review/scripts'
BACKEND = ROOT / 'apps/macos/backend'
if not (BACKEND / 'model_services.py').is_file():
    raise ImportError('implementation requires the complete ai-anti source checkout')
sys.path.insert(0, str(REVIEW))
sys.path.insert(0, str(BACKEND))
import model_services
import implementation_models
from ai_anti_config import ConfigError, lookup
from review_channels import resolve as review_channel

MODEL = 'glm-5.3'
PROVIDERS = ('codex', 'token_plan', 'qoder')


DIRECT_ROUTES = {
    'qwen': ('https://maas.qianwenaiapi.com/compatible-mode/v1/chat/completions',
             ('qwen4-max', 'qwen3.8-max')),
    'kimi': ('https://api.moonshot.cn/v1/chat/completions', ('kimi-k3.1-pro', 'kimi-k3')),
    'deepseek': ('https://api.deepseek.com/chat/completions', ('deepseek-v4-pro', 'deepseek-flash')),
}


def direct_route(chosen):
    for service, (url, models) in DIRECT_ROUTES.items():
        if chosen in models:
            return dict(provider='custom_api', requested_model=chosen, service=service,
                        endpoint=url, executor='custom_api', wire_api='chat_completions')
    raise ConfigError('custom_api requires an explicit supported direct model ID')


def provider(value):
    value = 'codex' if value is None else value
    if not isinstance(value, str) or value not in PROVIDERS:
        raise ConfigError('codex_relay.implementation_provider must be codex, token_plan or qoder')
    return value


def model(value, optional=False):
    if optional and value == '':
        return value
    if not isinstance(value, str) or not value or value != value.strip() or any(c.isspace() for c in value):
        raise ConfigError('invalid model ID')
    return value


def endpoint(value):
    if not isinstance(value, str):
        raise ConfigError('invalid service URL type')
    try:
        p = urlsplit(value)
        valid = not value or (p.scheme in ('http', 'https') and p.hostname and p.port != 0 and p.username is None and p.password is None and '?' not in value and '#' not in value and not any(c.isspace() for c in value))
    except ValueError:
        valid = False
    if not valid:
        raise ConfigError('invalid service URL')
    return value


def credential_path(value):
    if not isinstance(value, str) or not value or any(c in value for c in '\r\n\x00') or '..' in Path(value).parts:
        raise ConfigError('invalid credential path')


def resolve(config, workspace, explicit_provider=None, explicit_model=None):
    """Overrides are one coherent provider/model pair; never consult active_api for GLM."""
    if explicit_provider == 'custom_api':
        return direct_route(explicit_model)
    from token_plan_transport import contract
    contract(config, workspace)
    stored = provider(lookup(config, 'codex_relay.implementation_provider'))
    try:
        token_model = implementation_models.parse(lookup(config, 'codex_relay.token_plan_implementation_model'))
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    selected = stored if explicit_provider is None else provider(explicit_provider)
    secondary = lookup(config, 'codex_relay.secondary_model')
    if secondary is not None:
        model(secondary)
    chosen = explicit_model if explicit_model is not None else token_model if selected == 'token_plan' else MODEL if selected == 'qoder' else secondary
    if selected == 'codex':
        if chosen is not None:
            model(chosen)
        if chosen == 'GLM-5.3' or chosen in implementation_models.MODELS:
            raise ConfigError('external implementation model requires its matching provider')
        return dict(provider=selected, requested_model=chosen, executor='native', secondary_model=secondary)
    if selected == 'qoder':
        if chosen != MODEL:
            raise ConfigError('Qoder implementation supports only logical glm-5.3')
        return dict(provider=selected, requested_model=MODEL, executor='qoder', wire_api='qoder-cli', secondary_model=secondary)
    try:
        chosen = implementation_models.parse(chosen)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    qwen = lookup(config, 'plugins.qwen')
    qwen = {} if qwen is None else qwen
    if not isinstance(qwen, dict):
        raise ConfigError('plugins.qwen must be an object')
    profiles = model_services.load(config, qwen, endpoint, model, credential_path, ConfigError)
    profile = copy.deepcopy(profiles['profiles']['token_plan'])
    profile['model'] = chosen
    routed = {'plugins': {'qwen': profile}, 'token_plan_accounts': config.get('token_plan_accounts')}
    channel = review_channel(routed, 'qwen', None, Path(workspace), {}, executor='codex')
    from token_plan_transport import attach
    attach(config, channel, workspace, force=True)
    channel.update(provider=selected, secondary_model=secondary)
    return channel


def public(route):
    return {k: v for k, v in route.items() if k not in ('pattern', 'credential_paths', 'credential_key', 'token_plan_backup_paths', 'token_plan_accounts') and not k.startswith('_')}
