"""Startup config: debug defaults off, and a dev secret cannot boot production."""

import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]


class ProductionSettingsTests(SimpleTestCase):
    def _import_settings(self, **env):
        child = os.environ.copy()
        for key in ("DJANGO_DEBUG", "DJANGO_SECRET_KEY", "LOG_LEVEL"):
            child.pop(key, None)
        child["DJANGO_SETTINGS_MODULE"] = "config.settings"
        child.update(env)
        return subprocess.run(
            [sys.executable, "-c", "import config.settings"],
            cwd=ROOT,
            env=child,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_debug_defaults_off_and_rejects_dev_secrets(self):
        missing = self._import_settings()
        self.assertNotEqual(missing.returncode, 0, missing.stderr)
        self.assertIn("DJANGO_SECRET_KEY", missing.stderr)

        for secret in (
            "",
            "   ",
            "change-me",
            " dev-only-not-for-production ",
            "build-only-not-used-at-runtime",
        ):
            result = self._import_settings(DJANGO_DEBUG="0", DJANGO_SECRET_KEY=secret)
            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertIn("DJANGO_SECRET_KEY", result.stderr)

    def test_debug_off_accepts_a_unique_secret(self):
        code = (
            "import config.settings\n"
            "assert config.settings.DEBUG is False\n"
            "assert config.settings.SECRET_KEY == 'a-real-production-secret'\n"
            "assert config.settings.LOG_LEVEL == 'INFO'\n"
        )
        child = os.environ.copy()
        for key in ("DJANGO_DEBUG", "DJANGO_SECRET_KEY", "LOG_LEVEL"):
            child.pop(key, None)
        child.update(
            {
                "DJANGO_SETTINGS_MODULE": "config.settings",
                "DJANGO_DEBUG": "0",
                "DJANGO_SECRET_KEY": "a-real-production-secret",
                "LOG_LEVEL": "verbose",
            }
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=child,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_debug_on_allows_a_missing_secret_and_logs_to_stdout(self):
        code = (
            "import logging.config\n"
            "import sys\n"
            "import config.settings\n"
            "assert config.settings.DEBUG is True\n"
            "assert config.settings.LOG_LEVEL == 'INFO'\n"
            "assert config.settings.LOGGING['handlers']['console']['stream'] == 'ext://sys.stdout'\n"
            "logging.config.dictConfig(config.settings.LOGGING)\n"
            "assert any(getattr(handler, 'stream', None) is sys.stdout for handler in logging.getLogger().handlers)\n"
        )
        child = os.environ.copy()
        for key in ("DJANGO_DEBUG", "DJANGO_SECRET_KEY", "LOG_LEVEL"):
            child.pop(key, None)
        child["DJANGO_DEBUG"] = "1"
        child["DJANGO_SETTINGS_MODULE"] = "config.settings"
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=child,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
