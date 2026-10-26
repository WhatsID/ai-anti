"""Unauthenticated fixed-host observations; never permission escalation or model retry."""
import errno
import hashlib
import json
from pathlib import Path
import socket
import sys

from review_runner import execute, RunError

HOSTS = ('center.qoder.sh', 'api3.qoder.sh')
LIMIT = 65536


def probe():
    observations = []
    for host in HOSTS:
        item = {'host': host, 'dns': 'unknown', 'tcp': 'unknown'}
        try:
            addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            if not addresses:
                observations.append(item)
                continue
            item['dns'] = 'reachable'
            family, typ, proto, _, address = addresses[0]
            with socket.socket(family, typ, proto) as connection:
                connection.settimeout(2)
                connection.connect(address)
            item['tcp'] = 'reachable'
        except OSError as exc:
            item['errno'] = exc.errno
            # Timeouts alone cannot establish unavailable networking.
            item['dns' if item['dns'] != 'reachable' else 'tcp'] = (
                'unknown' if isinstance(exc, TimeoutError) else 'unavailable')
        observations.append(item)
    return {'hosts': observations}


def observation(payload):
    """Validate the helper contract before deriving an environment observation."""
    if not isinstance(payload, dict) or set(payload) != {'hosts'}:
        raise ValueError('invalid diagnostic envelope')
    hosts = payload['hosts']
    if not isinstance(hosts, list) or len(hosts) != len(HOSTS):
        raise ValueError('invalid diagnostic hosts')
    for host, item in zip(HOSTS, hosts):
        if (not isinstance(item, dict) or set(item) - {'host', 'dns', 'tcp', 'errno'}
                or item.get('host') != host
                or any(item.get(k) not in ('reachable', 'unavailable', 'unknown') for k in ('dns', 'tcp'))
                or ('errno' in item and item['errno'] is not None and type(item['errno']) is not int)):
            raise ValueError('invalid diagnostic observation')
    unavailable = any('unavailable' in (x['dns'], x['tcp']) for x in hosts)
    healthy = all(x['dns'] == x['tcp'] == 'reachable' for x in hosts)
    return {'status': 'environment_network_unavailable' if unavailable else 'reachable' if healthy else 'environment_unknown',
            'permission_denied': any(x.get('errno') in (errno.EACCES, errno.EPERM) for x in hosts),
            'hosts': hosts}


def diagnose(logfile, cwd, remaining):
    """One subprocess, capped at 10s including monitoring margin and stage remainder.

    Diagnostics cannot change or obscure the original validated model failure.
    No inherited credentials, prompts, material paths or configurable targets.
    """
    sidecar = Path(logfile).with_suffix('.network.json')
    raw = Path(logfile).with_suffix('.network.raw.json')
    result = {'status': 'environment_unknown', 'original_kind': 'ambiguous_model_error',
              'permission_denied': False}
    # execute monitors at 100ms and allows 150ms TERM + 3s cleanup.
    budget = min(6.5, remaining - 3.5)
    try:
        if budget <= 0:
            result.update(status='skipped', reason='stage_deadline')
        else:
            try:
                code = execute([sys.executable, str(Path(__file__).resolve())], cwd, raw,
                               budget, budget, LIMIT, env={})
                if code:
                    raise ValueError('diagnostic nonzero exit')
                result.update(observation(json.loads(raw.read_text('utf-8'))))
            except (RunError, OSError, ValueError, UnicodeError):
                result.update(status='environment_unknown', reason='diagnostic_failed')
            if raw.exists():
                result.update(raw_log=raw.name, raw_sha256=hashlib.sha256(raw.read_bytes()).hexdigest())
        sidecar.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        return dict(result, sidecar=sidecar.name, sha256=hashlib.sha256(sidecar.read_bytes()).hexdigest())
    except (OSError, ValueError):
        return {'status': 'environment_unknown', 'reason': 'diagnostic_evidence_unavailable',
                'original_kind': 'ambiguous_model_error', 'permission_denied': False}


if __name__ == '__main__':
    print(json.dumps(probe()))
