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
