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


def resolve(config, channel, model, repo, families, executor=None, responses_url=None):
    channel = channel if channel is not None else lookup(config, 'codex_relay.review_channel')
    channel = 'claude' if channel is None else channel
    if not isinstance(channel, str) or channel not in ('claude', 'qwen', 'qoder'):
        raise ConfigError('codex_relay.review_channel must be claude, qwen or qoder')
    if channel != 'qwen' and (executor is not None or responses_url is not None):
        raise ConfigError('executor/Responses overrides require Qwen Codex')
    if channel == 'claude':
        model = 'opus' if model is None else model
        if model not in families:
            raise ConfigError('invalid choice for Claude model alias: ' + str(model))
        return {'channel': channel, 'requested_model': model, 'configured_model': model,
                'cli_model': model, 'pattern': families[model]}
    if channel == 'qoder':
        model = 'kmodel_latest' if model is None else model
        if model not in ('kimi-k3', 'kmodel_latest'):
            raise ConfigError('Qoder supports only kimi-k3 or kmodel_latest (Kimi-K3)')
        return {'channel': channel, 'requested_model': model, 'configured_model': 'Kimi-K3',
                'cli_model': 'kmodel_latest', 'executor': 'qoder', 'wire_api': 'qoder-cli',
                'pattern': re.compile(re.escape('Kimi-K3'))}
    if model is not None:
        raise ConfigError('Qwen rejects --model; configure plugins.qwen.model instead')
    model = single_line(lookup(config, 'plugins.qwen.model'), 'plugins.qwen.model')
    executor = executor if executor is not None else lookup(config, 'codex_relay.qwen_executor')
    executor = 'claude' if executor is None else executor
    if executor not in ('claude', 'codex'):
        raise ConfigError('codex_relay.qwen_executor must be claude or codex')
    if responses_url is not None and executor != 'codex':
        raise ConfigError('Responses override requires Qwen Codex')
    key = 'plugins.qwen.responses_base_url' if executor == 'codex' else 'plugins.qwen.base_url'
    url = single_line(responses_url if responses_url is not None else lookup(config, key), key)
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme in ('http', 'https') and bool(parsed.hostname) and parsed.port != 0
    except ValueError:
        valid = False
    if valid and executor == 'codex' and (parsed.username is not None or parsed.password is not None or '?' in url or '#' in url):
        valid = False
    if not valid or any(c.isspace() for c in url):
        raise ConfigError(key + ' must be a valid http(s) URL')
    paths = lookup(config, 'plugins.qwen.credential_paths')
    if not isinstance(paths, list) or not paths:
        raise ConfigError('plugins.qwen.credential_paths must be a non-empty path array')
    resolved = []
    for value in paths:
        path = Path(single_line(value, 'plugins.qwen.credential_paths entry')).expanduser()
        resolved.append(path if path.is_absolute() else repo / path)
    return {'channel': channel, 'requested_model': model, 'configured_model': model,
            'executor': executor, 'wire_api': 'responses' if executor == 'codex' else 'anthropic',
            'cli_model': model if executor == 'codex' else 'sonnet', 'pattern': re.compile(re.escape(model)),
            'base_url': url, 'credential_paths': resolved}


def evidence(channel):
    value = {key: channel[key] for key in ('channel', 'configured_model', 'requested_model', 'cli_model')}
    value.update(executor=channel.get('executor', 'claude'), wire_api=channel.get('wire_api', 'anthropic'))
    if value['executor'] == 'codex':
        value.update(actual_model=None, model_evidence='requested_only')
    return value


def child_environment(channel):
    if channel['channel'] == 'claude':
        return None
    if channel['channel'] == 'qoder':
        env = dict(os.environ)
        for key in list(env):
            if key.startswith(('ANTHROPIC_', 'CLAUDE_', 'OPENAI_', 'CODEX_')) or key == 'AI_ANTI_QWEN_TOKEN':
                env.pop(key)
        return env
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
    if channel.get('executor') == 'codex':
        for key in list(env):
            if (key.startswith(('CODEX_', 'OPENAI_', 'ANTHROPIC_', 'CLAUDE_')) and key != 'CODEX_HOME') or key == 'AI_ANTI_QWEN_TOKEN':
                env.pop(key)
        env['AI_ANTI_QWEN_TOKEN'] = token
        return env
    for key in CONFLICTS:
        env.pop(key, None)
    env.pop('ANTHROPIC_AUTH_TOKEN', None)
    env.update(ANTHROPIC_API_KEY=token, ANTHROPIC_BASE_URL=channel['base_url'])
    for family in ('OPUS', 'SONNET', 'HAIKU'):
        env['ANTHROPIC_DEFAULT_' + family + '_MODEL'] = channel['configured_model']
    return env
