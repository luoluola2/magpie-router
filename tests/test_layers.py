"""四层路由的结构约束。"""

import unittest
from dataclasses import replace

from magpie_router.desired import DesiredGroup, DesiredSpec, application_order, validate_spec
from magpie_router.magpie import MagpieError


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
