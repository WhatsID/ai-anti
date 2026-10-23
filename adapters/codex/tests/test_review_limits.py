"""Offline budgets, wrapper and native subprocess guards."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/claude-review/scripts'
sys.path.insert(0, str(SCRIPTS))
from ai_anti_config import ConfigError, review_limits
from review_runner import execute, RunError


class ReviewLimitsTests(unittest.TestCase):
    def test_defaults_null_and_overrides(self):
        for data in ({}, {'review': {}}, {'review': {'timeout': None, 'idle_timeout': None}}):
            self.assertEqual(review_limits(data), (1800, 900))
        data = {'review': {'timeout': 3600, 'idle_timeout': 12}}
        self.assertEqual(review_limits(data), (3600, 12))
        self.assertEqual(review_limits(data, timeout=1), (1, 12))
        self.assertEqual(review_limits(data, idle_timeout=900), (3600, 900))
        self.assertEqual(review_limits({}, timeout=1, idle_timeout=900), (1, 900))

    def test_invalid_config_cannot_be_hidden_by_override(self):
        for key, cap in [('timeout', 3600), ('idle_timeout', 900)]:
            for bad in (False, True, 0, -1, 1.5, '10', [], {}, cap + 1):
                with self.subTest(key=key, bad=bad):
                    with self.assertRaises(ConfigError): review_limits({'review': {key: bad}}, 1, 1)
                    with self.assertRaises(ConfigError): review_limits({}, **{key: bad})
        for bad in (None, False, 3, [], 'x'):
            with self.assertRaises(ConfigError): review_limits({'review': bad})

    def test_native_idle_and_total_guards(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for source, total, idle, reason in [
                    ('import time; time.sleep(20)', 2, .2, 'idle timeout'),
                    ('import time\nwhile True:\n print("progress",flush=True); time.sleep(.05)', .3, .2, '^timeout$')]:
                start = time.monotonic()
                with self.assertRaisesRegex(RunError, reason):
                    execute([sys.executable, '-c', source], root, root/'log', total, idle, 10000)
                self.assertLess(time.monotonic()-start, 3)

    def test_wrapper_exit_stdin_and_prelaunch_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); conf = root/'.config/ai-anti/config.json'; conf.parent.mkdir(parents=True)
            log = root/'log'; marker = root/'started'
            env = dict(os.environ, HOME=temp)
            command = [sys.executable, str(SCRIPTS/'bounded_review.py'), '--log', str(log)]
            child = [sys.executable, '-c', 'import sys; print(repr(sys.stdin.read())); sys.exit(7)']
            run = subprocess.run(command + ['--'] + child, env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 7); self.assertEqual(log.read_text(), "''\n")
            self.assertIn('timeout=1800 idle_timeout=900', run.stderr)
            self.assertNotIn('budgets', log.read_text())
            conf.write_text(json.dumps({'review': {'timeout': True}}))
            run = subprocess.run(command + ['--timeout', '1', '--', sys.executable, '-c', 'from pathlib import Path; Path('+repr(str(marker))+').touch()'], env=env, capture_output=True)
            self.assertNotEqual(run.returncode, 0); self.assertFalse(marker.exists())
            conf.write_text('{}')
            run = subprocess.run(command + ['--', sys.executable, '-c', 'print("ok")'], env=env, capture_output=True)
            self.assertEqual(run.returncode, 0); self.assertEqual(log.read_text(), 'ok\n')

if __name__ == '__main__': unittest.main()
