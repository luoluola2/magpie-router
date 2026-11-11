"""上下文元数据、配置持久化和失败恢复的离线测试。"""

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from magpie_router.contexts import context_windows
from magpie_router.desired import DesiredGroup, diff_lines, load_spec, same_configuration
from magpie_router.magpie import MagpieError
from magpie_router.rollout import apply_group, restore_group
from magpie_router.routing import Member, RouteGroup, Rule, group_payload, read_group
from magpie_router.store import initialize


class ContextTests(unittest.TestCase):
    """验证元数据读取、声明值及容量变更的回滚边界。"""

    def actual(self, window=256000):
        """构造带已观测容量的单模型组。"""
        return RouteGroup(
            "architect", "Architect", (Member("a/a", True, ""),),
            "order", "session", context=window,
        )

    def desired(self):
        """构造只改变公布窗口的目标组。"""
        return DesiredGroup("group/architect", "order", "session", ("a/a",), context=1000000)

    @patch("magpie_router.contexts.urlopen")
    def test_metadata_and_invalid_responses(self, open_url):
        """组容量必须是唯一正整数，错误不能作为空窗口继续应用。"""
        for data in (
            {"data": [{"id": "group/architect", "context_window": 1000000}]},
            {"data": [{"id": "group/architect", "context_length": 272000}]},
        ):
            open_url.return_value = io.BytesIO(json.dumps(data).encode())
            self.assertGreater(context_windows()["group/architect"], 0)
        for data in (
            {}, {"data": []}, {"data": [None]},
            {"data": [{"id": "group/architect", "context_window": True}]},
            {"data": [{"id": "group/architect", "context_window": -1}]},
            {"data": [{"id": "group/a", "context_window": 1}] * 2},
        ):
            open_url.return_value = io.BytesIO(json.dumps(data).encode())
            with self.assertRaises(MagpieError):
                context_windows()
        open_url.side_effect = OSError("unavailable")
        with self.assertRaises(MagpieError):
            context_windows()

    @patch("magpie_router.routing.context_windows", return_value={"group/architect": 1000000})
    @patch("magpie_router.routing.checked_magpie")
    def test_group_read_requires_requested_metadata(self, command, windows):
        """CLI 不含容量时使用元数据补充，缺失组必须拒绝。"""
        command.return_value = "  Architect  group/architect\n  models    1 a/a  A\n"
        self.assertEqual(read_group("group/architect", include_context=True).context, 1000000)
        windows.return_value = {}
        with self.assertRaises(MagpieError):
            read_group("group/architect", include_context=True)

    def test_context_validation_and_diff(self):
        """规范拒绝非法窗口；diff 和日志保留有效公布值。"""
        raw = json.loads(Path("policies/routing.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            for value in (True, 0, -1, "1m", None):
                raw["groups"][0]["context"] = value
                path.write_text(json.dumps(raw), encoding="utf-8")
                with self.assertRaises(MagpieError):
                    load_spec(path)
        self.assertFalse(same_configuration(self.actual(), self.desired()))
        self.assertTrue(same_configuration(self.actual(1000000), self.desired()))
        self.assertFalse(same_configuration(self.actual(1048576), self.desired()))
        self.assertFalse(same_configuration(self.actual(500000), self.desired()))
        self.assertEqual(group_payload(self.actual())["context"], 256000)
        spec = load_spec(Path("policies/routing-v3.json"))
        actual = self.actual()
        self.assertTrue(any("公布上下文" in line for line in diff_lines([actual], spec)))

    def test_windows_and_implement_baseline(self):
        """长规则仍在公布容量内，Implement 显式保留两个 max 基准路径。"""
        groups = {group.id: group for group in load_spec(Path("policies/routing-v3.json")).groups}
        self.assertEqual(groups["group/work"].context, 1000000)
        self.assertEqual(groups["group/sota"].context, 272000)
        self.assertEqual(groups["group/classify"].context, 256000)
        self.assertIn("workbuddy/deepseek-v4.1-flash:max", groups["group/pool-build-credit"].members)
        self.assertIn("opencode-go/deepseek-v4.1-flash:max", groups["group/pool-build"].members)
        self.assertNotIn("workbuddy/hy4free", groups["group/pool-build"].members)
        for group in groups.values():
            for rule in group.rules:
                threshold = dict(rule.conditions).get("tokens")
                if threshold:
                    self.assertLess(int(threshold), group.context or 0)

    def test_existing_database_migration_preserves_rows(self):
        """旧镜像追加容量、阈值和规则条件列，重复初始化不删除旧数据。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "CREATE TABLE groups (group_id TEXT PRIMARY KEY, name TEXT, routing TEXT, stays TEXT, classifier TEXT)"
                )
                connection.execute("INSERT INTO groups VALUES ('group/a','A','order','turn','')")
                connection.execute(
                    "CREATE TABLE rules (group_id TEXT, position INTEGER, target TEXT, conditions_json TEXT)"
                )
                connection.execute("INSERT INTO rules VALUES ('group/a',1,'x/y','{}')")
                connection.commit()
            initialize(path)
            initialize(path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT group_id, layer, context, intent_sure FROM groups").fetchone(), ("group/a", "unknown", None, ""))
                self.assertEqual(
                    connection.execute("SELECT target, conditions_json, expressions_json, use_effort FROM rules").fetchone(),
                    ("x/y", "{}", "[]", ""),
                )

    @patch("magpie_router.rollout.checked_magpie")
    @patch("magpie_router.rollout.read_group")
    def test_context_apply_and_logs(self, read, command):
        """容量专用改动不重建规则；持久日志保留应用前后容量。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite"
            read.side_effect = [self.actual(), self.actual(1000000)]
            self.assertEqual(apply_group(self.desired(), path), "applied")
            command.assert_called_once_with(["group", "set", "architect", "context=1000000"])
            with closing(sqlite3.connect(path)) as connection:
                before, after, ok = connection.execute("SELECT before_json,after_json,ok FROM apply_log").fetchone()
            self.assertEqual(json.loads(before)["context"], 256000)
            self.assertEqual(json.loads(after)["context"], 1000000)
            self.assertEqual(ok, 1)

    @patch("magpie_router.rollout.checked_magpie")
    @patch("magpie_router.rollout.read_group")
    def test_context_failure_restores_and_verifies(self, read, command):
        """回读容量错误时恢复旧公布值，恢复失败不能报告成功。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite"
            read.side_effect = [self.actual(), self.actual(500000), self.actual(500000), self.actual()]
            with self.assertRaisesRegex(MagpieError, "已回滚并停止"):
                apply_group(self.desired(), path)
            self.assertTrue(any("context=256000" in call.args[0] for call in command.call_args_list))
            read.side_effect = [self.actual(), self.actual(500000), self.actual(500000), self.actual(500000)]
            with self.assertRaisesRegex(MagpieError, "回滚失败"):
                apply_group(self.desired(), path)

    @patch("magpie_router.rollout.checked_magpie")
    @patch("magpie_router.rollout.read_group")
    def test_restore_uses_actual_remaining_rules(self, read, command):
        """规则重建中途失败时按实际剩余条数删除，随后恢复完整基线。"""
        rule = Rule("a/a", (("images", "true"),))
        before = replace(self.actual(), rules=(rule, Rule("a/a", (("tokens", "400000"),))))
        read.return_value = replace(self.actual(), rules=(rule,))
        restore_group(before)
        commands = [call.args[0] for call in command.call_args_list]
        self.assertEqual(commands[0], ["group", "rule", "rm", "architect", "1"])
        self.assertEqual(sum(arguments[:3] == ["group", "rule", "add"] for arguments in commands), 2)

    @patch("magpie_router.rollout.checked_magpie")
    @patch("magpie_router.rollout.read_group")
    def test_context_drift_stops_before_write(self, read, command):
        """有效容量在基线后变化时，拒绝覆盖其他操作。"""
        read.return_value = self.actual(500000)
        with self.assertRaisesRegex(MagpieError, "已变化"):
            apply_group(self.desired(), baseline=self.actual())
        command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
