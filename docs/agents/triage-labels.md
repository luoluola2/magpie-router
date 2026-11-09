# Triage labels

本地工作项使用五个规范角色，标签字符串与名称一致：

- `needs-triage`：范围或优先级尚未确认。
- `needs-info`：缺少复现、证据或决策输入。
- `ready-for-agent`：范围和验收条件足够明确，可由 Agent 执行。
- `ready-for-human`：需要授权、判断或人工操作。
- `wontfix`：记录不处理的原因。

状态写在工作项 Markdown 的 `labels` 字段中，例如 `labels: [ready-for-agent]`。
