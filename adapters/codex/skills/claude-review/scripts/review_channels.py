"""Explicit review transport selection; credentials are read only before execution."""
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from ai_anti_config import ConfigError, lookup

CONFLICTS = ('ANTHROPIC_API_KEY', 'ANTHROPIC_MODEL', 'ANTHROPIC_SMALL_FAST_MODEL',
             'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY')


def single_line(value, key):
    if not isinstance(value, str) or not value.strip() or any(c in value for c in '\r\n\x00'):
        raise ConfigError(key + ' must be a non-empty single-line string')
    return value


def resolve(config, channel, model, repo, families):
    channel = channel if channel is not None else lookup(config, 'codex_relay.review_channel')
    channel = 'claude' if channel is None else channel
    if not isinstance(channel, str) or channel not in ('claude', 'qwen'):
        raise ConfigError('codex_relay.review_channel must be claude or qwen')
    if channel == 'claude':
        model = model or 'opus'
        return {'channel': channel, 'requested_model': model, 'configured_model': model,
                'cli_model': model, 'pattern': families[model]}
    if model is not None:
        raise ConfigError('Qwen rejects --model; configure plugins.qwen.model instead')
    model = single_line(lookup(config, 'plugins.qwen.model'), 'plugins.qwen.model')
    url = single_line(lookup(config, 'plugins.qwen.base_url'), 'plugins.qwen.base_url')
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme in ('http', 'https') and bool(parsed.hostname) and parsed.port != 0
    except ValueError:
        valid = False
    if not valid or any(c.isspace() for c in url):
        raise ConfigError('plugins.qwen.base_url must be a valid http(s) URL')
    paths = lookup(config, 'plugins.qwen.credential_paths')
    if not isinstance(paths, list) or not paths:
        raise ConfigError('plugins.qwen.credential_paths must be a non-empty path array')
    resolved = []
    for value in paths:
        path = Path(single_line(value, 'plugins.qwen.credential_paths entry')).expanduser()
        resolved.append(path if path.is_absolute() else repo / path)
    return {'channel': channel, 'requested_model': model, 'configured_model': model,
            'cli_model': 'sonnet', 'pattern': re.compile(re.escape(model)),
            'base_url': url, 'credential_paths': resolved}


def evidence(channel):
    return {key: channel[key] for key in ('channel', 'configured_model', 'requested_model', 'cli_model')}


def child_environment(channel):
    if channel['channel'] == 'claude':
        return None
    token = None
    for path in channel['credential_paths']:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            value = lookup(data, 'tokenplan.value')
        except (OSError, UnicodeError, ValueError, TypeError):
            continue
        if isinstance(value, str) and value.strip() and not any(c in value for c in '\r\n\x00'):
            token = value
            break
    if token is None:
        raise ConfigError('Qwen credential unavailable: tokenplan.value must be a non-empty string')
    env = dict(os.environ)
    for key in CONFLICTS:
        env.pop(key, None)
    env.update(ANTHROPIC_AUTH_TOKEN=token, ANTHROPIC_BASE_URL=channel['base_url'])
    for family in ('OPUS', 'SONNET', 'HAIKU'):
        env['ANTHROPIC_DEFAULT_' + family + '_MODEL'] = channel['configured_model']
    return env
