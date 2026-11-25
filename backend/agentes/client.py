"""Interface de cliente LLM, cliente Gemini com rotação de chaves e cliente falso."""

import os
from collections import deque
from collections.abc import Callable, Sequence
from typing import Protocol, Self, runtime_checkable

from pydantic import BaseModel, Field

from backend.schemas import StrictModel
from backend.schemas.types import NonEmptyStr

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_TIMEOUT_SECONDS = 30

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


class LLMConfig(StrictModel):
    """Configuração de modelo e chaves de contingência lidas de variáveis de ambiente."""

    model: NonEmptyStr = DEFAULT_GEMINI_MODEL
    api_keys: tuple[NonEmptyStr, ...]

    @classmethod
    def from_env(cls) -> Self:
        """Lê `LLM_MODEL` e até três chaves (`LLM_API_KEY`, `LLM_API_KEY_2`, `LLM_API_KEY_3`)."""
        model = os.environ.get("LLM_MODEL", "").strip() or DEFAULT_GEMINI_MODEL
        keys = tuple(
            value.strip()
            for env_name in ("LLM_API_KEY", "LLM_API_KEY_2", "LLM_API_KEY_3")
            if (value := os.environ.get(env_name, "").strip())
        )
        if not keys:
            raise ValueError(
                "nenhuma chave configurada em LLM_API_KEY, LLM_API_KEY_2 ou LLM_API_KEY_3"
            )
        return cls(model=model, api_keys=keys)


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

    @classmethod
    def from_env(
        cls,
        *,
        transport: GeminiTransport | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> Self:
        """Instancia o cliente usando `LLMConfig.from_env()`."""
        return cls(
            LLMConfig.from_env(),
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
        """Tenta as chaves configuradas em ordem; passa para a próxima se ocorrer falha."""
        last_error: LLMExecutionError | None = None
        for index, api_key in enumerate(self._config.api_keys, start=1):
            try:
                content = self._transport(
                    api_key,
                    self._config.model,
                    system_prompt,
                    user_prompt,
                    response_schema,
                )
                return LLMResponse(content=content, model=self._config.model, key_index=index)
            except LLMExecutionError as exc:
                last_error = exc

        total = len(self._config.api_keys)
        raise LLMExecutionError(
            f"todas as {total} chave(s) falharam para {self._config.model}: {last_error}"
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
