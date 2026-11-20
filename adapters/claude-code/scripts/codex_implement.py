#!/usr/bin/env python3
"""Run one bounded Codex implementation in a prepared linked Git worktree."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

SKILLS = Path(__file__).resolve().parents[2] / 'codex' / 'skills'
sys.path[:0] = [str(SKILLS / skill / 'scripts') for skill in ('claude-review', 'codex-relay')]

from ai_anti_config import ConfigError, agy_implementation, claude_implementation, load, review_limits
from implementation import CAP, digest, interrupt, write
from implementation_protocol import validate
from review_codex import parse_version_output
from review_runner import RunError, execute

VERSIONS = ('codex-cli 0.169.1',)


def git(workspace, *args):
    # Inspect the supplied directory even when the caller inherited Git overrides.
    env = os.environ.copy()
    for key in ('GIT_DIR', 'GIT_COMMON_DIR', 'GIT_WORK_TREE'):
        env.pop(key, None)
    result = subprocess.run(['git', '-C', str(workspace), 'rev-parse', *args], env=env,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise ConfigError('workspace must be a linked Git worktree with a committed HEAD')
    return result.stdout.rstrip('\n')


def run(args):
    workspace = args.workspace.resolve(strict=True)
    prompt = args.prompt.resolve(strict=True)
    if not workspace.is_dir() or not prompt.is_file():
        raise ConfigError('workspace directory and regular prompt file are required')
    if args.out.exists() or args.out.is_symlink():
        raise ConfigError('implementation output already exists')
    out = args.out.resolve()
    if out == workspace or workspace in out.parents:
        raise ConfigError('output must be outside the implementation workspace')
    root = Path(git(workspace, '--show-toplevel')).resolve()
    git_dir = Path(git(workspace, '--path-format=absolute', '--git-dir')).resolve()
    common = Path(git(workspace, '--path-format=absolute', '--git-common-dir')).resolve()
    if workspace != root or git_dir == common:
        raise ConfigError('workspace must be the root of a linked Git worktree, outside the main worktree')
    head = git(workspace, '--verify', 'HEAD^{commit}')

    config = load(args.config)
    selected = agy_implementation(config) if args.relay == 'agy' else claude_implementation(config)
    if selected is None:
        raise ConfigError(args.relay + '_relay.implementation must explicitly select codex')
    model = selected['model']
    effort = selected['effort']
    timeout, idle = review_limits(config, args.timeout, args.idle_timeout)
    prompt_bytes = prompt.read_bytes()
    payload = prompt_bytes.decode('utf-8')
    if '\0' in payload:
        raise ConfigError('prompt contains a NUL byte')
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    saved_prompt = out / 'prompt.txt'
    saved_prompt.write_bytes(prompt_bytes)
    log = out / 'implementation.jsonl'
    stderr = out / 'implementation.stderr.txt'
    evidence = dict(relay=args.relay, provider='codex', requested_model=model, requested_effort=effort,
                    model_evidence='requested_only',
                    cli_version=None, prompt_sha256=digest(saved_prompt), workspace=str(workspace),
                    head=head, duration_seconds=0, exit_code=None, log=log.name, log_sha256=None,
                    stderr_log=stderr.name, stderr_sha256=None, warnings=[], status='running',
                    completed=False, accepted=False,
                    limits=dict(timeout=timeout, idle_timeout=idle, max_output_bytes=CAP))
    started = time.monotonic()
    try:
        write(out / 'manifest.json', evidence)
        write(out / 'status.json', evidence)
        cli = shutil.which('codex')
        if not cli:
            raise RunError('Codex CLI unavailable')
        version_log = out / 'codex-version.txt'
        if execute([cli, '--version'], workspace, version_log, 20, 15, 65536):
            raise RunError('Codex version probe failed')
        version, _ = parse_version_output(version_log.read_text('utf-8'))
        evidence['cli_version'] = version
        if version not in VERSIONS:
            raise RunError('Codex implementation verified only for ' + ', '.join(VERSIONS))
        argv = [cli, 'exec', '--json', '--sandbox', 'workspace-write', '--model', model]
        if effort is not None:
            argv += ['-c', 'model_reasoning_effort=' + json.dumps(effort)]
        argv += ['-C', str(workspace), '--', payload]
        code = execute(argv, workspace, log, timeout, idle, CAP, stderr_log=stderr)
        evidence['exit_code'] = code
        evidence.update(validate(log, code, stderr_log=stderr), status='completed', completed=True)
    except (RunError, OSError, UnicodeError, KeyboardInterrupt) as exc:
        evidence.update(status='failed', error_kind=getattr(exc, 'kind', 'execution_failed'),
                        error=str(exc))
    finally:
        evidence.update(duration_seconds=round(time.monotonic() - started, 3),
                        log_sha256=digest(log), stderr_sha256=digest(stderr))
        write(out / 'manifest.json', evidence)
        write(out / 'status.json', evidence)
    return evidence


def main():
    signal.signal(signal.SIGTERM, interrupt)
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('workspace', 'prompt', 'out'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--relay', choices=('claude', 'agy'), default='claude')
    parser.add_argument('--timeout', type=int)
    parser.add_argument('--idle-timeout', type=int)
    try:
        evidence = run(parser.parse_args())
    except (ConfigError, OSError, UnicodeError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        print('implementation preparation failed: ' + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({key: evidence[key] for key in ('status', 'completed', 'accepted')}))
    return 0 if evidence['completed'] else 1


if __name__ == '__main__':
    sys.exit(main())
