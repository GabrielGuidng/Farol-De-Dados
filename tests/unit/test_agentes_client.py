"""Testes do LLMClient, LLMConfig, GeminiLLMClient (3 chaves) e FakeLLMClient (Etapa 5.1)."""

from pathlib import Path

import pytest

from backend.agentes import (
    DEFAULT_GEMINI_MODEL,
    FakeLLMClient,
    GeminiLLMClient,
    LLMClient,
    LLMConfig,
    LLMExecutionError,
    parse_dotenv_file,
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
    monkeypatch.setenv("LLM_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("LLM_API_KEY", "key-1")
    monkeypatch.setenv("LLM_API_KEY_2", "key-2")
    monkeypatch.setenv("LLM_API_KEY_3", "key-3")

    config = LLMConfig.from_env(load_dotenv=False)
    assert config.model == "gemini-3.5-flash-lite"
    assert config.api_keys == ("key-1", "key-2", "key-3")


def test_llm_config_uses_default_model_and_skips_blank_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "  ")
    monkeypatch.setenv("LLM_API_KEY_2", "key-backup")
    monkeypatch.delenv("LLM_API_KEY_3", raising=False)

    config = LLMConfig.from_env(load_dotenv=False)
    assert config.model == DEFAULT_GEMINI_MODEL
    assert config.api_keys == ("key-backup",)


def test_llm_config_raises_when_no_api_key_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("LLM_API_KEY", "LLM_API_KEY_2", "LLM_API_KEY_3"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValueError):
        LLMConfig.from_env(load_dotenv=False)


def test_llm_config_from_env_loads_dotenv_file_and_strips_quotes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in ("LLM_MODEL", "LLM_API_KEY", "LLM_API_KEY_2", "LLM_API_KEY_3"):
        monkeypatch.delenv(var, raising=False)

    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "# Comentario ignorado",
                'export LLM_MODEL="gemini-3.5-flash-lite"',
                "LLM_API_KEY='chave-a'",
                "LLM_API_KEY_2=chave-b",
                "",
            ]
        ),
        encoding="utf-8",
    )
    assert parse_dotenv_file(tmp_path / "inexistente.env") == {}

    monkeypatch.chdir(tmp_path)
    config = LLMConfig.from_env()
    assert config.model == "gemini-3.5-flash-lite"
    assert config.api_keys == ("chave-a", "chave-b")

    client = GeminiLLMClient.from_env(
        transport=lambda api_key, model, sys_p, usr_p, schema: '{"items": []}'
    )
    assert client.model_name == "gemini-3.5-flash-lite"


def test_llm_config_os_environ_overrides_dotenv_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "LLM_MODEL=gemini-3.5-flash-lite\nLLM_API_KEY=chave-dotenv\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_API_KEY", "chave-ambiente")
    monkeypatch.delenv("LLM_API_KEY_2", raising=False)
    monkeypatch.delenv("LLM_API_KEY_3", raising=False)

    config = LLMConfig.from_env(env_file=dotenv)
    assert config.api_keys == ("chave-ambiente",)


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

    config = LLMConfig(model="gemini-3.5-flash-lite", api_keys=("key-1", "key-2", "key-3"))
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


def test_gemini_client_falls_back_to_secondary_model_and_promotes_it() -> None:
    tried: list[tuple[str, str]] = []

    def fallback_transport(
        api_key: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: type,
    ) -> str:
        tried.append((model, api_key))
        if model == "gemini-3.8-flash":
            raise LLMExecutionError("503 high demand")
        return '{"items": ["ok"]}'

    config = LLMConfig(
        model="gemini-3.8-flash",
        api_keys=("k1", "k2"),
        fallback_models=("gemini-3.5-flash-lite",),
    )
    client = GeminiLLMClient(config, transport=fallback_transport)

    first = client.generate(
        system_prompt="sys",
        user_prompt="usr",
        response_schema=_SampleOutput,
    )
    assert first.model == "gemini-3.5-flash-lite"
    assert first.key_index == 1
    assert tried == [
        ("gemini-3.8-flash", "k1"),
        ("gemini-3.8-flash", "k2"),
        ("gemini-3.5-flash-lite", "k1"),
    ]

    tried.clear()
    second = client.generate(
        system_prompt="sys",
        user_prompt="usr",
        response_schema=_SampleOutput,
    )
    assert second.model == "gemini-3.5-flash-lite"
    assert tried == [("gemini-3.5-flash-lite", "k1")]
