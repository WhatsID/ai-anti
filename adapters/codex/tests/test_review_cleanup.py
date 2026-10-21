"""Own process-group cleanup, including an orphaned TERM-ignoring grandchild."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/claude-review/scripts'
sys.path.insert(0, str(SCRIPTS))
from review_runner import execute, stop_group, RunError


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_reap_leader_before_kill_but_still_kill_group(self):
        process = Mock(pid=123)
        calls = []
        process.poll.side_effect = lambda: calls.append('poll') or 0
        def killpg(pid, sig):
            self.assertEqual(pid, 123)
            calls.append(sig)
            if sig == signal.SIGKILL and 'poll' not in calls:
                raise PermissionError('unreaped macOS leader')
        with patch('review_runner.os.killpg', side_effect=killpg), patch('review_runner.time.sleep'):
            stop_group(process)
        self.assertEqual(calls, ['poll', signal.SIGTERM, 'poll', signal.SIGKILL])
        process.wait.assert_called_once_with(timeout=3)

    def test_real_permission_denial_is_not_suppressed(self):
        with patch('review_runner.os.killpg', side_effect=PermissionError('real denial')):
            with self.assertRaisesRegex(RunError, 'cleanup denied'):
                stop_group(Mock(pid=123))

    def test_exit_race_retries_only_after_reaping(self):
        process = Mock(pid=123, returncode=None)
        def poll():
            if poll.calls:
                process.returncode = 0
            poll.calls += 1
            return process.returncode
        poll.calls = 0
        process.poll.side_effect = poll
        with patch('review_runner.os.killpg', side_effect=[PermissionError('zombie'),
                   ProcessLookupError()]) as kill, patch('review_runner.time.sleep'):
            stop_group(process)
        self.assertEqual(kill.call_args_list[0], kill.call_args_list[1])
        self.assertEqual(kill.call_count, 2)
        # A running leader after the error does not justify retrying permissions.
        process = Mock(pid=123, returncode=None)
        process.poll.return_value = None
        with patch('review_runner.os.killpg', side_effect=PermissionError('real')) as kill:
            with self.assertRaisesRegex(RunError, 'cleanup denied'):
                stop_group(process)
        self.assertEqual(kill.call_count, 1)

    def test_exited_leader_term_ignoring_and_normal_exit(self):
        for body in ('pass', 'import time; time.sleep(30)',
                     'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)'):
            with self.subTest(body=body):
                p = subprocess.Popen([sys.executable, '-c', body], start_new_session=True)
                time.sleep(.15)
                stop_group(p)
                self.assertIsNotNone(p.returncode)

    def test_timeout_idle_and_output_cap_preserve_reason(self):
        cases = [('import time;time.sleep(30)', .3, 10, 10000, 'timeout'),
                 ('import time;time.sleep(30)', 10, .3, 10000, 'idle timeout'),
                 ('import time;print("x"*2000,flush=True);time.sleep(30)', 10, 10, 100, 'log limit'),
                 ('print("x"*4096)', 10, 10, 100, 'log limit')]
        for index, (body, timeout, idle, cap, reason) in enumerate(cases):
            start = time.monotonic()
            with self.subTest(reason=reason), self.assertRaisesRegex(RunError, '^' + reason + '$'):
                execute([sys.executable, '-c', body], self.root, self.root / str(index), timeout, idle, cap)
            self.assertLess(time.monotonic() - start, 4)

    def test_orphan_grandchild_stops_after_parent_exits(self):
        heartbeat = self.root / 'heartbeat'
        ready = self.root / 'ready'
        # Intermediate child exits; grandchild ignores TERM and keeps the original
        # process group. A parent-only or poll()-early-return implementation leaks it.
        body = '''import os,signal,time,sys
from pathlib import Path
heartbeat,ready=map(Path,sys.argv[1:])
if os.fork()==0:
    if os.fork()!=0: os._exit(0)
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    ready.write_text(str(os.getpid()))
    while True:
        with heartbeat.open('a') as f: f.write('tick\\n')
        time.sleep(.03)
while not ready.exists(): time.sleep(.01)
'''
        try:
            self.assertEqual(execute([sys.executable, '-c', body, str(heartbeat), str(ready)],
                                    self.root, self.root / 'log', 5, 5, 10000), 0)
            before = heartbeat.stat().st_size
            time.sleep(.25)
            self.assertEqual(heartbeat.stat().st_size, before, 'grandchild survived group cleanup')
        finally:
            if ready.exists():
                try: os.kill(int(ready.read_text()), signal.SIGKILL)
                except ProcessLookupError: pass


if __name__ == '__main__':
    unittest.main()
