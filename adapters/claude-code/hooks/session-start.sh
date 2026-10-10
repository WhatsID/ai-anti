#!/bin/bash
# 仓库根定位规则保留；用户当次明确指令优先于此处播报的默认模式。
set -u
root=$(git rev-parse --show-toplevel 2>/dev/null) || root=""
[ -n "$root" ] || root=$PWD
"${AI_ANTI_PYTHON:-python3}" - "$root" <<'PY'
import json
from pathlib import Path
import sys
valid = ('solo', 'required', 'auto', 'force')
project = Path(sys.argv[1]) / '.claude/relay-mode.local'
config = Path.home() / '.config/ai-anti/config.json'
try:
    if project.exists() or project.is_symlink():
        mode, source = project.read_text().strip(), '项目模式文件'
    elif config.exists() or config.is_symlink():
        data = json.loads(config.read_text())
        if not isinstance(data, dict) or not isinstance(data.get('relay', {}), dict):
            raise ValueError()
        mode = data.get('relay', {}).get('default_mode', 'required')
        source = '全局默认' if 'default_mode' in data.get('relay', {}) else '内置默认'
    else:
        mode, source = 'required', '内置默认'
    if mode not in valid:
        raise ValueError()
except (OSError, UnicodeError, ValueError, TypeError):
    print('[接力协作模式] 配置无效或不可读，停止自动判断；请修复项目/全局配置。', file=sys.stderr)
    sys.exit(1)
print('[接力协作模式] %s（来源：%s）——solo/required/auto/force 语义见 CLAUDE.global.md「协作模式开关」节' % (mode, source))
PY
