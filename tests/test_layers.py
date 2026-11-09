"""四层路由的结构约束。"""

import unittest
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from dataclasses import replace

from magpie_router.desired import DesiredGroup, DesiredSpec, application_order, validate_spec
from magpie_router.magpie import MagpieError
from magpie_router.store import _replace_current, initialize
from magpie_router.routing import RouteGroup, Member, Rule, topology_findings


class LayerTests(unittest.TestCase):
    """验证池复用、层级边界和手动资源隔离。"""

    def spec(self):
        """构造共享模型的最小四层配置。"""
        return DesiredSpec("v3", (
            DesiredGroup("group/fallback", "order", "turn", ("vendor/free",), layer="pool"),
            DesiredGroup("group/pool-sota", "order", "turn", ("vendor/opus",), layer="pool"),
            DesiredGroup("group/pool-a", "smart", "auto", ("vendor/free",), layer="pool"),
            DesiredGroup("group/classify", "order", "turn", ("group/pool-a", "group/fallback"), layer="task"),
            DesiredGroup("group/light", "order", "turn", ("group/pool-a", "group/fallback"), layer="task"),
            DesiredGroup("group/sota", "order", "turn", ("group/pool-sota",), layer="task"),
            DesiredGroup("group/work", "order", "turn", ("group/light",), classifier="group/classify", layer="entry"),
        ))

    def test_shared_pool_and_order(self):
        """共享资源池合法且依赖先于调用方。"""
        spec = self.spec()
        validate_spec(spec)
        order = [group.id for group in application_order(spec)]
        self.assertLess(order.index("group/pool-a"), order.index("group/light"))
        self.assertEqual(order[-1], "group/work")

    def test_forbidden_references(self):
        """跨层、同层、缺失兜底和手动模型不能进入自动链。"""
        spec = self.spec()
        for group_id, changes in (
            ("group/light", {"members": ("vendor/model", "group/fallback")}),
            ("group/light", {"members": ("group/pool-a",)}),
            ("group/pool-a", {"members": ("group/fallback",)}),
            ("group/pool-a", {"members": ("vendor/opus",)}),
            ("group/light", {"members": ("group/pool-sota", "group/fallback")}),
            ("group/work", {"members": ("group/sota",)}),
        ):
            groups = tuple(replace(group, **changes) if group.id == group_id else group for group in spec.groups)
            with self.subTest(group_id=group_id, changes=changes), self.assertRaises(MagpieError):
                validate_spec(DesiredSpec("v3", groups))

    def test_observed_layer_defaults_to_unknown(self):
        """镜像只标记已知规范 ID，不臆测未识别组的层级。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "router.db"
            initialize(path)
            actual = RouteGroup("other", "Other", (Member("vendor/model", True, ""),), "order", "turn")
            with closing(sqlite3.connect(path)) as connection:
                _replace_current(connection, [actual], {"group/known": "pool"})
                self.assertEqual(connection.execute("SELECT layer FROM groups").fetchone()[0], "unknown")

    def test_live_topology_checks_members_rules_and_classifier(self):
        """实时任务直连模型、资源池引用组和规则跨层均必须报告。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routing.json"
            path.write_text(Path("policies/routing.json").read_text(encoding="utf-8"), encoding="utf-8")
            groups = [
                RouteGroup("light", "Light", (
                    Member("workbuddy/hy4free", True, ""), Member("group/fallback", True, ""),
                ), "order", "turn"),
                RouteGroup("pool-reason", "Pool Reason", (
                    Member("group/fallback", True, ""),
                ), "smart", "auto"),
                RouteGroup("work", "Work", (
                    Member("group/light", True, ""),
                ), "order", "turn", (
                    Rule("group/pool-long", (("tokens", "400000"),)),
                ), "group/pool-classify"),
            ]
            findings = [finding for finding in topology_findings(groups, path) if finding.group in {
                "group/light", "group/pool-reason", "group/work",
            }]
            messages = [finding.message for finding in findings]
            self.assertEqual(len(findings), 4)
            self.assertTrue(all(finding.severity == "error" for finding in findings))
            self.assertTrue(any("workbuddy/hy4free" in message for message in messages))
            self.assertTrue(any("group/pool-classify" in message for message in messages))
