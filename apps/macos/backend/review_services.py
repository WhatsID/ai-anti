"""Public service profiles and migration. No credential contents or file I/O."""
import copy

IDS = ('token_plan', 'custom_api')
KEYS = ('tokenplan.value', 'review_services.token_plan.value', 'review_services.custom_api.value')
FIELDS = ('model', 'base_url', 'responses_base_url', 'credential_paths', 'credential_key')
DEFAULTS = dict(base_url='https://token-plan.maas.qianwenaiapi.com/apps/anthropic', responses_base_url='https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1')

def lookup(data, key):
    for part in key.split('.'):
        if not isinstance(data, dict): return None
        data = data.get(part)
    return data

def blank(service):
    return dict(model='qwen3.8-max' if service == 'token_plan' else '', base_url=DEFAULTS['base_url'] if service == 'token_plan' else '', responses_base_url=DEFAULTS['responses_base_url'] if service == 'token_plan' else '', credential_paths=[], credential_key='review_services.' + service + '.value')

def validate(data, endpoint, model, credential_path, error):
    if not isinstance(data, dict) or set(data) != {'version','active_api','profiles'} or type(data.get('version')) is not int or data['version'] != 1:
        raise error('review_services 版本或结构无效；请使用配套版本，未覆盖配置')
    if data['active_api'] not in IDS or not isinstance(data['profiles'], dict) or set(data['profiles']) != set(IDS):
        raise error('评审服务 ID 无效')
    data = copy.deepcopy(data)
    legacy = 0
    for service, p in data['profiles'].items():
        if not isinstance(p, dict) or set(p) != set(FIELDS): raise error('评审服务字段无效：' + service)
        model(p['model'], optional=True)
        for k in DEFAULTS: endpoint(p[k])
        if p['credential_key'] not in KEYS or p['credential_key'] not in ('tokenplan.value', 'review_services.'+service+'.value'):
            raise error('凭证键无效：' + service)
        legacy += p['credential_key'] == 'tokenplan.value'
        if not isinstance(p['credential_paths'], list) or not all(isinstance(x,str) for x in p['credential_paths']): raise error('凭证路径列表无效')
        for path in p['credential_paths']: credential_path(path)
    if legacy > 1: raise error('两个服务不能共享旧凭证槽')
    return data

def load(ai, qwen, endpoint, model, credential_path, error):
    if 'review_services' in ai:
        data = validate(ai['review_services'], endpoint, model, credential_path, error)
        expected = data['profiles'][data['active_api']]
        conflict = [k for k in FIELDS if qwen.get(k) != expected[k]]
        if conflict:
            raise error('评审服务投影冲突：' + ', '.join(conflict) + '；请按 review_services.active_api 对应 profile 同步 plugins.qwen 同名公开字段；不要删除配置或密钥')
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
        root = secrets.setdefault('review_services', {})
        if not isinstance(root,dict): raise error('密钥配置节无效')
        slot = root.setdefault(service,{})
        if not isinstance(slot,dict): raise error('密钥配置节无效')
        p = data['profiles'][service]
        p['credential_key'] = 'review_services.'+service+'.value'
        if action == 'clear':
            slot.pop('value',None); p['credential_paths'] = []
        else:
            slot['value'] = value
            p['credential_paths'] = ['~/.config/ai-anti/secrets.json'] + [x for x in p['credential_paths'] if x != '~/.config/ai-anti/secrets.json']
        changed = True
    return changed
