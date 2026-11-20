本规则仅适用于 AGY 作为主模型宿主的会话。

先从已安装插件解析同源机制根，再显式读取 core/WORKFLOW_ENTRY.md。不要从业务仓库工作目录推测机制位置。

```python
from pathlib import Path

plugin = Path.home() / ".gemini" / "config" / "plugins" / "ai-anti"
root = plugin.resolve(strict=True).parents[2]
entry = root / "core" / "WORKFLOW_ENTRY.md"
```

实际读取 entry 正文，按其模式优先级、项目覆写和授权分流。读取失败时报告具体路径；
同任务续接时实际读取同源 core/WORKFLOW_CONTINUITY.md。

命中完整接力时加载 agy-relay；用户只请求独立方案评审时加载 codex-review。
禁止使用 Codex 宿主的 codex-relay 或 claude-review 技能编排 AGY 主导任务。
作为受委派的评审或实施者时，遵守委派边界，不再发起接力。
