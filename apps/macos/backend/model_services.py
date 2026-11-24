"""Public service profiles and migration. No credential contents or file I/O."""
import copy

IDS = ('token_plan', 'custom_api')
KEYS = ('tokenplan.value',) + tuple(ns+'.'+service+'.value' for ns in ('model_services','review_services') for service in IDS)
FIELDS = ('model', 'base_url', 'responses_base_url', 'credential_paths', 'credential_key')
DEFAULTS = dict(base_url='https://token-plan.maas.qianwenaiapi.com/apps/anthropic', responses_base_url='https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1')

OWN_CREDENTIAL_PATH = '~/.config/ai-anti/secrets.json'
VENDOR_ENDPOINTS = {
    'qwen': 'https://maas.qianwenaiapi.com/compatible-mode/v1',
    'kimi': 'https://api.moonshot.cn/v1',
    'deepseek': 'https://api.deepseek.com',
}
REVIEW_MODELS = {
    'token_plan': ('qwen4-max', 'qwen3.8-max', 'deepseek-v4-pro', 'glm-5.3'),
    'qwen': ('qwen4-max',),
    'kimi': ('kimi-k3.1-pro', 'kimi-k3'),
    'deepseek': ('deepseek-v4-pro',),
}
DIRECT_MODELS = {
    'qwen': ('qwen4-max', 'qwen3.8-max'),
    'kimi': ('kimi-k3.1-pro', 'kimi-k3'),
    'deepseek': ('deepseek-v4-pro', 'deepseek-flash'),
}
VENDOR_KEYS = tuple(service + '.value' for service in VENDOR_ENDPOINTS)
OFFICIAL_CHANNEL = 'official_api'
IMPLEMENTATION_PROVIDERS = ('codex', 'token_plan', 'qoder', 'custom_api')


def selection(value, models, field, error):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'service', 'model'}:
        raise error(field + ' 必须为 service/model 对象，服务端点和凭证槽固定')
    service, model = value['service'], value['model']
    if not isinstance(service, str) or service not in models or model not in models[service]:
        raise error(field + ' 服务与模型不匹配')
    return dict(service=service, model=model)


def required_selection(value, models, field, error):
    chosen = selection(value, models, field, error)
    if chosen is None:
        raise error(field + ' 未配置')
    return chosen


def token_plan_destination(url, wire_api, error):
    if url != DEFAULTS['responses_base_url' if wire_api == 'responses' else 'base_url']:
        raise error('Token Plan 凭证需要对应协议的官方 HTTPS 端点')


def vendor_token(value):
    return isinstance(value, str) and bool(value) and all(33 <= ord(char) <= 126 for char in value)


def vendor_actions(requests, secrets, error):
    changed = False
    for service, request in requests.items():
        if not isinstance(request, dict) or set(request) - {'action', 'value'}:
            raise error('厂商凭证动作无效')
        action, value = request.get('action', 'retain'), request.get('value', '')
        if action not in ('retain', 'replace', 'clear') or not isinstance(value, str):
            raise error('厂商凭证动作无效')
        if action == 'replace':
            if not vendor_token(value):
                raise error('厂商密钥必须为非空可见 ASCII 字符')
        elif value:
            raise error('保留或清除不能包含新密钥')
        if action == 'retain':
            continue
        slot = secrets.get(service, {})
        if not isinstance(slot, dict):
            raise error('厂商凭证配置节无效')
        if action == 'replace':
            secrets.setdefault(service, {})['value'] = value
            changed = True
        elif 'value' in slot:
            del slot['value']
            if not slot:
                del secrets[service]
            changed = True
    return changed


def lookup(data, key):
    for part in key.split('.'):
        if not isinstance(data, dict): return None
        data = data.get(part)
    return data

def blank(service):
    return dict(model='qwen3.8-max' if service == 'token_plan' else '', base_url=DEFAULTS['base_url'] if service == 'token_plan' else '', responses_base_url=DEFAULTS['responses_base_url'] if service == 'token_plan' else '', credential_paths=[], credential_key='model_services.' + service + '.value')

def validate(data, endpoint, model, credential_path, error):
    if not isinstance(data, dict) or set(data) != {'version','active_api','profiles'} or type(data.get('version')) is not int or data['version'] != 1:
        raise error('model_services 版本或结构无效；请使用配套版本，未覆盖配置')
    if data['active_api'] not in IDS or not isinstance(data['profiles'], dict) or set(data['profiles']) != set(IDS):
        raise error('评审服务 ID 无效')
    data = copy.deepcopy(data)
    legacy = 0
    for service, p in data['profiles'].items():
        if not isinstance(p, dict) or set(p) != set(FIELDS): raise error('评审服务字段无效：' + service)
        model(p['model'], optional=True)
        for k in DEFAULTS: endpoint(p[k])
        if p['credential_key'] not in KEYS or p['credential_key'] not in ('tokenplan.value', 'model_services.'+service+'.value', 'review_services.'+service+'.value'):
            raise error('凭证键无效：' + service)
        legacy += p['credential_key'] == 'tokenplan.value'
        if not isinstance(p['credential_paths'], list) or not all(isinstance(x,str) for x in p['credential_paths']): raise error('凭证路径列表无效')
        for path in p['credential_paths']: credential_path(path)
    if legacy > 1: raise error('两个服务不能共享旧凭证槽')
    return data

def load(ai, qwen, endpoint, model, credential_path, error):
    present = [key for key in ('model_services', 'review_services') if key in ai]
    if present:
        validated = [validate(ai[key], endpoint, model, credential_path, error) for key in present]
        data = validated[0]
        if any(other != data for other in validated[1:]):
            raise error('model_services 与 review_services 配置冲突')
        expected = data['profiles'][data['active_api']]
        conflict = [k for k in FIELDS if qwen.get(k) != expected[k]]
        if conflict:
            raise error('评审服务投影冲突：' + ', '.join(conflict) + '；请按 model_services.active_api 对应 profile 同步 plugins.qwen 同名公开字段；不要删除配置或密钥')
        return data
    urls = [qwen.get(k, '') for k in DEFAULTS]
    known = any(urls) and all(not u or u == DEFAULTS[k] for k,u in zip(DEFAULTS, urls))
    active = 'token_plan' if known else 'custom_api'
    profiles = {k: blank(k) for k in IDS}
    profiles[active] = {k: copy.deepcopy(qwen.get(k, [] if k == 'credential_paths' else 'tokenplan.value' if k == 'credential_key' else '')) for k in FIELDS}
    if 'credential_paths' not in qwen: profiles[active]['credential_paths'] = ['~/.config/ai-anti/secrets.json']
    return validate(dict(version=1, active_api=active, profiles=profiles), endpoint, model, credential_path, error)

def actions(data, requests, secrets, error):
    if not isinstance(requests, dict) or any(k not in IDS for k in requests): raise error('凭证动作服务无效')
    changed = False
    for service, request in requests.items():
        if not isinstance(request, dict) or set(request) - {'action','value'}: raise error('凭证动作无效')
        action, value = request.get('action', 'retain'), request.get('value', '')
        if action not in ('retain','replace','clear') or not isinstance(value,str): raise error('凭证动作无效')
        if action != 'replace' and value: raise error('保留或清除不能包含新密钥')
        if action == 'retain': continue
        if action == 'replace' and (not value or value != value.strip() or any(c.isspace() for c in value)): raise error('新密钥格式无效')
        old_root = secrets.get('review_services', {})
        if not isinstance(old_root, dict): raise error('密钥配置节无效')
        old_slot = old_root.get(service, {})
        if not isinstance(old_slot, dict): raise error('密钥配置节无效')
        old_slot.pop('value', None)
        if not old_slot: old_root.pop(service, None)
        if not old_root: secrets.pop('review_services', None)
        root = secrets.setdefault('model_services', {})
        if not isinstance(root,dict): raise error('密钥配置节无效')
        slot = root.setdefault(service,{})
        if not isinstance(slot,dict): raise error('密钥配置节无效')
        p = data['profiles'][service]
        p['credential_key'] = 'model_services.'+service+'.value'
        if action == 'clear':
            slot.pop('value',None); p['credential_paths'] = []
        else:
            slot['value'] = value
            p['credential_paths'] = ['~/.config/ai-anti/secrets.json'] + [x for x in p['credential_paths'] if x != '~/.config/ai-anti/secrets.json']
        changed = True
    return changed

def migrate(data, accounts, secrets, resolve, own, error):
    """Move known private slots only when every referring path is owned.

    Mixed external/project paths retain their declared key: changing it would
    silently change fallback lookup semantics, even when the local value exists.
    """
    old = secrets.get('review_services', {})
    new = secrets.get('model_services', {})
    if not isinstance(old, dict) or not isinstance(new, dict): raise error('密钥配置节无效')
    changed = False
    slots = dict(data['profiles'], token_plan_backup=accounts['backup'])
    for service, profile in slots.items():
        legacy = 'review_services.' + service + '.value'
        canonical = 'model_services.' + service + '.value'
        paths = profile['credential_paths']
        source = old.get(service, {})
        target = new.get(service, {})
        if not isinstance(source,dict) or not isinstance(target,dict): raise error('密钥配置节无效')
        if 'value' in source and 'value' in target and source['value'] != target['value']:
            raise error('新旧服务凭证冲突；未覆盖密钥')
        if profile['credential_key'] not in (legacy,canonical) or any(resolve(p) != own for p in paths): continue
        if 'value' in source:
            new = secrets.setdefault('model_services', {})
            target = new.setdefault(service, {})
            target['value'] = source.pop('value')
            if not source: old.pop(service, None)
            changed = True
        profile['credential_key'] = canonical
    if 'review_services' in secrets and not old:
        secrets.pop('review_services'); changed = True
    return changed
