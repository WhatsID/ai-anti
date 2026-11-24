import concurrent.futures
from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/claude-review/scripts'))
from token_plan_proxy import Proxy, quota_reason, MAX_BODY, MAX_ERROR
from token_plan_transport import Transport

PRIMARY = 'synthetic-primary-credential'
BACKUP = 'synthetic-backup-credential'
QUOTA = b'{"code":"Throttling.AllocationQuota","message":"Your token-plan 1-month quota has been exhausted. The quota will reset at 12-02 16:00:00 UTC."}'

@contextmanager
def upstream(respond):
    records = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            raw = self.rfile.read(int(self.headers['Content-Length']))
            record = (self.path, self.headers.get('Authorization') or self.headers.get('x-api-key'), raw, dict(self.headers))
            records.append(record)
            status,body,headers = respond(record,len(records))
            self.send_response(status)
            for k,v in headers.items(): self.send_header(k,v)
            self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try: yield 'http://127.0.0.1:'+str(server.server_port),records
    finally: server.shutdown();server.server_close();thread.join()

def post(proxy,body=b'{"model":"glm-5.3","input":[]}',path=None,headers=None):
    client=http.client.HTTPConnection('127.0.0.1',proxy.server.server_port,timeout=3)
    auth={'Authorization':'Bearer '+proxy.token} if proxy.wire_api=='responses' else {'x-api-key':proxy.token,'anthropic-version':'2023-06-01'}
    auth.update(headers or {})
    client.request('POST',path or proxy.path,body,auth)
    response=client.getresponse(); result=(response.status,response.read());client.close();return result

class QuotaClassifier(unittest.TestCase):
    def test_strict_envelopes(self):
        self.assertEqual(quota_reason(429,QUOTA),'token_plan_exhausted')
        for name in ('code','type'):
            self.assertEqual(quota_reason(429,json.dumps({'error':{name:'insufficient_quota'}}).encode()),'quota_exhausted')
        for status,body in [(200,QUOTA),(401,QUOTA),(500,QUOTA),(429,b'{"code":"Throttling.AllocationQuota"}'),(429,b'{"error":{"type":"rate_limit_error","message":"quota low"}}'),(429,b'{"output":"Your token-plan 1-month quota has been exhausted."}'),(429,b'not json'),(429,b' '* (MAX_ERROR+1))]:
            self.assertIsNone(quota_reason(status,body))

class TransportHTTP(unittest.TestCase):
    def test_same_bytes_and_sticky_both_protocols(self):
        payload=b'{ "model": "glm-5.3", "input": [], "reasoning":{"effort":"high"} }'
        for wire in ('responses','anthropic'):
            with self.subTest(wire=wire), upstream(lambda r,n:(429,QUOTA,{}) if PRIMARY in r[1] else (200,b'ok',{})) as (url,records):
                with Proxy(url,PRIMARY,BACKUP,wire,5,_test_http=True) as proxy:
                    self.assertEqual(post(proxy,payload),(200,b'ok'))
                    self.assertEqual(post(proxy,payload),(200,b'ok'))
                    self.assertEqual(proxy.evidence()['switch_count'],1)
                    self.assertEqual(proxy.evidence()['used_slot'],'backup')
                    self.assertEqual([r[2] for r in records],[payload]*3)
                    self.assertIn(PRIMARY,records[0][1]); self.assertTrue(all(BACKUP in r[1] for r in records[1:]))
                    self.assertNotIn(PRIMARY,json.dumps(proxy.evidence()))
                    self.assertNotIn(proxy.token,repr(records))
                    if wire=='anthropic': self.assertEqual(records[0][3]['Anthropic-Version'],'2023-06-01')
                with self.assertRaises(OSError): socket.create_connection(('127.0.0.1',proxy.server.server_port),.1)

    def test_success_and_nonquota_never_switch(self):
        for status,body in [(200,b'ok'),(401,QUOTA),(403,QUOTA),(500,QUOTA),(429,b'{"error":{"type":"rate_limit_error"}}'),(429,b'bad'),(200,b'data: '+QUOTA+b'\n\n')]:
            with self.subTest(status=status,body=body), upstream(lambda r,n:(status,body,{})) as (url,records):
                with Proxy(url,PRIMARY,BACKUP,'responses',5,_test_http=True) as p:
                    self.assertEqual(post(p)[0],status); self.assertEqual(len(records),1); self.assertEqual(p.evidence()['switch_count'],0)

    def test_backup_quota_stops_at_two(self):
        with upstream(lambda r,n:(429,QUOTA,{})) as (url,records):
            with Proxy(url,PRIMARY,BACKUP,'responses',5,_test_http=True) as p:
                self.assertEqual(post(p)[0],429);self.assertEqual(len(records),2)
                self.assertEqual(post(p)[0],429);self.assertEqual(len(records),3)

    def test_server_bound_reference_not_replayed(self):
        for key in ('conversation','previous_response_id'):
            with upstream(lambda r,n:(429,QUOTA,{})) as (url,records):
                with Proxy(url,PRIMARY,BACKUP,'responses',5,_test_http=True) as p:
                    self.assertEqual(post(p,json.dumps({key:'upstream-id'}).encode())[0],429)
                    self.assertEqual(len(records),1); self.assertEqual(p.evidence()['switch_count'],0)

    def test_oversize_error_and_redirect_not_replayed(self):
        for status,body,headers in [(429,b'x'*(MAX_ERROR+1),{}),(302,b'',{'Location':'http://127.0.0.1:9/steal'})]:
            with upstream(lambda r,n:(status,body,headers)) as (url,records):
                with Proxy(url,PRIMARY,BACKUP,'responses',5,_test_http=True) as p:
                    self.assertIn(post(p)[0],(302,502));self.assertEqual(len(records),1)

    def test_local_guards(self):
        with upstream(lambda r,n:(200,b'ok',{})) as (url,records):
            with Proxy(url,PRIMARY,BACKUP,'responses',5,_test_http=True) as p:
                self.assertEqual(post(p,headers={'Authorization':'Bearer wrong'})[0],401)
                self.assertEqual(post(p,path='/evil')[0],404)
                self.assertIn(post(p,path=url+'/responses')[0],(401,404))
                self.assertEqual(post(p,headers={'Host':'example.invalid'})[0],401)
                self.assertEqual(post(p,headers={'Content-Encoding':'gzip'})[0],400)
                self.assertEqual(post(p,b'',headers={'Content-Length':str(MAX_BODY+1)})[0],413)
                self.assertEqual(records,[])

    def test_concurrent_selection(self):
        with upstream(lambda r,n:(429,QUOTA,{}) if PRIMARY in r[1] else (200,b'ok',{})) as (url,records):
            with Proxy(url,PRIMARY,BACKUP,'responses',5,_test_http=True) as p:
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                    results=list(pool.map(lambda n:post(p),range(3)))
                self.assertEqual(results,[(200,b'ok')]*3)
                self.assertEqual(sum(PRIMARY in r[1] for r in records),1)

    def test_write_once_session(self):
        # A CLI session performs a tool action between HTTP requests; switching
        # credentials does not rerun the surrounding operation.
        writes=[]
        with upstream(lambda r,n:(429,QUOTA,{}) if n==2 else (200,b'ok',{})) as (url,records):
            with Proxy(url,PRIMARY,BACKUP,'responses',5,_test_http=True) as p:
                self.assertEqual(post(p)[0],200);writes.append('write-once')
                self.assertEqual(post(p)[0],200)
                self.assertEqual(writes,['write-once']);self.assertEqual(len(records),3)

    def test_deadline_closes_idle_connection(self):
        with upstream(lambda r,n:(200,b'ok',{})) as (url,records):
            with Proxy(url,PRIMARY,BACKUP,'responses',.15,_test_http=True) as p:
                client=socket.create_connection(('127.0.0.1',p.server.server_port));client.sendall(b'POST /responses HTTP/1.1\r\n')
                time.sleep(.3)
                self.assertTrue(p.closed.is_set());client.close()

    def test_quiet_sse_keeps_idle_budget_and_never_replays(self):
        records=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']));records.append(True)
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                self.wfile.write(b'data: initial\n\n');self.wfile.flush()
                time.sleep(.15)
                try: self.wfile.write(b'data: '+QUOTA+b'\n\n');self.wfile.flush()
                except OSError: pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            url='http://127.0.0.1:'+str(server.server_port)
            with Proxy(url,PRIMARY,BACKUP,'responses',3,idle_timeout=.5,_test_http=True) as p:
                status,body=post(p);self.assertEqual(status,200);self.assertIn(QUOTA,body)
                self.assertEqual(p.evidence()['switch_count'],0)
            self.assertEqual(len(records),1)
        finally: server.shutdown();server.server_close();thread.join()

    def test_cancel_during_upstream_headers(self):
        entered = threading.Event(); release = threading.Event()
        def respond(record,n):
            entered.set(); release.wait(3); return 200,b'ok',{}
        with upstream(respond) as (url,records):
            p=Proxy(url,PRIMARY,BACKUP,'responses',10,idle_timeout=9,_test_http=True)
            p.__enter__()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pending=pool.submit(post,p)
                self.assertTrue(entered.wait(1))
                start=time.monotonic();p.close()
                self.assertLess(time.monotonic()-start,1)
                try: pending.result(timeout=1)
                except (OSError,http.client.HTTPException): pass
                release.set()

    def test_delayed_headers_respect_configured_idle(self):
        def respond(record,n):
            time.sleep(.15);return 200,b'ok',{}
        with upstream(respond) as (url,records):
            with Proxy(url,PRIMARY,BACKUP,'responses',3,idle_timeout=.5,_test_http=True) as p:
                self.assertEqual(post(p),(200,b'ok'))
            with Proxy(url,PRIMARY,BACKUP,'responses',3,idle_timeout=.05,_test_http=True) as p:
                self.assertEqual(post(p)[0],502)
                self.assertEqual(p.evidence()['switch_count'],0)

    def test_error_credentials_redacted(self):
        raw = json.dumps({'error':{'message': PRIMARY + ' ' + BACKUP}}).encode()
        with upstream(lambda r,n:(401,raw,{})) as (url,records):
            with Proxy(url,PRIMARY,BACKUP,'responses',5,_test_http=True) as p:
                status,body = post(p)
                self.assertEqual(status,401)
                self.assertNotIn(PRIMARY.encode(),body); self.assertNotIn(BACKUP.encode(),body)
                self.assertEqual(len(records),1)

    def test_exact_request_limit(self):
        with upstream(lambda r,n:(200,b'ok',{})) as (url,records):
            with Proxy(url,PRIMARY,BACKUP,'responses',5,_test_http=True) as p:
                self.assertEqual(post(p,b'x'*MAX_BODY)[0],200)
                self.assertEqual(len(records[0][2]),MAX_BODY)

    def test_count_tokens_does_not_switch(self):
        with upstream(lambda r,n:(429,QUOTA,{})) as (url,records):
            with Proxy(url,PRIMARY,BACKUP,'anthropic',5,_test_http=True) as p:
                self.assertEqual(post(p,path=p.path+'/count_tokens')[0],429)
                self.assertEqual(len(records),1); self.assertEqual(p.evidence()['switch_count'],0)

    def test_anthropic_beta_query_forwarded_exactly(self):
        with upstream(lambda r,n:(200,b'ok',{})) as (url,records):
            with Proxy(url+'/apps/anthropic',PRIMARY,BACKUP,'anthropic',5,_test_http=True) as proxy:
                messages = '/apps/anthropic/v1/messages'
                permitted = [messages+'?beta=true', messages+'/count_tokens?beta=true']
                for path in permitted:
                    self.assertEqual(post(proxy,path=path), (200,b'ok'))
                for base in (messages, messages+'/count_tokens'):
                    for suffix in ('?beta=false', '?x=1', '?beta=true&x=1', '??beta=true'):
                        with self.subTest(base=base,suffix=suffix):
                            self.assertEqual(post(proxy,path=base+suffix)[0],404)
                self.assertEqual([r[0] for r in records], permitted)
        with upstream(lambda r,n:(429,QUOTA,{}) if r[1] == PRIMARY else (200,b'ok',{})) as (url,records):
            with Proxy(url,PRIMARY,BACKUP,'anthropic',5,_test_http=True) as proxy:
                self.assertEqual(post(proxy,path='/v1/messages/count_tokens?beta=true')[0],429)
                self.assertEqual(post(proxy,path='/v1/messages?beta=true'), (200,b'ok'))
                self.assertEqual([r[0] for r in records],
                                 ['/v1/messages/count_tokens?beta=true','/v1/messages?beta=true','/v1/messages?beta=true'])
                self.assertEqual([r[1] for r in records], [PRIMARY,PRIMARY,BACKUP])
        with upstream(lambda r,n:(200,b'ok',{})) as (url,records):
            with Proxy(url,PRIMARY,BACKUP,'responses',5,_test_http=True) as proxy:
                self.assertEqual(post(proxy,path='/responses?beta=true')[0],404)
                self.assertEqual(records,[])

    def test_distinct_credentials_and_https(self):
        with self.assertRaises(ValueError): Proxy('https://example.invalid','same','same','responses',1)
        with self.assertRaises(ValueError): Proxy('http://example.invalid',PRIMARY,BACKUP,'responses',1)

class LegacyWrapper(unittest.TestCase):
    def test_one_process_http_failover_and_sanitized_sidecar(self):
        import tempfile
        from unittest.mock import patch
        from bounded_review import main
        import token_plan_transport
        with tempfile.TemporaryDirectory() as temp, upstream(lambda r,n:(429,QUOTA,{}) if PRIMARY in r[1] else (200,b'ok',{})) as (url,records):
            root=Path(temp); secret=root/'synthetic-secrets.json'
            secret.write_text(json.dumps({'tokenplan':{'value':PRIMARY},'model_services':{'token_plan_backup':{'value':BACKUP}}}))
            anthropic = 'https://token-plan.maas.qianwenaiapi.com/apps/anthropic'
            responses = 'https://token-plan.maas.qianwenaiapi.com/compatible-mode/v1'
            profile = dict(model='glm-5.3', base_url=anthropic, responses_base_url=responses,
                           credential_paths=[str(secret)], credential_key='tokenplan.value')
            custom=dict(model='',base_url='',responses_base_url='',credential_paths=[],credential_key='model_services.custom_api.value')
            config={'plugins':{'qwen':profile},'model_services':{'version':1,'active_api':'token_plan','profiles':{'token_plan':profile,'custom_api':custom}},'token_plan_accounts':{'version':1,'auto_failover':True,'backup':{'credential_paths':[str(secret)],'credential_key':'model_services.token_plan_backup.value'}}}
            path=root/'config.json';path.write_text(json.dumps(config))
            script=root/'fake.py';script.write_text('''import json,os,urllib.request,pathlib,sys
assert os.environ['ANTHROPIC_API_KEY'] not in ('synthetic-primary-credential','synthetic-backup-credential')
url=os.environ['ANTHROPIC_BASE_URL']+'/v1/messages'
def call():
 request=urllib.request.Request(url,data=b'{"model":"glm-5.3","messages":[]}',headers={'x-api-key':os.environ['ANTHROPIC_API_KEY']})
 with urllib.request.urlopen(request) as response: assert response.read()==b'ok'
call()
p=pathlib.Path(sys.argv[1]);p.write_text(p.read_text()+'write\\n' if p.exists() else 'write\\n')
call()
print(json.dumps({'type':'result','subtype':'success','is_error':False,'result':'offline report','permission_denials':[]}))
''')
            material=root/'material.txt';material.write_text('fixed source')
            log=root/'review.json';report=root/'report.txt';count=root/'count.txt'
            def test_proxy(destination, *args, **kwargs):
                self.assertEqual(destination, anthropic)
                return Proxy(url + '/anthropic', *args, _test_http=True, **kwargs)
            with patch.object(token_plan_transport,'Proxy',side_effect=test_proxy):
                code=main(['--token-plan-config',str(path),'--log',str(log),'--report',str(report),'--retry-protocol','claude-json','--input',str(material),'--max-retries','0','--',sys.executable,str(script),str(count)])
            self.assertEqual(code,0);self.assertEqual(count.read_text(),'write\n')
            self.assertEqual(len(records),3);self.assertEqual(records[0][2],records[1][2])
            sidecar=log.with_name(log.name+'.accounts.json')
            state=json.loads(sidecar.read_text());self.assertEqual(state['switch_count'],1)
            self.assertEqual(sidecar.stat().st_mode & 0o777,0o600)
            for output in (sidecar,log,report):
                self.assertNotIn(PRIMARY,output.read_text());self.assertNotIn(BACKUP,output.read_text())
