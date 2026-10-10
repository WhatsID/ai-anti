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

MODES = ('solo', 'required', 'auto', 'force')
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
    def raw(self, key):
        path = self.paths[key]
        safe_path(path)
        return path.read_bytes() if path.exists() else b''
    def read(self):
        ai_raw, claude_raw, codex_raw = (self.raw(k) for k in ('ai', 'claude', 'codex'))
        ai = object_json(ai_raw) if self.paths['ai'].exists() else {}
        claude = object_json(claude_raw) if self.paths['claude'].exists() else {}
        try:
            codex = tomllib.loads(codex_raw.decode()) if codex_raw else {}
        except (ValueError, UnicodeError):
            raise StoreError('Codex TOML 损坏') from None
        review, relay, plugins, desktop = (section(ai, k) for k in ('review', 'codex_relay', 'plugins', 'desktop'))
        qwen = section(plugins, 'qwen')
        mode_value = section(ai, 'relay').get('default_mode', 'required')
        if mode_value not in MODES:
            raise StoreError('全局协作模式无效')
        channels = review.get('channels', ['codex', 'same-family'])
        if not isinstance(channels, list) or not all(isinstance(x, str) for x in channels):
            raise StoreError('review.channels 必须为字符串列表')
        host = desktop.get('primary_host', 'claude')
        if host not in ('claude', 'codex'):
            raise StoreError('主导工具配置无效')
        primary = claude.get('model', '') if host == 'claude' else codex.get('model', '')
        values = dict(host=host, primary_model=primary, claude_model=claude.get('model', ''), codex_model=codex.get('model', ''),
                      channel=(channels[0] if channels else 'codex') if host == 'claude' else relay.get('review_channel', 'claude'),
                      reviewer_model=review.get('codex_model', ''), qwen_model=qwen.get('model', ''),
                      secondary_model=relay.get('secondary_model') or '', mode=mode_value, base_url=qwen.get('base_url', ''),
                      credential_paths=qwen.get('credential_paths', ['~/.config/ai-anti/secrets.json']), fallback_channels=channels[1:], claude_channel=(channels[0] if channels else 'codex'), codex_channel=relay.get('review_channel', 'claude'))
        for key in ('primary_model', 'claude_model', 'codex_model', 'reviewer_model', 'qwen_model', 'secondary_model', 'base_url'):
            if not isinstance(values[key], str):
                raise StoreError('配置字段类型无效：' + key)
        if values['channel'] not in ('claude', 'codex', 'qwen', 'same-family'):
            raise StoreError('评审通道配置无效')
        paths = values['credential_paths']
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise StoreError('凭证路径列表无效')
        for p in paths:
            path = self.credential_path(p)
            if path is not None:
                safe_path(path)
        endpoint = urlsplit(values['base_url'])
        if endpoint.username is not None or endpoint.password is not None or endpoint.query or endpoint.fragment:
            raise StoreError('已有服务地址含凭证或查询信息；请在原文件修复后刷新')
        resolved = [self.credential_path(p) for p in paths]
        values['relative_credentials'] = any(p is None for p in resolved)
        values['credential_present'] = any(p.is_file() for p in resolved if p is not None)
        return {'values': values, 'fingerprints': {k: fingerprint(p) for k,p in self.paths.items()},
                'signature': self.signature(values['credential_paths']), 'sources': {k: str(p) for k,p in self.paths.items() if k != 'secrets'},
                'can_restore': (self.desktop / 'last-save.json').is_file()}
    def credential_path(self, raw):
        if not isinstance(raw, str) or not raw:
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
    def credential_status(self, paths):
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
                token = object_json(path.read_bytes()).get('tokenplan', {}).get('value')
                if isinstance(token, str) and token.strip():
                    return True, path
            except (StoreError, AttributeError):
                continue
        return False, None
    def signature(self, paths):
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
        config_hashes = {k: fingerprint(p) for k,p in self.paths.items() if k != 'secrets'}
        return hashlib.sha256(encode([config_hashes, stamps])).hexdigest()
    def outputs(self, values, secret):
        host = values.get('host')
        if host not in ('claude', 'codex') or values.get('channel') not in (('codex', 'qwen') if host == 'claude' else ('qwen',)):
            raise StoreError('首期支持 Claude→Codex、Claude→Qwen、Codex→Qwen')
        if values.get('mode') not in MODES:
            raise StoreError('协作模式无效')
        for key in ('primary_model', 'secondary_model', 'reviewer_model', 'qwen_model'):
            model(values.get(key), optional=True)
        if values['channel'] == 'qwen':
            model(values['qwen_model'])
        url = values.get('base_url', '')
        parsed = urlsplit(url)
        if url and (parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment):
            raise StoreError('服务地址需为 HTTP(S) URL，不能包含凭证、查询参数或片段')
        if values['channel'] == 'qwen' and not url:
            raise StoreError('Qwen 服务地址不能为空')
        ai = object_json(self.raw('ai')) if self.paths['ai'].exists() else {}
        review, relay, qwen = section(ai, 'review'), section(ai, 'codex_relay'), section(section(ai, 'plugins'), 'qwen')
        channels = review.get('channels', ['codex', 'same-family'])
        if not isinstance(channels, list) or not all(isinstance(c,str) for c in channels):
            raise StoreError('评审通道列表无效')
        if host == 'claude':
            review['channels'] = [values['channel']] + [c for c in channels if c != values['channel']]
        else:
            relay['review_channel'] = values['channel']
        if values['reviewer_model']:
            review['codex_model'] = values['reviewer_model']
        else:
            review.pop('codex_model', None)
        relay['secondary_model'] = values['secondary_model'] or None
        section(ai, 'relay')['default_mode'] = values['mode']
        section(ai, 'desktop')['primary_host'] = host
        qwen['model'], qwen['base_url'] = values['qwen_model'], url
        paths = qwen.get('credential_paths', ['~/.config/ai-anti/secrets.json'])
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise StoreError('凭证路径列表无效')
        for p in paths:
            path = self.credential_path(p)
            if path is not None:
                safe_path(path)
        output = {}
        if secret:
            if not isinstance(secret, str) or secret != secret.strip() or any(c.isspace() for c in secret):
                raise StoreError('新密钥格式无效')
            secrets = object_json(self.raw('secrets')) if self.paths['secrets'].exists() else {}
            section(secrets, 'tokenplan')['value'] = secret
            output['secrets'] = encode(secrets)
            own = '~/.config/ai-anti/secrets.json'
            qwen['credential_paths'] = [own] + [p for p in paths if self.credential_path(p) != self.paths['secrets']]
        output['ai'] = encode(ai)
        primary = values['primary_model']
        if primary:
            if host == 'codex':
                output['codex'] = edit_toml(self.raw('codex'), primary)
            else:
                settings = object_json(self.raw('claude')) if self.paths['claude'].exists() else {}
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
            for key, path in self.paths.items():
                if key not in expected or fingerprint(path) != expected[key]:
                    raise StoreError('配置已被其他程序修改，请刷新后重试')
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
    def save(self, values, expected, secret=''):
        return self.transaction(self.outputs(values, secret), expected)
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
        return self.transaction(outputs, expected, restoring=True)
