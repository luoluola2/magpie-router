"""CLI 定位与执行的测试。"""

import unittest
from unittest.mock import patch

from magpie_router.magpie import MagpieError, find_magpie


class MagpieTests(unittest.TestCase):
    """确保路径配置不会造成隐式机器依赖。"""

    @patch("magpie_router.magpie.Path.is_file", return_value=False)
    def test_invalid_explicit_cli_is_an_error(self, is_file):
        """显式指定错误路径时立即报告。"""
        with patch.dict("os.environ", {"MAGPIE_CLI": "missing-magpie"}):
            with self.assertRaises(MagpieError):
                find_magpie()


if __name__ == "__main__":
    unittest.main()
