"""Testes integrados reproduzindo os Cenários A, B, C, Lote Combinado e Trava de Segurança."""

from datetime import UTC, date, datetime
from decimal import Decimal

from backend.api import (
    audit_stored_invoice,
    fetch_audits,
    fetch_contracts,
    fetch_invoices,
    fetch_payment_cycle_audit,
    fetch_rules,
    register_contract,
    sync_contract_dossier_data,
)
from backend.persistencia import (
    init_db,
    receive_document,
    save_invoice,
    save_rule,
)
from backend.schemas import (
    BillingItem,
    Contract,
    ContractRule,
    ContractStatus,
    DocumentType,
    Evidence,
    Invoice,
    InvoiceSource,
    ReadQuality,
    ReferencePeriod,
    ReviewStatus,
    RuleType,
    ValidityPeriod,
)
from frontend.ui.executive_report import build_executive_report


def test_scenario_a_b_c_and_combined_sections_and_safety_lock() -> None:
    conn = init_db(":memory:")
    try:
        # ---- Cenário A: Locação Controle (LOC-2024/017) ----
        contract_a = Contract(
            id="LOC-2024/017",
            company="GRUPO MARÉ ALTA LOGÍSTICA LTDA.",
            supplier="IMOBILIÁRIA CORDEIRO EMPREENDIMENTOS LTDA.",
            supplier_cnpj="55173042000116",
            subject="Locação galpão",
            validity=ValidityPeriod(start=date(2024, 9, 1), end=date(2027, 8, 31)),
            comparison_tolerance=Decimal("0.00"),
            status=ContractStatus.ACTIVE,
        )
        register_contract(conn, contract_a)
        doc_a = receive_document(
            document_id="doc-a1",
            contract_id=contract_a.id,
            document_type=DocumentType.CONTRACT,
            file_name="A1_dossie_locacao_controle.pdf",
            content=b"a1-bytes",
            received_at=datetime(2025, 1, 1, 10, 0, tzinfo=UTC),
            read_quality=ReadQuality.OK,
            extracted_text="LOCADORA: Banco 237, Agência 0412, Conta corrente 118390-6.",
            reviewed=True,
            conn=conn,
        )
        save_rule(
            conn,
            ContractRule(
                id="r-a-1",
                contract_id=contract_a.id,
                rule_type=RuleType.MONTHLY_PRICE,
                value=Decimal("22400.00"),
                validity=ValidityPeriod(start=date(2024, 9, 1), end=date(2025, 8, 31)),
                review_status=ReviewStatus.CONFIRMED,
                evidence=Evidence(
                    source_document_id=doc_a.id, page=1, literal_excerpt="Aluguel R$ 22.400,00."
                ),
            ),
        )
        save_rule(
            conn,
            ContractRule(
                id="r-a-2",
                contract_id=contract_a.id,
                rule_type=RuleType.MONTHLY_PRICE,
                value=Decimal("23094.40"),
                validity=ValidityPeriod(start=date(2025, 9, 1), end=date(2027, 8, 31)),
                review_status=ReviewStatus.CONFIRMED,
                evidence=Evidence(
                    source_document_id=doc_a.id, page=3, literal_excerpt="Passa para R$ 23.094,40."
                ),
            ),
        )
        for m in range(1, 13):
            amt = Decimal("22400.00") if m <= 8 else Decimal("23094.40")
            sup_name = (
                "Imobiliária Cordeiro Empreend. Ltda."
                if m % 2 == 1
                else "IMOBILIARIA CORDEIRO EMPREENDIMENTOS LTDA"
            )
            save_invoice(
                conn,
                Invoice(
                    id=f"inv-a-{m}",
                    contract_id=contract_a.id,
                    supplier=sup_name,
                    supplier_cnpj="55173042000116",
                    number=f"2025/{m:03d}",
                    issue_date=date(2025, m, 5),
                    reference_period=ReferencePeriod(year=2025, month=m),
                    total=amt,
                    source=InvoiceSource.PDF,
                    items=(
                        BillingItem(
                            description="Aluguel mensal",
                            quantity=Decimal("1"),
                            unit_value=amt,
                            total_value=amt,
                        ),
                    ),
                ),
            )
            audit_stored_invoice(conn, f"inv-a-{m}")

        # ---- Cenário B: Contrato 018/2024 (Limpeza por postos + B2 + B4) ----
        contract_b = Contract(
            id="018/2024",
            company="FUNDAÇÃO MUNICIPAL DE SAÚDE DE SERRA VERDE",
            supplier="LIMPAR MAIS SERVIÇOS DE CONSERVAÇÃO LTDA.",
            supplier_cnpj="24681035000100",
            subject="Limpeza continuada",
            validity=ValidityPeriod(start=date(2025, 1, 2), end=date(2025, 12, 31)),
            comparison_tolerance=Decimal("0.00"),
            status=ContractStatus.ACTIVE,
        )
        register_contract(conn, contract_b)
        b1_text = (
            "CLÁUSULA SEGUNDA - DO PREÇO.\n"
            "Posto Quantidade Valor mensal por posto (R$) Valor mensal total (R$)\n"
            "Servente de limpeza 12 5.920,00 (cinco mil, novecentos e vinte reais) 71.040,00\n"
            "Encarregado 1 7.350,00 (sete mil, trezentos e cinquenta reais) 7.350,00\n"
            "CLÁUSULA TERCEIRA - DA VIGÊNCIA. O contrato terá vigência de "
            "02/01/2025 a 31/12/2025.\n"
            "CLÁUSULA SÉTIMA - DO FATURAMENTO E DO PAGAMENTO. Conta da CONTRATADA:\n"
            "Banco 001, Agência 1234-5, Conta corrente 77310-2.\n"
            "CLÁUSULA OITAVA - DAS RETENÇÕES. Incidirão sobre o valor faturado, "
            "líquido de glosas:\n"
            "(a) 11% (onze por cento) de contribuição previdenciária; "
            "(b) 3% (três por cento) de ISS.\n"
        )
        receive_document(
            document_id="doc-b1",
            contract_id="018/2024",
            document_type=DocumentType.CONTRACT,
            file_name="B1_contrato_018_2024.pdf",
            content=b"b1-bytes",
            received_at=datetime(2025, 1, 2, 10, 0, tzinfo=UTC),
            read_quality=ReadQuality.OK,
            extracted_text=b1_text,
            reviewed=True,
            conn=conn,
        )
        b2_text = (
            "PRIMEIRO TERMO ADITIVO AO CONTRATO Nº 018/2024\n"
            "CLÁUSULA PRIMEIRA. Fica acrescido o quantitativo em 2 (dois) postos de servente de "
            "limpeza, passando de 12 (doze) para 14 (quatorze), com efeitos a partir de "
            "01/04/2025, ao preço unitário de R$ 5.920,00 (cinco mil, novecentos e vinte reais) "
            "por posto/mês.\n"
            "SEGUNDO TERMO ADITIVO AO CONTRATO Nº 018/2024\n"
            "CLÁUSULA PRIMEIRA - DA PRORROGAÇÃO. Fica prorrogada a vigência do contrato por 12 "
            "meses, de 01/01/2026 a 31/12/2026.\n"
            "CLÁUSULA SEGUNDA - DA REPACTUAÇÃO. Com efeitos a partir de 01/01/2026: "
            "servente de limpeza, R$ 6.204,16; encarregado, R$ 7.702,80.\n"
            "CLÁUSULA TERCEIRA. Mantido o quantitativo de 14 postos de servente e 1 encarregado.\n"
        )
        receive_document(
            document_id="doc-b2",
            contract_id="018/2024",
            document_type=DocumentType.AMENDMENT,
            file_name="B2_aditivos_1_e_2.pdf",
            content=b"b2-bytes",
            received_at=datetime(2025, 12, 12, 10, 0, tzinfo=UTC),
            read_quality=ReadQuality.OK,
            extracted_text=b2_text,
            reviewed=False,
            conn=conn,
        )
        b4_text = (
            "RELATÓRIO DE ORDENS BANCÁRIAS E LIQUIDAÇÕES - CONTRATO Nº 018/2024\n"
            "Delta Gestão Patrimonial Ltda 237 / 0561 /\n"
            "2025OB000345 22/07/2025 2025NE000144 2025/000106 82.689,00 03/07/2025\n"
            "71.938.452/0001-67 40228-9\n"
            "LIMPAR MAIS SERVIÇOS DE CONSERVAÇÃO LTDA 001 / 1234-5 /\n"
            "2025OB000372 01/10/2025 2025NE000144 2025/000109 87.523,10 03/10/2025\n"
            "24.681.035/0001-00 77310-2\n"
            "Relatório mensal de frequência (fiscal do contrato)\n"
            "05/2025 3 592,00\n"
            "07/2025 2 394,67\n"
            "REGULARIDADE FISCAL E GARANTIA CONTRATUAL\n"
            "Certidão de Tributos Federais (RFB/PGFN) 10/01/2025 09/07/2025 -\n"
            "Certidão de Tributos Federais (RFB/PGFN) 15/10/2025 12/04/2026 -\n"
            "Seguro-garantia Apólice nº 1007\n"
            "Vigência da apólice 02/01/2025 a 30/06/2025\n"
            "Renovação / endosso Não localizado\n"
        )
        receive_document(
            document_id="doc-b4",
            contract_id="018/2024",
            document_type=DocumentType.CONTRACT,
            file_name="B4_ordens_bancarias_frequencia_certidoes.pdf",
            content=b"b4-bytes",
            received_at=datetime(2026, 2, 1, 10, 0, tzinfo=UTC),
            read_quality=ReadQuality.OK,
            extracted_text=b4_text,
            reviewed=True,
            conn=conn,
        )

        # Carrega NFs de B (01/2025, 05/2025, 06/2025, 07/2025, 09/2025, 11/2025)
        def _b_inv(
            num: str,
            month: int,
            serv_qty: str,
            serv_u: str,
            enc_u: str,
            total: str,
            inss: str,
            iss: str,
            extra_items: tuple[BillingItem, ...] = (),
        ) -> Invoice:
            serv_tot = Decimal(serv_qty) * Decimal(serv_u)
            return Invoice(
                id=f"inv-b-{month}",
                contract_id="018/2024",
                supplier="LIMPAR MAIS SERVICOS DE CONSERVACAO LTDA",
                supplier_cnpj="24681035000100",
                number=num,
                issue_date=date(2025, month, 28),
                reference_period=ReferencePeriod(year=2025, month=month),
                total=Decimal(total),
                inss_withheld=Decimal(inss),
                iss_withheld=Decimal(iss),
                source=InvoiceSource.PDF,
                items=(
                    BillingItem(
                        description="Posto de Servente de limpeza",
                        quantity=Decimal(serv_qty),
                        unit_value=Decimal(serv_u),
                        total_value=serv_tot,
                    ),
                    BillingItem(
                        description="Posto de Encarregado",
                        quantity=Decimal("1"),
                        unit_value=Decimal(enc_u),
                        total_value=Decimal(enc_u),
                    ),
                    *extra_items,
                ),
            )

        for inv_b in (
            _b_inv("2025/000101", 1, "12", "5920.00", "7350.00", "78390.00", "8622.90", "2351.70"),
            _b_inv("2025/000105", 5, "14", "5920.00", "7350.00", "90230.00", "9925.30", "2706.90"),
            _b_inv("2025/000106", 6, "15", "5920.00", "7350.00", "96150.00", "10576.50", "2884.50"),
            _b_inv(
                "2025/000107",
                7,
                "14",
                "5920.00",
                "7350.00",
                "89835.33",
                "9881.89",
                "2695.06",
                extra_items=(
                    BillingItem(
                        description="Desconto por ausências sem reposição (glosa)",
                        quantity=Decimal("2"),
                        unit_value=Decimal("197.335"),
                        total_value=Decimal("394.67"),
                    ),
                ),
            ),
            _b_inv("2025/000109", 9, "14", "5920.00", "7350.00", "90230.00", "0.00", "2706.90"),
            _b_inv(
                "2025/000111", 11, "14", "6204.16", "7702.80", "94561.04", "10401.71", "2836.83"
            ),
        ):
            save_invoice(conn, inv_b)

        sync_contract_dossier_data(conn, "018/2024", auto_approve_above_threshold=True)
        for inv_b in fetch_invoices(conn, "018/2024"):
            audit_stored_invoice(conn, inv_b.id)

        # ---- Cenário C: Ata de Registro de Preços 007/2025 ----
        contract_c = Contract(
            id="ARP-007/2025",
            company="INSTITUTO DE PREVIDÊNCIA DOS SERVIDORES DE VALE DOURADO",
            supplier="PAPELARIA E SUPRIMENTOS BOA VISTA LTDA.",
            supplier_cnpj="60271584000161",
            subject="Material de expediente",
            validity=ValidityPeriod(start=date(2025, 3, 10), end=date(2026, 3, 9)),
            comparison_tolerance=Decimal("0.00"),
            status=ContractStatus.ACTIVE,
        )
        register_contract(conn, contract_c)
        c1_text = (
            "ATA DE REGISTRO DE PREÇOS Nº 007/2025\n"
            "Item Descrição Unidade Quantidade máxima Preço unitário (R$)\n"
            "1 Papel sulfite A4 75 g/m², resma com 500 folhas resma 2000 24,90\n"
            "2 Toner para impressora HP LaserJet, modelo 85A, compatível unidade 120 189,00\n"
            "3 Caneta esferográfica azul, caixa com 50 unidades caixa 150 41,50\n"
            "4 Pasta suspensa, caixa com 50 unidades caixa 60 96,00\n"
            "CLÁUSULA 2ª - DA VIGÊNCIA. De 10/03/2025 a 09/03/2026.\n"
        )
        receive_document(
            document_id="doc-c1",
            contract_id="ARP-007/2025",
            document_type=DocumentType.CONTRACT,
            file_name="C1_ata_registro_precos_007_2025.pdf",
            content=b"c1-bytes",
            received_at=datetime(2025, 3, 10, 10, 0, tzinfo=UTC),
            read_quality=ReadQuality.OK,
            extracted_text=c1_text,
            reviewed=True,
            conn=conn,
        )
        toner_desc = "0002 Toner para impressora HP LaserJet, modelo 85A, compatível"
        c_invoices = (
            Invoice(
                id="inv-c-412",
                contract_id="ARP-007/2025",
                supplier="PAPELARIA E SUPRIMENTOS BOA VISTA LTDA",
                supplier_cnpj="60271584000161",
                number="000412",
                issue_date=date(2025, 4, 12),
                reference_period=ReferencePeriod(year=2025, month=4),
                total=Decimal("8300.00"),
                service_description="Ref. AF-0101 - Ata nº 007/2025",
                source=InvoiceSource.PDF,
                items=(
                    BillingItem(
                        description="0001 Papel sulfite A4 75 g/m², resma com 500 folhas",
                        quantity=Decimal("300"),
                        unit_value=Decimal("24.90"),
                        total_value=Decimal("7470.00"),
                    ),
                    BillingItem(
                        description="0003 Caneta esferográfica azul, caixa com 50 unidades",
                        quantity=Decimal("20"),
                        unit_value=Decimal("41.50"),
                        total_value=Decimal("830.00"),
                    ),
                ),
            ),
            Invoice(
                id="inv-c-436",
                contract_id="ARP-007/2025",
                supplier="PAPELARIA E SUPRIMENTOS BOA VISTA LTDA",
                supplier_cnpj="60271584000161",
                number="000436",
                issue_date=date(2025, 5, 20),
                reference_period=ReferencePeriod(year=2025, month=5),
                total=Decimal("6630.00"),
                service_description="Ref. AF-0118 - Ata nº 007/2025",
                source=InvoiceSource.PDF,
                items=(
                    BillingItem(
                        description=toner_desc,
                        quantity=Decimal("30"),
                        unit_value=Decimal("189.00"),
                        total_value=Decimal("5670.00"),
                    ),
                    BillingItem(
                        description="0004 Pasta suspensa, caixa com 50 unidades",
                        quantity=Decimal("10"),
                        unit_value=Decimal("96.00"),
                        total_value=Decimal("960.00"),
                    ),
                ),
            ),
            Invoice(
                id="inv-c-471",
                contract_id="ARP-007/2025",
                supplier="PAPELARIA E SUPRIMENTOS BOA VISTA LTDA",
                supplier_cnpj="60271584000161",
                number="000471",
                issue_date=date(2025, 6, 18),
                reference_period=ReferencePeriod(year=2025, month=6),
                total=Decimal("10360.00"),
                service_description="Ref. AF-0131 - Ata nº 007/2025",
                source=InvoiceSource.PDF,
                items=(
                    BillingItem(
                        description="0001 Papel sulfite A4 75 g/m², resma com 500 folhas",
                        quantity=Decimal("400"),
                        unit_value=Decimal("25.90"),
                        total_value=Decimal("10360.00"),
                    ),
                ),
            ),
            Invoice(
                id="inv-c-502",
                contract_id="ARP-007/2025",
                supplier="PAPELARIA E SUPRIMENTOS BOA VISTA LTDA",
                supplier_cnpj="60271584000161",
                number="000502",
                issue_date=date(2025, 8, 10),
                reference_period=ReferencePeriod(year=2025, month=8),
                total=Decimal("5610.00"),
                service_description="Ref. AF-0160 - Ata nº 007/2025",
                source=InvoiceSource.PDF,
                items=(
                    BillingItem(
                        description="0001 Papel sulfite A4 75 g/m², resma com 500 folhas",
                        quantity=Decimal("200"),
                        unit_value=Decimal("24.90"),
                        total_value=Decimal("4980.00"),
                    ),
                    BillingItem(
                        description="9999 Pen drive 64 GB USB 3.0",
                        quantity=Decimal("15"),
                        unit_value=Decimal("42.00"),
                        total_value=Decimal("630.00"),
                    ),
                ),
            ),
            Invoice(
                id="inv-c-521",
                contract_id="ARP-007/2025",
                supplier="PAPELARIA E SUPRIMENTOS BOA VISTA LTDA",
                supplier_cnpj="60271584000161",
                number="000521",
                issue_date=date(2025, 9, 15),
                reference_period=ReferencePeriod(year=2025, month=9),
                total=Decimal("5260.00"),
                service_description="Ref. AF-0172 - Ata nº 007/2025",
                source=InvoiceSource.PDF,
                items=(
                    BillingItem(
                        description=toner_desc,
                        quantity=Decimal("20"),
                        unit_value=Decimal("189.00"),
                        total_value=Decimal("3780.00"),
                    ),
                    BillingItem(
                        description="0004 Pasta suspensa, caixa com 50 unidades",
                        quantity=Decimal("15"),
                        unit_value=Decimal("96.00"),
                        total_value=Decimal("1440.00"),
                    ),
                ),
            ),
            Invoice(
                id="inv-c-535",
                contract_id="ARP-007/2025",
                supplier="PAPELARIA E SUPRIMENTOS BOA VISTA LTDA",
                supplier_cnpj="60271584000161",
                number="000535",
                issue_date=date(2025, 9, 29),
                reference_period=ReferencePeriod(year=2025, month=9),
                total=Decimal("6630.00"),
                service_description="Ref. AF-0118 - Ata nº 007/2025",
                source=InvoiceSource.PDF,
                items=(
                    BillingItem(
                        description=toner_desc,
                        quantity=Decimal("30"),
                        unit_value=Decimal("189.00"),
                        total_value=Decimal("5670.00"),
                    ),
                    BillingItem(
                        description="0004 Pasta suspensa, caixa com 50 unidades",
                        quantity=Decimal("10"),
                        unit_value=Decimal("96.00"),
                        total_value=Decimal("960.00"),
                    ),
                ),
            ),
            Invoice(
                id="inv-c-558",
                contract_id="ARP-007/2025",
                supplier="PAPELARIA E SUPRIMENTOS BOA VISTA LTDA",
                supplier_cnpj="60271584000161",
                number="000558",
                issue_date=date(2025, 10, 14),
                reference_period=ReferencePeriod(year=2025, month=10),
                total=Decimal("29880.00"),
                service_description="Ref. AF-0185 - Ata nº 007/2025",
                source=InvoiceSource.PDF,
                items=(
                    BillingItem(
                        description="0001 Papel sulfite A4 75 g/m², resma com 500 folhas",
                        quantity=Decimal("1200"),
                        unit_value=Decimal("24.90"),
                        total_value=Decimal("29880.00"),
                    ),
                ),
            ),
            Invoice(
                id="inv-c-1204",
                contract_id="ARP-007/2025",
                supplier="ALFA COMERCIAL DE ESCRITÓRIO LTDA",
                supplier_cnpj="41820397000145",
                number="001204",
                issue_date=date(2025, 11, 25),
                reference_period=ReferencePeriod(year=2025, month=11),
                total=Decimal("1890.00"),
                service_description="Ref. AF-0209 - Ata nº 007/2025",
                source=InvoiceSource.PDF,
                items=(
                    BillingItem(
                        description=toner_desc,
                        quantity=Decimal("10"),
                        unit_value=Decimal("189.00"),
                        total_value=Decimal("1890.00"),
                    ),
                ),
            ),
            Invoice(
                id="inv-c-642",
                contract_id="ARP-007/2025",
                supplier="PAPELARIA E SUPRIMENTOS BOA VISTA LTDA",
                supplier_cnpj="60271584000161",
                number="000642",
                issue_date=date(2026, 3, 12),
                reference_period=ReferencePeriod(year=2026, month=3),
                total=Decimal("830.00"),
                service_description="Ref. AF-0233 - Ata nº 007/2025",
                source=InvoiceSource.PDF,
                items=(
                    BillingItem(
                        description="0003 Caneta esferográfica azul, caixa com 50 unidades",
                        quantity=Decimal("20"),
                        unit_value=Decimal("41.50"),
                        total_value=Decimal("830.00"),
                    ),
                ),
            ),
        )
        for inv_c in c_invoices:
            save_invoice(conn, inv_c)
        sync_contract_dossier_data(conn, "ARP-007/2025", auto_approve_above_threshold=True)
        for inv_c in c_invoices:
            audit_stored_invoice(conn, inv_c.id)

        # ---- Relatório combinado com os 3 contratos ----
        all_contracts = list(fetch_contracts(conn))
        all_rules = list(fetch_rules(conn))
        all_audited = [(inv, fetch_audits(conn, inv.id)[-1]) for inv in fetch_invoices(conn)]
        pay_audit = fetch_payment_cycle_audit(conn, None)
        report = build_executive_report(
            all_audited,
            payment_audit=pay_audit,
            contracts=all_contracts,
            rules=all_rules,
        )

        assert len(report.contract_sections) == 3
        sec_map = {sec.contract.id: sec.summary for sec in report.contract_sections}

        # Valida Cenário A: 0 achados, 12 notas limpas, exposição R$ 0,00
        rep_a = sec_map["LOC-2024/017"]
        assert rep_a.findings_count == 0
        assert rep_a.clean_invoices_count == 12
        assert rep_a.exposure_total == Decimal("0.00")

        # Valida Cenário B: 07/2025 limpa, 01/2025 como Média n/a (alinhada ao Apêndice),
        # e as 4 divergências + OPs/CND/Seguro-garantia presentes
        rep_b = sec_map["018/2024"]
        assert "2025/000107" in rep_b.clean_invoice_numbers
        mid_f = next(f for f in rep_b.findings if "2025/000101" in f.invoice_numbers)
        assert mid_f.severity == "Média"
        assert mid_f.risk_amount is None
        app_01 = next(e for e in rep_b.appendix_entries if e.invoice_number == "2025/000101")
        assert app_01.severity_label == "Média"
        risk_set_b = {f.risk_amount for f in rep_b.findings if f.risk_amount is not None}
        assert {
            Decimal("592.00"),
            Decimal("5920.00"),
            Decimal("9925.30"),
            Decimal("4331.04"),
            Decimal("82689.00"),
        } <= risk_set_b
        assert any("CND Federal" in f.problem for f in rep_b.findings)
        assert any("Seguro-garantia" in f.problem for f in rep_b.findings)

        # Valida Cenário C: 412 limpa e as 7 divergências somando exatamente R$ 12.910,00
        rep_c = sec_map["ARP-007/2025"]
        assert "000412" in rep_c.clean_invoice_numbers
        assert rep_c.exposure_total == Decimal("12910.00")

        # ---- Trava de Segurança: se 100% das notas de um contrato divergem no CNPJ ----
        bad_c = Contract(
            id="BAD-CTR",
            company="Órgão",
            supplier="Empresa Certa Ltda",
            supplier_cnpj="11222333000181",
            subject="Teste trava",
            validity=ValidityPeriod(start=date(2025, 1, 1), end=date(2025, 12, 31)),
            comparison_tolerance=Decimal("0.00"),
            status=ContractStatus.ACTIVE,
        )
        register_contract(conn, bad_c)
        for i in range(1, 5):
            inv_bad = Invoice(
                id=f"inv-bad-{i}",
                contract_id="BAD-CTR",
                supplier="Outro Fornecedor S.A.",
                supplier_cnpj="60271584000161",
                number=f"90{i}",
                issue_date=date(2025, i, 10),
                reference_period=ReferencePeriod(year=2025, month=i),
                total=Decimal("5000.00"),
                source=InvoiceSource.PDF,
            )
            save_invoice(conn, inv_bad)
            audit_stored_invoice(conn, inv_bad.id)
        bad_invs = fetch_invoices(conn, "BAD-CTR")
        bad_audited = [(inv, fetch_audits(conn, inv.id)[-1]) for inv in bad_invs]
        rep_bad = build_executive_report(bad_audited, contracts=[bad_c], rules=[])
        assert len(rep_bad.findings) == 1
        assert "Trava" in " ".join(rep_bad.reading_warnings)
        assert rep_bad.exposure_total == Decimal("0.00")
    finally:
        conn.close()
