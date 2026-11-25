"""Testes do LLMClient, LLMConfig, GeminiLLMClient (3 chaves) e FakeLLMClient (Etapa 5.1)."""

import pytest

from backend.agentes import (
    DEFAULT_GEMINI_MODEL,
    FakeLLMClient,
    GeminiLLMClient,
    LLMClient,
    LLMConfig,
    LLMExecutionError,
)
from backend.schemas import StrictModel


class _SampleOutput(StrictModel):
    items: tuple[str, ...] = ()


def test_fake_llm_client_implements_protocol_and_records_calls() -> None:
    payload = _SampleOutput(items=())
    client = FakeLLMClient([payload, "{invalido}", LLMExecutionError("falha simulada")])
    assert isinstance(client, LLMClient)
    assert client.model_name == "fake-llm"

    first = client.generate(
        system_prompt="sys",
        user_prompt="usr",
        response_schema=_SampleOutput,
    )
    assert first.content == payload.model_dump_json()
    assert len(client.calls) == 1
    assert client.calls[0].schema_name == "_SampleOutput"

    second = client.generate(
        system_prompt="sys2",
        user_prompt="usr2",
        response_schema=_SampleOutput,
    )
    assert second.content == "{invalido}"

    with pytest.raises(LLMExecutionError):
        client.generate(
            system_prompt="sys3",
            user_prompt="usr3",
            response_schema=_SampleOutput,
        )

    with pytest.raises(LLMExecutionError):
        client.generate(
            system_prompt="sys4",
            user_prompt="usr4",
            response_schema=_SampleOutput,
        )


def test_llm_config_from_env_collects_three_fallback_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("LLM_API_KEY", "key-1")
    monkeypatch.setenv("LLM_API_KEY_2", "key-2")
    monkeypatch.setenv("LLM_API_KEY_3", "key-3")

    config = LLMConfig.from_env()
    assert config.model == "gemini-2.5-flash"
    assert config.api_keys == ("key-1", "key-2", "key-3")


def test_llm_config_uses_default_model_and_skips_blank_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "  ")
    monkeypatch.setenv("LLM_API_KEY_2", "key-backup")
    monkeypatch.delenv("LLM_API_KEY_3", raising=False)

    config = LLMConfig.from_env()
    assert config.model == DEFAULT_GEMINI_MODEL
    assert config.api_keys == ("key-backup",)


def test_llm_config_raises_when_no_api_key_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("LLM_API_KEY", "LLM_API_KEY_2", "LLM_API_KEY_3"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValueError):
        LLMConfig.from_env()


def test_gemini_client_rotates_through_three_keys_on_failure() -> None:
    tried: list[str] = []

    def fake_transport(
        api_key: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: type,
    ) -> str:
        tried.append(api_key)
        if api_key in ("key-1", "key-2"):
            raise LLMExecutionError(f"cota esgotada em {api_key}")
        return '{"items": []}'

    config = LLMConfig(model="gemini-2.5-flash", api_keys=("key-1", "key-2", "key-3"))
    client = GeminiLLMClient(config, transport=fake_transport)
    assert isinstance(client, LLMClient)

    response = client.generate(
        system_prompt="sys",
        user_prompt="usr",
        response_schema=_SampleOutput,
    )
    assert tried == ["key-1", "key-2", "key-3"]
    assert response.key_index == 3
    assert response.content == '{"items": []}'


def test_gemini_client_raises_after_all_keys_fail() -> None:
    def always_fails(
        api_key: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: type,
    ) -> str:
        raise LLMExecutionError(f"erro em {api_key}")

    config = LLMConfig(api_keys=("k1", "k2", "k3"))
    client = GeminiLLMClient(config, transport=always_fails)

    with pytest.raises(LLMExecutionError, match="3 chave"):
        client.generate(
            system_prompt="sys",
            user_prompt="usr",
            response_schema=_SampleOutput,
        )
