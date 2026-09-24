#!/bin/bash
# Markdown 相对链接检查：给定目录内每个 .md 文件的相对链接目标必须存在，且不得指出该目录。
#
#   bash scripts/check_links.sh <目录>
#
# 跳过外链（含协议头）、纯锚点与围栏代码块；链接里的 #锚点 只核对文件，不核对标题。
set -eu

[ $# -eq 1 ] && [ -d "$1" ] || { echo "用法：check_links.sh <目录>" >&2; exit 2; }

"${AI_ANTI_PYTHON:-python3}" - "$1" <<'PY'
import os, re, sys

root = os.path.realpath(sys.argv[1])
link = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
bad = checked = 0
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = sorted(d for d in dirnames if d != ".git")
    for name in sorted(filenames):
        if not name.endswith(".md"):
            continue
        path = os.path.join(dirpath, name)
        rel = os.path.relpath(path, root)
        fenced = False
        with open(path, encoding="utf-8", errors="replace") as handle:
            for lineno, line in enumerate(handle, 1):
                if line.lstrip().startswith(("```", "~~~")):
                    fenced = not fenced
                    continue
                if fenced:
                    continue
                for target in link.findall(line):
                    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("#"):
                        continue
                    target = target.split("#", 1)[0]
                    if not target:
                        continue
                    checked += 1
                    resolved = os.path.realpath(os.path.join(dirpath, target))
                    inside = resolved == root or resolved.startswith(root + os.sep)
                    if not inside or not os.path.exists(resolved):
                        bad += 1
                        why = "指出导出树" if not inside else "目标不存在"
                        print("%s:%d: %s：%s" % (rel, lineno, why, target))
if bad:
    print("links: %d 处失效（共检查 %d 条）" % (bad, checked))
    sys.exit(1)
print("links: 全部有效（共检查 %d 条相对链接）" % checked)
PY
