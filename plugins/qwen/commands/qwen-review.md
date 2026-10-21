---
description: qwen 插件评审通道:经 anthropic 兼容端点让用户配置指定的模型做代理式只读评审。用法:/qwen-review plan <方案文件路径> 或 /qwen-review diff
---

用 qwen 插件（anthropic 兼容端点 + Claude Code CLI 驱动；模型、端点、凭证路径读用户配置）做交叉评审。参数: $ARGUMENTS

**定位**：跨模型评审通道插件（`plugins/qwen/plugin.json`，`kind: cross-model`），评审质量为代理式（模型自主读仓库），与 codex exec 同形态。它在通道顺序中排第几由用户配置 `review.channels` 决定（见 `CLAUDE.global.md`「异常与边界」的评审通道降级阶梯）。最终汇报中必须标注「qwen 评审」。

**调用形态先读 `core/CLI_DISCIPLINE.md`（外部 CLI 的调用纪律）**（四条，唯一权威）。
`claude -p` 与 `codex exec` 同属非交互 CLI，同样适用——下面的命令已按那四条写好。

**插件配置与凭证查找接口（用户级 `~/.config/ai-anti/config.json` 的 `plugins.qwen`，fail-closed）**：

- `model`：端点上的模型名；`base_url`：anthropic 兼容端点地址；
- `credential_paths`：凭证文件查找顺序，每个文件按 `credential_key` 逐级取值（缺字段才使用 `tokenplan.value`），按序取第一个可读到的非空值；
  相对路径按调用 cwd 解析（本插件现有 shell 行为；推荐绝对路径或 `~`），`~` 展开（如用户级
  `~/.config/ai-anti/secrets.json`，任何仓库之外，不入库）。

配置文件缺失、缺 `plugins.qwen` 或三项任一缺失/类型不对，或凭证按查找顺序全部缺失/取到空值 →
**本档判定不可用**，不发起任何调用（不做半配置调用），在汇报中写明「qwen 档不可用：配置缺失／
凭证缺失」并把本档结果报告给主模型。配置文件存在但 JSON 解析失败 → **停止并向用户报告配置损坏**，
不当作「未配置」静默降档（防止配置写坏后悄悄换通道）。

**调用方式**（Bash timeout 设 600000；超时会自动转后台，**转后台后必须主动查进度**，
长时间零输出即判定卡死）：

```bash
QWEN_CONF=$(python3 - <<'PY'
import json, pathlib, sys
path = pathlib.Path.home() / ".config" / "ai-anti" / "config.json"
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except FileNotFoundError:
    sys.exit(3)
except ValueError as exc:
    print("ai-anti 配置损坏：%s：%s" % (path, exc), file=sys.stderr)
    sys.exit(4)
try:
    qwen = data["plugins"]["qwen"]
    if not isinstance(qwen["credential_paths"], list):
        sys.exit(3)
    key = qwen.get("credential_key", "tokenplan.value")
    if key not in ("tokenplan.value", "review_services.token_plan.value", "review_services.custom_api.value"):
        sys.exit(3)
    fields = [qwen["model"], qwen["base_url"], key] + list(qwen["credential_paths"])
except (KeyError, TypeError):
    sys.exit(3)
if len(fields) < 4 or not all(isinstance(x, str) and x and "\n" not in x for x in fields):
    sys.exit(3)
print("\n".join(fields))
PY
)
case $? in
  0) ;;
  4) echo "ai-anti 配置损坏：停止并报告用户，不静默降档"; exit 4 ;;
  *) echo "qwen 档不可用：plugins.qwen 配置缺失，报告主模型"; exit 3 ;;
esac
QWEN_MODEL=$(printf '%s\n' "$QWEN_CONF" | sed -n 1p)
QWEN_BASE_URL=$(printf '%s\n' "$QWEN_CONF" | sed -n 2p)
QWEN_CREDENTIAL_KEY=$(printf '%s\n' "$QWEN_CONF" | sed -n 3p)
QWEN_PATHS=$(printf '%s\n' "$QWEN_CONF" | sed -n '4,$p')

TOKENPLAN_KEY=$(QWEN_CREDENTIAL_KEY="$QWEN_CREDENTIAL_KEY" QWEN_PATHS="$QWEN_PATHS" python3 - <<'PY'
import json, os, pathlib, sys
for raw in os.environ["QWEN_PATHS"].split("\n"):
    if not raw:
        continue
    try:
        value = json.loads(pathlib.Path(raw).expanduser().read_text(encoding="utf-8"))
        for part in os.environ["QWEN_CREDENTIAL_KEY"].split("."):
            value = value.get(part) if isinstance(value, dict) else None
    except Exception:
        continue
    if isinstance(value, str) and value.strip() and not any(c in value for c in "\r\n\x00"):
        print(value)
        sys.exit(0)
sys.exit(3)
PY
) || { echo "qwen 档不可用：凭证按查找顺序均缺失，报告主模型"; exit 3; }

env -u ANTHROPIC_API_KEY -u ANTHROPIC_MODEL -u ANTHROPIC_SMALL_FAST_MODEL \
  ANTHROPIC_BASE_URL="$QWEN_BASE_URL" \
  ANTHROPIC_AUTH_TOKEN="$TOKENPLAN_KEY" \
  ANTHROPIC_DEFAULT_SONNET_MODEL="$QWEN_MODEL" \
  ANTHROPIC_DEFAULT_OPUS_MODEL="$QWEN_MODEL" \
  ANTHROPIC_DEFAULT_HAIKU_MODEL="$QWEN_MODEL" \
  claude -p "$(cat <scratchpad>/qwen_prompt.txt)" --model sonnet \
  --allowedTools "Read,Grep,Glob,Bash(git show *),Bash(git -C * show *),Bash(git diff *),Bash(git -C * diff *),Bash(git log *),Bash(git -C * log *),Bash(git status*),Bash(git branch*)" \
  --output-format json < /dev/null > <scratchpad>/qwen_review.json 2>&1
```

要点：
- **模型名必须走「档位别名 + 档位默认模型映射」，不能用 `ANTHROPIC_MODEL` 直传**
  （2026-08-18 实测，claude CLI 2.1.234）。直传会被 CLI **客户端侧**的模型注册表拦下、
  请求根本发不出去；而只设 `ANTHROPIC_DEFAULT_*` 却不传 `--model sonnet` 的话，CLI 会
  改发 `~/.claude/settings.json` 里的 `model` 值，端点回 `Model not exist`。两者都要有。
- **`env -u` 那三个变量都要剥**：`ANTHROPIC_API_KEY` 会抢占 `ANTHROPIC_AUTH_TOKEN`
  （CLI 会打「另一个 auth source 优先」的警告）；`ANTHROPIC_MODEL` /
  `ANTHROPIC_SMALL_FAST_MODEL` 是防父环境继承——子进程继承父环境，而本模板从前自己就
  示范过 `export ANTHROPIC_MODEL=…`，某台机器的 shell rc 导出它的概率不为零，一旦继承
  就会再次触发客户端校验。
- **怎么判这一发成没成**（2026-08-18 立、2026-08-19 三态实测补全；防的是把成功误判成
  失败而白白降档）。本命令是 `2>&1`，stdout 与 stderr **合流进同一个文件**，别按「分看
  两个流」去读。三种形态实测如下：

  | 形态 | 文件里有 JSON 吗 | `is_error` | 判读 |
  |---|---|---|---|
  | **成功** | 有 | `false` | `result` 即评审正文 |
  | **端点侧失败** | **有** | `true` | `result` 是 API 报文，如 `code:"InvalidParameter","message":"Model not exist."` ⇒ **模型名本身不对**，按下面「端点可用模型」一条改配置 |
  | **客户端侧失败** | **没有**（只剩噪音行） | — | CLI 的模型注册表把请求拦在本地 ⇒ **模型名传递方式不对**，检查 `--model sonnet` 与三个 `ANTHROPIC_DEFAULT_*` 是否都在 |

  **`[claude-code:unrecognized_model]` 这一行三种形态里都会出现**（成功的调用也打它），
  **单独不构成任何判据**——2026-08-18 那次静默降档就是把这行当成了失败信号。
  **唯一判据是：能否解析出 JSON 对象，以及其中的 `is_error`。**
- **端点可用模型**：以端点服务商公布的清单为准，换模型时先查清单并用一次最小调用确认
  `is_error=false`，不要猜；2026-08-18 实测这类 anthropic 兼容端点对 `claude-*` 系列一律回
  `Model not exist`。换模型时若有 `review_services`，使用 App 或同时更新活动 profile 与 `plugins.qwen.model` 投影；旧配置才只改 `plugins.qwen.model`。
- 提示词经 `<scratchpad>/qwen_prompt.txt` 传入、命令末尾 `< /dev/null`、输出直写不接管道——
  三条都来自 `core/CLI_DISCIPLINE.md` 那条纪律，缺一条就可能空转或丢结论。
- 密钥运行时通过上面的查找接口按配置的 `credential_key` 逐级读取，只存活在本次 shell 变量里，
  **不写进任何文件或历史**，也不回显。
- allowedTools 是只读白名单（含 `git -C` 形态——qwen 习惯带 `-C` 跑 git，漏了会 denial 空转烧额度）。
- `--output-format json` 的 `usage` 字段留作额度消耗记录；`result` 字段是评审文本。

**两种模式的提示词**（与 codex-review 同构）。部署规模口径仍是项目覆写项；qwen 的凭证、
模型映射、只读白名单、JSON 三态结果与 `permission_denials` 本地判读均保留本文件既有规则，不上收。

运行时路径解析（送审编排者执行，先于读取 R3）：

```python
from pathlib import Path
import os
config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()
command_source = (config_dir / "commands" / "qwen-review.md").resolve()
ai_anti_root = command_source.parents[3]
core = ai_anti_root / "core" / "REVIEW_PRINCIPLES.md"
if not core.is_file() or not os.access(core, os.R_OK):
    raise SystemExit(f"停止送审：无法读取 {core}")
```

本文件安装后是 `~/.claude/commands/qwen-review.md` 指向 `plugins/qwen/commands/qwen-review.md`
的链接，解析后 `parents[3]` 同样是 ai-anti 根。
路径可含空格，不能由业务 cwd 推导。送审编排者按 R3 做 preflight；公共主模型规则不赋予 reviewer
实施、派发或额外权限。读取 core 后才把每种模式所列章节**原文**运行时放入 prompt，不能只传链接。
`plan`：先按 R3 fail closed 派生 request。通过后向 `qwen_prompt.txt` 注入 R1、R4、R6、R7、R8、R10 原文、项目部署规模口径及 qwen 特有铁律：不得读取凭证文件，完整 API key 不得出现在输出。
`diff`（或空参数）：注入 R1、R5、R6、R7、R8、R10 原文与项目部署规模口径，并在接力流程附改动清单；审 commit 时按
原模板使用 `git show` 形态。

**诚实防线（2026-08-05 首次校准实测的失效模式，两条都是硬要求）**：

- 评审提示词运行时注入 R10 原文；本档额外明确：评审默认是**静态审查**，没有测试执行权限。2026-08-05 首次校准的失效模式原句：6 条测试命令全被拒，报告却声称「实测 215 例」并编造了失败细节。
- 拿到输出后，**必须先核对 JSON 的 `permission_denials` 字段**：凡被拒命令对应的「实测/运行」类声明一律作废，按静态推断重新定级；再进入逐条裁决。
- `plan` 模式另加一条：`permission_denials` 若含**需求原文文件或方案文件的 Read**，则本轮
  **整轮作废**、不计为有效评审（评审方没读到那两个输入，产出的清单与差集都不成立）——
  修好读取权限后重跑，不得只把个别结论降级了事。


拿到输出后先按本文件的 `permission_denials` 规则判读，再逐条完整呈现和裁决；标注「qwen 评审」
及实际配置的模型名。

**额度与故障**：端点订阅可能有滚动配额窗（实测为 5 小时与 7 天两个窗），打满返回 429 `insufficient_quota`（报文自带重置时刻）。评审中途 429：等待窗口重置后重跑（上次进度不保留）；实测单次代理评审消耗可观（低档订阅的短窗口装不下一次完整评审）。**本档只报告本档成败**（成功；不可用及原因；配额耗尽及报文里的恢复时刻），由主模型按 `CLAUDE.global.md`「评审通道降级阶梯」的通道顺序进入下一档（同族档仍须满足其启用条件与补偿条款），**不要直接跳到跳过评审**；全部通道皆不可用才落到该阶梯最后一档：普通大任务跳过评审并标注，高风险任务停下问用户。另注意该阶梯的「会话内不重试」条款：本会话已确认耗尽的档不再重试，新会话重新逐档尝试。

Codex 侧的 `claude-review` skill 的源码入口 `review.py --channel qwen` 直接复用相同配置，使用 `--repo`
作为相对凭证路径基准；不经过本插件的 `review.channels` 路由。
