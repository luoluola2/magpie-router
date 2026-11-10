"""v2 规则、配置镜像和应用回滚测试。"""

import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from magpie_router.desired import (
    DesiredGroup, DesiredSpec, application_order, load_spec, validate_spec,
)
from magpie_router.magpie import MagpieError
from magpie_router.rollout import apply_group, apply_spec, rule_arguments
from magpie_router.routing import Member, RouteGroup, Rule, normalize_conditions, parse_group
from magpie_router.store import catalog_completeness, initialize, latest_completeness, sync_observation


DETAIL = """  Architect  group/architect
  routing   order
  stays     session
  models    1 opencode-go/kimi-k3.1-pro  Kimi
            2 codex/gpt-6.1-sol  Sol
  rules     1 tokens ≥ 400000 · images → opencode-go/kimi-k3.1-pro, codex/gpt-6.1-sol
            2 effort ≥ xhigh → codex/gpt-6.1-sol
            3 intent "评审" → opencode-go/kimi-k3.1-pro
  classifier group/classify  tells which intent a message is
"""


def route_group(group_id, members, stays="session", classifier=""):
    """构造比较用的实时路由组。"""
    return RouteGroup(group_id, group_id, tuple(Member(member, True, "") for member in members), "order", stays, (), classifier)


class SpecTests(unittest.TestCase):
    """锁定路由拓扑、规则语义与昂贵模型隔离。"""

    def test_v3_topology_and_sota_isolation(self):
        """二十四组可拓扑排序；SOTA 与按量 API 不进入自动链。"""
        spec = load_spec(Path("policies/routing-v3.json"))
        self.assertEqual(spec.version, "v3")
        self.assertEqual(len(spec.groups), 24)
        self.assertEqual(application_order(spec)[-1].id, "group/work")
        groups = {group.id: group for group in spec.groups}
        self.assertEqual(groups["group/work"].classifier, "group/classify")
        self.assertNotIn("group/fallback", groups["group/work"].members)
        self.assertEqual(groups["group/pool-sota"].members, (
            "codex/gpt-6-astra", "app-yylx-io-10275/claude-opus-5-5"))
        for group in spec.groups:
            if group.layer != "task" or group.id == "group/sota":
                continue
            self.assertNotIn("group/sota", " ".join(group.members))
            self.assertNotIn("astra", " ".join(group.members))
            self.assertNotIn("opus", " ".join(group.members))
            self.assertNotIn("deepseek/deepseek-flash", " ".join(group.members))
            self.assertEqual(group.members[-1], "group/fallback")

    def test_combined_conditions_and_work_precedence(self):
        """规则支持 AND 条件，意图优先于图片和长度。"""
        spec = load_spec(Path("policies/routing-v3.json"))
        work = {group.id: group for group in spec.groups}["group/work"]
        conditions = [dict(rule.conditions) for rule in work.rules]
        self.assertEqual(conditions[0], {"compact": "true"})
        self.assertIn({"intent": "实现功能、修改业务代码或定位并修复缺陷，包括必要测试和按截图实现界面"}, conditions)
        self.assertEqual(conditions[-2], {"images": "true"})
        self.assertEqual(conditions[-1], {"tokens": "500000"})
        self.assertEqual([rule.target for rule in work.rules][1], "group/review")
        self.assertEqual(normalize_conditions({"images": True, "tokens": "500k"}), (
            ("images", "true"), ("tokens", "500000")))

    def test_validate_rejects_loop_missing_fallback_and_sota_reference(self):
        """配置校验拒绝环、缺少末位兜底和自动调用 SOTA。"""
        spec = load_spec(Path("policies/routing-v3.json"))
        groups = {group.id: group for group in spec.groups}
        with self.assertRaises(MagpieError):
            validate_spec(DesiredSpec("v3", spec.groups[:-1] + (
                replace(groups["group/work"], members=("group/work",)),)))
        invalid = replace(groups["group/light"], members=groups["group/light"].members[:-1])
        with self.assertRaises(MagpieError):
            validate_spec(DesiredSpec("v3", tuple(invalid if group.id == invalid.id else group for group in spec.groups)))
        invalid_work = replace(groups["group/work"], members=("group/sota",) + groups["group/work"].members)
        with self.assertRaises(MagpieError):
            validate_spec(DesiredSpec("v3", spec.groups[:-1] + (invalid_work,)))

    def test_parse_combined_multi_target_rule(self):
        """CLI 规则可完整回读 AND 条件和有序多目标。"""
        parsed = parse_group("group/architect", DETAIL)
        self.assertEqual(parsed.rules[0], Rule(
            "opencode-go/kimi-k3.1-pro,codex/gpt-6.1-sol",
            (("images", "true"), ("tokens", "400000")),
        ))
        self.assertEqual(parsed.rules[1].conditions, (("effort", "xhigh"),))
        self.assertEqual(parsed.rules[2].conditions, (("intent", "评审"),))
        self.assertEqual(parsed.classifier, "group/classify")

    def test_parse_rejects_unknown_conditions(self):
        """未知条件明确失败，不静默丢失语义。"""
        with self.assertRaises(MagpieError):
            parse_group("group/architect", DETAIL.replace("images", "quota < 10"))
        with self.assertRaises(MagpieError):
            normalize_conditions({"tokens": "400k", "unknown": "1"})

    def test_rule_arguments_and_scenarios(self):
        """规则命令包含 effort 和意图分类器；离线覆盖关键分流场景。"""
        rule = Rule("a/a,b/b", normalize_conditions({"images": True, "tokens": "500k"}))
        self.assertEqual(rule_arguments(rule), ["use=a/a,b/b", "images", "tokens=500000"])
        intent = Rule("group/review", (("intent", "评审"),))
        self.assertEqual(rule_arguments(intent, "group/classify"), [
            "use=group/review", "intent=评审", "classifier=group/classify"])
        with self.assertRaises(MagpieError):
            rule_arguments(intent)
        spec = load_spec(Path("policies/routing-v3.json"))
        work = {group.id: group for group in spec.groups}["group/work"]
        rules = [dict(rule.conditions) for rule in work.rules]
        self.assertLess(rules.index({"images": "true"}), rules.index({"tokens": "500000"}))
        self.assertIn("按截图实现界面", str(rules))
        self.assertIn("识别图片", str(rules))


class StoreTests(unittest.TestCase):
    """覆盖镜像初始化和可替换观测。"""

    @patch("magpie_router.store.context_windows", return_value={"group/work": 1000000})
    def test_catalog_partial_and_repeat_init(self, windows):
        """截断目录标记为 partial，重复初始化不破坏观测。"""
        self.assertEqual(catalog_completeness("1 exposed of 2 from example"), "partial")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "router.db"
            initialize(path)
            initialize(path)
            with patch("magpie_router.store.checked_magpie", side_effect=self.commands("group/light")):
                self.assertEqual(sync_observation(path, Path("policies/routing.json")), "partial")
            with patch("magpie_router.store.checked_magpie", side_effect=self.commands("group/implement")):
                sync_observation(path, Path("policies/routing.json"))
            self.assertEqual(latest_completeness(path), "complete")
            connection = sqlite3.connect(path)
            try:
                members = [row[0] for row in connection.execute("SELECT member_id FROM members ORDER BY position")]
            finally:
                connection.close()
            self.assertEqual(members, ["group/implement"])

    def commands(self, member):
        """生成一次同步所需的 magpie 输出。"""
        def respond(arguments):
            if arguments == ["providers"]:
                return "1 exposed of 2 from example" if member == "group/light" else "1 exposed of 1 from example"
            if arguments == ["groups"]:
                return "  Work  group/work  order  \n"
            if arguments == ["group", "work"]:
                return f"  Work  group/work\n  routing   order\n  stays     turn\n  models    1 {member}  child\n"
            raise AssertionError(arguments)
        return respond


class ApplyTests(unittest.TestCase):
    """用假 magpie 验证应用、回读失败和中途停止。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db = Path(self.directory.name) / "router.db"

    def tearDown(self):
        self.directory.cleanup()

    def desired(self, members):
        """构造一个只比较成员的目标组。"""
        return DesiredGroup("group/architect", "order", "session", tuple(members))

    @patch("magpie_router.rollout.begin_apply", return_value=1)
    @patch("magpie_router.rollout.finish_apply")
    @patch("magpie_router.rollout.checked_magpie")
    @patch("magpie_router.rollout.read_group")
    def test_apply_success_records_both_states(self, read_group, checked, finish, begin):
        """成功应用时记录前后 JSON，且不读取网关密钥。"""
        state = {"group": route_group("architect", ["a/a"])}

        def read(reference, **kwargs):
            """返回假 magpie 的当前组，接受容量回读选项。"""
            return state["group"]

        def command(arguments):
            self.assertNotIn("MAGPIE_GATEWAY_KEY", os.environ.get("MAGPIE_GATEWAY_KEY", "absent-marker"))
            state["group"] = route_group("architect", ["b/b"])

        read_group.side_effect = read
        checked.side_effect = command
        self.assertEqual(apply_group(self.desired(["b/b"]), self.db), "applied")
        self.assertEqual(finish.call_args.args[2]["members"], ["b/b"])
        self.assertTrue(finish.call_args.args[3])
        checked.assert_called_once()

    @patch("magpie_router.rollout.begin_apply", return_value=1)
    @patch("magpie_router.rollout.finish_apply")
    @patch("magpie_router.rollout.checked_magpie")
    @patch("magpie_router.rollout.read_group")
    def test_mismatch_restores_previous_group(self, read_group, checked, finish, begin):
        """回读不一致时恢复原成员并报告失败。"""
        reads = [
            route_group("architect", ["a/a"]),
            route_group("architect", ["wrong/wrong"]),
            route_group("architect", ["a/a"]),
            route_group("architect", ["a/a"]),
            route_group("architect", ["a/a"]),
        ]
        read_group.side_effect = reads
        with self.assertRaisesRegex(MagpieError, "members"):
            apply_group(self.desired(["b/b"]), self.db)
        self.assertEqual(checked.call_args_list[0].args[0][3], "models=b/b")
        self.assertEqual(checked.call_args_list[1].args[0][3], "models=a/a")
        self.assertFalse(finish.call_args.args[3])

    @patch("magpie_router.rollout.sync_observation")
    @patch("magpie_router.rollout.apply_group")
    @patch("magpie_router.rollout.load_spec")
    @patch("magpie_router.rollout.check_exposed_models")
    @patch("magpie_router.rollout.read_all_groups", return_value=[])
    def test_later_group_is_not_attempted_after_failure(self, read_all, check, load, apply, sync):
        """一个组失败后停止后续组，整轮同步错误也会报告。"""
        load.return_value = DesiredSpec("v2", (
            DesiredGroup("group/fallback", "order", "turn", ("a/a",)),
            DesiredGroup("group/light", "order", "turn", ("group/fallback",)),
            DesiredGroup("group/work", "order", "turn", ("group/light",)),
        ))

        def fail(desired, db_path, **kwargs):
            if desired.id == "group/light":
                raise MagpieError("boom")
            return "applied"

        apply.side_effect = fail
        with self.assertRaises(MagpieError):
            apply_spec(self.db, Path("unused.json"))
        self.assertEqual([call.args[0].id for call in apply.call_args_list], ["group/fallback", "group/light"])

    def test_sources_do_not_reference_gateway_credentials(self):
        """过渡实现不接入网关密钥。"""
        for path in Path("magpie_router").glob("*.py"):
            self.assertNotIn("MAGPIE_GATEWAY", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
