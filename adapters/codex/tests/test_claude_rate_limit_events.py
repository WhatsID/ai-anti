"""Exercise the Claude review stream's rate-limit event contract."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/claude-review/scripts'))

import review_runner


INIT = {
    'type': 'system', 'subtype': 'init', 'model': 'claude-opus-5-5', 'session_id': 'fixture',
    'tools': ['Read', 'Glob', 'Grep'], 'mcp_servers': [], 'plugins': [],
    'skills': [], 'slash_commands': [], 'permissionMode': 'dontAsk',
}
REPORT = '# Fixture review'
RESULT = {'type': 'result', 'subtype': 'success', 'is_error': False, 'permission_denials': [], 'result': REPORT}
WARNING_INFO = {
    'status': 'allowed_warning',
    'resetsAt': 1795766400,
    'rateLimitType': 'seven_day',
    'utilization': 0.77,
    'overageStatus': 'rejected',
    'overageDisabledReason': 'org_level_disabled',
    'isUsingOverage': False,
    'surpassedThreshold': 0.75,
    'unifiedWindows': {
        'five_hour': {'utilization': 0.08, 'resetsAt': 1795435200, 'status': 'allowed'},
        'seven_day': {'utilization': 0.77, 'resetsAt': 1795766400,
                      'status': 'allowed_warning', 'surpassedThreshold': 0.75},
    },
    'overageEnabled': False,
}


def rate_event(info, uuid='rate-1'):
    return {'type': 'rate_limit_event', 'rate_limit_info': info, 'uuid': uuid, 'session_id': 'fixture'}


class ClaudeRateLimitEventsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.cwd = Path(temp.name)
        self.logfile = self.cwd / 'events.jsonl'

    def validate(self, *events):
        self.logfile.write_text(''.join(json.dumps(event) + '\n' for event in events), encoding='utf-8')
        return review_runner.validate(self.logfile, 0, 'opus', self.cwd)

    def test_allowed_rate_event(self):
        result = self.validate(INIT, rate_event({'status': 'allowed'}), RESULT)
        self.assertEqual(result['result'], REPORT)
        self.assertEqual(result['transport_warnings'], [{'kind': 'rate_limit_event', 'status': 'allowed'}])

    def test_allowed_warning_rate_events(self):
        result = self.validate(INIT, rate_event(WARNING_INFO), rate_event(WARNING_INFO, 'rate-2'), RESULT)
        self.assertEqual(result['result'], REPORT)
        self.assertEqual(result['transport_warnings'], [{'kind': 'rate_limit_event', 'status': 'allowed_warning'}] * 2)

    def test_quota_exhaustion(self):
        for event in (rate_event({'status': 'rejected'}), {'error': 'rate_limit'}):
            with self.subTest(event=event):
                with self.assertRaises(review_runner.RunError) as caught:
                    self.validate(INIT, event, RESULT)
                self.assertEqual(caught.exception.kind, 'quota_exhausted')

    def test_unknown_or_missing_rate_status(self):
        for info in ({'status': 'throttled'}, {'status': ''}, {'status': None}, {'status': []}, {}):
            with self.subTest(info=info), self.assertRaisesRegex(review_runner.RunError, '^unknown stream event$'):
                self.validate(INIT, rate_event(info), RESULT)

    def test_invalid_rate_event_shape(self):
        event = rate_event(WARNING_INFO)
        for key in ('rate_limit_info', 'uuid', 'session_id'):
            missing_key = dict(event)
            del missing_key[key]
            with self.subTest(missing=key), self.assertRaisesRegex(review_runner.RunError, '^unknown stream event$'):
                self.validate(INIT, missing_key, RESULT)
        for malformed in ({**event, 'extra': True}, rate_event('allowed_warning')):
            with self.subTest(event=malformed), self.assertRaisesRegex(review_runner.RunError, '^unknown stream event$'):
                self.validate(INIT, malformed, RESULT)

    def test_rate_event_outside_init_and_result(self):
        event = rate_event(WARNING_INFO)
        for events in ((event, INIT, RESULT), (INIT, RESULT, event)):
            with self.subTest(events=events), self.assertRaisesRegex(review_runner.RunError, '^unknown stream event$'):
                self.validate(*events)


if __name__ == '__main__':
    unittest.main()
