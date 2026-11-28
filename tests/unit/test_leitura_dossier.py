"""Testes unitários do extrator determinístico de dossiês e de planilhas de OP/certidões."""

from datetime import date
from decimal import Decimal

from backend.leitura import (
    detect_spreadsheet_kind,
    extract_dossier_artifacts,
    import_certificates_csv,
    import_payment_orders_csv,
)
from backend.schemas import CertificateType, RuleType

_SAMPLE_DOSSIER = """
Natureza jurídica: Sociedade Empresária Limitada (206-2)
Conta bancária da contratada Banco 341 - Agência 1287 - Conta corrente 45912-3
CLÁUSULA 2ª - DO PREÇO. Valor mensal de R$ 48.500,00. Reajuste após 12 meses pelo IPCA/IBGE.
CLÁUSULA 3ª - DA VIGÊNCIA. O contrato vigorará de 01/03/2025 a 28/02/2026.
CLÁUSULA 5ª - DAS RETENÇÕES. Incidirão sobre o valor bruto de cada nota fiscal: (a) retenção de 11%
(onze por cento) a título de contribuição previdenciária; (b) retenção de 2% de ISS.
CLÁUSULA 6ª - DA ALÇADA DE APROVAÇÃO. Pagamentos até R$ 50.000,00 pelo Gerente;
valores superiores pela Diretoria.
Auditoria Interna Pág. 3
3. Notas fiscais de serviço recebidas
1051 09/2025 30/09/2025 02/10/2025 52.300,00 5.753,00 1.046,00 45.501,00
1055 10/2025 31/10/2025 04/11/2025 48.500,00 4.850,00 970,00 42.680,00
4. Ordens de pagamento
Norte Sul Serviços Gerais ME 237 / 3340 /
2025/0690 27/06/2025 1040 42.195,00 27730 18843
31.926.485/0001-72 90218-7
Registro de aprovações por alçada
2025/1188 R$ 52.300,00 18843 - Gerente Financeira Até R$ 50.000,00
5. Certidões de regularidade da contratada
CND - Débitos Federais (RFB/PGFN) 17/12/2024 15/06/2025 Não localizada
CRF - FGTS (Caixa) 20/10/2025 18/11/2025 Não localizada
6. Resumo financeiro do período
Total líquido pago (11 ordens de pagamento) 467.436,00
"""


def test_extract_dossier_artifacts_parses_retentions_ops_certs_and_summary() -> None:
    res = extract_dossier_artifacts(
        _SAMPLE_DOSSIER,
        contract_id="2025/0187",
        source_document_id="doc-1",
    )
    assert res.contract_metadata.bank_code == "341"
    assert res.contract_metadata.bank_account == "45912-3"
    assert res.contract_metadata.legal_nature == "Sociedade Empresária Limitada"
    assert res.invoice_patches["1055"].inss_withheld == Decimal("4850.00")
    assert res.invoice_patches["1055"].attestation_date == date(2025, 11, 4)

    assert len(res.payment_orders) == 1
    assert res.payment_orders[0].op_number == "2025/0690"
    assert res.payment_orders[0].beneficiary_cnpj == "31926485000172"
    assert res.payment_orders[0].paid_amount == Decimal("42195.00")

    assert [c.certificate_type for c in res.tax_certificates] == [
        CertificateType.CND_FEDERAL,
        CertificateType.CRF_FGTS,
    ]
    assert res.financial_summary is not None
    assert res.financial_summary.reported_net_paid_total == Decimal("467436.00")

    rule_types = {r.rule_type for r in res.supplemental_rules}
    assert RuleType.INSS_RETENTION in rule_types
    assert RuleType.ISS_RETENTION in rule_types
    assert RuleType.APPROVAL_LIMIT in rule_types
    inss_rule = next(r for r in res.supplemental_rules if r.rule_type is RuleType.INSS_RETENTION)
    assert inss_rule.value == Decimal("11")


def test_spreadsheet_payment_orders_and_certificates_import() -> None:
    op_csv = (
        b"op_numero;data_pgto;nf;beneficiario;cnpj;banco;agencia;conta;valor_pago;"
        b"solicitante;aprovador\n"
        b"2025/0561;29/05/2025;1036;Norte Sul Ltda;28.741.963/0001-65;"
        b"341;1287;45912-3;42.195,00;20417;20417\n"
    )
    assert detect_spreadsheet_kind(op_csv, is_xlsx=False) == "PAYMENT_ORDERS"
    orders = import_payment_orders_csv(op_csv, default_contract_id="2025/0187")
    assert len(orders) == 1
    assert orders[0].requester_id == "20417"
    assert orders[0].approver_id == "20417"

    cert_csv = (
        "certidao;emissao;validade;status\nCND Federal;17/12/2024;15/06/2025;Não localizada\n"
    ).encode()
    assert detect_spreadsheet_kind(cert_csv, is_xlsx=False) == "CERTIFICATES"
    certs = import_certificates_csv(cert_csv, default_contract_id="2025/0187")
    assert len(certs) == 1
    assert certs[0].certificate_type is CertificateType.CND_FEDERAL
    assert certs[0].expiry_date == date(2025, 6, 15)
