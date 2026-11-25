"""Teste real contra a API do Google Gemini (fora da suíte padrão; rodar com `-m llm`)."""

import os

import pytest

from backend.agentes import (
    GeminiLLMClient,
    JuristaInput,
    run_jurista_agent,
)
from backend.leitura import PdfPage, PdfReadResult
from backend.schemas import ReadQuality, ReviewStatus


@pytest.mark.llm
def test_gemini_live_jurista_extraction_requires_explicit_marker() -> None:
    if not any(
        os.environ.get(var, "").strip() for var in ("LLM_API_KEY", "LLM_API_KEY_2", "LLM_API_KEY_3")
    ):
        pytest.skip("Defina LLM_API_KEY para executar o teste real contra a API do Gemini.")

    client = GeminiLLMClient.from_env()
    pdf_result = PdfReadResult(
        pages=(
            PdfPage(
                page_number=1,
                text=(
                    "Cláusula 3ª: A contratante pagará o valor mensal de R$ 10.000,00 "
                    "a partir de 01/01/2026 até 31/12/2026."
                ),
            ),
        ),
        read_quality=ReadQuality.OK,
        full_text=(
            "Cláusula 3ª: A contratante pagará o valor mensal de R$ 10.000,00 "
            "a partir de 01/01/2026 até 31/12/2026."
        ),
    )
    result = run_jurista_agent(
        JuristaInput.from_pdf("doc-live", "CT-LIVE-001", pdf_result),
        client,
    )
    assert result.schema_valid is True
    assert all(rule.review_status is not ReviewStatus.CONFIRMED for rule in result.rules)
