"""Pure public Token Plan account contract; no secret contents or I/O."""
import copy
from pathlib import PurePath

BACKUP_KEY = 'model_services.token_plan_backup.value'
BACKUP_KEYS = (BACKUP_KEY, 'review_services.token_plan_backup.value')

def normalize(value=None, credential_path=None, error=ValueError):
    if value is None:
        value = dict(version=1, auto_failover=False, backup=dict(credential_paths=[], credential_key=BACKUP_KEY))
    if (not isinstance(value, dict) or set(value) != {'version', 'auto_failover', 'backup'}
            or type(value.get('version')) is not int or value['version'] != 1
            or type(value.get('auto_failover')) is not bool):
        raise error('token_plan_accounts 版本或结构无效')
    backup = value['backup']
    if not isinstance(backup, dict) or set(backup) != {'credential_paths', 'credential_key'} or backup['credential_key'] not in BACKUP_KEYS:
        raise error('Token Plan 备用凭证槽无效')
    paths = backup['credential_paths']
    if not isinstance(paths, list): raise error('备用凭证路径列表无效')
    for path in paths:
        if not isinstance(path, str) or not path or any(c in path for c in '\r\n\x00') or '..' in PurePath(path).parts:
            raise error('备用凭证路径无效')
        if credential_path: credential_path(path)
    return copy.deepcopy(value)

def action(accounts, request, secrets, error=ValueError):
    if not isinstance(request, dict) or set(request) - {'action', 'value'}:
        raise error('备用凭证动作无效')
    kind, value = request.get('action', 'retain'), request.get('value', '')
    if kind not in ('retain','replace','clear') or not isinstance(value,str) or (kind != 'replace' and value):
        raise error('备用凭证动作无效')
    if kind == 'retain': return False
    if kind == 'replace' and (not value or any(c.isspace() for c in value) or '\x00' in value):
        raise error('备用密钥格式无效')
    old_root = secrets.get('review_services', {})
    if not isinstance(old_root, dict): raise error('密钥配置节无效')
    old_slot = old_root.get('token_plan_backup', {})
    if not isinstance(old_slot, dict): raise error('密钥配置节无效')
    old_slot.pop('value', None)
    if not old_slot: old_root.pop('token_plan_backup', None)
    if not old_root: secrets.pop('review_services', None)
    accounts['backup']['credential_key'] = BACKUP_KEY
    root = secrets.setdefault('model_services', {})
    if not isinstance(root,dict): raise error('密钥配置节无效')
    slot = root.setdefault('token_plan_backup', {})
    if not isinstance(slot,dict): raise error('密钥配置节无效')
    if kind == 'clear':
        slot.pop('value',None); accounts['backup']['credential_paths'] = []
    else:
        slot['value'] = value
        own = '~/.config/ai-anti/secrets.json'
        accounts['backup']['credential_paths'] = [own] + [p for p in accounts['backup']['credential_paths'] if p != own]
    return True

def ready(accounts, primary, error=ValueError):
    if accounts['auto_failover'] and (not accounts['backup']['credential_paths'] or not primary['credential_paths']):
        raise error('自动切换需要主账号与备用账号凭证路径')
