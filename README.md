# magpie-router

让 Agent 以可观察、可审批、可回滚的方式长期维护 [magpie](https://usemagpie.ai) 的路由策略。

本项目不是第二个模型网关。`magpie` 持有供应商、订阅、密钥和运行状态，并执行真实请求；本项目负责盘点、诊断、变更计划与验证。

## 当前能力

- `inspect`：只读盘点供应商、Agent、路由组和可用模型。
- `audit`：检查组内未暴露的成员、没有可用成员的组，以及供应商重复暴露等问题。
- `plan set-order`：根据实时组状态生成单个路由组的顺序变更计划，并附回滚数据。
- `apply`：再次确认实时状态与计划基线一致后，应用一个路由组变更并验证结果。

默认只读。只有 `apply --yes` 会修改 `magpie`，且当前只允许变更新计划中明确声明的一个路由组；供应商、账号、密钥、网关密钥和 Agent 模型均不在自动变更范围内。

## 使用

要求 Python 3.10+ 和已运行的 `magpie`。CLI 会依次使用 `MAGPIE_CLI`、PATH 中的 `magpie`，以及应用的标准安装位置。

```sh
python -m magpie_router inspect
python -m magpie_router audit
python -m magpie_router plan set-order group/long grok/grok-4.7@500k --output .local/long.plan.json
python -m magpie_router apply .local/long.plan.json --yes
```

`plan` 和 `apply` 在成员未暴露或路由组状态已变化时拒绝继续，不会顺带暴露模型或覆盖其他改动。`apply` 成功后把实际结果和回滚计划写入 `.local/`。

## 长期运行方式

日常由 Agent 使用项目技能完成 `观测 → 评估 → 提案 → 审批 → 应用 → 验证`。每次决策应来自新的状态与用量证据，不能把某次额度读数、延迟或模型排名永久写死。

调度器、跨多次运行的证据库和自动优化器尚未实现。只有在单次人工审批闭环稳定后，才逐步增加这些能力；自动化不放宽供应商、账号和密钥的审批边界。

## 文档

- `docs/architecture.md`：控制闭环、模块职责与实施阶段。
- `GLOSSARY.md`：项目统一术语。
- `skills/magpie-router/SKILL.md`：Agent 的工作流程。
- `docs/agents/`：本地 issue、领域文档和 triage 约定。

## 验证

```sh
python -m unittest discover -s tests -v
python -m magpie_router --help
```

运行中的原始观测和计划写入 `.local/`，不进入 Git；不要提交密钥、备份、完整会话、原始请求或个人使用记录。
