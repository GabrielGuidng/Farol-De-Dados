"""Interface de cliente LLM, cliente Gemini com rotação de chaves e cliente falso."""

import os
from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol, Self, runtime_checkable

from pydantic import BaseModel, Field

from backend.schemas import StrictModel
from backend.schemas.types import NonEmptyStr

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
DEFAULT_FALLBACK_MODELS: tuple[str, ...] = (
    "gemini-3.1-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.8-flash",
)
DEFAULT_TIMEOUT_SECONDS = 45

GeminiTransport = Callable[[str, str, str, str, type[BaseModel]], str]


class LLMExecutionError(RuntimeError):
    """Erro de comunicação ou execução ao consultar o provedor de modelo de linguagem."""


class LLMResponse(StrictModel):
    """Resposta bruta devolvida pelo cliente de modelo de linguagem."""

    content: str
    model: NonEmptyStr
    key_index: int = Field(default=1, ge=1)


class RecordedLLMCall(StrictModel):
    """Chamada registrada pelo FakeLLMClient para inspeção em testes sem rede."""

    system_prompt: str
    user_prompt: str
    schema_name: NonEmptyStr


def parse_dotenv_file(env_path: Path) -> dict[str, str]:
    """Lê pares chave=valor de um arquivo `.env` simples sem alterar `os.environ`."""
    if not env_path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        clean_key = key.strip()
        if not clean_key:
            continue
        clean_value = raw_value.strip()
        if (
            len(clean_value) >= 2
            and clean_value[0] == clean_value[-1]
            and clean_value[0] in ("'", '"')
        ):
            clean_value = clean_value[1:-1]
        values[clean_key] = clean_value
    return values


class LLMConfig(StrictModel):
    """Configuração de modelo e chaves de contingência lidas de variáveis de ambiente."""

    model: NonEmptyStr = DEFAULT_GEMINI_MODEL
    api_keys: tuple[NonEmptyStr, ...]
    fallback_models: tuple[NonEmptyStr, ...] = ()

    @classmethod
    def from_env(
        cls,
        *,
        env_file: Path | None = None,
        load_dotenv: bool = True,
    ) -> Self:
        """Lê `LLM_MODEL` e até três chaves (`LLM_API_KEY`, `LLM_API_KEY_2`, `LLM_API_KEY_3`)."""
        dotenv_values = (
            parse_dotenv_file(env_file if env_file is not None else Path(".env"))
            if load_dotenv
            else {}
        )

        def _get_var(name: str) -> str:
            if name in os.environ:
                return os.environ[name].strip()
            return dotenv_values.get(name, "").strip()

        model = _get_var("LLM_MODEL") or DEFAULT_GEMINI_MODEL
        keys = tuple(
            value
            for env_name in ("LLM_API_KEY", "LLM_API_KEY_2", "LLM_API_KEY_3")
            if (value := _get_var(env_name))
        )
        if not keys:
            raise ValueError(
                "nenhuma chave configurada em LLM_API_KEY, LLM_API_KEY_2 ou LLM_API_KEY_3"
            )
        raw_fallbacks = _get_var("LLM_FALLBACK_MODELS")
        if raw_fallbacks:
            fallbacks = tuple(
                part.strip()
                for part in raw_fallbacks.split(",")
                if part.strip() and part.strip() != model
            )
        else:
            fallbacks = tuple(item for item in DEFAULT_FALLBACK_MODELS if item != model)
        return cls(model=model, api_keys=keys, fallback_models=fallbacks)


@runtime_checkable
class LLMClient(Protocol):
    """Contrato único de comunicação com modelos de linguagem (permite troca de provedor)."""

    @property
    def model_name(self) -> str:
        """Identificador do modelo configurado."""
        ...

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[BaseModel],
    ) -> LLMResponse:
        """Envia o prompt delimitado e devolve saída estruturada conforme `response_schema`."""
        ...


class GeminiLLMClient:
    """Cliente Gemini com contingência automática entre até três chaves da API."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        transport: GeminiTransport | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds deve ser maior ou igual a 1")
        if transport is None:
            from backend.agentes.gemini_provider import create_gemini_transport

            resolved = create_gemini_transport(timeout_seconds=timeout_seconds)
        else:
            resolved = transport
        self._config = config
        self._transport = resolved
        self._preferred_model: str = config.model

    @classmethod
    def from_env(
        cls,
        *,
        transport: GeminiTransport | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        env_file: Path | None = None,
        load_dotenv: bool = True,
    ) -> Self:
        """Instancia o cliente usando `LLMConfig.from_env()`."""
        return cls(
            LLMConfig.from_env(env_file=env_file, load_dotenv=load_dotenv),
            transport=transport,
            timeout_seconds=timeout_seconds,
        )

    @property
    def model_name(self) -> str:
        """Identificador do modelo Gemini configurado."""
        return self._config.model

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[BaseModel],
    ) -> LLMResponse:
        """Tenta chaves e modelos de contingência em ordem; prioriza o último saudável."""
        all_models = (self._config.model, *self._config.fallback_models)
        ordered_models = (
            self._preferred_model,
            *(m for m in all_models if m != self._preferred_model),
        )
        last_error: LLMExecutionError | None = None
        for candidate_model in ordered_models:
            for index, api_key in enumerate(self._config.api_keys, start=1):
                try:
                    content = self._transport(
                        api_key,
                        candidate_model,
                        system_prompt,
                        user_prompt,
                        response_schema,
                    )
                    self._preferred_model = candidate_model
                    return LLMResponse(content=content, model=candidate_model, key_index=index)
                except LLMExecutionError as exc:
                    last_error = exc

        api_key = "<redacted>"
        total = len(self._config.api_keys)
        models_tried = ", ".join(ordered_models)
        raise LLMExecutionError(
            f"todas as {total} chave(s) falharam nos modelos [{models_tried}]: {last_error}"
        ) from last_error


class FakeLLMClient:
    """Cliente falso para testes offline: devolve respostas pré-definidas (inclusive inválidas)."""

    def __init__(
        self,
        responses: Sequence[str | BaseModel | LLMExecutionError] = (),
        *,
        model: str = "fake-llm",
    ) -> None:
        self._responses: deque[str | BaseModel | LLMExecutionError] = deque(responses)
        self._model = model
        self.calls: list[RecordedLLMCall] = []

    @property
    def model_name(self) -> str:
        """Identificador do modelo falso."""
        return self._model

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[BaseModel],
    ) -> LLMResponse:
        """Registra a chamada recebida e devolve (ou levanta) a próxima resposta da fila."""
        self.calls.append(
            RecordedLLMCall(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema_name=response_schema.__name__,
            )
        )
        if not self._responses:
            raise LLMExecutionError("FakeLLMClient sem respostas configuradas")

        item = self._responses.popleft()
        if isinstance(item, LLMExecutionError):
            raise item
        content = item.model_dump_json() if isinstance(item, BaseModel) else item
        return LLMResponse(content=content, model=self._model, key_index=1)
