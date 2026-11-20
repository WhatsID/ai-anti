---
name: codex-review
description: 在 AGY 中委派 Codex 对单份方案做独立只读评审并核对评审证据。
---

# AGY 的 Codex 方案评审

仅处理独立方案评审。需要完整接力时用 agy-relay。此技能只含 plan 入口。

从已安装插件的规则解析机制根。实际读取同源 core/REVIEW_PRINCIPLES.md 的 R2、R3、
R4、R6、R10 与 core/CLI_DISCIPLINE.md。按 R3 确认仓库根、精确提交基线、方案及
sibling request、项目部署规模、可写仓库外产物目录。附加文件使用仓库相对路径。
按 R2 保留逐字需求并核对最新轮次；配置只选 agy_relay.review，共用评审超时预算。

```bash
python3 "$mechanism/adapters/agy/scripts/codex_review.py" plan \
  --repo "$repo" --plan "$plan" --scale "$scale" --out "$review_out" < /dev/null
```

用 --files 追加必要材料；所有材料合计至多 2 MiB。--prepare-only 仅生成 prompt 与
manifest/status，用于检查输入，收到 completed=false 时不得进入裁决。
实际评审为同一 Codex 路线，暂态重试只由共享入口管理。

后台启动时按 CLI_DISCIPLINE 监控进程和产物增长，保留完整日志，遵守 AGY 宿主沙箱。
退出码 2 处理预检问题，1 报失败并保留证据，130/143 报中断；使用新输出目录才能另行发起。
退出码 0 后仍须读 manifest.json、status.json、warnings.json 和 review_out.txt，要求
completed=true。逐条查看 failed_command 及对应 review_raw.jsonl 证据，核实 request 和
方案已读取、顺序符合 R4；读不到任一材料或无法证实恢复读取，整轮作废。被拒的测试或
命令不得当作实测。遇到连接器调用整轮作废。版本警告和 requested_only 模型证据如实汇报。
呈现独立清单与全部评审意见，按 R6 标记建议处置；输出只作为第二意见。
