---
name: codex-review
description: 在 AGY 中按接力配置对单份方案发起独立只读评审并核对证据。
---

# AGY 的独立方案评审

仅处理独立方案评审。需要完整接力时用 agy-relay。此技能只含 plan 入口。

从已安装插件的规则解析机制根。实际读取同源 core/REVIEW_PRINCIPLES.md 的 R2、R3、
R4、R6、R10 与 core/CLI_DISCIPLINE.md。按 R3 确认仓库根、精确提交基线、方案及
sibling request、项目部署规模、可写仓库外产物目录。附加文件使用仓库相对路径。
按 R2 保留逐字需求并核对最新轮次；配置只选 agy_relay.review，共用评审超时预算。
按 R3 根据方案改动清单判定风险档，将 `risk_tier` 设为 `standard` 或 `high_risk`。

```bash
python3 "$mechanism/adapters/agy/scripts/codex_review.py" plan \
  --repo "$repo" --plan "$plan" --scale "$scale" --out "$review_out" \
  --risk-tier "$risk_tier" < /dev/null
```

`agy_relay.review.provider=codex` 或省略 provider 时使用单阶段只读 Codex CLI。
用 --files 追加必要材料；所有材料合计至多 2 MiB。

`provider=official_api` 按配置的 `service` 和 `model` 委托 `review.py`，依次生成独立清单
和方案评审。通过 MCP 只读材料注册表提供材料，要求非空的 `--files`，可附 `--context`。
复用的材料包包含全仓的 `tracked-paths.txt` 和 `git-status.txt`（含未跟踪文件名）。
送审前按实际接收方逐项核对这些 Git 元数据、方案、需求与所选文件的外发授权。
--prepare-only 仅生成准备产物，用于检查输入，收到 completed=false 时不得进入裁决。
暂态重试只由共享入口管理。

后台启动时按 CLI_DISCIPLINE 监控进程和产物增长，保留完整日志，遵守 AGY 宿主沙箱。
退出码 2 处理预检问题，1 报失败并保留证据，130/143 报中断；使用新输出目录才能另行发起。
退出码 0 后仍须读 manifest.json 和 status.json，要求 completed=true。原生 Codex 路由还须读
warnings.json 和 review_out.txt。逐条查看 failed_command 及对应 review_raw.jsonl 证据，核实 request 和
方案已读取、顺序符合 R4；读不到任一材料或无法证实恢复读取，整轮作废。被拒的测试或
命令不得当作实测。遇到连接器调用整轮作废。版本警告和 requested_only 模型证据如实汇报。
原生路由按 `effective_risk_tier` 和 `requested_effort` 汇报风险档与实际传入的思考深度（`null` 为「CLI 默认」）。
官方路由读 report.md 与两阶段日志，核对必要材料完整读取、`relay=agy`、实际服务、
请求模型、`actual_model=null` 与 `model_evidence=requested_only`。官方路由的 Responses
思考深度由模型元数据决定。
呈现独立清单与全部评审意见，按 R6 标记建议处置；输出只作为第二意见。
