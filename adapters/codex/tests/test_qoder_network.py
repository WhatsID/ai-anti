"""Distinguish ambiguous CLI failures, network observations and genuine recovery offline."""
import errno
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/claude-review/scripts'
sys.path.insert(0, str(SCRIPTS))
import review_qoder as qoder
import review_qoder_network as network
import review_retry as retrying
from review_runner import RunError
import test_review_retry as retry_tests
Clock = retry_tests.Clock
import test_qoder_protocol as protocol_tests


def payload(dns='reachable', tcp='reachable', number=None):
    return {'hosts': [dict(host=h, dns=dns, tcp=tcp, **({'errno': number} if number else {})) for h in network.HOSTS]}


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.log = self.root/'review.attempt1.jsonl'
        self.log.write_text('original immutable CLI bytes')
    def test_observations_never_claim_sandbox_or_upstream(self):
        for data, expected, denied in [(payload(), 'reachable', False),
                (payload('unavailable','unknown', -2), 'environment_network_unavailable', False),
                (payload('reachable','unavailable',errno.EACCES),'environment_network_unavailable',True),
                (payload('unknown','unknown'),'environment_unknown',False)]:
            result=network.observation(data)
            self.assertEqual(result['status'],expected);self.assertEqual(result['permission_denied'],denied)
            self.assertNotIn('sandbox_confirmed',result)
        for data in ({}, {'hosts':[]}, {'hosts':[dict(host='attacker',dns='reachable',tcp='reachable')]*2}):
            with self.assertRaises(ValueError):network.observation(data)
    def test_bounded_helper_evidence_and_failures(self):
        original=self.log.read_bytes()
        def execute(args,cwd,raw,timeout,idle,cap,env):
            self.assertEqual(args,[sys.executable,str(Path(network.__file__).resolve())])
            self.assertEqual(env,{});self.assertEqual(cap,65536)
            self.assertLessEqual(timeout,9.5);self.assertLessEqual(timeout,3.5);self.assertEqual(idle,timeout)
            raw.write_text(json.dumps(payload('unavailable','unknown',-2)));return 0
        with patch.object(network,'execute',execute):result=network.diagnose(self.log,self.root,4)
        self.assertEqual(result['status'],'environment_network_unavailable')
        sidecar=self.root/result['sidecar'];self.assertEqual(result['sha256'],hashlib.sha256(sidecar.read_bytes()).hexdigest())
        self.assertEqual(self.log.read_bytes(),original)
        for outcome in ('malformed','exit','timeout','denied'):
            def failed(args,cwd,raw,*args2,**kwargs):
                raw.write_text('bad json')
                if outcome=='timeout':raise RunError('timeout')
                if outcome=='denied':raise PermissionError('denied')
                return 1 if outcome=='exit' else 0
            with patch.object(network,'execute',failed):result=network.diagnose(self.log,self.root,10)
            self.assertEqual(result['status'],'environment_unknown');self.assertEqual(result['original_kind'],'ambiguous_model_error')
        with patch.object(network,'execute') as execute:
            result=network.diagnose(self.log,self.root,0)
            self.assertEqual(result['status'],'skipped');execute.assert_not_called()
        with patch.object(Path,'write_text',side_effect=PermissionError()):
            self.assertEqual(network.diagnose(self.log,self.root,0)['reason'],'diagnostic_evidence_unavailable')
    def test_validated_500_only_and_original_failure_survives(self):
        fixture=protocol_tests.QoderProtocolTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        events=fixture.events()
        failure=dict(type='result',subtype='error_during_execution',is_error=True,error_code=500,errors=['Internal server error.'])
        failed=[events[0],failure]
        def execute(args,cwd,log,*unused,**kwargs):log.write_text('\n'.join(map(json.dumps,failed)));return 1
        kwargs=dict(expected_model='Kimi-K3.1-Pro',registry_path=fixture.path,
                    required=[v['path'] for v in fixture.registry['materials'].values()])
        # Fixture registry path is discovered from its known registry rather than live input.
        registry_file=self.root/'registry.json';registry_file.write_text(json.dumps(fixture.registry));kwargs['registry_path']=registry_file
        with patch.object(qoder,'execute',execute),patch.object(qoder,'diagnose',return_value={'status':'reachable'}) as diagnose:
            with self.assertRaises(RunError) as caught:
                qoder.run_stage('fake',fixture.stage,'prompt',self.log,'kmodel_latest',30,20,65536,**kwargs)
            self.assertEqual(caught.exception.kind,'ambiguous_model_error');self.assertEqual(caught.exception.model_error,failure)
            self.assertEqual(caught.exception.exit_code,1);self.assertEqual(caught.exception.network_diagnostic['status'],'reachable');diagnose.assert_called_once()
            diagnose.reset_mock();failed[0]=dict(events[0],tools=['Bash'])
            with self.assertRaises(RunError):qoder.run_stage('fake',fixture.stage,'prompt',self.log,'kmodel_latest',30,20,65536,**kwargs)
            diagnose.assert_not_called()
        for source, error, expected in [('qoder',failure,'ambiguous_model_error'),(None,failure,'retryable_upstream'),
                ('qoder',dict(failure,error_code=10500),'retryable_upstream'),
                ('qoder',dict(failure,status_code=503),'retryable_upstream'),
                ('qoder',dict(failure,errors=['insufficient_quota']),'quota_exhausted'),
                ('qoder',dict(failure,type='overloaded_error'),'retryable_upstream')]:
            self.assertEqual(retrying.classify(error,source),expected)


class FormalNetworkTests(unittest.TestCase):
    invoke=retry_tests.FormalRetryTests.invoke
    def install_fake(self,target,ambiguous):
        text=Path(__file__).with_name('fake_qoder.py').read_text()
        if ambiguous:text=text.replace("'error_code':10500", "'error_code':500")
        target.write_text('#!'+sys.executable+'\n'+text.split('\n',1)[1]);target.chmod(0o700)
    def fake_network(self,args,cwd,raw,*unused,**kwargs):
        raw.write_text(json.dumps(payload('unavailable','unknown',-2)));return 0
    def check_failed(self,out,first):
        manifest=json.loads((out/'manifest.json').read_text());status=json.loads((out/'status.json').read_text())
        self.assertEqual(manifest['status'],'failed');self.assertFalse(status.get('completed',False))
        self.assertFalse((out/'report.md').exists())
        attempts=manifest['attempts'];self.assertEqual(attempts[first]['status'],'completed')
        record=attempts['review']['attempts'][0]
        self.assertEqual(attempts['review']['attempt_count'],1);self.assertEqual(record['kind'],'ambiguous_model_error')
        self.assertEqual(record['model_error']['error_code'],500);self.assertEqual(record['exit_code'],0)
        self.assertEqual(record['network_diagnostic']['status'],'environment_network_unavailable')
        diagnostic=record['network_diagnostic'];sidecar=out/'logs'/diagnostic['sidecar']
        self.assertEqual(diagnostic['sha256'],hashlib.sha256(sidecar.read_bytes()).hexdigest())
        self.assertEqual(diagnostic['original_kind'],'ambiguous_model_error')
        self.assertEqual(record['log_sha256'],hashlib.sha256((out/'logs'/record['log']).read_bytes()).hexdigest())
        self.assertEqual(status['attempts'],attempts)
        return (out/'logs/review.attempt1.jsonl').read_bytes()
    def test_formal_plan_and_explicit_new_run_preserve_old_evidence(self):
        import review
        from test_thin_review import ThinReviewTests
        fixture=ThinReviewTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        target=fixture.bindir/'qodercli';self.install_fake(target,True)
        out=fixture.base/'old';state=fixture.base/'counts';clock=Clock()
        args=['plan','--repo',str(fixture.repo),'--files','app.py','--scale','local','--plan',str(fixture.plan),'--channel','qoder','--out',str(out)]
        env=dict(fixture.env,FAKE_RETRY_STATE=str(state),FAKE_RETRY_STAGE='review',FAKE_RETRY_FAILURES='1')
        with patch.object(network,'execute',self.fake_network):
            self.assertEqual(self.invoke(review,args,env,clock),2)
        raw=self.check_failed(out,'inventory');self.assertEqual(sum(clock.waits),0)
        self.assertEqual(json.loads(state.read_text()),{'inventory':1,'review':1})
        # Separate explicit invocation models an approved complete NEW logical run.
        self.install_fake(target,False);new=fixture.base/'new';env['FAKE_RETRY_STATE']=str(fixture.base/'new-counts');clock=Clock()
        args[-1]=str(new)
        with patch.object(qoder,'diagnose') as diagnose:self.assertEqual(self.invoke(review,args,env,clock),0);diagnose.assert_not_called()
        self.assertEqual(sum(clock.waits),30);self.assertEqual(json.loads(Path(env['FAKE_RETRY_STATE']).read_text()),{'inventory':1,'review':2})
        self.assertEqual((out/'logs/review.attempt1.jsonl').read_bytes(),raw)
    def test_formal_bug_second_stage_preserves_independent_success(self):
        import bug_review
        from test_problem_review import ProblemReviewTests
        fixture=ProblemReviewTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        self.install_fake(fixture.bin/'qodercli',True);out=fixture.root/'failed';state=fixture.root/'counts';clock=Clock()
        env=dict(fixture.env,FAKE_RETRY_STATE=str(state),FAKE_RETRY_STAGE='review',FAKE_RETRY_FAILURES='1')
        args=['--bundle',str(fixture.bundle),'--config-base',str(fixture.root),'--channel','qoder','--out',str(out)]
        with patch.object(network,'execute',self.fake_network):self.assertEqual(self.invoke(bug_review,args,env,clock),2)
        self.check_failed(out,'independent');self.assertTrue((out/'independent.md').exists())
        self.assertEqual(json.loads(state.read_text()),{'independent':1,'review':1});self.assertEqual(sum(clock.waits),0)
    def test_qoder_trusted_10500_exhaustion_keeps_normal_budget(self):
        import review
        from test_thin_review import ThinReviewTests
        fixture=ThinReviewTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.install_fake(fixture.bindir/'qodercli',False)
        out=fixture.base/'exhausted';state=fixture.base/'counts';clock=Clock()
        args=['diff','--repo',str(fixture.repo),'--files','app.py','--scale','local','--channel','qoder','--out',str(out)]
        env=dict(fixture.env,FAKE_RETRY_STATE=str(state),FAKE_RETRY_STAGE='review',FAKE_RETRY_FAILURES='3')
        with patch.object(qoder,'diagnose') as diagnose:
            self.assertEqual(self.invoke(review,args,env,clock),2);diagnose.assert_not_called()
        self.assertEqual(sum(clock.waits),120);self.assertEqual(json.loads(state.read_text()),{'review':3})
        attempts=json.loads((out/'status.json').read_text())['attempts']['review']
        self.assertEqual([x['wait_seconds'] for x in attempts['attempts']],[30,90,0])
        self.assertEqual(attempts['error_kind'],'retryable_upstream');self.assertFalse((out/'report.md').exists())
