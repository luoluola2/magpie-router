"""比较实时路由与规范，并汇总可验证的问题。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .magpie import MagpieError, checked_magpie
from .model import RouteGroup, group_references, read_group
from .spec import (
    RETIRED_GROUPS,
    DesiredGroup,
    DesiredSpec,
    application_order,
    load_spec,
)


@dataclass(frozen=True)
class Finding:
    """一项可以直接由实时配置证明的问题。"""

    severity: str
    group: str
    message: str


def topology_findings(groups: list[RouteGroup], spec_path: Path | None = None) -> list[Finding]:
    """按实时成员检查相邻层引用，规则目标与分类器也纳入同一契约。"""
    spec = load_spec(spec_path)
    layers = {group.id: group.layer for group in spec.groups}
    expected_layers = {"entry": "task", "task": "pool"}
    findings = []
    actual_ids = {f"group/{group.id}" for group in groups}
    for missing in sorted(layers.keys() - actual_ids):
        findings.append(Finding("error", missing, "规范中的路由组不存在。"))
    for group in groups:
        reference = f"group/{group.id}"
        layer = layers.get(reference)
        if layer is None:
            if reference in RETIRED_GROUPS:
                findings.append(Finding("error", reference, "已知退役组仍存在；检查引用后由用户决定是否删除。"))
            else:
                findings.append(Finding("warning", reference, "实时组未包含在当前规范中，无法判断层级。"))
            continue
        references = [member.id for member in group.members]
        references.extend(target for rule in group.rules for target in rule.targets)
        if group.classifier and group.classifier != "typesafe/jev-latest":
            references.append(group.classifier)
        for target in references:
            if layer == "pool":
                if target.startswith("group/"):
                    findings.append(Finding("error", reference, f"资源池越层或反向引用路由组：{target}"))
                continue
            expected = expected_layers[layer]
            if layers.get(target) != expected:
                findings.append(Finding("error", reference, f"应仅引用 {expected} 层，实际引用：{target}"))
    return findings


def audit() -> list[Finding]:
    """审计所有实时组的可用性、空链风险及相邻层引用。"""
    groups_output = checked_magpie(["groups"])
    references = group_references(groups_output)
    if not references:
        raise MagpieError("未找到路由组，或组清单格式无法识别。")

    findings = []
    groups = [read_group(reference) for reference in references]
    for group in groups:
        reference = f"group/{group.id}"
        for position, member in enumerate(group.members, start=1):
            if not member.available:
                findings.append(Finding("warning", reference, f"第 {position} 位 {member.id} 当前不可服务：{member.detail}"))
        if not any(member.available for member in group.members):
            findings.append(Finding("error", reference, "没有任何已声明为可服务的成员。"))
    return findings + topology_findings(groups)


def same_configuration(actual: RouteGroup, desired: DesiredGroup) -> bool:
    """比较实时组与规范中的路由字段、成员、规则和分类器。"""
    return (
        f"group/{actual.id}" == desired.id
        and (not desired.name or actual.name == desired.name)
        and actual.routing == desired.routing
        and actual.stays == desired.stays
        and actual.classifier == desired.classifier
        and actual.intent_sure == desired.intent_sure
        and [member.id for member in actual.members] == list(desired.members)
        and actual.rules == desired.rules
        and (desired.context is None or actual.context == desired.context)
    )


def diff_lines(actual_groups: list[RouteGroup], spec: DesiredSpec) -> list[str]:
    """展示目标差异、已知退役组和未纳入规范的其他实时组。"""
    actual_by_id = {f"group/{group.id}": group for group in actual_groups}
    lines = []
    for desired in application_order(spec):
        actual = actual_by_id.get(desired.id)
        if actual is None:
            lines.append(f"MISSING {desired.id}")
            continue
        if same_configuration(actual, desired):
            lines.append(f"OK {desired.id}")
            continue
        lines.append(f"CHANGE {desired.id}")
        for label, before, after in (
            ("名称", actual.name, desired.name or actual.name),
            ("成员", tuple(member.id for member in actual.members), desired.members),
            ("routing", actual.routing, desired.routing),
            ("stays", actual.stays, desired.stays),
            ("分类器", actual.classifier, desired.classifier),
            ("意图置信度", actual.intent_sure, desired.intent_sure),
            ("规则", actual.rules, desired.rules),
            ("公布上下文", actual.context, desired.context if desired.context is not None else actual.context),
        ):
            if before != after:
                lines.append(f"  {label}：{before} → {after}")
    expected_ids = {group.id for group in spec.groups}
    for reference in sorted(actual_by_id.keys() - expected_ids):
        if reference in RETIRED_GROUPS:
            lines.append(f"RETIRED {reference}")
        else:
            lines.append(f"UNKNOWN {reference}")
    return lines
