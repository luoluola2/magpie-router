# 强化实现接受规则级降级

Magpie 的路由规则只把匹配成员提升为首选，命中后组内其余成员仍是候选；没有规则级独占或禁止父组回退的选项（0.1.3511、0.1.3716 只读核对）。因此「自动命中强化」与「强化任务绝不降级」不可兼得。2026-11-11 用户明确选择前者：把 `group/task-implement-strong` 加入 `group/entry-work` 成员并恢复强化意图规则，强化规则用 `use=task-implement-strong,task-implement` 声明显式次级候选，Sol:xhigh 失败时回退普通实现链，此后仍可能继续回退其他任务组。强化任务组自身仍只引用 `group/pool-build-strong`，不接普通兜底池。

代价是强化任务不再有全链路能力保证，`GLOSSARY.md` 的 Strict Capability Floor 不再作为默认约束；收益是不再需要用户手动切换入口，且能在 Jev 低置信时用 `when=message contains 强化实现` 作确定性覆盖。若上游提供规则级独占，应重新评估本决定，而不是静默改变降级边界。
