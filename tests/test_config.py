"""路由规范、配置镜像和应用回滚测试。"""

import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from magpie_router.desired import DesiredGroup, DesiredSpec, _validate_v1, load_spec
from magpie_router.magpie import MagpieError
from magpie_router.rollout import apply_group, apply_spec, rule_arguments
from magpie_router.routing import Member, RouteGroup, Rule, parse_group
from magpie_router.store import catalog_completeness, initialize, latest_completeness, sync_observation


DETAIL = """  Architect  group/architect
  routing   order
  stays     session
  models    1 opencode-go/kimi-k3.1-pro  Kimi
            2 app-yylx-io-10275/claude-opus-5-5  Opus
  rules     1 tokens ≥ 500k → group/long
            2 images → group/architect
            3 intent "评审" → group/review
  classifier group/light  tells which intent a message is
"""


def route_group(group_id, members, stays="session", classifier=""):
    """构造比较用的实时路由组。"""
    return RouteGroup(group_id, group_id, tuple(Member(member, True, "") for member in members), "order", stays, (), classifier)


class SpecTests(unittest.TestCase):
    """锁定已经确认的 v1 编排。"""

    def test_v1_order_rules_and_classifier(self):
        """规范包含 500k、七条规则、Light 默认入口和不同的评审顺序。"""
        spec = load_spec()
        self.assertEqual([group.id for group in spec.groups], [
            "group/fallback", "group/architect", "group/review", "group/tests", "group/long",
            "group/compact", "group/implement", "group/light", "group/work"])
        groups = {group.id: group for group in spec.groups}
        work = spec.groups[-1]
        self.assertEqual(work.members[0], "group/light")
        self.assertEqual(work.classifier, "group/light")
        self.assertEqual(work.rules[1].conditions, (("tokens", "500000"),))
        self.assertEqual(len(work.rules), 7)
        self.assertNotEqual(groups["group/architect"].members, groups["group/review"].members)
        self.assertNotIn("build-fast", " ".join(groups["group/tests"].members))

    def test_fallback_is_leaf_and_last_in_every_task_group(self):
        """兜底跨供应商、无环，且只从二级任务组末尾进入。"""
        spec = load_spec()
        groups = {group.id: group for group in spec.groups}
        fallback = groups["group/fallback"]
        self.assertEqual(fallback.members[0], "workbuddy/hy4free")
        self.assertGreater(len({member.split("/", 1)[0] for member in fallback.members}), 1)
        self.assertTrue(all(not member.startswith("group/") for member in fallback.members))
        self.assertNotIn("group/fallback", groups["group/work"].members)
        self.assertNotIn("group/openrouter-free", groups)
        for reference in groups["group/work"].members:
            self.assertEqual(groups[reference].members[-1], "group/fallback")
            self.assertEqual(groups[reference].members.count("group/fallback"), 1)

    def test_rejects_direct_fallback_and_reverse_reference(self):
        """拒绝 Work 直连兜底，以及兜底反向指向任务组。"""
        spec = load_spec()
        work = spec.groups[-1]
        invalid_work = replace(work, members=work.members + ("group/fallback",))
        with self.assertRaises(MagpieError):
            _validate_v1(spec.groups[:-1] + (invalid_work,))
        with self.assertRaises(MagpieError):
            _validate_v1(spec.groups[:-1] + (replace(work, classifier="group/fallback"),))
        invalid_fallback = replace(spec.groups[0], members=("group/light",))
        with self.assertRaises(MagpieError):
            _validate_v1((invalid_fallback,) + spec.groups[1:])

    def test_rejects_task_without_final_fallback(self):
        """缺少二级组末位兜底时给出配置错误。"""
        spec = load_spec()
        architect = spec.groups[1]
        invalid = replace(architect, members=architect.members[:-1])
        with self.assertRaises(MagpieError):
            _validate_v1((spec.groups[0], invalid) + spec.groups[2:])

    def test_intent_rule_carries_classifier(self):
        """含意图的规则在建立时同时提供分类器。"""
        rule = Rule("group/review", (("intent", "评审"),))
        self.assertEqual(rule_arguments(rule, "group/light"), [
            "use=group/review", "intent=评审", "classifier=group/light"])
        with self.assertRaises(MagpieError):
            rule_arguments(rule)

    def test_parse_rules_normalizes_tokens(self):
        """实时规则文本能还原 compact、token、图像和意图。"""
        parsed = parse_group("group/architect", DETAIL)
        self.assertEqual(parsed.rules[0].conditions, (("tokens", "500000"),))
        self.assertEqual(parsed.rules[1].conditions, (("images", "true"),))
        self.assertEqual(dict(parsed.rules[2].conditions)["intent"], "评审")
        self.assertEqual(parsed.classifier, "group/light")


class StoreTests(unittest.TestCase):
    """覆盖镜像初始化和可替换观测。"""

    def test_catalog_partial_and_repeat_init(self):
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

    @patch("magpie_router.rollout.record_apply")
    @patch("magpie_router.rollout.checked_magpie")
    @patch("magpie_router.rollout.read_group")
    def test_apply_success_records_both_states(self, read_group, checked, record):
        """成功应用时记录前后 JSON，且不读取网关密钥。"""
        state = {"group": route_group("architect", ["a/a"])}

        def read(reference):
            return state["group"]

        def command(arguments):
            self.assertNotIn("MAGPIE_GATEWAY_KEY", os.environ.get("MAGPIE_GATEWAY_KEY", "absent-marker"))
            state["group"] = route_group("architect", ["b/b"])

        read_group.side_effect = read
        checked.side_effect = command
        self.assertEqual(apply_group(self.desired(["b/b"]), self.db), "applied")
        before, after = record.call_args.args[2], record.call_args.args[3]
        self.assertEqual(before["members"], ["a/a"])
        self.assertEqual(after["members"], ["b/b"])
        checked.assert_called_once()

    @patch("magpie_router.rollout.record_apply")
    @patch("magpie_router.rollout.checked_magpie")
    @patch("magpie_router.rollout.read_group")
    def test_mismatch_restores_previous_group(self, read_group, checked, record):
        """回读不一致时恢复原成员并报告失败。"""
        reads = [
            route_group("architect", ["a/a"]),
            route_group("architect", ["wrong/wrong"]),
            route_group("architect", ["a/a"]),
            route_group("architect", ["a/a"]),
        ]
        read_group.side_effect = reads
        with self.assertRaises(MagpieError):
            apply_group(self.desired(["b/b"]), self.db)
        self.assertEqual(checked.call_args_list[0].args[0][3], "models=b/b")
        self.assertEqual(checked.call_args_list[1].args[0][3], "models=a/a")
        self.assertFalse(record.call_args.args[4])

    @patch("magpie_router.rollout.sync_observation")
    @patch("magpie_router.rollout.apply_group")
    @patch("magpie_router.rollout.load_spec")
    def test_later_group_is_not_attempted_after_failure(self, load, apply, sync):
        """一个组失败后停止后续组。"""
        load.return_value = DesiredSpec("v1", (
            DesiredGroup("group/architect", "order", "session", ("a/a",)),
            DesiredGroup("group/review", "order", "session", ("b/b",)),
            DesiredGroup("group/tests", "order", "session", ("c/c",)),
        ))

        def fail(desired, db_path):
            if desired.id == "group/review":
                raise MagpieError("boom")
            return "applied"

        apply.side_effect = fail
        with self.assertRaises(MagpieError):
            apply_spec(self.db, Path("unused.json"))
        self.assertEqual([call.args[0].id for call in apply.call_args_list], ["group/architect", "group/review"])

    def test_sources_do_not_reference_gateway_credentials(self):
        """过渡实现不接入网关密钥。"""
        for path in Path("magpie_router").glob("*.py"):
            self.assertNotIn("MAGPIE_GATEWAY", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
