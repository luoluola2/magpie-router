# 路由调度总表

2026-11-11：v4 目标配置见 `routing.json`；实际状态以 Magpie 为准，使用 `python -m magpie_router route diff` 比较。旧规范 `routing-v3.json` 仅供历史测试，不可用于应用。

业务链采用 `entry-work → task-* → pool-* → 具体模型`。Jev 是 Magpie 原生辅助分类器，任务入口和 Work 直接引用 `typesafe/jev-latest`；该辅助引用不属于业务成员链。Magpie 不允许将含决策模型的组作为另一组的普通成员。详见 `docs/jev-migration.md`。

## 任务入口

| 任务 | 默认资源池 | 额外选择 |
| --- | --- | --- |
| 通用问答 `task-general` | general → reason → fallback | 技术推导、复杂解释与多因素比较优先 reason |
| 架构 `task-architect` | reason → build → fallback | 代码示例、可行性验证和具体落地步骤优先 build |
| 实现 `task-implement` | build → fallback | 常规实现 |
| 强化实现 `task-implement-strong` | build-strong | 仅 Sol:xhigh；自身不接普通兜底，Work 命中后允许降级到 `task-implement` |
| 评审 `task-review` | reason → build → fallback | 实际代码行为与调用链优先 build |
| 测试 `task-tests` | tests → build → fallback | 集成、端到端及跨组件测试优先 build |
| 视觉 `task-vision` | vision → fallback | 视觉理解 |
| 摘要 `task-compact` | compact → fallback | 压缩、整理 |
| SOTA `task-sota` | sota | 仅手动 |

Work 的 12 条规则依次覆盖压缩、显式强化文字、强化意图、评审、架构、纯测试、常规实现、视觉、摘要、通用问答、闲聊与图片。Jev 失败时意图不匹配，确定性条件与默认通用入口仍可用。

**降级边界（2026-11-11 决定）：** 用户接受「自动强化 + 允许降级」，`task-implement-strong` 已加入 Work 成员。Magpie 的规则只提升首选项，命中后组内其他成员仍是候选；强化规则用 `use=task-implement-strong,task-implement` 显式声明次级候选，Sol:xhigh 失败时回退普通实现链，此后还可能继续回退其他任务组。因此不再要求「强化任务绝不降级」，也不宣称首选独占；取舍见 `docs/adr/0002-strong-routing-degradation-boundary.md`。

**一二层规则（Magpie 0.1.3521 起）：**

| 机制 | 用途 | 落点 |
| --- | --- | --- |
| `intent-sure=0.5` | Work 含强化意图，用高于默认 0.4 的阈值，减少普通实现被低置信判成强化实现 | 仅 Work |
| `when=message contains …` | 无需分类器的确定性覆盖；仅关键词匹配，不理解为语义 | Work 的显式强化；四个任务组的默认池前移 |
| `use=a,b` | 一条规则内声明有序候选，b 在 a 失败后先于后续规则 | Work 及各任务组的次级目标 |
| `use-effort=low` | 单条规则降低推理级别 | Work 闲聊规则 |

任务组的两条规则顺序固定：`when=` 表达式在前、意图规则在后；意图规则仍带 `classifier=typesafe/jev-latest`。这四个任务组不含强化候选，`intent-sure=0.3` 仍用于降低漏匹配（上游 #5101 记录 0.4 下 16.5% 被拒）。不使用未验证的 `tokens=`、时间/目录/代理限定和 `pause=`。`when="effort >= …"` 与既有 `effort=` 条件在 CLI 回读中同形，规范不采用。两个阈值与真实请求分流效果尚未验证。

## 资源池和成本

成员顺序、推理强度、上下文与规则文本以 `routing.json` 为准。`pool-general` 面向日常通用任务；`pool-fallback` 面向跨任务故障恢复，两者少量复用模型但用途不同。免费候选的路径证据和复核步骤见 `free-models.md`。

`pool-build` 依次包含 Terra、WorkBuddy Flash:max 和 OpenCode Go Flash:max，按 smart 调度；原 `pool-build-reserve` 已并入本池并退役。`pool-build-strong` 仅包含 Sol:xhigh，不能降级到普通模型。OpenCode Go 只使用 DeepSeek V4.1 Flash。`pool-sota` 手动使用 Astra 与鱼鱼 Opus，官方按量 DeepSeek 不进入自动路径。

固定订阅与按量成本分别评估。免费、赠费、未知价格及订阅窗口不合并为单一分数；实际成本依据见 `cost-model.md`、`providers.md`。

## 容量与验证边界

普通任务和 Work 公布 1,000,000 tokens，强化入口公布 500,000；池的公布容量不代表每个候选均可处理该长度。用户报告 Magpie 会跳过上下文不足的模型，独立 Long 入口已移除。图片过滤、输出预留、自动路由质量和配额收益尚未独立验证。

Jev 已在合成文本下返回预期分类，但 Work 的完整真实请求分流效果尚未验证；Jev 标称 32k 上下文，不因外围组公布容量而扩大。`docs/routing-blueprint.drawio` 是可编辑总蓝图，路由结构及部署状态变化时同步维护。

## 日常维护

按需观测目录、模型规格和价格，区分实际观测、用户确认和未知；新增或下架模型先形成建议。`route diff` 用 `RETIRED` 标记明确退役但仍存在的旧组，用 `UNKNOWN` 标记未纳入规范的组；两者均不会自动删除。处理退役组前检查路由、分类器和客户端引用。变更前核对实时状态，逐组保存基线、应用、回读并同步 SQLite；不记录密钥、完整会话或原始请求。模型目录不完整时标记 `partial`。
