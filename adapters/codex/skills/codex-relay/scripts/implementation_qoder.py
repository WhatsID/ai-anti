"""Subscription implementation executor; no retry, login/config mutation or sandbox claim."""
import hashlib
import json
import os
from pathlib import Path
import shutil
from implementation_qoder_protocol import VERSION, MODEL, TOOLS, validate
from review_runner import execute, RunError

ALLOWED_ENV = ('HOME', 'PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
               'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
               'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE')


def settings():
    return dict(disableAllHooks=True, skills=dict(enabled=False, disableShellExecution=True),
                autoMemoryEnabled=False, permissions=dict(allow=list(TOOLS)))


def flags(instructions):
    return ['--model', MODEL, '--tools', ','.join(TOOLS), '--allowed-tools', ','.join(TOOLS),
            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--setting-sources', '',
            '--disable-builtin-skills', '--settings', json.dumps(settings()),
            '--system-prompt', instructions, '--permission-mode', 'accept_edits',
            '--no-session-persistence', '--max-model-request-retries', '0', '--output-format', 'stream-json']


def prepare(out, instructions):
    """Public evidence only: does not discover CLI, inspect HOME or load login."""
    config = dict(cli_version_pin=VERSION, cli_model=MODEL, flags=flags(instructions), settings=settings())
    path = out / 'qoder-config.json'
    path.write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
    os.chmod(path, 0o600)
    return dict(model_evidence='requested_only', cli_model=MODEL, backend_model='unknown',
                isolation=dict(sandbox='none', network_access='host permission required',
                               shell_environment='allowlist; subscription HOME preserved',
                               hard_credential_file_isolation=False, native_agent_metadata_visible=True),
                warnings=['Tool and prompt restrictions are not filesystem/network isolation; Bash can access same-user files.'])


def run(cli, workspace, out, payload, instructions, limits, cap, evidence):
    cli = cli or shutil.which('qodercli')
    if not cli:
        local = Path.home() / '.local/bin/qodercli'
        if local.is_file() and os.access(local, os.X_OK):
            cli = str(local)
    if not cli:
        raise RunError('Qoder CLI unavailable')
    env = {key: os.environ[key] for key in ALLOWED_ENV if key in os.environ}
    version_log = out / 'qoder-version.txt'
    code = execute([cli, '--version'], out, version_log, 20, 15, 65536, env=env)
    evidence['version_preflight'] = dict(exit_code=code, sha256=hashlib.sha256(version_log.read_bytes()).hexdigest())
    text = version_log.read_text('utf-8').strip()
    if code or text != VERSION:
        raise RunError('Qoder implementation requires CLI ' + VERSION)
    evidence['cli_version'] = VERSION
    argv = [cli, '-p', payload] + flags(instructions)
    (out / 'qoder-argv.json').write_text(json.dumps(argv, indent=2) + '\n', encoding='utf-8')
    code = execute(argv, workspace, out / 'implementation.jsonl', *limits, cap, env=env)
    evidence['exit_code'] = code
    result = validate(out / 'implementation.jsonl', code, workspace, VERSION)
    result['warnings'] = evidence.get('warnings', []) + result['warnings']
    return result
