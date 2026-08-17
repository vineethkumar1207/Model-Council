import json
import os
from pathlib import Path

from council import CouncilEngine
from renderer import TerminalRenderer
from session_store import SessionStore
from providers.ollama import OllamaProvider
from providers.gemini import GeminiProvider

def load_config(root_path: Path):
    with open(root_path / "config.json", "r", encoding="utf-8") as f:
        return json.load(f)

def build_engine(root_path: Path):
    config = load_config(root_path)
    store = SessionStore(root_path / config["session_dir"])
    renderer = TerminalRenderer()

    providers = {
        "ollama": OllamaProvider(config["ollama_base_url"]),
    }

    if os.getenv("GEMINI_API_KEY"):
        providers["gemini"] = GeminiProvider(
            os.environ["GEMINI_API_KEY"],
            config.get("gemini_models", []),
        )

    engine = CouncilEngine(config, providers, store, renderer=renderer)
    return engine, store, renderer

def parse_role_override(raw: str, selected: list):
    lookup = {model["id"].lower(): model["id"] for model in selected}
    groups = {"analyst": [], "comparator": [], "challenger": [], "synthesizer": []}
    for chunk in raw.split(";"):
        if ":" not in chunk:
            continue
        role, values = chunk.split(":", 1)
        role = role.strip().lower()
        if role not in groups:
            continue
        for value in values.split(","):
            item = value.strip().lower()
            if not item:
                continue
            if item in lookup:
                groups[role].append(lookup[item])
            else:
                groups[role].append(value.strip())
    return {
        "experimental": True,
        "reason": "User-provided role mapping.",
        **groups,
    }
