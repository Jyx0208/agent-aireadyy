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


def test_llm_config_store_migrates_legacy_and_supports_multi_profiles(tmp_path: Path) -> None:
    path = tmp_path / "llm_config.json"
    path.write_text(
        '{"api_key":"legacy-key","base_url":"https://api.openai.com/v1","model":"gpt-test","timeout":"60"}',
        encoding="utf-8",
    )
    store = LLMConfigStore(path)

    loaded = store.load()
    assert loaded is not None
    assert loaded["api_key"] == "legacy-key"
    profiles = store.list_profiles()
    assert len(profiles) == 1
    assert profiles[0]["api_key_set"] is True
    assert "api_key" not in profiles[0]
    assert profiles[0]["is_default"] is True

    second = store.upsert_profile(
        {
            "id": "grok",
            "label": "Grok",
            "api_key": "grok-key",
            "base_url": "https://api.x.ai/v1",
            "model": "grok-4",
            "timeout": "90",
        }
    )
    assert second["id"] == "grok"
    assert second["api_key_set"] is True

    secrets = store.get_profile_secrets("grok")
    assert secrets == {
        "api_key": "grok-key",
        "base_url": "https://api.x.ai/v1",
        "model": "grok-4",
        "timeout": "90",
    }

    store.upsert_profile({"id": "grok", "api_key": "", "model": "grok-4.1", "timeout": "120", "base_url": "https://api.x.ai/v1"})
    assert store.get_profile_secrets("grok")["api_key"] == "grok-key"
    assert store.get_profile_secrets("grok")["model"] == "grok-4.1"

    store.set_default_profile("grok")
    assert store.load()["model"] == "grok-4.1"
    assert store.delete_profile("default") is True
    assert len(store.list_profiles()) == 1
