#!/usr/bin/env python3
"""Bounded implementation; primary owns authorization, scope gates and acceptance.

Only prepared isolated workspaces are supported. No write retry or landing occurs.
The shell sandbox is not a hard boundary against same-user credential file reads.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import time

from implementation_config import ConfigError, MODEL, public, resolve
import implementation_qoder
from ai_anti_config import load, review_limits
from review_channels import child_environment
from review_codex import capability
from review_runner import RunError, execute
from implementation_protocol import validate

CAP = 8 * 1024 * 1024
PATH = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
DISABLED = ('apps', 'hooks', 'goals', 'view_image', 'browser_use', 'computer_use',
            'shell_snapshot', 'multi_agent', 'js_repl', 'skill_search', 'tool_suggest')
INSTRUCTIONS = '''You are the sole delegated implementation worker. Follow the supplied
settled request, plan, adjudication, scope, frozen baseline and allowed commands.
Work only in the supplied isolated workspace and authorized materials. Do not read
credentials or unrelated files, start relays, agents, reviews, skills or network
operations. Do not commit, push, install or deploy. Report exact changed paths,
tests, failures and limitations. Stop on unresolved permission/environment errors.
Never claim actual server model identity or primary acceptance. The primary owns
independent acceptance. Do not retry this task in another process or model.'''


def configuration(route, instructions):
    options = {
        'model_provider': 'ai_anti_implementation', 'model': MODEL,
        'model_reasoning_effort': 'medium',
        'model_providers.ai_anti_implementation.name': 'Token Plan implementation',
        'model_providers.ai_anti_implementation.base_url': route['base_url'],
        'model_providers.ai_anti_implementation.wire_api': 'responses',
        'model_providers.ai_anti_implementation.env_key': 'AI_ANTI_QWEN_TOKEN',
        'model_providers.ai_anti_implementation.request_max_retries': 0,
        'model_providers.ai_anti_implementation.stream_max_retries': 0,
        'model_providers.ai_anti_implementation.supports_websockets': False,
        'approval_policy': 'never', 'project_doc_max_bytes': 0,
        'model_instructions_file': str(instructions), 'web_search': 'disabled',
        'features.skip_host_skill_discovery': True,
        'shell_environment_policy.inherit': 'none',
        'shell_environment_policy.set.PATH': PATH,
        'sandbox_workspace_write.network_access': False,
        'suppress_unstable_features_warning': True,
    }
    options.update({'features.' + name: False for name in DISABLED})
    return options


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.chmod(path, 0o600)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def run(args):
    workspace = args.workspace.resolve(strict=True)
    prompt = args.prompt.resolve(strict=True)
    if args.out.exists() or args.out.is_symlink():
        raise FileExistsError('implementation output already exists')
    out = args.out.resolve()
    if not workspace.is_dir() or not prompt.is_file():
        raise ConfigError('workspace directory and regular prompt file are required')
    if out == workspace or workspace in out.parents:
        raise ConfigError('output must be outside the implementation workspace')
    config = load(args.config)
    route = resolve(config, workspace, args.provider, args.model)
    limits = review_limits(config, args.timeout, args.idle_timeout)
    # Native model dispatch remains a host tool operation, never a CLI fallback.
    if route['provider'] == 'codex' and not args.prepare_only:
        raise ConfigError('Codex native implementation must use host spawn_agent, not this CLI')
    out.mkdir(mode=0o700, parents=True, exist_ok=False)
    instructions = out / 'instructions.txt'
    instructions.write_text(INSTRUCTIONS, encoding='utf-8')
    payload = prompt.read_text('utf-8')
    (out / 'prompt.txt').write_text(payload, encoding='utf-8')
    options = configuration(route, instructions) if route['provider'] == 'token_plan' else None
    if options is not None:
        write(out / 'codex-config.json', options)
    evidence = dict(public(route), actual_model=None, model_evidence='requested_only' if options else 'native_receipt_required',
                    status='prepared', completed=False, accepted=False, workspace=str(workspace),
                    prompt_sha256=digest(out / 'prompt.txt'), cli_version=None,
                    limits=dict(timeout=limits[0], idle_timeout=limits[1], max_output_bytes=CAP),
                    isolation=dict(sandbox='workspace-write', network_access=False,
                                   shell_environment='none; fixed PATH', hard_credential_file_isolation=False),
                    warnings=[], duration_seconds=0)
    if route['provider'] == 'qoder':
        evidence.update(implementation_qoder.prepare(out, INSTRUCTIONS))
    write(out / 'manifest.json', evidence)
    write(out / 'status.json', evidence)
    if args.prepare_only:
        return evidence
    started = time.monotonic()
    log = out / 'implementation.jsonl'
    try:
        if route['provider'] == 'qoder':
            evidence.update(implementation_qoder.run(args.cli, workspace, out, payload, INSTRUCTIONS, limits, CAP, evidence), status='completed', completed=True)
        else:
            cli = args.cli or shutil.which('codex')
            if not cli:
                raise RunError('Codex CLI unavailable')
            home = out / 'codex-home'
            home.mkdir(mode=0o700)
            # Reuse credential-key resolution, then pass only a small explicit env.
            authenticated = child_environment(route)
            allowed = ('HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                       'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
                       'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE')
            env = {key: authenticated[key] for key in allowed if key in authenticated}
            env.update(PATH=PATH, CODEX_HOME=str(home), AI_ANTI_QWEN_TOKEN=authenticated['AI_ANTI_QWEN_TOKEN'])
            cap = capability(cli, out, env=env)
            evidence.update(cli_version=cap['cli_version'], capability=cap)
            argv = [cli, '--no-daemon', 'exec', '--ignore-user-config', '--ignore-rules',
                    '--ephemeral', '--skip-git-repo-check', '--sandbox', 'workspace-write',
                    '--json', '-C', str(workspace)]
            for key, value in options.items():
                argv += ['-c', key + '=' + json.dumps(value)]
            argv += [payload]
            code = execute(argv, workspace, log, *limits, CAP, env=env)
            evidence['exit_code'] = code
            evidence.update(validate(log, code), status='completed', completed=True)
    except (RunError, ConfigError, OSError, UnicodeError, KeyboardInterrupt) as exc:
        # Raw provider output stays on disk; never echo credentials from errors.
        evidence.update(status='failed', error_kind=getattr(exc, 'kind', 'execution_failed'),
                        error='Implementation failed; inspect retained local logs and partial workspace before any new run')
    finally:
        evidence.update(duration_seconds=round(time.monotonic() - started, 3), log_sha256=digest(log))
        write(out / 'manifest.json', evidence)
        write(out / 'status.json', evidence)
    return evidence


def interrupt(signum, frame):
    raise KeyboardInterrupt('implementation interrupted')


def main():
    signal.signal(signal.SIGTERM, interrupt)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--prompt', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--provider', choices=('codex', 'token_plan', 'qoder'))
    parser.add_argument('--model')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--cli', help='explicit provider executable (also supports offline fake fixtures)')
    parser.add_argument('--timeout', type=int)
    parser.add_argument('--idle-timeout', type=int)
    args = parser.parse_args()
    try:
        result = run(args)
        print(json.dumps(dict(status=result['status'], completed=result['completed'], accepted=False)))
        return 0 if result['status'] in ('prepared', 'completed') else 1
    except (ConfigError, OSError, UnicodeError) as exc:
        print('implementation preparation failed: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
