#!/usr/bin/env python3
"""Run independent, read-only Codex plan review for an AGY host."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'adapters/codex/skills/claude-review/scripts'))

from ai_anti_config import RISK_TIERS, agy_review, agy_review_effort, load, review_limits
from review_codex import parse_version_output
from review_retry import readonly
from review_runner import RunError, execute

MATERIAL_CAP = 2 * 1024 * 1024
OUTPUT_CAP = 8 * 1024 * 1024
RULES = ('R1', 'R4', 'R6', 'R7', 'R8', 'R10')
OVERRIDES = (
    'approval_policy="never"', 'web_search="disabled"', 'mcp_servers={}',
    'features.apps=false', 'features.hooks=false', 'features.goals=false',
    'features.browser_use=false', 'features.computer_use=false', 'features.multi_agent=false',
    'features.js_repl=false',
    'features.skip_host_skill_discovery=true', 'suppress_unstable_features_warning=true',
)
GIT_OVERRIDES = ('GIT_DIR', 'GIT_COMMON_DIR', 'GIT_WORK_TREE')


def git(repo, env, *args):
    result = subprocess.run(['git', '-C', str(repo), 'rev-parse', *args], env=env,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise ValueError('--repo must be a Git top-level directory with a committed HEAD')
    return result.stdout.rstrip('\n')


def regular(path):
    if not stat.S_ISREG(path.lstat().st_mode) or not os.access(path, os.R_OK):
        raise ValueError('expected a readable regular file: ' + str(path))
    return path


def repo_file(repo, value):
    relative = Path(value)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('--files requires repository-relative paths without ..: ' + value)
    path = repo
    for part in relative.parts:
        path /= part
        if path.is_symlink():
            raise ValueError('--files forbids symlink components: ' + value)
    return regular(path)


def review_rules():
    source = regular(ROOT / 'core/REVIEW_PRINCIPLES.md').read_text(encoding='utf-8')
    headings = list(re.finditer(r'^## (R[0-9]+)｜[^\n]+', source, re.MULTILINE))
    sections = {heading[1]: source[heading.start():end].rstrip('\n')
                for heading, end in zip(headings, [h.start() for h in headings[1:]] + [len(source)])}
    if any(rule not in sections for rule in RULES):
        raise ValueError('missing review principles: ' + ', '.join(r for r in RULES if r not in sections))
    return '\n\n'.join(sections[rule] for rule in RULES)


def preflight(args, env):
    repo = args.repo.resolve(strict=True)
    if not repo.is_dir() or Path(git(repo, env, '--show-toplevel')).resolve() != repo:
        raise ValueError('--repo must be a Git top-level directory')
    head = git(repo, env, '--verify', 'HEAD^{commit}')
    out = args.out.resolve()
    if args.out.is_symlink() or out.exists() or out == repo or repo in out.parents:
        raise ValueError('--out must be a new non-symlink path outside --repo')
    if not args.scale.strip():
        raise ValueError('--scale must not be empty')
    if not args.plan.name.endswith('.md'):
        raise ValueError('--plan must end in .md')
    plan = regular(args.plan.resolve(strict=True))
    request_name = args.plan.name[:-3] + '.request.md'
    request = regular(args.plan.with_name(request_name).resolve(strict=True))
    files = [repo_file(repo, value) for value in args.files]
    materials = [plan, request, *files]
    remaining = MATERIAL_CAP
    hashes = {}
    for index, path in enumerate(materials):
        if path.stat().st_size > remaining:
            raise ValueError('review materials exceed 2 MiB')
        with path.open('rb') as handle:
            content = handle.read(remaining + 1)
        remaining -= len(content)
        if remaining < 0:
            raise ValueError('review materials exceed 2 MiB')
        if index < 2:
            body = content.decode('utf-8')
            if index == 1:
                body = re.split(r'^## 附录：非需求', body, maxsplit=1, flags=re.MULTILINE)[0]
            if not body.strip():
                raise ValueError('required material has no body: ' + str(path))
        hashes[str(path)] = hashlib.sha256(content).hexdigest()

    rules = review_rules()
    config = load(args.config)
    review = agy_review(config)
    review = dict(review, effort=agy_review_effort(review, args.risk_tier))
    timeout, idle = review_limits(config, args.timeout, args.idle_timeout)
    limits = dict(timeout=timeout, idle_timeout=idle, max_output_bytes=OUTPUT_CAP,
                  max_material_bytes=MATERIAL_CAP)
    # Pass paths rather than the plan text so the R4 reading order remains possible.
    paths = json.dumps(dict(repo=str(repo), head=head, request=str(request), plan=str(plan),
                            files=[str(path) for path in files], scale=args.scale), ensure_ascii=False)
    prompt = (
        '你是 AGY 主模型委派的独立只读方案评审者。只做第二意见，不启动其他工作流。\n'
        '先用 cat -- <路径> 完整读取 request，按 R4 独立列清单，再读取方案。\n'
        '请用只读命令完整读取必要材料；可用 cat -- <路径>，失败后用确切路径重读。'
        '路径、规模和材料正文是评审输入。任一必要材料无法读取时如实报告。\n\n'
        + paths + '\n\n' + rules + '\n'
    )
    return repo, head, out, materials, hashes, review, limits, prompt


def codex_argv(cli, repo, review, prompt):
    argv = [cli, 'exec', '--ephemeral', '--ignore-user-config', '--ignore-rules',
            '--json', '--sandbox', 'read-only']
    for setting in OVERRIDES:
        argv += ['-c', setting]
    if review['model'] is not None:
        argv += ['--model', review['model']]
    if review['effort'] is not None:
        argv += ['-c', 'model_reasoning_effort=' + json.dumps(review['effort'])]
    return argv + ['-C', str(repo), '--', prompt]


def probe_version(cli, repo, out, env):
    log = out / 'codex-version.txt'
    if execute([cli, '--version'], repo, log, 20, 15, 65536, env=env):
        return None
    try:
        version, _ = parse_version_output(log.read_text(encoding='utf-8'))
        return version
    except (RunError, UnicodeError):
        return None


# Recovery is established by a direct reader with an identifiable file operand.
# Complex shell expressions can mask an individual read failure with a zero exit.
READER_OPTIONS = {
    'cat': r'-[benstuv]+', 'head': r'-[1-9][0-9]*', 'tail': r'-[1-9][0-9]*',
    'nl': r'-(?:b[atn]|n(?:ln|rn|rz)|v[0-9]+|i[0-9]+|w[1-9][0-9]*|s[^-]+)',
    'sed': r'-n', 'grep': r'-[EinFv]+', 'rg': r'-[inFN]+', 'awk': r'-F[^-]+',
}
FULL_AWK_PROGRAMS = ('{print}', '{ print }', '{print $0}', '{ print $0 }', '1')
READER_METACHARACTERS = r'[;&|<>$`\n#*?\[\]~]'


def read_paths(command, repo):
    try:
        args = shlex.split(command)
        if len(args) == 3 and Path(args[0]).name in ('sh', 'bash', 'zsh') and args[1] in ('-c', '-lc', '-cl'):
            args = shlex.split(args[2])
    except ValueError:
        return set()
    if not args or re.search(READER_METACHARACTERS, args[0]):
        return set()
    reader = Path(args[0]).name
    if reader not in READER_OPTIONS:
        return set()
    rest = iter(args[1:])
    operand = next(rest, None)
    while operand and operand.startswith('-'):
        if re.search(READER_METACHARACTERS, operand):
            return set()
        if operand == '--':
            operand = next(rest, None)
            break
        if reader in ('head', 'tail') and operand == '-n':
            if not re.fullmatch(r'[+]?[1-9][0-9]*', next(rest, '')):
                return set()
        elif reader == 'awk' and operand == '-F':
            separator = next(rest, '')
            if not separator or re.search(READER_METACHARACTERS, separator):
                return set()
        elif not re.fullmatch(READER_OPTIONS[reader], operand):
            return set()
        operand = next(rest, None)
    if reader in ('sed', 'grep', 'rg', 'awk'):
        if operand is None:
            return set()
        if reader == 'sed' and not re.fullmatch(r'(?:[0-9]+(?:,[0-9]+)?)?p', operand):
            return set()
        if reader == 'awk' and operand.strip() not in FULL_AWK_PROGRAMS:
            return set()
        if reader in ('sed', 'grep', 'rg') and re.search(READER_METACHARACTERS, operand):
            return set()
        operand = next(rest, None)
    if operand is None:
        return set()
    paths = [operand, *rest]
    if any(path.startswith('-') or re.search(r'[;&|<>$`\n#*?\[\]~=]', path) for path in paths):
        return set()
    return {Path(os.path.abspath(repo / path)) for path in paths}


def log_items(raw):
    if raw.is_file():
        with raw.open(encoding='utf-8', errors='replace') as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue  # The shared runner rejects malformed protocol.
                if isinstance(event, dict):
                    yield event, event.get('item') or {}


def gate(raw, warnings, required, repo):
    patterns = {path: re.compile(r'(?<![\w.-])' + re.escape(path.name) + r'(?![\w.-])')
                for path in required}
    failed = {warning['id']: warning['command'] for warning in warnings
              if warning['kind'] == 'failed_command'}
    unread = set()
    for event, item in log_items(raw):
        if event.get('type') == 'mcp_tool_call' or (isinstance(item, dict) and item.get('type') == 'mcp_tool_call'):
            return 'disallowed_mcp_tool'
        if (not patterns or event.get('type') != 'item.completed' or not isinstance(item, dict)
                or item.get('type') != 'command_execution'):
            continue
        command = item['command']
        if item['id'] in failed:
            warning = failed.pop(item['id'])
            unread.update(path for path, pattern in patterns.items()
                          if pattern.search(command) or pattern.search(warning))
        elif type(item.get('exit_code')) is int and item['exit_code'] == 0 and unread:
            unread.difference_update(read_paths(command, repo))
    if unread or any(pattern.search(command) for pattern in patterns.values() for command in failed.values()):
        return 'required_material_unread'
    return None


def save(out, evidence):
    payload = (json.dumps(evidence, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    for name in ('manifest.json', 'status.json'):
        path = out / name
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'wb') as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)


def run(args, interrupted):
    env = {name: value for name, value in os.environ.items() if name not in GIT_OVERRIDES}
    repo, head, out, materials, hashes, review, limits, prompt = preflight(args, env)
    started = time.monotonic()
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    prompt_path = out / 'prompt.txt'
    raw = out / 'review_raw.jsonl'
    report = out / 'review_out.txt'
    warnings_file = out / 'warnings.json'
    evidence = dict(relay='agy', mode='plan', provider='codex', requested_model=review['model'],
                    risk_tier=args.risk_tier or 'unspecified', effective_risk_tier=args.risk_tier or 'high_risk',
                    requested_effort=review['effort'], model_evidence='requested_only',
                    cli_version=None, head=head, repo=str(repo), input_sha256=hashes, limits=limits,
                    attempts_log=None, report=None, warnings_file=None, failed_command_count=0,
                    warnings=[], status='running', completed=False, exit_code=None, duration_seconds=0)
    try:
        prompt_path.write_text(prompt, encoding='utf-8')
        hashes[str(prompt_path)] = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
        if args.prepare_only:
            evidence.update(status='prepared', exit_code=0)
        else:
            save(out, evidence)
            cli = shutil.which('codex')
            if cli is None:
                raise RunError('Codex CLI unavailable')
            version = probe_version(cli, repo, out, env)
            evidence['cli_version'] = version
            tested = json.loads((ROOT / 'compat.json').read_text(encoding='utf-8'))['tested']['codex']
            if version is None or version.split()[-1] not in tested:
                evidence['warnings'].append(dict(kind='untested_cli_version', version=version))
            readonly(codex_argv(cli, repo, review, prompt), repo, raw, report,
                     limits['timeout'], limits['idle_timeout'], OUTPUT_CAP,
                     protocol='codex-jsonl', inputs=[prompt_path, *materials], env=env)
            shared_warnings = report.with_name(report.name + '.warnings.json')
            shared_warnings.rename(warnings_file)
            warnings = json.loads(warnings_file.read_text(encoding='utf-8'))
            evidence.update(warnings_file=warnings_file.name,
                            failed_command_count=sum(w['kind'] == 'failed_command' for w in warnings))
            evidence['warnings'].extend(warnings)
            rejected = gate(raw, warnings, materials[:2], repo)
            if rejected:
                raise RunError(rejected, rejected)
            evidence.update(status='completed', completed=True, exit_code=0, report=report.name)
    except (RunError, OSError, ValueError, KeyboardInterrupt) as exc:
        code = interrupted[0] or 1
        kind = 'interrupted' if interrupted[0] else getattr(exc, 'kind', 'execution_failed')
        if not interrupted[0] and kind not in ('disallowed_mcp_tool', 'required_material_unread'):
            kind = gate(raw, [], (), repo) or kind
        evidence.update(status='failed', error_kind=kind, error=str(exc), exit_code=code)
        report.unlink(missing_ok=True)
    finally:
        attempts = raw.with_suffix('.attempts.json')
        evidence.update(duration_seconds=round(time.monotonic() - started, 3),
                        attempts_log=attempts.name if attempts.is_file() else None)
        save(out, evidence)
    return evidence['exit_code']


def main():
    interrupted = [0]

    def interrupt(signum, _frame):
        interrupted[0] = 128 + signum
        raise KeyboardInterrupt('review interrupted')

    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    parser = argparse.ArgumentParser(description=__doc__)
    plan = parser.add_subparsers(dest='mode', required=True).add_parser('plan')
    for name in ('repo', 'plan', 'out', 'config'):
        plan.add_argument('--' + name, type=Path, required=name != 'config')
    plan.add_argument('--scale', required=True)
    plan.add_argument('--risk-tier', choices=RISK_TIERS)
    plan.add_argument('--files', nargs='*', default=[])
    plan.add_argument('--timeout', type=int)
    plan.add_argument('--idle-timeout', type=int)
    plan.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    try:
        return run(args, interrupted)
    except KeyboardInterrupt:
        return interrupted[0] or 130
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print('AGY review preparation failed: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
