"""按依赖应用路由配置；每组写入前保存基线，失败恢复后停止。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .desired import (
    DesiredGroup, DesiredSpec, application_order, check_exposed_models,
    diff_lines, load_spec, same_configuration,
)
from .magpie import MagpieError, checked_magpie
from .routing import GROUP_ID, RouteGroup, Rule, group_payload, group_references, read_group
from .store import begin_apply, finish_apply, sync_observation


def rule_arguments(rule: Rule, classifier: str = "") -> list[str]:
    """将已验证规则转换为参数数组；多目标沿用 magpie 的 use=a,b。"""
    arguments = [f"use={rule.target}"]
    for key, value in rule.conditions:
        if key in {"compact", "images"}:
            arguments.append(key)
        elif key in {"tokens", "effort", "intent"}:
            arguments.append(f"{key}={value}")
        else:
            raise MagpieError(f"无法编译条件：{key}")
    arguments.extend(f"when={expression}" for expression in rule.expressions)
    if rule.use_effort:
        arguments.append(f"use-effort={rule.use_effort}")
    if "intent" in dict(rule.conditions):
        if not classifier:
            raise MagpieError(f"{rule.target} 的意图规则缺少分类器。")
        arguments.append(f"classifier={classifier}")
    if not rule.conditions and not rule.expressions:
        raise MagpieError("规则没有可应用的条件。")
    return arguments


def _short_id(reference: str) -> str:
    """返回校验后的组 id，避免把任意文本用作组引用。"""
    if not reference.startswith("group/") or not GROUP_ID.fullmatch(reference[6:]):
        raise MagpieError(f"无效路由组引用：{reference}")
    return reference[6:]


def _remove_rules(reference: str, count: int) -> None:
    """从末尾删除规则，避免编号前移；删除最后一条意图会清掉分类器。"""
    for position in range(count, 0, -1):
        checked_magpie(["group", "rule", "rm", _short_id(reference), str(position)])


def _mutate(desired: DesiredGroup, current: RouteGroup) -> None:
    """先移除旧规则再替换候选，正确设置路由策略和分类器。"""
    short = _short_id(desired.id)
    replace_rules = current.rules != desired.rules
    if replace_rules:
        _remove_rules(desired.id, len(current.rules))
    settings = []
    if desired.name and current.name != desired.name:
        settings.append("name=" + desired.name)
    if tuple(member.id for member in current.members) != desired.members:
        settings.append("models=" + ",".join(desired.members))
    if current.routing != desired.routing:
        settings.append("routing=" + desired.routing)
    if current.stays != desired.stays:
        settings.append("stays=" + desired.stays)
    if desired.context is not None and current.context != desired.context:
        settings.append(f"context={desired.context}")
    if current.intent_sure != desired.intent_sure:
        settings.append("intent-sure=" + desired.intent_sure)
    if settings:
        checked_magpie(["group", "set", short, *settings])
    if replace_rules:
        for rule in desired.rules:
            checked_magpie(["group", "rule", "add", short, *rule_arguments(rule, desired.classifier)])
    if current.classifier != desired.classifier or replace_rules:
        checked_magpie(["group", "set", short, "classifier=" + desired.classifier])


def restore_group(before: RouteGroup) -> None:
    """先清除当前剩余规则，再恢复基线；调用方回读验证有效配置。"""
    short = _short_id(f"group/{before.id}")
    current = read_group(f"group/{before.id}")
    _remove_rules(f"group/{before.id}", len(current.rules))
    settings = ["models=" + ",".join(member.id for member in before.members), "name=" + before.name]
    if before.routing:
        settings.append("routing=" + before.routing)
    if before.stays:
        settings.append("stays=" + before.stays)
    if before.context is not None:
        settings.append(f"context={before.context}")
    settings.append("intent-sure=" + before.intent_sure)
    checked_magpie(["group", "set", short, *settings])
    for rule in before.rules:
        checked_magpie(["group", "rule", "add", short, *rule_arguments(rule, before.classifier)])
    checked_magpie(["group", "set", short, "classifier=" + before.classifier])


def _payload(desired: DesiredGroup) -> dict:
    """生成不含运行状态的目标配置，持久化为应用记录。"""
    return {
        "id": desired.id, "routing": desired.routing, "stays": desired.stays,
        "members": list(desired.members), "classifier": desired.classifier,
        "context": desired.context, "intent_sure": desired.intent_sure,
        "rules": [{
            "target": rule.target, "conditions": dict(rule.conditions),
            "expressions": list(rule.expressions), "use_effort": rule.use_effort,
        } for rule in desired.rules],
    }


def apply_group(
    desired: DesiredGroup, db_path: Path | None = None, *,
    create: bool = False, baseline: RouteGroup | None = None,
) -> str:
    """应用单组；新组失败时删除，现有组失败时恢复，日志在写操作前提交。"""
    short = _short_id(desired.id)
    if create:
        if desired.id in group_references(checked_magpie(["groups"])):
            raise MagpieError(f"{desired.id} 已被创建，拒绝覆盖。")
        current = None
    else:
        current = read_group(desired.id, include_context=desired.context is not None)
        if baseline is not None and group_payload(current) != group_payload(baseline):
            raise MagpieError(f"{desired.id} 已变化，拒绝覆盖本轮基线。")
    if current is not None and same_configuration(current, desired):
        return "unchanged"
    run_id = begin_apply(db_path, desired.id, group_payload(current) if current else {}, _payload(desired))
    try:
        if current is None:
            checked_magpie([
                "group", "add", short, "models=" + ",".join(desired.members),
                "routing=" + desired.routing, "stays=" + desired.stays,
            ])
            current_new = read_group(desired.id)
            if desired.name:
                checked_magpie(["group", "set", short, "name=" + desired.name])
            _mutate(desired, current_new)
        else:
            _mutate(desired, current)
        actual = read_group(desired.id, include_context=desired.context is not None)
        if not same_configuration(actual, desired):
            fields = (
                ("name", actual.name, desired.name if desired.name else actual.name),
                ("members", tuple(member.id for member in actual.members), desired.members),
                ("routing", actual.routing, desired.routing),
                ("stays", actual.stays, desired.stays),
                ("classifier", actual.classifier, desired.classifier),
                ("意图置信度", actual.intent_sure, desired.intent_sure),
                ("rules", actual.rules, desired.rules),
                ("context", actual.context, desired.context if desired.context is not None else actual.context),
            )
            differences = "; ".join(f"{name}: {before!r} != {target!r}" for name, before, target in fields if before != target)
            raise MagpieError(f"{desired.id} 应用后的实际状态与规范不符：{differences}")
    except (MagpieError, OSError, sqlite3.Error) as error:
        try:
            if current is None:
                if desired.id in group_references(checked_magpie(["groups"])):
                    checked_magpie(["group", "rm", short])
                if desired.id in group_references(checked_magpie(["groups"])):
                    raise MagpieError(f"{desired.id} 新组删除后仍存在。")
                restored = {}
            else:
                restore_group(current)
                restored = group_payload(read_group(desired.id, include_context=current.context is not None))
                if restored != group_payload(current):
                    raise MagpieError(f"{desired.id} 回滚后的实际配置与基线不符。")
        except (MagpieError, OSError, sqlite3.Error) as restore_error:
            message = f"{error}；回滚失败：{restore_error}"
            finish_apply(db_path, run_id, {}, False, message)
            raise MagpieError(message) from restore_error
        finish_apply(db_path, run_id, restored, False, f"已回滚：{error}")
        raise MagpieError(f"{desired.id} 已回滚并停止：{error}") from error
    finish_apply(db_path, run_id, group_payload(actual), True)
    return "created" if create else "applied"


def read_all_groups(*, include_context: bool = False) -> list[RouteGroup]:
    """只读取当前存在的组；详情读取错误不能被当成组不存在。"""
    return [read_group(ref, include_context=include_context) for ref in group_references(checked_magpie(["groups"]))]


def sync_and_diff(db_path: Path | None = None, spec_path: Path | None = None) -> list[str]:
    """同步镜像并展示差异，新组显示 MISSING 而不报读取失败。"""
    spec = load_spec(spec_path)
    sync_observation(db_path, spec_path)
    return diff_lines(read_all_groups(include_context=any(group.context is not None for group in spec.groups)), spec)


def apply_spec(db_path: Path | None = None, spec_path: Path | None = None) -> list[str]:
    """预检模型后保存整轮快照、逐组应用；同步失败也会明确报告。"""
    spec = load_spec(spec_path)
    check_exposed_models(spec)
    sync_observation(db_path, spec_path)
    current = {
        f"group/{group.id}": group
        for group in read_all_groups(include_context=any(group.context is not None for group in spec.groups))
    }
    backup = Path(".local") / (
        "route-before-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8] + ".json"
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(json.dumps(
        {reference: group_payload(group) for reference, group in current.items()},
        ensure_ascii=False, indent=2,
    ) + "\n", encoding="utf-8")
    results = []
    failure = None
    try:
        for desired in application_order(spec):
            status = apply_group(
                desired, db_path, create=desired.id not in current, baseline=current.get(desired.id),
            )
            results.append(f"{status} {desired.id}")
        print("\n".join(results), flush=True)
    except MagpieError as error:
        failure = str(error)
        print("已停止在失败组；完成的操作保留在上方列表和本地应用日志中。", flush=True)
    try:
        sync_observation(db_path, spec_path)
    except (MagpieError, OSError, sqlite3.Error) as error:
        failure = (failure + "；" if failure else "") + f"SQLite 同步失败：{error}"
    if failure:
        raise MagpieError(f"{failure}；本轮快照：{backup}")
    return results
