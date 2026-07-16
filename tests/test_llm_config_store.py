from __future__ import annotations

from pathlib import Path

import pytest

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


def test_first_named_profile_becomes_default(tmp_path: Path) -> None:
    store = LLMConfigStore(tmp_path / "llm_config.json")
    created = store.upsert_profile(
        {
            "id": "judge-a",
            "label": "Judge A",
            "api_key": "secret",
            "base_url": "https://example.com/v1",
            "model": "model-a",
            "timeout": "30",
        }
    )
    assert created["is_default"] is True
    assert store.list_profiles()[0]["is_default"] is True
    assert store.load()["model"] == "model-a"


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


def test_expert_profile_identity_metadata_round_trips_without_public_secrets(tmp_path: Path) -> None:
    store = LLMConfigStore(tmp_path / "llm_config.json")
    created = store.upsert_profile(
        {
            "id": "claude-opus",
            "label": "Claude Opus",
            "api_key": "anthropic-secret",
            "base_url": "https://api.anthropic.com",
            "model": "claude-opus-4-8",
            "timeout": "300",
            "provider": "anthropic",
            "requested_model_id": "claude-opus-4-8",
            "resolved_model_id": "claude-opus-4-8-20260701",
            "model_family": "claude",
            "endpoint_identity": "anthropic:production",
            "routing_profile_id": "anthropic-primary",
            "identity_verification": "provider_attested",
            "enabled": True,
            "capabilities": ["structured_output", "adaptive_thinking"],
        }
    )

    assert created["provider"] == "anthropic"
    assert created["resolved_model_id"] == "claude-opus-4-8-20260701"
    assert created["model_family"] == "claude"
    assert created["identity_verification"] == "provider_attested"
    assert created["capabilities"] == ["structured_output", "adaptive_thinking"]
    assert "api_key" not in created
    assert "anthropic-secret" not in str(store.list_profiles())

    private = store.get_profile("claude-opus", include_secrets=True)
    assert private is not None
    assert private["api_key"] == "anthropic-secret"
    assert private["provider"] == "anthropic"
    assert private["routing_profile_id"] == "anthropic-primary"


def test_legacy_profile_identity_defaults_are_explicitly_unverified(tmp_path: Path) -> None:
    path = tmp_path / "llm_config.json"
    path.write_text(
        '{"api_key":"legacy-key","base_url":"https://proxy.example/v1","model":"alias-model","timeout":"60"}',
        encoding="utf-8",
    )
    profile = LLMConfigStore(path).list_profiles()[0]

    assert profile["provider"] == "openai_compatible"
    assert profile["requested_model_id"] == "alias-model"
    assert profile["resolved_model_id"] is None
    assert profile["model_family"] == "alias-model"
    assert profile["endpoint_identity"] == "https://proxy.example/v1"
    assert profile["identity_verification"] == "unverified"
    assert profile["enabled"] is True


def test_profile_store_rejects_self_declared_verified_identity(tmp_path: Path) -> None:
    store = LLMConfigStore(tmp_path / "llm_config.json")
    with pytest.raises(ValueError, match="verified_identity_requires_runtime_attestation"):
        store.upsert_profile(
            {
                "id": "unsafe-verified",
                "api_key": "secret",
                "base_url": "https://example.com/v1",
                "model": "alias",
                "timeout": "30",
                "provider": "proxy",
                "model_family": "claimed-family",
                "resolved_model_id": "claimed-model",
                "identity_verification": "verified",
            }
        )


def test_public_profile_strips_credentials_from_endpoint_identity(tmp_path: Path) -> None:
    store = LLMConfigStore(tmp_path / "llm_config.json")
    created = store.upsert_profile(
        {
            "id": "safe-endpoint",
            "api_key": "stored-secret",
            "base_url": "https://user:pass@proxy.example/v1?api_key=query-secret#fragment",
            "model": "alias-model",
            "timeout": "30",
            "endpoint_identity": "https://route-user:route-pass@proxy.example/v1?token=route-secret#fragment",
        }
    )

    assert created["base_url"] == "https://proxy.example/v1"
    assert created["endpoint_identity"] == "https://proxy.example/v1"
    assert "pass" not in str(store.list_profiles())
    assert "secret" not in str(store.list_profiles())

    private = store.get_profile("safe-endpoint", include_secrets=True)
    assert private is not None
    assert private["api_key"] == "stored-secret"
    assert private["base_url"].startswith("https://user:pass@")
