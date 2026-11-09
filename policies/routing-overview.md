# 路由调度总表

本表记录长期策略，`routing.json` 是期望配置，magpie 是运行事实来源；运行 `route diff` 核对两者。子代理职责见 `subagents/README.md`，渠道和价格依据分别见 `providers.md`、`cost-model.md`。

2026-11-09 已完成各任务入口的分层迁移，并通过 CLI 回读确认成员；上下文容量以实时元数据为准，差异通过 `route diff` 报告。

## 四层结构

`Work → 任务入口 → 资源池 → 具体模型`。配置引用仅跨相邻层，图片、长度和推理要求由 magpie 处理；复用模型路径不会产生新额度。Classify 是 Work 的辅助分类入口；SOTA 仅供手动选择，自动路径不通向它。

显示名称分别采用 `入口 · …`、`任务 · …`、`资源池 · …`。Fallback 是第三层资源池；Long 和 Vision 各有独立的任务入口与资源池。组 ID 保持稳定，供 Agent 和路由引用。

| 入口 | 默认资源池顺序 | 额外规则 |
| --- | --- | --- |
| Architect | reason → long → fallback | 400k 起优先 long |
| Implement | build → build-long → build-credit → fallback | 400k 起优先 build-long |
| Review | reason → long → fallback | 400k 起优先 long |
| Tests | tests → long → fallback | 无 |
| Vision | vision → long → fallback | 400k 起优先 long |
| Light | free → economy → long → fallback | 200k 起优先 economy |
| Compact | compact → long → fallback | 无 |
| Long | long → fallback | 无 |
| Classify | classify → fallback | 无 |
| SOTA | sota | 手动选择，不接普通 Fallback |

Work 的顺序是压缩、评审、架构、纯测试、实现、视觉理解、图片、500k 长请求，默认 Light。意图分类失败时，图片和长度规则仍可匹配；截图实现仍属于 Implement。

## 资源池与成本

| 资源池 | 成员及调度 | 成本和用途 |
| --- | --- | --- |
| reason | Sol、Kimi、Grok；smart / auto | 架构和评审的订阅候选 |
| build | Terra、Sol、Kimi、Go Flash:max；smart / auto | 实现主力；Flash:max 是能力目标，其他成员尚无对照证明 |
| build-long | Kimi、Go Flash:max；smart / auto | 长实现优先 |
| build-credit | WorkBuddy Flash:max；order / turn | 积分和赠费后备，不能认定免费 |
| economy | Qwen、GLM、Haiku、Luna，均 low；smart / turn | 已购订阅轻任务 |
| tests | Go Flash:low、GLM:high、Haiku:low、Qwen:low；smart / turn | 常规测试 |
| compact | WorkBuddy Flash、Go Flash、Qwen、Haiku，均 low；order / turn | 压缩 |
| classify | WorkBuddy Flash:none、Haiku:low、Luna:low；order / turn | 分类 |
| vision | Terra、Kimi、Qwen；smart / auto | 声明支持视觉的订阅路径 |
| long | Kimi、Qwen、Go Flash；smart / auto | 长请求 |
| free | Hy4 和四个 OpenRouter 免费模型；order / turn | 轻任务免费优先，具体 ID 见规范 |
| fallback | free 的成员、Qwen、Luna、Grok；order / turn | 跨供应商恢复，允许能力降级 |
| sota | Astra → 鱼鱼 Opus；order / turn | 手动；前者消耗 Plus 窗口，后者产生按量现金成本 |

`smart` 以 magpie 的订阅窗口规则调度，并不证明候选质量等价。`auto` 的缓存收益及窗口利用收益尚未实测。订阅模型共享对应账户窗口；OpenCode Go 两个密钥各有资源窗口。WorkBuddy 的赠费和模型价格按实际路径确认，不以 Free plan 推断全部免费。

## 上下文与验证边界

Work 和八个普通任务入口公布 1M，Classify 为 256k、SOTA 为 272k。资源池的公布容量见 `routing.json`，它是入口提示，不保证每个成员均可处理同等长度。400k、200k 是候选偏好切换阈值。

用户已实测：会话超过某模型上下文时，magpie 跳过该模型并尝试后续可容纳的候选。本项目没有独立验证图片过滤、输出预留和粘性行为，也没有实测模型质量或额度优化；维护时不要把上述事项写成已验证结论。

2026-11-09 本机 `model price` 将 Hy4 和四条指定 OpenRouter 路径的输入、输出及缓存价格显示为零；供应商限制和价格可能改变。模型能力参数以实际路径的 `magpie model spec` 为准，价格与套餐变更按 `cost-model.md` 核查。

## 维护

调整前核对当前配置、能力及成本依据；按依赖顺序逐组应用并回读，保留 SQLite 应用记录及 `.local/` 快照。只记录可复核的策略和证据，不提交密钥、完整会话、原始请求或个人用量。模型暴露、供应商、订阅和 Agent 默认模型须单独授权。
