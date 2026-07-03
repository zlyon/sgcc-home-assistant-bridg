"""LLM provider configuration helpers.

The project uses OpenAI-compatible chat/completions for captcha solving.
`LLM_*` is the canonical configuration, while `ARK_*` aliases are accepted for
users migrating from upstream Volcengine Ark examples.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_MODEL = "doubao-seed-2-0-pro-260215"


@dataclass(frozen=True)
class LlmConfig:
    api_key: str
    base_url: str
    model: str
    source: str = "LLM_*"


def _first_env(*names: str, default: str = "") -> tuple[str, str]:
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip():
            return str(value).strip(), name
    return default, "default"


def load_llm_config() -> LlmConfig:
    """Load OpenAI-compatible LLM config with Volcengine Ark aliases.

    Canonical variables:
    - LLM_API_KEY
    - LLM_BASE_URL
    - LLM_MODEL

    Compatibility aliases:
    - ARK_API_KEY / VOLCENGINE_ARK_API_KEY
    - ARK_BASE_URL / VOLCENGINE_ARK_BASE_URL
    - ARK_MODEL / VOLCENGINE_ARK_MODEL
    """

    api_key, key_source = _first_env(
        "LLM_API_KEY",
        "ARK_API_KEY",
        "VOLCENGINE_ARK_API_KEY",
    )
    base_url, _ = _first_env(
        "LLM_BASE_URL",
        "ARK_BASE_URL",
        "VOLCENGINE_ARK_BASE_URL",
        default=DEFAULT_BASE_URL,
    )
    model, _ = _first_env(
        "LLM_MODEL",
        "ARK_MODEL",
        "VOLCENGINE_ARK_MODEL",
        default=DEFAULT_MODEL,
    )
    return LlmConfig(api_key=api_key, base_url=base_url, model=model, source=key_source)
