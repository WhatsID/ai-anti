#!/usr/bin/env python3
"""Bound an external review CLI using the shared user budget and process runner."""
import argparse
from pathlib import Path
import sys
from ai_anti_config import ConfigError, load, review_limits
from review_runner import RunError, execute
from review_retry import readonly


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
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
    try:
        timeout, idle = review_limits(load(), args.timeout, args.idle_timeout)
        if not command or not 0 < args.max_output_bytes <= 8 * 1024 * 1024:
            raise ConfigError('command required; output cap must be in 1..8388608')
        print('review budgets: timeout=%s idle_timeout=%s' % (timeout, idle), file=sys.stderr)
        if args.retry_protocol:
            if args.report is None or not args.input:
                raise ConfigError('retry protocol requires report and declared inputs')
            readonly(command, Path.cwd(), args.log, args.report, timeout, idle, args.max_output_bytes,
                     args.retry_protocol, args.input, args.max_retries)
            return 0
        if args.report or args.input:
            raise ConfigError('report/inputs require explicit retry protocol')
        return execute(command, Path.cwd(), args.log, timeout, idle, args.max_output_bytes)
    except (ConfigError, RunError, OSError) as exc:
        print('review failed: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
