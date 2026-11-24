"""Bounded synthetic checks; no business repository is ever read."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from config_store import StoreError, model, effective_executor
import model_services as services

OPTIONAL_SAVE_KEYS = ('dual_plan_enabled', 'agy_dual_plan_enabled', 'agy_review_model', 'agy_implementation_model')
CLI_PROBE_NAMES = ('claude', 'codex', 'agy', 'qodercli')
SEMVER_RE = re.compile(r'\b([0-9]+\.[0-9]+\.[0-9]+)\b')


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

def _find_executable(name, local=None):
    for candidate in ('/opt/homebrew/bin/' + name, '/usr/local/bin/' + name, shutil.which(name), local):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None

def discover(name):
    local = str(Path.home() / '.local/bin' / name) if name in CLI_PROBE_NAMES else None
    candidate = _find_executable(name, local)
    if candidate is None:
        raise CheckError('未找到 ' + name + ' CLI；请安装或设置启动环境')
    return candidate

def _has_tool(name):
    return _find_executable(name) is not None

def run(argv, cwd, log, timeout=8, limit=2*1024*1024, env=None, idle_timeout=None, termination_grace=5):
    global _active
    if _cancelled:
        raise CheckError('检测已取消')
    start = last_output = time.monotonic()
    last_size = 0
    with open(log, 'wb') as out:
        os.chmod(log, 0o600)
        _active = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                   env=env, start_new_session=True)
        reason = None
        try:
            while _active.poll() is None:
                now = time.monotonic()
                size = Path(log).stat().st_size
                if size != last_size:
                    last_size, last_output = size, now
                if _cancelled:
                    reason = '检测已取消'
                elif time.monotonic() - start > timeout:
                    reason = '检测超时，已终止进程'
                elif idle_timeout is not None and now - last_output >= idle_timeout:
                    reason = '检测空闲超时，已终止进程'
                elif Path(log).stat().st_size > limit:
                    reason = '检测输出超过上限，已终止进程'
                if reason:
                    try:
                        os.killpg(_active.pid, signal.SIGINT)
                    except ProcessLookupError:
                        pass
                    try:
                        _active.wait(timeout=termination_grace)
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

def cli_status(names):
    if not isinstance(names, list) or not names or any(
            not isinstance(name, str) or name not in CLI_PROBE_NAMES for name in names):
        raise StoreError('CLI 名称列表无效')
    items = {}
    with tempfile.TemporaryDirectory(prefix='ai-anti-cli-') as temp:
        for name in dict.fromkeys(names):
            if _cancelled:
                raise CheckError('检测已取消')
            items[name] = _cli_status_item(name, temp)
        if _cancelled:
            raise CheckError('检测已取消')
    return {'items': items, 'package_managers': {'brew': _has_tool('brew')}}

def _cli_status_item(name, temp):
    try:
        cli = discover(name)
        log = Path(temp) / 'version.log'
        if run([os.path.abspath(cli), '--version'], temp, log, timeout=3, limit=65536, termination_grace=1) != 0:
            return {'state': 'error', 'reason': '版本命令退出异常'}
        match = SEMVER_RE.search(log.read_text('utf-8', errors='replace'))
        if match:
            return {'state': 'installed', 'version': match.group(1)}
        reason = '无法识别版本号'
    except CheckError as exc:
        if exc.category == 'cancelled':
            raise
        if exc.category == 'cli_unavailable':
            return {'state': 'missing'}
        reason = {'timeout': '版本检测超时', 'output_limit': '版本输出超过上限'}.get(exc.category, '无法执行 CLI')
    except OSError:
        reason = '无法执行 CLI'
    return {'state': 'error', 'reason': reason}

def source_root(store, explicit=None, host=None):
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    else:
        entries = [(store.root / '.agents/skills/codex-relay/SKILL.md', 4),
                   (store.root / '.claude/commands/codex-review.md', 3)]
        agy_plugin = (store.root / '.gemini/config/plugins/ai-anti/plugin.json', 3)
        entries.insert(0 if host == 'agy' else len(entries), agy_plugin)
        for base, depth in entries:
            if base.is_file():
                resolved = base.resolve()
                if len(resolved.parents) > depth:
                    candidates.append(resolved.parents[depth])
    for root in candidates:
        if all((root / p).is_file() for p in ('core/REVIEW_PRINCIPLES.md', 'core/CLI_DISCIPLINE.md',
                'adapters/codex/skills/claude-review/scripts/review.py', 'adapters/claude-code/commands/codex-review.md')):
            return root.resolve()
    raise CheckError('找不到已安装 ai-anti 源码；使用 --source-root 指定经过验证的 checkout')

def agy_entry(store, root):
    plugin = root / 'adapters/agy/plugin'
    required = (plugin / 'plugin.json', plugin / 'rules/AGENTS.md',
                plugin / 'skills/agy-relay/SKILL.md', plugin / 'skills/codex-review/SKILL.md',
                root / 'adapters/agy/scripts/codex_review.py', root / 'adapters/agy/scripts/claude_plan.py')
    link = store.root / '.gemini/config/plugins/ai-anti'
    try:
        valid = link.is_symlink() and link.resolve() == plugin.resolve() and all(
            stat.S_ISREG(path.lstat().st_mode) and os.access(path, os.R_OK) and path.read_text('utf-8').strip()
            for path in required)
    except (OSError, UnicodeError, RuntimeError):
        valid = False
    if not valid:
        raise CheckError('AGY 插件未安装、入口不完整或指向其他 ai-anti 源码；请运行 install.sh --with-agy 或指定 --source-root')

def planning_version(root, log, cli):
    label = 'AGY' if cli == 'agy' else 'Claude'
    match = SEMVER_RE.search(log.read_text('utf-8', errors='replace'))
    if not match:
        raise CheckError(f'{label} CLI 版本输出无效')
    version = match.group(1)
    try:
        tested = json.loads((root / 'compat.json').read_text('utf-8'))['tested'][cli]
        if not isinstance(tested, list):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CheckError(f'{label} 实测版本清单无效') from exc
    return version, '在实测清单内' if version in tested else '未在实测清单'

def review_budgets(store, root, timeout=None):
    """Load the validated checkout's parser, using the Store's config path."""
    path = root / 'adapters/codex/skills/claude-review/scripts/ai_anti_config.py'
    if not path.is_file():
        raise CheckError('ai-anti 源码缺少 review_limits；请更新 ai-anti 源码')
    spec = importlib.util.spec_from_file_location('_aianti_check_config', path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        if not callable(getattr(module, 'review_limits', None)):
            raise CheckError('ai-anti 源码缺少 review_limits；请更新 ai-anti 源码')
        return module.review_limits(module.load(store.paths['ai']), timeout=timeout)
    except CheckError:
        raise
    except (ValueError, OSError, ImportError, SyntaxError) as exc:
        raise CheckError('评审预算配置或源码无效：' + str(exc)) from exc

def check_claude_codex_version(root, log):
    """Use the pinned versions and parser from the formal implementation entry."""
    path = root / 'adapters/claude-code/scripts/codex_implement.py'
    if not path.is_file():
        raise CheckError('ai-anti 源码缺少 Codex 实施入口；请更新 ai-anti 源码')
    spec = importlib.util.spec_from_file_location('_aianti_check_codex_implement', path)
    entry = importlib.util.module_from_spec(spec)
    original_path = sys.path[:]
    try:
        spec.loader.exec_module(entry)
        versions = entry.VERSIONS
    except (AttributeError, OSError, ImportError, SyntaxError) as exc:
        raise CheckError('Codex 实施版本检查所需源码无效') from exc
    finally:
        # The formal entry adds its sibling scripts to the import path.
        sys.path[:] = original_path
    try:
        version, _ = entry.parse_version_output(log.read_text('utf-8'))
    except (entry.RunError, OSError, UnicodeError) as exc:
        raise CheckError('Codex 实施 CLI 版本输出无效') from exc
    if version not in versions:
        raise CheckError('Codex 实施要求 ' + '、'.join(versions))

def result(kind, state, message, snapshot, actual='未知'):
    return dict(kind=kind, state=state, message=message, signature=snapshot['signature'], actual_model=actual,
                checked_at=time.strftime('%Y-%m-%d %H:%M:%S'), timestamp=time.time())

def local_agy(store, snapshot, source):
    v = snapshot['values']
    root = source_root(store, source, host='agy')
    review_budgets(store, root)
    agy_entry(store, root)
    agy_impl = v['mode'] != 'solo'
    if agy_impl and not v['agy_implementation_model']:
        raise CheckError('AGY 实施未配置 agy_relay.implementation；实施阶段会停止')
    names = {'agy'}
    dual = agy_impl and v['agy_dual_plan_enabled']
    if dual:
        names.add('claude')
    if agy_impl:
        names.add('codex')
    with tempfile.TemporaryDirectory(prefix='ai-anti-local-') as temp:
        for name in names:
            cli = discover(name)
            log = Path(temp) / (name + '.log')
            if run([cli, '--version'], temp, log) != 0:
                raise CheckError(name + ' --version 失败；原始输出已隐藏')
            if name == 'agy':
                version, compatibility = planning_version(root, log, 'agy')
            elif name == 'claude':
                claude_version, claude_compat = planning_version(root, log, 'claude')
            else:
                check_claude_codex_version(root, log)
    if store.read()['signature'] != snapshot['signature']:
        raise CheckError('检测中配置或凭证发生变化，结果作废')
    route = ('Codex · ' + v['agy_implementation_model'] + ' · 关联工作树实施（AGY 接力）'
             if agy_impl else '停用独立实施')
    reviewer = v['agy_review_model'] or 'CLI 默认'
    dual_detail = ''
    if dual:
        partner_model = v['agy_dual_plan']['model'] or 'CLI 默认'
        dual_detail = f'；双方案：Claude · {partner_model}（Claude {claude_version} {claude_compat}）'
    return result('local', 'passed', f'本地检查通过；实施：{route}；评审：Codex · {reviewer}；'
                  f'AGY {version}（{compatibility}）{dual_detail}；未验证登录、额度或实施模型可调用，评审检测不能替代实施证据', snapshot)

def official_review(values):
    return values['official_review'] if values.get('channel') == services.OFFICIAL_CHANNEL else None


def is_token_plan_review(values):
    official = official_review(values)
    if official:
        return official['service'] == 'token_plan'
    return values['channel'] == 'qwen' and values['model_services']['active_api'] == 'token_plan'


def validate_review_destination(values):
    if not is_token_plan_review(values):
        return
    executor = review_executor(values)
    wire_api = 'responses' if executor == 'codex' else 'anthropic'
    field = 'responses_base_url' if executor == 'codex' else 'base_url'
    profile = values['model_services']['profiles']['token_plan']
    services.token_plan_destination(profile[field], wire_api, CheckError)


def review_slot(values):
    official = official_review(values)
    if official is None:
        return values['credential_paths'], values.get('credential_key', 'tokenplan.value')
    if official['service'] == 'token_plan':
        profile = values['model_services']['profiles']['token_plan']
        return profile['credential_paths'], profile['credential_key']
    return vendor_slot(official)


def vendor_slot(selection):
    return [services.OWN_CREDENTIAL_PATH], selection['service'] + '.value'


def review_executor(values):
    return 'codex' if official_review(values) else effective_executor(values)


def review_model(values):
    official = official_review(values)
    return official['model'] if official else values['qwen_model']


def local(store, source=None):
    snapshot = store.read()
    v = snapshot['values']
    if v['host'] == 'agy':
        for key in ('agy_dual_plan_error', 'agy_relay_error'):
            if v[key]:
                raise CheckError(v[key])
    if v['channel'] == 'qoder' and v['host'] != 'codex': raise CheckError('Qoder 暂不支持 Claude 主模型')
    validate_review_destination(v)
    # Validation builds bytes but never writes them.
    check_values = {k: val for k, val in v.items() if k not in OPTIONAL_SAVE_KEYS}
    store.outputs(check_values, '')
    if v['host'] == 'agy':
        return local_agy(store, snapshot, source)
    claude_codex_implementation = v['host'] == 'claude' and v['mode'] != 'solo' and v['claude_implementation_provider'] == 'codex'
    try:
        root = source_root(store, source)
    except CheckError as exc:
        if claude_codex_implementation:
            raise CheckError('无法定位 ai-anti 源码，未检查实施版本') from exc
        raise
    review_budgets(store, root)
    active_implementation = v['host'] == 'codex' and v['mode'] != 'solo'
    implementation = active_implementation and v['implementation_provider'] == 'token_plan'
    direct_implementation = v['direct_implementation'] if active_implementation and v['implementation_provider'] == 'custom_api' else None
    api_review = v['channel'] in ('qwen', services.OFFICIAL_CHANNEL)
    if implementation:
        profile = v['model_services']['profiles']['token_plan']
        if not profile['responses_base_url']:
            raise CheckError('实施 Token Plan Responses 地址未配置')
        if not store.credential_status(profile['credential_paths'], profile['credential_key'])[0]:
            raise CheckError('实施 Token Plan 凭证未配置或不可解析')
    if direct_implementation and not store.credential_status(*vendor_slot(direct_implementation))[0]:
        raise CheckError('实施厂商凭证未配置或不可解析')
    if v['token_plan_accounts']['auto_failover'] and (implementation or is_token_plan_review(v)):
        validate_accounts(store, v)
    qoder_implementation = active_implementation and v['implementation_provider'] == 'qoder'
    names = {v['host'], review_executor(v) if api_review else 'qodercli' if v['channel'] == 'qoder' else 'codex'}
    if qoder_implementation:
        names.add('qodercli')
    if claude_codex_implementation:
        names.add('codex')
    with tempfile.TemporaryDirectory(prefix='ai-anti-local-') as temp:
        for name in names:
            cli = discover(name)
            log = Path(temp) / (name + '.log')
            if run([cli, '--version'], temp, log) != 0:
                raise CheckError(name + ' --version 失败；原始输出已隐藏')
            if name == 'codex' and claude_codex_implementation:
                check_claude_codex_version(root, log)
            if name == 'qodercli' and qoder_implementation:
                version = log.read_text('utf-8').strip()
                if version != '1.1.77':
                    raise CheckError('Qoder 实施要求 qodercli 1.1.77')
    if api_review and not store.credential_status(*review_slot(v))[0]:
        raise CheckError('没有找到可解析的评审服务凭证')
    if store.read()['signature'] != snapshot['signature']:
        raise CheckError('检测中配置或凭证发生变化，结果作废')
    if direct_implementation:
        route = direct_implementation['service'] + ' · ' + direct_implementation['model'] + ' · Chat Completions'
    elif claude_codex_implementation:
        route = 'Codex · ' + v['claude_implementation_model'] + ' · 关联工作树实施'
    else:
        route = ('停用独立实施' if v['mode'] == 'solo' else 'Claude Code 实施代理' if v['host'] == 'claude' else 'Qoder 订阅 · GLM-5.3 · qodercli 1.1.77' if qoder_implementation else 'Token Plan · ' + v['token_plan_implementation_model'] + ' · Responses' if implementation else 'Codex 原生 · ' + (v['secondary_model'] or '未指定'))
    return result('local', 'passed', '本地检查通过；实施：' + route + '；未验证登录、额度或实施模型可调用，评审检测不能替代实施证据', snapshot)

def validate_accounts(store, values):
    import model_services
    account = values['token_plan_accounts']
    profiles = values['model_services']['profiles']
    tokens = []
    for slot in (profiles['token_plan'], account['backup']):
        found, path = store.credential_status(slot['credential_paths'], slot['credential_key'])
        if not found: raise CheckError('自动切换需要主账号与备用账号凭证')
        value = model_services.lookup(json.loads(path.read_text('utf-8')), slot['credential_key'])
        if not value or any(c.isspace() for c in value) or '\x00' in value:
            raise CheckError('Token Plan 密钥格式无效')
        tokens.append(value)
    if tokens[0] == tokens[1]: raise CheckError('主账号与备用账号必须使用不同凭证')

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
    executor = review_executor(values)
    expected = dict(channel='qwen', executor=executor, wire_api='responses' if executor == 'codex' else 'anthropic', requested_model=review_model(values))
    official = official_review(values)
    if official:
        expected['service'] = official['service']
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

def qoder_evidence(status, manifest):
    if status.get('completed') is not True or manifest.get('mode') != 'diff': raise ValueError()
    for item in (status, manifest):
        if any(item.get(k) != v for k,v in dict(status='completed',channel='qoder',executor='qoder',wire_api='qoder-cli',requested_model='kimi-k3.1-pro',actual_model='Kimi-K3.1-Pro').items()): raise ValueError()
    return 'Kimi-K3.1-Pro'

def real(store, source=None, timeout=None):
    snapshot = store.read()
    v = snapshot['values']
    if v['channel'] == 'qoder' and v['host'] != 'codex': raise CheckError('Qoder 暂不支持 Claude 主模型')
    if v['mode'] == 'solo': raise CheckError('solo 模式停用交叉评审')
    if v['host'] == 'agy':
        raise CheckError('AGY 主模型暂不支持桌面端合成评审检测；请使用「检查配置」或从 AGY 发起评审')
    validate_review_destination(v)
    check_values = {k: val for k, val in v.items() if k not in OPTIONAL_SAVE_KEYS}
    store.outputs(check_values, '')
    root = source_root(store, source)
    timeout, idle_timeout = review_budgets(store, root, timeout)
    api_review = v['channel'] in ('qwen', services.OFFICIAL_CHANNEL)
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
        if api_review:
            if not store.credential_status(*review_slot(v))[0]:
                raise CheckError('评审服务凭证不存在或不可解析')
            # Formal runner retains full reads, tool/permission and actual model gates.
            argv = [sys.executable, str(root / 'adapters/codex/skills/claude-review/scripts/review.py'), 'diff',
                    '--repo', str(repo), '--files', 'sample.txt', '--scale', '单用户本机合成检测，无部署',
                    '--out', str(out), '--channel', v['channel'], '--qwen-executor', review_executor(v),
                    '--timeout', str(timeout), '--idle-timeout', str(idle_timeout), '--max-output-bytes', str(8*1024*1024)]
            if v['channel'] == 'qwen' and review_executor(v) == 'codex':
                argv += ['--responses-base-url', v['responses_base_url']]
            if run(argv, repo, temp / 'runner.log', timeout=timeout+60, limit=8*1024*1024, env=env) != 0:
                raise CheckError('API 评审正式入口检测失败；日志已隐藏，已按有限重试策略结束，未降级')
            try:
                status = json.loads((out / 'status.json').read_text())
                manifest = json.loads((out / 'manifest.json').read_text())
                actual = qwen_evidence(status, manifest, v)
            except (OSError, ValueError, KeyError):
                raise CheckError('API 评审正式入口证据不完整或实际模型不匹配') from None
        elif v['channel'] == 'qoder':
            argv = [sys.executable, str(root / 'adapters/codex/skills/claude-review/scripts/review.py'), 'diff',
                    '--repo', str(repo), '--files', 'sample.txt', '--scale', '单用户本机合成检测，无部署',
                    '--out', str(out), '--channel', 'qoder', '--model', 'kimi-k3.1-pro', '--timeout', str(timeout),
                    '--idle-timeout', str(idle_timeout), '--max-output-bytes', str(2*1024*1024)]
            if run(argv, repo, temp / 'runner.log', timeout=timeout+60, limit=2*1024*1024, env=env) != 0:
                raise CheckError('Qoder 正式入口检测失败；已按有限重试策略结束，未降级')
            try:
                status = json.loads((out / 'status.json').read_text())
                manifest = json.loads((out / 'manifest.json').read_text())
                actual = qoder_evidence(status, manifest)
            except (OSError, ValueError, KeyError):
                raise CheckError('Qoder 正式入口证据不完整或实际模型不匹配') from None
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
            if run(argv, repo, log, timeout=timeout, env=env, idle_timeout=idle_timeout) != 0:
                raise CheckError('Codex 合成调用失败；原始错误已隐藏，未自动重试')
            actual = codex_events(log, marker)
        if store.read()['signature'] != snapshot['signature']:
            raise CheckError('检测中配置或凭证发生变化，结果作废')
        reply = result('real', 'passed', '合成评审检测通过；不代表业务评审全量验证', snapshot, actual)
        if api_review:
            reply.update(token_plan_accounts=status.get('token_plan_accounts', dict(enabled=False)), requested_model=review_model(v), model_evidence='requested_only' if review_executor(v) == 'codex' else 'actual')
        return reply
