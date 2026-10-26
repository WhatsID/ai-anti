"""Bounded recovery contracts with virtual time; no paid model calls or real waits."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/claude-review/scripts'
sys.path.insert(0, str(SCRIPTS))
import review_retry as retrying
from review_runner import RunError


class Clock:
    def __init__(self): self.now = 0; self.waits = []
    def __call__(self): return self.now
    def sleep(self, seconds): self.now += seconds; self.waits.append(seconds)


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.input = self.root/'input'
        self.input.write_text('frozen'); self.log = self.root/'stage.jsonl'; self.clock = Clock()
    def run_retry(self, operation, timeout=600, retries=2):
        return retrying.retry(operation, self.log, timeout, retries, [self.input], self.clock, self.clock.sleep)
    def records(self): return json.loads(self.log.with_suffix('.attempts.json').read_text())
    def test_classification_trusted_fields_only_permanent_wins(self):
        for error in ({'error_code':10500}, {'type':'overloaded_error'}, {'type':'rate_limit_error'},
                      {'status_code':503}, {'message':retrying.CAPACITY}):
            self.assertEqual(retrying.classify(error),'retryable_upstream')
        for message in ('insufficient_quota','quota exhausted','quota exceeded','insufficient credits'):
            self.assertEqual(retrying.classify({'errors':[message], 'error_code':10500}),'quota_exhausted')
        for code in (400,401,403,404,413,422):
            self.assertEqual(retrying.classify({'status_code':code,'error_code':10500}),'permanent_upstream')
        for error in ({'status_code':429},{'result':retrying.CAPACITY},{'content':'overloaded_error'}, {'message':'maybe retry'},{}):
            self.assertEqual(retrying.classify(error),'unknown_upstream')
    def test_three_attempts_backoff_canonical_and_evidence(self):
        remaining = []
        def operation(raw, budget):
            remaining.append(budget); raw.write_text('raw-'+str(len(remaining)))
            if len(remaining)<3: raise retrying.upstream_error({'error_code':10500},1)
            return {'result':'report'}
        result=self.run_retry(operation)
        self.assertEqual(remaining,[600,570,480]); self.assertEqual(sum(self.clock.waits),120)
        self.assertEqual(result['retry_count'],2); self.assertEqual(self.log.read_text(),'raw-3')
        records=self.records();self.assertEqual(records['status'],'completed')
        self.assertEqual([r['wait_seconds'] for r in records['attempts']],[30,90,0])
        self.assertEqual([r['exit_code'] for r in records['attempts']],[1,1,0])
        for record in records['attempts']:
            self.assertEqual(record['log_sha256'],hashlib.sha256((self.root/record['log']).read_bytes()).hexdigest())
    def test_exhaustion_and_no_retry_kinds(self):
        for kind in ('retryable_upstream','quota_exhausted','unknown_upstream','execution_failed','incomplete_reads','cancelled'):
            with self.subTest(kind=kind):
                self.log=self.root/(kind+'.jsonl');calls=[]
                def operation(raw,budget):
                    calls.append(budget);raw.write_text('failed bytes');raise RunError('failed',kind)
                with self.assertRaises(RunError):self.run_retry(operation)
                self.assertEqual(len(calls),3 if kind=='retryable_upstream' else 1)
                self.assertEqual(self.records()['status'],'failed');self.assertEqual(self.log.read_text(),'failed bytes')
    def test_budget_includes_execution_and_wait(self):
        calls=[]
        def operation(raw,budget):
            calls.append(budget);raw.write_text('failure');self.clock.now+=10
            raise RunError('temporary','retryable_upstream')
        with self.assertRaisesRegex(RunError,'insufficient'):self.run_retry(operation,40)
        self.assertEqual(len(calls),1);self.assertEqual(sum(self.clock.waits),0)
    def test_cancel_during_wait_and_during_execution_retains_raw(self):
        def operation(raw,budget):raw.write_text('cancel raw');raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):self.run_retry(operation)
        self.assertEqual(self.records()['error_kind'],'cancelled');self.assertEqual(self.records()['attempt_count'],1)
        self.log=self.root/'wait.jsonl'
        def temporary(raw,budget):raw.write_text('temporary');raise RunError('temporary','retryable_upstream')
        def cancel(seconds):raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):retrying.retry(temporary,self.log,600,2,[self.input],self.clock,cancel)
        self.assertEqual(self.records()['error_kind'],'cancelled');self.assertEqual(self.records()['attempt_count'],1)
    def test_snapshot_change_blocks_retry_and_explicit_disable(self):
        calls=[]
        def operation(raw,budget):
            calls.append(1);raw.write_text('failure');self.input.write_text('changed')
            raise RunError('temporary','retryable_upstream')
        with self.assertRaisesRegex(RunError,'snapshot changed'):self.run_retry(operation)
        self.assertEqual(len(calls),1)
        self.log=self.root/'disabled.jsonl';calls.clear()
        with self.assertRaises(RunError):self.run_retry(operation,retries=0)
        self.assertEqual(len(calls),1);self.assertEqual(self.records()['retry_count'],0)
    def direct(self,events,code=0):
        self.log.write_text('\n'.join(json.dumps(e) for e in events))
        return retrying.direct_result(self.log,code,'codex-jsonl')
    def events(self):
        return [{'type':'thread.started','thread_id':'t'}, {'type':'turn.started'},
                {'type':'item.completed','item':{'id':'f','type':'agent_message','text':'real report'}}, {'type':'turn.completed'}]
    def test_direct_body_not_jsonl_and_invalid_lifecycle(self):
        events=self.events();self.assertEqual(self.direct(events)['result'],'real report')
        for malformed in (events[:-1],events+[{'type':'turn.completed'}],events+[{'type':'noise'}],
                          events[:2]+[copy.deepcopy(events[2]),dict(type='item.completed',item=dict(id='f2',type='agent_message',text='second'))]+events[-1:]):
            with self.assertRaises(RunError):self.direct(malformed)
        with self.assertRaises(RunError):self.direct(events,1)
    def test_direct_errors_quota_and_tool_guards(self):
        failed=self.events()[:2]+[{'type':'turn.failed','error':{'type':'overloaded_error'}}]
        with self.assertRaises(RunError) as caught:self.direct(failed,1)
        self.assertEqual(caught.exception.kind,'retryable_upstream')
        for error in ({'status_code':429},{'type':'insufficient_quota','error_code':10500}):
            failed[-1]['error']=error
            with self.assertRaises(RunError) as caught:self.direct(failed,1)
            self.assertNotEqual(caught.exception.kind,'retryable_upstream')
        tool={'type':'item.completed','item':{'id':'x','type':'command_execution','status':'failed','exit_code':1}}
        with self.assertRaises(RunError) as caught:self.direct(failed[:2]+[tool]+failed[-1:],1)
        self.assertEqual(caught.exception.kind,'execution_failed')
    def test_single_json_error_only_and_permissions_preserved(self):
        result={'type':'result','subtype':'success','is_error':False,'result':retrying.CAPACITY,'permission_denials':[{'tool':'Bash'}]}
        self.log.write_text(json.dumps(result))
        self.assertEqual(retrying.direct_result(self.log,0,'claude-json')['permission_denials'],[{'tool':'Bash'}])
        result.update(is_error=True,subtype='error_during_execution')
        self.log.write_text(json.dumps(result))
        with self.assertRaises(RunError) as caught:retrying.direct_result(self.log,1,'claude-json')
        self.assertEqual(caught.exception.kind,'execution_failed')
        result['permission_denials']=[];self.log.write_text(json.dumps(result))
        with self.assertRaises(RunError) as caught:retrying.direct_result(self.log,1,'claude-json')
        self.assertEqual(caught.exception.kind,'retryable_upstream')

if __name__=='__main__':unittest.main()

class FormalRetryTests(unittest.TestCase):
    def invoke(self, module, argv, env, clock):
        import os
        original = retrying.retry
        def injected(*args, **kwargs):
            return original(*args, **kwargs, clock=clock, sleep=clock.sleep)
        with patch.dict(os.environ, env, clear=True), patch.object(sys, 'argv', ['entry', *argv]), patch.object(retrying, 'retry', injected):
            status = module.main()
            if status:
                out = Path(argv[argv.index('--out')+1])
                print((out/'status.json').read_text() if (out/'status.json').exists() else 'no status')
            return status
    def test_formal_plan_diff_failed_stage_only_and_three_attempt_stop(self):
        import review
        from test_thin_review import ThinReviewTests
        fixture=ThinReviewTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        for mode, stage, failures, success in [('plan','inventory',1,True),('plan','review',2,True),('diff','review',3,False)]:
            out=fixture.base/(mode+'-'+stage); state=fixture.base/(mode+'-'+stage+'.state');clock=Clock()
            args=[mode,'--repo',str(fixture.repo),'--files','app.py','--scale','local fixture','--out',str(out)]
            if mode=='plan':args+=['--plan',str(fixture.plan)]
            env=dict(fixture.env,FAKE_RETRY_STATE=str(state),FAKE_RETRY_STAGE=stage,FAKE_RETRY_FAILURES=str(failures))
            self.assertEqual(self.invoke(review,args,env,clock),0 if success else 2)
            counts=json.loads(state.read_text());self.assertEqual(counts[stage],failures+1 if success else 3)
            if stage=='review' and mode=='plan':self.assertEqual(counts['inventory'],1)
            manifest=json.loads((out/'manifest.json').read_text());self.assertEqual(manifest['status'],'completed' if success else 'failed')
            self.assertEqual(sum(clock.waits),30 if failures==1 else 120)
            self.assertEqual((out/'report.md').exists(),success)
    def test_bug_second_stage_and_second_round_lineage_prepare_only(self):
        import bug_review
        from test_problem_review import ProblemReviewTests
        fixture=ProblemReviewTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        state=fixture.root/'counts';clock=Clock()
        env=dict(fixture.env,FAKE_RETRY_STATE=str(state),FAKE_RETRY_STAGE='review',FAKE_RETRY_FAILURES='2')
        out=fixture.root/'first'
        common=['--bundle',str(fixture.bundle),'--config-base',str(fixture.root),'--channel','claude']
        self.assertEqual(self.invoke(bug_review,[*common,'--out',str(out)],env,clock),0)
        self.assertEqual(json.loads(state.read_text()),{'independent':1,'review':3})
        from test_problem_material import save
        fixture.input['revision']=2;save(fixture.bundle,fixture.input)
        state.unlink();second=fixture.root/'second'
        self.assertEqual(self.invoke(bug_review,[*common,'--out',str(second),'--round','2','--previous',str(out)],env,Clock()),0)
        self.assertEqual(json.loads(state.read_text()),{'independent':1,'review':3})
        state.unlink();prepared=fixture.root/'prepared'
        self.assertEqual(self.invoke(bug_review,[*common,'--out',str(prepared),'--prepare-only'],env,Clock()),0)
        self.assertFalse(state.exists())
    def test_qoder_and_codex_formal_retries_and_quota_zero_retry(self):
        import review
        import shutil
        from test_thin_review import ThinReviewTests
        fixture=ThinReviewTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        for cli, filename in [('qodercli','fake_qoder.py'),('codex','fake_codex.py')]:
            target=fixture.bindir/cli;shutil.copyfile(Path(__file__).with_name(filename),target);target.chmod(0o700)
        fixture.write_config({'plugins':{'qwen':{'model':'fixture.v1-max','base_url':'https://example.invalid','responses_base_url':'https://example.invalid/v1','credential_paths':['credential.json']}}})
        (fixture.repo/'credential.json').write_text('{"tokenplan":{"value":"OFFLINE_FAKE_TOKEN"}}')
        for channel, extra, error, expected in [('qoder',[],None,0),('qwen',['--qwen-executor','codex'],None,0),('claude',[],'insufficient_quota',2)]:
            out=fixture.base/(channel+'-retry');state=fixture.base/(channel+'.state');clock=Clock()
            env=dict(fixture.env,FAKE_RETRY_STATE=str(state),FAKE_RETRY_STAGE='review',FAKE_RETRY_FAILURES='1')
            if error:env['FAKE_RETRY_ERROR']=error
            args=['diff','--repo',str(fixture.repo),'--files','app.py','--scale','local fixture','--out',str(out),'--channel',channel,*extra]
            self.assertEqual(self.invoke(review,args,env,clock),expected)
            self.assertEqual(json.loads(state.read_text())['review'],1 if error else 2)
            self.assertEqual(sum(clock.waits),0 if error else 30)
