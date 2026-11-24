#!/usr/bin/env python3
"""Run one bounded Codex implementation in a prepared linked Git worktree."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

SKILLS = Path(__file__).resolve().parents[2] / 'codex' / 'skills'
sys.path[:0] = [str(SKILLS / skill / 'scripts') for skill in ('claude-review', 'codex-relay')]

from ai_anti_config import ConfigError, agy_implementation, claude_implementation, load, review_limits
from implementation import CAP, digest, interrupt, write
from implementation_protocol import valid_thread_id, validate
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


def read_object(path, mode='r'):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ConfigError(f'{path.name} must be a regular file')
    try:
        with path.open(mode, encoding='utf-8') as handle:
            data = json.load(handle)
    except ValueError as exc:
        raise ConfigError(f'{path.name} must be a JSON object') from exc
    if not isinstance(data, dict):
        raise ConfigError(f'{path.name} must be a JSON object')
    return data


def resume_evidence(source, out, identity, head, relay, model):
    if source.is_symlink() or not source.is_dir():
        raise ConfigError('--resume-from must be a directory and cannot be a symlink')
    source = source.resolve(strict=True)
    if source == out or source in out.parents or out in source.parents:
        raise ConfigError('--resume-from and --out must not overlap')
    try:
        prior = read_object(source / 'manifest.json')
        status = read_object(source / 'status.json')
    except OSError as exc:
        raise ConfigError('cannot read previous implementation evidence: ' + str(exc)) from exc
    if prior != status:
        raise ConfigError('previous manifest.json and status.json differ')
    for key, expected in dict(provider='codex', relay=relay, requested_model=model).items():
        if prior.get(key) != expected:
            raise ConfigError(f'previous implementation {key} does not match')
    if prior.get('cli_version') not in VERSIONS:
        raise ConfigError('previous implementation cli_version is unsupported')
    if prior.get('status') != 'completed' or prior.get('completed') is not True:
        raise ConfigError('previous implementation must be completed')
    if not valid_thread_id(prior.get('thread_id')):
        raise ConfigError('previous implementation lacks a valid thread_id; use a cold start')
    for key, path in identity.items():
        try:
            same = os.path.samefile(prior.get(key), path)
        except (OSError, TypeError, ValueError):
            same = False
        if not same:
            raise ConfigError(f'previous implementation {key} does not match')
    if prior.get('head') != head:
        raise ConfigError('previous implementation HEAD does not match')
    return source, prior


def register_attempt(git_dir, out, source, prior_token):
    path = git_dir / 'ai-anti-implementation.json'
    try:
        # Lock the directory so all callers share the lock across atomic file replacement.
        lock = os.open(git_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                state = read_object(path, 'r+')
            except FileNotFoundError:
                state = None
            token = secrets.token_hex(16) if state is None else state.get('token')
            if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{32}', token):
                raise ConfigError('invalid implementation worktree_token in state')
            if source is not None:
                if state is None or token != prior_token:
                    raise ConfigError('previous implementation worktree_token does not match')
                if state.get('last_out') != str(source):
                    raise ConfigError('previous implementation is not the last_out attempt')
            try:
                with tempfile.TemporaryDirectory(dir=git_dir, prefix='.ai-anti-implementation-') as tmp:
                    pending = Path(tmp) / path.name
                    write(pending, dict(token=token, last_out=str(out)))
                    os.replace(pending, path)
            except OSError:
                if state is None and source is None:
                    return None
                raise
        finally:
            os.close(lock)
    except OSError as exc:
        raise ConfigError('cannot update implementation worktree state: ' + str(exc)) from exc
    return token


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
    if args.relay == 'agy' and selected['provider'] != 'codex':
        raise ConfigError('agy_relay.implementation provider ' + selected['provider']
                          + ' must use adapters/codex/skills/codex-relay/scripts/implementation.py --relay agy')
    model = selected['model']
    effort = selected['effort']
    timeout, idle = review_limits(config, args.timeout, args.idle_timeout)
    prompt_bytes = prompt.read_bytes()
    payload = prompt_bytes.decode('utf-8')
    if '\0' in payload:
        raise ConfigError('prompt contains a NUL byte')
    source = thread_id = prior_token = None
    if args.resume_from is not None:
        identity = dict(workspace=workspace, git_dir=git_dir, git_common_dir=common)
        source, prior = resume_evidence(args.resume_from, out, identity, head, args.relay, model)
        thread_id = prior['thread_id']
        prior_token = prior.get('worktree_token')
    token = register_attempt(git_dir, out, source, prior_token)
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    saved_prompt = out / 'prompt.txt'
    saved_prompt.write_bytes(prompt_bytes)
    log = out / 'implementation.jsonl'
    stderr = out / 'implementation.stderr.txt'
    evidence = dict(relay=args.relay, provider='codex', requested_model=model, requested_effort=effort,
                    model_evidence='requested_only',
                    cli_version=None, prompt_sha256=digest(saved_prompt), workspace=str(workspace),
                    git_dir=str(git_dir), git_common_dir=str(common), worktree_token=token,
                    thread_id=None, resumed_from=str(source) if source is not None else None,
                    requested_thread_id=thread_id,
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
        argv = [cli, 'exec', '--json', '--sandbox', 'workspace-write']
        model_args = ['--model', model]
        if effort is not None:
            model_args += ['-c', 'model_reasoning_effort=' + json.dumps(effort)]
        if source is None:
            argv += [*model_args, '-C', str(workspace), '--', payload]
        else:
            argv += ['-C', str(workspace), 'resume', *model_args, '--', thread_id, payload]
        code = execute(argv, workspace, log, timeout, idle, CAP, stderr_log=stderr)
        evidence['exit_code'] = code
        if source is not None and code:
            message = ('Error: thread/resume: thread/resume failed: '
                       f'no rollout found for thread id {thread_id} (code -32600)')
            if log.stat().st_size == 0:
                lines = [line for line in stderr.read_bytes().splitlines() if line.strip()]
                if lines == [message.encode('utf-8')]:
                    raise RunError(message, kind='session_not_found')
            raise RunError(f'Codex resume exited with code {code}')
        evidence.update(validate(log, code, stderr_log=stderr, expected_thread_id=thread_id),
                        status='completed', completed=True)
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
    parser.add_argument('--resume-from', type=Path, help='output directory of the last completed attempt')
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
