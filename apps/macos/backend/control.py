#!/usr/bin/env python3
"""One request over stdin, one redacted JSON response over stdout."""
import argparse
import json
import signal
import sys
from config_store import Store, StoreError
import checks

def dispatch(store, request, source=None):
    if not isinstance(request, dict):
        raise StoreError('请求必须为对象')
    action = request.get('action')
    if action == 'inspect':
        return {'signature': store.read()['signature']}
    if action == 'load':
        return store.read()
    if action == 'save':
        return store.save(request['values'], request['fingerprints'], request.get('secret', ''))
    if action == 'restore':
        return store.restore(request['fingerprints'])
    if action == 'local':
        return checks.local(store, source)
    if action == 'real':
        return checks.real(store, source)
    raise StoreError('未知操作')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config-root')
    parser.add_argument('--source-root')
    args = parser.parse_args()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, checks.stop)
    try:
        raw = sys.stdin.buffer.read(1024*1024+1)
        if len(raw) > 1024*1024:
            raise StoreError('请求过大')
        data = dispatch(Store(args.config_root), json.loads(raw), args.source_root)
        reply = {'ok': True, 'data': data}
    except checks.CheckError as exc:
        reply = {'ok': False, 'error': str(exc), 'category': exc.category}
    except StoreError as exc:
        reply = {'ok': False, 'error': str(exc), 'category': 'configuration'}
    except Exception:
        # No exception text: it can contain credentials, endpoint or CLI output.
        reply = {'ok': False, 'error': '操作失败；敏感错误详情已隐藏，请检查权限和配置格式', 'category': 'internal'}
    print(json.dumps(reply, ensure_ascii=False))
    return 0 if reply['ok'] else 1

if __name__ == '__main__':
    sys.exit(main())
