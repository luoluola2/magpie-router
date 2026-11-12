---
name: magpie-router
description: 盘点、诊断和维护 magpie 的多供应商路由；用于调整分组、规则或公布上下文，比较供应商与成本，处理失效成员，或审查路由配置。
---

# 维护 magpie 路由

以 magpie 为实时事实来源，以 `config/routing.json` 为唯一 v4 期望路由。先读 `GLOSSARY.md`、`config/routing-overview.md` 和 `AGENTS.md`；比较渠道或费用时再读 `config/providers.md` 与 `config/cost-model.md`。配置执行按 `magpie-setup`、额度用量按 `magpie-quota`。遵守项目授权边界：没有本次明确批准不得写入。

## 工作流程

1. 运行 `magpie-router inspect`、`magpie-router audit` 或 `magpie-router route diff`，按任务回读实时组。解析失败、缺失成员或无法验证的元数据都应停止写入；CLI 组详情本身不能证明网关上下文元数据可用。
2. 仅在相关时按 `magpie-quota` 读取额度，检查价格与模型资料，并记录来源和日期。区分已付订阅、免费路径、按量费用与未知价格；模型能力要由任务证据评价，不能从名称或窗口大小推断。
3. 形成 Change Set：说明完整规范差异、每个受影响组的实时基线与目标、依据、影响和回滚方式。`route apply --yes` 会应用整份规范中的差异，因此写入范围必须限制在本次明确批准且出现在 `route diff` 中的组。
4. 修改 `config/routing.json` 后运行 `magpie-router route diff`。它会同步 SQLite 镜像，并通过只读网关模型元数据核对公布容量；读取失败时停止。重新观测后确认每项差异仍在批准范围内，基线漂移时重新计划，不继续写入。
5. 获得本次明确批准后运行 `magpie-router route apply --yes`。按依赖顺序逐组应用，验证每个组；失败时会尝试恢复失败组并停止，已成功组不会自动回滚。
6. 应用后回读受影响组，再运行 `magpie-router route diff` 与 `magpie-router audit`。检查本地应用日志和 `state/snapshots/` 回滚快照；确认镜像、实际配置和目标一致，并报告失败与未验证项。
7. 路由或领域语义变化时同步更新 `config/routing-overview.md` 与 `GLOSSARY.md`。脱敏证据、回滚快照和工作记录放入 `state/evidence/`、`state/snapshots/`、`state/work/`；不把凭据、原始请求、配额或会话写入仓库。

## 完成标准

- 每个目标成员都对应实时可服务模型或有效子组。
- 完整 Change Set 有实时基线、目标、依据、影响和回滚方式。
- 写入只涉及本次批准且出现在 `route diff` 中的组；应用后回读与目标一致。
- 失败、回滚结果和未验证假设均已报告；未将敏感本机资料写入 Git。

供应商、账号、密钥、模型暴露和 Agent 默认模型需要独立明确授权。真实模型探测会消耗额度或产生费用，不能当作只读验证。
