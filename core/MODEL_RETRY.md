# 模型故障的有限重试

本文件是评审与实施重试行为的唯一权威；外部 CLI 调用纪律仍见
[CLI_DISCIPLINE.md](CLI_DISCIPLINE.md)，评审权限与裁决仍见
[REVIEW_PRINCIPLES.md](REVIEW_PRINCIPLES.md)。
2026-10-26：真实 Qoder 1.1.77 在读取材料后返回 synthetic assistant 与
`error_during_execution/is_error=true/error_code=10500`，原入口一次失败即停；
用户另报告实施 `Selected model is at capacity. Please try a different model`，手动重试恢复。
这些证据支持有限恢复，不支持将协议错误都当瞬态故障。

## 次数、等待、预算

每个逻辑模型阶段最多 3 attempt，首次失败后等 30 秒，再失败后等 90 秒；
成功即停。调用方不得再次包重试以叠乘。formal plan 的 inventory/review、bug 的
independent/review 独立计数，仅重跑失败阶段。第二轮谱系只在入口认领一次，失败退出后
不可自动新建输出目录重发。`--max-retries 0|1|2` 默认 2，明确不重试优先。
单次进程/同次原生逻辑派发内累计；新代理不重置。人工明确新运行可有新预算，不建立
跨目录、跨进程持久账本。

阶段 timeout 包含所有 attempt 与退避，每次执行仅使用剩余预算，idle 不超过剩余时间；
预算不足不启动新请求。等待可取消。每 attempt 输出上限沿用通道，最多三份；取消、
最终失败、预算不足也写 attempts 证据。原生代理无法强制统一进程 deadline，须披露。
可控 SDK retry 保持 0；CLI 自带且不可关闭的重试不在本计数内，须披露边界。

## 可信分类与门禁

仅合法前缀之后的可信结构化模型失败/宿主明确工具错误可触发恢复：已知 10500、
server_error/internal_server_error/overloaded_error/temporary_capacity、明确短期
rate_limit_error，已知结构化 HTTP 500/502/503/504/529，以及上述精确 capacity 原文。
不执行错误内的换模型建议。配额/credits/余额耗尽优先于 429、10500、capacity；鉴权、
权限/审批拒绝、无效配置/模型/参数、上下文超限、版本不支持、取消、进程停止失败、
输出上限、timeout/idle timeout、材料/协议/工具验证失败和未知错误均不自动重试。
未知 429 不猜。不得扫描 assistant 正文、tool_result 或材料关键词判断失败。
Qoder synthetic 仅可作为匹配失败 envelope，绝不是有效模型。非法 init/工具/材料或
畸形流不能借尾部错误恢复；成功仍须完整材料读取及原有身份门禁。

Qoder 合法失败 envelope 中的通用 `error_code=500` 若没有可信 typed transient、
10500、精确 capacity 或结构化 HTTP status 来源，归 `ambiguous_model_error`，不重试；
不改变其他通道分类。永久/配额与协议门禁仍优先。其他裸数字码的来源收紧另登记待办。
只在此合法模糊 500 后收集一次独立子进程诊断：固定两主机 DNS/TCP443，最多10秒且
不超过阶段剩余预算，输出64KiB，无凭证/HTTP，无 SDK/子进程重试。
原始 CLI raw 不变；attempt 保存原 envelope、退出码、原分类和诊断 sidecar/hash。
诊断失败或预算耗尽不覆盖原错误，网络观察不转换成可信上游故障。
宿主权限及显式完整新运行边界见 [CLI_DISCIPLINE.md](CLI_DISCIPLINE.md#qoder-宿主联网权限)。

固定原模型、通道、执行器、凭证环境和 prompt/材料。重试前验证输入快照及 registry；
变化即停止。通用 execute、preflight、git 和任意写命令不隐式重试。readonly 直接命令
必须显式指定协议和可核验输入路径；每 attempt raw 独立保存，成功正文独立提取。
Codex 需要完整生命周期、唯一成功 terminal、exit0，最后工具活动之后唯一非空
agent_message；未知/错误事件、多候选、空结果、终态后内容失败。Qwen JSON 按
is_error/result/permission_denials 判读，保留 raw 与权限证据供 R10，不能将 JSONL 当报告。
日志保留每次分类、时间、等待、hash；canonical 保存最终尝试 raw（成功必须通过全部原守卫），失败保留 failed 状态且不伪造报告。

## 原生评审与部分实施的恢复

主模型记录同次派发累计次数，包括 spawn 容量失败。优先续同一 agent/会话；评审仍按
R9 空上下文与补偿约束，不带入主模型结论或上次倾向。实施恢复前必须确认旧调用停止、
无遗留写进程且单写者；核对 repo/root、branch、HEAD、授权 scope 及 tracked/untracked
实际 diff，保留部分改动、测试结果和无关改动。检查范围外写入及不明外部副作用。
记录已完成/待完成，继续当前状态，不 reset，不重跑非幂等操作。无法证明安全停止，
并发变化、未知副作用或 scope 不清即停止报告，不以重试扩大授权。
无法恢复同 agent 时，只有旧 agent 已停止且现场可审计，才可同模型替代并继承计数。
用户固定模型时不得自动 fallback 或主模型接管；实际模型选择与运行证据仍必需，
完成后仍独立验收。测试失败/方案卡点走原修订逻辑，不当 API retry。
既有降级仅在本档预算耗尽或不可恢复后按既有授权处理，本文件不新增换模型授权。

主会话自身若因容量错误停止，仓库代码和已停止的主模型无法主动自救；需要宿主/用户恢复主会话。
本策略管理仍在运行的编排者所派发的评审/实施调用，不宣称能重启宿主会话。
