"""User-level ai-anti config (~/.config/ai-anti/config.json), Python 3.9 stdlib only.

A missing file, missing key or null value means the built-in default. A file that
exists but cannot be parsed, or holds the wrong types, fails closed: a broken
config must never silently switch review channels or models.
"""
import json
from pathlib import Path
import re
import sys


class ConfigError(ValueError):
    pass


# Keys are passed to `claude --model` as one argv element (never a shell), so they
# must be a value the CLI itself accepts: an alias or a full model name.
ALIAS = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]*')
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max', 'ultra')
RISK_TIERS = ('standard', 'high_risk')
AGY_EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
CLAUDE_EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
AGY_DUAL_PLAN_PARTNERS = ('claude',)
AGY_DUAL_PLAN_SYNTHESIZERS = ('agy', 'claude')
DUAL_PLAN_PARTNERS = ('agy',)
DUAL_PLAN_SYNTHESIZERS = ('claude', 'agy')
DUAL_PLAN_ON_FAILURE = ('stop', 'fallback')


BACKEND = Path(__file__).resolve().parents[5] / 'apps/macos/backend'


def config_path():
    return Path.home() / '.config' / 'ai-anti' / 'config.json'


def load(path=None):
    path = Path(path) if path else config_path()
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError) as exc:
        raise ConfigError('unreadable ai-anti config ' + str(path) + ': ' + str(exc)) from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ConfigError('invalid JSON in ai-anti config ' + str(path) + ': ' + str(exc)) from exc
    if not isinstance(data, dict):
        raise ConfigError('ai-anti config top level must be an object: ' + str(path))
    return data


def lookup(data, dotted):
    """Value at a dotted key, or None when any level is absent or null."""
    node = data
    for key in dotted.split('.'):
        if node is None:
            return None
        if not isinstance(node, dict):
            raise ConfigError('ai-anti config: ' + dotted + ' parent is not an object')
        node = node.get(key)
    return node


def model_families(data, defaults):
    """Built-in families overridden/extended by claude_review.model_families.

    Configured patterns are used verbatim with fullmatch and re.ASCII; they must
    only accept the intended family with ASCII digit version segments.
    """
    families = dict(defaults)
    raw = lookup(data, 'claude_review.model_families')
    if raw is None:
        return families
    if not isinstance(raw, dict):
        raise ConfigError('ai-anti config: claude_review.model_families must be an object')
    for alias, pattern in raw.items():
        if not ALIAS.fullmatch(alias):
            raise ConfigError('ai-anti config: invalid model alias ' + repr(alias))
        if not isinstance(pattern, str) or not pattern:
            raise ConfigError('ai-anti config: pattern for ' + alias + ' must be a non-empty string')
        try:
            families[alias] = re.compile(pattern, re.ASCII)
        except re.error as exc:
            raise ConfigError('ai-anti config: invalid pattern for ' + alias + ': ' + str(exc)) from exc
    return families


def review_limits(data, timeout=None, idle_timeout=None):
    """Validate persisted budgets before applying per-call overrides (seconds)."""
    if not isinstance(data, dict) or ('review' in data and not isinstance(data['review'], dict)):
        raise ConfigError('ai-anti config: review must be an object')
    values = []
    for key, default, maximum, override in (
            ('timeout', 1800, 3600, timeout), ('idle_timeout', 900, 900, idle_timeout)):
        value = lookup(data, 'review.' + key)
        if value is None:
            value = default
        for candidate in (value, override):
            if candidate is not None and (type(candidate) is not int or not 0 < candidate <= maximum):
                raise ConfigError('invalid bounded resource limits: ai-anti config review.' + key + ' must be an integer in 1..' + str(maximum))
        values.append(value if override is None else override)
    return tuple(values)


def _codex_effort(data, key):
    value = lookup(data, key)
    if value is not None and (not isinstance(value, str) or value not in EFFORTS):
        raise ConfigError('ai-anti config: ' + key + ' must be one of ' + ', '.join(EFFORTS))
    return value


def _tiered_effort(base, elevated, base_key, elevated_key, risk_tier):
    if risk_tier is not None and risk_tier not in RISK_TIERS:
        raise ConfigError('review risk tier must be one of ' + ', '.join(RISK_TIERS))
    if base is not None and elevated is not None and EFFORTS.index(elevated) < EFFORTS.index(base):
        raise ConfigError('ai-anti config: ' + elevated_key + ' must not be lower than ' + base_key)
    if risk_tier == 'standard':
        return base
    return base if elevated is None else elevated


def review_codex_effort(data, risk_tier=None):
    """Return the optional Codex effort for the Claude-led review tier."""
    base_key = 'review.codex_effort'
    elevated_key = 'review.codex_high_risk_effort'
    return _tiered_effort(_codex_effort(data, base_key), _codex_effort(data, elevated_key),
                          base_key, elevated_key, risk_tier)


def claude_implementation(data):
    """Return the explicitly selected Claude-led implementation route."""
    key = 'claude_relay.implementation'
    value = lookup(data, key)
    if value is None:
        return None
    if not isinstance(value, dict) or value.get('provider') != 'codex':
        raise ConfigError('ai-anti config: ' + key + ' must be an object with provider codex')
    model = value.get('model')
    if not isinstance(model, str) or not model or any(c.isspace() for c in model):
        raise ConfigError('ai-anti config: ' + key + '.model must be a non-empty string without whitespace')
    return {'provider': 'codex', 'model': model, 'effort': _codex_effort(data, key + '.effort')}


def _agy_object(value, key, allowed):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConfigError('ai-anti config: ' + key + ' must be an object')
    unknown = value.keys() - allowed
    if unknown:
        raise ConfigError('ai-anti config: ' + key + ' has unknown keys: '
                          + ', '.join(sorted(map(repr, unknown))))
    return value


def _relay_routes():
    if not (BACKEND / 'relay_routes.py').is_file():
        raise ConfigError('agy_relay requires apps/macos/backend/relay_routes.py in the ai-anti checkout')
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    try:
        import relay_routes
    except ImportError as exc:
        raise ConfigError('agy_relay requires the complete ai-anti source checkout') from exc
    return relay_routes


def agy_relay_section(data):
    routes = _relay_routes()
    return routes.parse_host_relay(data, 'agy_relay', routes.AGY_SECTION_KEYS, ConfigError)


def _agy_route(data, role):
    routes = _relay_routes()
    section = agy_relay_section(data) or {}
    routes.parse_codex_defaults(section.get('codex_defaults'), 'agy_relay', ConfigError)
    providers = routes.AGY_REVIEW_PROVIDERS if role == 'review' else routes.AGY_IMPLEMENTATION_PROVIDERS
    return routes.parse_route(section.get(role), 'agy_relay', role, providers, ConfigError)


def agy_review(data):
    """Select the independent AGY review route, with Codex defaults."""
    return _agy_route(data, 'review')


def agy_review_effort(review, risk_tier=None):
    """Select the effort for a parsed AGY Codex review route."""
    if review['provider'] != 'codex':
        return None
    return _tiered_effort(review['effort'], review['high_risk_effort'], 'agy_relay.review.effort',
                          'agy_relay.review.high_risk_effort', risk_tier)


def agy_implementation(data):
    """Select the explicitly configured AGY implementation route, if any."""
    return _agy_route(data, 'implementation')


def claude_qwen_executor(data):
    """Select the Claude-led Qwen executor independently of codex_relay."""
    new_key = 'claude_relay.qwen_executor'
    old_key = 'plugins.qwen.claude_executor'
    new_value = lookup(data, new_key)
    old_value = lookup(data, old_key)
    for key, value in ((new_key, new_value), (old_key, old_value)):
        if value is not None and (not isinstance(value, str) or value not in ('claude', 'codex')):
            raise ConfigError('ai-anti config: ' + key + ' must be claude or codex')
    if new_value is not None and old_value is not None and new_value != old_value:
        raise ConfigError('ai-anti config: ' + new_key + ' conflicts with ' + old_key)
    if new_value is not None:
        return new_value
    if old_value is not None:
        return old_value
    return 'claude'


def claude_dual_plan_defaults():
    """Return a fresh copy of the built-in dual-plan settings."""
    return {
        'enabled': False,
        'partner': 'agy',
        'model': None,
        'effort': None,
        'synthesizer': 'claude',
        'on_failure': 'stop',
    }


def claude_dual_plan(data):
    """Return validated Claude-led dual-plan settings, or None when absent/null."""
    key = 'claude_relay.dual_plan'
    value = lookup(data, key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConfigError('ai-anti config: ' + key + ' must be an object')

    defaults = claude_dual_plan_defaults()
    enabled = value.get('enabled', defaults['enabled'])
    if type(enabled) is not bool:
        raise ConfigError('ai-anti config: ' + key + '.enabled must be a boolean')

    partner = value.get('partner')
    if partner is None:
        partner = defaults['partner']
    elif not isinstance(partner, str) or partner not in DUAL_PLAN_PARTNERS:
        raise ConfigError('ai-anti config: ' + key + '.partner must be one of ' + ', '.join(DUAL_PLAN_PARTNERS))

    model = value.get('model', defaults['model'])
    if model is not None and (not isinstance(model, str) or not model or any(c.isspace() for c in model)):
        raise ConfigError('ai-anti config: ' + key + '.model must be a non-empty string without whitespace')

    effort = value.get('effort', defaults['effort'])
    if effort is not None and (not isinstance(effort, str) or effort not in AGY_EFFORTS):
        raise ConfigError('ai-anti config: ' + key + '.effort must be one of ' + ', '.join(AGY_EFFORTS))

    synthesizer = value.get('synthesizer')
    if synthesizer is None:
        synthesizer = defaults['synthesizer']
    elif not isinstance(synthesizer, str) or synthesizer not in DUAL_PLAN_SYNTHESIZERS:
        raise ConfigError('ai-anti config: ' + key + '.synthesizer must be one of ' + ', '.join(DUAL_PLAN_SYNTHESIZERS))

    on_failure = value.get('on_failure')
    if on_failure is None:
        on_failure = defaults['on_failure']
    elif not isinstance(on_failure, str) or on_failure not in DUAL_PLAN_ON_FAILURE:
        raise ConfigError('ai-anti config: ' + key + '.on_failure must be one of ' + ', '.join(DUAL_PLAN_ON_FAILURE))

    return {
        'enabled': enabled,
        'partner': partner,
        'model': model,
        'effort': effort,
        'synthesizer': synthesizer,
        'on_failure': on_failure,
    }


def agy_dual_plan_defaults():
    return {
        'enabled': False, 'partner': 'claude', 'model': None, 'effort': None,
        'synthesizer': 'agy', 'on_failure': 'stop',
    }


def agy_dual_plan(data):
    """Validate AGY-led planning independently of review and implementation."""
    key = 'agy_relay.dual_plan'
    result = agy_dual_plan_defaults()
    value = _agy_object((agy_relay_section(data) or {}).get('dual_plan'), key, result.keys())
    if value is None:
        return None

    prefix = 'ai-anti config: ' + key
    enabled = value.get('enabled', False)
    if type(enabled) is not bool:
        raise ConfigError(prefix + '.enabled must be a boolean')
    result['enabled'] = enabled
    for field, allowed in (('partner', AGY_DUAL_PLAN_PARTNERS), ('effort', CLAUDE_EFFORTS),
                           ('synthesizer', AGY_DUAL_PLAN_SYNTHESIZERS), ('on_failure', DUAL_PLAN_ON_FAILURE)):
        selected = value.get(field)
        if selected is not None:
            if not isinstance(selected, str) or selected not in allowed:
                raise ConfigError(f'{prefix}.{field} must be one of ' + ', '.join(allowed))
            result[field] = selected
    model = value.get('model')
    if model is not None and (not isinstance(model, str) or not model or any(c.isspace() for c in model)):
        raise ConfigError(prefix + '.model must be a non-empty string without whitespace')
    result['model'] = model
    return result
