# 路由调度总表

2026-11-10：v4 目标配置见 `routing.json`；实际状态以 Magpie 为准，使用 `python -m magpie_router route diff` 比较。旧规范 `routing-v3.json` 仅供历史测试，不可用于应用。

业务链采用 `entry-work → task-* → pool-* → 具体模型`。Jev 是 Magpie 原生辅助分类器，任务入口和 Work 直接引用 `typesafe/jev-latest`；该辅助引用不属于业务成员链。Magpie 不允许将含决策模型的组作为另一组的普通成员。详见 `docs/jev-migration.md`。

## 任务入口

| 任务 | 默认资源池 | 额外选择 |
| --- | --- | --- |
| 通用问答 `task-general` | general → reason → fallback | 技术推导、复杂解释与多因素比较优先 reason |
| 架构 `task-architect` | reason → build → fallback | 代码示例、可行性验证和具体落地步骤优先 build |
| 实现 `task-implement` | build → build-reserve → fallback | 常规实现 |
| 强化实现 `task-implement-strong` | build-strong | 仅 Sol:xhigh，不接普通兜底 |
| 评审 `task-review` | reason → build → fallback | 实际代码行为与调用链优先 build |
| 测试 `task-tests` | tests → build → fallback | 集成、端到端及跨组件测试优先 build |
| 视觉 `task-vision` | vision → fallback | 视觉理解 |
| 摘要 `task-compact` | compact → fallback | 压缩、整理 |
| SOTA `task-sota` | sota | 仅手动 |

Work 的意图规则依次覆盖压缩、评审、架构、纯测试、常规实现、视觉、摘要、通用问答及闲聊，图片条件位于意图规则之后。Jev 失败时意图不匹配，确定性条件与默认通用入口仍可用。

**待解决：** Magpie 的意图规则只提升首选项，失败后仍尝试组内其他成员。若将强化任务作为 Work 成员自动选择，强化候选失败后仍可能落入普通链，违反不降级要求。因此暂时移除 Work 的强化意图规则；强化实现入口仍可手动选择。恢复自动强化需要新的运行时能力或重新确定降级边界。

## 资源池和成本

成员顺序、推理强度、上下文与规则文本以 `routing.json` 为准。`pool-general` 面向日常通用任务；`pool-fallback` 面向跨任务故障恢复，两者少量复用模型但用途不同。免费候选的路径证据和复核步骤见 `free-models.md`。

`pool-build` 为 Terra 和 OpenCode Go Flash:max；`pool-build-reserve` 为 WorkBuddy Flash:max。`pool-build-strong` 仅包含 Sol:xhigh，不能降级到普通模型。OpenCode Go 只使用 DeepSeek V4.1 Flash。`pool-sota` 手动使用 Astra 与鱼鱼 Opus，官方按量 DeepSeek 不进入自动路径。

固定订阅与按量成本分别评估。免费、赠费、未知价格及订阅窗口不合并为单一分数；实际成本依据见 `cost-model.md`、`providers.md`。

## 容量与验证边界

普通任务和 Work 公布 1,000,000 tokens，强化入口公布 500,000；池的公布容量不代表每个候选均可处理该长度。用户报告 Magpie 会跳过上下文不足的模型，独立 Long 入口已移除。图片过滤、输出预留、自动路由质量和配额收益尚未独立验证。

Jev 已在合成文本下返回预期分类，但 Work 的完整真实请求分流效果尚未验证；Jev 标称 32k 上下文，不因外围组公布容量而扩大。`docs/routing-blueprint.drawio` 是可编辑总蓝图，路由结构及部署状态变化时同步维护。

## 日常维护

按需观测目录、模型规格和价格，区分实际观测、用户确认和未知；新增或下架模型先形成建议。`route diff` 用 `RETIRED` 标记明确退役但仍存在的旧组，用 `UNKNOWN` 标记未纳入规范的组；两者均不会自动删除。处理退役组前检查路由、分类器和客户端引用。变更前核对实时状态，逐组保存基线、应用、回读并同步 SQLite；不记录密钥、完整会话或原始请求。模型目录不完整时标记 `partial`。
