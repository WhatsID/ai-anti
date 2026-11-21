---
name: agy-relay
description: 由 AGY 起草方案（可选 Claude 双方案规划）、委派 Codex 评审与实施，再由 AGY 独立终审。
---

# AGY 主导接力

从已安装插件的规则解析机制根。实际读取 core/WORKFLOW_ENTRY.md、core/WORKFLOW_CONTINUITY.md、
core/REVIEW_PRINCIPLES.md 与 core/CLI_DISCIPLINE.md。按项目规则与共享模式确定是否进入本流程。
由 AGY 负责方案、裁决、需求核对、终审与汇报。方案阶段止于待实施，明确授权后才启动实施。
按共享方法读取适用的规划、设计和测试正文。记录阶段、产物、指纹、用户裁决和未覆盖项，
产物放 <scratchpad> 或项目指定的本地任务目录。

## 第 1 步：冻结基线并起草方案

读取项目入口、范围规则、历史与现状。用以下命令确认主树根、提交、分支与脏状态；
symbolic-ref 退出 1 时记录 detached，其他失败明确报告。

```bash
git -C "$repo" rev-parse --show-toplevel
git -C "$repo" rev-parse --verify 'HEAD^{commit}'
git -C "$repo" symbolic-ref -q HEAD
git -C "$repo" status --short
```

同时记录 Git 仓库身份（git rev-parse --path-format=absolute --git-common-dir）。
按项目方案目录保存 YYYYMMDD-slug.md 与同名 .request.md。按 R2 逐字收录用户各轮需求、
约束与裁决，推断放在附录。列出改动文件、风险、验收命令与部署规模。记录目标路径中
已有的用户脏输入并确认哪些纳入范围。用 hashlib.sha256(path.read_bytes()).hexdigest()
记录每个既有常规文件的当前字节指纹；计划新增路径须通过 os.path.lexists(path) 为假
验证并标记 absent。记录预期删除的既有目标指纹。未确认的重叠脏输入先报告。

### 双方案规划分支

从机制根的 `adapters/codex/skills/claude-review/scripts` 导入 `ai_anti_config.load` 和
`agy_dual_plan`，读取 `~/.config/ai-anti/config.json`。配置损坏或字段无效即停并报告。
优先级为**用户当次明确指令 > 本会话临时禁用状态 > `agy_relay.dual_plan.enabled` > 默认关闭**。
关闭时由 AGY 独立起草并保存方案。沿用同任务仍有效的方案时按 `core/WORKFLOW_CONTINUITY.md` 续接。
封装器自动读取项目仓库根目录下受跟踪的 `.ai-anti/plan-snapshot-exclude`，默认无规划快照排除。

- 「本次不用双方案」「本次开启双方案」「本次由 Claude 综合」只覆盖当前一次规划，
  方案落盘或规划中止时即消费。
- 只有用户明确声明「本会话禁用双方案」才跨任务保留，直到「本会话恢复双方案」清除会话覆写，
  恢复遵循全局配置。覆写只存在会话上下文中。长会话压缩后以当次指令或用户重新声明为准。
- **1a**：完成项目入口读取、基线冻结和脏文件确认后，先按 R2 逐字写入同名
  `.request.md`。将已确认纳入任务的必要未跟踪文件固定为一组仓库相对路径，
  以重复的 `--extra-file` 参数在 draft 和 synthesize 两次调用复用。
- **任务目录**：新任务使用 `<scratchpad>/dual-plan/<YYYYMMDD>-<slug>/`，重名追加
  `-2`、`-3` 等。每次调用都选从未存在的 `--out`，保留既有产物和失败证据。
- **规划工作树**（防 2026-11-21 另一会话修改主树受跟踪文件，双方案 draft 运行约 9 分钟后以 `integrity_aborted` 作废）。
  - 1a 后、1b 前在主树外建 `$task_dir/plan-worktree`（`$plan_worktree`），与各 `--out` 平级。先核实 `$task_dir` 归当前用户独占（0700），再按项目只读检出配方创建 detached 工作树。
    无配方时用 `git -C "$repo" worktree add --detach "$plan_worktree" "$frozen_head"`。只用第 1 步冻结的 SHA，不建分支、临时提交，不装依赖或构建。双方案关闭或续接复用既有有效方案时跳过建树。
  - 1a 确认纳入时，为必要未跟踪 `--extra-file` 和受跟踪的 `.ai-anti/plan-snapshot-exclude` 记录 SHA-256，脏目标文件沿用第 1 步指纹。只镜像这两类材料及已确认纳入任务的脏改动。
    相对路径不得含 `..`、`.git` 或穿越软链父目录。复制当前字节和权限，软链按目标重建，删除及重命名旧路径同步移除。
    按已核实的源索引重建成员资格，暂存新增与重命名新路径用 `git add`，忽略规则命中的已索引路径用 `git add -f`。额外文件限常规文件并保持未跟踪。R2 原件留在主树，经 `--request` 传入。
  - 稀疏检出须让受跟踪的排除声明实际落盘，让本任务的脏受跟踪路径可被 Git 识别（纳入稀疏集合或清除 `skip-worktree` 位）。稀疏检出本身不限制 `git archive` 范围。
  - 复制后先将规划树实际副本与第 1 步及 1a 确认的指纹逐项比对，防止确认后、复制前主树改写改变已确认输入。核对后在 `$task_dir/mirror.json` 记录各路径的 SHA-256、类型、权限及索引成员资格（软链哈希取链接目标，删除记缺失）。
    每次调用前按冻结 SHA、`git status` 和镜像逐项核对规划树，任何不一致即停。draft 与 Claude synthesize 共用规划树及同一组 `extra_args`。AGY 起草与综合前先按冻结 SHA 和镜像核对事实。
    按新基线重跑时新建不重名的规划树。第 4 步实施树独立创建。
  - 成功或 `fallback` 落盘后，核实本次工作树登记、冻结 SHA 及无意外文件（含未跟踪与忽略文件），先普通 `git worktree remove`。
    需强制回收时先逐项核对镜像且无额外内容，再用 `git worktree remove --force`。核对或回收失败则保留并报告路径与错误。保留任务证据。
  - 创建或同步失败即停并报告，禁止在主树自动补跑。准备期部分建成的树同样按上述核对回收，无法证实只有本次内容时保留并报告实际路径与 stderr。
    停止类失败保留规划树和实际 `--out` 并报告路径，中断先确认进程已停。1c 等待用户裁决期间保留现场，用户取消时按上述核对回收。
- **1b**：AGY 依 `core/PLANNING.md` 独立起草，完成出稿前初步验证并附自查记录，
  写入任务目录的 `plan-agy.md`。同时后台启动并监控 Claude draft。可选
  `--instructions` 仅传部署规模、规划格式、共享方法要点及脱敏后的客观只读探针事实。
  `--instructions` 中的主树与规划树路径须脱敏为 `<REPO_ROOT>`。
  AGY 草稿和设计倾向须留到综合阶段。执行器在独立起草时剥离 R2 非需求附录，并在只读快照内运行。
  配置触发时采用默认 `--trigger config`，用户当次消息启用时用 `--trigger user-message`，
  两种触发均须校验原配置。

  ```bash
  python3 "$mechanism/adapters/agy/scripts/claude_plan.py" --repo "$plan_worktree" --source-repo "$repo" --stage draft \
    --request "$request" --out "$draft_out" "${extra_args[@]}" "${trigger_args[@]}" < /dev/null
  ```

- **1c**：综合方按**用户当次点名 > 合法配置的 `synthesizer` > `agy`** 决定。核对
  Claude draft 退出码为 0，且 manifest/status 均表明已完成、完整性通过，从实际成功的
  `--out` 取 `plan.md`。由 AGY 综合时，将该稿与 `plan-agy.md` 按 R1 核对。
  由 Claude 综合时，`claude_draft` 指向这份实际成功的稿件，复用 1a 的 `extra_args`；
  当次触发双方案或当次点名 Claude 综合时用 `--trigger user-message`。综合输出核验
  manifest/status、R1 与仓库事实。遇到实质产品方向或用户可见交互分歧，停下请用户裁决。

  ```bash
  python3 "$mechanism/adapters/agy/scripts/claude_plan.py" --repo "$plan_worktree" --source-repo "$repo" --stage synthesize \
    --request "$request" --agy-draft "$task_dir/plan-agy.md" --claude-draft "$claude_draft" \
    --out "$synthesis_out" "${extra_args[@]}" "${trigger_args[@]}" < /dev/null
  ```

- **失败处置**：只有退出码为 1 且 manifest/status 可核验 `fallback_allowed=true` 和
  `integrity_ok=true` 才按 `on_failure` 处理。`stop` 默认停止并保留证据。
  `fallback` 使用 `plan-agy.md` 经落盘前复核后落盘，在送审和汇报注明失败阶段与原因。
  退出码 2、3、130、143、其他非零码或无法核验证据时，停止并保留现场。
- **落盘前复核**：AGY 对拟落盘综合稿或降级稿逐项检查关键设计与代码或探针证据，
  对照需求原文排查遗漏，检查 manifest 的警告，清理失效暂存引用。无法静态成立的主张
  补最小反例或保留为待验证。在综合记录中保留验证证据和自查记录。写入前对照冻结 SHA 与镜像
  核对主树漂移，另记落盘时观察到的主树 HEAD，保留第 1 步冻结记录供第 4 步核对，并按续接规则判定。
  漂移涉及本任务路径时按最新主树修正行号与代码事实，实质范围变化停下请用户确认。
  复核后保存到第 1 步确定的方案路径，随后进入第 2 步。

## 第 2 步：Codex 只读评审

读取 codex-review 技能。按 R3 在发起评审前核对必要材料、路径、规模、配置与输出目录。
每一轮使用仓库外新目录，提示词和完整日志留证。

```bash
python3 "$mechanism/adapters/agy/scripts/codex_review.py" plan \
  --repo "$repo" --plan "$plan" --scale "$scale" --out "$review_out" < /dev/null
```

需要时用 --files 加入仓库内相对文件；总材料至多 2 MiB。--prepare-only 仅准备材料。
独立评审依 R4 先读 request，再列独立清单并读取方案。0 仍须核对 completed=true，
2 表示预检失败，1 表示失败，130/143 表示中断。实际读取 warnings.json，对照
review_raw.jsonl 逐条核对 failed_command。对 request 或方案的读取失败须有后续成功读取证据；
未读到 request 或方案，整轮作废。连接器事件使整轮作废。保留尝试日志与版本警告。
暂态重试由共享入口管理。

## 第 3 步：R6 分诊定稿与 3b 需求核对

逐条列出原意见、具体失败场景、严重度与裁决。按 R6 将意见分为必修、登记、驳回，
记录登记位置或驳回依据。只在架构被整个推翻或出现 blocker 级新事实时启动第二轮，
最多两轮。第二轮后必修仍无法解决即停止并请用户裁决。

第 3b 步逐项向用户列明定稿相对 request 的用户可见差异，包括范围、行为、默认值、
权限和验收口径。逐项确认并写入任务记录。没有差异时明确记录。核对实施授权；
尚未取得授权时，保持待实施。新增需求须按 R2 补入 request 并按共享规则核对轮次。

## 第 4 步：固定 Codex 在隔离工作树实施

核对冻结的主树身份、HEAD、ref、文件指纹与 absent，再创建仓库外的 linked worktree。

```bash
git -C "$repo" worktree add --detach "$candidate" "$frozen_head"
```

把已确认纳入任务的主树脏输入逐个带入候选，包括已有用户新增文件或删除。
在候选树仅暂存这些路径，核对 git diff --cached 后做本地临时基线提交。此提交仅用于
候选树，执行前遵循用户授权及项目提交纪律。无脏输入时沿用冻结 HEAD。
记录候选基线的完整 SHA。真正由任务新增的路径继续标记 absent。
把定稿、request、允许改动的逐文件清单、测试方法和工作树限制写入提示词文件；
主树中的本地产物使用明确绝对路径。提示词规定实施者仅在候选树内改动。

```bash
python3 "$mechanism/adapters/claude-code/scripts/codex_implement.py" \
  --relay agy --workspace "$candidate" --prompt "$implement_prompt" \
  --out "$implement_out" < /dev/null
```

从 agy_relay.implementation 读取显式 provider=codex 与固定 model。检查退出码、
manifest/status、实际候选树状态与 relay=agy。配置缺失、失败或中断即停，保留证据和候选，
请用户裁决。禁止自动重试、换模型或 AGY 自动接管。

## 第 5 步：AGY 独立终审与逐文件落回

AGY 对照候选基线独立读取全部增量。用 git status --short、git diff <候选基线>，
逐个读未跟踪或被忽略的任务文件，核对实际增删与定稿范围。独立运行验证并比较第 3b 步
确认的差异，发现新增用户可见差异先停并请用户确认。Codex 实施的增量由 AGY 终审，
不再交给 Codex 评审。completed 只表示实施协议完成。

落回前完整重跑第 1 步的仓库身份、顶层根、HEAD 和 attached ref/detached 检查，
逐文件比较当前字节 SHA-256；计划新增路径用 os.path.lexists 检查仍为假，包含悬空链接。
同时确认目标路径及祖先仍指向冻结的位置。任何变化或重叠立即停止，保留主树与候选树现场，
不覆盖、不 reset、不 checkout。全数通过且落回已获授权后，逐文件将候选的已验收新增、
修改和删除落回。保留既有用户改动与暂存区状态，复核落回的文件与验证结果。
核对候选没有未带回的用户内容后才回收工作树；回收失败报告路径。

## 第 6 步：汇报

汇报需求及第 3b 步裁决、逐条评审意见与分诊、实际通道和请求模型/深度、修改文件与理由、
验证命令与结果、版本或工具警告、未覆盖项、产物路径，以及落回和工作树处置状态。
双方案规划注明协作方 Claude、请求模型和实际模型（`actual_model`）、请求深度、综合方及
实际调用目录。执行过双方案调用时同时注明规划工作树的处置状态，保留时附路径。
当次消息触发时注明来源。临时禁用或降级时注明原因，降级另注明失败阶段。
对用户跳过项或失败门禁明示证据。不得将安装探针、离线替身或 requested_only 记录当作
真实新会话遵循、真实模型身份或终审通过的证据。

## AGY 后台与沙箱纪律

遵循 core/CLI_DISCIPLINE.md。非交互入口 stdin 接 /dev/null，保留完整磁盘日志，
后台查询实际任务状态、进程及产物增长，直到取得退出码和终态证据。停滞时核对总预算、
空闲预算和重试等待，按共享纪律处理；写入口没有自动重试。确认上一次写进程已经结束，
才可在新的用户裁决下另开实施。遵守 AGY 宿主沙箱与联网授权，不绕过批准；
只读评审禁外部连接器，workspace-write 实施只能在获准的隔离工作树内执行。
