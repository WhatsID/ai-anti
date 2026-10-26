# 显式问题复核

这是业务分析与独立第二意见之间的文本协议，不绑定 Bug skill、Git 仓库或 App UI。
共享语义见 [core/BUG_REVIEW.md](../core/BUG_REVIEW.md)。Codex 使用
`bug-review` skill，Claude Code 使用 `/bug-review`，两者运行同一 Python 3.9+ stdlib 入口。
先解析安装入口的符号链接定位真实源码，不从业务目录猜路径。

```bash
python3 /resolved/ai-anti/adapters/codex/skills/claude-review/scripts/bug_review.py \
  --bundle /absolute/problem-bundle --out /absolute/new-review-output \
  --config-base /absolute/credential-base --channel qoder --model kimi-k3.1-pro
```

默认通道是既有 `codex_relay.review_channel`（缺省 Claude），**不是** Claude 工作流的
`review.channels`。可覆盖 `--channel claude|qwen|qoder`；Claude 支持既有模型别名，Qoder
规范 alias 为 kimi-k3.1-pro；kimi-k3/kmodel_latest 保留输入兼容，均请求 kmodel_latest，且只接受实际模型 Kimi-K3.1-Pro。CLI 明确准入 1.1.74/1.1.77；1.1.77 + Kimi-K3.1-Pro 已完成真实双阶段验收（详见 [兼容记录](COMPATIBILITY.md)）。Qwen 模型来自现有配置且拒绝 --model。Qwen 的
`--qwen-executor claude|codex`、`--responses-base-url` 与既有评审相同。
`--config-base` 默认为调用 cwd，只用于相对凭证路径；它不授权读取该目录其他材料。
无 Git 目录直接调用即可，不初始化仓库、不读取 HEAD。

bundle 是调用方准备的普通绝对目录，只读 manifest 声明的本地 UTF-8 文本，不接受 URL 或自动下载。
例 `manifest.json`（所有字段必须提供，无未知字段或重复 JSON key）：

```json
{
  "schema_version": 1,
  "task_id": "playback-20261023",
  "revision": 1,
  "problem_file": "problem.txt",
  "analysis_file": "analysis.txt",
  "evidence": [
    {
      "id": "E1",
      "file": "events.txt",
      "source": "device log supplied by caller",
      "locator": "10:01:00–10:01:10; original lines 300–320",
      "kind": "raw_excerpt",
      "coverage": "stop event only; earlier transport context omitted"
    }
  ],
  "limitations": ["Full trace not available"],
  "domain_checks": ["Check timestamp alignment"],
  "environment": "synthetic app v1; 2026-10-23; device firmware unknown"
}
```

problem.txt 保存用户原话与预期，analysis.txt 保存候选解释、证据 id、排除依据、反证、未验证项。
不得把主模型推断伪装成用户原话。task_id 非空，revision 是正整数；environment 非空，未知明确写出。
evidence 可为空；每项的 id/file/source/locator/coverage 为非空字符串，kind 为 raw_excerpt 或 derived。
派生统计/摘要用 derived，不能当原始材料；limitations/domain_checks 是可为空的非空字符串数组。
文件必须各自唯一且角色不重叠；拒绝绝对 file、..、符号链接、FIFO/其他非常规文件、敏感文件名。
每文件/阶段最多2 MiB，每 LF 行最多2000字符；冻结分块最多12 KiB/120行。不截断、不秘密摘要。
大日志由调用方先选摘录并标明遗漏；二进制录屏不能直接送审。

材料全部先冻结。independent 阶段不含分析文件、路径或哈希；review 阶段加入分析和原样独立输出，
以新进程完整读取所有分块。origin 只作来源标签，不是工具参数。记录原文哈希、块哈希和行号；
冻结材料变化、未完整读取、越权工具、权限拒绝、错误模型均失败。工具限制不是 OS 级凭证隔离。
`--prepare-only` 不读凭证、不启动模型，只生成第一阶段提示词和 prepared 状态，第二阶段等待独立结果。
每阶段独立限时：Claude/Qoder默认600秒、Qwen默认1800秒；idle默认900秒，输出默认2/8 MiB。
可通过 --timeout/--idle-timeout/--max-output-bytes 设置，硬上限3600秒/900秒/8 MiB。
可信暂态故障依 [MODEL_RETRY.md](../core/MODEL_RETRY.md) 重试失败阶段最多两次，
30/90 秒退避计入阶段预算；最终失败保留状态/日志，不降级或调用模型修格式。

输出目录必须在 bundle 外且独占新建，父目录须已存在。先读 status.json，再读完整 report.md。
manifest.json 是本次运行清单（区别于 bundle 输入清单），记录任务、轮次、通道身份、限额、来源、
冻结哈希、阶段证据；independent.md 原样保存独立输出，review.raw.txt 原样保存第二阶段 JSON。
校验通过才写 findings.json 与 report.md。completed 仅表示协议校验完成。

结果对象严格含 assessment、findings、coverage（非空字符串）、limitations（字符串数组）。
assessment 枚举 supported/partially_supported/unsupported/insufficient_evidence。
findings 每项严格含 id、claim、assessment、evidence_ids、reason、alternative、next_check；
id 唯一，claim/reason 非空，evidence_ids 为不重复的已声明证据 id 数组，alternative 为非空字符串或 null。
next_check 是 null，或含 needed、purpose、distinguishing_observation 三个非空字符串的对象。
未知字段、无效引用、重复 id、非法 JSON 直接失败。无证据时总体和各发现必须 insufficient_evidence。
supported 仅表示所读材料支持，不能当成生产实证、根因已证实或已修复。
身份保留实际证据边界：Codex Responses 的 actual_model=null/requested_only 不证明后端模型，
调用方核对主模型与评审模型；同模型或未知身份不能称跨模型。

调用方逐条裁决采纳/待验证/驳回并列证据，可保存 adjudication.md。
补查不是自动复审。只有重要新事实或根因实质变化才允许第二轮，保持 task_id、递增 revision：

```bash
python3 /resolved/ai-anti/adapters/codex/skills/claude-review/scripts/bug_review.py \
  --bundle /absolute/revised-bundle --out /absolute/new-second-output \
  --round 2 --previous /absolute/first-completed-output
```

前轮须是 completed 的 problem/round1，任务、manifest/输出/冻结字节哈希须匹配；
独占 round-2.claim.json 限制同一本地前轮再次开分支。prepare-only 不占谱系。
最多两轮，不提供第三轮；此约定不防恶意复制或伪造本地文件。
第二轮仍有分歧或缺材料就输出未知项，不称根因已证实。人工确认修复前停在报告，
获授权后将结论、证据、缺口、原故障验证条件交现有修复方案评审，核对版本与环境。

两种上层接入示例：车机 Bug skill 按自己的授权采集日志，把停止事件摘录标 E1，给出固件未知项，
独立复核可请求故障前传输状态；网站排障 skill 按自己的流程采集性能记录，把 p95 时序摘录标 E1，
把聚合指标标 derived/E2，复核可请求区分前端与依赖延迟的观测。两者只调用上述同一入口，
本能力不执行采集或 next_check，不依赖项目条件分支，不修改这些业务 skill 的已验证模式。
