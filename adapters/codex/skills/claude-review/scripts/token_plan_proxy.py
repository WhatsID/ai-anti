"""Bounded, authenticated loopback transport for one Token Plan logical run.

Only an explicit HTTP 429 quota rejection before downstream headers permits one
same-byte replay. No provider text, credentials, or request bodies enter evidence.
"""
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import secrets
import socket
import ssl
import threading
import time
import weakref
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler, HTTPHandler, HTTPSHandler, HTTPRedirectHandler, Request
from urllib.error import HTTPError

MAX_BODY = 8 * 1024 * 1024
MAX_ERROR = 64 * 1024
HOP = {'connection','keep-alive','proxy-authenticate','proxy-authorization','te','trailer','transfer-encoding','upgrade','host','content-length','authorization','x-api-key','accept-encoding'}
QUOTA = re.compile(r'Your token-plan [\w -]+ quota has been exhausted\.', re.I)

def quota_reason(status, body):
    if status != 429 or len(body) > MAX_ERROR: return None
    try: data = json.loads(body)
    except (ValueError, UnicodeError): return None
    if not isinstance(data,dict): return None
    error = data.get('error', data)
    if not isinstance(error,dict): return None
    if any(error.get(k) in ('insufficient_quota', 'quota_exhausted') for k in ('code','type')):
        return 'quota_exhausted'
    message = error.get('message')
    if isinstance(message,str) and QUOTA.search(message): return 'token_plan_exhausted'
    return None

def replayable(body):
    try: data = json.loads(body)
    except (ValueError, UnicodeError): return False
    return isinstance(data,dict) and not any(data.get(k) for k in ('previous_response_id','conversation'))

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None

class Proxy:
    def __init__(self, upstream, primary, backup, wire_api, budget, *, idle_timeout=900, _test_http=False):
        p = urlsplit(upstream)
        if (p.scheme != 'https' and not (_test_http and p.scheme == 'http' and p.hostname == '127.0.0.1')) or not p.hostname or p.username or p.password or p.query or p.fragment:
            raise ValueError('Token Plan failover requires a credential-free HTTPS upstream')
        if not primary or not backup or primary == backup: raise ValueError('Token Plan failover requires two distinct credentials')
        self.upstream = upstream.rstrip('/')
        self.wire_api = wire_api
        self.path = p.path.rstrip('/') + ('/responses' if wire_api == 'responses' else '/v1/messages')
        # Anthropic clients append /v1/messages to the configured base.
        self.paths = {self.path}
        if wire_api == 'anthropic': self.paths.add(self.path + '/count_tokens')
        self.keys = (primary, backup)
        self.token = secrets.token_urlsafe(48)
        self.deadline = time.monotonic() + budget
        self.lock = threading.Lock()
        self.active_lock = threading.Lock()
        self.active = weakref.WeakSet()
        self.idle_timeout = idle_timeout
        self.slots = threading.BoundedSemaphore(4)
        self.admission = threading.BoundedSemaphore(4)
        self.closed = threading.Event()
        self.state = dict(enabled=True, used_slot='primary', switch_count=0, switches=[], non_replayable_requests=0)
        self.slot = 0
        # urllib follows proxy environment for upstream, and validates TLS normally.
        cafile = os.environ.get('SSL_CERT_FILE') or os.environ.get('REQUESTS_CA_BUNDLE') or os.environ.get('CURL_CA_BUNDLE')
        context = ssl.create_default_context(cafile=cafile, capath=os.environ.get('SSL_CERT_DIR'))
        owner = self
        def tracked_connect(address, timeout, source_address=None):
            # Register before connect/header wait so cancellation can interrupt it.
            last = None
            for family, kind, proto, _, sockaddr in socket.getaddrinfo(*address, 0, socket.SOCK_STREAM):
                sock = socket.socket(family,kind,proto)
                with owner.active_lock: owner.active.add(sock)
                try:
                    sock.settimeout(timeout)
                    if source_address: sock.bind(source_address)
                    sock.connect(sockaddr)
                    return sock
                except OSError as exc:
                    last = exc; sock.close()
            if last: raise last
            raise OSError('No upstream address')
        class TrackedHTTP(http.client.HTTPConnection):
            def __init__(connection,*args,**kwargs):
                super().__init__(*args,**kwargs); connection._create_connection = tracked_connect
        class TrackedHTTPS(http.client.HTTPSConnection):
            def __init__(connection,*args,**kwargs):
                super().__init__(*args,**kwargs); connection._create_connection = tracked_connect
            def connect(connection):
                http.client.HTTPConnection.connect(connection)
                hostname = connection._tunnel_host or connection.host
                connection.sock = context.wrap_socket(connection.sock, server_hostname=hostname, do_handshake_on_connect=False)
                with owner.active_lock: owner.active.add(connection.sock)
                connection.sock.do_handshake()
        class HTTP(HTTPHandler):
            def http_open(handler,request): return handler.do_open(TrackedHTTP,request)
        class HTTPS(HTTPSHandler):
            def https_open(handler,request): return handler.do_open(TrackedHTTPS,request,context=context)
        # urllib supports HTTP(S)_PROXY; explicitly honor ALL_PROXY as fallback.
        from urllib.request import getproxies
        proxies = getproxies()
        if proxies.get('all'):
            for scheme in ('http','https'): proxies.setdefault(scheme,proxies['all'])
        for scheme in ('http','https'):
            proxy_url = proxies.get(scheme)
            if proxy_url and urlsplit(proxy_url if '://' in proxy_url else 'http://' + proxy_url).scheme not in ('http','https'):
                raise ValueError('Token Plan transport supports HTTP(S) upstream proxies only')
        self.opener = build_opener(ProxyHandler(proxies), HTTP(), HTTPS(), NoRedirect())
        owner = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass
            def setup(self):
                super().setup(); self.connection.settimeout(min(15, max(.1, owner.remaining())))
                with owner.active_lock: owner.active.add(self.connection)
            def finish(self):
                try: super().finish()
                finally:
                    with owner.active_lock: owner.active.discard(self.connection)
            def reject(self, code, message):
                raw = json.dumps({'error':{'type':'local_transport_error','message':message}}).encode()
                self.send_response(code); self.send_header('Content-Length',str(len(raw))); self.send_header('Connection','close'); self.end_headers()
                self.wfile.write(raw); self.close_connection = True
            def do_POST(self):
                if not owner.slots.acquire(blocking=False): return self.reject(503,'Local concurrency limit')
                try: self.forward()
                except (OSError, ValueError, TimeoutError, http.client.HTTPException):
                    if not getattr(self, 'sent', False):
                        try: self.reject(502,'Token Plan transport failed')
                        except OSError: pass
                    self.close_connection = True
                finally: owner.slots.release()
            def forward(self):
                self.sent = False
                host = '127.0.0.1:' + str(owner.server.server_port)
                auth = self.headers.get('Authorization','') if owner.wire_api == 'responses' else self.headers.get('x-api-key','')
                expected = 'Bearer ' + owner.token if owner.wire_api == 'responses' else owner.token
                if len(self.headers.get_all('Authorization' if owner.wire_api == 'responses' else 'x-api-key',[])) != 1 or len(self.headers.get_all('Host',[])) != 1 or self.headers['Host'] != host or not hmac.compare_digest(auth.encode('utf-8'), expected.encode('utf-8')): return self.reject(401,'Local authentication required')
                if self.path not in owner.paths: return self.reject(404,'Unsupported endpoint')
                if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Encoding') or self.headers.get('Expect'):
                    return self.reject(400,'Unsupported transfer encoding')
                lengths = self.headers.get_all('Content-Length', [])
                if len(lengths) != 1 or not lengths[0].isdigit(): return self.reject(411,'One Content-Length required')
                size = int(lengths[0])
                if size > MAX_BODY: return self.reject(413,'Request exceeds 8 MiB limit')
                body = self.rfile.read(size)
                if len(body) != size: return self.reject(400,'Incomplete request')
                connection_names = {x.strip().lower() for x in self.headers.get('Connection','').split(',')}
                headers = {k:v for k,v in self.headers.items() if k.lower() not in HOP | connection_names}
                headers['Accept-Encoding'] = 'identity'
                headers['Content-Length'] = str(size)
                # Synchronize selection through response classification. New requests
                # cannot race back to a primary already proved exhausted.
                with owner.lock:
                    for attempt in range(2):
                        owner.check_deadline()
                        slot = owner.slot
                        hdr = dict(headers)
                        hdr['Authorization' if owner.wire_api == 'responses' else 'x-api-key'] = ('Bearer ' if owner.wire_api == 'responses' else '') + owner.keys[slot]
                        request = Request(owner.upstream + self.path[len(urlsplit(owner.upstream).path.rstrip('/')):], data=body, headers=hdr, method='POST')
                        try: response = owner.opener.open(request, timeout=min(owner.idle_timeout, owner.remaining()))
                        except HTTPError as error: response = error
                        upstream_socket = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
                        with owner.active_lock:
                            owner.active.add(response)
                            if upstream_socket: owner.active.add(upstream_socket)
                        try:
                            status = response.status
                            if 300 <= status < 400: return self.reject(502,'Upstream redirects are prohibited')
                            if sum(len(k)+len(v) for k,v in response.headers.items()) > MAX_ERROR:
                                return self.reject(502,'Upstream headers exceed limit')
                            error_body = None
                            if status >= 300:
                                error_body = response.read(MAX_ERROR + 1)
                                if len(error_body) > MAX_ERROR: return self.reject(502,'Upstream error exceeds limit')
                            reason = quota_reason(status,error_body or b'') if self.path == owner.path else None
                            if reason and slot == 0 and attempt == 0:
                                if replayable(body):
                                    owner.slot = 1
                                    owner.state.update(used_slot='backup', switch_count=1)
                                    owner.state['switches'].append(dict(http_status=429, reason=reason))
                                    continue
                                owner.state['non_replayable_requests'] += 1
                            owner.state['used_slot'] = 'backup' if slot else 'primary'
                            if response.headers.get('Content-Encoding') not in (None,'identity'):
                                return self.reject(502,'Unsupported upstream encoding')
                            if error_body is not None:
                                for secret in owner.keys:
                                    error_body = error_body.replace(secret.encode('utf-8'), b'[redacted]')
                            self.send_response(status)
                            for k,v in response.headers.items():
                                if k.lower() not in HOP and k.lower() not in ('set-cookie',):
                                    for secret in owner.keys: v = v.replace(secret, '[redacted]')
                                    self.send_header(k,v)
                            if error_body is not None: self.send_header('Content-Length',str(len(error_body)))
                            self.send_header('Connection','close'); self.end_headers(); self.sent = True
                            if error_body is not None:
                                self.wfile.write(error_body)
                            else:
                                total = 0
                                while True:
                                    owner.check_deadline()
                                    chunk = response.read1(min(65536, MAX_BODY + 1 - total))
                                    if not chunk: break
                                    total += len(chunk)
                                    if total > MAX_BODY: raise ValueError('Response exceeds limit')
                                    self.wfile.write(chunk); self.wfile.flush()
                            self.close_connection = True
                            return
                        finally:
                            response.close()
                            with owner.active_lock:
                                owner.active.discard(response)
                                if upstream_socket: owner.active.discard(upstream_socket)
            def do_GET(self): self.reject(405,'POST required')
            do_CONNECT = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = do_GET
        class Server(ThreadingHTTPServer):
            daemon_threads = False
            block_on_close = True
            def handle_error(server, request, address):
                pass  # Never dump provider headers or error bytes to stderr.
            def process_request(server, request, address):
                # Bound threads as well as upstream connections.
                if not owner.admission.acquire(blocking=False):
                    request.close(); return
                try: super().process_request(request,address)
                except BaseException:
                    owner.admission.release(); raise
            def process_request_thread(server, request, address):
                try: super().process_request_thread(request,address)
                finally: owner.admission.release()
        self.server = Server(('127.0.0.1',0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval':.05}, daemon=True)
        self.timer = threading.Timer(budget, self.close)
        self.timer.daemon = True
    def remaining(self): return max(.001, self.deadline - time.monotonic())
    def check_deadline(self):
        if self.closed.is_set() or time.monotonic() >= self.deadline: raise TimeoutError('Token Plan transport deadline')
    @property
    def base_url(self): return 'http://127.0.0.1:' + str(self.server.server_port) + urlsplit(self.upstream).path.rstrip('/')
    def __enter__(self):
        self.thread.start(); self.timer.start(); return self
    def evidence(self):
        # Copy without waiting on a streaming request's account lock.
        return json.loads(json.dumps(self.state))
    def close(self):
        if self.closed.is_set(): return
        self.closed.set(); self.timer.cancel()
        with self.active_lock:
            active = list(self.active)
        for obj in active:
            if isinstance(obj,socket.socket):
                try: obj.shutdown(socket.SHUT_RDWR)
                except OSError: pass
        for obj in active:
            try:
                obj.close()
            except (OSError, ValueError): pass
        self.server.shutdown(); self.server.server_close()
        if threading.current_thread() is not self.thread: self.thread.join(timeout=2)
    def __exit__(self,*args): self.close()
