"""User-level ai-anti config (~/.config/ai-anti/config.json), Python 3.9 stdlib only.

A missing file, missing key or null value means the built-in default. A file that
exists but cannot be parsed, or holds the wrong types, fails closed: a broken
config must never silently switch review channels or models.
"""
import json
from pathlib import Path
import re


class ConfigError(ValueError):
    pass


# Keys are passed to `claude --model` as one argv element (never a shell), so they
# must be a value the CLI itself accepts: an alias or a full model name.
ALIAS = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]*')
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max', 'ultra')
AGY_EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
DUAL_PLAN_PARTNERS = ('agy',)
DUAL_PLAN_SYNTHESIZERS = ('claude', 'agy')
DUAL_PLAN_ON_FAILURE = ('stop', 'fallback')


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


def review_codex_effort(data):
    """Return the optional Codex effort for Claude-led review."""
    return _codex_effort(data, 'review.codex_effort')


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


def claude_qwen_executor(data):
    """Select the Claude-led Qwen executor independently of codex_relay."""
    key = 'plugins.qwen.claude_executor'
    value = lookup(data, key)
    if value is None:
        return 'claude'
    if value not in ('claude', 'codex'):
        raise ConfigError('ai-anti config: ' + key + ' must be claude or codex')
    return value


def claude_dual_plan(data):
    """Return validated Claude-led dual-plan settings, or None when absent/null."""
    key = 'claude_relay.dual_plan'
    value = lookup(data, key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConfigError('ai-anti config: ' + key + ' must be an object')

    if 'enabled' in value:
        enabled = value['enabled']
        if type(enabled) is not bool:
            raise ConfigError('ai-anti config: ' + key + '.enabled must be a boolean')
    else:
        enabled = False

    partner = value.get('partner')
    if partner is None:
        partner = 'agy'
    elif not isinstance(partner, str) or partner not in DUAL_PLAN_PARTNERS:
        raise ConfigError('ai-anti config: ' + key + '.partner must be one of ' + ', '.join(DUAL_PLAN_PARTNERS))

    model = value.get('model')
    if model is not None and (not isinstance(model, str) or not model or any(c.isspace() for c in model)):
        raise ConfigError('ai-anti config: ' + key + '.model must be a non-empty string without whitespace')

    effort = value.get('effort')
    if effort is not None and (not isinstance(effort, str) or effort not in AGY_EFFORTS):
        raise ConfigError('ai-anti config: ' + key + '.effort must be one of ' + ', '.join(AGY_EFFORTS))

    synthesizer = value.get('synthesizer')
    if synthesizer is None:
        synthesizer = 'claude'
    elif not isinstance(synthesizer, str) or synthesizer not in DUAL_PLAN_SYNTHESIZERS:
        raise ConfigError('ai-anti config: ' + key + '.synthesizer must be one of ' + ', '.join(DUAL_PLAN_SYNTHESIZERS))

    on_failure = value.get('on_failure')
    if on_failure is None:
        on_failure = 'stop'
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
