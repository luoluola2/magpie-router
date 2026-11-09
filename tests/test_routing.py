"""路由解析、计划边界和审计逻辑的离线测试。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from magpie_router.magpie import MagpieError
from magpie_router.routing import RouteGroup, apply_order_plan, create_order_plan, group_references, load_plan, parse_group


GROUP_OUTPUT = """  Long  group/long
  routing   order
  stays     session
  models    1 grok/grok-4.7@500k  Grok · Grok 4.7 @ 500k
            2 group/light  routing group Light · order
  rules     1 tokens >= 200000 -> group/long
"""


def group(members=None):
    """构造测试路由组。"""
    return parse_group("group/long", GROUP_OUTPUT) if members is None else RouteGroup("long", "Long", tuple(members))


class RoutingTests(unittest.TestCase):
    """覆盖正常路由配置及其失败边界。"""

    def test_parse_group_keeps_order_and_nested_group(self):
        """保留成员原始顺序和嵌套组引用。"""
        actual = group()
        self.assertEqual([member.id for member in actual.members], ["grok/grok-4.7@500k", "group/light"])
        self.assertTrue(all(member.available for member in actual.members))

    def test_parse_group_rejects_unknown_shape(self):
        """缺少成员区时不把配置当成空组。"""
        with self.assertRaises(MagpieError):
            parse_group("group/long", "  Long  group/long\n")

    def test_group_references_only_reads_top_level_rows(self):
        """不会把箭头后的子组误当作新的顶层组。"""
        output = "  Work  group/work  order  group/implement -> group/light\n  Light  group/light  order\n"
        self.assertEqual(group_references(output), ["group/work", "group/light"])

    @patch("magpie_router.routing.read_group")
    def test_plan_requires_full_served_membership(self, read_group):
        """拒绝借排序计划静默删除成员。"""
        read_group.return_value = group()
        with self.assertRaises(MagpieError):
            create_order_plan("group/long", ["group/light"], "test")

    @patch("magpie_router.routing.read_group")
    def test_plan_contains_reverse_change(self, read_group):
        """计划保存基线与可回滚顺序。"""
        read_group.return_value = group()
        plan = create_order_plan("group/long", ["group/light", "grok/grok-4.7@500k"], "test")
        self.assertEqual(plan["rollback"]["target"], plan["baseline"])
        self.assertEqual(plan["rollback"]["baseline"], plan["target"])

    @patch("magpie_router.routing.checked_magpie")
    @patch("magpie_router.routing.read_group")
    def test_apply_rejects_drift_before_writing(self, read_group, checked):
        """实时配置漂移时不执行任何写命令。"""
        read_group.return_value = group()
        plan = {"group": "group/long", "baseline": ["group/light"], "target": ["grok/grok-4.7@500k"]}
        with self.assertRaises(MagpieError):
            apply_order_plan(plan)
        checked.assert_not_called()

    @patch("magpie_router.routing.read_group")
    def test_apply_rejects_malformed_plan_before_reading(self, read_group):
        """直接调用应用接口时也不能绕过计划校验。"""
        with self.assertRaises(MagpieError):
            apply_order_plan({})
        read_group.assert_not_called()

    @patch("magpie_router.routing.write_plan")
    @patch("magpie_router.routing.checked_magpie")
    @patch("magpie_router.routing.read_group")
    def test_apply_verifies_actual_order(self, read_group, checked, write_plan):
        """成功时传入参数数组并回读目标顺序。"""
        read_group.side_effect = [group(), group()]
        plan = {"group": "group/long", "baseline": [member.id for member in group().members], "target": [member.id for member in group().members]}
        with tempfile.TemporaryDirectory() as directory:
            with patch("magpie_router.routing.Path.cwd", return_value=Path(directory)):
                apply_order_plan(plan)
        checked.assert_called_once_with(["group", "set", "long", "models=grok/grok-4.7@500k,group/light"])
        self.assertTrue(write_plan.call_args.args[0]["verified"])

    def test_load_plan_rejects_membership_change(self):
        """加载外部 JSON 时重新限制为纯排序。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            path.write_text(json.dumps({"version": 1, "kind": "set-group-order", "group": "group/long", "baseline": ["a/a"], "target": ["b/b"]}), encoding="utf-8")
            with self.assertRaises(MagpieError):
                load_plan(path)

    def test_load_plan_rejects_missing_group(self):
        """无效组引用不会留给后续命令处理。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            path.write_text(json.dumps({"version": 1, "kind": "set-group-order", "baseline": ["a/a"], "target": ["a/a"]}), encoding="utf-8")
            with self.assertRaises(MagpieError):
                load_plan(path)


if __name__ == "__main__":
    unittest.main()
