"""路由组观测、审计与单组变更计划。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .magpie import MagpieError, checked_magpie

PLAN_VERSION = 1
GROUP_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


@dataclass(frozen=True)
class Member:
    """路由组中一个保留原始标识的候选。"""

    id: str
    available: bool
    detail: str


@dataclass(frozen=True)
class Rule:
    """一条路由规则；条件使用稳定的键值序列表示。"""

    target: str
    conditions: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RouteGroup:
    """magpie 中一个路由组的实时配置。"""

    id: str
    name: str
    members: tuple[Member, ...]
    routing: str = ""
    stays: str = ""
    rules: tuple[Rule, ...] = ()
    classifier: str = ""


@dataclass(frozen=True)
class Finding:
    """一项可以直接由实时配置证明的问题。"""

    severity: str
    group: str
    message: str


def _group_id(reference: str) -> str:
    """校验 group/<id> 引用并返回可安全传给 CLI 的 id。"""
    if not reference.startswith("group/") or not GROUP_ID.fullmatch(reference[6:]):
        raise MagpieError(f"无效路由组引用：{reference}")
    return reference[6:]


def token_count(value: str) -> str:
    """把 500k、1m 或纯数字统一为 token 个数字符串。"""
    matched = re.fullmatch(r"(\d+)([kKmM])?", value.strip())
    if not matched:
        raise MagpieError(f"无法识别 token 数量：{value}")
    count = int(matched.group(1))
    suffix = (matched.group(2) or "").lower()
    if suffix == "k":
        count *= 1000
    elif suffix == "m":
        count *= 1_000_000
    return str(count)


def _rule_conditions(body: str) -> tuple[tuple[str, str], ...]:
    """把 magpie 的单条件规则正文转换为规范条件。"""
    if body in {"compact", "compacting"}:
        return (("compact", "true"),)
    if body == "images":
        return (("images", "true"),)
    tokens = re.fullmatch(r"tokens\s*(?:>=|≥)\s*(\d+[kKmM]?)", body)
    if tokens:
        return (("tokens", token_count(tokens.group(1))),)
    intent = re.fullmatch(r'intent\s+"(.*)"', body)
    if intent:
        return (("intent", intent.group(1)),)
    raise MagpieError(f"无法识别规则：{body}")


def _parse_rules(reference: str, output: str) -> tuple[Rule, ...]:
    """解析规则区；没有规则区时返回空，有规则区但无法识别时失败。"""
    rules: list[Rule] = []
    for line in output.splitlines():
        matched = re.match(r"^\s+(?:rules\s+)?(\d+)\s+(.+?)\s+(?:→|->)\s+(\S+)\s*$", line)
        if not matched:
            continue
        position = int(matched.group(1))
        if position != len(rules) + 1:
            raise MagpieError(f"{reference} 的规则序号不连续。")
        rules.append(Rule(matched.group(3), _rule_conditions(matched.group(2).strip())))
    if re.search(r"^\s{2}rules\s+", output, re.MULTILINE) and not rules:
        raise MagpieError(f"{reference} 的规则区无法识别。")
    return tuple(rules)


def _field(output: str, name: str) -> str:
    """读取组详情中的单个字段；字段缺失时返回空字符串。"""
    matched = re.search(rf"^\s{{2}}{name}\s+(\S+)", output, re.MULTILINE)
    return matched.group(1) if matched else ""


def parse_group(reference: str, output: str) -> RouteGroup:
    """解析 magpie 当前版本的组详情；成员区缺失时拒绝猜测。"""
    group_id = _group_id(reference)
    heading = re.search(r"^\s{2}(\S.*?)\s{2,}(group/[a-zA-Z0-9_-]+)\s*$", output, re.MULTILINE)
    if not heading or heading.group(2) != reference:
        raise MagpieError(f"无法识别 {reference} 的详情。")

    lines = output.splitlines()
    member_start = next((index for index, line in enumerate(lines) if re.match(r"^\s{2}models\s+\d+\s+", line)), None)
    if member_start is None:
        raise MagpieError(f"{reference} 没有可识别的成员区。")

    members: list[Member] = []
    for line in lines[member_start:]:
        if re.match(r"^\s{2}(?!models\s+\d+\s+)[a-zA-Z][a-zA-Z-]*\s{2,}", line):
            break
        matched = re.match(r"^\s+(?:models\s+)?(\d+)\s+(\S+)(?:\s{2,}(.*))?$", line)
        if not matched:
            continue
        position = int(matched.group(1))
        if position != len(members) + 1:
            raise MagpieError(f"{reference} 的成员序号不连续。")
        detail = (matched.group(3) or "").strip()
        members.append(Member(matched.group(2), "not served:" not in detail, detail))

    if not members:
        raise MagpieError(f"{reference} 的成员为空或格式未知。")
    return RouteGroup(
        group_id,
        heading.group(1).strip(),
        tuple(members),
        _field(output, "routing"),
        _field(output, "stays"),
        _parse_rules(reference, output),
        _field(output, "classifier"),
    )


def group_payload(group: RouteGroup) -> dict:
    """生成写入本地镜像和回滚日志的组状态。"""
    return {
        "id": f"group/{group.id}",
        "name": group.name,
        "routing": group.routing,
        "stays": group.stays,
        "classifier": group.classifier,
        "members": [member.id for member in group.members],
        "rules": [{"target": rule.target, "conditions": dict(rule.conditions)} for rule in group.rules],
    }


def read_group(reference: str) -> RouteGroup:
    """通过 magpie 读取一个路由组的最新状态。"""
    return parse_group(reference, checked_magpie(["group", _group_id(reference)]))


def group_references(output: str) -> list[str]:
    """从组清单提取顶层组引用，保持声明顺序并去重。"""
    references = []
    for line in output.splitlines():
        matched = re.match(r"^\s{2}\S.*?\s{2,}(group/[a-zA-Z0-9_-]+)\s{2,}", line)
        if matched and matched.group(1) not in references:
            references.append(matched.group(1))
    return references


def inspect() -> dict[str, str]:
    """采集建立路由决策所需的标准只读视图。"""
    return {
        "agents": checked_magpie(["ls"]),
        "providers": checked_magpie(["providers"]),
        "groups": checked_magpie(["groups"]),
        "models": checked_magpie(["models"]),
    }


def audit() -> list[Finding]:
    """审计所有已声明路由组的可用性与空链风险。"""
    groups_output = checked_magpie(["groups"])
    references = group_references(groups_output)
    if not references:
        raise MagpieError("未找到路由组，或组清单格式无法识别。")

    findings = []
    for reference in references:
        group = read_group(reference)
        for position, member in enumerate(group.members, start=1):
            if not member.available:
                findings.append(Finding("warning", reference, f"第 {position} 位 {member.id} 当前不可服务：{member.detail}"))
        if not any(member.available for member in group.members):
            findings.append(Finding("error", reference, "没有任何已声明为可服务的成员。"))
    return findings


def create_order_plan(reference: str, members: list[str], reason: str) -> dict:
    """基于实时成员全集创建排序计划，并保存反向计划。"""
    group = read_group(reference)
    current = [member.id for member in group.members]
    if not members:
        raise MagpieError("计划必须显式列出目标组的全部成员。")
    if len(members) != len(set(members)):
        raise MagpieError("计划包含重复成员。")
    if set(members) != set(current) or len(members) != len(current):
        raise MagpieError("计划成员必须与当前组的成员全集一致；增删或暴露模型需要独立批准。")

    unavailable = [member.id for member in group.members if not member.available]
    if unavailable:
        raise MagpieError(f"组内存在未暴露成员，不能自动生成变更：{', '.join(unavailable)}")

    timestamp = datetime.now(timezone.utc).isoformat()
    plan = {
        "version": PLAN_VERSION,
        "kind": "set-group-order",
        "created_at": timestamp,
        "group": reference,
        "baseline": current,
        "target": members,
        "reason": reason,
    }
    plan["rollback"] = {**plan, "baseline": members, "target": current, "rollback": None}
    return plan


def write_plan(plan: dict, path: Path) -> None:
    """将计划以 UTF-8 JSON 写入本地文件，不执行任何路由变更。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_plan(path: Path) -> dict:
    """读取计划并检查它属于当前支持的有限变更类型。"""
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MagpieError(f"无法读取计划：{error}") from error
    if not isinstance(plan, dict) or plan.get("version") != PLAN_VERSION or plan.get("kind") != "set-group-order":
        raise MagpieError("计划版本或类型不受支持。")
    _group_id(plan.get("group", ""))
    baseline = plan.get("baseline")
    target = plan.get("target")
    if not isinstance(baseline, list) or not isinstance(target, list) or not baseline or not target:
        raise MagpieError("计划缺少有效的 baseline 或 target。")
    if any(not isinstance(member, str) or not member for member in [*baseline, *target]):
        raise MagpieError("计划成员必须是非空字符串。")
    if len(baseline) != len(set(baseline)) or len(target) != len(set(target)):
        raise MagpieError("计划成员不能重复。")
    if set(baseline) != set(target):
        raise MagpieError("计划只能改变成员顺序，不能改变成员集合。")
    return plan


def apply_order_plan(plan: dict) -> tuple[RouteGroup, Path]:
    """校验实时基线后应用单组排序，读取验证并保存可回滚记录。"""
    if not isinstance(plan, dict):
        raise MagpieError("计划必须是 JSON 对象。")
    _group_id(plan.get("group", ""))
    for field in ("baseline", "target"):
        members = plan.get(field)
        if not isinstance(members, list) or not members or any(not isinstance(member, str) or not member for member in members):
            raise MagpieError(f"计划的 {field} 必须是非空成员列表。")
        if len(members) != len(set(members)):
            raise MagpieError(f"计划的 {field} 不能包含重复成员。")
    if set(plan["baseline"]) != set(plan["target"]):
        raise MagpieError("计划只能改变顺序，不能改变成员集合。")
    reference = plan["group"]
    group = read_group(reference)
    current = [member.id for member in group.members]
    if current != plan["baseline"]:
        raise MagpieError(f"{reference} 已发生变化，拒绝应用旧计划。当前：{current}")
    if any(not member.available for member in group.members):
        raise MagpieError(f"{reference} 含有不可服务成员，拒绝应用。")

    local = Path(".local")
    local.mkdir(exist_ok=True)
    checked_magpie(["group", "set", _group_id(reference), "models=" + ",".join(plan["target"])])
    actual = read_group(reference)
    if [member.id for member in actual.members] != plan["target"]:
        raise MagpieError(f"{reference} 应用后的实际状态与计划不符：{[member.id for member in actual.members]}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = local / f"{_group_id(reference)}-{stamp}-applied.json"
    write_plan({**plan, "applied_at": datetime.now(timezone.utc).isoformat(), "verified": True}, report_path)
    return actual, report_path
