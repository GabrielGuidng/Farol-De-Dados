"""Agentes de IA: cliente LLM, prompts versionados, ancoragem, Jurista e Fiscal (PDF)."""

from backend.agentes.client import (
    DEFAULT_GEMINI_MODEL,
    FakeLLMClient,
    GeminiLLMClient,
    GeminiTransport,
    LLMClient,
    LLMConfig,
    LLMExecutionError,
    LLMResponse,
    RecordedLLMCall,
)
from backend.agentes.prompts import (
    DOCUMENT_END_DELIMITER,
    DOCUMENT_START_DELIMITER,
    FISCAL_AGENT_PROMPT_VERSION,
    FISCAL_AGENT_SYSTEM_PROMPT,
    FISCAL_AGENT_USER_TEMPLATE,
    LEGAL_AGENT_PROMPT_VERSION,
    LEGAL_AGENT_SYSTEM_PROMPT,
    LEGAL_AGENT_USER_TEMPLATE,
)

__all__ = [
    "DEFAULT_GEMINI_MODEL",
    "DOCUMENT_END_DELIMITER",
    "DOCUMENT_START_DELIMITER",
    "FISCAL_AGENT_PROMPT_VERSION",
    "FISCAL_AGENT_SYSTEM_PROMPT",
    "FISCAL_AGENT_USER_TEMPLATE",
    "LEGAL_AGENT_PROMPT_VERSION",
    "LEGAL_AGENT_SYSTEM_PROMPT",
    "LEGAL_AGENT_USER_TEMPLATE",
    "FakeLLMClient",
    "GeminiLLMClient",
    "GeminiTransport",
    "LLMClient",
    "LLMConfig",
    "LLMExecutionError",
    "LLMResponse",
    "RecordedLLMCall",
]
