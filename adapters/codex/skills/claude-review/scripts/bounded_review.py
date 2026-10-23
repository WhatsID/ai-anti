#!/usr/bin/env python3
"""Bound an external review CLI using the shared user budget and process runner."""
import argparse
from pathlib import Path
import sys
from ai_anti_config import ConfigError, load, review_limits
from review_runner import RunError, execute


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log', type=Path, required=True)
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
        return execute(command, Path.cwd(), args.log, timeout, idle, args.max_output_bytes)
    except (ConfigError, RunError, OSError) as exc:
        print('review failed: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
