"""只读盘点、路由差异与显式配置应用的命令行接口。"""

from __future__ import annotations

import argparse
import sqlite3
import sys

from .apply import apply_spec, sync_and_diff
from .compare import audit
from .magpie import MagpieError
from .model import inspect
from .store import initialize, sync_observation


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(prog="magpie-router", description="维护 magpie 路由配置。")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect", help="只读盘点 magpie")
    commands.add_parser("audit", help="审计路由组可用性")

    database = commands.add_parser("db", help="本地配置镜像")
    database_commands = database.add_subparsers(dest="db_command", required=True)
    database_commands.add_parser("init", help="创建或补齐 SQLite 镜像")
    database_commands.add_parser("sync", help="只读同步 magpie 配置")

    route = commands.add_parser("route", help="比较或应用 config/routing.json")
    route_commands = route.add_subparsers(dest="route_command", required=True)
    route_commands.add_parser("diff", help="显示规范与实时路由的差异")
    route_apply = route_commands.add_parser("apply", help="应用已批准的规范差异")
    route_apply.add_argument("--yes", action="store_true", help="确认应用 config/routing.json")
    return parser


def main(arguments: list[str] | None = None) -> int:
    """执行命令并返回进程状态；未授权或非零发现均不会被伪装为成功。"""
    args = build_parser().parse_args(arguments)
    try:
        if args.command == "inspect":
            for name, output in inspect().items():
                print(f"\n## {name}\n{output.rstrip()}")
        elif args.command == "audit":
            findings = audit()
            for finding in findings:
                print(f"{finding.severity.upper()} {finding.group}: {finding.message}")
            print(f"\n{len(findings)} 项发现。")
            return 1 if any(finding.severity == "error" for finding in findings) else 0
        elif args.command == "db":
            if args.db_command == "init":
                print(f"配置镜像：{initialize()}")
            else:
                print(f"已同步，目录{sync_observation()}。")
        elif args.command == "route":
            if args.route_command == "diff":
                lines = sync_and_diff()
                print("\n".join(lines))
                return 1 if any(line.startswith(("CHANGE", "MISSING", "RETIRED")) for line in lines) else 0
            if not args.yes:
                raise MagpieError("应用路由规范需要 --yes。")
            apply_spec()
        return 0
    except (MagpieError, OSError, sqlite3.Error) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2
