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
  ./install.sh --status --project DIR  可选只读项目根/模式/基线诊断
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
ACTION=""
PROJECT=""
WITH_HOOK=""       # 空＝继承 manifest；1＝安装；0＝移除
WITH_CODEX=""
ADD_PLUGINS=""
DEL_PLUGINS=""
INSTALL_OPTS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall|--status)
      [ -z "$ACTION" ] || [ "$ACTION" = "$1" ] || die "--status 与 --uninstall 不能同用"
      ACTION=$1; MODE=${1#--} ;;
    --project)
      [ $# -ge 2 ] && [ -n "$2" ] || die "--project 需要目录"
      [ -z "$PROJECT" ] || die "--project 只能指定一次"
      PROJECT=$2; shift ;;

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

if [ -n "$PROJECT" ]; then
  [ "$MODE" = status ] || die "--project 仅与 --status 同用"
  [ -d "$PROJECT" ] && [ -r "$PROJECT" ] && [ -x "$PROJECT" ] || die "--project 目录无效或不可访问"
  PROJECT=$(cd "$PROJECT" && pwd -P) || die "无法访问项目目录"
fi

command -v "$PY" >/dev/null 2>&1 || die "找不到 ${PY}（manifest 读写需要 python3）"


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
        print("🟢 Codex entry: OK (%s)" % target)
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
    print(("🔴 " if mode == "status" else "") + "ai-anti: Codex entry: %s" % exc, file=sys.stderr)
    sys.exit(1)
PY
}

# Shared source contract: bounded explicit list, never follow FIFO/non-regular input.
check_sources() {
  "$PY" - "$REPO" "$1" "$MODE" <<'PY'
import os, pathlib, stat, sys
root = pathlib.Path(sys.argv[1])
names = ["core/" + n + ".md" for n in ("WORKFLOW_ENTRY", "PLANNING", "DESIGN", "RETRO", "DEBUGGING", "TESTING", "REVIEW_PRINCIPLES", "CLI_DISCIPLINE")]
names += ["adapters/claude-code/CLAUDE.global.md", "adapters/claude-code/agents/implementer.md", "adapters/claude-code/commands/codex-implement.md"]
if sys.argv[2] == "1":
    names += ["adapters/codex/AGENTS.global.md"] + ["adapters/codex/skills/" + n + "/SKILL.md" for n in ("claude-review", "codex-relay")]
errors = 0
for name in names:
    path = root / name
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("not a regular file")
        if not os.access(path, os.R_OK) or not path.read_text(encoding="utf-8").strip():
            raise ValueError("unreadable or empty")
    except (OSError, UnicodeError, ValueError) as exc:
        print(("🔴 " if sys.argv[3] == "status" else "") + "错误 共享源缺口：%s (%s)" % (path, exc))
        errors += 1
sys.exit(bool(errors))
PY
}

do_install() {
  prev_hook=$(manifest_field hook.injected)
  prev_codex=$(manifest_field codex_skills.installed)
  EFF_HOOK=${WITH_HOOK:-${prev_hook:-0}}
  EFF_CODEX=${WITH_CODEX:-${prev_codex:-0}}
  check_sources "$EFF_CODEX" || die "源完整性预检失败，未做任何改动"
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
    [ -f "$HOOK_SH" ] && [ ! -L "$HOOK_SH" ] && [ -r "$HOOK_SH" ] && [ -x "$HOOK_SH" ] || die "${HOOK_SH} 不存在或不可执行"
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
    print("🟡   警告 版本清单 %s 无法读取" % sys.argv[1])
    sys.exit(1)
problems = 0
for label, env in (("claude", "CLAUDE_V"), ("codex", "CODEX_V")):
    text = os.environ[env].strip()
    match = re.search(r"[0-9]+\.[0-9]+\.[0-9]+", text)
    if not text:
        print("🟡   警告 %-7s 未安装" % label)
        problems += 1
    elif not match:
        print("🟡   警告 %-7s 版本无法识别：%s" % (label, text))
        problems += 1
    elif match.group(0) in tested.get(label, []):
        print("🟢 OK   %-7s %s（在实测清单内）" % (label, match.group(0)))
    else:
        print("🟡   警告 %-7s %s（不在实测清单 %s 内；可用但未经验证）"
              % (label, match.group(0), ", ".join(tested.get(label, []))))
        problems += 1
current = sys.version_info[:3]
text = ".".join(str(x) for x in current)
if current[:len(minimum)] >= minimum:
    print("🟢 OK   python  %s（≥ %s）" % (text, compat["python_min"]))
else:
    print("🟡   警告 python  %s（低于 %s）" % (text, compat["python_min"]))
    problems += 1
sys.exit(1 if problems else 0)
PY
}

# Report health independently of legacy helpers: corrupt types never reach them.
status_installation() {
  "$PY" - "$REPO" "$CLAUDE_DIR" "$MANIFEST" "$GLOBAL_MD" "$HOOK_SH" "$HOME" "$AGENTS_SKILLS_DIR" <<'PY'
import json, os, pathlib, shlex, stat, sys
repo, claude, manifest, global_md, hook, home, skills = sys.argv[1:]
errors = 0
def error(text):
    global errors
    errors += 1
    print("🔴 错误 " + text)
def regular(path):
    return stat.S_ISREG(pathlib.Path(path).lstat().st_mode)
data = {}
valid = True
print("仓库：" + repo)
print("用户级目录：" + claude)
try:
    if not os.path.lexists(manifest):
        print("🔴 manifest：不存在（未安装）")
        errors += 1
    else:
        if not regular(manifest): raise ValueError("not a regular file")
        data = json.loads(pathlib.Path(manifest).read_text())
        if not isinstance(data, dict): raise ValueError("root must be object")
        if "schema_version" in data and (not isinstance(data["schema_version"], int) or isinstance(data["schema_version"], bool)): raise ValueError("schema_version type")
        types = {"repo": str, "links": list, "created_dirs": list, "plugins": list,
                 "claude_md": dict, "hook": dict, "codex_skills": dict}
        for key, kind in types.items():
            if key in data and not isinstance(data[key], kind): raise ValueError(key + " type")
        for key in ("plugins", "created_dirs"):
            if any(not isinstance(n, str) for n in data.get(key, [])): raise ValueError(key + " members")
        for section, fields in (("hook", {"injected": bool, "command": (str, type(None))}),
                                ("codex_skills", {"installed": bool, "dir": str}),
                                ("claude_md", {"path": str, "created_by_install": bool}),
                                ("codex_entry", {"path": str, "source": str, "created_by_install": bool, "separator_hex": str, "suffix_hex": str})):
            node = data.get(section)
            if node is not None:
                if not isinstance(node, dict): raise ValueError(section + " type")
                for key, kind in fields.items():
                    if key in node and not isinstance(node[key], kind): raise ValueError(section + "." + key)
        for entry in data.get("links", []):
            if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) for k in ("path", "target")):
                raise ValueError("link entry type")
        print("manifest：" + manifest)
        if data.get("schema_version") != 3 or any(k not in data for k in ("codex_skills", "plugins", "links")):
            print("🔴 提示 legacy manifest：重跑安装升级")
            errors += 1
except (OSError, UnicodeError, ValueError, TypeError) as exc:
    error("manifest 损坏：" + str(exc))
    valid = False
    data = {}
if valid and data:
    entries = {n["path"]: n["target"] for n in data.get("links", [])}
    required = {os.path.join(claude, "agents/implementer.md"): os.path.join(repo, "adapters/claude-code/agents/implementer.md"),
                os.path.join(claude, "commands/codex-implement.md"): os.path.join(repo, "adapters/claude-code/commands/codex-implement.md")}
    if data.get("codex_skills", {}).get("installed"):
        if data["codex_skills"].get("dir") != skills: error("Codex skill 目录与当前安装路径不一致")
        for name in ("claude-review", "codex-relay"):
            required[os.path.join(skills, name)] = os.path.join(repo, "adapters/codex/skills", name)
    for dest, want in required.items():
        if entries.get(dest) != want: error("托管路由未登记或目标错误：" + dest)
    for dest, want in entries.items():
        actual = os.path.realpath(dest)
        if not os.path.islink(dest) or actual != os.path.realpath(want) or not actual.startswith(repo + os.sep) or not os.path.exists(actual):
            error("托管链接目标错误/缺失：" + dest)
        else: print("🟢 OK   %s -> %s" % (dest, actual))
path = pathlib.Path(claude) / "CLAUDE.md"
try:
    if not regular(path): raise ValueError("not a regular file")
    text = path.read_text()
    begin, end = "<!-- ai-anti:begin -->", "<!-- ai-anti:end -->"
    if text.count(begin) != 1 or text.count(end) != 1 or text.index(begin) >= text.index(end):
        raise ValueError("missing/damaged markers")
    imported = "~/" + global_md[len(home) + 1:] if global_md.startswith(home + "/") else global_md
    if "@" + imported not in text[text.index(begin):text.index(end)].splitlines():
        raise ValueError("wrong import target")
    print("🟢 CLAUDE.md 标记块：已存在（目标正确）")
except (OSError, UnicodeError, ValueError) as exc:
    error("CLAUDE.md 标记块：" + str(exc))
if valid and data.get("hook", {}).get("injected"):
    try:
        if not regular(hook) or not os.access(hook, os.R_OK | os.X_OK): raise ValueError("hook source missing/unreadable/not executable")
        settings_path = pathlib.Path(claude) / "settings.json"
        if not regular(settings_path): raise ValueError("settings.json not a regular file")
        settings = json.loads(settings_path.read_text())
        current = shlex.quote(hook)
        if data["hook"].get("command") != current: raise ValueError("manifest command not current source")
        found = any(obj.get("command") == current for entry in settings.get("hooks", {}).get("SessionStart", [])
                    if isinstance(entry, dict) for obj in entry.get("hooks", []) if isinstance(obj, dict))
        if not found: raise ValueError("settings command missing")
        print("🟢 SessionStart hook：已注入")
    except json.JSONDecodeError:
        error("SessionStart hook：settings.json 不是合法 JSON，修复后重跑")
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError) as exc: error("SessionStart hook：" + str(exc))
else: print("🟡 SessionStart hook：未注入或未验证")
print("评审插件：" + (" ".join(data.get("plugins", [])) or "无"))
print("提示 插件仅检查托管链接，不检查 plugin.json、配置、凭证或通道可用性")
if valid and data.get("codex_skills", {}).get("installed"):
    print("🟢 Codex skill：已安装（%s）" % data["codex_skills"].get("dir", ""))
else: print("🟡 Codex skill：未安装或未验证（需要时加 --with-codex）")
print("Codex 检查标志：%s" % ("1" if valid and data.get("codex_skills", {}).get("installed") else "0"))
sys.exit(1 if errors else 0)
PY
}

report_project() {
  "$PY" - "$PROJECT" "$HOME" <<'PY'
import json, os, pathlib, stat, subprocess, sys
errors = 0
def error(text):
    global errors
    errors += 1
    print("🔴 错误 " + text)
root = pathlib.Path(sys.argv[1])
env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
env["GIT_OPTIONAL_LOCKS"] = "0"
def git(*args):
    return subprocess.run(["git", "-C", str(root), *args], env=env, timeout=5,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
try:
    result = git("rev-parse", "--show-toplevel")
    if result.returncode == 0:
        root = pathlib.Path(result.stdout.strip()).resolve()
        def revision(name):
            check = git("rev-parse", "--verify", "--quiet", name)
            if check.returncode == 0: return True
            if check.returncode == 1 and not check.stderr.strip(): return False
            return None
        head = revision("HEAD^{commit}")
        print(("🟢 " if head else "🟡 ") + "Git：%s" % ("已有 HEAD" if head else "无 HEAD" if head is False else "HEAD 未验证"))
        if head is False: print("🟡 提示 完整接力缺少基线，不自动初始化或提交")
        if head is None: print("🟡 提示 Git 引用检测失败，未验证")
    elif "not a git repository" in result.stderr:
        print("🟡 Git：非 Git 目录；提示 完整接力缺少基线")
    else: print("🟡 提示 Git 检测失败，未验证")
except (OSError, subprocess.TimeoutExpired, UnicodeError): print("🟡 提示 Git 不可用/超时，未验证")
print("项目根：" + str(root))
for name in ("AGENTS.md", "CLAUDE.md"):
    path = root / name
    if not os.path.lexists(path): print("🟡 提示 项目入口 %s 缺失：小任务可用已有平台默认" % name)
    else:
        try:
            if not stat.S_ISREG(path.stat().st_mode) or not os.access(path, os.R_OK): raise ValueError()
            path.read_text()
            print("🟢 项目入口 %s：可读；正文覆写需 AI 读取" % name)
        except (OSError, UnicodeError, ValueError): error("项目入口 %s 不可读/不安全" % name)
config = pathlib.Path(sys.argv[2]) / ".config/ai-anti/config.json"
data, config_error = {}, None
try:
    if os.path.lexists(config):
        if not stat.S_ISREG(config.stat().st_mode): raise ValueError("config not regular")
        data = json.loads(config.read_text())
        if not isinstance(data, dict): raise ValueError("config root type")
except (OSError, UnicodeError, ValueError) as exc:
    config_error = str(exc)
    error("全局配置无法读取：" + config_error)
modepath = root / ".claude/relay-mode.local"
try:
    if os.path.lexists(modepath):
        if not stat.S_ISREG(modepath.stat().st_mode): raise ValueError("project mode not regular")
        mode, source = modepath.read_text().strip(), "项目模式文件"
    else:
        if config_error: raise ValueError("global config unavailable")
        relay = data.get("relay", {})
        if not isinstance(relay, dict): raise ValueError("relay type")
        mode = relay.get("default_mode", "rules")
        source = "全局默认" if "default_mode" in relay else "内置默认"
    if mode not in ("solo", "auto", "rules", "force"): raise ValueError("invalid mode")
    print("🟢 协作模式：%s（来源：%s）" % (mode, source))
except (OSError, UnicodeError, ValueError, TypeError): error("协作模式无效或不可读；合法值 solo/auto/rules/force，未回落默认")
try:
    if config_error: raise ValueError("global config unavailable")
    defaults = data.get("defaults", {})
    if not isinstance(defaults, dict): raise ValueError("defaults type")
    scale = defaults.get("deployment_scale")
    if scale is not None and (not isinstance(scale, str) or not scale.strip()): raise ValueError("deployment_scale type/value")
    print("部署规模 fallback：" + (scale or "未声明部署规模"))
except ValueError as exc: error("全局部署规模无法确认：" + str(exc))
print("提示 仅显示全局/内置 fallback，项目正文覆写需 AI 读取；当次用户指令可覆盖报告模式")
print("边界：可供新会话加载，不证明模型遵循、通道联网或完整接力；不执行项目脚本")
sys.exit(bool(errors))
PY
}

do_status() {
  problems=0
  failures=0
  installation_report=$(status_installation) || { problems=$((problems + 1)); failures=1; }
  printf '%s\n' "$installation_report" | sed '/^Codex 检查标志：/d'
  codex=0
  if printf '%s\n' "$installation_report" | grep -Fxq "Codex 检查标志：1"; then codex=1; fi
  check_sources "$codex" || { problems=$((problems + 1)); failures=1; }
  if [ "$codex" = 1 ]; then codex_entry status || { problems=$((problems + 1)); failures=1; }; fi
  echo "CLI 版本（实测清单 compat.json）："
  report_versions || problems=$((problems + 1))
  if [ -n "$PROJECT" ]; then report_project || { problems=$((problems + 1)); failures=1; }; else echo "提示 plain --status 不解析 relay/defaults；需要时加 --project DIR"; fi
  if [ "$problems" = 0 ]; then echo "🟢 状态：全部正常";
  elif [ "$failures" = 1 ]; then echo "🔴 状态：有 ${problems} 项需要留意";
  else echo "🟡 状态：有 ${problems} 项需要留意"; fi
}

case "$MODE" in
  install) do_install ;;
  uninstall) do_uninstall ;;
  status) do_status ;;
esac
