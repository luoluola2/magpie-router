"""v2 规则、配置镜像和应用回滚测试。"""

import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from magpie_router.apply import apply_group, apply_spec, rule_arguments
from magpie_router.magpie import MagpieError
from magpie_router.model import (
    Member,
    RouteGroup,
    Rule,
    group_references,
    normalize_conditions,
    normalize_expressions,
    normalize_use_effort,
    parse_group,
)
from magpie_router.paths import config_dir, database_path
from magpie_router.spec import (
    DesiredGroup,
    DesiredSpec,
    application_order,
    load_spec,
    validate_spec,
)
from magpie_router.store import (
    catalog_completeness,
    initialize,
    latest_completeness,
    sync_observation,
)

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

NEW_DETAIL = """  Probe  group/probe
  routing   order
  stays     turn
  models    1 workbuddy/hy4free
            2 codex/gpt-6-luna:low
  rules     1 effort ≥ xhigh → workbuddy/hy4free
            2 subagent · message contains "提交" → codex/gpt-6-luna:low, workbuddy/hy4free
            3 intent "a quick question" → workbuddy/hy4free at low
            4 system contains "foo" → codex/gpt-6-luna:low
  intent-sure 0.3  how sure the classifier must be of an intent for it to count
  classifier typesafe/jev-latest  tells which intent a message is
"""


def route_group(group_id, members, stays="session", classifier=""):
    """构造比较用的实时路由组。"""
    return RouteGroup(group_id, group_id, tuple(Member(member, True, "") for member in members), "order", stays, (), classifier)


class PathTests(unittest.TestCase):
    def test_defaults_are_repository_relative(self):
        """从其他工作目录调用时仍定位项目配置和默认数据库。"""
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as temporary:
            try:
                os.chdir(temporary)
                self.assertTrue((config_dir() / "routing.json").is_file())
                self.assertEqual(database_path(), original / "state" / "db" / "magpie-router.db")
            finally:
                os.chdir(original)


class SpecTests(unittest.TestCase):
    """锁定路由拓扑、规则语义与昂贵模型隔离。"""

    def test_v4_topology_and_strong_intent(self):
        """当前规范为 v4，入口末位拓扑与强化回退保持有效。"""
        spec = load_spec()
        self.assertEqual(spec.version, "v4")
        self.assertEqual(application_order(spec)[-1].id, "group/entry-work")
        groups = {group.id: group for group in spec.groups}
        self.assertIn("group/task-implement-strong", groups["group/entry-work"].members)
        self.assertEqual(groups["group/task-sota"].members, ("group/pool-sota",))
        with tempfile.TemporaryDirectory() as directory:
            old_spec = Path(directory) / "routing.json"
            old_spec.write_text('{"version":"v3","groups":[]}', encoding="utf-8")
            with self.assertRaises(MagpieError):
                load_spec(old_spec)

    def test_v4_work_precedence_and_combined_conditions(self):
        """保留四层入口规则顺序，并校验规范支持的 AND 条件。"""
        spec = load_spec()
        work = next(group for group in spec.groups if group.id == "group/entry-work")
        self.assertEqual(dict(work.rules[0].conditions), {"compact": "true"})
        self.assertTrue(any("group/task-implement-strong" in rule.targets for rule in work.rules))
        self.assertEqual(normalize_conditions({"images": True, "tokens": "500k"}), (
            ("images", "true"), ("tokens", "500000")))

    def test_validate_rejects_loop_missing_fallback_and_sota_reference(self):
        """v4 校验拒绝缺失成员、末位兜底和自动调用 SOTA。"""
        spec = load_spec()
        altered = tuple(replace(group, members=("group/entry-work",)) if group.id == "group/task-general" else group for group in spec.groups)
        with self.assertRaises(MagpieError):
            validate_spec(DesiredSpec("v4", altered))
        altered = tuple(replace(group, members=("group/pool-general",)) if group.id == "group/task-general" else group for group in spec.groups)
        with self.assertRaises(MagpieError):
            validate_spec(DesiredSpec("v4", altered))
        altered = tuple(replace(group, members=("group/pool-sota", "group/pool-fallback")) if group.id == "group/task-general" else group for group in spec.groups)
        with self.assertRaises(MagpieError):
            validate_spec(DesiredSpec("v4", altered))
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

    def test_group_references_only_reads_top_level_rows(self):
        """不会把箭头后的子组误当作新的顶层组。"""
        output = "  Work  group/work  order  group/implement -> group/light\n  Light  group/light   order\n"
        self.assertEqual(group_references(output), ["group/work", "group/light"])

    def test_parse_rejects_unknown_conditions(self):
        """未知条件明确失败，不静默丢失语义。"""
        with self.assertRaises(MagpieError):
            parse_group("group/architect", DETAIL.replace("images", "quota < 10"))
        with self.assertRaises(MagpieError):
            normalize_conditions({"tokens": "400k", "unknown": "1"})

    def test_parse_when_expressions_use_effort_and_intent_sure(self):
        """新版 CLI 的 when=、use-effort 和组级阈值可完整回读。"""
        parsed = parse_group("group/probe", NEW_DETAIL)
        self.assertEqual(parsed.intent_sure, "0.3")
        self.assertEqual(parsed.classifier, "typesafe/jev-latest")
        self.assertEqual(parsed.rules[0].conditions, (("effort", "xhigh"),))
        self.assertEqual(parsed.rules[1].conditions, ())
        self.assertEqual(parsed.rules[1].expressions, ("message contains 提交", "subagent"))
        self.assertEqual(parsed.rules[1].targets, ("codex/gpt-6-luna:low", "workbuddy/hy4free"))
        self.assertEqual(parsed.rules[2].use_effort, "low")
        self.assertEqual(parsed.rules[3].expressions, ("system contains foo",))
        self.assertEqual(rule_arguments(parsed.rules[1]), [
            "use=codex/gpt-6-luna:low,workbuddy/hy4free",
            "when=message contains 提交", "when=subagent",
        ])
        self.assertIn("use-effort=low", rule_arguments(parsed.rules[2], parsed.classifier))
        with self.assertRaises(MagpieError):
            normalize_expressions(["quota < 10"])
        with self.assertRaises(MagpieError):
            normalize_use_effort("ultra")

    def test_rule_arguments_and_scenarios(self):
        """规则命令包含 effort 和意图分类器；离线覆盖关键分流场景。"""
        rule = Rule("a/a,b/b", normalize_conditions({"images": True, "tokens": "500k"}))
        self.assertEqual(rule_arguments(rule), ["use=a/a,b/b", "images", "tokens=500000"])
        intent = Rule("group/review", (("intent", "评审"),))
        self.assertEqual(rule_arguments(intent, "group/classify"), [
            "use=group/review", "intent=评审", "classifier=group/classify"])
        with self.assertRaises(MagpieError):
            rule_arguments(intent)
        spec = load_spec()
        work = next(group for group in spec.groups if group.id == "group/entry-work")
        self.assertTrue(any("message contains 强化实现" in expression for rule in work.rules for expression in rule.expressions))
        self.assertFalse(any("tokens" in dict(rule.conditions) for rule in work.rules))


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
                self.assertEqual(sync_observation(path, Path("config/routing.json")), "partial")
            with patch("magpie_router.store.checked_magpie", side_effect=self.commands("group/implement")):
                sync_observation(path, Path("config/routing.json"))
            self.assertEqual(latest_completeness(path), "complete")
            connection = sqlite3.connect(path)
            try:
                members = [row[0] for row in connection.execute("SELECT member_id FROM members ORDER BY position")]
            finally:
                connection.close()
            self.assertEqual(members, ["group/implement"])

    @patch("magpie_router.store.context_windows", return_value={"group/work": 1000000})
    def test_sync_persists_intent_sure_expressions_and_effort(self, windows):
        """镜像保留组级阈值、when 表达式和规则推理强度。"""
        detail = """  Work  group/work
  routing   order
  stays     turn
  models    1 group/implement  child
  rules     1 message contains "强化实现" → group/implement-strong,group/implement
            2 intent "问候" → group/general at low
  intent-sure 0.5  how sure the classifier must be
  classifier typesafe/jev-latest  tells which intent a message is
"""

        def respond(arguments):
            if arguments == ["providers"]:
                return "1 exposed of 1 from example"
            if arguments == ["groups"]:
                return "  Work  group/work  order  \n"
            if arguments == ["group", "work"]:
                return detail
            raise AssertionError(arguments)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "router.db"
            with patch("magpie_router.store.checked_magpie", side_effect=respond):
                sync_observation(path, Path("config/routing.json"))
            connection = sqlite3.connect(path)
            try:
                sure = connection.execute(
                    "SELECT intent_sure FROM groups WHERE group_id='group/work'"
                ).fetchone()[0]
                rows = connection.execute(
                    "SELECT target, conditions_json, expressions_json, use_effort FROM rules ORDER BY position"
                ).fetchall()
            finally:
                connection.close()
        self.assertEqual(sure, "0.5")
        self.assertEqual(rows[0][0], "group/implement-strong,group/implement")
        self.assertEqual(rows[0][1], "{}")
        self.assertEqual(rows[0][2], '["message contains 强化实现"]')
        self.assertEqual(rows[0][3], "")
        self.assertEqual(rows[1][2], "[]")
        self.assertEqual(rows[1][3], "low")

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

    @patch("magpie_router.apply.begin_apply", return_value=1)
    @patch("magpie_router.apply.finish_apply")
    @patch("magpie_router.apply.checked_magpie")
    @patch("magpie_router.apply.read_group")
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

    @patch("magpie_router.apply.begin_apply", return_value=1)
    @patch("magpie_router.apply.finish_apply")
    @patch("magpie_router.apply.checked_magpie")
    @patch("magpie_router.apply.read_group")
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

    @patch("magpie_router.apply.sync_observation")
    @patch("magpie_router.apply.apply_group")
    @patch("magpie_router.apply.load_spec")
    @patch("magpie_router.apply.check_exposed_models")
    @patch("magpie_router.apply.read_all_groups", return_value=[])
    def test_later_group_is_not_attempted_after_failure(self, read_all, check, load, apply, sync):
        """一个组失败后停止后续组，整轮同步错误也会报告。"""
        spec = load_spec()
        load.return_value = spec
        ordered = application_order(spec)
        failing_id = ordered[1].id

        def fail(desired, db_path, **kwargs):
            if desired.id == failing_id:
                raise MagpieError("boom")
            return "applied"

        apply.side_effect = fail
        with patch("magpie_router.apply.snapshot_dir", return_value=Path(self.directory.name)), self.assertRaises(MagpieError):
            apply_spec(self.db, Path("unused.json"))
        self.assertEqual([call.args[0].id for call in apply.call_args_list], [ordered[0].id, failing_id])

    def test_sources_do_not_reference_gateway_credentials(self):
        """过渡实现不接入网关密钥。"""
        for path in Path("src/magpie_router").glob("*.py"):
            self.assertNotIn("MAGPIE_GATEWAY", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
