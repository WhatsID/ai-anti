#!/bin/bash
# 公开导出树的卫生检查：扫描禁词，命中即非零退出并列出 文件:行。
#
#   bash scripts/check_hygiene.sh <目录>
#   bash scripts/check_hygiene.sh [--patterns 文件]... [--allowlist 文件]... <目录>
#   bash scripts/check_hygiene.sh --self-test
#
# 默认模式与放行表是本脚本旁的 hygiene_patterns.txt 与 hygiene_allowlist.txt；--patterns /
# --allowlist 追加更多（export_public.sh 用它传入不公开的补充禁词表）。
# 只扫描给定目录，源仓库本身可以保留不公开的记录，不适用零命中标准。
set -eu

HERE=$(cd "$(dirname "$0")" && pwd -P)
PY=${AI_ANTI_PYTHON:-python3}

# scan <模式文件列表(换行分隔)> <放行文件列表(换行分隔)> <目录>
scan() {
  "$PY" - "$1" "$2" "$3" <<'PY'
import os, re, sys

pattern_files = [p for p in sys.argv[1].split("\n") if p]
allow_files = [p for p in sys.argv[2].split("\n") if p]
root = os.path.realpath(sys.argv[3])

def data_lines(path):
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            if line.strip() and not line.lstrip().startswith("#"):
                yield line

patterns = []
for path in pattern_files:
    for line in data_lines(path):
        patterns.append((line, re.compile(line, re.IGNORECASE)))
allow = []
for path in allow_files:
    for line in data_lines(path):
        scope, sep, fixed = line.partition(":")
        if not sep or not fixed:
            sys.exit("放行表格式错误（应为 <路径或 *>:<固定字符串>）：%s: %s" % (path, line))
        allow.append((scope, fixed))

# 模式文件与放行表本身必然含禁词，按其在被扫目录中的相对路径跳过。
skip = {"scripts/hygiene_patterns.txt", "scripts/hygiene_allowlist.txt"}
hits = 0
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = sorted(d for d in dirnames if d != ".git")
    for name in sorted(filenames):
        full = os.path.join(dirpath, name)
        rel = os.path.relpath(full, root)
        if rel in skip or os.path.islink(full):
            continue
        with open(full, "rb") as handle:
            data = handle.read()
        if b"\0" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        # 路径本身也要扫：文件名里的私人字样同样会随导出公开。
        lines = [("(path)", rel)] + list(enumerate(text.split("\n"), 1))
        for lineno, line in lines:
            for scope, fixed in allow:
                if scope in ("*", rel):
                    line = line.replace(fixed, "")
            for source, regex in patterns:
                if regex.search(line):
                    hits += 1
                    print("%s:%s: [%s] %s" % (rel, lineno, source, line.strip()[:160]))
if hits:
    print("hygiene: %d 处命中" % hits)
    sys.exit(1)
print("hygiene: 零命中（%d 个模式，%d 条放行）" % (len(patterns), len(allow)))
PY
}

self_test() {
  local base
  base=$(mktemp -d "${TMPDIR:-/tmp}/ai-anti-hygiene-test.XXXXXX")
  local fails=0
  mkdir -p "$base/clean/docs" "$base/dirty/docs" "$base/allowed" "$base/named"
  printf 'token-alpha\n' > "$base/p.txt"
  printf 'docs/ok.md:token-alpha-ok\n*:beta-token-alpha\n' > "$base/a.txt"
  printf 'nothing here\n' > "$base/clean/docs/a.md"
  printf 'line one\nsee TOKEN-ALPHA here\n' > "$base/dirty/docs/a.md"
  mkdir -p "$base/allowed/docs"
  printf 'token-alpha-ok\nbeta-token-alpha\n' > "$base/allowed/docs/ok.md"
  printf 'token-alpha-ok\n' > "$base/allowed/other.md"
  printf 'x\n' > "$base/named/token-alpha.md"
  # 真实的公开模式表也要能拦下本机路径（运行时拼接，避免本文件自身含禁词）。
  mkdir -p "$base/real"
  printf 'path %s\n' "/Us""ers/someone/x" > "$base/real/a.md"

  run() { bash "$0" --patterns "$base/p.txt" --allowlist "$base/a.txt" --no-default "$1" > "$base/out.txt" 2>&1; }
  expect_ok() { if run "$1"; then :; else echo "  FAIL: 应通过：$1"; cat "$base/out.txt"; fails=$((fails + 1)); fi; }
  expect_hit() {
    if run "$1"; then echo "  FAIL: 应命中：$1"; fails=$((fails + 1));
    elif ! grep -Fq -- "$2" "$base/out.txt"; then echo "  FAIL: 输出缺 $2"; cat "$base/out.txt"; fails=$((fails + 1)); fi
  }
  expect_ok "$base/clean"
  expect_hit "$base/dirty" "docs/a.md:2:"
  # 放行只对指定文件生效；other.md 的同一字符串仍命中
  expect_hit "$base/allowed" "other.md:1:"
  if grep -Fq "docs/ok.md" "$base/out.txt"; then echo "  FAIL: 放行未生效"; fails=$((fails + 1)); fi
  expect_hit "$base/named" "token-alpha.md:(path):"
  if bash "$0" "$base/real" > "$base/out.txt" 2>&1; then
    echo "  FAIL: 默认模式表未拦下本机路径"; fails=$((fails + 1))
  fi
  if bash "$0" "$base/clean" > "$base/out.txt" 2>&1; then :; else
    echo "  FAIL: 默认模式表误报干净目录"; cat "$base/out.txt"; fails=$((fails + 1))
  fi
  if bash "$0" "$base/missing-dir" > /dev/null 2>&1; then
    echo "  FAIL: 不存在的目录应报错"; fails=$((fails + 1))
  fi
  rm -- "$base"/p.txt "$base"/a.txt "$base"/out.txt
  if [ "$fails" -eq 0 ]; then echo "self-test: ALL PASS（临时目录 ${base}）"; else echo "self-test: $fails 项失败"; exit 1; fi
}

PATTERNS=""
ALLOWS=""
DEFAULTS=1
TARGET=""
while [ $# -gt 0 ]; do
  case "$1" in
    --self-test) self_test; exit 0 ;;
    --patterns) [ $# -ge 2 ] || { echo "缺少 --patterns 参数" >&2; exit 2; }; PATTERNS="$PATTERNS$2
"; shift ;;
    --allowlist) [ $# -ge 2 ] || { echo "缺少 --allowlist 参数" >&2; exit 2; }; ALLOWS="$ALLOWS$2
"; shift ;;
    --no-default) DEFAULTS=0 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    -*) echo "未知参数：${1}" >&2; exit 2 ;;
    *) [ -z "$TARGET" ] || { echo "只接受一个目录" >&2; exit 2; }; TARGET=$1 ;;
  esac
  shift
done
[ -n "$TARGET" ] || { echo "用法：check_hygiene.sh <目录> | --self-test" >&2; exit 2; }
[ -d "$TARGET" ] || { echo "目录不存在：${TARGET}" >&2; exit 2; }

if [ "$DEFAULTS" -eq 1 ]; then
  PATTERNS="$HERE/hygiene_patterns.txt
$PATTERNS"
  ALLOWS="$HERE/hygiene_allowlist.txt
$ALLOWS"
fi
scan "$PATTERNS" "$ALLOWS" "$TARGET"
