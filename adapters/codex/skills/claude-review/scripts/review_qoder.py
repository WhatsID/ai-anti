"""Pinned Qoder subscription CLI, isolated material-only reviewer."""
import hashlib
import json
import time

from review_materials import load_registry, mcp_config, TOOL
from review_qoder_protocol import validate, SUPPORTED_VERSIONS
from review_runner import execute, RunError
from review_qoder_network import diagnose

SETTINGS = {'disableAllHooks': True, 'skills': {'enabled': False, 'disableShellExecution': True},
            'autoMemoryEnabled': False}
SYSTEM = ('You are a read-only code reviewer. Follow only the provided review instructions. '
          'Do not delegate or invoke workflows.')


def flags(config):
    return ['--tools', '', '--allowed-tools', TOOL, '--strict-mcp-config',
            '--mcp-config', json.dumps(config), '--setting-sources', '', '--disable-builtin-skills',
            '--settings', json.dumps(SETTINGS), '--system-prompt', SYSTEM,
            '--permission-mode', 'dont_ask', '--no-session-persistence',
            '--max-model-request-retries', '0', '--output-format', 'stream-json']


def capability(cli, out, env=None, material_config=None):
    path = out / 'version.txt'
    code = execute([cli, '--version'], out, path, 20, 15, 65536, env=env)
    data = path.read_bytes()
    version = data.decode('utf-8').strip()
    if code or version not in SUPPORTED_VERSIONS:
        raise RunError('unsupported Qoder CLI version: ' + version)
    # Several verified isolation flags are hidden from --help. Runtime init gates
    # are authoritative; unknown versions cannot inherit this capability claim.
    return {'cli_version': version, 'version': {'exit': code, 'sha256': hashlib.sha256(data).hexdigest()},
            'review_flags': flags(material_config), 'runtime_init_required': True}


def run_stage(cli, cwd, prompt, logfile, model, timeout, idle, cap, families=None,
              env=None, expected_model=None, required=None, registry_path=None):
    if registry_path is None:
        raise RunError('Qoder requires material registry')
    registry = load_registry(registry_path)
    prompt_file = logfile.with_suffix('.prompt.txt')
    prompt_file.write_text(prompt, encoding='utf-8')
    args = [cli, '-p', prompt_file.read_text(encoding='utf-8'), '--model', model] + flags(mcp_config(registry_path))
    started = time.time()
    deadline = time.monotonic() + timeout
    code = execute(args, cwd, logfile, timeout, idle, cap, env=env)
    try:
        result = validate(logfile, code, model, cwd, families, expected_model, required, registry)
    except RunError as exc:
        if exc.kind == 'ambiguous_model_error':
            exc.network_diagnostic = diagnose(logfile, cwd, deadline - time.monotonic())
        raise
    result.update(started_at=started, finished_at=time.time(),
                  log_sha256=hashlib.sha256(logfile.read_bytes()).hexdigest(),
                  log_bytes=logfile.stat().st_size)
    return result
