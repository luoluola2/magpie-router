---
name: magpie-router
description: 盘点、诊断并长期维护 magpie 的多供应商模型路由。用户要求优化路由组、比较不同供应商路径、处理失效成员、制定路由变更或审查 magpie-router 项目时使用；即使用户只说“调整路由”或“看看模型怎么走”，也应先用本流程建立证据。
---

# 维护 magpie 路由

把 magpie 当作事实来源，把每次优化当作有基线的受控变更。先读仓库的 `GLOSSARY.md`、`policies/README.md`、`policies/cost-model.md`、`policies/routing-overview.md` 和 `policies/providers.md`。 路由编排以 `policies/routing.json` 为准。

## 工作流程

1. 在仓库根目录运行 `python -m magpie_router inspect`，确认 gateway、供应商、Agent、组和模型均可读取。
2. 运行 `python -m magpie_router audit`，逐一核对报告与实时组详情；解析失败时停止变更。
3. 只在与任务相关时读取用量、配额或模型价格，并记录采集时间。将 Workload、能力需求和跨时间 Evidence 与当前问题关联，避免按一次读数排名模型；评估 WorkBuddy 时逐模型区分免费与赠费，评估 OpenCode Go 时区分两个密钥。
4. 给出建议及不修改的备选方案，说明候选顺序、收益、风险与仍需验证的假设。
5. 用 `plan set-order` 生成单组 Change Set。计划只接受实时状态中已暴露的成员，不顺带扩大供应商或模型权限。
6. 向用户展示计划文件中的当前状态、目标状态、原因和回滚方式，取得本次应用批准。
7. 运行 `python -m magpie_router apply <计划> --yes`，随后检查命令报告的实际状态。状态漂移或验证失败时报告实际结果，不继续尝试其他写操作。
8. 把必要的脱敏结论写入 `.local/`；仓库只更新长期 Policy、领域术语或可复用流程。

## 完成标准

- 每个建议成员都对应实时可服务模型或有效子组。
- 每个受影响组都有当前状态、目标状态和反向计划。
- 写入已获得明确批准，且应用后读取结果与计划一致。
- 未将配额、余额、密钥、会话内容或本机路径写入仓库。

需要新增供应商、暴露模型、修改账户或改变 Agent 默认模型时，将其作为独立任务请求授权，不用路由计划代替。
