"""Cliente real da API do Google Gemini com sanitização de JSON Schema para Decimal."""

from collections.abc import Mapping

import httpx
from pydantic import BaseModel

from backend.agentes.client import GeminiTransport, LLMExecutionError

DEFAULT_TIMEOUT_SECONDS = 30

_STRIPPED_SCHEMA_KEYS = frozenset({"decimal_places", "max_digits"})


def _is_decimal_union(branches: list[object]) -> bool:
    has_string = False
    has_number = False
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        branch_type = branch.get("type")
        if branch_type == "string":
            has_string = True
        elif branch_type == "number":
            has_number = True
        elif branch_type != "null":
            return False
    return has_string and has_number


def _sanitize_schema_node(node: object) -> object:
    if isinstance(node, list):
        return [_sanitize_schema_node(item) for item in node]
    if not isinstance(node, Mapping):
        return node

    sanitized: dict[str, object] = {}
    for key, value in node.items():
        if key in _STRIPPED_SCHEMA_KEYS:
            continue
        sanitized[str(key)] = _sanitize_schema_node(value)

    any_of = sanitized.get("anyOf")
    if isinstance(any_of, list) and _is_decimal_union(any_of):
        kept = [
            branch
            for branch in any_of
            if isinstance(branch, dict) and branch.get("type") != "number"
        ]
        if len(kept) == 1 and isinstance(kept[0], dict):
            sanitized.pop("anyOf")
            sanitized.update(kept[0])
        else:
            sanitized["anyOf"] = kept
    return sanitized


def build_gemini_json_schema(response_schema: type[BaseModel]) -> dict[str, object]:
    """
    Constrói JSON Schema compatível com `google-genai`, convertendo campos `Decimal` para
    strings puras (sem ramo `number` nem metadados não aceitos) para preservar precisão exata.
    """
    raw = response_schema.model_json_schema()
    cleaned = _sanitize_schema_node(raw)
    if not isinstance(cleaned, dict):
        raise ValueError("JSON Schema inválido")
    return cleaned


def create_gemini_transport(*, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> GeminiTransport:
    """Cria função de transporte síncrona para a API do Gemini com timeout e JSON Schema."""
    if timeout_seconds < 1:
        raise ValueError("timeout_seconds deve ser maior ou igual a 1")
    timeout_ms = timeout_seconds * 1000

    def _transport(
        api_key: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[BaseModel],
    ) -> str:
        from google import genai
        from google.genai import errors as genai_errors
        from google.genai import types

        json_schema = build_gemini_json_schema(response_schema)
        try:
            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=timeout_ms),
            )
            response = client.models.generate_content(
                model=model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    response_json_schema=json_schema,
                    temperature=0.0,
                ),
            )
        except (
            genai_errors.APIError,
            httpx.HTTPError,
            TimeoutError,
            OSError,
            ValueError,
            RuntimeError,
        ) as exc:
            raise LLMExecutionError(f"falha na chamada ao modelo {model}: {exc}") from exc

        if not response.text:
            raise LLMExecutionError(f"resposta vazia do modelo {model}")
        return response.text

    return _transport
