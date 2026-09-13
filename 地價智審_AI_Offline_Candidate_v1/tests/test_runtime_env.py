import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.runtime_env import load_environment, frontend_config


class RuntimeEnvironmentTests(unittest.TestCase):
    def test_precedence_and_literal_values(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"PORT": "9000"}, clear=True):
            root = Path(folder)
            (root / ".env.shared.example").write_text("PORT=8090\nAPP_MODE=local\n", encoding="utf-8")
            (root / ".env").write_text("PORT=8091\nAPP_MODE=mock\nRAG_API_TOKEN='$(literal)'\n", encoding="utf-8")
            load_environment(root)
            self.assertEqual(os.environ["PORT"], "9000")
            self.assertEqual(os.environ["APP_MODE"], "mock")
            self.assertEqual(os.environ["RAG_API_TOKEN"], "$(literal)")

    def test_frontend_does_not_expose_secrets(self):
        with patch.dict(os.environ, {"APP_MODE": "production", "API_BASE_URL": "https://api.example.test/",
                                     "RAG_API_TOKEN": "secret", "AWS_SECRET_ACCESS_KEY": "secret"}, clear=True):
            self.assertEqual(frontend_config(), {"MODE": "production", "API_BASE_URL": "https://api.example.test",
                                                 "LOCAL_BACKEND": False})

    def test_invalid_production_config_fails(self):
        for url in ("", "http://example.test", "https://REPLACE_WITH_API_GATEWAY_INVOKE_URL"):
            with patch.dict(os.environ, {"APP_MODE": "production", "API_BASE_URL": url}, clear=True):
                with self.assertRaises(ValueError):
                    frontend_config()

    def test_invalid_env_does_not_expose_value(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            root = Path(folder)
            (root / ".env").write_text("TOKEN='secret", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"Unclosed environment value: .env:1"):
                load_environment(root)


if __name__ == "__main__":
    unittest.main()
