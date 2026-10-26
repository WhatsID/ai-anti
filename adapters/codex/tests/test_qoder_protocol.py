"""Raw Qoder wire mutations, including independently forged text copies."""
import copy
import json
from pathlib import Path
import sys
import unittest
import test_material_ids as materials

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/claude-review/scripts'
sys.path.insert(0, str(SCRIPTS))
from review_qoder_protocol import validate
from review_runner import RunError, validate as claude_validate
from review_materials import TOOL, read_material, build_registry
from review_chunks import chunk_stage
from review_material import MaterialError


class QoderProtocolTests(materials.MaterialIds):
    def events(self):
        events = super().events()
        events[0].update(model='Kimi-K3.1-Pro', qodercli_version='1.1.77', plugins=[],
                         slash_commands=['help'], agents=['Explore'])
        events[0]['mcp_servers'] = [{'name': 'materials', 'status': 'connected'}]
        for event in events:
            if event['type'] == 'assistant':
                event['message']['model'] = 'Kimi-K3.1-Pro'
            if event['type'] == 'user':
                raw = event['tool_use_result'][0]['text']
                event['message']['content'][0]['content'] = raw or '(' + TOOL + ' completed with no output)'
        events[-1].update(stop_reason='end_turn', total_credits=.1)
        return events

    def check(self, events, code=0):
        self.log.write_text(''.join(json.dumps(e) + '\n' for e in events))
        return validate(self.log, code, 'kmodel_latest', self.stage,
                        expected_model='Kimi-K3.1-Pro', required=[v['path'] for v in
                        self.registry['materials'].values()], registry=self.registry)

    def test_retryable_terminal_after_safe_prefix(self):
        events = self.events()[:-1]
        events.append(dict(type='result', subtype='error_during_execution', is_error=True,
                           error_code=10500, errors=['Internal server error.']))
        with self.assertRaises(RunError) as caught:
            self.check(events, code=1)
        self.assertEqual(caught.exception.kind, 'retryable_upstream')

    def test_bad_prefix_and_synthetic_cannot_borrow_terminal_error(self):
        failure = dict(type='result', subtype='error_during_execution', is_error=True,
                       error_code=10500, errors=['Internal server error.'], permission_denials=[])
        prefix = self.events()[:-1]
        synthetic = dict(type='assistant', message=dict(model='<synthetic>', stop_reason='stop_sequence',
                         content=[dict(type='text', text='Internal server error.')]))
        good = prefix + [synthetic, failure]
        self.log.write_text(''.join(json.dumps(e) + '\n' for e in good))
        with self.assertRaises(RunError) as caught:self.check(good,1)
        self.assertEqual(caught.exception.kind, 'retryable_upstream')
        cases=[]
        for mutate in ('init', 'model', 'text', 'block_error', 'terminal_error', 'after', 'unknown'):
            events=copy.deepcopy(good)
            if mutate=='init':events[0]['tools'].append('Bash')
            if mutate=='model':events[1]['message']['model']='other'
            if mutate=='text':events[-2]['message']['content'][0]['text']='different'
            if mutate=='block_error':events[-2]['message']['content'][0]['is_error']=True
            if mutate=='terminal_error':events[-1]['error']={'type':'permission_denied'}
            if mutate=='after':events.append(dict(type='system',subtype='artifacts_update',artifacts=[]))
            if mutate=='unknown':events.insert(-2,dict(type='unknown'))
            cases.append(events)
        for events in cases:
            with self.assertRaises(RunError) as caught:self.check(events,1)
            self.assertNotEqual(caught.exception.kind,'retryable_upstream')
        quota=copy.deepcopy(good);quota[-1]['errors']=['insufficient_quota'];quota[-2]['message']['content'][0]['text']='insufficient_quota'
        with self.assertRaises(RunError) as caught:self.check(quota,1)
        self.assertEqual(caught.exception.kind,'quota_exhausted')

    def queue(self, status='queued'):
        # Transport shape observed in the 1.1.77 failure; no business log content.
        return dict(type='system', subtype='model_queue_status', status=status,
                    request_id='request-a', request_set_id='set-a', model_key='kmodel_latest',
                    queue_type='p0', queue_count=0, wait_time_ms=0, queue_wait_elapsed_ms=0,
                    queue_max_wait_ms=3600000, service_available=True, uuid='event-a',
                    session_id='session-a')

    def queued_events(self):
        events = self.events()
        events[0]['session_id'] = 'session-a'
        events.insert(1, self.queue())
        ready = self.queue('ready'); ready['request_id'] = 'request-b'
        events.insert(4, ready)  # Between material reads; no pairing requirement.
        events.insert(-1, self.queue())
        return events

    def test_queue_notifications_preserve_material_reads(self):
        self.assertEqual(len(self.check(self.queued_events())['complete_reads']), 2)

    def test_queue_shape_version_session_and_original_guards(self):
        cases = []
        for key in self.queue():
            events = self.queued_events(); del events[1][key]; cases.append(events)
        for key, value in [('status', 'waiting'), ('type', 'notice'), ('model_key', 'other'),
                           ('queue_type', 'p1'), ('service_available', False),
                           ('service_available', 1), ('session_id', 'other'),
                           ('error', 'failed'), ('is_error', True),
                           ('permission_denials', ['Read']), ('payload', {})]:
            events = self.queued_events(); events[1][key] = value; cases.append(events)
        for key in ('queue_count', 'wait_time_ms', 'queue_wait_elapsed_ms', 'queue_max_wait_ms'):
            for value in (True, -1, '0', 0.0, None):
                events = self.queued_events(); events[1][key] = value; cases.append(events)
        for key in ('request_id', 'request_set_id', 'uuid', 'session_id'):
            for value in ('', '  ', 1, None):
                events = self.queued_events(); events[1][key] = value; cases.append(events)
        for version in ('1.1.74',):
            events = self.queued_events(); events[0]['qodercli_version'] = version; cases.append(events)
        for value in ('', None, 1):
            events = self.queued_events(); events[0]['session_id'] = value; cases.append(events)
        events = self.queued_events(); del events[0]['session_id']; cases.append(events)
        events = self.queued_events(); events.insert(0, events.pop(1)); cases.append(events)
        events = self.queued_events(); events.append(self.queue()); cases.append(events)
        events = self.queued_events(); events.insert(2, copy.deepcopy(events[0])); cases.append(events)
        events = self.queued_events(); events.insert(2, dict(type='system', subtype='hook_started')); cases.append(events)
        events = self.queued_events(); del events[2:4]; cases.append(events)  # Missing material.
        events = self.queued_events(); events[2]['message']['model'] = 'Other'; cases.append(events)
        events = self.queued_events(); events.pop(); cases.append(events)
        for events in cases:
            with self.subTest(events=events[:3]), self.assertRaises(RunError): self.check(events)
        with self.assertRaises(RunError): self.check(self.queued_events(), code=1)

    def test_explicit_versions_and_invalid_init_versions(self):
        for version in ('1.1.74', '1.1.77'):
            events = self.events(); events[0]['qodercli_version'] = version
            self.assertEqual(self.check(events)['model'], 'Kimi-K3.1-Pro')
        for version in (None, 1177, [], '1.1.78', '', '1.1.77\nnoise'):
            events = self.events(); events[0]['qodercli_version'] = version
            with self.subTest(version=version), self.assertRaises(RunError): self.check(events)
        events = self.events(); del events[0]['qodercli_version']
        with self.assertRaises(RunError): self.check(events)

    def test_raw_strings_empty_unicode_and_repeated_material(self):
        events = self.events()
        self.assertEqual(len(self.check(events)['complete_reads']), 2)
        pair = copy.deepcopy(events[1:3])
        pair[0]['message']['content'][0]['id'] = 'again'
        pair[1]['message']['content'][0]['tool_use_id'] = 'again'
        events[-1:-1] = pair
        self.assertEqual(len(self.check(events)['complete_reads']), 2)

    def material_events(self, raw):
        # Freeze an independent byte sample, then derive ordinary read envelopes.
        self.stage = self.root / ('sample-' + str(len(list(self.root.glob('sample-*')))))
        self.stage.mkdir()
        (self.stage / 'unicode.txt').write_bytes('中文\n\n'.encode('utf-8'))
        (self.stage / 'empty.txt').write_bytes(b'')
        (self.stage / 'sample.txt').write_bytes(raw.encode('utf-8'))
        self.registry = build_registry(self.stage, chunk_stage(self.stage), self.path)
        events = self.events()
        result = next(e for e in events if e.get('type') == 'user'
                      and e['tool_use_result'] == [{'type': 'text', 'text': raw}])
        return events, result

    def test_lf_only_wrapper_and_direct_raw(self):
        wrapper = '(' + TOOL + ' completed with no output)'
        for raw in ('\n', '\n\n'):
            events, result = self.material_events(raw)
            for version in ('1.1.74', '1.1.77'):
                events[0]['qodercli_version'] = version
                self.assertEqual(len(self.check(events)['complete_reads']), 3)
            result['message']['content'][0]['content'] = wrapper
            self.assertEqual(len(self.check(events)['complete_reads']), 3)
            events[0]['qodercli_version'] = '1.1.74'
            with self.assertRaises(RunError): self.check(events)

    def test_no_output_wrapper_excludes_other_whitespace(self):
        wrapper = '(' + TOOL + ' completed with no output)'
        for raw in ('text', ' ', '\t', '\r', '\r\n', '\n ', '\n\t', '\u00a0', '\u2003', '\u2028'):
            with self.subTest(raw=raw):
                events, result = self.material_events(raw)
                self.check(events)  # Exact direct delivery remains valid.
                result['message']['content'][0]['content'] = wrapper
                with self.assertRaises(RunError): self.check(events)

    def test_lf_wrapper_preserves_both_envelopes_and_audit(self):
        events, result = self.material_events('\n')
        wrapper = '(' + TOOL + ' completed with no output)'
        result['message']['content'][0]['content'] = wrapper
        index = events.index(result)
        for top in (None, [], [{'type': 'text', 'text': ''}],
                    [{'type': 'text', 'text': '\n\n'}],
                    [{'type': 'text', 'text': '\n'}, {'type': 'text', 'text': ''}]):
            changed = copy.deepcopy(events)
            if top is None: del changed[index]['tool_use_result']
            else: changed[index]['tool_use_result'] = top
            with self.subTest(top=top), self.assertRaises(RunError): self.check(changed)
        for message in (wrapper + ' ', wrapper + '\n', '', '\n\n'):
            changed = copy.deepcopy(events)
            changed[index]['message']['content'][0]['content'] = message
            with self.subTest(message=message), self.assertRaises(RunError): self.check(changed)
        changed = copy.deepcopy(events); del changed[index - 1:index + 1]
        with self.assertRaises(RunError): self.check(changed)
        changed = copy.deepcopy(events); changed.insert(index + 1, copy.deepcopy(result))
        with self.assertRaises(RunError): self.check(changed)
        self.check(events)
        mid = events[index - 1]['message']['content'][0]['input']['id']
        target = self.stage / self.registry['materials'][mid]['path']
        target.write_bytes(b'X')  # Same byte count, different frozen hash.
        with self.assertRaises(MaterialError): self.check(events)

    def test_raw_text_copies_are_checked_before_normalizing(self):
        events = self.events()
        result_index = next(i for i, e in enumerate(events) if e.get('type') == 'user'
                            and e['tool_use_result'][0]['text'])
        for target in ('message', 'top', 'both'):
            changed = copy.deepcopy(events)
            result = changed[result_index]
            if target in ('message', 'both'):
                result['message']['content'][0]['content'] = 'forged'
            if target in ('top', 'both'):
                result['tool_use_result'] = [{'type': 'text', 'text': 'forged'}]
            with self.subTest(target=target), self.assertRaises(RunError):
                self.check(changed)
        # A correct top-level body cannot excuse a list where Qoder sent a string.
        changed = copy.deepcopy(events)
        changed[result_index]['message']['content'][0]['content'] = changed[result_index]['tool_use_result']
        with self.assertRaises(RunError): self.check(changed)
        self.check(events)
        with self.assertRaises(RunError):
            claude_validate(self.log, 0, 'opus', self.stage, required=[v['path'] for v in
                            self.registry['materials'].values()], registry=self.registry)

    def test_init_model_tools_skills_plugins_hooks_and_order(self):
        cases = []
        for key, value in [('model', 'Kimi-K3'), ('model', 'Kimi-K3.1-Pro-extra'), ('tools', [TOOL, 'Read']),
                           ('skills', ['debug']), ('plugins', [{'name': 'builtin'}]),
                           ('mcp_servers', []), ('permissionMode', 'default'),
                           ('qodercli_version', '1.1.75'), ('agents', None)]:
            events = self.events(); events[0][key] = value; cases.append(events)
        for subtype in ('hook_started', 'hook_response'):
            events = self.events(); events.insert(1, {'type': 'system', 'subtype': subtype}); cases.append(events)
        events = self.events(); events.insert(1, copy.deepcopy(events[0])); cases.append(events)
        events = self.events(); events[0], events[1] = events[1], events[0]; cases.append(events)
        events = self.events(); events[1]['message']['model'] = 'Kimi-K3'; cases.append(events)
        events = self.events(); del events[1]['message']['model']; cases.append(events)
        events = self.events(); events[1]['message']['role'] = 'user'; cases.append(events)
        events = self.events(); events[1]['message']['content'][0]['type'] = 'unknown'; cases.append(events)
        for events in cases:
            with self.subTest(event=events[:2]), self.assertRaises(RunError): self.check(events)

    def test_incomplete_duplicate_denied_extra_tool_and_terminal(self):
        cases = []
        for key, value in [('is_error', True), ('permission_denials', ['Read']),
                           ('subtype', 'error'), ('stop_reason', 'max_tokens'), ('result', '')]:
            events = self.events(); events[-1][key] = value; cases.append(events)
        for key, value in [('id', ''), ('name', 'Bash'), ('input', {'id': '999'}),
                           ('input', {'id': self.first, 'path': '/etc/passwd'})]:
            events = self.events(); events[1]['message']['content'][0][key] = value; cases.append(events)
        events = self.events(); events[2]['message']['content'][0]['is_error'] = True; cases.append(events)
        events = self.events(); events[2]['message']['content'][0]['is_error'] = 'false'; cases.append(events)
        events = self.events(); events.insert(3, copy.deepcopy(events[1])); cases.append(events)
        events = self.events(); events.insert(3, copy.deepcopy(events[2])); cases.append(events)
        events = self.events(); del events[1:3]; cases.append(events)
        events = self.events(); del events[2]; cases.append(events)
        events = self.events(); events.insert(2, events.pop()); cases.append(events)
        events = self.events(); events.append(copy.deepcopy(events[-1])); cases.append(events)
        events = self.events(); events.append(copy.deepcopy(events[1])); cases.append(events)
        events = self.events(); events.insert(1, {'type': 'system', 'subtype': 'artifacts_update',
                                               'artifacts': ['unexpected']}); cases.append(events)
        for events in cases:
            with self.subTest(events=events[-2:]), self.assertRaises(RunError): self.check(events)
        with self.assertRaises(RunError): self.check(self.events(), code=1)
        self.log.write_text('{')
        with self.assertRaises(RunError):
            validate(self.log, 0, 'kmodel_latest', self.stage, required=list(
                v['path'] for v in self.registry['materials'].values()), registry=self.registry)


for name in dir(materials.MaterialIds):
    if name.startswith('test_') and name not in QoderProtocolTests.__dict__:
        setattr(QoderProtocolTests, name, None)

if __name__ == '__main__':
    unittest.main()
