"""Bounded synthetic checks; no business repository is ever read."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from config_store import StoreError, model, effective_executor

class CheckError(StoreError):
    def __init__(self, message):
        super().__init__(message)
        self.category = ('cancelled' if '取消' in message else
                         'timeout' if '超时' in message else
                         'output_limit' if '上限' in message else
                         'cli_unavailable' if '未找到' in message or '缺少' in message else
                         'invalid_evidence' if '证据' in message or '未完成' in message else
                         'stale_result' if '作废' in message else 'check_failed')

_active = None
_cancelled = False

def stop(signum=None, frame=None):
    global _cancelled
    _cancelled = True
    if _active is not None and _active.poll() is None:
        try:
            os.killpg(_active.pid, signal.SIGINT)
        except ProcessLookupError:
            pass

def discover(name):
    for candidate in ('/opt/homebrew/bin/' + name, '/usr/local/bin/' + name, shutil.which(name)):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise CheckError('未找到 ' + name + ' CLI；请安装或设置启动环境')

def run(argv, cwd, log, timeout=8, limit=2*1024*1024, env=None):
    global _active
    if _cancelled:
        raise CheckError('检测已取消')
    start = time.monotonic()
    with open(log, 'wb') as out:
        os.chmod(log, 0o600)
        _active = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                   env=env, start_new_session=True)
        reason = None
        try:
            while _active.poll() is None:
                if _cancelled:
                    reason = '检测已取消'
                elif time.monotonic() - start > timeout:
                    reason = '检测超时，已终止进程'
                elif Path(log).stat().st_size > limit:
                    reason = '检测输出超过上限，已终止进程'
                if reason:
                    os.killpg(_active.pid, signal.SIGINT)
                    try:
                        _active.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(_active.pid, signal.SIGKILL)
                        _active.wait()
                    raise CheckError(reason)
                time.sleep(.1)
            code = _active.returncode
            if _cancelled:
                raise CheckError('检测已取消')
            if Path(log).stat().st_size > limit:
                raise CheckError('检测输出超过上限')
            return code
        finally:
            # Kill descendants too, even if the immediate process exited first.
            try:
                os.killpg(_active.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            _active = None

def source_root(store, explicit=None):
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    else:
        for base in (store.root / '.agents/skills/codex-relay/SKILL.md', store.root / '.claude/commands/codex-review.md'):
            if base.is_file():
                resolved = base.resolve()
                candidates.append(resolved.parents[4] if base.name == 'SKILL.md' else resolved.parents[3])
    for root in candidates:
        if all((root / p).is_file() for p in ('core/REVIEW_PRINCIPLES.md', 'core/CLI_DISCIPLINE.md',
                'adapters/codex/skills/claude-review/scripts/review.py', 'adapters/claude-code/commands/codex-review.md')):
            return root.resolve()
    raise CheckError('找不到已安装 ai-anti 源码；使用 --source-root 指定经过验证的 checkout')

def result(kind, state, message, snapshot, actual='未知'):
    return dict(kind=kind, state=state, message=message, signature=snapshot['signature'], actual_model=actual,
                checked_at=time.strftime('%Y-%m-%d %H:%M:%S'), timestamp=time.time())

def local(store, source=None):
    snapshot = store.read()
    v = snapshot['values']
    # Validation builds bytes but never writes them.
    store.outputs(v, '')
    source_root(store, source)
    names = {v['host'], effective_executor(v) if v['channel'] == 'qwen' else 'codex'}
    with tempfile.TemporaryDirectory(prefix='ai-anti-local-') as temp:
        for name in names:
            cli = discover(name)
            if run([cli, '--version'], temp, Path(temp) / (name + '.log')) != 0:
                raise CheckError(name + ' --version 失败；原始输出已隐藏')
    if v['channel'] == 'qwen' and not store.credential_status(v['credential_paths'])[0]:
        raise CheckError('没有找到可解析的 Qwen 凭证')
    if store.read()['signature'] != snapshot['signature']:
        raise CheckError('检测中配置或凭证发生变化，结果作废')
    return result('local', 'passed', '本地检查通过；未验证登录、额度或模型可调用', snapshot)

def codex_events(path, marker):
    finished, errors, response, actual = False, False, [], '未知'
    try:
        for line in Path(path).read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue  # Some CLI versions emit diagnostic lines before JSON.
            if not isinstance(event, dict):
                continue
            typ = event.get('type', '')
            if typ in ('error', 'turn.failed') or event.get('error'):
                errors = True
            if typ == 'turn.completed':
                finished = True
            if typ == 'item.completed':
                item = event.get('item', {})
                if item.get('type') == 'agent_message' and isinstance(item.get('text'), str):
                    response.append(item['text'])
            # Only a structured field is model evidence. Never infer from requested argv.
            if isinstance(event.get('model'), str):
                actual = event['model']
    except (OSError, UnicodeError):
        raise CheckError('Codex 结构化输出无法读取') from None
    if errors or not finished or marker not in '\n'.join(response):
        raise CheckError('Codex 未完成有效合成材料读取；退出码不足以证明成功')
    return actual

def qwen_evidence(status, manifest, values):
    executor = effective_executor(values)
    expected = dict(channel='qwen', executor=executor, wire_api='responses' if executor == 'codex' else 'anthropic', requested_model=values['qwen_model'])
    if status.get('completed') is not True or manifest.get('mode') != 'diff':
        raise ValueError()
    for item in (status, manifest):
        if item.get('status') != 'completed' or any(item.get(k) != v for k, v in expected.items()):
            raise ValueError()
        if executor == 'codex':
            if 'actual_model' not in item or item['actual_model'] is not None or item.get('model_evidence') != 'requested_only':
                raise ValueError()
        elif item.get('actual_model') != values['qwen_model']:
            raise ValueError()
    return status['actual_model']

def real(store, source=None, timeout=None):
    snapshot = store.read()
    v = snapshot['values']
    store.outputs(v, '')
    root = source_root(store, source)
    timeout = timeout if timeout is not None else (1800 if v['channel'] == 'qwen' else 1200)
    with tempfile.TemporaryDirectory(prefix='ai-anti-probe-') as temp:
        temp = Path(temp).resolve()
        os.chmod(temp, 0o700)
        repo, out = temp / 'fixture', temp / 'output'
        repo.mkdir()
        env = dict(os.environ)
        env['PATH'] = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:' + env.get('PATH', '')
        if store.isolated:
            env['HOME'] = str(store.root)
            env['CODEX_HOME'] = str(store.root / '.codex')
            env['CLAUDE_CONFIG_DIR'] = str(store.root / '.claude')
        marker = 'AIANTI_' + uuid.uuid4().hex
        (repo / 'sample.txt').write_text('synthetic baseline\n')
        git = discover('git')
        for args in (['init'], ['add', 'sample.txt'], ['-c', 'user.name=Fixture', '-c', 'user.email=fixture.invalid', 'commit', '-m', '合成检测基线']):
            if run([git, *args], repo, temp / 'git.log', env=env) != 0:
                raise CheckError('无法创建隔离合成仓库')
        (repo / 'sample.txt').write_text('synthetic change\nREAD_TOKEN=' + marker + '\n')
        if v['channel'] == 'qwen':
            if not store.credential_status(v['credential_paths'])[0]:
                raise CheckError('Qwen 凭证不存在或不可解析')
            # Formal runner retains full reads, tool/permission and actual model gates.
            import sys
            argv = [sys.executable, str(root / 'adapters/codex/skills/claude-review/scripts/review.py'), 'diff',
                    '--repo', str(repo), '--files', 'sample.txt', '--scale', '单用户本机合成检测，无部署',
                    '--out', str(out), '--channel', 'qwen', '--qwen-executor', effective_executor(v),
                    '--timeout', str(timeout), '--idle-timeout', '900', '--max-output-bytes', str(8*1024*1024)]
            if effective_executor(v) == 'codex':
                argv += ['--responses-base-url', v['responses_base_url']]
            if run(argv, repo, temp / 'runner.log', timeout=timeout+60, limit=8*1024*1024, env=env) != 0:
                raise CheckError('Qwen 正式入口检测失败；日志已隐藏，未自动重试或降级')
            try:
                status = json.loads((out / 'status.json').read_text())
                manifest = json.loads((out / 'manifest.json').read_text())
                actual = qwen_evidence(status, manifest, v)
            except (OSError, ValueError, KeyError):
                raise CheckError('Qwen 正式入口证据不完整或实际模型不匹配') from None
        else:
            cli = discover('codex')
            help_log = temp / 'help.log'
            if run([cli, 'exec', '--help'], repo, help_log, env=env) != 0:
                raise CheckError('无法检查 Codex CLI 能力')
            help_text = help_log.read_text(errors='replace')
            required = ('--json', '--sandbox', '--ephemeral', '--ignore-user-config', '--ignore-rules')
            if any(flag not in help_text for flag in required):
                raise CheckError('Codex CLI 缺少隔离检测所需参数；请升级后重试')
            chosen = v['reviewer_model']
            if not chosen:
                raise CheckError('真实 Codex 检测需要明确填写评审模型 ID')
            model(chosen)
            prompt = temp / 'prompt.txt'
            prompt.write_text('Read sample.txt in this synthetic repository using your read-only tools. Return the exact READ_TOKEN value found in the file. Do not modify anything. This is only a model availability check, not a business review.\n')
            argv = [cli, 'exec', '--sandbox', 'read-only', '--json', '--ephemeral', '--ignore-user-config', '--ignore-rules', '--model', chosen, prompt.read_text()]
            log = temp / 'codex.jsonl'
            if run(argv, repo, log, timeout=timeout, env=env) != 0:
                raise CheckError('Codex 合成调用失败；原始错误已隐藏，未自动重试')
            actual = codex_events(log, marker)
        if store.read()['signature'] != snapshot['signature']:
            raise CheckError('检测中配置或凭证发生变化，结果作废')
        reply = result('real', 'passed', '合成评审检测通过；不代表业务评审全量验证', snapshot, actual)
        if v['channel'] == 'qwen':
            reply.update(requested_model=v['qwen_model'], model_evidence='requested_only' if effective_executor(v) == 'codex' else 'actual')
        return reply
