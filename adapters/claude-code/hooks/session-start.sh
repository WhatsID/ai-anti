#!/bin/bash
# 接力协作模式播报（Claude Code SessionStart hook）。
#
# 模式文件位于**仓库根**的 .claude/relay-mode.local：用 git rev-parse --show-toplevel
# 定位仓库根（会话 cwd 可能在子目录，直接读 ./.claude/ 会漏读）；非 git 目录才回退当前
# 工作目录。文件读不到时按默认 required——语义见 CLAUDE.global.md「协作模式开关」节。
set -u

root=$(git rev-parse --show-toplevel 2>/dev/null) || root=""
[ -n "$root" ] || root=$PWD

mode=$(cat "$root/.claude/relay-mode.local" 2>/dev/null || echo required)

# 花括号必需：紧跟其后的全角破折号是多字节字符，写成 $mode 会被当成变量名的一部分。
echo "[接力协作模式] ${mode}——solo/required/auto/force 语义见 CLAUDE.global.md「协作模式开关」节"
