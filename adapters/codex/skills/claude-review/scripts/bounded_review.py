#!/usr/bin/env python3
"""Bound an external review CLI using the shared user budget and process runner."""
import argparse
import json
import os
from pathlib import Path
import sys
from ai_anti_config import ConfigError, load, review_limits
from review_runner import RunError, execute
from review_retry import readonly
from review_channels import resolve, child_environment
from token_plan_transport import Transport, contract
from model_metadata import effort


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--token-plan-config', type=Path, help='explicit shared API configuration for legacy plugin')
    parser.add_argument('--log', type=Path, required=True)
    parser.add_argument('--retry-protocol', choices=('claude-json', 'codex-jsonl'))
    parser.add_argument('--report', type=Path)
    parser.add_argument('--input', type=Path, action='append', default=[])
    parser.add_argument('--max-retries', type=int, choices=(0, 1, 2), default=2)
    parser.add_argument('--timeout', type=int)
    parser.add_argument('--idle-timeout', type=int)
    parser.add_argument('--max-output-bytes', type=int, default=2 * 1024 * 1024)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    transport = None
    try:
        config = load(args.token_plan_config)
        contract(config, Path.cwd())
        timeout, idle = review_limits(config, args.timeout, args.idle_timeout)
        if not command or not 0 < args.max_output_bytes <= 8 * 1024 * 1024:
            raise ConfigError('command required; output cap must be in 1..8388608')
        print('review budgets: timeout=%s idle_timeout=%s' % (timeout, idle), file=sys.stderr)
        if args.token_plan_config:
            sidecar = args.log.with_name(args.log.name + '.accounts.json')
            if sidecar.exists() or sidecar.is_symlink(): raise ConfigError('account evidence output already exists')
        channel = resolve(config, 'qwen', None, Path.cwd(), {}, executor='claude') if args.token_plan_config else {}
        transport = Transport(channel, timeout + 60, idle)
        with transport:
            env = child_environment(channel) if channel else None
            if channel:
                command = [effort(channel['configured_model']) if arg == '__AIANTI_EFFORT__' else arg for arg in command]
            if args.retry_protocol:
                if args.report is None or not args.input:
                    raise ConfigError('retry protocol requires report and declared inputs')
                readonly(command, Path.cwd(), args.log, args.report, timeout, idle, args.max_output_bytes,
                         args.retry_protocol, args.input, args.max_retries, env=env)
                return 0
            if args.report or args.input:
                raise ConfigError('report/inputs require explicit retry protocol')
            return execute(command, Path.cwd(), args.log, timeout, idle, args.max_output_bytes, env=env)
    except (ConfigError, RunError, OSError) as exc:
        print('review failed: ' + str(exc), file=sys.stderr)
        return 1

    finally:
        if args.token_plan_config and transport:
            sidecar = args.log.with_name(args.log.name + '.accounts.json')
            # Exclusive creation prevents following a planted symlink.
            fd = os.open(sidecar, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump(transport.evidence(), out, ensure_ascii=False)
                out.write('\n')


if __name__ == '__main__':
    sys.exit(main())
