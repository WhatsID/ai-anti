"""Offline end-to-end tests of the explicit bug-review CLI."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_problem_material import bundle, save, result, SCRIPTS
ENTRY = SCRIPTS / 'bug_review.py'
FIXTURES = Path(__file__).resolve().parent


class ProblemReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for cli, fixture in [('claude','fake_claude.py'), ('qodercli','fake_qoder.py'), ('codex','fake_codex.py')]:
            target=self.bin / cli
            target.write_text('#!' + sys.executable + '\n' + (FIXTURES / fixture).read_text().split('\n', 1)[1])
            target.chmod(0o755)
        self.bundle = self.root / 'bundle'
        self.input = bundle(self.bundle)
        self.audit = self.root / 'audit.jsonl'
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        FAKE_AUDIT=str(self.audit), PYTHONDONTWRITEBYTECODE='1')
        for key in ('AI_ANTI_CONFIG', 'FAKE_MODE', 'FAKE_PROBLEM_RESULT', 'FAKE_CODEX_MODE', 'FAKE_ACTUAL_MODEL', 'FAKE_PROBLEM_INDEPENDENT', 'FAKE_PROBLEM_MUTATE_ORIGIN'):
            self.env.pop(key, None)
        self.count=0
    def tearDown(self):
        self.temp.cleanup()
    def config(self):
        config=self.home / '.config/ai-anti/config.json'
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({'plugins': {'qwen': {'model': 'qwen3.8-max',
            'base_url': 'https://example.invalid', 'responses_base_url': 'https://example.invalid/v1',
            'credential_paths': ['credential.json']}}}))
        (self.root / 'credential.json').write_text('{"tokenplan":{"value":"OFFLINE_FAKE_TOKEN"}}')
    def run_review(self, *args, success=True):
        self.count += 1
        output=self.root / ('out-' + str(self.count))
        process=subprocess.run([sys.executable, str(ENTRY), '--bundle', str(self.bundle), '--out', str(output),
            '--config-base', str(self.root), *args], cwd=self.root, env=self.env, capture_output=True, timeout=20)
        self.assertEqual(process.returncode == 0, success, process.stdout.decode() + process.stderr.decode())
        return output
    def test_two_stages_are_fresh_and_blind(self):
        output=self.run_review('--channel','claude')
        calls=[json.loads(line) for line in self.audit.read_text().splitlines()]
        self.assertEqual(len(calls),2)
        first=''.join(calls[0]['files'].values())
        second=''.join(calls[1]['files'].values())
        self.assertNotIn('ANALYSIS-SECRET', first)
        self.assertNotIn('analysis.txt', first)
        self.assertIn('ANALYSIS-SECRET', second)
        independent=(output / 'independent.md').read_text()
        self.assertIn(independent, second)
        self.assertNotEqual(calls[0]['cwd'], calls[1]['cwd'])
        self.assertEqual(json.loads((output / 'status.json').read_text())['status'],'completed')
        self.assertIn('协议校验完成',(output / 'report.md').read_text())
    def test_different_domain_same_interface(self):
        self.input['environment']='synthetic website v2; deployment time unknown'
        self.input['domain_checks']=['Compare p95 latency and dependency timing']
        self.input['evidence'][0]['source']='browser timing'
        save(self.bundle,self.input)
        (self.bundle / 'problem.txt').write_text('User asks why website is slow. Expected fast navigation.')
        output=self.run_review('--channel','claude')
        self.assertEqual(json.loads((output / 'findings.json').read_text())['findings'][0]['evidence_ids'],['E1'])
    def test_qoder_fixture(self):
        for version in ('1.1.74', '1.1.77'):
            self.env.update(FAKE_VERSION=version, FAKE_INIT_VERSION=version)
            output=self.run_review('--channel','qoder','--model','kimi-k3.1-pro')
            manifest=json.loads((output / 'manifest.json').read_text())
            self.assertEqual(set(manifest['stages']), {'independent','review'})
            self.assertEqual(manifest['actual_model'],'Kimi-K3.1-Pro')
    def test_qwen_claude_and_codex_fixtures(self):
        self.config()
        for executor in ('claude','codex'):
            output=self.run_review('--channel','qwen','--qwen-executor',executor)
            status=json.loads((output / 'status.json').read_text())
            if executor == 'codex':
                self.assertIsNone(status['actual_model'])
                self.assertEqual(status['model_evidence'],'requested_only')
    def test_prepare_only_without_credentials_or_model(self):
        self.config()
        (self.root / 'credential.json').unlink()
        for item in self.bin.iterdir(): item.unlink()
        output=self.run_review('--channel','qwen','--prepare-only')
        self.assertFalse(self.audit.exists())
        self.assertEqual(json.loads((output / 'status.json').read_text())['status'],'prepared')
        self.assertFalse((output / 'logs/review.prompt.txt').exists())
    def test_failures_no_retry_or_second_stage(self):
        for mode in ('no_read','partial_read','denied','wrong_model','extra_tool','nonzero'):
            with self.subTest(mode=mode):
                self.audit.unlink(missing_ok=True)
                self.env['FAKE_MODE']=mode
                output=self.run_review('--channel','claude',success=False)
                self.assertEqual(len(self.audit.read_text().splitlines()),1)
                self.assertEqual(json.loads((output / 'status.json').read_text())['status'],'failed')
    def test_tampered_frozen_chunks_fail(self):
        (self.bundle / 'event.txt').write_text(('log event\n')*500)
        self.env['FAKE_MODE']='tamper_chunk'
        self.run_review('--channel','claude',success=False)
        self.assertEqual(len(self.audit.read_text().splitlines()),1)
    def test_invalid_result_keeps_raw_no_repair_call(self):
        for raw in ('not JSON', json.dumps(dict(result(), assessment='invalid'))):
            self.audit.unlink(missing_ok=True)
            self.env['FAKE_PROBLEM_RESULT']=raw
            output=self.run_review('--channel','claude',success=False)
            self.assertEqual(len(self.audit.read_text().splitlines()),2)
            self.assertEqual((output / 'review.raw.txt').read_text(),raw)
            self.assertFalse((output / 'findings.json').exists())
    def test_round_lineage_and_unique_second_round(self):
        first=self.run_review('--channel','claude')
        self.input['revision']=2
        save(self.bundle,self.input)
        prepared=self.run_review('--round','2','--previous',str(first),'--prepare-only')
        self.assertFalse((first / 'round-2.claim.json').exists())
        second=self.run_review('--round','2','--previous',str(first),'--channel','claude')
        self.run_review('--round','2','--previous',str(first),'--channel','claude',success=False)
        self.run_review('--round','2','--previous',str(second),'--channel','claude',success=False)
        self.assertEqual(json.loads((prepared / 'status.json').read_text())['status'],'prepared')
    def test_bad_previous_task_hash_and_round_three(self):
        first=self.run_review('--channel','claude')
        self.input['task_id']='other'; self.input['revision']=2; save(self.bundle,self.input)
        self.run_review('--round','2','--previous',str(first),success=False)
        self.input['task_id']='problem-task'; save(self.bundle,self.input)
        (first / 'report.md').write_text('tampered')
        self.run_review('--round','2','--previous',str(first),success=False)
        self.run_review('--round','3',success=False)
    def test_previous_requires_both_nonempty_frozen_stages(self):
        from hashlib import sha256
        first=self.run_review('--channel','claude')
        self.input['revision']=2; save(self.bundle,self.input)
        original=json.loads((first / 'manifest.json').read_text())
        for snapshots in (None, {}, {'independent': {}}, {'independent': {}, 'review': {}}):
            manifest=dict(original)
            if snapshots is None: manifest.pop('snapshots')
            else: manifest['snapshots']=snapshots
            (first / 'manifest.json').write_text(json.dumps(manifest))
            status=json.loads((first / 'status.json').read_text())
            status['manifest_sha256']=sha256((first / 'manifest.json').read_bytes()).hexdigest()
            (first / 'status.json').write_text(json.dumps(status))
            self.run_review('--round','2','--previous',str(first),'--prepare-only',success=False)
    def test_private_modes_and_source_freeze(self):
        self.env['FAKE_PROBLEM_MUTATE_ORIGIN']=str(self.bundle / 'analysis.txt')
        output=self.run_review('--channel','claude')
        calls=[json.loads(line) for line in self.audit.read_text().splitlines()]
        self.assertIn('ANALYSIS-SECRET',''.join(calls[1]['files'].values()))
        self.assertNotIn('CHANGED AFTER FREEZE',''.join(calls[1]['files'].values()))
        self.assertEqual(output.stat().st_mode & 0o777,0o700)
        for path in output.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777,0o700 if path.is_dir() else 0o600,str(path))
    def test_long_independent_fails_before_second_call(self):
        self.env['FAKE_PROBLEM_INDEPENDENT']='x'*2001
        self.run_review('--channel','claude',success=False)
        self.assertEqual(len(self.audit.read_text().splitlines()),1)
    def test_empty_evidence_enforced_end_to_end(self):
        self.input['evidence']=[]; save(self.bundle,self.input)
        value=result(); value['findings']=[]
        self.env['FAKE_PROBLEM_RESULT']=json.dumps(value)
        self.run_review('--channel','claude',success=False)
        value['assessment']='insufficient_evidence'
        self.env['FAKE_PROBLEM_RESULT']=json.dumps(value)
        self.run_review('--channel','claude')
    def test_out_must_be_new_absolute_and_outside_bundle(self):
        for output in ('relative', str(self.bundle), str(self.bundle / 'out'), str(self.home)):
            process=subprocess.run([sys.executable,str(ENTRY),'--bundle',str(self.bundle),'--out',output,
                                    '--prepare-only'],cwd=self.root,env=self.env,capture_output=True,timeout=10)
            self.assertNotEqual(process.returncode,0)
        self.assertFalse(self.audit.exists())
