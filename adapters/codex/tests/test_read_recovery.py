"""Exact missing-Read recovery with synthetic CLI events, never a live model."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import test_thin_review as thin

sys.path.insert(0, str(thin.ENTRY.parent))
from review_runner import RunError, validate

# Envelope transcribed from CLI 2.1.280's 2026-10-11 failure log. Runtime paths
# and ids are substituted for the isolated fixture; the original log is untouched.
MISSING = {'type': 'user', 'message': {'role': 'user', 'content': [
    {'type': 'tool_result', 'content': 'File does not exist. Note: your current working directory is <cwd>.',
     'is_error': True, 'tool_use_id': '<id>'}]},
    'tool_use_result': 'Error: File does not exist. Note: your current working directory is <cwd>.'}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.stage = self.root / 'stage'
        self.stage.mkdir()
        self.name = 'chunks/001/001.txt'
        (self.stage / self.name).parent.mkdir(parents=True)
        (self.stage / self.name).write_text('one\ntwo\n')
        (self.stage / 'other.txt').write_text('other')
        self.log = self.root / 'events.jsonl'
        self.init = {'type': 'system', 'subtype': 'init', 'model': 'claude-opus-5',
                     'tools': ['Read', 'Glob', 'Grep'], 'mcp_servers': [], 'plugins': [],
                     'permissionMode': 'dontAsk', 'skills': [], 'slash_commands': []}
        self.final = {'type': 'result', 'subtype': 'success', 'is_error': False,
                      'permission_denials': [], 'result': 'review'}

    def use(self, uid, path, tool='Read'):
        return {'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'name': tool, 'id': uid, 'input': {'file_path': path}}]}}

    def missing(self, uid):
        event = copy.deepcopy(MISSING)
        message = event['message']['content'][0]['content'].replace('<cwd>', str(self.stage))
        event['message']['content'][0].update(content=message, tool_use_id=uid)
        event['tool_use_result'] = 'Error: ' + message
        return event

    def read(self, uid='good', path=None, start=1, count=None):
        path = self.name if path is None else path
        lines = (self.stage / path).read_text().split('\n')
        count = len(lines) if count is None else count
        return [self.use(uid, path), {'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'tool_use_id': uid, 'content': 'rendered', 'is_error': False}]},
            'tool_use_result': {'type': 'text', 'file': {'filePath': path,
                'content': '\n'.join(lines[start - 1:start - 1 + count]),
                'startLine': start, 'numLines': count, 'totalLines': len(lines)}}}]

    def errors(self, count):
        events = []
        for index in range(count):
            uid = 'typo-' + str(index)
            events.extend([self.use(uid, 'chunks/099/00' + str(index) + '.txt'), self.missing(uid)])
        return events

    def check(self, events, required=True, init=None, code=0):
        self.log.write_text('\n'.join(json.dumps(event) for event in [init or self.init, *events, self.final]))
        return validate(self.log, code, 'opus', self.stage,
                        required=[self.name] if required else None)

    def test_one_to_three_errors_and_exact_plain_message(self):
        for count in range(1, 4):
            events = self.errors(count)
            if count == 1:
                events[1]['message']['content'][0]['content'] = 'File does not exist.'
                events[1]['tool_use_result'] = 'Error: File does not exist.'
            result = self.check(events + self.read())
            self.assertEqual(result['read_recovery_count'], count)
            self.assertEqual(result['complete_reads'], {self.name})
            self.assertEqual(result['transport_warnings'], [])
            for item in result['read_recoveries']:
                self.assertEqual(item['corrected_path'], self.name)
                self.assertEqual(item['corrected_tool_id'], 'good')

    def test_budget_is_cumulative_across_successes(self):
        events = []
        for index in range(4):
            uid = 'typo-' + str(index)
            events += [self.use(uid, 'missing-' + str(index)), self.missing(uid)]
            events += self.read('good-' + str(index))
        with self.assertRaises(RunError):
            self.check(events)

    def test_unresolved_and_unread_default_strict(self):
        for events, required in [(self.errors(1), True),
                                 (self.errors(1) + self.read(path='other.txt'), True),
                                 (self.errors(1) + self.read(), False),
                                 (self.errors(1) + self.read(count=1), True)]:
            with self.subTest(events=events), self.assertRaises(RunError):
                self.check(events, required)

    def test_prior_coverage_cannot_correct_with_partial_or_zero_read(self):
        for count in (0, 1):
            with self.subTest(count=count), self.assertRaisesRegex(RunError, 'uncorrected'):
                self.check(self.read('before') + self.errors(1) + self.read('after', count=count))
        # Ordinary partial Reads still accumulate coverage when no recovery is needed.
        self.check(self.read('a', count=1) + self.read('b', start=2, count=2))

    def test_empty_required_file_recovers(self):
        (self.stage / self.name).write_text('')
        events = self.read(count=0)
        events[-1]['tool_use_result']['file']['totalLines'] = 0
        self.assertEqual(self.check(self.errors(1) + events)['read_recovery_count'], 1)

    def test_empty_read_forged_content_or_start_fails(self):
        (self.stage / self.name).write_text('')
        for field, value in (('content', 'forged'), ('startLine', 0), ('startLine', 2)):
            events = self.read(count=0)
            events[-1]['tool_use_result']['file']['totalLines'] = 0
            events[-1]['tool_use_result']['file'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(RunError):
                self.check(self.errors(1) + events)

    def test_existing_traversal_outside_and_symlink_rejected(self):
        (self.stage / 'link').symlink_to(self.root)
        (self.stage / 'dangling').symlink_to(self.root / 'absent')
        (self.stage / 'inside-link').symlink_to(self.stage / 'chunks')
        for path in (self.name, '.', '../absent', str(self.root / 'absent'),
                     'link/absent', 'dangling', 'inside-link/absent', '', 7, None):
            with self.subTest(path=path), self.assertRaises(RunError):
                self.check([self.use('bad', path), self.missing('bad')] + self.read())

    def test_error_envelope_and_unique_id_strict(self):
        cases = []
        for message in ('Permission denied.', 'File does not exist. extra',
                        'prefix File does not exist.', 'File does not exist. Note: your current working directory is wrong.'):
            event = self.missing('bad')
            event['message']['content'][0]['content'] = message
            event['tool_use_result'] = 'Error: ' + message
            cases.append([self.use('bad', 'missing'), event])
        event = self.missing('bad'); event['tool_use_result'] = {'type': 'text'}
        cases.append([self.use('bad', 'missing'), event])
        event = self.missing('bad'); event['type'] = 'assistant'
        cases.append([self.use('bad', 'missing'), event])
        event = self.missing('bad'); event['message']['content'][0]['is_error'] = 1
        cases.append([self.use('bad', 'missing'), event])
        cases.extend([[self.missing('unknown')],
                      [self.use('bad', 'missing', 'Grep'), self.missing('bad')],
                      [self.use('bad', 'missing'), self.use('bad', 'missing'), self.missing('bad')],
                      [self.use('bad', 'missing'), self.missing('bad'), self.missing('bad')]])
        for events in cases:
            with self.subTest(events=events), self.assertRaises(RunError):
                self.check(events + self.read())

    def test_success_must_bind_unique_result_and_real_content(self):
        for mutation in ('multiple', 'wrong_id', 'text', 'path', 'range', 'metadata', 'duplicate'):
            events = self.read()
            result = events[-1]
            file = result['tool_use_result']['file']
            if mutation == 'multiple':
                events.insert(0, self.use('grep', 'other.txt', 'Grep'))
                result['message']['content'].insert(0, {'type': 'tool_result', 'tool_use_id': 'grep'})
            elif mutation == 'wrong_id': result['message']['content'][0]['tool_use_id'] = 'other-id'
            elif mutation == 'text': file['content'] = 'forged'
            elif mutation == 'path': file['filePath'] = 'other.txt'
            elif mutation == 'range': file['totalLines'] = 7
            elif mutation == 'metadata': result.pop('tool_use_result')
            else: events.append(copy.deepcopy(result))
            with self.subTest(mutation=mutation), self.assertRaises(RunError):
                self.check(self.errors(1) + events)

    def test_model_exit_and_permission_gates_preserved(self):
        for kind in ('model', 'exit', 'permission'):
            init = dict(self.init)
            events = self.errors(1) + self.read()
            if kind == 'model': init['model'] = 'qwen'
            if kind == 'permission': events[-1]['permission_denials'] = ['Read']
            with self.subTest(kind=kind), self.assertRaises(RunError):
                self.check(events, init=init, code=1 if kind == 'exit' else 0)


if __name__ == '__main__':
    unittest.main()
