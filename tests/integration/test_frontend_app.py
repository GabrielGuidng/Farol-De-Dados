"""Testes de integração da interface Streamlit (Bloco 1: 7.1, 7.2 e 7.3) via AppTest."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from pytest import MonkeyPatch
from streamlit.testing.v1 import AppTest

from backend.agentes import (
    ExtractedRuleCandidate,
    FakeLLMClient,
    JuristaExtractionPayload,
)
from backend.api import (
    fetch_audits,
    fetch_contract,
    fetch_contracts,
    fetch_documents,
    fetch_invoices,
    fetch_reviews,
    fetch_rules,
    import_billing_document,
    mark_amendment_reviewed,
    process_contract_document,
    register_amendment,
    register_contract,
    review_rule,
    seed_synthetic_demo,
)
from backend.persistencia import connect, init_db
from backend.schemas import (
    Amendment,
    Contract,
    ContractStatus,
    DocumentType,
    ReviewDecision,
    ReviewStatus,
    ReviewTargetType,
    RuleRelation,
    RuleType,
    ValidityPeriod,
)
from tests.fixtures.synthetic_files import SYNTHETIC_DIR

_APP_PATH = str(Path(__file__).resolve().parents[2] / "frontend" / "app.py")
_TS = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


def _sample_contract(contract_id: str = "CT-2026-001") -> Contract:
    return Contract(
        id=contract_id,
        company="Empresa Exemplo S.A.",
        supplier="Fornecedor Exemplo Ltda.",
        supplier_cnpj="11.222.333/0001-81",
        subject="Serviços de suporte técnico contínuo",
        validity=ValidityPeriod(start=date(2026, 1, 1), end=date(2026, 12, 31)),
        comparison_tolerance=Decimal("0.00"),
        status=ContractStatus.ACTIVE,
    )


def test_app_register_contract_and_preserve_theme_state(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    db_path = tmp_path / "ui_register.db"
    monkeypatch.setenv("FAROL_DB_PATH", str(db_path))

    at = AppTest.from_file(_APP_PATH, default_timeout=15)
    at.run()
    assert not at.exception
    assert at.selectbox(key="upload_doc_mode").value == "AUTO"
    assert at.button(key="process_pc_files_btn").label == "Importar e processar arquivos"

    # Clicar em importar sem arquivos exibe aviso controlado sem exceção.
    at.button(key="process_pc_files_btn").click().run()
    assert not at.exception

    # Preenche o formulário da etapa 7.1 (Contratos e Documentos é a vista padrão).
    at.text_input(key="contract_form_id").set_value("CT-2026-777")
    at.text_input(key="contract_form_company").set_value("Empresa Alpha S.A.")
    at.text_input(key="contract_form_supplier").set_value("Fornecedor Beta Ltda.")
    at.text_input(key="contract_form_cnpj").set_value("11.222.333/0001-81")
    at.text_input(key="contract_form_subject").set_value("Manutenção mensal de servidores")
    at.text_input(key="contract_form_tolerance").set_value("5,00")
    at.button(key="FormSubmitter:contract_register_form-Cadastrar contrato").click().run()
    assert not at.exception

    with connect(db_path) as conn:
        contracts = fetch_contracts(conn)
        assert [c.id for c in contracts] == ["CT-2026-777"]
        saved = fetch_contract(conn, "CT-2026-777")
        assert saved is not None
        assert saved.comparison_tolerance == Decimal("5.00")

    # Alternar para Tema Claro na sidebar preserva seleção e vista ativa.
    at.sidebar.radio(key="theme_selector_widget").set_value("☀️ Claro").run()
    assert not at.exception
    assert at.session_state["theme"] == "light"
    assert at.session_state["selected_contract_id"] == "CT-2026-777"
    assert at.session_state["active_view"] == "Contratos e Documentos"

    at.sidebar.radio(key="theme_selector_widget").set_value("🌙 Escuro").run()
    assert at.session_state["theme"] == "dark"
    assert at.session_state["selected_contract_id"] == "CT-2026-777"


def _rendered_html(at: AppTest) -> str:
    md_parts = [md.value for md in at.markdown]
    html_parts = [str(getattr(el, "body", getattr(el, "value", ""))) for el in at.get("html")]
    return "\n".join(md_parts + html_parts)


def test_app_rule_review_amendment_and_competence_view(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    db_path = tmp_path / "ui_rules.db"
    monkeypatch.setenv("FAROL_DB_PATH", str(db_path))
    init_db(db_path).close()

    with connect(db_path) as conn:
        contract = _sample_contract()
        register_contract(conn, contract)
        base_proc = process_contract_document(
            conn,
            contract_id=contract.id,
            document_id="doc-base",
            file_name="ct_2026_001_contrato_base.pdf",
            content=SYNTHETIC_DIR / "ct_2026_001_contrato_base.pdf",
            client=FakeLLMClient(
                [
                    JuristaExtractionPayload(
                        rules=(
                            ExtractedRuleCandidate(
                                rule_type=RuleType.MONTHLY_PRICE,
                                new_value=Decimal("10000.00"),
                                validity_start=date(2026, 1, 1),
                                validity_end=date(2026, 12, 31),
                                relation=RuleRelation.CREATED,
                                page=2,
                                literal_excerpt=(
                                    "Cláusula 3ª: O valor mensal é de R$ 10.000,00 de 01/01/2026 "
                                    "até 31/12/2026."
                                ),
                            ),
                            ExtractedRuleCandidate(
                                rule_type=RuleType.OVERAGE_PRICE,
                                new_value=Decimal("999.00"),
                                validity_start=date(2026, 1, 1),
                                relation=RuleRelation.CREATED,
                                page=3,
                                literal_excerpt=(
                                    "Cláusula 5ª: A hora excedente custa R$ 150,00 "
                                    "desde 01/01/2026."
                                ),
                            ),
                        )
                    )
                ]
            ),
            received_at=_TS,
        )
        add_proc = process_contract_document(
            conn,
            contract_id=contract.id,
            document_id="doc-add-01",
            file_name="ct_2026_001_aditivo_01.docx",
            content=SYNTHETIC_DIR / "ct_2026_001_aditivo_01.docx",
            client=FakeLLMClient(
                [
                    JuristaExtractionPayload(
                        rules=(
                            ExtractedRuleCandidate(
                                rule_type=RuleType.MONTHLY_PRICE,
                                new_value=Decimal("10800.00"),
                                validity_start=date(2026, 5, 1),
                                relation=RuleRelation.ALTERED,
                                clause="Cláusula 1ª - Reajuste Contratual",
                                paragraph="1",
                                literal_excerpt=(
                                    "Pelo 1º Termo Aditivo ao contrato CT-2026-001, o valor mensal "
                                    "passa de R$ 10.000,00 para R$ 10.800,00 a partir de "
                                    "01/05/2026."
                                ),
                            ),
                        )
                    )
                ]
            ),
            document_type=DocumentType.AMENDMENT,
            received_at=_TS,
        )
        base_monthly = base_proc.rules[0]
        overage_rule = base_proc.rules[1]
        add_monthly = add_proc.rules[0]
        register_amendment(
            conn,
            Amendment(
                id="amend-01",
                affected_rule_id=base_monthly.id,
                new_rule=add_monthly,
                source_document_id="doc-add-01",
                effective_date=date(2026, 5, 1),
                relation=RuleRelation.ALTERED,
            ),
        )

    at = AppTest.from_file(_APP_PATH, default_timeout=15)
    at.run()
    assert not at.exception

    at.sidebar.radio(key="nav_view_radio").set_value("Regras e Vigência").run()
    assert not at.exception

    # Verifica destaque do trecho literal dentro do texto original completo (7.2).
    initial_rules_html = _rendered_html(at)
    assert "original-doc-details" in initial_rules_html
    assert '<mark class="doc-highlight">' in initial_rules_html

    # Confirma regra base mensal em 1 clique pela UI (7.2).
    at.button(key=f"confirm_rule_btn_{base_monthly.id}").click().run()
    assert not at.exception

    # Corrige regra de hora excedente inconclusiva (999 -> 150,00) via modal de correção.
    at.button(key=f"open_corr_btn_{overage_rule.id}").click().run()
    assert not at.exception
    at.text_input(key=f"corr_val_{overage_rule.id}").set_value("150,00")
    at.text_input(key=f"corr_start_{overage_rule.id}").set_value("2026-01")
    at.text_input(key=f"corr_end_{overage_rule.id}").set_value("2026-12")
    at.text_input(key=f"corr_clause_{overage_rule.id}").set_value("Cláusula 5ª")
    at.text_area(key=f"just_{overage_rule.id}").set_value(
        "Corrigido valor da hora excedente conforme página 3."
    )
    at.button(
        key=f"FormSubmitter:rule_correct_form_{overage_rule.id}-Salvar correção"
    ).click().run()
    assert not at.exception

    # Confirma as demais regras pendentes em lote pelo botão do contrato.
    at.button(key="confirm_all_pending_rules_btn").click().run()
    assert not at.exception

    # Marca aditivo como revisado em 1 clique.
    at.button(key="mark_amend_btn_doc-add-01").click().run()
    assert not at.exception

    # Consulta competência 2026-05 na aba de vigência (7.3).
    at.text_input(key="competence_input").set_value("2026-05").run()
    assert not at.exception
    rendered_html = _rendered_html(at)
    assert "R$ 10.800,00" in rendered_html
    assert "ct_2026_001_aditivo_01.docx" in rendered_html

    with connect(db_path) as conn:
        rules_by_id = {r.id: r for r in fetch_rules(conn, "CT-2026-001")}
        assert rules_by_id[base_monthly.id].review_status is ReviewStatus.CONFIRMED
        assert rules_by_id[overage_rule.id].review_status is ReviewStatus.CORRECTED
        assert rules_by_id[overage_rule.id].value == Decimal("150.00")
        assert rules_by_id[overage_rule.id].validity.end == date(2026, 12, 31)
        assert rules_by_id[overage_rule.id].evidence.clause == "Cláusula 5ª"
        assert rules_by_id[add_monthly.id].review_status is ReviewStatus.CONFIRMED
        docs_by_id = {d.id: d for d in fetch_documents(conn, "CT-2026-001")}
        assert docs_by_id["doc-add-01"].reviewed is True
        assert len(fetch_reviews(conn)) == 4


def test_app_billing_ambiguous_link_audit_memory_and_decisions(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    db_path = tmp_path / "ui_billing_74.db"
    monkeypatch.setenv("FAROL_DB_PATH", str(db_path))
    init_db(db_path).close()

    ambiguous_xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00">\n'
        b"  <NFe>\n"
        b'    <infNFe Id="NFe-104A" versao="4.00">\n'
        b"      <ide><nNF>104</nNF><dhEmi>2026-10-30T10:00:00-03:00</dhEmi></ide>\n"
        b"      <emit><CNPJ>11222333000181</CNPJ><xNome>Fornecedor Exemplo Ltda.</xNome></emit>\n"
        b'      <det nItem="1"><prod>\n'
        b"        <xProd>Mensalidade de suporte</xProd><qCom>1</qCom><uCom>mes</uCom>\n"
        b"        <vUnCom>10800.00</vUnCom><vProd>10800.00</vProd>\n"
        b"      </prod></det>\n"
        b"      <total><ICMSTot><vNF>10800.00</vNF></ICMSTot></total>\n"
        b"      <infAdic><infCpl>Competencia 2026-10</infCpl></infAdic>\n"
        b"    </infNFe>\n"
        b"  </NFe>\n"
        b"</nfeProc>\n"
    )

    with connect(db_path) as conn:
        register_contract(conn, _sample_contract("CT-2026-001"))
        register_contract(conn, _sample_contract("CT-2026-001-B"))
        base_proc = process_contract_document(
            conn,
            contract_id="CT-2026-001",
            document_id="doc-base",
            file_name="ct_2026_001_contrato_base.pdf",
            content=SYNTHETIC_DIR / "ct_2026_001_contrato_base.pdf",
            client=FakeLLMClient(
                [
                    JuristaExtractionPayload(
                        rules=(
                            ExtractedRuleCandidate(
                                rule_type=RuleType.MONTHLY_PRICE,
                                new_value=Decimal("10000.00"),
                                validity_start=date(2026, 1, 1),
                                validity_end=date(2026, 12, 31),
                                relation=RuleRelation.CREATED,
                                page=2,
                                literal_excerpt=(
                                    "Cláusula 3ª: O valor mensal é de R$ 10.000,00 de 01/01/2026 "
                                    "até 31/12/2026."
                                ),
                            ),
                        )
                    )
                ]
            ),
            received_at=_TS,
        )
        base_rule = review_rule(
            conn,
            base_proc.rules[0].id,
            user="auditor",
            decision=ReviewDecision.CONFIRM,
            justification="Regra base confirmada.",
            reviewed_at=_TS,
        ).rule
        add_proc = process_contract_document(
            conn,
            contract_id="CT-2026-001",
            document_id="doc-add-02",
            file_name="ct_2026_001_aditivo_02.pdf",
            content=SYNTHETIC_DIR / "ct_2026_001_aditivo_02.pdf",
            client=FakeLLMClient(
                [
                    JuristaExtractionPayload(
                        rules=(
                            ExtractedRuleCandidate(
                                rule_type=RuleType.MONTHLY_PRICE,
                                new_value=Decimal("10500.00"),
                                validity_start=date(2026, 9, 1),
                                relation=RuleRelation.ALTERED,
                                page=2,
                                literal_excerpt=(
                                    "Cláusula 2ª: Passa de R$ 10.800,00 para R$ 10.500,00 a "
                                    "partir de 01/09/2026."
                                ),
                            ),
                        )
                    )
                ]
            ),
            document_type=DocumentType.AMENDMENT,
            received_at=_TS,
        )
        add_rule = review_rule(
            conn,
            add_proc.rules[0].id,
            user="auditor",
            decision=ReviewDecision.CONFIRM,
            justification="Aditivo 02 confirmado.",
            reviewed_at=_TS,
        ).rule
        register_amendment(
            conn,
            Amendment(
                id="amend-02",
                affected_rule_id=base_rule.id,
                new_rule=add_rule,
                source_document_id="doc-add-02",
                effective_date=date(2026, 9, 1),
                relation=RuleRelation.ALTERED,
            ),
        )
        mark_amendment_reviewed(
            conn,
            "doc-add-02",
            user="auditor",
            justification="Aditivo revisado.",
            reviewed_at=_TS,
        )
        imp = import_billing_document(
            conn,
            document_id="doc-nfe-104",
            file_name="nfe_104_ambigua.xml",
            content=ambiguous_xml,
            received_at=_TS,
            invoice_id="inv-ambig-104",
        )
        assert imp.unlinked_invoice_ids == ("inv-ambig-104",)

    at = AppTest.from_file(_APP_PATH, default_timeout=15)
    at.run()
    assert not at.exception

    at.sidebar.radio(key="nav_view_radio").set_value("Cobranças e Auditoria").run()
    assert not at.exception

    # Vínculo manual do CNPJ ambíguo ao contrato CT-2026-001.
    at.selectbox(key="assign_select_inv-ambig-104").set_value("CT-2026-001")
    at.button(
        key="FormSubmitter:assign_contract_form_inv-ambig-104-Vincular contrato"
    ).click().run()
    assert not at.exception

    # Executa a auditoria determinística pela UI.
    at.button(key="audit_visible_btn").click().run()
    assert not at.exception

    rendered_html = _rendered_html(at)
    assert "Com divergência" in rendered_html
    assert "R$ 10.800,00" in rendered_html
    assert "R$ 10.500,00" in rendered_html
    assert "+R$ 300,00" in rendered_html
    assert "calc-memory" in rendered_html

    with connect(db_path) as conn:
        linked_inv = fetch_invoices(conn, "CT-2026-001")
        assert [i.id for i in linked_inv] == ["inv-ambig-104"]
        audits = fetch_audits(conn, "inv-ambig-104")
        assert len(audits) == 1
        audit_id = audits[0].result.id

    # Registra decisão humana ("Ressalvar") com justificativa no modal do cartão.
    at.button(key=f"open_audit_corr_btn_{audit_id}").click().run()
    assert not at.exception
    at.text_area(key=f"audit_dec_just_{audit_id}").set_value(
        "Diferença de R$ 300,00 ressalvada para glosa."
    )
    at.button(key=f"FormSubmitter:audit_decision_form_{audit_id}-Salvar ressalva").click().run()
    assert not at.exception

    with connect(db_path) as conn:
        revs = fetch_reviews(
            conn,
            target_type=ReviewTargetType.AUDIT_RESULT,
            target_id=audit_id,
        )
        assert len(revs) == 1
        assert revs[0].decision is ReviewDecision.CORRECT
        assert revs[0].new_status is ReviewStatus.CORRECTED

    # Testa exclusão individual da cobrança mantendo a trilha `reviews` íntegra.
    at.button(key="delete_invoice_btn_inv-ambig-104").click().run()
    assert not at.exception
    with connect(db_path) as conn:
        assert fetch_invoices(conn, "CT-2026-001") == ()
        assert len(fetch_reviews(conn, target_type=ReviewTargetType.AUDIT_RESULT)) == 1


def test_app_delete_uploaded_documents_and_dashboard_report_view(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    db_path = tmp_path / "ui_dashboard_75.db"
    monkeypatch.setenv("FAROL_DB_PATH", str(db_path))
    init_db(db_path).close()

    with connect(db_path) as conn:
        seed_synthetic_demo(conn, SYNTHETIC_DIR, reviewed_at=_TS)
        initial_reviews_count = len(fetch_reviews(conn))

    at = AppTest.from_file(_APP_PATH, default_timeout=15)
    at.run()
    assert not at.exception

    # Abre Painel e Relatório (7.5): gráficos e valores aparecem imediatamente na abertura.
    at.sidebar.radio(key="nav_view_radio").set_value("Painel e Relatório").run()
    assert not at.exception
    assert len(at.get("vega_lite_chart")) == 2

    at.button(key="dashboard_audit_pending_btn").click().run()
    assert not at.exception

    rendered_html = _rendered_html(at)
    assert "Relatório Executivo de Auditoria" in rendered_html or any(
        "Relatório Executivo de Auditoria" in sh.value for sh in at.subheader
    )
    assert "+R$ 300,00" in rendered_html
    assert "Exposição estimada" in rendered_html
    assert "10/2026" in rendered_html
    assert len(at.get("vega_lite_chart")) == 2
    # Botões de download do Relatório Executivo: HTML imprimível, Excel (.xlsx) e CSV.
    assert len(at.get("download_button")) == 3

    at.button(key="print_exec_report_btn").click().run()
    assert not at.exception
    assert len(at.get("iframe")) >= 1

    at.sidebar.radio(key="theme_selector_widget").set_value("☀️ Claro").run()
    assert not at.exception
    assert at.session_state["theme"] == "light"

    # Vai para Contratos e Documentos e testa apagar 1 documento e depois todos.
    at.sidebar.radio(key="nav_view_radio").set_value("Contratos e Documentos").run()
    assert not at.exception

    with connect(db_path) as conn:
        docs_before = fetch_documents(conn, "CT-2026-001")
        assert len(docs_before) >= 6

    at.selectbox(key="delete_single_doc_select").set_value("doc-inv-demo-104")
    at.button(key="delete_single_doc_btn").click().run()
    assert not at.exception

    with connect(db_path) as conn:
        docs_after_single = fetch_documents(conn, "CT-2026-001")
        assert all(doc.id != "doc-inv-demo-104" for doc in docs_after_single)

    # Tentar apagar todos sem marcar confirmação mantém documentos intactos.
    at.button(key="delete_all_docs_btn").click().run()
    assert not at.exception
    with connect(db_path) as conn:
        assert len(fetch_documents(conn, "CT-2026-001")) == len(docs_after_single)

    at.checkbox(key="confirm_delete_listed_docs").set_value(True)
    at.button(key="delete_all_docs_btn").click().run()
    assert not at.exception

    with connect(db_path) as conn:
        assert fetch_documents(conn, "CT-2026-001") == ()
        assert len(fetch_reviews(conn)) == initial_reviews_count

    at.button(key="delete_empty_contract_btn").click().run()
    assert not at.exception
    with connect(db_path) as conn:
        assert fetch_contract(conn, "CT-2026-001") is None


def test_app_history_seed_demo_decisions_filter_and_full_reset(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    db_path = tmp_path / "ui_history_76.db"
    monkeypatch.setenv("FAROL_DB_PATH", str(db_path))

    at = AppTest.from_file(_APP_PATH, default_timeout=15)
    at.run()
    assert not at.exception

    # Navega para Histórico e Dados (7.6) com banco inicialmente vazio.
    at.sidebar.radio(key="nav_view_radio").set_value("Histórico e Dados").run()
    assert not at.exception

    # Carrega o cenário sintético de demonstração com 1 clique.
    at.button(key="seed_demo_btn").click().run()
    assert not at.exception

    with connect(db_path) as conn:
        assert [c.id for c in fetch_contracts(conn)] == ["CT-2026-001"]
        reviews_count = len(fetch_reviews(conn))
        assert reviews_count >= 8

    # Testa filtro por tipo de alvo e alterna tema para Claro preservando estado.
    at.segmented_control(key="history_target_filter").set_value("Regras").run()
    assert not at.exception
    assert len(at.get("download_button")) == 1

    at.sidebar.radio(key="theme_selector_widget").set_value("☀️ Claro").run()
    assert not at.exception
    assert at.session_state["theme"] == "light"

    # Clicar em Apagar todos os dados sem confirmação mantém tudo intacto.
    at.button(key="reset_all_data_btn").click().run()
    assert not at.exception
    with connect(db_path) as conn:
        assert len(fetch_contracts(conn)) == 1
        assert len(fetch_reviews(conn)) == reviews_count

    # Confirmar exclusão total apaga contratos, documentos, regras, cobranças e histórico.
    at.checkbox(key="confirm_full_reset").set_value(True)
    at.button(key="reset_all_data_btn").click().run()
    assert not at.exception
    assert at.session_state["theme"] == "light"

    with connect(db_path) as conn:
        assert fetch_contracts(conn) == ()
        assert fetch_documents(conn) == ()
        assert fetch_rules(conn) == ()
        assert fetch_invoices(conn) == ()
        assert fetch_audits(conn) == ()
        assert fetch_reviews(conn) == ()
