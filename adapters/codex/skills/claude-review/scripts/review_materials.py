"""Frozen ID-only material transport and stdio MCP; no repository traversal."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import stat

from review_chunks import MAX_BLOCK_BYTES, required_files
from review_material import MaterialError, MAX_STAGE_BYTES

TOOL = 'mcp__materials__read_material'
MAX_REGISTRY_BYTES = 2 * 1024 * 1024


def read_material(registry, material_id):
    if not isinstance(material_id, str) or material_id not in registry['materials']:
        raise MaterialError('unknown material id')
    item = registry['materials'][material_id]
    root = Path(registry['root'])
    path = root / item['path']
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise MaterialError('symlink material forbidden')
    if path.resolve().parent != path.parent.resolve():
        raise MaterialError('unsafe material path')
    path.resolve().relative_to(root)
    fd = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise MaterialError('material must be a regular file')
        data = handle.read(MAX_BLOCK_BYTES + 1)
    if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
        raise MaterialError('material size/hash mismatch')
    return data.decode('utf-8')


def load_registry(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > MAX_REGISTRY_BYTES:
        raise MaterialError('unsafe registry')
    value = json.loads(path.read_text('utf-8'))
    if not isinstance(value, dict) or set(value) != {'version', 'root', 'materials'} or type(value['version']) is not int or value['version'] != 1:
        raise MaterialError('invalid registry structure')
    root = Path(value['root']) if isinstance(value['root'], str) else Path('.')
    if not root.is_absolute() or not root.is_dir() or any(p.is_symlink() for p in (root, *root.parents)):
        raise MaterialError('invalid material root')
    if not isinstance(value['materials'], dict) or not value['materials']:
        raise MaterialError('empty material registry')
    paths, total = set(), 0
    for index, (mid, item) in enumerate(value['materials'].items(), 1):
        if mid != '%03d' % index or not isinstance(item, dict) or set(item) != {'path', 'bytes', 'sha256'}:
            raise MaterialError('invalid registry entry')
        name = item['path']
        if (not isinstance(name, str) or not re.fullmatch(r'chunks/[0-9]{3,}/[0-9]{3,}\.txt', name)
                or name in paths or type(item['bytes']) is not int or not 0 <= item['bytes'] <= MAX_BLOCK_BYTES
                or not isinstance(item['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', item['sha256'])):
            raise MaterialError('invalid material metadata')
        paths.add(name)
        total += item['bytes']
    if total > MAX_STAGE_BYTES:
        raise MaterialError('material registry exceeds stage limit')
    for mid in value['materials']:
        read_material(value, mid)
    return value


def build_registry(stage, sources, path):
    materials = {}
    for source in sources.values():
        for block in source['blocks']:
            materials['%03d' % (len(materials) + 1)] = {key: block[key] for key in ('path', 'bytes', 'sha256')}
    if [v['path'] for v in materials.values()] != required_files(sources):
        raise MaterialError('registry source mismatch')
    value = {'version': 1, 'root': str(Path(stage).resolve()), 'materials': materials}
    path = Path(path)
    if path.resolve().is_relative_to(Path(stage).resolve()):
        raise MaterialError('registry must be outside materials')
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.chmod(path, 0o600)
    return load_registry(path)


def mcp_config(registry_path):
    # Explicitly erase credentials/endpoints inherited by the CLI's MCP child.
    keys = set(k for k in os.environ if k.startswith(('ANTHROPIC_', 'CLAUDE_CODE_USE_')))
    keys.update(('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL'))
    return {'mcpServers': {'materials': {'command': sys.executable,
        'args': [str(Path(__file__).resolve()), str(Path(registry_path).resolve())],
        'env': {key: '' for key in sorted(keys)}}}}


class MaterialAudit:
    def __init__(self, registry, cwd, required, error):
        self.registry, self.error = registry, error
        if Path(registry['root']).resolve() != cwd or set(required or []) != {v['path'] for v in registry['materials'].values()}:
            raise error('registry does not match stage requirements')
        self.pending, self.seen, self.results, self.complete = {}, set(), set(), set()

    def consume(self, event, blocks):
        for block in blocks:
            if block.get('type') == 'tool_use':
                uid, args = block.get('id'), block.get('input')
                if (event.get('type') != 'assistant' or not isinstance(uid, str) or not uid or uid in self.seen
                        or block.get('name') != TOOL or not isinstance(args, dict) or set(args) != {'id'}
                        or not isinstance(args['id'], str) or args['id'] not in self.registry['materials']):
                    raise self.error('invalid material tool invocation')
                self.seen.add(uid)
                self.pending[uid] = args['id']
            elif block.get('type') == 'tool_result':
                uid = block.get('tool_use_id')
                if (event.get('type') != 'user' or len(blocks) != 1 or not isinstance(uid, str)
                        or uid not in self.pending or uid in self.results or block.get('is_error', False) is not False):
                    raise self.error('invalid material tool result')
                mid = self.pending.pop(uid)
                try:
                    raw = read_material(self.registry, mid)
                    expected = [{'type': 'text', 'text': raw}]
                except (MaterialError, OSError, ValueError, UnicodeError) as exc:
                    raise self.error('invalid frozen material: ' + str(exc)) from exc
                message_matches = (block.get('content') == expected or (raw == '' and
                    block.get('content') == '(' + TOOL + ' completed with no output)'))
                if not message_matches or event.get('tool_use_result') != expected:
                    raise self.error('material text does not match frozen snapshot')
                self.results.add(uid)
                self.complete.add(self.registry['materials'][mid]['path'])


def serve(registry_path):
    registry = load_registry(registry_path)
    while True:
        line = sys.stdin.readline(65537)
        if not line:
            break
        if len(line.encode('utf-8')) > 65536:
            raise MaterialError('oversize MCP request')
        request = json.loads(line)
        if not isinstance(request, dict) or request.get('jsonrpc') != '2.0':
            raise MaterialError('invalid JSON-RPC request')
        if 'id' not in request:
            if request.get('method') not in ('notifications/initialized', 'notifications/cancelled'):
                raise MaterialError('unknown MCP notification')
            continue
        method, params = request.get('method'), request.get('params', {})
        if method == 'initialize':
            result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'materials', 'version': '1'}}
        elif method == 'tools/list':
            result = {'tools': [{'name': 'read_material', 'description': 'Read a frozen material by ID.',
                'inputSchema': {'type': 'object', 'properties': {'id': {'type': 'string', 'enum': list(registry['materials'])}},
                                'required': ['id'], 'additionalProperties': False}}]}
        elif method == 'tools/call':
            try:
                if (not isinstance(params, dict) or not {'name', 'arguments'} <= set(params)
                        or set(params) - {'name', 'arguments', '_meta'}
                        or ('_meta' in params and not isinstance(params['_meta'], dict))
                        or params['name'] != 'read_material' or not isinstance(params['arguments'], dict)
                        or set(params['arguments']) != {'id'}):
                    raise MaterialError('only read_material with one id is permitted')
                result = {'content': [{'type': 'text', 'text': read_material(registry, params['arguments']['id'])}]}
            except (MaterialError, OSError, ValueError, UnicodeError) as exc:
                result = {'content': [{'type': 'text', 'text': str(exc)}], 'isError': True}
        else:
            print(json.dumps({'jsonrpc': '2.0', 'id': request['id'],
                              'error': {'code': -32601, 'message': 'method not allowed'}}), flush=True)
            continue
        print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2:
            raise MaterialError('registry argument required')
        serve(sys.argv[1])
    except (MaterialError, OSError, ValueError, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
