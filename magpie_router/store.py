"""本地 SQLite 配置镜像。只保存路由配置，不保存凭据或用量。"""

from __future__ import annotations

from contextlib import contextmanager

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .desired import default_spec_path, load_spec
from .magpie import MagpieError, checked_magpie
from .routing import RouteGroup, group_payload, group_references, parse_group

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    taken_at TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    completeness TEXT NOT NULL CHECK (completeness IN ('complete', 'partial'))
);
CREATE TABLE IF NOT EXISTS groups (
    group_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    routing TEXT NOT NULL,
    stays TEXT NOT NULL,
    classifier TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    group_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    member_id TEXT NOT NULL,
    available INTEGER NOT NULL,
    detail TEXT NOT NULL,
    PRIMARY KEY (group_id, position)
);
CREATE TABLE IF NOT EXISTS rules (
    group_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    target TEXT NOT NULL,
    conditions_json TEXT NOT NULL,
    PRIMARY KEY (group_id, position)
);
CREATE TABLE IF NOT EXISTS desired_routes (
    id INTEGER PRIMARY KEY,
    version TEXT NOT NULL,
    loaded_at TEXT NOT NULL,
    spec_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS apply_log (
    id INTEGER PRIMARY KEY,
    taken_at TEXT NOT NULL,
    group_id TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    ok INTEGER NOT NULL,
    error TEXT NOT NULL
);
"""


def database_path() -> Path:
    """返回环境变量指定的数据库，默认放在未跟踪的 .local 目录。"""
    configured = os.environ.get("MAGPIE_ROUTER_DB")
    return Path(configured) if configured else Path(".local") / "magpie-router.db"


@contextmanager
def connect(path: Path | None = None):
    """打开数据库并在退出时提交和关闭，避免 Windows 上文件仍被占用。"""
    target = path or database_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        yield connection
        connection.commit()
    finally:
        connection.close()


def initialize(path: Path | None = None) -> Path:
    """创建配置镜像；重复执行不清除已有观测和日志。"""
    target = path or database_path()
    with connect(target) as connection:
        connection.executescript(SCHEMA)
    return target


def catalog_completeness(providers_output: str) -> str:
    """根据暴露数量判断目录是否完整；无法证明完整时记为 partial。"""
    import re

    found = False
    for exposed, total in re.findall(r"(\d+)\s+exposed of\s+(\d+)", providers_output):
        found = True
        if int(exposed) < int(total):
            return "partial"
    if not found or re.search(r"\d+\s+more", providers_output):
        return "partial"
    return "complete"


def _replace_current(connection: sqlite3.Connection, groups: list[RouteGroup]) -> None:
    """用最新解析结果替换当前组、成员和规则。"""
    connection.execute("DELETE FROM rules")
    connection.execute("DELETE FROM members")
    connection.execute("DELETE FROM groups")
    for group in groups:
        reference = f"group/{group.id}"
        connection.execute(
            "INSERT INTO groups (group_id, name, routing, stays, classifier) VALUES (?, ?, ?, ?, ?)",
            (reference, group.name, group.routing, group.stays, group.classifier),
        )
        for position, member in enumerate(group.members, start=1):
            connection.execute(
                "INSERT INTO members (group_id, position, member_id, available, detail) VALUES (?, ?, ?, ?, ?)",
                (reference, position, member.id, int(member.available), member.detail),
            )
        for position, rule in enumerate(group.rules, start=1):
            connection.execute(
                "INSERT INTO rules (group_id, position, target, conditions_json) VALUES (?, ?, ?, ?)",
                (reference, position, rule.target, json.dumps(dict(rule.conditions), ensure_ascii=False)),
            )


def sync_observation(path: Path | None = None, spec_path: Path | None = None) -> str:
    """通过 magpie CLI 刷新镜像，并记录目录是否完整。"""
    target = initialize(path)
    spec = spec_path or default_spec_path()
    if not spec.is_file():
        raise MagpieError(f"找不到路由规范：{spec}")
    load_spec(spec)
    providers = checked_magpie(["providers"])
    groups_output = checked_magpie(["groups"])
    references = group_references(groups_output)
    if not references:
        raise MagpieError("未找到路由组，或组清单格式无法识别。")
    details = {reference: checked_magpie(["group", reference[6:]]) for reference in references}
    parsed = [parse_group(reference, details[reference]) for reference in references]
    completeness = catalog_completeness(providers)
    raw = "\n".join([providers, groups_output, *details.values()])
    now = datetime.now(timezone.utc).isoformat()
    with connect(target) as connection:
        _replace_current(connection, parsed)
        connection.execute(
            "INSERT INTO observations (taken_at, raw_text, completeness) VALUES (?, ?, ?)",
            (now, raw, completeness),
        )
        connection.execute(
            "INSERT INTO desired_routes (version, loaded_at, spec_json) VALUES (?, ?, ?)",
            (load_spec(spec).version, now, spec.read_text(encoding="utf-8")),
        )
    return completeness


def record_apply(path: Path, group_id: str, before: dict, after: dict, ok: bool, error: str) -> None:
    """追加一条应用或回滚结果，保留前后 JSON。"""
    with connect(initialize(path)) as connection:
        connection.execute(
            "INSERT INTO apply_log (taken_at, group_id, before_json, after_json, ok, error) VALUES (?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                group_id,
                json.dumps(before, ensure_ascii=False),
                json.dumps(after, ensure_ascii=False),
                int(ok),
                error,
            ),
        )


def latest_completeness(path: Path | None = None) -> str:
    """返回最近一次观测的完整性。"""
    with connect(path or database_path()) as connection:
        row = connection.execute("SELECT completeness FROM observations ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        raise MagpieError("还没有配置观测。")
    return row["completeness"]


def payload_from_group(group: RouteGroup) -> dict:
    """复用统一的组 JSON 结构。"""
    return group_payload(group)
