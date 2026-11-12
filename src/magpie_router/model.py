"""路由组领域类型与 magpie 详情解析。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace

from .contexts import context_windows
from .magpie import MagpieError, checked_magpie

GROUP_ID = re.compile(r"^[a-zA-Z0-9_-]+$")
WHEN_FIELDS = {"tokens", "effort", "agent", "subagent", "not", "message", "system", "last"}
USE_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
RULE_FRAGMENT = re.compile(
    r'(?P<intent>intent\s+"(?:[^"\\]|\\.)*")'
    r'|(?P<tokens>tokens\s*(?:>=|≥)\s*\d+[kKmM]?)'
    r'|(?P<effort>effort\s*(?:>=|≥)\s*\w+|effort\s+on\b)'
    r'|(?P<bool>images\b|compacting\b|compact\b)'
    r'|(?P<when>(?:subagent|not\s+subagent)\b|agent\s+is(?:\s+not)?\s+\S+'
    r'|(?:message|system|last)\s+(?:contains|does\s+not\s+contain|starts\s+with|matches)\s+"(?:[^"\\]|\\.)*")'
)


@dataclass(frozen=True)
class Member:
    """路由组中一个保留原始标识的候选。"""

    id: str
    available: bool
    detail: str


@dataclass(frozen=True)
class Rule:
    """一条路由规则；条件、when= 表达式与推理覆盖分开保存。"""

    target: str
    conditions: tuple[tuple[str, str], ...]
    expressions: tuple[str, ...] = ()
    use_effort: str = ""

    @property
    def targets(self) -> tuple[str, ...]:
        """返回规则声明的候选顺序，保留多层模型 id。"""
        return tuple(self.target.split(","))


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
    context: int | None = None
    intent_sure: str = ""


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


def normalize_conditions(raw: dict) -> tuple[tuple[str, str], ...]:
    """校验支持的 AND 条件并规范顺序；不忽略未知键或假值。"""
    if not isinstance(raw, dict) or not raw or set(raw) - {"compact", "images", "tokens", "intent", "effort"}:
        raise MagpieError(f"不支持的规则条件：{raw}")
    normalized = {}
    for key, value in raw.items():
        if key in {"compact", "images"}:
            if value is not True and value != "true":
                raise MagpieError(f"{key} 必须明确为 true。")
            normalized[key] = "true"
        elif key == "tokens":
            normalized[key] = token_count(str(value))
            if int(normalized[key]) <= 0:
                raise MagpieError("tokens 必须大于零。")
        elif key == "effort":
            if not isinstance(value, str) or value not in {"on", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}:
                raise MagpieError(f"不支持的推理级别：{value}")
            normalized[key] = value
        else:
            if not isinstance(value, str) or not value.strip() or "\n" in value:
                raise MagpieError("intent 必须是单行非空文本。")
            normalized[key] = value
    return tuple(sorted(normalized.items()))


def normalize_expressions(raw: object) -> tuple[str, ...]:
    """校验 when= 表达式并统一顺序；不把解析失败当成空条件。"""
    if not isinstance(raw, list):
        raise MagpieError(f"when 表达式必须是列表：{raw}")
    expressions = []
    for value in raw:
        if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
            raise MagpieError(f"无效的 when 表达式：{value}")
        expression = " ".join(value.split())
        if expression.split(None, 1)[0].lower() not in WHEN_FIELDS:
            raise MagpieError(f"不支持的 when 字段：{value}")
        expressions.append(expression)
    if len(set(expressions)) != len(expressions):
        raise MagpieError("when 表达式重复。")
    return tuple(sorted(expressions))


def normalize_use_effort(value: object) -> str:
    """校验规则级推理覆盖；空值表示不覆盖。"""
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or value not in USE_EFFORTS:
        raise MagpieError(f"不支持的 use-effort：{value}")
    return value


def _canonical_when(fragment: str) -> str:
    """把 CLI 带引号的 when= 条件还原为规范里的单行表达式。"""
    text = " ".join(fragment.split())
    quoted = re.fullmatch(r'(.+?)\s+"((?:[^"\\]|\\.)*)"', text)
    if quoted:
        try:
            value = json.loads('"' + quoted.group(2) + '"')
        except json.JSONDecodeError as error:
            raise MagpieError(f"无法识别 when 引号：{fragment}") from error
        text = quoted.group(1) + " " + value
    return text.replace("≥", ">=").replace("≤", "<=")


def _rule_parts(body: str) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    """完整解析 CLI 的 AND 正文；引号中的分隔符不作为条件边界。"""
    # effort/tokens 的 when= 与既有条件在 CLI 中同形，规范暂不使用这两种表达式。
    remaining = body.strip()
    conditions: dict = {}
    expressions: list[str] = []
    while remaining:
        matched = RULE_FRAGMENT.match(remaining)
        if not matched:
            raise MagpieError(f"无法完整识别规则：{body}")
        fragment = matched.group(0)
        kind = matched.lastgroup
        if kind == "intent":
            try:
                value, _ = json.JSONDecoder().raw_decode(fragment[fragment.index('"'):])
            except json.JSONDecodeError as error:
                raise MagpieError(f"无法识别意图引号：{body}") from error
            key = "intent"
        elif kind == "tokens":
            count = re.search(r"\d+[kKmM]?", fragment)
            if count is None:
                raise MagpieError(f"无法识别 token 条件：{body}")
            key, value = "tokens", count.group(0)
        elif kind == "effort":
            level = re.search(r"\w+$", fragment)
            if level is None:
                raise MagpieError(f"无法识别推理条件：{body}")
            key, value = "effort", ("on" if fragment.endswith(" on") else level.group(0))
        elif kind == "bool":
            key, value = ("images" if fragment == "images" else "compact"), True
        else:
            expression = _canonical_when(fragment)
            if expression in expressions:
                raise MagpieError(f"规则存在重复条件：{body}")
            expressions.append(expression)
            remaining = remaining[matched.end():].strip()
            if remaining:
                if not remaining.startswith("·"):
                    raise MagpieError(f"无法识别条件分隔符：{body}")
                remaining = remaining[1:].strip()
                if not remaining:
                    raise MagpieError(f"规则条件不完整：{body}")
            continue
        if key in conditions:
            raise MagpieError(f"规则存在重复条件：{body}")
        conditions[key] = value
        remaining = remaining[matched.end():].strip()
        if remaining:
            if not remaining.startswith("·"):
                raise MagpieError(f"无法识别条件分隔符：{body}")
            remaining = remaining[1:].strip()
            if not remaining:
                raise MagpieError(f"规则条件不完整：{body}")
    return (normalize_conditions(conditions) if conditions else (), tuple(sorted(expressions)))


def _parse_rules(reference: str, output: str) -> tuple[Rule, ...]:
    """解析规则区；没有规则区时返回空，有规则区但无法识别时失败。"""
    rules: list[Rule] = []
    in_rules = False
    for line in output.splitlines():
        if re.match(r"^  rules\s+", line):
            in_rules = True
        elif in_rules and re.match(r"^  \S", line):
            break
        if not in_rules or not line.strip():
            continue
        matched = re.fullmatch(r"\s+(?:rules\s+)?(\d+)\s+(.+)\s+(?:→|->)\s+(.+?)\s*", line)
        if not matched:
            raise MagpieError(f"{reference} 存在无法识别的规则行。")
        position = int(matched.group(1))
        if position != len(rules) + 1:
            raise MagpieError(f"{reference} 的规则序号不连续。")
        target_text = matched.group(3).strip()
        effort = re.search(r"\s+at\s+(low|medium|high|xhigh|max)$", target_text)
        use_effort = effort.group(1) if effort else ""
        if effort:
            target_text = target_text[:effort.start()]
        targets = tuple(part.strip() for part in target_text.split(","))
        if any(not target or re.search(r"\s", target) for target in targets) or len(set(targets)) != len(targets):
            raise MagpieError(f"{reference} 存在无效或重复规则目标。")
        conditions, expressions = _rule_parts(matched.group(2).strip())
        rules.append(Rule(",".join(targets), conditions, expressions, use_effort))
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
        None,
        _field(output, "intent-sure"),
    )


def group_payload(group: RouteGroup) -> dict:
    """生成写入本地镜像和回滚日志的组状态。"""
    return {
        "id": f"group/{group.id}",
        "name": group.name,
        "routing": group.routing,
        "stays": group.stays,
        "classifier": group.classifier,
        "context": group.context,
        "intent_sure": group.intent_sure,
        "members": [member.id for member in group.members],
        "rules": [{
            "target": rule.target, "conditions": dict(rule.conditions),
            "expressions": list(rule.expressions), "use_effort": rule.use_effort,
        } for rule in group.rules],
    }


def read_group(reference: str, *, include_context: bool = False) -> RouteGroup:
    """读取组配置；需要容量验证时补充网关元数据，缺失时明确失败。"""
    group = parse_group(reference, checked_magpie(["group", _group_id(reference)]))
    if include_context:
        windows = context_windows()
        if reference not in windows:
            raise MagpieError(f"模型元数据缺少 {reference}，无法验证上下文。")
        group = replace(group, context=windows[reference])
    return group


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
