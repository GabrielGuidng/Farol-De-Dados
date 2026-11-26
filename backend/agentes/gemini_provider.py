"""Cliente real da API do Google Gemini com sanitização de JSON Schema para Decimal."""

from collections.abc import Mapping

import httpx
from pydantic import BaseModel

try:
    import httpx2

    _HTTP_ERROR_TYPES: tuple[type[Exception], ...] = (httpx.HTTPError, httpx2.HTTPError)
except ImportError:
    _HTTP_ERROR_TYPES = (httpx.HTTPError,)

from backend.agentes.client import GeminiTransport, LLMExecutionError

DEFAULT_TIMEOUT_SECONDS = 45

_STRIPPED_SCHEMA_KEYS = frozenset({"decimal_places", "max_digits"})
_NUMERIC_BOUND_KEYS = frozenset(
    {"ge", "gt", "le", "lt", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}
)
_DECIMAL_STRING_PATTERN = r"^[0-9]+(\.[0-9]+)?$"


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
        for bound_key in _NUMERIC_BOUND_KEYS:
            sanitized.pop(bound_key, None)
        kept: list[object] = []
        for branch in any_of:
            if not isinstance(branch, dict) or branch.get("type") == "number":
                continue
            if branch.get("type") == "string":
                clean_branch = {k: v for k, v in branch.items() if k not in _NUMERIC_BOUND_KEYS}
                clean_branch["pattern"] = _DECIMAL_STRING_PATTERN
                kept.append(clean_branch)
            else:
                kept.append(branch)
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
    timeout_ms = max(timeout_seconds, 10) * 1000

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
                http_options=types.HttpOptions(
                    timeout=timeout_ms,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
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
            *_HTTP_ERROR_TYPES,
            TimeoutError,
            OSError,
            ValueError,
            RuntimeError,
        ) as exc:
            api_key = "<redacted>"
            raise LLMExecutionError(f"falha na chamada ao modelo {model}: {exc}") from exc

        if not response.text:
            raise LLMExecutionError(f"resposta vazia do modelo {model}")
        return response.text

    return _transport
