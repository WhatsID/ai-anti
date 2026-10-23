#!/usr/bin/env python3
"""Explicit, bounded, two-stage problem second opinions; no Git required."""
import argparse
from functools import partial
import json
import os
from pathlib import Path
import shutil
import sys
import time

from ai_anti_config import ConfigError, load as load_config, model_families
from problem_material import directory, load_bundle, freeze
from problem_result import validate_result, previous_round, reserve_previous, report
from review_material import MaterialError, no_symlink, sha
from review import write_json, snapshot_hashes, require_full_reads, make_prompt, compat_warning
from review_channels import resolve, evidence, child_environment
from review_chunks import chunk_file, required_files, source_labels
from review_materials import build_registry, mcp_config
from review_runner import MODEL_FAMILIES, RunError, capability, run_stage

ROOT = Path(__file__).resolve().parents[5]

RESULT_RULES = '''阶段二只输出严格 JSON 对象，不带代码围栏或其他文字。字段必须恰好为：
assessment: supported|partially_supported|unsupported|insufficient_evidence；
findings: 数组，每项恰好含 id、claim、assessment（同枚举）、evidence_ids（只引用输入证据id）、
reason、alternative（非空字符串或null）、next_check（null或恰好含needed、purpose、distinguishing_observation三个非空字符串的对象）；
coverage: 非空字符串；limitations: 非空字符串数组（可为空数组）。
没有证据时总体和每项assessment必须为insufficient_evidence。derived不能当原始材料。
supported只表示已读材料支持，不等同生产实证、根因成立或已修复。不得执行补证或修复。'''


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--config-base', default=str(Path.cwd()))
    p.add_argument('--channel', choices=('claude', 'qwen', 'qoder'))
    p.add_argument('--model')
    p.add_argument('--qwen-executor', choices=('claude', 'codex'))
    p.add_argument('--responses-base-url')
    p.add_argument('--timeout', type=int)
    p.add_argument('--idle-timeout', type=int, default=900)
    p.add_argument('--max-output-bytes', type=int)
    p.add_argument('--round', type=int, choices=(1, 2), default=1)
    p.add_argument('--previous')
    p.add_argument('--prepare-only', action='store_true')
    return p


def new_output(value, bundle):
    output = Path(value)
    if not output.is_absolute() or '..' in output.parts:
        raise MaterialError('--out must be an absolute new directory')
    no_symlink(output.parent)
    if output == bundle or bundle in output.parents or output in bundle.parents:
        raise MaterialError('--out must be outside bundle, without containing it')
    output.mkdir(mode=0o700)  # Atomic exclusive creation, including dangling symlinks.
    return output


def rules():
    core = (ROOT / 'core/REVIEW_PRINCIPLES.md').read_text(encoding='utf-8')
    sections = []
    for prefix in ('## R1｜', '## R7｜', '## R10｜'):
        start = core.find(prefix)
        if start < 0:
            raise MaterialError('core authority section missing: ' + prefix)
        end = core.find('\n## ', start + 1)
        sections.append(core[start:] if end < 0 else core[start:end])
    return '\n\n'.join(sections) + '\n\n' + (ROOT / 'core/PROBLEM_REVIEW.md').read_text(encoding='utf-8')


def prepare_prompt(stage, sources, channel, logs, authority, mode, manifest):
    registry_path = registry = None
    if channel['channel'] in ('qwen', 'qoder'):
        registry_path = logs / (mode + '.registry.json')
        registry = build_registry(stage, sources, registry_path)
        manifest.setdefault('material_registries', {})[mode] = registry
        if channel.get('executor') == 'codex':
            from review_codex import write_config
            write_config(channel, registry_path, logs / (mode + '.codex-config.json'))
        else:
            write_json(logs / (mode + '.mcp.json'), mcp_config(registry_path))
    # Reuse transport instructions only; diagnostic semantics replace plan/diff text.
    prompt = make_prompt('inventory', required_files(sources), authority, '问题复核；无自动执行', sources, registry)
    prompt = prompt.rsplit('\n\n', 1)[0]
    prompt += '\n\nProblem review stage: ' + mode
    prompt += ('\n不读分析结论。独立列出候选解释、证据、排除判据、反证和待验证项，不声称根因已证实。'
               if mode == 'independent' else '\n读取分析和原样 independent.md，逐条复核。\n' + RESULT_RULES)
    (logs / (mode + '.prompt.txt')).write_text(prompt, encoding='utf-8')
    manifest.setdefault('prompt_sha256', {})[mode] = sha(prompt.encode('utf-8'))
    return prompt, registry_path


def main():
    os.umask(0o077)
    args = parser().parse_args()
    output = manifest = channel = None
    warnings = []
    try:
        config = load_config()
        families = model_families(config, MODEL_FAMILIES)
        config_base = directory(args.config_base, '--config-base')
        channel = resolve(config, args.channel, args.model, config_base, families,
                          args.qwen_executor, args.responses_base_url)
        args.timeout = args.timeout if args.timeout is not None else (1800 if channel['channel'] == 'qwen' else 600)
        args.max_output_bytes = args.max_output_bytes if args.max_output_bytes is not None else (8 if channel['channel'] == 'qwen' else 2) * 1024 * 1024
        if not (0 < args.timeout <= 3600 and 0 < args.idle_timeout <= 900 and 0 < args.max_output_bytes <= 8 * 1024 * 1024):
            raise MaterialError('invalid bounded resource limits')
        bundle, source_manifest, records, input_hash = load_bundle(args.bundle)
        previous = previous_round(args.previous, source_manifest, args.round)
        authority = rules()
        output = new_output(args.out, bundle)
        logs = output / 'logs'
        logs.mkdir()
        manifest = {'mode': 'problem', 'schema_version': 1, 'status': 'preparing',
                    'task_id': source_manifest['task_id'], 'revision': source_manifest['revision'],
                    'round': args.round, 'previous': previous, 'bundle': str(bundle),
                    'bundle_manifest_sha256': input_hash, 'config_base': str(config_base),
                    'created_at': time.time(), **evidence(channel), 'warnings': warnings,
                    'core_root': str(ROOT), 'authority_sha256': sha(authority.encode('utf-8')),
                    'discipline_sha256': sha((ROOT / 'core/CLI_DISCIPLINE.md').read_bytes()),
                    'limits': {'timeout': args.timeout, 'idle_timeout': args.idle_timeout,
                               'max_output_bytes': args.max_output_bytes},
                    'material_transport': 'material_ids' if channel['channel'] in ('qwen', 'qoder') else 'filesystem_read',
                    'inputs': [{k: r[k] for k in ('role', 'origin', 'name', 'sha256')} for r in records],
                    'stages': {}, 'sources': {}, 'snapshots': {}}
        stages = {name: output / 'materials' / name for name in ('independent', 'review')}
        for name, stage in stages.items():
            manifest['sources'][name] = freeze(stage, source_manifest, records, name == 'review')
            manifest['snapshots'][name] = snapshot_hashes(stage)
        initial, initial_registry = prepare_prompt(stages['independent'], manifest['sources']['independent'], channel, logs, authority, 'independent', manifest)
        write_json(output / 'manifest.json', manifest)
        if args.prepare_only:
            manifest['status'] = 'prepared'
            write_json(output / 'manifest.json', manifest)
            write_json(output / 'status.json', {'status': 'prepared', 'completed': False, **evidence(channel)})
            print(output)
            return 0
        reserve_previous(previous, output)
        env = child_environment(channel)  # No credential reads in prepare-only.
        runner, preflight, executor = run_stage, capability, 'claude'
        if channel['channel'] == 'qoder':
            from review_qoder import run_stage as runner, capability as preflight
            executor = 'qodercli'
        elif channel.get('executor') == 'codex':
            from review_codex import run_stage as codex_run, capability as preflight
            runner, executor = partial(codex_run, channel=channel), 'codex'
            warnings.append('Codex Responses 身份仅为 requested_only，actual_model=null；服务端路由身份未证实。')
        cli = shutil.which(executor)
        if not cli:
            raise RunError(executor + ' not found on PATH')
        manifest['capabilities'] = preflight(cli, logs, env=env, material_config=mcp_config(initial_registry) if initial_registry else None)
        warnings.extend(manifest['capabilities'].get('warnings', []))
        manifest['cli_version'] = manifest['capabilities']['cli_version']
        warning = compat_warning(manifest['cli_version'], 'qoder' if executor == 'qodercli' else executor)
        if warning:
            warnings.append(warning)
        independent = None
        for name, stage in stages.items():
            if snapshot_hashes(stage) != manifest['snapshots'][name]:
                raise RunError(name + ' frozen snapshot changed before execution')
            if name == 'independent':
                prompt, registry_path = initial, initial_registry
            else:
                (stage / 'independent.md').write_bytes(independent.encode('utf-8'))
                sources = manifest['sources'][name]
                sources['independent.md'] = chunk_file(stage, 'independent.md', len(sources) + 1)
                manifest['snapshots'][name] = snapshot_hashes(stage)
                prompt, registry_path = prepare_prompt(stage, sources, channel, logs, authority, name, manifest)
            write_json(output / 'manifest.json', manifest)
            sources = manifest['sources'][name]
            result = runner(cli, stage, prompt, logs / (name + '.jsonl'), channel['cli_model'],
                            args.timeout, args.idle_timeout, args.max_output_bytes,
                            {channel['cli_model']: channel['pattern']}, env=env,
                            expected_model=channel['configured_model'] if channel['channel'] in ('qwen', 'qoder') else None,
                            required=source_labels(sources), registry_path=registry_path)
            raw_name = 'independent.md' if name == 'independent' else 'review.raw.txt'
            (output / raw_name).write_bytes(result['result'].encode('utf-8'))
            require_full_reads(result, required_files(sources), sources)
            if snapshot_hashes(stage) != manifest['snapshots'][name]:
                raise RunError(name + ' frozen snapshot changed during execution')
            manifest['stages'][name] = {k: v for k, v in result.items() if k not in ('result', 'complete_reads')}
            write_json(output / 'manifest.json', manifest)
            if name == 'independent':
                independent = result['result']
            else:
                findings = validate_result(result['result'], [e['id'] for e in source_manifest['evidence']])
        write_json(output / 'findings.json', findings)
        identity = {**evidence(channel), 'actual_model': result['model']}
        warnings.extend(name + ' 阶段纠正 Read 缺文件错误：' + str(len(item.get('read_recoveries', [])))
                        for name, item in manifest['stages'].items() if item.get('read_recoveries'))
        warning_text = ''.join('> 警告：' + warning + '\n' for warning in warnings)
        (output / 'report.md').write_text(warning_text + report(findings, independent, identity, source_manifest), encoding='utf-8')
        manifest.update(status='completed', actual_model=result['model'], finished_at=time.time(),
                        exit_reason='verified protocol result; not proof of root cause')
        manifest['output_hashes'] = {name: sha((output / name).read_bytes()) for name in
                                     ('findings.json', 'report.md', 'independent.md', 'review.raw.txt')}
        write_json(output / 'manifest.json', manifest)
        write_json(output / 'status.json', {'status': 'completed', 'completed': True, **identity,
                    'warnings': warnings, 'limits': manifest['limits'],
                    'manifest_sha256': sha((output / 'manifest.json').read_bytes())})
        print(output)
        return 0
    except (MaterialError, ConfigError, RunError, OSError, ValueError, TypeError, KeyError, KeyboardInterrupt) as exc:
        if output:
            if manifest:
                manifest.update(status='failed', exit_reason=str(exc), finished_at=time.time())
                write_json(output / 'manifest.json', manifest)
            write_json(output / 'status.json', {'status': 'failed', 'completed': False,
                'error': str(exc), 'failure_kind': getattr(exc, 'kind', 'preparation_failed'),
                'warnings': warnings, **(evidence(channel) if channel else {})})
            print(output)
        else:
            print('problem review preparation failed: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
