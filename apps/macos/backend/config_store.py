"""Small, fail-closed configuration controller. Never returns credential contents."""
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib
from urllib.parse import urlsplit
import agy_relay
import model_services as services
import dual_plan
import implementation_models
import relay_routes
import token_plan_accounts as accounts

def token_plan_model(value):
    try:
        return implementation_models.parse(value)
    except ValueError as exc:
        raise StoreError(str(exc)) from exc


MODES = ('solo', 'auto', 'rules', 'force')
HOSTS = ('claude', 'codex', 'agy')
CODEX_MODELS = ('gpt-6.2-sol', 'gpt-6.2-astra', 'gpt-6.1-sol', 'gpt-6-astra', 'gpt-6-sol', 'gpt-6-luna')
FINGERPRINT_LABELS = {
    'ai': 'ai-anti 配置',
    'claude': 'Claude 设置',
    'codex': 'Codex 配置',
    'secrets': '凭证文件',
}

class StoreError(Exception):
    pass

def object_json(raw):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise StoreError('JSON 配置损坏，未覆盖原文件') from None
    if not isinstance(value, dict):
        raise StoreError('JSON 配置必须是对象')
    return value

def model(value, optional=False):
    if optional and value == '':
        return value
    if not isinstance(value, str) or not value or value != value.strip() or any(c.isspace() for c in value):
        raise StoreError('模型 ID 必须为非空且不含空白的字符串')
    return value

def endpoint(value):
    if not isinstance(value, str):
        raise StoreError('服务地址类型无效')
    try:
        parsed = urlsplit(value)
        valid = not value or (value == value.strip() and not any(c.isspace() for c in value) and parsed.scheme in ('https', 'http') and parsed.hostname and parsed.port != 0 and parsed.username is None and parsed.password is None and '?' not in value and '#' not in value)
    except ValueError:
        valid = False
    if not valid:
        raise StoreError('服务地址需为 HTTP(S) URL，不能包含凭证、查询参数或片段')
    return value

def implementation_provider(value):
    value = 'codex' if value is None else value
    if not isinstance(value, str) or value not in services.IMPLEMENTATION_PROVIDERS:
        raise StoreError('实施服务无效；合法值：codex/token_plan/qoder/custom_api')
    return value

def codex_services(relay):
    official = services.selection(relay.get('official_review'), services.REVIEW_MODELS,
                                  'codex_relay.official_review', StoreError)
    direct = services.selection(relay.get('direct_implementation'), services.DIRECT_MODELS,
                                'codex_relay.direct_implementation', StoreError)
    if relay.get('review_channel') == services.OFFICIAL_CHANNEL and official is None:
        raise StoreError('Codex 官方评审服务未配置')
    if implementation_provider(relay.get('implementation_provider')) == 'custom_api' and direct is None:
        raise StoreError('Codex 厂商直连实施服务未配置')
    return official, direct


def codex_destinations(relay, profiles, official):
    if (relay.get('review_channel') == services.OFFICIAL_CHANNEL and official['service'] == 'token_plan'
            or relay.get('implementation_provider') == 'token_plan'
            or relay.get('review_channel') == 'qwen' and profiles['active_api'] == 'token_plan'
            and relay.get('qwen_executor', 'claude') == 'codex'):
        url = profiles['profiles']['token_plan']['responses_base_url']
        if url or relay.get('review_channel') == services.OFFICIAL_CHANNEL:
            services.token_plan_destination(url, 'responses', StoreError)


def agy_destinations(ai, profiles):
    try:
        relay = agy_relay.parse(ai, StoreError)
    except StoreError:
        return  # Keep invalid AGY sections available for repair or byte-for-byte restore.
    if any(relay_routes.uses_token_plan(relay[role]) for role in relay_routes.ROLES):
        services.token_plan_destination(profiles['profiles']['token_plan']['responses_base_url'],
                                        'responses', StoreError)


def agy_plan_input(ai):
    relay = agy_relay.section(ai, StoreError) or {}
    return {'agy_relay': {'dual_plan': copy.deepcopy(relay['dual_plan'])}} if 'dual_plan' in relay else {}


def lookup(ai, dotted):
    for key in dotted.split('.'):
        if ai is None:
            return None
        if not isinstance(ai, dict):
            raise StoreError(dotted + ' 的上级配置节必须为对象')
        ai = ai.get(key)
    return ai

def claude_implementation(ai):
    value = lookup(ai, 'claude_relay.implementation')
    if value is None:
        return 'claude-agent', ''
    if not isinstance(value, dict) or value.get('provider') != 'codex':
        raise StoreError('claude_relay.implementation 必须为 provider=codex 的对象')
    return 'codex', model(value.get('model'))

def claude_qwen_executor(ai):
    new_key = 'claude_relay.qwen_executor'
    old_key = 'plugins.qwen.claude_executor'
    new_value = lookup(ai, new_key)
    old_value = lookup(ai, old_key)
    for key, value in ((new_key, new_value), (old_key, old_value)):
        if value is not None and (not isinstance(value, str) or value not in ('claude', 'codex')):
            raise StoreError(key + ' 无效；合法值：claude/codex')
    if new_value is not None and old_value is not None and new_value != old_value:
        raise StoreError(new_key + ' 与旧键 ' + old_key + ' 取值冲突')
    if new_value is not None:
        return new_value
    if old_value is not None:
        return old_value
    return 'claude'

def executor_for(host, values, ai):
    if host == 'codex':
        executor = values.get('qwen_executor', ai.get('codex_relay', {}).get('qwen_executor', 'claude'))
    else:
        executor = values.get('claude_qwen_executor', claude_qwen_executor(ai))
    if executor not in ('claude', 'codex'):
        raise StoreError('API 评审执行器无效')
    return executor

def effective_executor(values):
    return executor_for(values['host'], values, {})

def section(value, key):
    result = value.setdefault(key, {})
    if not isinstance(result, dict):
        raise StoreError('配置节必须为对象：' + key)
    return result

def safe_path(path):
    # Resolve nothing: a symlink must never redirect a write or backup read.
    for part in (path, *path.parents):
        if part.is_symlink():
            raise StoreError('配置目标或父目录是符号链接，已停止')
    if path.exists() and not path.is_file():
        raise StoreError('配置目标不是普通文件')

def fingerprint(path):
    safe_path(path)
    if not path.exists():
        return None
    if path.name == 'secrets.json':
        st = path.stat()
        return 'stat:' + ':'.join(str(x) for x in (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns))
    return hashlib.sha256(path.read_bytes()).hexdigest()

def atomic(path, raw, permissions=0o600):
    safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    safe_path(path)
    fd, name = tempfile.mkstemp(prefix='.ai-anti-', dir=path.parent)
    try:
        os.fchmod(fd, permissions)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()

def edit_toml(raw, desired):
    try:
        before = tomllib.loads(raw.decode())
    except (ValueError, UnicodeError):
        raise StoreError('Codex TOML 损坏，未覆盖') from None
    lines = raw.decode().splitlines(keepends=True)
    top = next((i for i, line in enumerate(lines) if line.lstrip().startswith('[')), len(lines))
    candidates = [i for i in range(top) if re.match(r'^\s*(?:model|"model"|\'model\')\s*=', lines[i])]
    encoded = json.dumps(desired, ensure_ascii=False)
    if 'model' in before:
        if len(candidates) != 1 or not isinstance(before['model'], str):
            raise StoreError('复杂顶层 model 写法不支持；请手工编辑后刷新')
        i = candidates[0]
        # Only a single-line scalar; reject multiline and line continuation.
        match = re.fullmatch(r'(\s*(?:model|"model"|\'model\')\s*=\s*)("(?:[^"\\\n]|\\.)*"|\'[^\'\n]*\')(\s*(?:#[^\n]*)?)(\r?\n)?', lines[i])
        if not match:
            raise StoreError('复杂顶层 model 写法不支持；请手工编辑后刷新')
        lines[i] = match[1] + encoded + match[3] + (match[4] or '\n')
    else:
        lines.insert(0, 'model = ' + encoded + '\n')
    output = ''.join(lines).encode()
    try:
        after = tomllib.loads(output.decode())
    except ValueError:
        raise StoreError('TOML 写入校验失败') from None
    expected = copy.deepcopy(before)
    expected['model'] = desired
    if after != expected:
        raise StoreError('TOML 语义发生意外变化')
    return output

class Store:
    def __init__(self, root=None):
        self.isolated = root is not None
        self.root = Path(root).absolute() if root else Path.home()
        self.ai = self.root / '.config/ai-anti'
        self.desktop = self.ai / 'desktop'
        self.paths = {
            'ai': self.ai / 'config.json',
            'claude': (self.root / '.claude' if root else Path(os.environ.get('CLAUDE_CONFIG_DIR') or str(self.root / '.claude')).expanduser()) / 'settings.json',
            'codex': (self.root / '.codex' if root else Path(os.environ.get('CODEX_HOME') or str(self.root / '.codex')).expanduser()) / 'config.toml',
            'secrets': self.ai / 'secrets.json',
        }
    def _read_optional(self, key):
        path = self.paths[key]
        safe_path(path)
        return path.read_bytes() if path.exists() else None
    def raw(self, key):
        return self._read_optional(key) or b''
    def read(self):
        raw = {k: self._read_optional(k) for k in ('ai', 'claude', 'codex')}
        config_hashes = {k: hashlib.sha256(data).hexdigest() if data is not None else None
                         for k, data in raw.items()}
        ai = object_json(raw['ai']) if raw['ai'] is not None else {}
        claude = object_json(raw['claude']) if raw['claude'] is not None else {}
        codex_raw = raw['codex']
        try:
            codex = tomllib.loads(codex_raw.decode()) if codex_raw else {}
        except (ValueError, UnicodeError):
            raise StoreError('Codex TOML 损坏') from None
        review, relay, plugins, desktop = (section(ai, k) for k in ('review', 'codex_relay', 'plugins', 'desktop'))
        qwen = section(plugins, 'qwen')
        mode_value = section(ai, 'relay').get('default_mode', 'rules')
        if mode_value not in MODES:
            raise StoreError('全局协作模式无效；合法值：' + '/'.join(MODES))
        channels = review.get('channels', ['codex', 'same-family'])
        if not isinstance(channels, list) or not all(isinstance(x, str) for x in channels):
            raise StoreError('review.channels 必须为字符串列表')
        host = desktop.get('primary_host', 'claude')
        if host not in HOSTS:
            raise StoreError('主模型配置无效')
        primary = claude.get('model', '') if host == 'claude' else codex.get('model', '') if host == 'codex' else ''
        channel = (channels[0] if channels else 'codex') if host == 'claude' else relay.get('review_channel', 'claude') if host == 'codex' else 'codex'
        claude_provider, claude_model = claude_implementation(ai)
        official, direct = codex_services(relay)
        values = dict(host=host, primary_model=primary, claude_model=claude.get('model', ''), codex_model=codex.get('model', ''),
                      channel=channel, official_review=official, direct_implementation=direct,
                      reviewer_model=review.get('codex_model', ''), qwen_model=qwen.get('model', ''),
                      token_plan_implementation_model=token_plan_model(relay.get('token_plan_implementation_model')), secondary_model=relay.get('secondary_model') or '', implementation_provider=implementation_provider(relay.get('implementation_provider')), mode=mode_value, base_url=qwen.get('base_url', ''),
                      responses_base_url=qwen.get('responses_base_url', ''), qwen_executor=relay.get('qwen_executor', 'claude'),
                      claude_implementation_provider=claude_provider, claude_implementation_model=claude_model,
                      claude_qwen_executor=claude_qwen_executor(ai), codex_models=list(CODEX_MODELS),
                      credential_paths=qwen.get('credential_paths', ['~/.config/ai-anti/secrets.json']), fallback_channels=channels[1:], claude_channel=(channels[0] if channels else 'codex'), codex_channel=relay.get('review_channel', 'claude'))
        plan, plan_error = None, ''
        try:
            plan = dual_plan.parse(ai, StoreError)
        except StoreError as exc:
            plan_error = str(exc)
        values['dual_plan'] = plan or dict(dual_plan.DEFAULTS)
        values['dual_plan_enabled'] = values['dual_plan']['enabled']
        values['dual_plan_error'] = plan_error
        agy_plan, agy_plan_error = None, ''
        try:
            agy_plan = dual_plan.parse_agy(agy_plan_input(ai), StoreError)
        except StoreError as exc:
            agy_plan_error = str(exc)
        values['agy_dual_plan'] = agy_plan or dict(dual_plan.AGY_DEFAULTS)
        values['agy_dual_plan_enabled'] = values['agy_dual_plan']['enabled']
        values['agy_dual_plan_error'] = agy_plan_error
        agy_error = ''
        try:
            agy_cfg = agy_relay.parse(ai, StoreError)
        except StoreError as exc:
            agy_error = str(exc)
            agy_cfg = {'review': dict(agy_relay.REVIEW_DEFAULT), 'implementation': None, 'codex_defaults': {}}
        values['agy_relay'] = agy_cfg
        for role in relay_routes.ROLES:
            route = agy_cfg[role]
            if host == 'agy':
                values['agy_' + role + '_route'] = route
            values['agy_' + role + '_model'] = (route.get('model') or '') if route and not relay_routes.is_official(route) else ''
        if host == 'agy':
            values['channel'] = agy_cfg['review']['provider']
        values['agy_relay_error'] = agy_error
        for key in ('primary_model', 'claude_model', 'codex_model', 'reviewer_model', 'qwen_model', 'secondary_model', 'base_url'):
            if not isinstance(values[key], str):
                raise StoreError('配置字段类型无效：' + key)
        supported = ('claude', 'codex', 'qwen', 'qoder', 'same-family')
        if host in ('codex', 'agy'):
            supported += (services.OFFICIAL_CHANNEL,)
        if values['channel'] not in supported:
            raise StoreError('评审通道配置无效')
        paths = values['credential_paths']
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise StoreError('凭证路径列表无效')
        for p in paths:
            path = self.credential_path(p)
            if path is not None:
                safe_path(path)
        if values['qwen_executor'] not in ('claude', 'codex'):
            raise StoreError('API 评审执行器无效')
        for key in ('base_url', 'responses_base_url'):
            endpoint(values[key])
        profiles = services.load(ai, qwen, endpoint, model, self.credential_path, StoreError)
        values['model_services'] = profiles
        values['token_plan_accounts'] = accounts.normalize(ai.get('token_plan_accounts'), self.credential_path, StoreError)
        values['credential_key'] = profiles['profiles'][profiles['active_api']]['credential_key']
        resolved = [self.credential_path(p) for p in paths]
        values['relative_credentials'] = any(p is None for p in resolved)
        values['credential_present'] = any(p.is_file() for p in resolved if p is not None)
        credential_paths = [p for profile in profiles['profiles'].values() for p in profile['credential_paths']]
        credential_paths += values['token_plan_accounts']['backup']['credential_paths']
        credential_paths.append(services.OWN_CREDENTIAL_PATH)
        fingerprints = dict(config_hashes, secrets=fingerprint(self.paths['secrets']))
        return {'values': values, 'fingerprints': fingerprints,
                'signature': self.signature(credential_paths, config_hashes=config_hashes),
                'sources': {k: str(p) for k, p in self.paths.items() if k != 'secrets'},
                'can_restore': (self.desktop / 'last-save.json').is_file()}
    def credential_path(self, raw):
        if not isinstance(raw, str) or not raw or any(c in raw for c in '\r\n\x00'):
            raise StoreError('凭证路径无效')
        if '..' in Path(raw).parts:
            raise StoreError('凭证路径不能包含父目录跳转')
        if raw == '~' or raw.startswith('~/'):
            path = self.root / raw[2:] if raw != '~' else self.root
        else:
            path = Path(raw)
            if not path.is_absolute():
                return None  # Existing project-relative credentials have no desktop context.
        path = Path(os.path.abspath(path))
        if self.isolated and not path.is_relative_to(self.root):
            raise StoreError('fixture 模式禁止读取隔离根之外的凭证')
        safe_path(path)
        return path
    def credential_status(self, paths, credential_key='tokenplan.value'):
        if credential_key not in (*services.KEYS, *accounts.BACKUP_KEYS, *services.VENDOR_KEYS):
            raise StoreError('凭证键无效')
        vendor = credential_key in services.VENDOR_KEYS
        if vendor and paths != [services.OWN_CREDENTIAL_PATH]:
            raise StoreError('厂商凭证必须使用用户密钥文件')
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise StoreError('凭证查找路径必须为字符串列表')
        for raw in paths:
            path = self.credential_path(raw)
            if path is None:
                continue
            safe_path(path)
            if not path.is_file():
                continue
            try:
                token = services.lookup(object_json(path.read_bytes()), credential_key)
                valid = services.vendor_token(token) if vendor else isinstance(token, str) and token.strip()
                if valid:
                    return True, path
            except (StoreError, AttributeError):
                continue
        return False, None
    def signature(self, paths, config_hashes=None):
        # Configuration hashes + credential stat only. No secret content goes to UI.
        stamps = {}
        for raw in paths:
            path = self.credential_path(raw)
            if path is None:
                stamps['relative:' + raw] = 'not_checked_no_project_context'
                continue
            safe_path(path)
            try:
                s = path.stat()
                stamps[str(path)] = [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]
            except FileNotFoundError:
                stamps[str(path)] = None
        if config_hashes is None:
            config_hashes = {k: fingerprint(p) for k, p in self.paths.items() if k != 'secrets'}
        return hashlib.sha256(encode([config_hashes, stamps])).hexdigest()
    def outputs(self, values, secret, secret_actions=None):
        if secret_actions is not None and not isinstance(secret_actions, dict):
            raise StoreError('凭证动作必须为对象')
        host = values.get('host')
        if host not in HOSTS:
            raise StoreError('主模型无效')
        updates = values.get('host_updates', {})
        if not isinstance(updates, dict) or any(h not in ('claude', 'codex') for h in updates):
            raise StoreError('host_updates 主模型无效')
        updates = {h: dict(fields) if isinstance(fields, dict) else fields for h, fields in updates.items()}
        for h, fields in updates.items():
            if not isinstance(fields, dict) or any(k not in ('primary_model', 'channel') for k in fields):
                raise StoreError('host_updates 字段无效')
            if 'primary_model' in fields:
                model(fields['primary_model'], optional=True)
            if 'channel' in fields and fields['channel'] not in (('codex', 'qwen') if h == 'claude' else ('qwen', 'qoder', services.OFFICIAL_CHANNEL)):
                raise StoreError('支持 Claude→Codex、Claude→API、Codex→API/Qoder')
        if host == 'agy':
            if values.get('channel') not in relay_routes.AGY_REVIEW_PROVIDERS:
                raise StoreError('AGY 主模型仅支持 Codex 或官方服务评审')
            if values.get('primary_model', '') != '':
                raise StoreError('AGY 主模型 ID 由 AGY 设置管理，本应用不修改')
        else:
            current = updates.setdefault(host, {})
            for key in ('primary_model', 'channel'):
                if key in current and current[key] != values.get(key):
                    raise StoreError('host_updates 与当前主模型字段冲突')
            model(values.get('primary_model'), optional=True)
        if values.get('mode') not in MODES:
            raise StoreError('协作模式无效；合法值：' + '/'.join(MODES))
        for key in ('secondary_model', 'reviewer_model', 'qwen_model'):
            model(values.get(key), optional=True)
        ai = object_json(self.raw('ai')) if self.paths['ai'].exists() else {}
        if host == 'agy' and lookup(ai, 'desktop.primary_host') != 'agy':
            try:
                agy_relay.parse(ai, StoreError)
            except StoreError as exc:
                raise StoreError('AGY 接力配置无效，不能切换为 AGY 主模型') from exc
        if any(k in values for k in agy_relay.CHANGE_KEYS):
            agy_relay.apply(ai, {k: values[k] for k in agy_relay.CHANGE_KEYS if k in values}, StoreError)
        if host == 'agy' and 'agy_review_route' in values:
            if values['channel'] != agy_relay.parse_review(ai, StoreError)['provider']:
                raise StoreError('AGY 评审通道与 agy_review_route.provider 冲突')
        account_config = accounts.normalize(values.get('token_plan_accounts', ai.get('token_plan_accounts')), self.credential_path, StoreError)
        accounts.normalize(ai.get('token_plan_accounts'), self.credential_path, StoreError)
        review, relay, qwen = section(ai, 'review'), section(ai, 'codex_relay'), section(section(ai, 'plugins'), 'qwen')
        codex_services(relay)  # Validate stored selections before applying a changed draft.
        for key in ('official_review', 'direct_implementation'):
            if key in values:
                if values[key] is None:
                    relay.pop(key, None)
                else:
                    relay[key] = values[key]
        profiles = None
        if any(k in obj for k in ('model_services','review_services') for obj in (values, ai)):
            # Validate stored source/projection before accepting a new draft.
            stored_profiles = services.load(ai, qwen, endpoint, model, self.credential_path, StoreError)
            drafts = [services.validate(values[k], endpoint, model, self.credential_path, StoreError) for k in ('model_services','review_services') if k in values]
            if len(drafts) == 2 and drafts[0] != drafts[1]: raise StoreError('服务配置别名冲突')
            profiles = drafts[0] if drafts else stored_profiles
            active_profile = profiles['profiles'][profiles['active_api']]
            if secret_actions is None and (not drafts or profiles == stored_profiles):
                # Old clients edit the historical public fields.
                for public, old in (('model','qwen_model'), ('base_url','base_url'), ('responses_base_url','responses_base_url')):
                    if old in values: active_profile[public] = values[old]
                profiles = services.validate(profiles, endpoint, model, self.credential_path, StoreError)
                active_profile = profiles['profiles'][profiles['active_api']]
            values = dict(values, qwen_model=active_profile['model'], base_url=active_profile['base_url'], responses_base_url=active_profile['responses_base_url'])
        # Legacy executor fields still have to be valid even though saves fix both routes.
        executor_for('codex', values, ai)
        executor_for('claude', values, ai)
        stored_provider, stored_model = claude_implementation(ai)
        claude_provider = values.get('claude_implementation_provider', stored_provider)
        if claude_provider not in ('claude-agent', 'codex'):
            raise StoreError('Claude 实施服务无效；合法值：claude-agent/codex')
        if claude_provider == 'codex':
            chosen_model = model(values.get('claude_implementation_model', stored_model))
            if stored_provider != 'codex':
                if ai.get('claude_relay') is None:
                    ai['claude_relay'] = {}
                section(ai, 'claude_relay')['implementation'] = {'provider': 'codex', 'model': chosen_model}
            elif chosen_model != stored_model:
                ai['claude_relay']['implementation']['model'] = chosen_model
        elif 'claude_implementation_provider' in values and isinstance(ai.get('claude_relay'), dict):
            ai['claude_relay'].pop('implementation', None)
        if 'dual_plan_enabled' in values:
            dual_plan.apply_enabled(ai, values['dual_plan_enabled'], StoreError)
        if 'agy_dual_plan_enabled' in values:
            plan_input = agy_plan_input(ai)
            dual_plan.apply_agy_enabled(plan_input, values['agy_dual_plan_enabled'], StoreError)
            if 'agy_relay' in plan_input:
                if ai.get('agy_relay') is None:
                    ai['agy_relay'] = {}
                ai['agy_relay']['dual_plan'] = plan_input['agy_relay']['dual_plan']
        urls = {key: endpoint(values.get(key, qwen.get(key, ''))) for key in ('base_url', 'responses_base_url')}
        channels = review.get('channels', ['codex', 'same-family'])
        if not isinstance(channels, list) or not all(isinstance(c,str) for c in channels):
            raise StoreError('评审通道列表无效')
        original_channels = {'claude': channels[0] if channels else 'codex', 'codex': relay.get('review_channel', 'claude')}
        if host != 'agy':
            if values.get('channel') != original_channels[host]:
                current['channel'] = values.get('channel')
            current['primary_model'] = values['primary_model']
        for h, fields in updates.items():
            route = fields.get('channel', original_channels[h])
            if 'channel' in fields and route not in (('codex', 'qwen') if h == 'claude' else ('qwen', 'qoder', services.OFFICIAL_CHANNEL)):
                raise StoreError('支持 Claude→Codex、Claude→API、Codex→API/Qoder')
            if 'channel' in fields and route != original_channels[h]:
                if h == 'claude':
                    review['channels'] = [route] + [c for c in channels if c != route]
                else:
                    relay['review_channel'] = route
        if not urls['responses_base_url'] and ('qwen' in review.get('channels', channels) or relay.get('review_channel') == 'qwen'):
            raise StoreError('Qwen 评审使用 Responses，请填写评审服务地址（Responses）')
        for h, fields in updates.items():
            if fields.get('channel', original_channels[h]) == 'qwen' and (h == host or 'channel' in fields):
                model(values['qwen_model'])
        if values['reviewer_model']:
            review['codex_model'] = values['reviewer_model']
        else:
            review.pop('codex_model', None)
        relay['implementation_provider'] = implementation_provider(values.get('implementation_provider', relay.get('implementation_provider')))
        token_plan_model(relay.get('token_plan_implementation_model'))
        relay['token_plan_implementation_model'] = token_plan_model(values.get('token_plan_implementation_model', relay.get('token_plan_implementation_model')))
        official, _ = codex_services(relay)
        relay['secondary_model'] = values['secondary_model'] or None
        section(ai, 'relay')['default_mode'] = values['mode']
        section(ai, 'desktop')['primary_host'] = host
        qwen['model'] = values['qwen_model']
        qwen.update(urls)
        relay['qwen_executor'] = 'codex'
        if ai.get('claude_relay') is None:
            ai['claude_relay'] = {}
        section(ai, 'claude_relay')['qwen_executor'] = 'codex'
        qwen.pop('claude_executor', None)
        paths = qwen.get('credential_paths', ['~/.config/ai-anti/secrets.json'])
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise StoreError('凭证路径列表无效')
        for p in paths:
            path = self.credential_path(p)
            if path is not None:
                safe_path(path)
        projected = profiles or services.load(ai, qwen, endpoint, model, self.credential_path, StoreError)
        codex_destinations(relay, projected, official)
        agy_destinations(ai, projected)
        output = {}
        legacy_secret = bool(secret) and not any(k in ai for k in ('model_services','review_services'))
        if legacy_secret:
            if not isinstance(secret, str) or secret != secret.strip() or any(c.isspace() for c in secret):
                raise StoreError('新密钥格式无效')
            secrets = object_json(self.raw('secrets')) if self.paths['secrets'].exists() else {}
            section(secrets, 'tokenplan')['value'] = secret
            output['secrets'] = encode(secrets)
            own = '~/.config/ai-anti/secrets.json'
            qwen['credential_paths'] = [own] + [p for p in paths if self.credential_path(p) != self.paths['secrets']]
        requests = dict(secret_actions or {})
        vendor_requests = {name: requests.pop(name) for name in services.VENDOR_ENDPOINTS if name in requests}
        if profiles is not None:
            if secret and secret_actions is not None:
                raise StoreError('服务配置请使用独立凭证动作')
            if legacy_secret:
                active_profile['credential_paths'] = qwen['credential_paths']
                active_profile['credential_key'] = 'tokenplan.value'
            secrets = object_json(output['secrets']) if 'secrets' in output else (object_json(self.raw('secrets')) if self.paths['secrets'].exists() else {})
            migrated = services.migrate(profiles, account_config, secrets, self.credential_path, self.paths['secrets'], StoreError)
            if migrated or self.paths['secrets'].exists(): output['secrets'] = encode(secrets)
            if secret and not legacy_secret: requests = {profiles['active_api']: {'action':'replace','value':secret}}
            if requests:
                requests = dict(requests)
                backup_request = requests.pop('token_plan_backup', {'action':'retain'})
                changed = services.actions(profiles, requests, secrets, StoreError)
                changed = accounts.action(account_config, backup_request, secrets, StoreError) or changed
                if changed: output['secrets'] = encode(secrets)
            profiles = services.validate(profiles, endpoint, model, self.credential_path, StoreError)
            ai.pop('review_services', None)
            ai['model_services'] = profiles
            qwen.update(copy.deepcopy(profiles['profiles'][profiles['active_api']]))
        elif requests:
            raise StoreError('凭证动作需要服务配置')
        if vendor_requests:
            secrets = object_json(output['secrets']) if 'secrets' in output else (object_json(self.raw('secrets')) if self.paths['secrets'].exists() else {})
            if services.vendor_actions(vendor_requests, secrets, StoreError):
                output['secrets'] = encode(secrets)
        if account_config['auto_failover']:
            token_profiles = profiles or services.load(ai, qwen, endpoint, model, self.credential_path, StoreError)
            primary_slot = token_profiles['profiles']['token_plan']
            accounts.ready(account_config, primary_slot, StoreError)
            pending_secrets = object_json(output['secrets']) if 'secrets' in output else None
            resolved_tokens = []
            for slot in (primary_slot, account_config['backup']):
                token = None
                for raw in slot['credential_paths']:
                    path = self.credential_path(raw)
                    if path is None: continue
                    try:
                        data = pending_secrets if path == self.paths['secrets'] and pending_secrets is not None else object_json(path.read_bytes())
                        found = services.lookup(data, slot['credential_key'])
                    except (OSError, StoreError): continue
                    if isinstance(found,str) and found and not any(c.isspace() for c in found) and '\x00' not in found:
                        token = found; break
                if token is None: raise StoreError('自动切换需要两个有效账号凭证；项目相对路径请由 CLI 验证')
                resolved_tokens.append(token)
            if resolved_tokens[0] == resolved_tokens[1]: raise StoreError('主账号与备用账号必须使用不同凭证')
        ai['token_plan_accounts'] = account_config
        output['ai'] = encode(ai)
        for h, fields in updates.items():
            primary = fields.get('primary_model', '')
            if not primary:
                continue  # Empty means retain the existing default.
            if h == 'codex':
                raw = self.raw('codex')
                try:
                    existing = tomllib.loads(raw.decode()).get('model', '') if raw else ''
                except (ValueError, UnicodeError):
                    raise StoreError('Codex TOML 损坏，未覆盖') from None
                if primary != existing:
                    output['codex'] = edit_toml(raw, primary)
            else:
                settings = object_json(self.raw('claude')) if self.paths['claude'].exists() else {}
                if primary != settings.get('model', ''):
                    settings['model'] = primary
                    output['claude'] = encode(settings)
        return output
    def transaction(self, outputs, expected, restoring=False):
        safe_path(self.desktop / 'lock')
        self.desktop.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.desktop, 0o700)
        with open(self.desktop / 'lock', 'a+b') as lock:
            os.chmod(self.desktop / 'lock', 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            changed = [key for key, path in self.paths.items()
                       if key not in expected or fingerprint(path) != expected[key]]
            if changed:
                labels = '、'.join(FINGERPRINT_LABELS[key] for key in changed)
                raise StoreError(f'配置已被其他程序修改（{labels}），请刷新后重试')
            old = {k: self.raw(k) if self.paths[k].exists() else None for k in outputs}
            old_permissions = {k: self.paths[k].stat().st_mode & 0o777 if self.paths[k].exists() else 0o600 for k in outputs}
            backup = self.desktop / 'last-save.json'
            safe_path(backup)
            previous_backup = backup.read_bytes() if backup.exists() else None
            # Persist recovery record before the first target mutation; only known target keys.
            record = {'before': {k: (v.hex() if v is not None else None) for k,v in old.items()},
                      'after': {},
                      'permissions': old_permissions}
            if not restoring:
                atomic(backup, encode(record))
            written = []
            try:
                for key, raw in outputs.items():
                    if fingerprint(self.paths[key]) != expected[key]:
                        raise StoreError('保存期间检测到外部修改')
                    if raw is None:
                        self.paths[key].unlink(missing_ok=True)
                    else:
                        atomic(self.paths[key], raw, 0o600 if key == 'secrets' else old_permissions[key])
                    written.append(key)
                for key, raw in outputs.items():
                    digest = hashlib.sha256(raw).hexdigest() if raw is not None else None
                    if (hashlib.sha256(self.raw(key)).hexdigest() if self.paths[key].exists() else None) != digest:
                        raise StoreError('保存后的配置回读不一致')
                if not restoring:
                    record['after'] = {k: fingerprint(self.paths[k]) for k in outputs}
                    atomic(backup, encode(record))
            except Exception:
                partial = False
                for key in reversed(written):
                    current = hashlib.sha256(outputs[key]).hexdigest() if outputs[key] is not None else None
                    if (hashlib.sha256(self.raw(key)).hexdigest() if self.paths[key].exists() else None) != current:
                        partial = True
                        continue
                    if old[key] is None:
                        self.paths[key].unlink(missing_ok=True)
                    else:
                        try:
                            atomic(self.paths[key], old[key], old_permissions[key])
                        except Exception:
                            partial = True
                if not partial and not restoring:
                    if previous_backup is None:
                        backup.unlink(missing_ok=True)
                    else:
                        atomic(backup, previous_backup)
                raise StoreError('保存失败；部分状态需人工检查，未覆盖外部修改' if partial else '保存失败，已回滚本次写入') from None
            if restoring:
                backup.unlink(missing_ok=True)
        return self.read()
    def save(self, values, expected, secret='', secret_actions=None):
        return self.transaction(self.outputs(values, secret, secret_actions), expected)
    def restore(self, expected):
        backup = self.desktop / 'last-save.json'
        safe_path(backup)
        if not backup.is_file():
            raise StoreError('没有可恢复的保存记录')
        record = object_json(backup.read_bytes())
        before, after = record.get('before'), record.get('after')
        if not isinstance(before,dict) or not isinstance(after,dict) or set(before) != set(after) or not set(before) <= set(self.paths):
            raise StoreError('恢复记录损坏')
        for key,digest in after.items():
            if fingerprint(self.paths[key]) != digest:
                raise StoreError('保存后存在外部修改，不能恢复')
        try:
            outputs = {k: bytes.fromhex(v) if v is not None else None for k,v in before.items()}
        except (ValueError, TypeError):
            raise StoreError('恢复记录损坏') from None
        if outputs.get('ai') is not None:
            restored_ai = object_json(outputs['ai'])
            restored_host = section(restored_ai, 'desktop').get('primary_host', 'claude')
            if restored_host not in HOSTS:
                raise StoreError('恢复记录主模型无效；请先完成一次新保存以刷新恢复点')
            claude_implementation(restored_ai)
            claude_qwen_executor(restored_ai)
            accounts.normalize(restored_ai.get('token_plan_accounts'), self.credential_path, StoreError)
            restored_relay = section(restored_ai, 'codex_relay')
            restored_official, _ = codex_services(restored_relay)
            token_plan_model(restored_relay.get('token_plan_implementation_model'))
            restored_executor = restored_relay.get('qwen_executor', 'claude')
            if restored_executor not in ('claude', 'codex'):
                raise StoreError('恢复记录 API 评审执行器无效；请先完成一次新保存以刷新恢复点')
            restored_qwen = section(section(restored_ai, 'plugins'), 'qwen')
            restored_profiles = services.load(restored_ai, restored_qwen, endpoint, model, self.credential_path, StoreError)
            if restored_relay.get('review_channel') == services.OFFICIAL_CHANNEL:
                codex_destinations(restored_relay, restored_profiles, restored_official)
            agy_destinations(restored_ai, restored_profiles)
            accounts.ready(accounts.normalize(restored_ai.get('token_plan_accounts'), self.credential_path, StoreError), restored_profiles['profiles']['token_plan'], StoreError)
            for key in ('base_url', 'responses_base_url'):
                endpoint(restored_qwen.get(key, ''))
            restored_mode = section(restored_ai, 'relay').get('default_mode', 'rules')
            if restored_mode not in MODES:
                raise StoreError('恢复记录协作模式无效；合法值：' + '/'.join(MODES) + '；请先完成一次新保存以刷新恢复点')
        return self.transaction(outputs, expected, restoring=True)
