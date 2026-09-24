#!/bin/bash
# install.sh 的隔离测试。每个用例都在临时目录里跑：HOME 与 CLAUDE_CONFIG_DIR 都被覆盖，
# 被测安装器来自复制到「含空格路径」下的 checkout，绝不触碰真实用户环境。
# 用法：bash tests/test_install.sh < /dev/null
set -u
# 系统 python3 在 HOME 被改写时会把字节码缓存写进 $HOME/Library/Caches，干扰「无残留」快照。
export PYTHONDONTWRITEBYTECODE=1

SRC=$(cd "$(dirname "$0")/.." && pwd -P)
# 物理路径：安装器用 pwd -P 解析仓库，macOS 的 /var -> /private/var 不能让比较失真。
BASE=$(cd "$(mktemp -d "${TMPDIR:-/tmp}/ai-anti-install-test.XXXXXX")" && pwd -P)
CHECKOUT="$BASE/checkout with space/ai-anti"
FAKEBIN="$BASE/fakebin"
HOOK_RAW="$CHECKOUT/adapters/claude-code/hooks/session-start.sh"
FAILS=0
CASES=0

# make_checkout <目录>：复制安装器所需的最小 checkout。
make_checkout() {
  mkdir -p "$1"
  for part in install.sh compat.json core adapters/claude-code adapters/codex/skills plugins; do
    mkdir -p "$(dirname "$1/$part")"
    cp -R "$SRC/$part" "$1/$part"
  done
}

mkdir -p "$FAKEBIN"
make_checkout "$CHECKOUT"
cat > "$FAKEBIN/claude" <<'EOF'
#!/bin/sh
echo "${FAKE_CLAUDE_VERSION:-2.1.280 (Claude Code)}"
EOF
cat > "$FAKEBIN/codex" <<'EOF'
#!/bin/sh
echo "codex-cli ${FAKE_CODEX_VERSION:-0.156.0}"
EOF
chmod +x "$FAKEBIN/claude" "$FAKEBIN/codex"
HOOK_QUOTED=$(python3 -c 'import shlex, sys; print(shlex.quote(sys.argv[1]))' "$HOOK_RAW")
# 供 pyjson 断言表达式直接嵌入的 Python 字面量
HOOK_QUOTED_PY=$(python3 -c 'import sys; print(repr(sys.argv[1]))' "$HOOK_QUOTED")

fail() { echo "  FAIL: $*"; FAILS=$((FAILS + 1)); }
pass() { :; }
check() { if "$@"; then pass; else fail "$*"; fi; }

new_case() {
  CASES=$((CASES + 1))
  echo "== $1"
  H="$BASE/case-$CASES/home"
  C="$H/.claude"
  S="$H/.agents/skills"
  OUT="$BASE/case-$CASES/out.txt"
  mkdir -p "$H"
}

# inst_from <checkout> [参数…]：用指定 checkout 的安装器
inst_from() {
  co=$1
  shift
  HOME="$H" CLAUDE_CONFIG_DIR="$C" PATH="$FAKEBIN:$PATH" /bin/bash "$co/install.sh" "$@" \
    < /dev/null > "$OUT" 2>&1
}
inst() { inst_from "$CHECKOUT" "$@"; }

# 整棵临时 HOME 的快照：路径、链接目标与文件内容哈希。
snapshot() {
  (cd "$H" && find . -print | LC_ALL=C sort | while IFS= read -r p; do
    if [ -L "$p" ]; then echo "L $p -> $(readlink "$p")"
    elif [ -f "$p" ]; then echo "F $p $(shasum "$p" | cut -d' ' -f1)"
    else echo "D $p"; fi
  done)
}

# pyjson <文件> <Python 断言表达式，data 为解析结果>
pyjson() {
  python3 - "$1" "$2" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
if not eval(sys.argv[2], {"data": data}):
    print("    json:", json.dumps(data, ensure_ascii=False))
    sys.exit(1)
PY
}

link_is() { [ -L "$1" ] && [ "$(readlink "$1")" = "$2" ]; }
has() { grep -Fq -- "$2" "$1"; }

write_json() {
  python3 - "$1" "$2" <<'PY'
import json, sys
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(json.loads(sys.argv[2]), handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY
}

assert_full_install() {
  for name in codex-implement codex-review opus-review relay-mode; do
    check link_is "$C/commands/$name.md" "$CHECKOUT/adapters/claude-code/commands/$name.md"
  done
  check link_is "$C/commands/qwen-review.md" "$CHECKOUT/plugins/qwen/commands/qwen-review.md"
  check link_is "$C/agents/implementer.md" "$CHECKOUT/adapters/claude-code/agents/implementer.md"
  check link_is "$S/claude-review" "$CHECKOUT/adapters/codex/skills/claude-review"
  check link_is "$S/codex-relay" "$CHECKOUT/adapters/codex/skills/codex-relay"
  check pyjson "$C/ai-anti.manifest.json" \
    'data["plugins"] == ["qwen"] and data["codex_skills"]["installed"] and data["hook"]["injected"]'
  check pyjson "$C/settings.json" \
    "[i['command'] for e in data['hooks']['SessionStart'] for i in e['hooks']] == [$HOOK_QUOTED_PY]"
}

# ---------------------------------------------------------------------------
new_case "带选项安装 → 普通重装 → 卸载无残留；含空格路径的 hook 可执行"
before=$(snapshot)
check inst --with-hook --with-plugin qwen --with-codex
assert_full_install
check has "$C/CLAUDE.md" "@$CHECKOUT/adapters/claude-code/CLAUDE.global.md"
cmd=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["hooks"]["SessionStart"][0]["hooks"][0]["command"])' "$C/settings.json")
mkdir -p "$BASE/case-$CASES/cwd"
hook_out=$(cd "$BASE/case-$CASES/cwd" && /bin/sh -c "$cmd" 2>&1)
check test "$hook_out" = "[接力协作模式] required——solo/required/auto/force 语义见 CLAUDE.global.md「协作模式开关」节"
check inst
assert_full_install
check inst --status
check has "$OUT" "状态：全部正常"
check has "$OUT" "评审插件：qwen"
check inst --uninstall
check test "$(snapshot)" = "$before"
[ "$(snapshot)" = "$before" ] || diff <(echo "$before") <(snapshot)

# ---------------------------------------------------------------------------
new_case "混合 SessionStart 条目：安装/卸载只动本工具对象"
mkdir -p "$C"
printf '# my notes\n' > "$C/CLAUDE.md"
user_obj='{"type": "command", "command": "echo user-hook", "timeout": 5}'
write_json "$C/settings.json" "{\"model\": \"keep-me\", \"hooks\": {\"SessionStart\": [{\"matcher\": \"startup\", \"hooks\": [$user_obj, {\"type\": \"command\", \"command\": $(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$HOOK_QUOTED")}]}, {\"hooks\": [{\"type\": \"command\", \"command\": \"echo other-entry\"}]}], \"Stop\": [{\"hooks\": [$user_obj]}]}}"
check inst --with-hook
check pyjson "$C/settings.json" "data['model'] == 'keep-me' and data['hooks']['Stop'] == [{'hooks': [$user_obj]}]"
check pyjson "$C/settings.json" "data['hooks']['SessionStart'][0]['matcher'] == 'startup' and data['hooks']['SessionStart'][0]['hooks'][0] == $user_obj"
check pyjson "$C/settings.json" "sum(i.get('command') == $HOOK_QUOTED_PY for e in data['hooks']['SessionStart'] for i in e['hooks']) == 1"
check pyjson "$C/settings.json" "len(data['hooks']['SessionStart']) == 2"
check inst
check inst --uninstall
expected="$BASE/case-$CASES/expected.json"
write_json "$expected" "{\"model\": \"keep-me\", \"hooks\": {\"SessionStart\": [{\"matcher\": \"startup\", \"hooks\": [$user_obj]}, {\"hooks\": [{\"type\": \"command\", \"command\": \"echo other-entry\"}]}], \"Stop\": [{\"hooks\": [$user_obj]}]}}"
check cmp -s "$C/settings.json" "$expected"
check test "$(cat "$C/CLAUDE.md")" = "# my notes"

# ---------------------------------------------------------------------------
new_case "与外部 SessionStart 条目共存：追加独立条目，卸载后原样"
mkdir -p "$C"
write_json "$C/settings.json" '{"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}'
cp "$C/settings.json" "$BASE/case-$CASES/seed.json"
check inst --with-hook
check pyjson "$C/settings.json" "len(data['hooks']['SessionStart']) == 2 and data['hooks']['SessionStart'][0]['hooks'][0]['command'] == 'echo mine'"
check inst --uninstall
check cmp -s "$C/settings.json" "$BASE/case-$CASES/seed.json"

# ---------------------------------------------------------------------------
new_case "旧版未加引号的 hook 条目被认领并改成引用形态（旧 manifest 继承）"
mkdir -p "$C"
write_json "$C/settings.json" "{\"hooks\": {\"SessionStart\": [{\"hooks\": [{\"type\": \"command\", \"command\": $(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$HOOK_RAW")}]}]}}"
write_json "$C/ai-anti.manifest.json" "{\"schema_version\": 1, \"repo\": $(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$CHECKOUT"), \"links\": [], \"created_dirs\": [], \"claude_md\": {\"created_by_install\": true}, \"hook\": {\"injected\": true}}"
check inst
check pyjson "$C/settings.json" "[i['command'] for e in data['hooks']['SessionStart'] for i in e['hooks']] == [$HOOK_QUOTED_PY]"
check pyjson "$C/ai-anti.manifest.json" "data['hook']['injected'] and data['schema_version'] == 2"
check inst --uninstall
check pyjson "$C/settings.json" "data == {}"

# ---------------------------------------------------------------------------
new_case "--with-codex 接管指向本仓库的既有 skill 链接"
mkdir -p "$S"
ln -s "$CHECKOUT/adapters/codex/skills/claude-review" "$S/claude-review"
check inst --with-codex
check link_is "$S/claude-review" "$CHECKOUT/adapters/codex/skills/claude-review"
check pyjson "$C/ai-anti.manifest.json" "sum(l['component'] == 'codex' for l in data['links']) == 2"
check inst --uninstall
check test ! -e "$S/claude-review" -a ! -L "$S/claude-review"
check test ! -L "$S/codex-relay"
check test -d "$S"

# ---------------------------------------------------------------------------
new_case "既有非本工具对象 fail closed，不落任何盘"
mkdir -p "$C/commands"
printf 'user file\n' > "$C/commands/codex-review.md"
before=$(snapshot)
inst && fail "普通文件被覆盖却返回成功"
check has "$OUT" "已存在且不是本工具安装的符号链接"
check test "$(snapshot)" = "$before"
rm "$C/commands/codex-review.md"
mkdir -p "$S/codex-relay"
before=$(snapshot)
inst --with-codex && fail "真实 skill 目录被接管却返回成功"
check test "$(snapshot)" = "$before"
rmdir "$S/codex-relay"
ln -s "$BASE" "$S/claude-review"
inst --with-codex && fail "外部链接被接管却返回成功"
check has "$OUT" "不属于 ai-anti"
check test ! -e "$C/ai-anti.manifest.json"

# ---------------------------------------------------------------------------
new_case "显式取消可选组件，并收回搬走的旧 qwen 链接"
before=$(snapshot)
check inst --with-hook --with-plugin qwen --with-codex
check inst --remove-plugin qwen --without-codex --without-hook
check test ! -L "$C/commands/qwen-review.md"
check test ! -L "$S/claude-review"
check test ! -e "$C/settings.json"
check pyjson "$C/ai-anti.manifest.json" "data['plugins'] == [] and not data['codex_skills']['installed'] and not data['hook']['injected']"
# 模拟搬迁前的安装：manifest 里有指向旧位置（已不存在）的 qwen 链接
ln -s "$CHECKOUT/adapters/claude-code/commands/qwen-review.md" "$C/commands/qwen-review.md"
python3 - "$C/ai-anti.manifest.json" "$C/commands/qwen-review.md" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
data["links"].append({"path": sys.argv[2], "target": "old"})
json.dump(data, open(sys.argv[1], "w"))
PY
check inst
check test ! -L "$C/commands/qwen-review.md"
check inst --with-plugin qwen
check link_is "$C/commands/qwen-review.md" "$CHECKOUT/plugins/qwen/commands/qwen-review.md"
check inst --uninstall
check test "$(snapshot)" = "$before"

# ---------------------------------------------------------------------------
new_case "--status 报告不在实测清单内的 CLI 版本；参数冲突与未知插件报错"
check inst
FAKE_CODEX_VERSION=0.1.0 inst --status
check has "$OUT" "不在实测清单"
check has "$OUT" "OK   claude  2.1.280"
has "$OUT" "状态：全部正常" && fail "版本不符仍报告全部正常"
inst --with-hook --without-hook && fail "冲突参数被接受"
inst --uninstall --with-codex && fail "卸载与安装选项同用被接受"
inst --with-plugin nosuch && fail "不存在的插件被接受"
inst --with-plugin 'bad/name' && fail "非法插件名被接受"
check inst --uninstall

# ---------------------------------------------------------------------------
new_case "仓库移动后在新位置重装：接管 manifest 登记的旧链接"
OLD="$BASE/case-$CASES/old place/ai-anti"
NEW="$BASE/case-$CASES/new place/ai-anti"
make_checkout "$OLD"
before=$(snapshot)
check inst_from "$OLD" --with-hook --with-plugin qwen --with-codex
mkdir -p "$(dirname "$NEW")"
mv "$OLD" "$NEW"
check inst_from "$NEW"
for name in codex-implement codex-review opus-review relay-mode; do
  check link_is "$C/commands/$name.md" "$NEW/adapters/claude-code/commands/$name.md"
done
check link_is "$C/commands/qwen-review.md" "$NEW/plugins/qwen/commands/qwen-review.md"
check link_is "$S/codex-relay" "$NEW/adapters/codex/skills/codex-relay"
NEW_PY=$(python3 -c 'import sys; print(repr(sys.argv[1]))' "$NEW")
check pyjson "$C/ai-anti.manifest.json" "data['repo'] == $NEW_PY and all(l['target'].startswith($NEW_PY + '/') for l in data['links'])"
NEW_HOOK_PY=$(python3 -c 'import shlex, sys; print(repr(shlex.quote(sys.argv[1])))' "$NEW/adapters/claude-code/hooks/session-start.sh")
check pyjson "$C/settings.json" "[i['command'] for e in data['hooks']['SessionStart'] for i in e['hooks']] == [$NEW_HOOK_PY]"
check has "$C/CLAUDE.md" "@$NEW/adapters/claude-code/CLAUDE.global.md"
check inst_from "$NEW" --status
check has "$OUT" "状态：全部正常"
check inst_from "$NEW" --uninstall
check test "$(snapshot)" = "$before"

# ---------------------------------------------------------------------------
new_case "仓库移动后接管的反例：未登记路径、登记但指向别处、manifest repo 为空均 fail closed"
OLD="$BASE/case-$CASES/old/ai-anti"
NEW="$BASE/case-$CASES/new/ai-anti"
make_checkout "$OLD"
check inst_from "$OLD"
mkdir -p "$(dirname "$NEW")"
mv "$OLD" "$NEW"
# manifest 未登记的路径，指向旧 checkout 内部
mkdir -p "$S"
ln -s "$OLD/adapters/codex/skills/claude-review" "$S/claude-review"
before=$(snapshot)
inst_from "$NEW" --with-codex && fail "未登记的旧仓库链接被接管却返回成功"
check has "$OUT" "不属于 ai-anti"
check test "$(snapshot)" = "$before"
rm "$S/claude-review"
# manifest 登记过，但当前指向旧仓库之外
rm "$C/commands/codex-review.md"
ln -s "$BASE/case-$CASES/elsewhere.md" "$C/commands/codex-review.md"
before=$(snapshot)
inst_from "$NEW" && fail "登记但指向旧仓库外的链接被接管却返回成功"
check test "$(snapshot)" = "$before"
# manifest repo 为空：外部链接不得接管
python3 - "$C/ai-anti.manifest.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
data["repo"] = ""
json.dump(data, open(sys.argv[1], "w"))
PY
before=$(snapshot)
inst_from "$NEW" && fail "manifest repo 为空时外部链接被接管却返回成功"
check test "$(snapshot)" = "$before"

# ---------------------------------------------------------------------------
new_case "已注入 hook 后 settings.json 损坏：重装、--without-hook、--uninstall 都在改动前停止"
check inst --with-hook
printf '{broken' > "$C/settings.json"
before=$(snapshot)
inst && fail "坏 settings.json 上重装却返回成功"
check has "$OUT" "settings.json 不是合法 JSON"
check test "$(snapshot)" = "$before"
inst --without-hook && fail "坏 settings.json 上 --without-hook 却返回成功"
check test "$(snapshot)" = "$before"
inst --uninstall && fail "坏 settings.json 上卸载却返回成功"
check test "$(snapshot)" = "$before"
check test -f "$C/ai-anti.manifest.json"
inst --status
check has "$OUT" "settings.json 不是合法 JSON，修复后重跑"
has "$OUT" "重跑 --with-hook" && fail "--status 对坏 settings.json 仍提示重跑 --with-hook"
rm "$C/settings.json"
check inst --uninstall

# ---------------------------------------------------------------------------
new_case "首装 --with-hook 遇坏 settings.json：不落任何盘"
mkdir -p "$C"
printf '{broken' > "$C/settings.json"
before=$(snapshot)
inst --with-hook && fail "坏 settings.json 上首装却返回成功"
check test "$(snapshot)" = "$before"

# ---------------------------------------------------------------------------
new_case "CLAUDE.md 标记顺序颠倒：安装与卸载都在改动前停止"
mkdir -p "$C"
printf '# notes\n<!-- ai-anti:end -->\n<!-- ai-anti:begin -->\n' > "$C/CLAUDE.md"
before=$(snapshot)
inst && fail "标记颠倒时首装却返回成功"
check has "$OUT" "顺序颠倒"
check test "$(snapshot)" = "$before"
rm "$C/CLAUDE.md"
check inst
printf '# notes\n<!-- ai-anti:end -->\n<!-- ai-anti:begin -->\n' > "$C/CLAUDE.md"
before=$(snapshot)
inst && fail "标记颠倒时重装却返回成功"
check test "$(snapshot)" = "$before"
inst --uninstall && fail "标记颠倒时卸载却返回成功"
check has "$OUT" "顺序颠倒"
check test "$(snapshot)" = "$before"

# ---------------------------------------------------------------------------
new_case "qwen 配置片段：credential_paths 不是数组 → 本档不可用（exit 3）"
snippet="$BASE/case-$CASES/qwen_conf.py"
awk 'f && $0 == "PY" { exit } f { print } index($0, "QWEN_CONF=$(python3") == 1 { f = 1 }' \
  "$CHECKOUT/plugins/qwen/commands/qwen-review.md" > "$snippet"
check test -s "$snippet"
check grep -q credential_paths "$snippet"
mkdir -p "$H/.config/ai-anti"
write_json "$H/.config/ai-anti/config.json" '{"plugins": {"qwen": {"model": "m", "base_url": "https://example.invalid", "credential_paths": "~/.config/ai-anti/secrets.json"}}}'
HOME="$H" python3 "$snippet" < /dev/null > "$OUT" 2>&1
check test "$?" = 3
write_json "$H/.config/ai-anti/config.json" '{"plugins": {"qwen": {"model": "m", "base_url": "https://example.invalid", "credential_paths": ["~/.config/ai-anti/secrets.json"]}}}'
HOME="$H" python3 "$snippet" < /dev/null > "$OUT" 2>&1
check test "$?" = 0
check test "$(sed -n 3p "$OUT")" = "~/.config/ai-anti/secrets.json"

# ---------------------------------------------------------------------------
echo
echo "cases=$CASES failures=$FAILS"
if [ "$FAILS" = 0 ]; then
  echo "ALL PASS"
  case "$BASE" in
    */ai-anti-install-test.*) rm -r -- "$BASE" ;;
  esac
  exit 0
fi
echo "FAILED (临时目录保留在 $BASE 供排查)"
exit 1
