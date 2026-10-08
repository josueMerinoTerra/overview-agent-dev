"""Offline tests for the shared settings in overview_agent/config.py."""
from __future__ import annotations

import unittest

from overview_agent import config


class ConfigTests(unittest.TestCase):
    def test_project_root_is_the_repository_root(self):
        self.assertTrue((config.PROJECT_ROOT / "requirements.txt").is_file())
        self.assertTrue((config.PROJECT_ROOT / "overview_agent" / "config.py").is_file())

    def test_dotenv_is_read_from_the_project_root(self):
        self.assertEqual(config.load_dotenv.__defaults__, (config.PROJECT_ROOT / ".env",))


if __name__ == "__main__":
    unittest.main()
