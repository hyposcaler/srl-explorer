from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

TOPOLOGY: dict[str, dict[str, str]] = {
    "leaf1": {"address": "172.80.80.11", "role": "leaf"},
    "leaf2": {"address": "172.80.80.12", "role": "leaf"},
    "leaf3": {"address": "172.80.80.13", "role": "leaf"},
    "spine1": {"address": "172.80.80.21", "role": "spine"},
    "spine2": {"address": "172.80.80.22", "role": "spine"},
}

CREDENTIALS = {"username": "admin", "password": "NokiaSrl1!"}


@dataclass
class Config:
    openai_api_key: str
    openai_model: str = "gpt-4o"
    prometheus_url: str = "http://localhost:9090"
    yang_models_dir: Path = field(default_factory=lambda: Path("./srlinux-yang-models/srlinux-yang-models"))
    yang_cache_dir: Path = field(default_factory=lambda: Path(".cache"))
    logs_dir: Path = field(default_factory=lambda: Path("./logs"))
    context_window: int = 128_000


def get_config() -> Config:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is required")

    return Config(
        openai_api_key=api_key,
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        prometheus_url=os.environ.get("PROMETHEUS_URL", "http://localhost:9090"),
        yang_models_dir=Path(os.environ.get("YANG_MODELS_DIR", "./srlinux-yang-models/srlinux-yang-models")),
        yang_cache_dir=Path(os.environ.get("YANG_CACHE_DIR", ".cache")),
        logs_dir=Path(os.environ.get("SRL_EXPLORER_LOGS_DIR", "./logs")),
        context_window=int(os.environ.get("CONTEXT_WINDOW", "128000")),
    )
