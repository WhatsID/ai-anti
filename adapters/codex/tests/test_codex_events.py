"""Replay sanitized CLI captures and exercise the boundaries of each exception."""
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

TESTS = Path(__file__).resolve().parent
FIXTURES = TESTS / 'fixtures/cli_20261118'
APPLY_PATCH_STDERR = FIXTURES / 'codex-implement-apply-patch.stderr.txt'
WRITE_STDIN_STDERR = FIXTURES / 'codex-implement-write-stdin.stderr.txt'
RECONNECT_IO_EVENT = {
    'type': 'error',
    'message': 'Reconnecting... 2/5 (stream disconnected before completion: '
               'IO error: peer closed connection without sending TLS close_notify: '
               'https://docs.rs/rustls/latest/rustls/manual/_03_howto/index.html#unexpected-eof)',
}
sys.path[:0] = [str(TESTS.parent / 'skills' / skill / 'scripts') for skill in ('claude-review', 'codex-relay')]

from implementation_protocol import validate as validate_implementation
from review_codex_protocol import validate as validate_responses
from review_retry import direct_result, readonly
from review_runner import RunError, execute, validate as validate_claude


def events(name):
    return [json.loads(line) for line in (FIXTURES / (name + '.jsonl')).read_text().splitlines()]


class CliReplay(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.log = self.root / 'events.jsonl'

    def write_events(self, data):
        self.log.write_text(''.join(json.dumps(event) + '\n' for event in data), encoding='utf-8')
        return self.log

    def direct(self, data, code=0, stderr=None):
        return direct_result(self.write_events(data), code, 'codex-jsonl', stderr)

    def test_implementation_returns_unique_thread_id_and_matches_expected(self):
        thread = {'type': 'thread.started', 'thread_id': 'fixture-thread'}
        log = self.write_events([
            thread, {'type': 'turn.started'},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'done'}},
            {'type': 'turn.completed'},
        ])
        self.assertEqual(validate_implementation(log, 0)['thread_id'], 'fixture-thread')
        self.assertEqual(validate_implementation(log, 0, expected_thread_id='fixture-thread')['thread_id'],
                         'fixture-thread')
        with self.assertRaises(RunError) as caught:
            validate_implementation(log, 0, expected_thread_id='wrong-thread')
        self.assertEqual(caught.exception.kind, 'session_mismatch')

    def test_implementation_checks_thread_position_and_uniqueness_only_on_resume(self):
        thread = {'type': 'thread.started', 'thread_id': 'fixture-thread'}
        started = {'type': 'turn.started'}
        final = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'done'}}
        completed = {'type': 'turn.completed'}
        for label, prefix in (
            ('duplicate', [thread, thread, started]),
            ('late', [started, thread]),
            ('missing', [started]),
            ('invalid', [{**thread, 'thread_id': '-option'}, started]),
        ):
            with self.subTest(label=label):
                log = self.write_events([*prefix, final, completed])
                result = validate_implementation(log, 0)
                self.assertEqual(result['result'], 'done')
                self.assertIsNone(result['thread_id'])
                with self.assertRaises(RunError) as caught:
                    validate_implementation(log, 0, expected_thread_id='fixture-thread')
                self.assertEqual(caught.exception.kind, 'session_mismatch')

    def test_both_original_codex_reviews(self):
        expected = [
            ('codex-review-a', 'Redacted message item_26', ['item_16', 'item_22'], 2, 18),
            ('codex-review-b', 'Redacted message item_16', [], 0, 16),
        ]
        for name, final, failed, budget_count, stderr_count in expected:
            with self.subTest(fixture=name):
                result = self.direct(events(name), stderr=FIXTURES / (name + '.stderr.txt'))
                self.assertEqual(result['result'], final)
                warnings = result['transport_warnings']
                self.assertEqual([w for w in warnings if w['kind'] == 'failed_command'],
                                 [{'kind': 'failed_command', 'id': uid, 'exit_code': 1,
                                   'command': 'fixture-command ' + uid} for uid in failed])
                self.assertEqual(sum(w['kind'] == 'project_doc_budget' for w in warnings), budget_count)
                self.assertEqual(sum(w['count'] for w in warnings if w.get('stream') == 'stderr'), stderr_count)

    def test_failed_command_permission_denial(self):
        data = events('codex-review-a')
        failed = next(e['item'] for e in data if e.get('item', {}).get('status') == 'failed')
        for marker in ('Operation not permitted', 'Permission DENIED', 'Read-only file system',
                       'Sandbox', 'not permitted', 'Denied by'):
            with self.subTest(marker=marker):
                failed['aggregated_output'] = 'shell: ' + marker
                with self.assertRaisesRegex(RunError, 'failed direct command'):
                    self.direct(data)

    def test_command_exit_and_lifecycle_guards(self):
        data = events('codex-review-a')
        index = next(i for i, e in enumerate(data) if e.get('item', {}).get('status') == 'failed')
        for key, values in [('exit_code', (0, True, '1', None)), ('status', ('completed', 'unknown'))]:
            for value in values:
                with self.subTest(key=key, value=value):
                    sample = copy.deepcopy(data)
                    sample[index]['item'][key] = value
                    with self.assertRaises(RunError):
                        self.direct(sample)
        self.assertEqual(data[index - 1]['item']['id'], data[index]['item']['id'])
        with self.assertRaisesRegex(RunError, 'unmatched direct tool result'):
            self.direct(data[:index - 1] + data[index:])
        data[index]['item']['command'] = 'x' * 205
        result = self.direct(data)
        self.assertEqual(next(w['command'] for w in result['transport_warnings']
                              if w['kind'] == 'failed_command'), 'x' * 200)

    def test_budget_warning_requires_verbatim_shape_and_preturn(self):
        original = events('codex-review-a')
        for index in (1, 2):
            with self.subTest(index=index):
                sample = copy.deepcopy(original)
                notice = sample[index]['item']
                self.assertEqual(notice['type'], 'error')
                notice['message'] += '!'
                with self.assertRaises(RunError):
                    self.direct(sample)
                sample = copy.deepcopy(original)
                sample[index]['item']['extra'] = True
                with self.assertRaises(RunError):
                    self.direct(sample)
        sample = copy.deepcopy(original)
        notice = sample.pop(1)
        started = next(i for i, e in enumerate(sample) if e['type'] == 'turn.started')
        sample.insert(started + 1, notice)
        with self.assertRaisesRegex(RunError, 'unknown direct item'):
            self.direct(sample)

    def test_original_implementation_reconnect_and_file_changes(self):
        data = events('codex-implement')
        stderr = FIXTURES / 'codex-implement.stderr.txt'
        result = validate_implementation(self.write_events(data), 0, stderr_log=stderr)
        self.assertEqual(result['result'], 'Redacted message item_130')
        self.assertEqual(sum(w.get('kind') == 'reconnect' for w in result['warnings']), 1)
        self.assertEqual(sum(w.get('kind') == 'responses_websocket' for w in result['warnings']), 1)
        file_change = next(e for e in data if e.get('item', {}).get('type') == 'file_change')
        review = events('codex-review-b')
        review.insert(2, file_change)
        with self.assertRaisesRegex(RunError, 'unknown direct item'):
            self.direct(review)

    def test_original_implementation_remote_mcp_stderr(self):
        stderr = FIXTURES / 'codex-implement-rmcp.stderr.txt'
        result = validate_implementation(self.write_events(events('codex-implement')), 0, stderr_log=stderr)
        self.assertIn({'kind': 'remote_mcp_unavailable', 'stream': 'stderr', 'count': 1}, result['warnings'])

    def test_implementation_replays_apply_patch_stderr(self):
        log = self.write_events(events('codex-implement'))
        result = validate_implementation(log, 0, stderr_log=APPLY_PATCH_STDERR)
        self.assertEqual(result['result'], 'Redacted message item_130')
        self.assertIn({'kind': 'apply_patch_verification', 'stream': 'stderr', 'count': 1,
                       'context_lines': 1}, result['warnings'])

    def test_implementation_replays_write_stdin_stderr(self):
        log = self.write_events(events('codex-implement'))
        result = validate_implementation(log, 0, stderr_log=WRITE_STDIN_STDERR)
        self.assertEqual(result['result'], 'Redacted message item_130')
        self.assertIn({'kind': 'write_stdin_unknown_process', 'stream': 'stderr', 'count': 2}, result['warnings'])

    def test_review_and_implementation_replay_reconnect_io(self):
        warning = {'kind': 'reconnect', 'message': RECONNECT_IO_EVENT['message']}
        review = events('codex-review-b')
        started = next(i for i, event in enumerate(review) if event['type'] == 'turn.started')
        review.insert(started + 1, RECONNECT_IO_EVENT)
        result = self.direct(review)
        self.assertEqual(result['result'], 'Redacted message item_16')
        self.assertIn(warning, result['transport_warnings'])

        implementation = events('codex-implement')
        reconnect = next(event for event in implementation if event['type'] == 'error')
        reconnect['message'] = RECONNECT_IO_EVENT['message']
        result = validate_implementation(self.write_events(implementation), 0)
        self.assertEqual(result['result'], 'Redacted message item_130')
        self.assertIn(warning, result['warnings'])

    def test_readonly_rejects_implementation_stderr(self):
        review = events('codex-review-b')
        for stderr in (APPLY_PATCH_STDERR, WRITE_STDIN_STDERR):
            with self.subTest(fixture=stderr.name):
                with self.assertRaisesRegex(RunError, 'unknown Codex stderr line'):
                    self.direct(review, stderr=stderr)

    def test_apply_patch_counts_consecutive_blocks_and_blank_context(self):
        notice, header, context = APPLY_PATCH_STDERR.read_text().splitlines()
        _, write_stdin, _ = WRITE_STDIN_STDERR.read_text().splitlines()
        stderr = self.root / 'stderr.txt'
        lines = [notice, header, context, '', 'old line', header, 'other old line', write_stdin, notice]
        stderr.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        log = self.write_events(events('codex-implement'))
        result = validate_implementation(log, 0, stderr_log=stderr)
        self.assertEqual([w for w in result['warnings'] if w.get('stream') == 'stderr'], [
            {'kind': 'stdin_notice', 'stream': 'stderr', 'count': 2},
            {'kind': 'apply_patch_verification', 'stream': 'stderr', 'count': 2, 'context_lines': 4},
            {'kind': 'write_stdin_unknown_process', 'stream': 'stderr', 'count': 1},
        ])

    def assert_rejects_implementation_stderr(self, variants):
        log = self.write_events(events('codex-implement'))
        stderr = self.root / 'stderr.txt'
        for case, lines in variants.items():
            with self.subTest(case=case):
                stderr.write_text('\n'.join(lines) + '\n', encoding='utf-8')
                with self.assertRaisesRegex(RunError, 'unknown Codex stderr line'):
                    validate_implementation(log, 0, stderr_log=stderr)

    def test_apply_patch_requires_nonblank_context(self):
        notice, header, _ = APPLY_PATCH_STDERR.read_text().splitlines()
        _, write_stdin, _ = WRITE_STDIN_STDERR.read_text().splitlines()
        self.assert_rejects_implementation_stderr({
            'eof_after_header': [header],
            'tracing_after_header': [header, write_stdin],
            'stdin_notice_after_header': [header, notice],
            'blank_context': [header, '', ' \t'],
        })

    def test_apply_patch_rejects_other_headers(self):
        _, header, context = APPLY_PATCH_STDERR.read_text().splitlines()
        headers = {
            'relative_path': header.replace('/redacted/', 'redacted/'),
            'missing_colon': header[:-1],
            'other_apply_patch_error': header.replace('Failed to find expected lines', 'Failed to read file'),
            'missing_timestamp': header.split(' ', 1)[1],
        }
        self.assert_rejects_implementation_stderr({case: [line, context] for case, line in headers.items()})

    def test_apply_patch_rejects_unknown_tracing_after_context(self):
        _, header, context = APPLY_PATCH_STDERR.read_text().splitlines()
        _, write_stdin, _ = WRITE_STDIN_STDERR.read_text().splitlines()
        stamp, _, _ = write_stdin.partition(' ERROR ')
        trailing = {
            'unknown_error': stamp + ' ERROR unknown note',
            'warn': write_stdin.replace(' ERROR ', ' WARN '),
            'invalid_date': write_stdin.replace('2026-11-20', '2026-99-20'),
        }
        self.assert_rejects_implementation_stderr({
            case: [header, context, line] for case, line in trailing.items()
        })

    def test_apply_patch_rejects_panic_and_diagnostic_context(self):
        notice, header, context = APPLY_PATCH_STDERR.read_text().splitlines()
        variants = {
            'panic': [header, "thread 'main' panicked at example.rs:1:"],
            'appended_warn': [notice, header, context, 'WARN unknown note'],
            'replacement_warn': [notice, header, 'WARN unknown note'],
        }
        for prefix in ('TRACE ', 'DEBUG ', 'INFO ', 'WARNING ', 'ERROR ', 'FATAL ',
                       'error[', 'warning:', 'Error:', 'Warning['):
            variants[prefix] = [header, context, prefix + 'unknown note']
        self.assert_rejects_implementation_stderr(variants)

    def test_write_stdin_rejects_invalid_pid_and_router_variants(self):
        _, line, _ = WRITE_STDIN_STDERR.read_text().splitlines()
        prefix, _ = line.rsplit(' ', 1)
        self.assert_rejects_implementation_stderr({
            'zero_pid': [prefix + ' 0'],
            'leading_zero_pid': [prefix + ' 053902'],
            'nonnumeric_pid': [prefix + ' abc'],
            'missing_timestamp': [line.split(' ', 1)[1]],
            'other_router_error': [line.replace('Unknown process id 53902', 'Process already exited')],
        })

    def test_implementation_stderr_does_not_bypass_terminal_guards(self):
        data = events('codex-implement')
        for stderr in (APPLY_PATCH_STDERR, WRITE_STDIN_STDERR):
            for case, code, sample in (('nonzero_exit', 1, data), ('missing_completion', 0, data[:-1])):
                with self.subTest(fixture=stderr.name, case=case):
                    with self.assertRaisesRegex(RunError, 'requires exit 0, one completed turn'):
                        validate_implementation(self.write_events(sample), code, stderr_log=stderr)

    def test_implementation_rejects_remote_mcp_stderr_variants(self):
        sample = (FIXTURES / 'codex-implement-rmcp.stderr.txt').read_text()
        line = next(line for line in sample.splitlines() if 'rmcp::transport::worker:' in line)
        stamp, _, message = line.partition(' ERROR ')
        log = self.write_events(events('codex-implement'))
        stderr = self.root / 'stderr.txt'
        variants = {
            'changed_url': line.replace('/ps/mcp)', '/ps/mcq)'),
            'missing_timestamp': 'ERROR ' + message,
            'other_error': stamp + ' ERROR rmcp::transport::worker: worker quit with fatal: Connection reset',
        }
        for case, variant in variants.items():
            with self.subTest(case=case):
                stderr.write_text(sample.replace(line, variant), encoding='utf-8')
                with self.assertRaisesRegex(RunError, 'unknown Codex stderr line'):
                    validate_implementation(log, 0, stderr_log=stderr)

    def test_reconnect_rejects_unobserved_reason(self):
        sample = events('codex-implement')
        reconnect = next(e for e in sample if e['type'] == 'error')
        reconnect['message'] = reconnect['message'].replace('tls handshake eof', 'request timed out')
        with self.assertRaisesRegex(RunError, 'implementation error'):
            validate_implementation(self.write_events(sample), 0)

    def test_reconnect_io_rejects_modified_url_and_counter(self):
        message = RECONNECT_IO_EVENT['message']
        for case, variant in (('changed_url', message.replace('docs.rs', 'docs-rs')),
                              ('last_attempt', message.replace('2/5', '5/5'))):
            with self.subTest(case=case):
                review = events('codex-review-b')
                started = next(i for i, event in enumerate(review) if event['type'] == 'turn.started')
                review.insert(started + 1, {**RECONNECT_IO_EVENT, 'message': variant})
                with self.assertRaisesRegex(RunError, 'unknown direct event'):
                    self.direct(review)
                implementation = events('codex-implement')
                reconnect = next(event for event in implementation if event['type'] == 'error')
                reconnect['message'] = variant
                with self.assertRaisesRegex(RunError, 'implementation error'):
                    validate_implementation(self.write_events(implementation), 0)

    def test_reconnect_requires_valid_counter_format_and_lifecycle(self):
        sample = events('codex-implement')
        reconnect = next(e for e in sample if e['type'] == 'error')
        message = reconnect['message']
        for bad in (message.replace('2/5', '5/5'), message[:-1], 'unrelated error'):
            with self.subTest(message=bad):
                reconnect['message'] = bad
                with self.assertRaisesRegex(RunError, 'implementation error'):
                    validate_implementation(self.write_events(sample), 0)
        reconnect['message'] = message
        review = events('codex-review-b')
        review.insert(2, reconnect)
        self.assertEqual(self.direct(review)['result'], 'Redacted message item_16')
        with self.assertRaisesRegex(RunError, 'missing direct terminal'):
            self.direct(review[:-1])
        with self.assertRaisesRegex(RunError, 'nonzero direct CLI exit'):
            self.direct(review, code=1)
        review[-1] = {'type': 'turn.failed', 'error': {'type': 'overloaded_error'}}
        with self.assertRaisesRegex(RunError, 'invalid direct failed turn') as caught:
            self.direct(review)
        self.assertEqual(caught.exception.kind, 'execution_failed')
        with self.assertRaisesRegex(RunError, 'requires exit 0'):
            validate_implementation(self.write_events(sample[:-1]), 0)

    def test_responses_reconnect_and_warning_keep_tool_guards(self):
        implementation = events('codex-implement')
        startup = implementation[:4]
        reconnect = next(e for e in implementation if e['type'] == 'error')
        final = events('codex-review-b')[-2:]
        registry = {'root': str(self.root), 'materials': {}}
        def validate(data):
            return validate_responses(self.write_events(data), 0, 'qwen4-max', self.root, [], registry)
        result = validate(startup + [reconnect] + final)
        self.assertEqual([w['kind'] for w in result['transport_warnings']],
                         ['project_doc_budget', 'project_doc_budget', 'reconnect'])
        for kind in ('file_change', 'command_execution', 'unknown'):
            item = {'type': 'item.completed', 'item': {'type': kind, 'id': 'x'}}
            with self.assertRaisesRegex(RunError, 'unexpected Codex item'):
                validate(startup + [item] + final)

    def test_unknown_codex_stderr_fails(self):
        stderr = self.root / 'stderr.txt'
        known = (FIXTURES / 'codex-review-a.stderr.txt').read_text()
        for extra in ['WARN unknown note', 'Reading additional input from stdin... ',
                      known.splitlines()[1].replace('2026-11-18', '2026-99-18'),
                      known.splitlines()[1].replace('request timed out', 'permission denied')]:
            with self.subTest(extra=extra):
                stderr.write_text(known + extra + '\n')
                with self.assertRaisesRegex(RunError, 'unknown Codex stderr line'):
                    self.direct(events('codex-review-a'), stderr=stderr)

    def test_readonly_retains_sidecar_and_attempt_stderr_hash(self):
        report = self.root / 'report.md'
        source = FIXTURES / 'codex-review-a.jsonl'
        stderr = FIXTURES / 'codex-review-a.stderr.txt'
        command = [sys.executable, '-c', 'from pathlib import Path; import sys; '
                   'sys.stdout.buffer.write(Path(sys.argv[1]).read_bytes()); '
                   'sys.stderr.buffer.write(Path(sys.argv[2]).read_bytes())', str(source), str(stderr)]
        result = readonly(command, self.root, self.log, report, 10, 3, 100000, 'codex-jsonl', [source, stderr])
        self.assertEqual(report.read_text(), 'Redacted message item_26')
        self.assertEqual(json.loads(report.with_name('report.md.warnings.json').read_text()),
                         result['transport_warnings'])
        attempt = json.loads(self.log.with_suffix('.attempts.json').read_text())['attempts'][0]
        self.assertEqual(attempt['stderr_log'], 'events.attempt1.jsonl.stderr.txt')
        self.assertEqual(attempt['stderr_sha256'], hashlib.sha256(stderr.read_bytes()).hexdigest())
        self.assertEqual((self.root / attempt['stderr_log']).read_bytes(), stderr.read_bytes())

    def test_split_streams_combined_cap_and_idle(self):
        stderr = self.root / 'stderr.txt'
        code = "import os; os.write(1, b'1234'); os.write(2, b'5678')"
        args = [sys.executable, '-c', code]
        self.assertEqual(execute(args, self.root, self.log, 5, 2, 8, stderr_log=stderr), 0)
        self.assertEqual((self.log.read_bytes(), stderr.read_bytes()), (b'1234', b'5678'))
        with self.assertRaisesRegex(RunError, 'log limit'):
            execute(args, self.root, self.log, 5, 2, 7, stderr_log=stderr)
        code = '''import os
import time
for _ in range(12):
    os.write(2, b'.')
    time.sleep(.1)
'''
        self.assertEqual(execute([sys.executable, '-c', code], self.root, self.log, 5, .8,
                                 100, stderr_log=stderr), 0)
        self.assertEqual(stderr.read_bytes(), b'.' * 12)
        self.assertEqual(self.log.read_bytes(), b'')

    def stream(self, data, model='opus'):
        return validate_claude(self.write_events(data), 0, model, self.root,
                               families={'haiku': re.compile(r'claude-haiku-[0-9]+-[0-9]+'),
                                         'opus': re.compile(r'claude-opus-[0-9]+-[0-9]+')})

    def test_three_original_claude_streams(self):
        for model, name in [('haiku', 'haiku'), ('opus', 'opus-thinking'), ('opus', 'opus-ui_invalidate')]:
            with self.subTest(fixture=name):
                result = self.stream(events('claude-stream-' + name), model)
                self.assertEqual(result['result'], 'redacted-result')
                self.assertIn({'kind': 'rate_limit_event', 'status': 'allowed'}, result['transport_warnings'])

    def thinking_tokens_stream(self):
        data = events('claude-stream-review-thinking_tokens')
        for event in data:
            for block in event.get('message', {}).get('content', []):
                if block['type'] == 'tool_use':
                    (self.root / block['input']['file_path']).write_text('', encoding='utf-8')
        return data

    def test_original_claude_thinking_tokens_stream(self):
        result = self.stream(self.thinking_tokens_stream())
        self.assertEqual(result['result'], 'redacted-result')
        self.assertEqual(len(result['complete_reads']), 5)
        self.assertEqual(result['transport_warnings'].count({'kind': 'thinking_tokens'}), 2)

    def test_claude_rejects_thinking_tokens_variants(self):
        data = self.thinking_tokens_stream()
        index = next(i for i, event in enumerate(data) if event.get('subtype') == 'thinking_tokens')
        before, event, after = data[:index], data[index], data[index + 1:]
        variants = {
            'extra_key': [*before, {**event, 'extra': True}, *after],
            'before_init': [event, *before, *after],
            'after_result': [*before, *after, event],
        }
        for key in ('estimated_tokens', 'estimated_tokens_delta'):
            missing = dict(event)
            del missing[key]
            variants[(key, 'missing')] = [*before, missing, *after]
            for value in (-1, 1.5, True, '1'):
                variants[(key, value)] = [*before, {**event, key: value}, *after]
        for case, sample in variants.items():
            with self.subTest(case=case):
                with self.assertRaisesRegex(RunError, 'unknown system event'):
                    self.stream(sample)

    def test_claude_stream_exact_ui_and_allowed_rate_limit(self):
        data = events('claude-stream-opus-ui_invalidate')
        ui, init, assistant, rate, result = data
        for sample in ([init, ui, assistant, rate, result], [ui, *data],
                       [{**ui, 'event': 'ui.other'}, *data[1:]],
                       [{**ui, 'extra': False}, *data[1:]],
                       [{**ui, 'subtype': 'thinking_tokens'}, *data[1:]],
                       [rate, *data], [ui, init, assistant, result, rate],
                       [ui, init, assistant, {**rate, 'extra': True}, result],
                       [ui, init, assistant, {**rate, 'rate_limit_info': {'status': 'throttled'}}, result]):
            with self.assertRaises(RunError):
                self.stream(sample)
        with self.assertRaises(RunError) as caught:
            self.stream([ui, init, assistant, {**rate, 'rate_limit_info': {'status': 'rejected'}}, result])
        self.assertEqual(caught.exception.kind, 'quota_exhausted')
        for plugin in ({'name': 'easel', 'path': 'builtin', 'source': 'easel@builtin'},
                       {'name': 'cc-plugin-easel', 'path': '/fixture', 'source': 'cc-plugin-easel@builtin'}):
            invalid = {**init, 'plugins': [plugin]}
            with self.assertRaisesRegex(RunError, 'unsafe or missing init capability set'):
                self.stream([ui, invalid, assistant, rate, result])

    def test_claude_connector_notice_and_success(self):
        normal = (FIXTURES / 'claude-json-anthropic.txt').read_text()
        prefix = (FIXTURES / 'claude-json-proxy404.txt').read_text().splitlines()[:2]
        for lines in ([], prefix, prefix[1:]):
            self.log.write_text('\n'.join(lines + [normal]))
            result = direct_result(self.log, 0, 'claude-json')
            self.assertEqual(result['result'], 'redacted-result')
            self.assertEqual(len(result['transport_warnings']), len(lines))
        for lines in ([prefix[0].replace('disabled', 'disableD'), prefix[1]],
                      [prefix[0], *prefix], list(reversed(prefix))):
            self.log.write_text('\n'.join(lines + [normal]))
            with self.assertRaises(RunError):
                direct_result(self.log, 0, 'claude-json')

    def test_original_claude_error_envelopes_still_do_not_retry(self):
        for name in ('proxy404', 'direct429'):
            with self.subTest(fixture=name):
                source = FIXTURES / ('claude-json-' + name + '.txt')
                log = self.root / (name + '.txt')
                report = self.root / (name + '.md')
                command = [sys.executable, '-c', 'import sys; from pathlib import Path; '
                           'sys.stdout.buffer.write(Path(sys.argv[1]).read_bytes()); sys.exit(1)', str(source)]
                with self.assertRaisesRegex(RunError, 'unsupported direct failure'):
                    readonly(command, self.root, log, report, 10, 3, 10000, 'claude-json', [source])
                attempts = json.loads(log.with_suffix('.attempts.json').read_text())
                self.assertEqual((attempts['attempt_count'], attempts['retry_count']), (1, 0))
                self.assertFalse(report.exists())
