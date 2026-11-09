"""读取 v2 规范，验证引用和手动 SOTA 隔离，按依赖比较配置。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .magpie import MagpieError, checked_magpie
from .routing import GROUP_ID, RouteGroup, Rule, normalize_conditions

SPEC_VERSION = "v2"
EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}


@dataclass(frozen=True)
class DesiredGroup:
    """目标组；name 仅用于新建显示，不强制改名现有组。"""

    id: str
    routing: str
    stays: str
    members: tuple[str, ...]
    rules: tuple[Rule, ...] = ()
    classifier: str = ""
    name: str = ""


@dataclass(frozen=True)
class DesiredSpec:
    """一份按声明保存、按引用关系应用的期望路由。"""

    version: str
    groups: tuple[DesiredGroup, ...]


def default_spec_path() -> Path:
    """返回仓库中的当前路由规范路径。"""
    return Path("policies") / "routing.json"


def model_reference(reference: str) -> str:
    """移除已知推理后缀，保留 :free、上下文变体和模型 id 内的斜线。"""
    base, separator, suffix = reference.rpartition(":")
    return base if separator and suffix in EFFORTS else reference


def _reference(value: object) -> str:
    """校验组或模型引用，拒绝空值、逗号和命令选项。"""
    if not isinstance(value, str) or not re.fullmatch(r"[^\s,=]+/[^\s,=]+", value):
        raise MagpieError(f"无效路由引用：{value}")
    if value.startswith("group/") and not GROUP_ID.fullmatch(value[6:]):
        raise MagpieError(f"无效路由组引用：{value}")
    return value


def load_spec(path: Path | None = None) -> DesiredSpec:
    """读取规范；候选顺序、阈值和意图文本只由 JSON 定义。"""
    spec_path = path or default_spec_path()
    try:
        raw = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MagpieError(f"无法读取路由规范：{error}") from error
    if not isinstance(raw, dict) or set(raw) != {"version", "groups"} or raw.get("version") != SPEC_VERSION:
        raise MagpieError("路由规范须为 v2；旧配置可从本地回滚快照恢复。")
    if not isinstance(raw["groups"], list) or not raw["groups"]:
        raise MagpieError("规范缺少 groups。")
    spec = DesiredSpec(SPEC_VERSION, tuple(_desired_group(item) for item in raw["groups"]))
    validate_spec(spec)
    return spec


def _desired_group(raw: dict) -> DesiredGroup:
    """校验组字段、成员和规则，组合条件均为 AND。"""
    if not isinstance(raw, dict) or set(raw) - {"id", "name", "routing", "stays", "members", "rules", "classifier"}:
        raise MagpieError("路由组结构或字段不受支持。")
    reference = _reference(raw.get("id"))
    if not reference.startswith("group/"):
        raise MagpieError("路由组 id 必须以 group/ 开头。")
    members = raw.get("members")
    if not isinstance(members, list) or not members:
        raise MagpieError(f"{reference} 缺少成员。")
    members = tuple(_reference(member) for member in members)
    if len(members) != len(set(members)):
        raise MagpieError(f"{reference} 的成员重复。")
    if raw.get("routing") not in {"order", "smart", "usage", "pace", "rotate", "weight", "drain"}:
        raise MagpieError(f"{reference} 的 routing 不受支持。")
    if raw.get("stays") not in {"auto", "session", "turn", "off", "strict"}:
        raise MagpieError(f"{reference} 的 stays 不受支持。")
    raw_rules = raw.get("rules", [])
    if not isinstance(raw_rules, list):
        raise MagpieError(f"{reference} 的 rules 必须是列表。")
    rules = []
    for item in raw_rules:
        if not isinstance(item, dict) or set(item) != {"use", "when"} or not isinstance(item["use"], str):
            raise MagpieError(f"{reference} 的规则无效。")
        targets = tuple(_reference(target.strip()) for target in item["use"].split(","))
        if len(targets) != len(set(targets)):
            raise MagpieError(f"{reference} 的规则目标重复。")
        rules.append(Rule(",".join(targets), normalize_conditions(item["when"])))
    classifier = raw.get("classifier", "")
    if classifier:
        _reference(classifier)
    elif classifier != "":
        raise MagpieError(f"{reference} 的分类器格式无效。")
    name = raw.get("name", "")
    if not isinstance(name, str) or "\n" in name:
        raise MagpieError(f"{reference} 的显示名称无效。")
    return DesiredGroup(
        raw["id"],
        raw.get("routing", ""),
        raw.get("stays", ""),
        tuple(members),
        tuple(rules),
        classifier,
        name,
    )


def dependencies(group: DesiredGroup) -> tuple[str, ...]:
    """列出成员、规则和分类器中的依赖，保持首次引用顺序。"""
    references = list(group.members)
    references.extend(target for rule in group.rules for target in rule.targets)
    if group.classifier:
        references.append(group.classifier)
    return tuple(dict.fromkeys(ref for ref in references if ref.startswith("group/")))


def application_order(spec: DesiredSpec) -> tuple[DesiredGroup, ...]:
    """拓扑排序，拒绝环和超过 magpie 八层的依赖，Work 最后应用。"""
    by_id = {group.id: group for group in spec.groups}
    ordered = []
    visited = set()

    def visit(reference: str, stack: tuple[str, ...]) -> None:
        """递归检查依赖并收集先决组；每条路径均检查深度。"""
        if reference not in by_id:
            raise MagpieError(f"引用了规范中不存在的组：{reference}")
        if reference in stack:
            raise MagpieError(f"路由依赖成环：{' → '.join(stack + (reference,))}")
        if len(stack) >= 8:
            raise MagpieError("嵌套路由超过 magpie 的八层限制。")
        for dependency in dependencies(by_id[reference]):
            visit(dependency, stack + (reference,))
        if reference not in visited:
            visited.add(reference)
            ordered.append(by_id[reference])

    for group in spec.groups:
        if group.id != "group/work":
            visit(group.id, ())
    if "group/work" in by_id:
        visit("group/work", ())
    return tuple(ordered)


def validate_spec(spec: DesiredSpec) -> None:
    """校验引用、末位兜底和 SOTA 隔离，不硬编码任务排序或规则数。"""
    by_id = {group.id: group for group in spec.groups}
    if len(by_id) != len(spec.groups):
        raise MagpieError("组 id 重复。")
    if not {"group/work", "group/fallback", "group/sota"} <= set(by_id):
        raise MagpieError("规范必须包含 Work、Fallback 和 SOTA。")
    for group in spec.groups:
        for rule in group.rules:
            if not set(rule.targets) <= set(group.members):
                raise MagpieError(f"{group.id} 的规则目标必须是本组成员。")
            if "intent" in dict(rule.conditions) and not group.classifier:
                raise MagpieError(f"{group.id} 的意图规则缺少分类器。")
        if group.id != "group/work" and "group/work" in dependencies(group):
            raise MagpieError("其他组不能反向调用 Work。")
        if group.id != "group/sota":
            if "group/sota" in dependencies(group):
                raise MagpieError("SOTA 只能由用户主动选择，不能被其他组引用。")
            references = list(group.members) + ([group.classifier] if group.classifier else [])
            for reference in references:
                model = model_reference(reference)
                provider, model_id = model.split("/", 1)
                if "astra" in model_id.lower() or "opus" in model_id.lower() or provider == "deepseek":
                    raise MagpieError(f"{group.id} 自动链包含仅限手动调用的模型：{reference}")
        if group.id not in {"group/work", "group/fallback", "group/sota"}:
            if group.members.count("group/fallback") != 1 or group.members[-1] != "group/fallback":
                raise MagpieError(f"{group.id} 必须以唯一的 Fallback 作为最后成员。")
            if any("group/fallback" in rule.targets for rule in group.rules) or group.classifier == "group/fallback":
                raise MagpieError(f"{group.id} 不能提前选择末位 Fallback。")
    for reference in ("group/fallback", "group/sota"):
        if dependencies(by_id[reference]) or by_id[reference].classifier:
            raise MagpieError(f"{reference} 只能引用具体模型，不能使用组或分类器。")
    if "group/fallback" in dependencies(by_id["group/work"]):
        raise MagpieError("Work 不能直接调用 Fallback。")
    application_order(spec)


def check_exposed_models(spec: DesiredSpec) -> None:
    """确认所需模型已暴露；只读供应商列表，不刷新或启用模型。"""
    references = {
        model_reference(ref)
        for group in spec.groups
        for ref in (*group.members, group.classifier)
        if ref and not ref.startswith("group/")
    }
    for provider in sorted({ref.split("/", 1)[0] for ref in references}):
        output = checked_magpie(["provider", provider])
        exposed = set(re.findall(r"^  \s+(" + re.escape(provider) + r"/\S+)\s{2,}", output, re.MULTILINE))
        missing = sorted(ref for ref in references if ref.startswith(provider + "/") and ref not in exposed)
        if missing:
            raise MagpieError(f"无法确认模型已暴露，拒绝写入：{', '.join(missing)}")


def same_configuration(actual: RouteGroup, desired: DesiredGroup) -> bool:
    """比较实时组与规范中的路由字段、成员、规则和分类器。"""
    return (
        f"group/{actual.id}" == desired.id
        and actual.routing == desired.routing
        and actual.stays == desired.stays
        and actual.classifier == desired.classifier
        and [member.id for member in actual.members] == list(desired.members)
        and actual.rules == desired.rules
    )


def diff_lines(actual_groups: list[RouteGroup], spec: DesiredSpec) -> list[str]:
    """展示实际差异，包括规则数量相同但条件或顺序变化。"""
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
            ("成员", tuple(member.id for member in actual.members), desired.members),
            ("routing", actual.routing, desired.routing),
            ("stays", actual.stays, desired.stays),
            ("分类器", actual.classifier, desired.classifier),
            ("规则", actual.rules, desired.rules),
        ):
            if before != after:
                lines.append(f"  {label}：{before} → {after}")
    return lines
