# magpie-router

让 Agent 以可观察、可审批、可回滚的方式维护 [magpie](https://usemagpie.ai) 路由策略。

`magpie` 是供应商、订阅、密钥和运行状态的事实来源，并执行真实请求。本项目负责只读盘点、策略检查、差异比较、受控应用和验证，不实现第二个模型网关。

## 安装

要求 Python 3.10+ 和已运行的 `magpie`。本项目使用标准库作为运行时依赖；在仓库根目录安装可编辑包：

```sh
python -m pip install -e .
```

安装后可从任意工作目录使用 `magpie-router`。`python -m magpie_router` 也可用于本地开发。

magpie CLI 依次从 `MAGPIE_CLI`、PATH 和应用标准安装位置查找。上下文元数据默认从 `http://127.0.0.1:3425/v1/models` 读取，可通过 `MAGPIE_MODELS_URL` 覆盖；若接口需要认证，本实现会明确失败，不读取密钥文件或自动创建凭据。

## 能力与使用

```sh
magpie-router inspect
magpie-router audit
magpie-router db sync
magpie-router route diff
magpie-router route apply --yes
```

`config/routing.json` 是唯一的 v4 期望路由。`route diff` 同步本地镜像并显示完整规范与实时配置的差异；`route apply --yes` 按依赖顺序应用这份规范中的差异。应用前必须重新观测，并确认差异中的每个写入组都在本次明确批准范围内。应用会逐组记录结果，失败组会尝试恢复基线并停止后续操作；此前成功的组不会自动回滚。

四层路由结构和当前策略见 `config/routing-overview.md`。`context` 是客户端可见容量，不保证每个候选都能处理该长度。策略不代表已部署状态，实际配置始终以 magpie 为准。

## 本地状态与文档

SQLite 镜像默认位于 `state/db/magpie-router.db`，可用 `MAGPIE_ROUTER_DB` 覆盖。回滚快照、脱敏证据和工作记录位于 `state/snapshots/`、`state/evidence/`、`state/work/`；这些目录不进入 Git。

数据库结构见 `docs/database.md`，控制流程与模块职责见 `docs/architecture.md`，领域术语见 `GLOSSARY.md`。Agent 的路由工作流程见 `skills/magpie-router/SKILL.md`，项目标准与授权边界见 `AGENTS.md`。

## 验证

```sh
python -m unittest discover -s tests -v
magpie-router --help
```

不要提交凭据、magpie 备份、完整会话、原始请求、个人使用记录或本机绝对路径。
