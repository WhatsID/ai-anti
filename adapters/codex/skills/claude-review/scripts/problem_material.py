"""Strict local problem bundles; freeze declared text only (Python 3.9+)."""
import json
from pathlib import Path

from review_material import MaterialError, no_symlink, relative_file, read_text, sha, enforce_limit
from review_chunks import chunk_stage


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise MaterialError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(MaterialError('non-finite JSON')))


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise MaterialError(label + ' must be a non-empty string')
    return value


def fields(value, names, label):
    if not isinstance(value, dict) or set(value) != set(names):
        raise MaterialError(label + ' has missing or unknown fields')


def directory(value, label):
    path = Path(value)
    if not path.is_absolute():
        raise MaterialError(label + ' must be absolute')
    no_symlink(path)
    if not path.is_dir():
        raise MaterialError(label + ' must be an existing directory')
    return path


def load_bundle(value):
    root = directory(value, '--bundle')
    _, manifest_path = relative_file(root, 'manifest.json')
    raw = read_text(manifest_path, 'bundle manifest')
    manifest = strict_json(raw.decode('utf-8'))
    fields(manifest, ('schema_version', 'task_id', 'revision', 'problem_file', 'analysis_file',
                      'evidence', 'limitations', 'domain_checks', 'environment'), 'manifest')
    if type(manifest['schema_version']) is not int or manifest['schema_version'] != 1:
        raise MaterialError('unsupported schema_version')
    text(manifest['task_id'], 'task_id')
    text(manifest['environment'], 'environment')
    if type(manifest['revision']) is not int or manifest['revision'] < 1:
        raise MaterialError('revision must be a positive integer')
    for key in ('limitations', 'domain_checks'):
        if not isinstance(manifest[key], list):
            raise MaterialError(key + ' must be an array')
        for item in manifest[key]:
            text(item, key)
    if not isinstance(manifest['evidence'], list):
        raise MaterialError('evidence must be an array')
    records, seen, ids = [], {'manifest.json'}, set()
    roles = [('problem', text(manifest['problem_file'], 'problem_file')),
             ('analysis', text(manifest['analysis_file'], 'analysis_file'))]
    for item in manifest['evidence']:
        fields(item, ('id', 'file', 'source', 'locator', 'kind', 'coverage'), 'evidence')
        for key in item:
            text(item[key], 'evidence.' + key)
        if item['id'] in ids or item['kind'] not in ('raw_excerpt', 'derived'):
            raise MaterialError('duplicate evidence id or invalid kind')
        ids.add(item['id'])
        roles.append(('evidence', item['file']))
    for index, (role, name) in enumerate(roles):
        normalized, path = relative_file(root, name)
        if normalized in seen:
            raise MaterialError('duplicate path or overlapping material roles: ' + normalized)
        seen.add(normalized)
        data = read_text(path, normalized)
        if role != 'evidence' and not data.decode('utf-8').strip():
            raise MaterialError(role + ' must not be empty')
        records.append({'role': role, 'origin': normalized, 'name': '%03d.txt' % index,
                        'data': data, 'sha256': sha(data)})
    enforce_limit([(r['name'], None, r['data']) for r in records])
    return root, manifest, records, sha(raw)


def freeze(stage, manifest, records, include_analysis=False):
    stage.mkdir(parents=True)
    # Keep origin labels out of filenames and transport arguments. The blind stage
    # intentionally excludes even the analysis path and hash from metadata.
    metadata = {k: manifest[k] for k in ('schema_version', 'task_id', 'revision', 'environment',
                                       'evidence', 'limitations', 'domain_checks')}
    chosen = [r for r in records if include_analysis or r['role'] != 'analysis']
    metadata['source_mapping'] = [{k: r[k] for k in ('role', 'origin', 'name', 'sha256')} for r in chosen]
    (stage / 'context.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    for record in chosen:
        (stage / record['name']).write_bytes(record['data'])
    return chunk_stage(stage)
