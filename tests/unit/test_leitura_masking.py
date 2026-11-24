"""Testes de mascaramento de CPF e RG: cenário A-10 (Etapa 4.6)."""

from backend.leitura import CPF_MASK, RG_MASK, mask_sensitive_data, mask_text


def test_a10_masks_formatted_and_labeled_cpf_and_rg() -> None:
    text = (
        "Contratante representado por João da Silva, CPF 123.456.789-01, "
        "RG: 12.345.678-9, e Maria Oliveira, CPF: 98765432100, RG nº MG-98.765.432."
    )
    result = mask_sensitive_data(text)

    assert result.cpf_count == 2
    assert result.rg_count == 2
    assert "123.456.789-01" not in result.text
    assert "98765432100" not in result.text
    assert "12.345.678-9" not in result.text
    assert "98.765.432" not in result.text
    assert result.text.count(CPF_MASK) == 2
    assert result.text.count(RG_MASK) == 2


def test_a10_preserves_cnpj_money_dates_and_contract_ids() -> None:
    text = (
        "Contrato CT-2026-001 firmado com Fornecedor Exemplo, CNPJ 11.222.333/0001-81, "
        "valor mensal de R$ 10.500,00 com vigência em 01/05/2026. "
        "Testemunha: CPF 111.222.333-44, R.G. 44.555.666-X."
    )
    masked = mask_text(text)

    assert "CT-2026-001" in masked
    assert "11.222.333/0001-81" in masked
    assert "R$ 10.500,00" in masked
    assert "01/05/2026" in masked
    assert "111.222.333-44" not in masked
    assert "44.555.666-X" not in masked
    assert CPF_MASK in masked
    assert RG_MASK in masked
