"""验证 v4 规则的配置映射；固定分类结果不代表模型分类质量。"""

import unittest
from unittest.mock import patch

from magpie_router.cli import main
from magpie_router.desired import load_spec
from magpie_router.rollout import rule_arguments


def selected_target(group, *, matched_intent=None, images=False, compact=False, message=""):
    """按固定分类结果检查策略中的规则顺序，不调用 Magpie 或 Jev。"""
    for rule in group.rules:
        conditions = dict(rule.conditions)
        if rule.expressions and any(
            expression.rsplit(" ", 1)[-1] not in message
            for expression in rule.expressions
        ):
            continue
        if conditions.get("compact") == "true" and not compact:
            continue
        if "intent" in conditions and conditions["intent"] != matched_intent:
            continue
        if conditions.get("images") == "true" and not images:
            continue
        if conditions or rule.expressions:
            return rule.targets[0]
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
            ("跨模块接口或数据契约重构", "group/task-implement-strong"),
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
        self.assertEqual(selected_target(work, message="请强化实现这个功能"), "group/task-implement-strong")

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
                self.assertEqual(len(group.rules), 2)
                self.assertEqual(group.intent_sure, "0.3")
                expression_rule, rule = group.rules
                self.assertEqual(len(expression_rule.expressions), 1)
                self.assertNotEqual(group.members[0], target)
                self.assertEqual(selected_target(group), group.members[0])
                self.assertEqual(selected_target(group, matched_intent=dict(rule.conditions)["intent"]), target)
                self.assertIn("classifier=typesafe/jev-latest", rule_arguments(rule, group.classifier))

    def test_new_rule_conditions_compile_and_strong_chain_falls_back(self):
        """新条件编译为 CLI 参数，强化规则显式声明普通实现为回退。"""
        work = self.groups["group/entry-work"]
        self.assertIn("group/task-implement-strong", work.members)
        self.assertEqual(work.intent_sure, "0.5")
        strong_rules = [rule for rule in work.rules if "group/task-implement-strong" in rule.targets]
        self.assertEqual(len(strong_rules), 2)
        for rule in strong_rules:
            self.assertEqual(rule.targets, ("group/task-implement-strong", "group/task-implement"))
        override = next(rule for rule in strong_rules if rule.expressions)
        self.assertIn("when=message contains 强化实现", rule_arguments(override, work.classifier))
        chitchat = next(rule for rule in work.rules if rule.use_effort)
        self.assertEqual(chitchat.use_effort, "low")
        self.assertIn("use-effort=low", rule_arguments(chitchat, work.classifier))
        task = self.groups["group/task-general"]
        self.assertIn(f"when={task.rules[0].expressions[0]}", rule_arguments(task.rules[0], task.classifier))

    @patch("magpie_router.cli.sync_and_diff")
    def test_diff_exit_distinguishes_retired_and_unknown(self, diff):
        """退役组使差异检查失败，未知组提示但不自动阻断。"""
        diff.return_value = ["OK group/entry-work", "RETIRED group/task-long"]
        self.assertEqual(main(["route", "diff"]), 1)
        diff.return_value = ["OK group/entry-work", "UNKNOWN group/personal"]
        self.assertEqual(main(["route", "diff"]), 0)


if __name__ == "__main__":
    unittest.main()
