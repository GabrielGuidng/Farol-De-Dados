"""Testes offline do provedor real Gemini: sanitização de JSON Schema e erros de rede (5.9)."""

import json

import httpx
import pytest
from google.genai import types

from backend.agentes import (
    FiscalExtractionPayload,
    GeminiLLMClient,
    JuristaExtractionPayload,
    LLMConfig,
    LLMExecutionError,
    ReportExplanationPayload,
    TradutorExtractionPayload,
    build_gemini_json_schema,
    create_gemini_transport,
)


@pytest.mark.parametrize(
    "schema_cls",
    [
        JuristaExtractionPayload,
        FiscalExtractionPayload,
        TradutorExtractionPayload,
        ReportExplanationPayload,
    ],
)
def test_build_gemini_json_schema_is_accepted_by_google_genai_config(schema_cls: type) -> None:
    schema = build_gemini_json_schema(schema_cls)
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=schema,
        temperature=0.0,
    )
    assert config.response_json_schema == schema
    serialized = json.dumps(schema)
    assert "decimal_places" not in serialized
    assert "max_digits" not in serialized
    if schema_cls in (
        JuristaExtractionPayload,
        FiscalExtractionPayload,
        TradutorExtractionPayload,
    ):
        assert r"^[0-9]+(\\.[0-9]+)?$" in serialized
        assert '"ge":' not in serialized


def test_gemini_transport_wraps_network_and_timeout_errors_and_rotates_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from google import genai

    attempts: list[str] = []

    class _FakeModels:
        def __init__(self, api_key: str) -> None:
            self._api_key = api_key

        def generate_content(self, **kwargs: object) -> object:
            attempts.append(self._api_key)
            if self._api_key == "k1":
                raise httpx.ConnectError("sem rede")
            if self._api_key == "k2":
                raise TimeoutError("tempo esgotado")
            return type("_Resp", (), {"text": '{"summary": "ok"}'})()

    class _FakeGenaiClient:
        def __init__(self, *, api_key: str, http_options: object) -> None:
            self.models = _FakeModels(api_key)

    monkeypatch.setattr(genai, "Client", _FakeGenaiClient)

    client = GeminiLLMClient(
        LLMConfig(api_keys=("k1", "k2", "k3")),
        transport=create_gemini_transport(timeout_seconds=10),
    )
    response = client.generate(
        system_prompt="sys",
        user_prompt="usr",
        response_schema=ReportExplanationPayload,
    )
    assert attempts == ["k1", "k2", "k3"]
    assert response.key_index == 3
    assert response.content == '{"summary": "ok"}'


def test_gemini_client_default_transport_raises_llm_execution_error_when_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from google import genai

    class _BrokenModels:
        def generate_content(self, **kwargs: object) -> object:
            raise httpx.ReadTimeout("timeout")

    class _BrokenClient:
        def __init__(self, *, api_key: str, http_options: object) -> None:
            self.models = _BrokenModels()

    monkeypatch.setattr(genai, "Client", _BrokenClient)

    client = GeminiLLMClient(LLMConfig(api_keys=("k1",)), timeout_seconds=5)
    with pytest.raises(LLMExecutionError, match="timeout"):
        client.generate(
            system_prompt="sys",
            user_prompt="usr",
            response_schema=ReportExplanationPayload,
        )


def test_gemini_transport_wraps_httpx2_connect_error_and_rotates_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    httpx2 = pytest.importorskip("httpx2")
    from google import genai

    attempts: list[str] = []

    class _FakeModels:
        def __init__(self, api_key: str) -> None:
            self._api_key = api_key

        def generate_content(self, **kwargs: object) -> object:
            attempts.append(self._api_key)
            if self._api_key == "k1":
                raise httpx2.ConnectError("erro de rede httpx2")
            return type("_Resp", (), {"text": '{"summary": "ok-httpx2"}'})()

    class _FakeGenaiClient:
        def __init__(self, *, api_key: str, http_options: object) -> None:
            self.models = _FakeModels(api_key)

    monkeypatch.setattr(genai, "Client", _FakeGenaiClient)

    client = GeminiLLMClient(
        LLMConfig(api_keys=("k1", "k2")),
        transport=create_gemini_transport(timeout_seconds=10),
    )
    response = client.generate(
        system_prompt="sys",
        user_prompt="usr",
        response_schema=ReportExplanationPayload,
    )
    assert attempts == ["k1", "k2"]
    assert response.key_index == 2
    assert response.content == '{"summary": "ok-httpx2"}'
