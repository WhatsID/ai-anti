"""Problem second-opinion result schema, reports and local round lineage."""
import json
from pathlib import Path

from problem_material import directory, fields, strict_json, text
from review_material import MaterialError, read_text, relative_file, sha

ASSESSMENTS = ('supported', 'partially_supported', 'unsupported', 'insufficient_evidence')


def validate_result(raw, evidence_ids):
    value = strict_json(raw)
    fields(value, ('assessment', 'findings', 'coverage', 'limitations'), 'result')
    if value['assessment'] not in ASSESSMENTS:
        raise MaterialError('invalid result assessment')
    text(value['coverage'], 'coverage')
    if not isinstance(value['limitations'], list) or not isinstance(value['findings'], list):
        raise MaterialError('findings and limitations must be arrays')
    for item in value['limitations']:
        text(item, 'limitation')
    seen = set()
    for finding in value['findings']:
        fields(finding, ('id', 'claim', 'assessment', 'evidence_ids', 'reason', 'alternative', 'next_check'), 'finding')
        for key in ('id', 'claim', 'reason'):
            text(finding[key], key)
        if finding['id'] in seen or finding['assessment'] not in ASSESSMENTS:
            raise MaterialError('duplicate finding id or invalid assessment')
        seen.add(finding['id'])
        refs = finding['evidence_ids']
        if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs) or len(refs) != len(set(refs)) or set(refs) - set(evidence_ids):
            raise MaterialError('invalid evidence references')
        if finding['alternative'] is not None:
            text(finding['alternative'], 'alternative')
        check = finding['next_check']
        if check is not None:
            fields(check, ('needed', 'purpose', 'distinguishing_observation'), 'next_check')
            for key in check:
                text(check[key], 'next_check.' + key)
    if not evidence_ids and (value['assessment'] != 'insufficient_evidence' or
                            any(f['assessment'] != 'insufficient_evidence' for f in value['findings'])):
        raise MaterialError('empty evidence requires insufficient_evidence')
    return value


def previous_round(value, manifest, round_number):
    if round_number == 1:
        if value is not None:
            raise MaterialError('--previous is only valid for round 2')
        return None
    if value is None:
        raise MaterialError('round 2 requires --previous')
    root = directory(value, '--previous')
    def read(name):
        _, path = relative_file(root, name)
        with path.open('rb') as handle:
            data = handle.read(8 * 1024 * 1024 + 1)
        if len(data) > 8 * 1024 * 1024:
            raise MaterialError('previous output exceeds limit: ' + name)
        return data
    raw = read('manifest.json')
    prior = strict_json(raw.decode('utf-8'))
    status = strict_json(read('status.json').decode('utf-8'))
    if (prior.get('mode') != 'problem' or prior.get('round') != 1 or prior.get('status') != 'completed'
            or type(prior.get('round')) is not int or prior.get('task_id') != manifest['task_id']
            or status.get('status') != 'completed' or status.get('completed') is not True
            or status.get('manifest_sha256') != sha(raw) or prior.get('previous') is not None):
        raise MaterialError('invalid previous completed first round or hash')
    if type(prior.get('revision')) is not int or manifest['revision'] <= prior['revision']:
        raise MaterialError('second round requires a newer revision')
    hashes = prior.get('output_hashes')
    if not isinstance(hashes, dict) or set(hashes) != {'findings.json', 'report.md', 'independent.md', 'review.raw.txt'}:
        raise MaterialError('previous output hashes unavailable')
    for name, expected in hashes.items():
        if sha(read(name)) != expected:
            raise MaterialError('previous output hash mismatch: ' + name)
    # Also verify frozen material bytes, rather than trusting a completed label.
    snapshots_by_stage = prior.get('snapshots')
    if not isinstance(snapshots_by_stage, dict) or set(snapshots_by_stage) != {'independent', 'review'}:
        raise MaterialError('previous must have both frozen stages')
    sources = prior.get('sources')
    if not isinstance(sources, dict) or set(sources) != {'independent', 'review'}:
        raise MaterialError('previous source mappings unavailable')
    for stage, snapshots in snapshots_by_stage.items():
        if stage not in ('independent', 'review') or not isinstance(snapshots, dict) or not snapshots:
            raise MaterialError('invalid previous snapshots')
        if not isinstance(sources[stage], dict) or not sources[stage]:
            raise MaterialError('previous source mapping is empty')
        blocks = {block['path']: block['sha256'] for source in sources[stage].values() for block in source['blocks']}
        if blocks != snapshots:
            raise MaterialError('previous frozen source mapping mismatch')
        for name, expected in snapshots.items():
            _, path = relative_file(root / 'materials' / stage, name)
            if sha(read_text(path, name)) != expected:
                raise MaterialError('previous frozen snapshot changed')
    return {'path': str(root), 'manifest_sha256': sha(raw)}


def reserve_previous(previous, output):
    if previous:
        # Exclusive local claim prevents two second rounds for the same first run.
        path = Path(previous['path']) / 'round-2.claim.json'
        with path.open('x', encoding='utf-8') as handle:
            json.dump({'out': str(output), 'previous_sha256': previous['manifest_sha256']}, handle)


def report(value, independent, identity, manifest):
    lines = ['# 问题复核（第二意见）', '',
             'completed 仅表示协议校验完成。supported 仅表示所读证据支持，不代表生产实证、根因已证实或已修复。', '',
             '身份边界：' + json.dumps(identity, ensure_ascii=False),
             '调用方须核对主模型与评审模型；未知身份或同模型不能宣称跨模型。', '',
             '独立阶段只隐藏分析正文；调用方选择的证据仍可能有倾向。', '',
             '## 独立清单（原样）', '', independent, '', '## 复核结果', '',
             '总体：' + value['assessment'], '', '覆盖：' + value['coverage']]
    for item in value['findings']:
        lines += ['', '### ' + item['id'] + '：' + item['claim'], item['assessment'],
                  '证据：' + ', '.join(item['evidence_ids']), item['reason'],
                  '替代解释：' + (item['alternative'] or '无'),
                  '补证请求：' + json.dumps(item['next_check'], ensure_ascii=False)]
    lines += ['', '## 限制与未覆盖材料', '',
              *['- ' + item for item in manifest['limitations'] + value['limitations']], '',
              '仅复核调用方声明的文本；derived 不是原始材料，来源哈希不证明远端材料真伪。',
              '补查与修复须由调用方按既有授权执行；本入口不执行 next_check。']
    return '\n'.join(lines) + '\n'
