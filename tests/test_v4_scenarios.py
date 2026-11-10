"""验证 v4 规则的配置映射；固定分类结果不代表模型分类质量。"""

import unittest
from unittest.mock import patch

from magpie_router.cli import main
from magpie_router.desired import load_spec
from magpie_router.rollout import rule_arguments


def selected_target(group, *, matched_intent=None, images=False, compact=False):
    """按固定分类结果检查策略中的规则顺序，不调用 Magpie 或 Jev。"""
    for rule in group.rules:
        conditions = dict(rule.conditions)
        if conditions.get("compact") == "true" and compact:
            return rule.target
        if conditions.get("intent") == matched_intent and matched_intent is not None:
            return rule.target
        if conditions.get("images") == "true" and images:
            return rule.target
    return group.members[0]


class V4ScenarioTests(unittest.TestCase):
    """使用预先确定的意图命中验证目标配置，不推断真实请求分类。"""

    @classmethod
    def setUpClass(cls):
        """加载当前规范以检查实际意图和候选顺序。"""
        cls.groups = {group.id: group for group in load_spec().groups}

    def test_work_intent_order_and_targets(self):
        """明确意图先于历史图片，压缩先于所有意图。"""
        work = self.groups["group/entry-work"]
        scenarios = [
            ("主要交付问题与风险", "group/task-review"),
            ("主要交付方案决策", "group/task-architect"),
            ("仅编写或修复常规测试", "group/task-tests"),
            ("按截图实现界面", "group/task-implement"),
            ("截图理解", "group/task-vision"),
            ("提取要点", "group/task-compact"),
            ("解释技术概念", "group/task-general"),
            ("寒暄、随口交流", "group/task-general"),
        ]
        for fragment, target in scenarios:
            matches = [
                dict(rule.conditions)["intent"] for rule in work.rules
                if fragment in dict(rule.conditions).get("intent", "")
            ]
            with self.subTest(fragment=fragment):
                self.assertEqual(len(matches), 1)
                self.assertEqual(selected_target(work, matched_intent=matches[0], images=True), target)
        self.assertEqual(selected_target(work, images=True, compact=True), "group/task-compact")

    def test_classifier_failure_uses_deterministic_rules_or_default(self):
        """分类失败或无匹配时保留图片和默认路径，不声称验证了远端超时。"""
        work = self.groups["group/entry-work"]
        for outcome in ("失败", "超时", "无匹配", "无上下文的继续"):
            with self.subTest(outcome=outcome):
                self.assertEqual(selected_target(work), "group/task-general")
                self.assertEqual(selected_target(work, images=True), "group/task-vision")

    def test_task_rules_compile_and_change_default(self):
        """四个任务入口的意图规则确实选择不同于默认池的候选。"""
        expected = {
            "group/task-general": "group/pool-reason",
            "group/task-architect": "group/pool-build",
            "group/task-review": "group/pool-build",
            "group/task-tests": "group/pool-build",
        }
        for reference, target in expected.items():
            group = self.groups[reference]
            with self.subTest(reference=reference):
                self.assertEqual(group.classifier, "typesafe/jev-latest")
                self.assertEqual(len(group.rules), 1)
                rule = group.rules[0]
                self.assertNotEqual(group.members[0], target)
                self.assertEqual(selected_target(group), group.members[0])
                self.assertEqual(selected_target(group, matched_intent=dict(rule.conditions)["intent"]), target)
                self.assertIn("classifier=typesafe/jev-latest", rule_arguments(rule, group.classifier))

    @patch("magpie_router.cli.sync_and_diff")
    def test_diff_exit_distinguishes_retired_and_unknown(self, diff):
        """退役组使差异检查失败，未知组提示但不自动阻断。"""
        diff.return_value = ["OK group/entry-work", "RETIRED group/task-long"]
        self.assertEqual(main(["route", "diff"]), 1)
        diff.return_value = ["OK group/entry-work", "UNKNOWN group/personal"]
        self.assertEqual(main(["route", "diff"]), 0)


if __name__ == "__main__":
    unittest.main()
