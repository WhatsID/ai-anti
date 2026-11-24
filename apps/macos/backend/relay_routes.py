"""Shared relay route schema. Validation operates only on supplied values."""
import implementation_models
import model_services

EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max', 'ultra')
OFFICIAL_PROVIDER = model_services.OFFICIAL_CHANNEL
REVIEW_MODELS = model_services.REVIEW_MODELS
IMPLEMENTATION_MODELS = {'token_plan': implementation_models.MODELS, **model_services.DIRECT_MODELS}
AGY_REVIEW_PROVIDERS = ('codex', OFFICIAL_PROVIDER)
AGY_IMPLEMENTATION_PROVIDERS = ('codex', OFFICIAL_PROVIDER)
AGY_SECTION_KEYS = ('review', 'implementation', 'dual_plan', 'codex_defaults')
DEFAULT_REVIEW = {'provider': 'codex', 'model': None, 'effort': None, 'high_risk_effort': None}
ROLES = ('review', 'implementation')


def _object(value, key, error, allowed=()):
    if not isinstance(value, dict):
        raise error('ai-anti config: ' + key + ' must be an object')
    if allowed:
        unknown = value.keys() - allowed
        if unknown:
            raise error('ai-anti config: ' + key + ' has unknown keys: '
                        + ', '.join(sorted(map(repr, unknown))))
    return value


def parse_host_relay(config, host_key, allowed_keys, error):
    if config is None:
        return None
    if not isinstance(config, dict):
        raise error('ai-anti config: ' + host_key + ' parent is not an object')
    value = config.get(host_key)
    return None if value is None else _object(value, host_key, error, allowed_keys)


def _effort_fields(role):
    return ('effort', 'high_risk_effort') if role == 'review' else ('effort',)


def _efforts(value, key, role, error):
    result = {field: value.get(field) for field in _effort_fields(role)}
    for field, effort in result.items():
        if effort is not None and effort not in EFFORTS:
            raise error('ai-anti config: ' + key + '.' + field + ' must be one of ' + ', '.join(EFFORTS))
    base, elevated = result['effort'], result.get('high_risk_effort')
    if base is not None and elevated is not None and EFFORTS.index(elevated) < EFFORTS.index(base):
        raise error('ai-anti config: ' + key + '.high_risk_effort must not be lower than ' + key + '.effort')
    return result


def parse_route(value, host_key, role, allowed_providers, error):
    key = host_key + '.' + role
    if value is None:
        return dict(DEFAULT_REVIEW) if role == 'review' else None
    _object(value, key, error)
    provider = value.get('provider')
    if provider is None and role == 'review':
        provider = 'codex'
    allowed_fields = ('provider', 'service', 'model') if provider == OFFICIAL_PROVIDER else (
        'provider', 'model', *_effort_fields(role))
    _object(value, key, error, allowed_fields)
    if provider not in allowed_providers:
        raise error('ai-anti config: ' + key + '.provider must be one of ' + ', '.join(allowed_providers))
    model = value.get('model')
    if provider == OFFICIAL_PROVIDER:
        models = REVIEW_MODELS if role == 'review' else IMPLEMENTATION_MODELS
        service = value.get('service')
        if not isinstance(service, str) or service not in models or model not in models[service]:
            raise error('ai-anti config: ' + key + ' service and model do not match')
        return dict(provider=provider, service=service, model=model)

    if not (role == 'review' and model is None):
        if not isinstance(model, str) or not model or any(c.isspace() for c in model):
            raise error('ai-anti config: ' + key + '.model must be a non-empty string without whitespace')
    return dict(provider=provider, model=model, **_efforts(value, key, role, error))


def parse_codex_defaults(value, host_key, error):
    if value is None:
        return {}
    key = host_key + '.codex_defaults'
    _object(value, key, error, ROLES)
    result = {}
    for role, fields in value.items():
        role_key = key + '.' + role
        _object(fields, role_key, error, _effort_fields(role))
        result[role] = {field: effort for field, effort in _efforts(fields, role_key, role, error).items()
                        if effort is not None}
    return result


def is_official(route):
    return bool(route and route['provider'] == OFFICIAL_PROVIDER)


def uses_token_plan(route):
    return is_official(route) and route['service'] == 'token_plan'


def vendor_service(route):
    if is_official(route) and route['service'] in model_services.VENDOR_ENDPOINTS:
        return route['service']
