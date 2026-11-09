"""只读盘点与显式单组路由变更的命令行接口。"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from .magpie import MagpieError
from .rollout import apply_spec, sync_and_diff
from .routing import apply_order_plan, audit, create_order_plan, inspect, load_plan, write_plan
from .store import initialize, sync_observation


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(prog="magpie-router", description="维护 magpie 路由配置。")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect", help="只读盘点 magpie")
    commands.add_parser("audit", help="审计路由组可用性")

    plan = commands.add_parser("plan", help="生成不执行的路由计划")
    plan_commands = plan.add_subparsers(dest="plan_command", required=True)
    order = plan_commands.add_parser("set-order", help="对单个组的完整成员排序")
    order.add_argument("group", help="目标组，例如 group/work")
    order.add_argument("members", nargs="+", help="实时成员全集的新顺序")
    order.add_argument("--reason", default="Agent 提案，经用户批准后应用", help="写入计划的变更理由")
    order.add_argument("--output", type=Path, required=True, help="计划 JSON 路径，建议位于 .local/")

    apply = commands.add_parser("apply", help="核对基线后应用一个单组计划")
    apply.add_argument("plan", type=Path, help="计划 JSON 路径")
    apply.add_argument("--yes", action="store_true", help="确认已获得本次配置变更批准")

    database = commands.add_parser("db", help="本地配置镜像")
    database_commands = database.add_subparsers(dest="db_command", required=True)
    database_commands.add_parser("init", help="创建或补齐 SQLite 镜像")
    database_commands.add_parser("sync", help="只读同步 magpie 配置")

    route = commands.add_parser("route", help="比较或应用 policies/routing.json")
    route_commands = route.add_subparsers(dest="route_command", required=True)
    route_commands.add_parser("diff", help="显示规范与实时路由的差异")
    route_apply = route_commands.add_parser("apply", help="按规范逐组应用路由")
    route_apply.add_argument("--yes", action="store_true", help="确认应用 policies/routing.json")
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
        elif args.command == "plan":
            plan = create_order_plan(args.group, args.members, args.reason)
            write_plan(plan, args.output)
            print(f"计划已写入 {args.output}，尚未修改 magpie。")
            print(f"当前：{plan['baseline']}\n目标：{plan['target']}")
        elif args.command == "apply":
            if not args.yes:
                raise MagpieError("应用配置需要本次明确批准，并传入 --yes。")
            actual, report = apply_order_plan(load_plan(args.plan))
            print(f"已验证 {actual.id}：{[member.id for member in actual.members]}")
            print(f"应用记录与回滚计划：{report}")
        elif args.command == "db":
            if args.db_command == "init":
                print(f"配置镜像：{initialize()}")
            else:
                print(f"已同步，目录{sync_observation()}。")
        elif args.command == "route":
            if args.route_command == "diff":
                lines = sync_and_diff()
                print("\n".join(lines))
                return 1 if any(line.startswith(("CHANGE", "MISSING")) for line in lines) else 0
            if not args.yes:
                raise MagpieError("应用路由规范需要 --yes。")
            apply_spec()
        return 0
    except (MagpieError, OSError, sqlite3.Error) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2
