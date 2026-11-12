# SQLite 配置镜像

默认路径为 `state/db/magpie-router.db`，可用 `MAGPIE_ROUTER_DB` 覆盖；数据库、WAL 和 SHM 不进入 Git。运行 `magpie-router db sync` 更新实时观测并追加期望规范。

| 表 | 当前内容 |
| --- | --- |
| `groups` | 组 ID、显示名、层级、调度、粘性、分类器、公布上下文和意图置信度 |
| `members` | 有序成员引用、当时可用标记及供应商描述 |
| `rules` | 有序规则目标、条件 JSON、`when` 表达式 JSON 和规则推理强度 |
| `observations` | 每次 CLI 观测原文及目录完整性 |
| `desired_routes` | 按时间追加的完整规范 JSON |
| `apply_log` | 每组应用前后的 JSON、成功标记和错误 |

`groups.layer` 仅在规范包含对应组 ID 时填入 `entry`、`task` 或 `pool`；其他实时组为 `unknown`。该列不表示实时请求一定按期望拓扑工作。`desired_routes` 保留目标，`apply_log` 保留执行结果，不能只凭目标推断已经部署。

同步替换当前组、成员和规则，保留历史观测和应用日志。旧库在初始化时追加 `intent_sure`、`expressions_json` 和 `use_effort` 列，已有行使用空值，不删除历史记录。项目只保存配置与回滚证据，不保存凭据、配额、原始请求或会话内容。
