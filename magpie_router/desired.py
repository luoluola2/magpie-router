"""期望路由规范的读取、校验与比较。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .magpie import MagpieError
from .routing import RouteGroup, Rule, token_count

SPEC_VERSION = "v1"
APPLY_ORDER = (
    "group/fallback",
    "group/architect",
    "group/review",
    "group/tests",
    "group/long",
    "group/compact",
    "group/implement",
    "group/light",
    "group/work",
)
ARCHITECT_INTENT = "架构设计、跨模块重构或技术方案，而不是直接修改代码"
REVIEW_INTENT = "评审已有方案、架构、代码改动、PR 或迁移计划并找出问题，而不是实现修改"
TEST_INTENT = "以测试框架、测试配置或批量测试修复为主要任务，且不包含主要业务实现"
IMPLEMENT_INTENT = "实现功能或修复缺陷，包括为此编写必要的测试"


@dataclass(frozen=True)
class DesiredGroup:
    """规范中一个准备应用的路由组。"""

    id: str
    routing: str
    stays: str
    members: tuple[str, ...]
    rules: tuple[Rule, ...] = ()
    classifier: str = ""


@dataclass(frozen=True)
class DesiredSpec:
    """一份有应用顺序的期望路由。"""

    version: str
    groups: tuple[DesiredGroup, ...]


def default_spec_path() -> Path:
    """返回仓库中的当前路由规范路径。"""
    return Path("policies") / "routing.json"


def _condition(raw: dict) -> tuple[tuple[str, str], ...]:
    """校验并规范化一条规则中的唯一条件。"""
    if set(raw) != {"compact", "tokens", "images", "intent"} and len(raw) != 1:
        raise MagpieError(f"规则条件无效：{raw}")
    if "compact" in raw:
        return (("compact", "true"),)
    if "images" in raw:
        return (("images", "true"),)
    if "tokens" in raw:
        return (("tokens", token_count(str(raw["tokens"]))),)
    if "intent" in raw and isinstance(raw["intent"], str) and raw["intent"]:
        return (("intent", raw["intent"]),)
    raise MagpieError(f"规则条件无效：{raw}")


def load_spec(path: Path | None = None) -> DesiredSpec:
    """读取并校验 v1 路由规范。"""
    spec_path = path or default_spec_path()
    try:
        raw = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MagpieError(f"无法读取路由规范：{error}") from error
    if not isinstance(raw, dict) or raw.get("version") != SPEC_VERSION or not isinstance(raw.get("groups"), list):
        raise MagpieError("路由规范版本或结构不受支持。")
    groups = tuple(_desired_group(item) for item in raw["groups"])
    if tuple(group.id for group in groups) != APPLY_ORDER:
        raise MagpieError("路由规范的组顺序不符合 v1。")
    _validate_v1(groups)
    return DesiredSpec(SPEC_VERSION, groups)


def _desired_group(raw: dict) -> DesiredGroup:
    """转换规范中的一个组。"""
    if not isinstance(raw, dict):
        raise MagpieError("路由组必须是对象。")
    members = raw.get("members")
    if not isinstance(members, list) or not members or len(members) != len(set(members)):
        raise MagpieError(f"{raw.get('id')} 的成员无效。")
    if any(not isinstance(member, str) or not member for member in members):
        raise MagpieError(f"{raw.get('id')} 的成员必须是非空字符串。")
    rules = []
    for item in raw.get("rules", []):
        if not isinstance(item, dict) or not isinstance(item.get("use"), str) or not isinstance(item.get("when"), dict):
            raise MagpieError(f"{raw.get('id')} 的规则无效。")
        rules.append(Rule(item["use"], _condition(item["when"])))
    return DesiredGroup(
        raw["id"],
        raw.get("routing", ""),
        raw.get("stays", ""),
        tuple(members),
        tuple(rules),
        raw.get("classifier", ""),
    )


def _validate_v1(groups: tuple[DesiredGroup, ...]) -> None:
    """锁定已确认的 v1 编排，避免规范在未评审时漂移。"""
    by_id = {group.id: group for group in groups}
    architect = by_id["group/architect"].members
    review = by_id["group/review"].members
    if architect == review or architect[0] == review[0]:
        raise MagpieError("Architect 与 Review 必须使用不同的首选模型。")
    work = by_id["group/work"]
    if (
        "group/fallback" in work.members
        or work.classifier == "group/fallback"
        or any(rule.target == "group/fallback" for rule in work.rules)
    ):
        raise MagpieError("Work 不能直接调用 Fallback。")
    fallback = by_id["group/fallback"]
    if any(member.startswith("group/") for member in fallback.members) or fallback.rules or fallback.classifier:
        raise MagpieError("Fallback 只能包含具体模型，不能反向引用任务组或分类器。")
    for reference in work.members:
        if reference not in by_id or by_id[reference].members[-1] != "group/fallback":
            raise MagpieError(f"{reference} 必须以 group/fallback 作为最后成员。")
    if work.members[0] != "group/light" or work.classifier != "group/light" or work.stays != "turn":
        raise MagpieError("Work 必须默认进入 Light，并由 Light 担任分类器。")
    if len(work.rules) != 7 or ("tokens", "500000") not in work.rules[1].conditions:
        raise MagpieError("Work 必须有七条规则，且第二条为 500k。")
    expected = (ARCHITECT_INTENT, REVIEW_INTENT, TEST_INTENT, IMPLEMENT_INTENT)
    actual = tuple(dict(rule.conditions)["intent"] for rule in work.rules if dict(rule.conditions).get("intent"))
    if actual != expected:
        raise MagpieError("Work 的意图文本不符合 v1。")
    if by_id["group/compact"].stays != "turn":
        raise MagpieError("Compact 必须按单次压缩保持。")
    joined = " ".join(member for group in groups for member in group.members)
    if "build-fast" in joined or "grok-4.6" in joined or "grok-4.5" in joined:
        raise MagpieError("v1 不能包含未暴露的长上下文或构建模型。")


def same_configuration(actual: RouteGroup, desired: DesiredGroup) -> bool:
    """比较实时组与规范中的路由字段、成员、规则和分类器。"""
    actual_rules = [(rule.target, rule.conditions) for rule in actual.rules]
    desired_rules = [(rule.target, rule.conditions) for rule in desired.rules]
    return (
        f"group/{actual.id}" == desired.id
        and actual.routing == desired.routing
        and actual.stays == desired.stays
        and actual.classifier == desired.classifier
        and [member.id for member in actual.members] == list(desired.members)
        and actual_rules == desired_rules
    )


def diff_lines(actual_groups: list[RouteGroup], spec: DesiredSpec) -> list[str]:
    """列出规范与实时配置的差异；一致的组也保留一行。"""
    actual_by_id = {f"group/{group.id}": group for group in actual_groups}
    lines = []
    for desired in spec.groups:
        actual = actual_by_id.get(desired.id)
        if actual is None:
            lines.append(f"MISSING {desired.id}")
            continue
        if same_configuration(actual, desired):
            lines.append(f"OK {desired.id}")
            continue
        lines.append(f"CHANGE {desired.id}")
        lines.append(f"  成员：{[member.id for member in actual.members]} -> {list(desired.members)}")
        lines.append(f"  保持：{actual.stays or '-'} -> {desired.stays or '-'}")
        lines.append(f"  分类器：{actual.classifier or '-'} -> {desired.classifier or '-'}")
        lines.append(f"  规则：{len(actual.rules)} -> {len(desired.rules)}")
    return lines
