import tempfile
import unittest
from pathlib import Path

from shiftbot.config import ConfigError, load_env, read_config


EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.toml"


class ConfigTests(unittest.TestCase):
    def read(self, content):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            path.write_text(content, encoding="utf-8")
            return read_config(str(path))

    def test_example_config_is_structurally_valid_without_secrets(self):
        cfg = read_config(str(EXAMPLE))
        self.assertEqual(len(cfg.outlets), 2)
        self.assertEqual(cfg.interval, 30)
        self.assertFalse(cfg.at_deadline_is_on_time)

    def test_common_server_needs_separate_department_filters(self):
        content = EXAMPLE.read_text().replace('department_id = "11111111-1111-4111-8111-111111111111"', '')
        with self.assertRaises(ConfigError):
            self.read(content)

    def test_plain_http_is_rejected_for_credentials(self):
        content = EXAMPLE.read_text().replace("https://your-company", "http://your-company")
        with self.assertRaises(ConfigError):
            self.read(content)

    def test_all_registers_requires_explicit_list(self):
        content = EXAMPLE.read_text().replace("require_all_registers = false", "require_all_registers = true", 1)
        with self.assertRaises(ConfigError):
            self.read(content)

    def test_environment_file_never_executes_shell(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("IIKO_TEST_VALUE='$(uname) `$NOT_A_COMMAND`'\n", encoding="utf-8")
            with patch.dict("os.environ", {}, clear=True):
                load_env(str(path))
                self.assertEqual(os.environ["IIKO_TEST_VALUE"], "$(uname) `$NOT_A_COMMAND`")


if __name__ == "__main__":
    unittest.main()
