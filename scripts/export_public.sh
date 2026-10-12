#!/bin/bash
# 从本仓库导出公开树并做发布前检查。
#
#   bash scripts/export_public.sh <目标目录>              导出 HEAD（git archive）
#   bash scripts/export_public.sh --worktree <目标目录>   导出工作树里已跟踪与未忽略的文件（提交前自查）
#
# 排除范围只由 .gitattributes 的 export-ignore 决定，本脚本不另列路径。
# 目标目录必须不存在或为空。导出后依次跑：禁词检查、相对链接检查、Codex 适配离线测试、
# 安装器测试；任一失败非零退出。导出树留在原地供检查，本脚本不删除任何东西。
# 源仓库里带 export-ignore 的 hygiene_patterns*.txt（不公开的补充禁词表）会一并用于禁词检查。
set -eu

SRC=$(cd "$(dirname "$0")/.." && pwd -P)
PY=${AI_ANTI_PYTHON:-python3}
MODE=head
if [ "${1:-}" = "--worktree" ]; then MODE=worktree; shift; fi
[ $# -eq 1 ] || { echo "用法：export_public.sh [--worktree] <目标目录>" >&2; exit 2; }
DEST=$1

# 先按物理路径判断是否落在仓库内，再创建目录，拒绝时不留下任何东西。
ABS=$("$PY" -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$DEST")
case "$ABS/" in
  "$SRC"/*) echo "目标目录不能在仓库内：${ABS}" >&2; exit 2 ;;
esac
if [ -e "$DEST" ]; then
  [ -d "$DEST" ] || { echo "目标不是目录：${DEST}" >&2; exit 2; }
  [ -z "$(ls -A "$DEST")" ] || { echo "目标目录非空，拒绝导出：${DEST}" >&2; exit 2; }
else
  mkdir -p "$DEST"
fi
DEST=$(cd "$DEST" && pwd -P)

# 列出源仓库工作树中的候选文件（NUL 分隔），附带各自的 export-ignore 属性。
# 输出每行：<ignored 0/1>\t<相对路径>
list_candidates() {
  (cd "$SRC" && git ls-files -z -c -o --exclude-standard) | "$PY" -c '
import os, subprocess, sys
src = sys.argv[1]
names = []
for rel in sys.stdin.buffer.read().decode("utf-8").split("\0"):
    if rel and rel not in names and os.path.lexists(os.path.join(src, rel)):
        names.append(rel)
out = subprocess.run(["git", "-C", src, "check-attr", "-z", "--stdin", "export-ignore"],
                     input="\0".join(names).encode("utf-8"), stdout=subprocess.PIPE, check=True).stdout
parts = out.decode("utf-8").split("\0")
ignored = {parts[i] for i in range(0, len(parts) - 2, 3) if parts[i + 2] not in ("unspecified", "unset")}
for rel in names:
    print("%d\t%s" % (rel in ignored, rel))
' "$SRC"
}

echo "== 导出（${MODE}）→ ${DEST}"
if [ "$MODE" = head ]; then
  git -C "$SRC" archive --worktree-attributes --format=tar HEAD | tar -x -C "$DEST"
else
  list_candidates | "$PY" -c '
import os, shutil, sys
src, dest = sys.argv[1], sys.argv[2]
for line in sys.stdin.read().splitlines():
    ignored, rel = line.split("\t", 1)
    if ignored == "1":
        continue
    full, target = os.path.join(src, rel), os.path.join(dest, rel)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.islink(full):
        os.symlink(os.readlink(full), target)
    else:
        shutil.copy2(full, target)
' "$SRC" "$DEST"
fi

# 复核：导出树里不得出现任何带 export-ignore 的路径；同时收集不公开的补充禁词表。
set --
LEAKED=""
while IFS="$(printf '\t')" read -r ignored rel; do
  [ "$ignored" = 1 ] || continue
  if [ -e "$DEST/$rel" ] || [ -L "$DEST/$rel" ]; then LEAKED="$LEAKED ${rel}"; fi
  case "$(basename "$rel")" in
    hygiene_patterns*.txt) set -- "$@" --patterns "$SRC/$rel" ;;
  esac
done <<EOF
$(list_candidates)
EOF
[ -z "$LEAKED" ] || { echo "export-ignore 路径出现在导出树中：${LEAKED}" >&2; exit 1; }

FAILED=""
step() {
  local name=$1; shift
  echo "== ${name}"
  if "$@"; then echo "   通过"; else echo "   失败"; FAILED="$FAILED ${name}"; fi
}

step hygiene bash "$SRC/scripts/check_hygiene.sh" "$@" "$DEST"
step links bash "$DEST/scripts/check_links.sh" "$DEST"
step codex-tests sh -c 'cd "$1" && "$2" -m unittest discover -s adapters/codex/tests < /dev/null' _ "$DEST" "$PY"
step global-entry-tests sh -c 'cd "$1" && "$2" tests/test_global_entry.py < /dev/null' _ "$DEST" "$PY"
step install-health-tests sh -c 'cd "$1" && "$2" tests/test_install_health.py < /dev/null' _ "$DEST" "$PY"
step install-tests sh -c 'cd "$1" && bash tests/test_install.sh < /dev/null' _ "$DEST"

if [ -n "$FAILED" ]; then
  echo "export_public: 失败项：${FAILED}（导出树保留在 ${DEST}）"
  exit 1
fi
echo "export_public: 全部通过（导出树 ${DEST}）"
