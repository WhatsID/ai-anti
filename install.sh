#!/bin/bash
# ai-anti 用户级安装器：把 Claude Code 适配层分发到 ~/.claude，可选 Codex 入口写入 ${CODEX_HOME:-~/.codex}/AGENTS.md 自有块，技能分发到 ~/.agents/skills。
#
# 设计要点（方案 20260813-collab-infra-extraction.md 关键实现点 2/5；20260924 开源整理 P1-6）：
#   - 逐文件托管 symlink（不做整目录链接），源始终是本仓库里的文件或目录；
#   - ~/.claude/CLAUDE.md 只维护自己的标记块区间，不碰块外任何内容；
#   - 每个成功安装的对象记入所有权 manifest（~/.claude/ai-anti.manifest.json）；
#   - fail-closed：目标已存在且不属于本工具（不是指向本仓库的符号链接）一律报错
#     停止，不覆盖不合并；
#   - 可选组件（hook、插件、Codex skill）的所有权随普通重装继承，只有 --uninstall 或
#     显式的 --without-hook / --remove-plugin / --without-codex 才取消并移除；
#   - SessionStart hook 按对象认领：只增删 command 等于本工具命令的那一个对象，
#     同条目里用户自己的对象与属性原样保留；
#   - --uninstall 只移除 manifest 中仍指向本仓库的对象与自己的标记块；
#   - 幂等：重复执行结果一致；全程不使用 rm -rf。
#
# 保持单文件：安装器要能在执行前一次读完，拆成多文件反而难以审阅。
# 兼容 macOS 自带 bash 3.2（不用关联数组，不在 set -u 下展开可能为空的数组）。

set -eu

REPO=$(cd "$(dirname "$0")" && pwd -P)
CLAUDE_DIR=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
AGENTS_SKILLS_DIR="$HOME/.agents/skills"
MANIFEST="$CLAUDE_DIR/ai-anti.manifest.json"
MARKER_BEGIN="<!-- ai-anti:begin -->"
MARKER_END="<!-- ai-anti:end -->"
GLOBAL_MD="$REPO/adapters/claude-code/CLAUDE.global.md"
HOOK_SH="$REPO/adapters/claude-code/hooks/session-start.sh"
COMPAT="$REPO/compat.json"
CODEX_HOME_DIR=${CODEX_HOME:-$HOME/.codex}
CODEX_GLOBAL_MD="$REPO/adapters/codex/AGENTS.global.md"
CODEX_ENTRY_META=""
CODEX_SKILLS="claude-review codex-relay"
PY=${AI_ANTI_PYTHON:-python3}
TAB=$(printf '\t')
NL='
'

die() { echo "ai-anti: 错误：$*" >&2; exit 1; }
info() { echo "ai-anti: $*"; }

usage() {
  cat <<'EOF'
ai-anti 用户级安装器

  ./install.sh                      安装/刷新；已安装的可选组件（hook、插件、Codex skill）原样保留
  ./install.sh --with-hook          同上，并把 SessionStart 播报 hook 注入用户级 settings.json
  ./install.sh --without-hook       移除本工具注入的 SessionStart hook
  ./install.sh --with-plugin NAME   安装评审插件 plugins/NAME（可重复）
  ./install.sh --remove-plugin NAME 移除评审插件 NAME 的托管链接（可重复）
  ./install.sh --with-codex         安装 Codex 用户入口及 skill（claude-review、codex-relay）
  ./install.sh --without-codex      移除 Codex 入口自有块及 skill 托管链接
  ./install.sh --uninstall          按 manifest 精确卸载
  ./install.sh --status             只报告当前安装状态与 CLI 版本，不做任何改动
  ./install.sh --help               本帮助

安装的对象：
  ~/.claude/commands/*.md   -> adapters/claude-code/commands/ 与已选插件 commands/ 的逐文件符号链接
  ~/.claude/agents/*.md     -> adapters/claude-code/agents/ 的逐文件符号链接
  ~/.claude/CLAUDE.md       -> 标记块内一行 @import 指向 CLAUDE.global.md
  ${CODEX_HOME:-~/.codex}/AGENTS.md -> 自有标记块，显式读取 AGENTS.global.md
  ~/.agents/skills/<skill>  -> adapters/codex/skills/<skill>（仅 --with-codex）
  ~/.claude/ai-anti.manifest.json  所有权清单（卸载依据）

用户配置 ~/.config/ai-anti/config.json 由用户自己维护，本脚本不创建也不改写。
注意：仓库移动位置后必须重跑本脚本——符号链接、@import 与 hook 都是绝对路径。
EOF
}

check_plugin_name() {
  case "$1" in
    ''|-*|*[!a-z0-9-]*) die "插件名不合法：${1}（只允许小写字母、数字与连字符）" ;;
  esac
}

# in_list <项> <换行分隔列表>
in_list() {
  printf '%s\n' "$2" | grep -Fxq -- "$1"
}

MODE=install
WITH_HOOK=""       # 空＝继承 manifest；1＝安装；0＝移除
WITH_CODEX=""
ADD_PLUGINS=""
DEL_PLUGINS=""
INSTALL_OPTS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall) MODE=uninstall ;;
    --status) MODE=status ;;
    --with-hook)
      [ "$WITH_HOOK" != 0 ] || die "--with-hook 与 --without-hook 不能同用"
      WITH_HOOK=1; INSTALL_OPTS=1 ;;
    --without-hook)
      [ "$WITH_HOOK" != 1 ] || die "--with-hook 与 --without-hook 不能同用"
      WITH_HOOK=0; INSTALL_OPTS=1 ;;
    --with-codex)
      [ "$WITH_CODEX" != 0 ] || die "--with-codex 与 --without-codex 不能同用"
      WITH_CODEX=1; INSTALL_OPTS=1 ;;
    --without-codex)
      [ "$WITH_CODEX" != 1 ] || die "--with-codex 与 --without-codex 不能同用"
      WITH_CODEX=0; INSTALL_OPTS=1 ;;
    --with-plugin|--remove-plugin)
      [ $# -ge 2 ] || die "$1 需要插件名"
      check_plugin_name "$2"
      if [ "$1" = --with-plugin ]; then
        ADD_PLUGINS="$ADD_PLUGINS$2$NL"
      else
        DEL_PLUGINS="$DEL_PLUGINS$2$NL"
      fi
      INSTALL_OPTS=1
      shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数 ${1}（用 --help 查看用法）" ;;
  esac
  shift
done

if [ "$MODE" != install ] && [ "$INSTALL_OPTS" = 1 ]; then
  die "安装选项不能与 --uninstall / --status 同用"
fi
while IFS= read -r name; do
  [ -n "$name" ] || continue
  if in_list "$name" "$DEL_PLUGINS"; then
    die "插件 ${name} 同时出现在 --with-plugin 与 --remove-plugin 中"
  fi
done <<EOF
$ADD_PLUGINS
EOF

command -v "$PY" >/dev/null 2>&1 || die "找不到 ${PY}（manifest 读写需要 python3）"
[ -f "$GLOBAL_MD" ] || die "缺少 ${GLOBAL_MD}——仓库不完整？"

WORK=$(mktemp -d "${TMPDIR:-/tmp}/ai-anti-install.XXXXXX")
LINKS="$WORK/links"
NEWDIRS="$WORK/newdirs"
: > "$LINKS"
: > "$NEWDIRS"
# 必须自己记住并回传退出码：macOS bash 3.2 的 EXIT trap 会把 set -e/-u 触发的失败状态
# 冲成 0（trap 里最后一条命令的状态），实测会让安装失败被报成成功。
cleanup() {
  status=$?
  for leftover in links newdirs newdests oldlinks tolink dirs.raw dirs; do
    rm -f "$WORK/$leftover" 2>/dev/null || true
  done
  rmdir "$WORK" 2>/dev/null || true
  exit "$status"
}
trap cleanup EXIT

# ---------------------------------------------------------------- 公共小工具

# 把 $HOME 下的绝对路径折成 ~/ 形式（用于 @import 行）。
tilde_path() {
  case "$1" in
    "$HOME"/*) printf '~/%s\n' "${1#"$HOME"/}" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

# 符号链接解析后的最终目标；不是链接则返回非零。
link_target() {
  "$PY" - "$1" <<'PY'
import os, sys
path = sys.argv[1]
try:
    raw = os.readlink(path)
except OSError:
    sys.exit(1)
print(os.path.realpath(os.path.join(os.path.dirname(path), raw)))
PY
}

inside_dir() {
  # inside_dir <路径> <目录>
  case "$1" in
    "$2"/*) return 0 ;;
    *) return 1 ;;
  esac
}

manifest_links() {
  [ -f "$MANIFEST" ] || return 0
  "$PY" - "$MANIFEST" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(0)
for entry in data.get("links", []):
    path = entry.get("path")
    if path:
        print(path)
PY
}

# manifest_list <键>：逐行打印 manifest 顶层字符串数组（created_dirs、plugins）。
manifest_list() {
  [ -f "$MANIFEST" ] || return 0
  "$PY" - "$MANIFEST" "$1" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(0)
for item in data.get(sys.argv[2]) or []:
    if isinstance(item, str) and item:
        print(item)
PY
}

manifest_field() {
  [ -f "$MANIFEST" ] || return 0
  "$PY" - "$MANIFEST" "$1" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(0)
node = data
for key in sys.argv[2].split("."):
    if not isinstance(node, dict):
        sys.exit(0)
    node = node.get(key)
if isinstance(node, bool):
    print("1" if node else "0")
elif node is not None:
    print(node)
PY
}

# 标记块必须成对且至多一处，否则停止（不猜用户的意图）。
check_marker_sanity() {
  file=$1
  [ -f "$file" ] || return 0
  begins=$(grep -Fc "$MARKER_BEGIN" "$file" || true)
  ends=$(grep -Fc "$MARKER_END" "$file" || true)
  [ "$begins" = "$ends" ] ||
    die "${file} 的 ai-anti 标记块不成对（begin=${begins} end=${ends}）——已停止，请人工修复"
  [ "$begins" -le 1 ] ||
    die "${file} 出现 ${begins} 个 ai-anti 标记块——已停止，请人工修复"
  if [ "$begins" = 1 ]; then
    begin_line=$(grep -Fn "$MARKER_BEGIN" "$file" | cut -d: -f1)
    end_line=$(grep -Fn "$MARKER_END" "$file" | cut -d: -f1)
    [ "$begin_line" -lt "$end_line" ] ||
      die "${file} 的 ai-anti 标记块顺序颠倒（begin 在第 ${begin_line} 行，end 在第 ${end_line} 行）——已停止，请人工修复"
  fi
}

# 逐级创建缺失目录并逐级记下（卸载时才能把它们作为空目录收回）。
ensure_dir() {
  local dir=$1
  if [ ! -d "$dir" ]; then
    { [ ! -e "$dir" ] && [ ! -L "$dir" ]; } || die "${dir} 已存在且不是目录——已停止"
    ensure_dir "$(dirname "$dir")"
    mkdir "$dir"
    printf '%s\n' "$dir" >> "$NEWDIRS"
  fi
}

# release_link <路径> <仓库>：仍指向该仓库的托管链接才删除；返回 1 表示跳过。
release_link() {
  dest=$1
  owner_repo=$2
  if [ ! -L "$dest" ]; then
    echo "ai-anti: 跳过 ${dest}（已不是符号链接，可能被替换过）" >&2
    return 1
  fi
  current=$(link_target "$dest" || true)
  if [ -n "$current" ] && inside_dir "$current" "$owner_repo"; then
    rm -- "$dest"
    return 0
  fi
  echo "ai-anti: 跳过 ${dest}（指向 ${current}，已不属于本仓库）" >&2
  return 1
}

# ------------------------------------------------------------ SessionStart hook

# hook_tool <inject|remove|check> [settings_created]：按对象认领本工具的 hook。
# 本工具的对象＝command 等于脚本路径的 shell 引用形态、旧版未加引号的原始路径，
# 或 manifest 记下的命令。输出一个词：created/updated/unchanged/removed/removed-file/
# present/absent/invalid。
hook_tool() {
  HOOK_CMD="$HOOK_SH" HOOK_EXTRA="$(manifest_field hook.command)" SETTINGS_CREATED="${2:-0}" \
  "$PY" - "$1" "$CLAUDE_DIR/settings.json" <<'PY'
import copy, json, os, shlex, sys

action, path = sys.argv[1], sys.argv[2]
raw = os.environ["HOOK_CMD"]
quoted = shlex.quote(raw)
ours = {raw, quoted}
if os.environ.get("HOOK_EXTRA"):
    ours.add(os.environ["HOOK_EXTRA"])


def bad(message):
    if action != "check":
        sys.exit("ai-anti: 错误：%s %s——已停止，未做改动" % (path, message))
    print("invalid")
    sys.exit(0)


exists = os.path.exists(path)
data = {}
if exists:
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    if text.strip():
        try:
            data = json.loads(text)
        except ValueError:
            bad("不是合法 JSON")
    if not isinstance(data, dict):
        bad("顶层不是对象")
original = copy.deepcopy(data)
hooks = data.get("hooks", {})
if not isinstance(hooks, dict):
    bad("的 hooks 字段不是对象")
entries = hooks.get("SessionStart", [])
if not isinstance(entries, list):
    bad("的 hooks.SessionStart 不是数组")

found = False
kept = []
for entry in entries:
    items = entry.get("hooks") if isinstance(entry, dict) else None
    if not isinstance(items, list):
        kept.append(entry)
        continue
    new_items = []
    touched = False
    for item in items:
        if isinstance(item, dict) and item.get("command") in ours:
            touched = True
            if action == "inject" and not found:
                new_items.append(dict(item, type="command", command=quoted))
            found = True
            continue
        new_items.append(item)
    if touched and not new_items:
        continue                       # 条目里只剩本工具对象：整条移除
    kept.append(dict(entry, hooks=new_items) if touched else entry)

if action == "check":
    print("present" if found else "absent")
    sys.exit(0)
if action == "remove" and not found:
    print("absent")
    sys.exit(0)
if action == "inject" and not found:
    kept.append({"hooks": [{"type": "command", "command": quoted}]})

if kept:
    hooks["SessionStart"] = kept
else:
    hooks.pop("SessionStart", None)
if hooks:
    data["hooks"] = hooks
else:
    data.pop("hooks", None)

if action == "remove" and not data and exists and os.environ.get("SETTINGS_CREATED") == "1":
    os.remove(path)
    print("removed-file")
    sys.exit(0)
if exists and data == original:
    print("unchanged")
    sys.exit(0)
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
print("removed" if action == "remove" else ("updated" if exists else "created"))
PY
}

# 预检：settings.json 必须能被 hook_tool 处理，否则在第一次落盘之前停止。
assert_settings_ok() {
  state=$(hook_tool check) || state=invalid
  [ "$state" != invalid ] ||
    die "${CLAUDE_DIR}/settings.json 不是合法 JSON 或结构不对——已停止，未做任何改动；修复该文件后重跑"
}

# ------------------------------------------------------------------ 安装逻辑

# 插件目录必须有合法 plugin.json，name 与目录名一致，kind 取值合法（契约见 docs/PLUGINS.md）。
check_plugin() {
  "$PY" - "$REPO/plugins/$1" "$1" <<'PY'
import json, os, sys
root, name = sys.argv[1], sys.argv[2]
try:
    data = json.load(open(os.path.join(root, "plugin.json"), encoding="utf-8"))
except (OSError, ValueError) as exc:
    sys.exit("ai-anti: 错误：插件 %s 的 plugin.json 无法读取或不是合法 JSON：%s" % (name, exc))
if not isinstance(data, dict) or data.get("name") != name:
    sys.exit("ai-anti: 错误：插件 %s 的 plugin.json 缺少 name 或与目录名不一致" % name)
if data.get("kind") not in ("cross-model", "same-family"):
    sys.exit("ai-anti: 错误：插件 %s 的 kind 必须是 cross-model 或 same-family" % name)
PY
}

# 生效的插件集合＝manifest 已有 ∪ --with-plugin − --remove-plugin（保序去重）。
resolve_plugins() {
  EFF_PLUGINS=""
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    check_plugin_name "$name"
    in_list "$name" "$DEL_PLUGINS" && continue
    in_list "$name" "$EFF_PLUGINS" && continue
    if [ ! -f "$REPO/plugins/$name/plugin.json" ]; then
      in_list "$name" "$ADD_PLUGINS" && die "找不到插件 ${name}（缺 plugins/${name}/plugin.json）"
      echo "ai-anti: 警告：manifest 记录的插件 ${name} 已不在仓库中，收回其托管链接" >&2
      continue
    fi
    check_plugin "$name"
    EFF_PLUGINS="$EFF_PLUGINS$name$NL"
  done <<EOF
$(manifest_list plugins)
$ADD_PLUGINS
EOF
}

plan_links() {
  for kind in commands agents; do
    srcdir="$REPO/adapters/claude-code/$kind"
    [ -d "$srcdir" ] || die "缺少 ${srcdir}"
    for src in "$srcdir"/*.md; do
      [ -e "$src" ] || continue
      printf '%s\t%s\t%s\n' "$CLAUDE_DIR/$kind/$(basename "$src")" "$src" core >> "$LINKS"
    done
  done
  [ -s "$LINKS" ] || die "适配层里没有可安装的 commands/agents 文件"
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    found=0
    for src in "$REPO/plugins/$name/commands"/*.md; do
      [ -e "$src" ] || continue
      printf '%s\t%s\t%s\n' "$CLAUDE_DIR/commands/$(basename "$src")" "$src" "plugin:$name" >> "$LINKS"
      found=1
    done
    [ "$found" = 1 ] || die "插件 ${name} 没有 commands/*.md"
  done <<EOF
$EFF_PLUGINS
EOF
  if [ "$EFF_CODEX" = 1 ]; then
    for skill in $CODEX_SKILLS; do
      src="$REPO/adapters/codex/skills/$skill"
      [ -f "$src/SKILL.md" ] || die "缺少 ${src}/SKILL.md"
      printf '%s\t%s\t%s\n' "$AGENTS_SKILLS_DIR/$skill" "$src" codex >> "$LINKS"
    done
  fi
  dup=$(cut -f1 "$LINKS" | sort | uniq -d)
  [ -z "$dup" ] || die "多个组件要安装同名对象：${dup}"
}

# 目标可否由本工具接管；不可接管即报错停止（此时尚未落任何盘）。
assert_takeable() {
  dest=$1
  want=$2
  if [ -L "$dest" ]; then
    current=$(link_target "$dest" || true)
    if [ "$current" = "$want" ]; then
      return 0                                      # 幂等：已是同一目标
    fi
    if [ -n "$current" ] && inside_dir "$current" "$REPO"; then
      return 0                                      # 指向本仓库别处：本工具旧链接，可刷新
    fi
    # 仓库移动后重装：manifest 登记过、且指向 manifest 记下的旧仓库位置，才算本工具旧链接。
    # repo 为空时 inside_dir 恒真，必须排除。
    if [ -n "$current" ] && [ -n "$MANIFEST_REPO" ] && inside_dir "$current" "$MANIFEST_REPO" &&
       grep -Fxq -- "$dest" "$WORK/oldlinks"; then
      return 0
    fi
    die "${dest} 已是指向仓库外的符号链接（${current}），不属于 ai-anti——已停止，未做任何改动"
  fi
  if [ -e "$dest" ]; then
    die "${dest} 已存在且不是本工具安装的符号链接——已停止，未覆盖也未合并；请先自行处理该文件"
  fi
  return 0
}

# Codex entry uses bytes: user text, including missing final newline, survives removal.
# Check and prepare metadata before any install/uninstall mutation.
codex_entry() {
  CODEX_HOME_DIR="$CODEX_HOME_DIR" CODEX_SOURCE="$CODEX_GLOBAL_MD" \
  "$PY" - "$1" "$MANIFEST" "${2:-}" <<'PY'
import json, os, pathlib, stat, sys
mode, manifest_path, prepared = sys.argv[1:]
try:
    old = json.loads(pathlib.Path(manifest_path).read_text()) if os.path.isfile(manifest_path) else {}
    meta = old.get("codex_entry") or {}
    home = pathlib.Path(os.path.abspath(os.environ["CODEX_HOME_DIR"]))
    target = pathlib.Path(meta["path"]) if mode in ("remove", "check-remove") and meta else home / "AGENTS.md"
    if mode in ("remove", "check-remove") and not meta:
        sys.exit(0)  # v1/v2 manifests never owned a Codex instruction file.
    def regular(path, directory=False):
        if os.path.lexists(path):
            st = path.lstat()
            if not (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode)):
                raise ValueError("unsafe object: %s" % path)
    # Do not follow a symlink at any ancestor, even a dangling one.
    for parent in [target.parent, *target.parent.parents]:
        regular(parent, True)
    regular(target)
    if mode not in ("remove", "check-remove"):
        override = home / "AGENTS.override.md"
        regular(override)
        if override.exists() and override.stat().st_size:
            raise ValueError("AGENTS.override.md shadows the entry; resolve it manually")
        if meta and meta.get("path") != str(target):
            raise ValueError("CODEX_HOME changed; run --without-codex using the recorded installation first")
    source = pathlib.Path(os.environ["CODEX_SOURCE"])
    if mode not in ("remove", "check-remove"):
        for required in (source, source.parents[2] / "core/WORKFLOW_ENTRY.md"):
            if not required.is_file():
                raise ValueError("unreadable Codex source: %s" % required)
            required.read_bytes()
    begin, end = b"<!-- ai-anti:codex:begin -->", b"<!-- ai-anti:codex:end -->"
    original = target.read_bytes() if target.exists() else b""
    if original.count(begin) != original.count(end) or original.count(begin) > 1:
        raise ValueError("damaged or duplicate Codex markers")
    start = original.find(begin); finish = original.find(end)
    if start >= 0 and finish < start:
        raise ValueError("reversed Codex markers")
    block = (begin + b"\n" + ("Read the ai-anti user-level workflow entry explicitly from this JSON-quoted absolute path: "
             + json.dumps(str(source), ensure_ascii=False) + "\nThis instruction does not expand user authorization.\n").encode() + end)
    if start >= 0:
        if not meta:
            raise ValueError("unowned Codex marker block; resolve manually")
        finish += len(end)
    if mode == "status":
        if not meta or start < 0 or original[start:finish] != block:
            raise ValueError("Codex entry missing or differs from current source routing")
        print("Codex entry: OK (%s)" % target)
    elif mode == "prepare":
        if start < 0:
            separator = b"\n\n" if original else b""
            suffix = b"\n"
        else:
            separator = bytes.fromhex(meta.get("separator_hex", ""))
            suffix = bytes.fromhex(meta.get("suffix_hex", ""))
        print(json.dumps({"path": str(target), "source": str(source),
              "created_by_install": meta.get("created_by_install", not target.exists()),
              "separator_hex": separator.hex(), "suffix_hex": suffix.hex()}, ensure_ascii=False))
    elif mode == "write":
        new = json.loads(prepared)
        if start >= 0:
            updated = original[:start] + block + original[finish:]
        else:
            updated = original + bytes.fromhex(new["separator_hex"]) + block + bytes.fromhex(new["suffix_hex"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if updated != original:
            target.write_bytes(updated)
    elif mode == "remove" and start >= 0:
        separator = bytes.fromhex(meta.get("separator_hex", ""))
        suffix = bytes.fromhex(meta.get("suffix_hex", ""))
        # Remove only the installer-owned adjacent bytes if they still match.
        left = start - len(separator) if separator and original[:start].endswith(separator) else start
        right = finish + len(suffix) if suffix and original[finish:].startswith(suffix) else finish
        updated = original[:left] + original[right:]
        if not updated and meta.get("created_by_install"):
            target.unlink()
        else:
            target.write_bytes(updated)
except (OSError, ValueError, KeyError, TypeError) as exc:
    print("ai-anti: Codex entry: %s" % exc, file=sys.stderr)
    sys.exit(1)
PY
}

do_install() {
  prev_hook=$(manifest_field hook.injected)
  prev_codex=$(manifest_field codex_skills.installed)
  EFF_HOOK=${WITH_HOOK:-${prev_hook:-0}}
  EFF_CODEX=${WITH_CODEX:-${prev_codex:-0}}
  resolve_plugins
  plan_links
  MANIFEST_REPO=$(manifest_field repo)
  manifest_links > "$WORK/oldlinks"

  # 第一遍：全量预检，任何一处不可接管就整体停止，绝不落一半
  while IFS="$TAB" read -r dest want comp; do
    [ -n "$dest" ] || continue
    assert_takeable "$dest" "$want"
  done < "$LINKS"

  # 继承上一次安装记下的所有权事实——重复安装不能把它们冲掉，否则卸载会漏删
  # 本工具自己创建的 CLAUDE.md、settings.json 与目录。
  manifest_list created_dirs >> "$NEWDIRS"
  old_repo=$MANIFEST_REPO
  [ -n "$old_repo" ] || old_repo=$REPO

  claude_md="$CLAUDE_DIR/CLAUDE.md"
  created_claude_md=0
  [ -f "$claude_md" ] || created_claude_md=1
  if [ "$(manifest_field claude_md.created_by_install)" = "1" ]; then
    created_claude_md=1
  fi
  settings_created=$(manifest_field hook.settings_created_by_install)
  settings_created=${settings_created:-0}
  check_marker_sanity "$claude_md"
  if [ "$EFF_HOOK" = "1" ]; then
    [ -x "$HOOK_SH" ] || die "${HOOK_SH} 不存在或不可执行"
  fi
  if [ "$EFF_HOOK" = "1" ] || [ "$prev_hook" = "1" ]; then
    assert_settings_ok                              # 本次要注入或移除 hook
  fi

  if [ "$EFF_CODEX" = 1 ]; then
    CODEX_ENTRY_META=$(codex_entry prepare) || die "Codex 入口预检失败，未做任何改动"
  elif [ -n "$(manifest_field codex_entry.path)" ]; then
    codex_entry check-remove || die "Codex 入口移除预检失败，未做任何改动"
  fi

  # 第二遍：落盘。先收回本次不再托管的旧链接（--remove-plugin、--without-codex、
  # 或仓库里已删除/搬走的文件），再建新链接。
  ensure_dir "$CLAUDE_DIR"
  cut -f1 "$LINKS" > "$WORK/newdests"
  released=0
  while IFS= read -r dest; do
    [ -n "$dest" ] || continue
    grep -Fxq -- "$dest" "$WORK/newdests" && continue
    [ -e "$dest" ] || [ -L "$dest" ] || continue
    if release_link "$dest" "$old_repo"; then
      released=$((released + 1))
    fi
  done < "$WORK/oldlinks"
  [ "$released" = 0 ] || info "已收回 $released 个不再托管的旧链接"

  count=0
  while IFS="$TAB" read -r dest want comp; do
    [ -n "$dest" ] || continue
    ensure_dir "$(dirname "$dest")"
    if [ -L "$dest" ]; then
      if [ "$(link_target "$dest" || true)" = "$want" ]; then
        count=$((count + 1))
        continue
      fi
      rm -- "$dest"
    fi
    ln -s "$want" "$dest"
    count=$((count + 1))
  done < "$LINKS"
  info "已托管 $count 个符号链接（commands/agents、插件、Codex skill）"

  write_marker_block "$claude_md"
  info "已刷新 $claude_md 的 ai-anti 标记块（@import 指向 CLAUDE.global.md）"

  if [ "$EFF_HOOK" = "1" ]; then
    result=$(hook_tool inject)
    [ "$result" != created ] || settings_created=1
    info "SessionStart hook 已就位（${result}）：$CLAUDE_DIR/settings.json"
  elif [ "$prev_hook" = "1" ]; then
    result=$(hook_tool remove "$settings_created")
    [ "$result" != removed-file ] || settings_created=0
    info "已移除 SessionStart hook（${result}）"
  else
    info "未注入 SessionStart hook（默认关闭；需要时加 --with-hook）"
  fi

  if [ "$EFF_CODEX" = 1 ]; then
    ensure_dir "$CODEX_HOME_DIR"
    codex_entry write "$CODEX_ENTRY_META"
  else
    codex_entry remove
  fi

  write_manifest "$created_claude_md" "$EFF_HOOK" "$settings_created"
  info "manifest 已写入 $MANIFEST"
}

write_marker_block() {
  file=$1
  import_line="@$(tilde_path "$GLOBAL_MD")"
  MARKER_BEGIN="$MARKER_BEGIN" MARKER_END="$MARKER_END" \
  IMPORT_LINE="$import_line" REPO_PATH="$REPO" \
  "$PY" - "$file" <<'PY'
import os, sys

path = sys.argv[1]
begin = os.environ["MARKER_BEGIN"]
end = os.environ["MARKER_END"]
block = "\n".join([
    begin,
    "<!-- 由 %s/install.sh 维护；块外内容不受影响，手改本块会在下次安装时被覆盖 -->"
    % os.environ["REPO_PATH"],
    os.environ["IMPORT_LINE"],
    end,
])

original = ""
if os.path.exists(path):
    with open(path, encoding="utf-8") as handle:
        original = handle.read()

if begin in original and end in original:
    head, rest = original.split(begin, 1)
    _, tail = rest.split(end, 1)
    updated = head + block + tail
elif original.strip():
    updated = original.rstrip("\n") + "\n\n" + block + "\n"
else:
    updated = block + "\n"

if updated != original:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(updated)
PY
}

write_manifest() {
  REPO_PATH="$REPO" CLAUDE_DIR="$CLAUDE_DIR" MANIFEST_PATH="$MANIFEST" \
  LINKS_FILE="$LINKS" NEWDIRS_FILE="$NEWDIRS" \
  CREATED_CLAUDE_MD="$1" HOOK_INJECTED="$2" SETTINGS_CREATED="$3" \
  CODEX_ENTRY_META="$CODEX_ENTRY_META" PLUGINS="$EFF_PLUGINS" CODEX="$EFF_CODEX" AGENTS_SKILLS_DIR="$AGENTS_SKILLS_DIR" HOOK_SH="$HOOK_SH" \
  "$PY" - <<'PY'
import json, os, shlex, time

links = []
with open(os.environ["LINKS_FILE"], encoding="utf-8") as handle:
    for line in handle:
        line = line.rstrip("\n")
        if not line:
            continue
        path, target, component = line.split("\t", 2)
        links.append({"path": path, "target": target, "component": component})

created_dirs = []
with open(os.environ["NEWDIRS_FILE"], encoding="utf-8") as handle:
    for line in handle:
        line = line.rstrip("\n")
        if line and line not in created_dirs:
            created_dirs.append(line)

claude_dir = os.environ["CLAUDE_DIR"]
hook_injected = os.environ["HOOK_INJECTED"] == "1"
manifest = {
    "schema_version": 3,
    "repo": os.environ["REPO_PATH"],
    "claude_dir": claude_dir,
    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "links": links,
    "created_dirs": created_dirs,
    "claude_md": {
        "path": os.path.join(claude_dir, "CLAUDE.md"),
        "created_by_install": os.environ["CREATED_CLAUDE_MD"] == "1",
    },
    "hook": {
        "settings_path": os.path.join(claude_dir, "settings.json"),
        "injected": hook_injected,
        "command": shlex.quote(os.environ["HOOK_SH"]) if hook_injected else None,
        "settings_created_by_install": os.environ["SETTINGS_CREATED"] == "1",
    },
    "plugins": [name for name in os.environ["PLUGINS"].split("\n") if name],
    "codex_entry": json.loads(os.environ["CODEX_ENTRY_META"]) if os.environ["CODEX_ENTRY_META"] else None,
    "codex_skills": {
        "installed": os.environ["CODEX"] == "1",
        "dir": os.environ["AGENTS_SKILLS_DIR"],
    },
}
with open(os.environ["MANIFEST_PATH"], "w", encoding="utf-8") as handle:
    json.dump(manifest, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY
}

# ------------------------------------------------------------------ 卸载逻辑

do_uninstall() {
  if [ ! -f "$MANIFEST" ]; then
    info "没有 manifest（${MANIFEST}），无可卸载对象"
    return 0
  fi

  manifest_repo=$(manifest_field repo)
  [ -n "$manifest_repo" ] || die "manifest 缺 repo 字段——已停止，未删除任何对象"
  # 预检：删除任何对象之前确认 settings.json 与 CLAUDE.md 标记块都能处理
  if [ "$(manifest_field hook.injected)" = "1" ]; then
    assert_settings_ok
  fi
  check_marker_sanity "$CLAUDE_DIR/CLAUDE.md"
  codex_entry check-remove || die "Codex 入口移除预检失败，未删除任何对象"

  removed=0
  skipped=0
  manifest_links > "$WORK/tolink"
  while IFS= read -r dest; do
    [ -n "$dest" ] || continue
    [ -e "$dest" ] || [ -L "$dest" ] || continue
    if release_link "$dest" "$manifest_repo"; then
      removed=$((removed + 1))
    else
      skipped=$((skipped + 1))
    fi
  done < "$WORK/tolink"
  info "已移除 $removed 个托管链接，跳过 $skipped 个"

  codex_entry remove
  remove_marker_block "$CLAUDE_DIR/CLAUDE.md"

  if [ "$(manifest_field hook.injected)" = "1" ]; then
    result=$(hook_tool remove "$(manifest_field hook.settings_created_by_install)")
    info "SessionStart hook：${result}"
  fi

  # 目录清单要在删 manifest 之前取出；只 rmdir 空目录、由深到浅，非空一律保留（不使用 rm -rf）
  { printf '%s\n' "$CLAUDE_DIR/commands" "$CLAUDE_DIR/agents"; manifest_list created_dirs; } > "$WORK/dirs.raw"
  "$PY" - "$WORK/dirs.raw" > "$WORK/dirs" <<'PY'
import sys
seen = []
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.rstrip("\n")
    if line and line not in seen:
        seen.append(line)
for item in sorted(seen, key=lambda p: p.count("/"), reverse=True):
    print(item)
PY
  rm -- "$MANIFEST"
  info "已移除 manifest"
  while IFS= read -r dir; do
    [ -d "$dir" ] || continue
    if rmdir "$dir" 2>/dev/null; then
      info "已移除空目录 $dir"
    fi
  done < "$WORK/dirs"
}

remove_marker_block() {
  file=$1
  [ -f "$file" ] || return 0
  check_marker_sanity "$file"
  created=$(manifest_field claude_md.created_by_install)
  MARKER_BEGIN="$MARKER_BEGIN" MARKER_END="$MARKER_END" CREATED="$created" \
  "$PY" - "$file" <<'PY'
import os, sys

path = sys.argv[1]
begin = os.environ["MARKER_BEGIN"]
end = os.environ["MARKER_END"]

with open(path, encoding="utf-8") as handle:
    original = handle.read()

if begin not in original or end not in original:
    sys.exit(0)

head, rest = original.split(begin, 1)
_, tail = rest.split(end, 1)
updated = (head.rstrip("\n") + "\n" + tail.lstrip("\n")).strip("\n")

if not updated:
    if os.environ.get("CREATED") == "1":
        os.remove(path)
        print("ai-anti: 已移除安装时创建的空 CLAUDE.md")
    else:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("")
        print("ai-anti: 已清空 CLAUDE.md 中的 ai-anti 标记块（文件保留）")
    sys.exit(0)

with open(path, "w", encoding="utf-8") as handle:
    handle.write(updated + "\n")
print("ai-anti: 已移除 CLAUDE.md 中的 ai-anti 标记块（块外内容保留）")
PY
}

# ------------------------------------------------------------------ 状态报告

# 实测版本清单见 compat.json 与 docs/COMPATIBILITY.md；不在清单内只提示，不阻断。
report_versions() {
  claude_v=""
  codex_v=""
  if command -v claude >/dev/null 2>&1; then
    claude_v=$(claude --version < /dev/null 2>&1 || true)
  fi
  if command -v codex >/dev/null 2>&1; then
    codex_v=$(codex --version < /dev/null 2>&1 || true)
  fi
  CLAUDE_V="$claude_v" CODEX_V="$codex_v" "$PY" - "$COMPAT" <<'PY'
import json, os, re, sys

try:
    compat = json.load(open(sys.argv[1], encoding="utf-8"))
    tested = compat["tested"]
    minimum = tuple(int(x) for x in compat["python_min"].split("."))
except (OSError, ValueError, KeyError, TypeError, AttributeError):
    print("  警告 版本清单 %s 无法读取" % sys.argv[1])
    sys.exit(1)
problems = 0
for label, env in (("claude", "CLAUDE_V"), ("codex", "CODEX_V")):
    text = os.environ[env].strip()
    match = re.search(r"[0-9]+\.[0-9]+\.[0-9]+", text)
    if not text:
        print("  警告 %-7s 未安装" % label)
        problems += 1
    elif not match:
        print("  警告 %-7s 版本无法识别：%s" % (label, text))
        problems += 1
    elif match.group(0) in tested.get(label, []):
        print("  OK   %-7s %s（在实测清单内）" % (label, match.group(0)))
    else:
        print("  警告 %-7s %s（不在实测清单 %s 内；可用但未经验证）"
              % (label, match.group(0), ", ".join(tested.get(label, []))))
        problems += 1
current = sys.version_info[:3]
text = ".".join(str(x) for x in current)
if current[:len(minimum)] >= minimum:
    print("  OK   python  %s（≥ %s）" % (text, compat["python_min"]))
else:
    print("  警告 python  %s（低于 %s）" % (text, compat["python_min"]))
    problems += 1
sys.exit(1 if problems else 0)
PY
}

do_status() {
  problems=0
  echo "仓库：$REPO"
  echo "用户级目录：$CLAUDE_DIR"
  if [ -f "$MANIFEST" ]; then
    echo "manifest：${MANIFEST}（updated_at=$(manifest_field updated_at)）"
    manifest_links > "$WORK/tolink"
    while IFS= read -r dest; do
      [ -n "$dest" ] || continue
      if [ -L "$dest" ]; then
        current=$(link_target "$dest" || true)
        if [ -n "$current" ] && inside_dir "$current" "$REPO" && [ -e "$current" ]; then
          echo "  OK   $dest -> $current"
        elif [ -n "$current" ] && inside_dir "$current" "$REPO"; then
          echo "  悬空 $dest -> ${current}（重跑安装刷新）"
          problems=$((problems + 1))
        else
          echo "  外部 $dest -> $current"
          problems=$((problems + 1))
        fi
      elif [ -e "$dest" ]; then
        echo "  占用 ${dest}（不是符号链接）"
        problems=$((problems + 1))
      else
        echo "  缺失 $dest"
        problems=$((problems + 1))
      fi
    done < "$WORK/tolink"
  else
    echo "manifest：不存在（未安装）"
    problems=$((problems + 1))
  fi
  if [ -f "$CLAUDE_DIR/CLAUDE.md" ] && grep -Fq "$MARKER_BEGIN" "$CLAUDE_DIR/CLAUDE.md"; then
    echo "CLAUDE.md 标记块：已存在"
  else
    echo "CLAUDE.md 标记块：不存在"
    problems=$((problems + 1))
  fi
  if [ "$(manifest_field hook.injected)" = "1" ]; then
    hook_state=$(hook_tool check || true)
    if [ "$hook_state" = present ]; then
      echo "SessionStart hook：已注入"
    elif [ "$hook_state" = invalid ]; then
      echo "SessionStart hook：settings.json 不是合法 JSON，修复后重跑"
      problems=$((problems + 1))
    else
      echo "SessionStart hook：manifest 记为已注入，但 settings.json 中找不到（重跑 --with-hook）"
      problems=$((problems + 1))
    fi
  else
    echo "SessionStart hook：未注入"
  fi
  plugins=$(manifest_list plugins | tr '\n' ' ')
  echo "评审插件：${plugins:-无}"
  if [ "$(manifest_field codex_skills.installed)" = "1" ]; then
    codex_entry status || problems=$((problems + 1))
    echo "Codex skill：已安装（$(manifest_field codex_skills.dir)）"
  else
    echo "Codex skill：未安装（需要时加 --with-codex）"
  fi
  echo "CLI 版本（实测清单 compat.json）："
  report_versions || problems=$((problems + 1))
  if [ "$problems" = 0 ]; then
    echo "状态：全部正常"
  else
    echo "状态：有 ${problems} 项需要留意"
  fi
}

case "$MODE" in
  install) do_install ;;
  uninstall) do_uninstall ;;
  status) do_status ;;
esac
