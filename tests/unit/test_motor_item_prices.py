"""Testes unitários do motor `ITEM_PRICE`, saldo acumulado, AF duplicada, glosa e M-16."""

import ast
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from backend.motor import (
    ITEM_QUANTITY_CUMULATIVE_CHECK,
    ITEM_QUANTITY_MONTHLY_CHECK,
    ITEM_UNIT_PRICE_CHECK,
    MISSING_GLOSA_CHECK,
    UNREGISTERED_ITEM_CHECK,
    audit_invoice,
    check_contract_validity,
    check_duplicates,
)
from backend.schemas import (
    Amendment,
    AuditClassification,
    BillingItem,
    CheckResult,
    Contract,
    ContractRule,
    ContractStatus,
    Document,
    DocumentType,
    Evidence,
    Invoice,
    InvoiceSource,
    ReadQuality,
    ReferencePeriod,
    ReviewStatus,
    RuleRelation,
    RuleType,
    ValidityPeriod,
)


def _ev(doc_id: str = "doc-1") -> Evidence:
    return Evidence(
        source_document_id=doc_id,
        page=1,
        literal_excerpt="Tabela de itens e preços.",
    )


def _contract_limpeza() -> Contract:
    return Contract(
        id="018/2024",
        company="Fundação Municipal de Saúde",
        supplier="LIMPAR MAIS SERVIÇOS DE CONSERVAÇÃO LTDA.",
        supplier_cnpj="24681035000100",
        subject="Limpeza",
        validity=ValidityPeriod(start=date(2025, 1, 2), end=date(2025, 12, 31)),
        comparison_tolerance=Decimal("0.00"),
        status=ContractStatus.ACTIVE,
    )


def _limpeza_rules() -> tuple[ContractRule, ...]:
    return (
        ContractRule(
            id="r-serv-base",
            contract_id="018/2024",
            rule_type=RuleType.ITEM_PRICE,
            item_name="Servente de limpeza",
            value=Decimal("5920.00"),
            quantity=Decimal("14"),
            unit="posto",
            quantity_scope="MONTHLY",
            validity=ValidityPeriod(start=date(2025, 4, 1)),
            review_status=ReviewStatus.CONFIRMED,
            evidence=_ev(),
        ),
        ContractRule(
            id="r-enc-base",
            contract_id="018/2024",
            rule_type=RuleType.ITEM_PRICE,
            item_name="Encarregado",
            value=Decimal("7350.00"),
            quantity=Decimal("1"),
            unit="posto",
            quantity_scope="MONTHLY",
            validity=ValidityPeriod(start=date(2025, 1, 2)),
            review_status=ReviewStatus.CONFIRMED,
            evidence=_ev(),
        ),
        ContractRule(
            id="r-inss",
            contract_id="018/2024",
            rule_type=RuleType.INSS_RETENTION,
            value=Decimal("11"),
            unit="%",
            validity=ValidityPeriod(start=date(2025, 1, 2)),
            review_status=ReviewStatus.CONFIRMED,
            evidence=_ev(),
        ),
    )


def _limpeza_future_amendments() -> tuple[list[Amendment], list[Document]]:
    r_serv_26 = ContractRule(
        id="r-serv-26",
        contract_id="018/2024",
        rule_type=RuleType.ITEM_PRICE,
        item_name="Servente de limpeza",
        value=Decimal("6204.16"),
        quantity=Decimal("14"),
        unit="posto",
        quantity_scope="MONTHLY",
        validity=ValidityPeriod(start=date(2026, 1, 1)),
        review_status=ReviewStatus.CONFIRMED,
        evidence=_ev("doc-add"),
        relation=RuleRelation.ALTERED,
    )
    r_enc_26 = ContractRule(
        id="r-enc-26",
        contract_id="018/2024",
        rule_type=RuleType.ITEM_PRICE,
        item_name="Encarregado",
        value=Decimal("7702.80"),
        quantity=Decimal("1"),
        unit="posto",
        quantity_scope="MONTHLY",
        validity=ValidityPeriod(start=date(2026, 1, 1)),
        review_status=ReviewStatus.CONFIRMED,
        evidence=_ev("doc-add"),
        relation=RuleRelation.ALTERED,
    )
    docs = [
        Document(
            id="doc-add",
            contract_id="018/2024",
            document_type=DocumentType.AMENDMENT,
            file_name="B2.pdf",
            received_at=datetime(2025, 12, 12, 10, 0, tzinfo=UTC),
            file_hash="a" * 64,
            read_quality=ReadQuality.OK,
            reviewed=True,
            extracted_text="2º Termo Aditivo",
        )
    ]
    amends = [
        Amendment(
            id="am-1",
            affected_rule_id="r-serv-base",
            new_rule=r_serv_26,
            source_document_id="doc-add",
            effective_date=date(2026, 1, 1),
            relation=RuleRelation.ALTERED,
        ),
        Amendment(
            id="am-2",
            affected_rule_id="r-enc-base",
            new_rule=r_enc_26,
            source_document_id="doc-add",
            effective_date=date(2026, 1, 1),
            relation=RuleRelation.ALTERED,
        ),
    ]
    return amends, docs


def test_monthly_quantity_glosa_and_premature_repactuacao() -> None:
    amends, docs = _limpeza_future_amendments()
    contract = _contract_limpeza()
    rules = _limpeza_rules()

    # 05/2025: glosa de R$ 592,00 não descontada
    nf_05 = Invoice(
        id="inv-05",
        contract_id="018/2024",
        supplier="LIMPAR MAIS SERVIÇOS DE CONSERVAÇÃO LTDA.",
        supplier_cnpj="24681035000100",
        number="2025/000105",
        issue_date=date(2025, 5, 30),
        reference_period=ReferencePeriod(year=2025, month=5),
        total=Decimal("90230.00"),
        inss_withheld=Decimal("9925.30"),
        expected_glosa=Decimal("592.00"),
        source=InvoiceSource.PDF,
        items=(
            BillingItem(
                description="Posto de Servente de limpeza",
                quantity=Decimal("14"),
                unit_value=Decimal("5920.00"),
                total_value=Decimal("82880.00"),
            ),
            BillingItem(
                description="Posto de Encarregado",
                quantity=Decimal("1"),
                unit_value=Decimal("7350.00"),
                total_value=Decimal("7350.00"),
            ),
        ),
    )
    res_05 = audit_invoice(nf_05, contract=contract, rules=rules, amendments=amends, documents=docs)
    assert res_05.classification is AuditClassification.DIVERGENCE
    glosa_chk = next(c for c in res_05.checks if c.name == MISSING_GLOSA_CHECK)
    assert glosa_chk.result is CheckResult.DIVERGENCE
    assert res_05.difference == Decimal("592.00")

    # 07/2025: glosa de R$ 394,67 devidamente descontada → Sem divergência
    nf_07 = Invoice(
        id="inv-07",
        contract_id="018/2024",
        supplier="LIMPAR MAIS SERVIÇOS DE CONSERVAÇÃO LTDA.",
        supplier_cnpj="24681035000100",
        number="2025/000107",
        issue_date=date(2025, 7, 31),
        reference_period=ReferencePeriod(year=2025, month=7),
        total=Decimal("89835.33"),
        inss_withheld=Decimal("9881.89"),
        expected_glosa=Decimal("394.67"),
        source=InvoiceSource.PDF,
        items=(
            BillingItem(
                description="Posto de Servente de limpeza",
                quantity=Decimal("14"),
                unit_value=Decimal("5920.00"),
                total_value=Decimal("82880.00"),
            ),
            BillingItem(
                description="Posto de Encarregado",
                quantity=Decimal("1"),
                unit_value=Decimal("7350.00"),
                total_value=Decimal("7350.00"),
            ),
            BillingItem(
                description="Desconto por ausências sem reposição (glosa)",
                quantity=Decimal("2"),
                unit_value=Decimal("197.335"),
                total_value=Decimal("394.67"),
            ),
        ),
    )
    res_07 = audit_invoice(nf_07, contract=contract, rules=rules, amendments=amends, documents=docs)
    assert res_07.classification is AuditClassification.NO_DIVERGENCE
    assert res_07.base == Decimal("89835.33")

    # 06/2025: 15 postos de servente vs 14 contratados → +R$ 5.920,00
    nf_06 = nf_05.model_copy(
        update={
            "id": "inv-06",
            "number": "2025/000106",
            "issue_date": date(2025, 6, 30),
            "reference_period": ReferencePeriod(year=2025, month=6),
            "total": Decimal("96150.00"),
            "inss_withheld": Decimal("10576.50"),
            "expected_glosa": None,
            "items": (
                BillingItem(
                    description="Posto de Servente de limpeza",
                    quantity=Decimal("15"),
                    unit_value=Decimal("5920.00"),
                    total_value=Decimal("88800.00"),
                ),
                nf_05.items[1],
            ),
        }
    )
    res_06 = audit_invoice(nf_06, contract=contract, rules=rules, amendments=amends, documents=docs)
    assert res_06.classification is AuditClassification.DIVERGENCE
    assert any(c.name == ITEM_QUANTITY_MONTHLY_CHECK for c in res_06.checks)
    assert res_06.difference == Decimal("5920.00")

    # 11/2025: repactuação de 01/01/2026 aplicada antecipadamente → +R$ 4.331,04
    nf_11 = nf_05.model_copy(
        update={
            "id": "inv-11",
            "number": "2025/000111",
            "issue_date": date(2025, 11, 28),
            "reference_period": ReferencePeriod(year=2025, month=11),
            "total": Decimal("94561.04"),
            "inss_withheld": Decimal("10401.71"),
            "expected_glosa": None,
            "items": (
                BillingItem(
                    description="Posto de Servente de limpeza",
                    quantity=Decimal("14"),
                    unit_value=Decimal("6204.16"),
                    total_value=Decimal("86858.24"),
                ),
                BillingItem(
                    description="Posto de Encarregado",
                    quantity=Decimal("1"),
                    unit_value=Decimal("7702.80"),
                    total_value=Decimal("7702.80"),
                ),
            ),
        }
    )
    res_11 = audit_invoice(nf_11, contract=contract, rules=rules, amendments=amends, documents=docs)
    assert res_11.classification is AuditClassification.DIVERGENCE
    unit_chk = next(c for c in res_11.checks if c.name == ITEM_UNIT_PRICE_CHECK)
    assert "01/01/2026" in unit_chk.message
    assert res_11.difference == Decimal("4331.04")


def test_independent_inss_runs_even_with_unreviewed_amendment() -> None:
    amends, docs = _limpeza_future_amendments()
    unreviewed_docs = [docs[0].model_copy(update={"reviewed": False})]
    amends_early = [amends[0].model_copy(update={"effective_date": date(2025, 4, 1)})]
    nf_09 = Invoice(
        id="inv-09",
        contract_id="018/2024",
        supplier="LIMPAR MAIS SERVIÇOS DE CONSERVAÇÃO LTDA.",
        supplier_cnpj="24681035000100",
        number="2025/000109",
        issue_date=date(2025, 9, 30),
        reference_period=ReferencePeriod(year=2025, month=9),
        total=Decimal("90230.00"),
        inss_withheld=Decimal("0.00"),
        source=InvoiceSource.PDF,
        items=(
            BillingItem(
                description="Posto de Servente de limpeza",
                quantity=Decimal("14"),
                unit_value=Decimal("5920.00"),
                total_value=Decimal("82880.00"),
            ),
        ),
    )
    res = audit_invoice(
        nf_09,
        contract=_contract_limpeza(),
        rules=_limpeza_rules(),
        amendments=amends_early,
        documents=unreviewed_docs,
    )
    assert res.classification is AuditClassification.DIVERGENCE
    assert any(
        c.name == "inss_retention" and c.result is CheckResult.DIVERGENCE for c in res.checks
    )


def test_ata_cumulative_cap_unregistered_item_af_duplicate_and_expiry_issue_date() -> None:
    ata = Contract(
        id="ARP-007/2025",
        company="IPREV Vale Dourado",
        supplier="PAPELARIA E SUPRIMENTOS BOA VISTA LTDA.",
        supplier_cnpj="60271584000161",
        subject="Material de expediente",
        validity=ValidityPeriod(start=date(2025, 3, 10), end=date(2026, 3, 9)),
        comparison_tolerance=Decimal("0.00"),
        status=ContractStatus.ACTIVE,
    )
    sulfite_rule = ContractRule(
        id="r-sulfite",
        contract_id="ARP-007/2025",
        rule_type=RuleType.ITEM_PRICE,
        item_name="Papel sulfite A4 75 g/m², resma com 500 folhas",
        value=Decimal("24.90"),
        quantity=Decimal("2000"),
        unit="resma",
        quantity_scope="TOTAL",
        validity=ValidityPeriod(start=date(2025, 3, 10), end=date(2026, 3, 9)),
        review_status=ReviewStatus.CONFIRMED,
        evidence=_ev(),
    )

    def _danfe(iid: str, num: str, dt: date, qty: str, unit_p: str, total: str, af: str) -> Invoice:
        return Invoice(
            id=iid,
            contract_id="ARP-007/2025",
            supplier="PAPELARIA E SUPRIMENTOS BOA VISTA LTDA.",
            supplier_cnpj="60271584000161",
            number=num,
            issue_date=dt,
            reference_period=ReferencePeriod(year=dt.year, month=dt.month),
            total=Decimal(total),
            service_description=f"Ref. {af} - Ata nº 007/2025",
            source=InvoiceSource.PDF,
            items=(
                BillingItem(
                    description="0001 Papel sulfite A4 75 g/m², resma com 500 folhas",
                    quantity=Decimal(qty),
                    unit_value=Decimal(unit_p),
                    total_value=Decimal(total),
                ),
            ),
        )

    nf1 = _danfe("i1", "000412", date(2025, 4, 12), "900", "24.90", "22410.00", "AF-0101")
    nf_cum = _danfe("i2", "000558", date(2025, 10, 14), "1200", "24.90", "29880.00", "AF-0185")
    res_cum = audit_invoice(nf_cum, contract=ata, rules=(sulfite_rule,), other_invoices=(nf1,))
    assert any(c.name == ITEM_QUANTITY_CUMULATIVE_CHECK for c in res_cum.checks)
    assert res_cum.difference == Decimal("2490.00")

    # Item não registrado
    nf_unreg = Invoice(
        id="i3",
        contract_id="ARP-007/2025",
        supplier="PAPELARIA E SUPRIMENTOS BOA VISTA LTDA.",
        supplier_cnpj="60271584000161",
        number="000502",
        issue_date=date(2025, 8, 10),
        reference_period=ReferencePeriod(year=2025, month=8),
        total=Decimal("5610.00"),
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
    )
    res_unreg = audit_invoice(nf_unreg, contract=ata, rules=(sulfite_rule,))
    assert any(c.name == UNREGISTERED_ITEM_CHECK for c in res_unreg.checks)
    assert res_unreg.difference == Decimal("630.00")

    # AF duplicada em meses distintos
    nf_af2 = _danfe("i4", "000535", date(2025, 9, 29), "10", "24.90", "249.00", "AF-0101")
    dups = check_duplicates((nf1, nf_af2))
    assert "i1" in dups and "i4" in dups
    assert "AF-101" in dups["i4"].message

    # Emissão após o vencimento dentro do próprio mês (12/03/2026 > 09/03/2026)
    val_res = check_contract_validity(
        ata,
        (),
        ReferencePeriod(year=2026, month=3),
        issue_date=date(2026, 3, 12),
    )
    assert val_res.reasons[0].code.value == "OUTSIDE_CONTRACT_VALIDITY"


def test_m16_item_prices_module_has_zero_float() -> None:
    src_path = Path(__file__).resolve().parents[2] / "backend" / "motor" / "item_prices.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            raise AssertionError(
                f"float literal proibido encontrado em item_prices.py: {node.value}"
            )
        if isinstance(node, ast.Name) and node.id == "float":
            raise AssertionError("Uso de float proibido em item_prices.py")


def test_jurista_resolves_delta_quantity_drops_redundant_monthly_and_timeline_inherits_qty() -> (
    None
):
    from backend.agentes import (
        ExtractedRuleCandidate,
        FakeLLMClient,
        JuristaExtractionPayload,
        JuristaInput,
        run_jurista_agent,
    )
    from backend.leitura import PdfPage, PdfReadResult
    from backend.motor import (
        apply_amendments,
        match_invoice_item_to_rule_key,
        select_applicable_item_rules,
    )

    excerpt = (
        "CLÁUSULA PRIMEIRA. Fica acrescido o quantitativo em 2 (dois) postos de servente de "
        "limpeza, passando de 12 (doze) para 14 (quatorze), com efeitos a partir de 01/04/2025, "
        "ao preço unitário de R$ 5.920,00 (cinco mil, novecentos e vinte reais) por posto/mês."
    )
    pdf = PdfReadResult(
        pages=(PdfPage(page_number=1, text=excerpt),),
        read_quality=ReadQuality.OK,
        full_text=excerpt,
    )
    client = FakeLLMClient(
        [
            JuristaExtractionPayload(
                rules=(
                    ExtractedRuleCandidate(
                        rule_type=RuleType.ITEM_PRICE,
                        item_name="Servente de limpeza",
                        new_value=Decimal("5920.00"),
                        quantity=Decimal("2"),
                        unit="posto",
                        quantity_scope="MONTHLY",
                        validity_start=date(2025, 4, 1),
                        relation=RuleRelation.ALTERED,
                        page=1,
                        literal_excerpt=excerpt,
                    ),
                    ExtractedRuleCandidate(
                        rule_type=RuleType.MONTHLY_PRICE,
                        new_value=Decimal("82880.00"),
                        validity_start=date(2025, 4, 1),
                        relation=RuleRelation.ALTERED,
                        page=1,
                        literal_excerpt=excerpt,
                    ),
                )
            )
        ]
    )
    res = run_jurista_agent(
        JuristaInput.from_pdf("doc-b2", "018/2024", pdf, is_amendment=True),
        client,
    )
    assert len(res.rules) == 1
    assert res.rules[0].rule_type is RuleType.ITEM_PRICE
    assert res.rules[0].quantity == Decimal("14")
    assert res.rules[0].verification_score == Decimal("1.00")

    base_serv = ContractRule(
        id="r-serv-base",
        contract_id="018/2024",
        rule_type=RuleType.ITEM_PRICE,
        item_name="Servente de limpeza",
        value=Decimal("5920.00"),
        quantity=Decimal("12"),
        unit="posto",
        quantity_scope="MONTHLY",
        validity=ValidityPeriod(start=date(2025, 1, 2)),
        review_status=ReviewStatus.CONFIRMED,
        evidence=_ev(),
    )
    repact_no_qty = ContractRule(
        id="r-serv-26",
        contract_id="018/2024",
        rule_type=RuleType.ITEM_PRICE,
        item_name="Servente de limpeza",
        value=Decimal("6204.16"),
        quantity=None,
        unit=None,
        quantity_scope=None,
        validity=ValidityPeriod(start=date(2026, 1, 1)),
        review_status=ReviewStatus.CONFIRMED,
        evidence=_ev("doc-b2"),
        relation=RuleRelation.ALTERED,
    )
    add_14 = res.rules[0].model_copy(update={"review_status": ReviewStatus.CONFIRMED})
    tl = apply_amendments(
        [base_serv],
        [
            Amendment(
                id="am-1",
                affected_rule_id=base_serv.id,
                new_rule=add_14,
                source_document_id="doc-b2",
                effective_date=date(2025, 4, 1),
                relation=RuleRelation.ALTERED,
            ),
            Amendment(
                id="am-2",
                affected_rule_id=add_14.id,
                new_rule=repact_no_qty,
                source_document_id="doc-b2",
                effective_date=date(2026, 1, 1),
                relation=RuleRelation.ALTERED,
            ),
        ],
    )
    sel_26 = select_applicable_item_rules(tl.rules, ReferencePeriod(year=2026, month=1))
    assert sel_26["servente limpeza"].rule is not None
    assert sel_26["servente limpeza"].rule.quantity == Decimal("14")
    assert sel_26["servente limpeza"].rule.quantity_scope == "MONTHLY"

    keys = ("papel sulfite a4 75 g m2 resma 500 folhas", "toner impressora hp laserjet 85a")
    assert (
        match_invoice_item_to_rule_key("Resma de papel sulfite A4 Chamex", keys)
        == "papel sulfite a4 75 g m2 resma 500 folhas"
    )
    assert match_invoice_item_to_rule_key("Pen drive 64 GB USB 3.0", keys) is None
