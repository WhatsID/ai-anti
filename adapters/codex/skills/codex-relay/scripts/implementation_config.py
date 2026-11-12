"""Public implementation routing; no credentials or process/file side effects."""
import copy
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[5]
REVIEW = ROOT / 'adapters/codex/skills/claude-review/scripts'
BACKEND = ROOT / 'apps/macos/backend'
if not (BACKEND / 'review_services.py').is_file():
    raise ImportError('implementation requires the complete ai-anti source checkout')
sys.path.insert(0, str(REVIEW))
sys.path.insert(0, str(BACKEND))
import review_services
from ai_anti_config import ConfigError, lookup
from review_channels import resolve as review_channel

MODEL = 'glm-5.3'
PROVIDERS = ('codex', 'token_plan')


def provider(value):
    value = 'codex' if value is None else value
    if not isinstance(value, str) or value not in PROVIDERS:
        raise ConfigError('codex_relay.implementation_provider must be codex or token_plan')
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
    stored = provider(lookup(config, 'codex_relay.implementation_provider'))
    selected = stored if explicit_provider is None else provider(explicit_provider)
    secondary = lookup(config, 'codex_relay.secondary_model')
    if secondary is not None:
        model(secondary)
    chosen = explicit_model if explicit_model is not None else MODEL if selected == 'token_plan' else secondary
    if selected == 'codex':
        if chosen is not None:
            model(chosen)
        if chosen == MODEL:
            raise ConfigError('glm-5.3 requires implementation provider token_plan')
        return dict(provider=selected, requested_model=chosen, executor='native', secondary_model=secondary)
    if chosen != MODEL:
        raise ConfigError('Token Plan implementation supports only glm-5.3')
    qwen = lookup(config, 'plugins.qwen')
    qwen = {} if qwen is None else qwen
    if not isinstance(qwen, dict):
        raise ConfigError('plugins.qwen must be an object')
    profiles = review_services.load(config, qwen, endpoint, model, credential_path, ConfigError)
    profile = copy.deepcopy(profiles['profiles']['token_plan'])
    profile['model'] = MODEL
    routed = {'plugins': {'qwen': profile}}
    channel = review_channel(routed, 'qwen', None, Path(workspace), {}, executor='codex')
    channel.update(provider=selected, secondary_model=secondary)
    return channel


def public(route):
    return {k: v for k, v in route.items() if k not in ('pattern', 'credential_paths', 'credential_key')}
