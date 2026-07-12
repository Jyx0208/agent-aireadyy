from __future__ import annotations

from pathlib import Path

from agent.web.llm_config_store import LLMConfigStore


def test_llm_config_store_round_trips_and_deletes_private_config(tmp_path: Path) -> None:
    path = tmp_path / ".agent_secrets" / "llm_config.json"
    store = LLMConfigStore(path)
    config = {
        "api_key": "secret-key",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-pro",
        "timeout": "1200",
    }

    store.save(config)

    assert store.load() == config
    assert path.exists()
    assert not path.with_suffix(".tmp").exists()
    assert store.delete() is True
    assert store.load() is None
    assert store.delete() is False


def test_llm_config_store_treats_corrupt_or_incomplete_files_as_unconfigured(tmp_path: Path) -> None:
    path = tmp_path / "llm_config.json"
    store = LLMConfigStore(path)
    path.write_text("not json", encoding="utf-8")
    assert store.load() is None

    path.write_text('{"api_key":"secret"}', encoding="utf-8")
    assert store.load() is None
