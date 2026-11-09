"""把期望路由逐组应用到 magpie，并在失败时恢复该组。"""

from __future__ import annotations

from .desired import DesiredGroup, DesiredSpec, diff_lines, load_spec, same_configuration
from .magpie import MagpieError, checked_magpie
from .routing import RouteGroup, Rule, group_payload, read_group
from .store import database_path, record_apply, sync_observation


def rule_arguments(rule: Rule, classifier: str = "") -> list[str]:
    """把结构化规则转换为 magpie group rule add 的参数。"""
    conditions = dict(rule.conditions)
    arguments = [f"use={rule.target}"]
    if "compact" in conditions:
        arguments.append("compact")
    if "images" in conditions:
        arguments.append("images")
    if "tokens" in conditions:
        arguments.append(f"tokens={conditions['tokens']}")
    if "intent" in conditions:
        if not classifier:
            raise MagpieError(f"{rule.target} 的意图规则缺少分类器。")
        arguments.extend([f"intent={conditions['intent']}", f"classifier={classifier}"])
    if len(arguments) == 1:
        raise MagpieError(f"规则没有可应用的条件：{rule.target}")
    return arguments


def _short_id(reference: str) -> str:
    """返回 magpie 命令使用的组 id。"""
    if not reference.startswith("group/"):
        raise MagpieError(f"无效路由组引用：{reference}")
    return reference[6:]


def _remove_rules(reference: str, count: int) -> None:
    """从末尾删除规则，避免删除过程中序号前移。"""
    short = _short_id(reference)
    for position in range(count, 0, -1):
        checked_magpie(["group", "rule", "rm", short, str(position)])


def restore_group(before: RouteGroup) -> None:
    """按应用前快照恢复一个组的成员、保持策略、规则和分类器。"""
    reference = f"group/{before.id}"
    short = before.id
    checked_magpie(["group", "set", short, "models=" + ",".join(member.id for member in before.members)])
    if before.stays:
        checked_magpie(["group", "set", short, f"stays={before.stays}"])
    _remove_rules(reference, len(read_group(reference).rules))
    for rule in before.rules:
        checked_magpie(["group", "rule", "add", short, *rule_arguments(rule, before.classifier)])
    if before.classifier:
        checked_magpie(["group", "rule", "classifier", short, before.classifier])


def _mutate(desired: DesiredGroup, current: RouteGroup) -> None:
    """对一个组执行最小命令集；规则仅在内容变化时整体替换。"""
    short = _short_id(desired.id)
    if [member.id for member in current.members] != list(desired.members):
        checked_magpie(["group", "set", short, "models=" + ",".join(desired.members)])
    if current.stays != desired.stays:
        checked_magpie(["group", "set", short, f"stays={desired.stays}"])
    current_rules = [(rule.target, rule.conditions) for rule in current.rules]
    desired_rules = [(rule.target, rule.conditions) for rule in desired.rules]
    if current_rules != desired_rules:
        _remove_rules(desired.id, len(read_group(desired.id).rules))
        for rule in desired.rules:
            checked_magpie([
                "group", "rule", "add", short,
                *rule_arguments(rule, desired.classifier or current.classifier),
            ])
    if current.classifier != desired.classifier and not any(
        "intent" in dict(rule.conditions) for rule in desired.rules
    ):
        if not desired.classifier:
            raise MagpieError(f"{desired.id} 需要清空分类器，当前过渡命令不支持。")
        checked_magpie(["group", "rule", "classifier", short, desired.classifier])


def apply_group(desired: DesiredGroup, db_path=None) -> str:
    """应用一个组并回读；失败时恢复该组，不继续隐式修改。"""
    current = read_group(desired.id)
    before = group_payload(current)
    if same_configuration(current, desired):
        record_apply(db_path, desired.id, before, before, True, "")
        return "unchanged"
    try:
        _mutate(desired, current)
        actual = read_group(desired.id)
        if not same_configuration(actual, desired):
            raise MagpieError(f"{desired.id} 应用后的实际状态与规范不符。")
    except MagpieError as error:
        try:
            restore_group(current)
        except MagpieError as restore_error:
            record_apply(db_path, desired.id, before, {}, False, f"{error}；回滚失败：{restore_error}")
            raise MagpieError(f"{error}；回滚失败：{restore_error}") from restore_error
        record_apply(db_path, desired.id, before, group_payload(read_group(desired.id)), False, str(error))
        raise
    record_apply(db_path, desired.id, before, group_payload(actual), True, "")
    return "applied"


def sync_and_diff(db_path=None, spec_path=None) -> list[str]:
    """刷新镜像后返回规范差异。"""
    sync_observation(db_path, spec_path)
    spec = load_spec(spec_path)
    return diff_lines([read_group(group.id) for group in spec.groups], spec)


def apply_spec(db_path=None, spec_path=None) -> list[str]:
    """按规范顺序应用全部分组，任一失败即停止。"""
    spec = load_spec(spec_path)
    sync_observation(db_path, spec_path)
    results = []
    try:
        for desired in spec.groups:
            results.append(f"{apply_group(desired, db_path)} {desired.id}")
    finally:
        try:
            sync_observation(db_path, spec_path)
        except MagpieError:
            pass
    return results


def load_for_cli(spec_path=None) -> DesiredSpec:
    """供命令行读取默认规范。"""
    return load_spec(spec_path)
