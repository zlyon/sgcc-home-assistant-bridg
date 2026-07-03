import os
import unittest
from unittest.mock import patch

from sgcc_ha_bridge.llm_config import DEFAULT_BASE_URL, DEFAULT_MODEL, load_llm_config


class LlmConfigTestCase(unittest.TestCase):
    def test_llm_variables_take_priority(self):
        with patch.dict(os.environ, {
            "LLM_API_KEY": "llm-key",
            "LLM_BASE_URL": "https://llm.example/v1",
            "LLM_MODEL": "llm-model",
            "ARK_API_KEY": "ark-key",
            "ARK_MODEL": "ark-model",
        }, clear=True):
            config = load_llm_config()
        self.assertEqual(config.api_key, "llm-key")
        self.assertEqual(config.base_url, "https://llm.example/v1")
        self.assertEqual(config.model, "llm-model")
        self.assertEqual(config.source, "LLM_API_KEY")

    def test_ark_aliases_are_supported(self):
        with patch.dict(os.environ, {
            "ARK_API_KEY": "ark-key",
            "ARK_MODEL": "ep-20260601092754-example",
        }, clear=True):
            config = load_llm_config()
        self.assertEqual(config.api_key, "ark-key")
        self.assertEqual(config.base_url, DEFAULT_BASE_URL)
        self.assertEqual(config.model, "ep-20260601092754-example")
        self.assertEqual(config.source, "ARK_API_KEY")

    def test_defaults_keep_openai_compatible_endpoint(self):
        with patch.dict(os.environ, {}, clear=True):
            config = load_llm_config()
        self.assertEqual(config.api_key, "")
        self.assertEqual(config.base_url, DEFAULT_BASE_URL)
        self.assertEqual(config.model, DEFAULT_MODEL)


if __name__ == "__main__":
    unittest.main()
