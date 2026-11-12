# 通用子代理

本目录是五个通用角色的人手维护源；magpie 资源库负责分配到所选客户端，客户端原生配置是同步结果。角色绑定现有任务组，具体模型及兜底由路由策略管理。2026-11-10：已从 Codex 生成文件回读五个角色的目标任务组绑定；新会话加载和实际委派尚未验证。

主代理决定是否委派，并提供目标、必要上下文、工作范围、约束和验收条件；负责整合结果及最终验证。并行写入任务必须划分不重叠的文件范围。角色不继续委派。

Explorer、Architect 和 Reviewer 默认只读，Implementer 只修改分配范围。当前这些边界只写在提示词里：Codex 自定义代理继承父会话的沙箱和批准策略，不能在角色文件中设置独立的 `sandbox_mode` 或 `approval_policy`；magpie 生成的四份角色文件也未使用 `features.*` 限制工具。Claude Code 原生支持 `tools`、`disallowedTools` 与 `permissionMode`，但本机尚未部署，不能视为已生效。Grok Build 生成文件没有模型字段，同样只有提示词约束。

## 客户端差异

| 能力 | Codex | Grok Build | Claude Code |
| --- | --- | --- | --- |
| 资源库角色文件 | `~/.codex/agents/<name>.toml` | `~/.grok/agents/<name>.md` | `~/.claude/agents/<name>.md` |
| 角色模型字段 | 写入 `model`，可为 `group/<id>` | 不写入；由客户端自行选择模型，不保证任务组绑定 | 支持 `model`，但 magpie 当前不把资源库 Model 写入 Claude Code 文件 |
| 权限表达 | 继承父会话；可用 `features.*` 关闭部分能力 | 继承客户端工具集 | 可用 frontmatter 限制工具与权限模式 |
| 触发方式 | 明确要求子代理，或项目/技能指令请求 | 由客户端依据角色描述与上下文决定 | 依据 `description` 自动委派，或显式 @-mention |
| 生效时机 | 资源库写入后，新会话启动才读取 | 同左 | 同左 |

当前 magpie CLI 未暴露资源库子代理管理命令；角色由用户在资源库界面创建，CLI 只用于只读盘点及 `magpie library sync` 重放。不要直接编辑 magpie 私有配置文件，也不要把客户端生成文件当作第二份维护源。

## 部署与验证

2026-11-09 核对：magpie 0.1.3373，Codex CLI 0.167.0；`magpie codex subagent_model` 与 `magpie claude subagent` 均为 `default`，没有统一模型覆盖。资源库中四个角色的 `agents` 均为 `codex`、`grok`，`hooks` 条目为 0；本阶段不新增 hooks。

| 项目 | Codex | Grok Build | Claude Code |
| --- | --- | --- | --- |
| 角色同步 | 四份 TOML 已生成 | 四份 Markdown 已生成 | 未发现角色目录或生成文件 |
| 模型绑定 | 五个生成文件已写入对应 `group/task-*`；新会话加载尚未验证 | 生成文件无模型字段，不能确认绑定到任务组 | 未部署 |
| 配置回读 | 名称、描述、职责与交付要点均与源定义一致；只读仍是行为约定 | 名称、描述、职责与交付要点均与源定义一致；只读仍是行为约定 | 不适用 |
| 新会话加载 / 明确委派 | 尚未验证 | 暂缓：关键模型绑定未表达 | 暂缓：尚未部署 |
| 自然触发 | 仅观察，不作验收 | 仅观察，不作验收 | 仅观察，不作验收 |

`default` 是观测结果，不等于已强制覆盖；若 Agents 页面改为固定模型，会覆盖 Codex 角色自身绑定，需作为独立变更确认。Claude Code 需要用户在资源库界面为四个角色勾选 `claude` 并重新同步后，才能验证；若继续保留 Grok Build，则需接受其角色文件不携带任务组模型这一限制。当前不把“资源库中已创建”解释为“三种客户端均已生效”。

配置正确、明确委派成功、自然触发是否合理分别记录。新会话中依次验证角色发现、明确委派及输出；实现角色在隔离的临时工程测试。自然触发只作观察，不据此宣称模型质量或额度优化成功。运行单元测试、只读 `route diff` 与 `audit`，确认角色接入没有改变实际路由。

部署前保存有关角色和覆盖设置的本地恢复依据。失败时仅撤销本次新增角色分配并恢复被修改设置，不恢复整个配置库、不影响无关资源。

本轮对 magpie 配置只做只读核对，没有改动角色分配、客户端设置、全局模型覆盖或路由；资源库自带的备份与四个 `mr-` 角色的当前分配足以界定恢复范围。若后续部署失败，只取消这四个角色的本次客户端勾选并恢复被修改设置，不使用整库恢复，也不影响无关资源。

## 参考依据

- [magpie Library](https://usemagpie.ai/docs/library)：资源库写入各客户端原生文件；模型字段仅写入 Codex；客户端重启后读取。
- [Codex Subagents](https://developers.openai.com/codex/subagents)：自定义代理继承父会话沙箱与批准策略；模型与推理字段可覆盖；触发通常由明确请求或项目/技能指令发起。
- [Claude Code Subagents](https://code.claude.com/docs/en/sub-agents)：支持 `tools`、`disallowedTools`、`permissionMode` 与 `model` frontmatter；可自动委派或显式调用。
- 核对日期：2026-11-09（Asia/Singapore）。
