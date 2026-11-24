"""Public implementation routing; no credentials or process/file side effects."""
from pathlib import Path
import sys

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
from review_channels import resolve_token_plan

MODEL = 'glm-5.3'
PROVIDERS = model_services.IMPLEMENTATION_PROVIDERS


def direct_route(chosen):
    for service, models in model_services.DIRECT_MODELS.items():
        if chosen in models:
            endpoint = model_services.VENDOR_ENDPOINTS[service] + '/chat/completions'
            return dict(provider='custom_api', requested_model=chosen, service=service,
                        endpoint=endpoint, executor='custom_api', wire_api='chat_completions')
    raise ConfigError('custom_api requires an explicit supported direct model ID')


def provider(value):
    value = 'codex' if value is None else value
    if not isinstance(value, str) or value not in PROVIDERS:
        raise ConfigError('codex_relay.implementation_provider must be codex, token_plan, qoder or custom_api')
    return value


def model(value, optional=False):
    if optional and value == '':
        return value
    if not isinstance(value, str) or not value or value != value.strip() or any(c.isspace() for c in value):
        raise ConfigError('invalid model ID')
    return value


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
    if selected == 'custom_api':
        field = 'codex_relay.direct_implementation'
        chosen = model_services.required_selection(lookup(config, field), model_services.DIRECT_MODELS, field, ConfigError)
        if explicit_model is not None:
            chosen['model'] = explicit_model
            model_services.selection(chosen, model_services.DIRECT_MODELS, field, ConfigError)
        return direct_route(chosen['model'])
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
    channel = resolve_token_plan(config, chosen, Path(workspace))
    channel.update(provider=selected, secondary_model=secondary)
    return channel


def public(route):
    return {k: v for k, v in route.items() if k not in ('pattern', 'credential_paths', 'credential_key', 'token_plan_backup_paths', 'token_plan_accounts') and not k.startswith('_')}
