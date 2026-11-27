"""Etapa 7.1 — Cadastro de contratos e envio direto de arquivos do computador."""

import sqlite3
from datetime import date
from decimal import InvalidOperation
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from backend.api import (
    UploadClassificationMode,
    delete_all_uploaded_documents,
    delete_empty_contract,
    delete_uploaded_document,
    fetch_contract,
    fetch_contracts,
    fetch_documents,
    fetch_invoices,
    fetch_rules,
    ingest_uploaded_files,
    register_contract,
)
from backend.schemas import (
    Contract,
    ContractStatus,
    ReadQuality,
    ValidityPeriod,
)
from frontend.state import VIEW_BILLING, VIEW_DASHBOARD, VIEW_RULES, build_gemini_client
from frontend.ui.components import (
    BadgeTone,
    KpiItem,
    render_badge,
    render_banner,
    render_empty_state,
    render_html_table,
    render_kpi_row,
    render_page_header,
)
from frontend.ui.formatting import (
    CONTRACT_STATUS_LABELS,
    DOCUMENT_TYPE_LABELS,
    READ_QUALITY_LABELS,
    format_brl,
    format_datetime_br,
    format_validity_br,
    parse_decimal_input,
)

_UPLOAD_MODE_LABELS: dict[UploadClassificationMode, str] = {
    "AUTO": "Detectar automaticamente (contrato, aditivo, cobrança ou dossiê)",
    "CONTRACT": "Contrato-base (.pdf, .docx)",
    "AMENDMENT": "Termo aditivo (.pdf, .docx)",
    "BILLING": "Cobrança / Nota Fiscal / Planilha (.xml, .csv, .xlsx, .pdf, .docx)",
    "DOSSIER": "Dossiê completo — contrato + cobrança no mesmo arquivo (.pdf, .docx)",
}


def _format_validation_error(exc: ValidationError) -> str:
    details = "; ".join(err.get("msg", "dado inválido") for err in exc.errors())
    return f"Não foi possível salvar o contrato: {details}."


def _render_last_batch_alerts(batch_info: dict[str, object]) -> None:
    auto_ids = batch_info.get("auto_contracts")
    if isinstance(auto_ids, list) and auto_ids:
        joined = ", ".join(str(x) for x in auto_ids)
        render_banner(
            "info",
            "Contrato cadastrado automaticamente",
            f"Identificado(s) e salvo(s) a partir dos arquivos: {joined}.",
        )

    llm_warn = batch_info.get("llm_warn")
    if isinstance(llm_warn, str) and llm_warn:
        render_banner("ai", "IA não configurada para extração", llm_warn)

    raw_audits = batch_info.get("audits_count", 0)
    audits_count = raw_audits if isinstance(raw_audits, int) else 0
    raw_divs = batch_info.get("divergences_count", 0)
    divs_count = raw_divs if isinstance(raw_divs, int) else 0
    if audits_count > 0:
        if divs_count > 0:
            render_banner(
                "bad",
                "Divergências ou pendências detectadas na auditoria",
                (
                    f"{divs_count} de {audits_count} cobrança(s) auditada(s) apresentou(aram) "
                    "divergência ou alerta. Confira Cobranças e Auditoria ou Painel e Relatório."
                ),
            )
        else:
            render_banner(
                "ok",
                "Auditoria automática concluída",
                f"{audits_count} cobrança(s) auditada(s) sem divergências críticas.",
            )

    outcomes = batch_info.get("outcomes")
    if not isinstance(outcomes, list):
        return
    for item in outcomes:
        if not isinstance(item, dict):
            continue
        file_name = str(item.get("file_name", ""))
        kind = str(item.get("detected_kind", ""))
        quality = str(item.get("read_quality", ""))
        raw_rules_count = item.get("rules_count", 0)
        rules_count = raw_rules_count if isinstance(raw_rules_count, int) else 0
        raw_inv_count = item.get("invoices_count", 0)
        inv_count = raw_inv_count if isinstance(raw_inv_count, int) else 0
        if quality == ReadQuality.NO_TEXT.value:
            render_banner(
                "bad",
                "PDF sem camada de texto",
                (
                    f"O arquivo {file_name} foi marcado como NO_TEXT e não passou pela IA; "
                    "solicite um PDF pesquisável ou realize OCR antes."
                ),
            )
        elif (
            kind in {"CONTRACT", "AMENDMENT", "DOSSIER"}
            and quality == ReadQuality.OK.value
            and rules_count == 0
            and inv_count == 0
        ):
            render_banner(
                "ai",
                "Nenhuma regra extraída",
                (
                    f"Nenhuma regra estruturada foi obtida em {file_name}; "
                    "o sistema não presume que nada mudou."
                ),
            )
        warnings = item.get("warnings")
        if isinstance(warnings, list):
            for warn_text in warnings:
                if quality == ReadQuality.NO_TEXT.value and "NO_TEXT" in str(warn_text):
                    continue
                tone = "bad" if "Falha" in str(warn_text) else "ai"
                render_banner(tone, file_name, str(warn_text))


def _render_empty_contract_cleanup(conn: sqlite3.Connection, contract: Contract | None) -> None:
    if contract is None:
        return
    if (
        fetch_documents(conn, contract.id)
        or fetch_rules(conn, contract.id)
        or fetch_invoices(conn, contract.id)
    ):
        return
    st.caption(f"O contrato **{contract.id}** está vazio (sem documentos, regras nem cobranças).")
    if st.button(
        "Remover contrato vazio selecionado",
        type="secondary",
        key="delete_empty_contract_btn",
    ):
        if delete_empty_contract(conn, contract.id):
            st.session_state["selected_contract_id"] = None
            st.session_state["contracts_flash"] = (
                "ok",
                "Contrato removido",
                f"Contrato vazio {contract.id} removido com sucesso.",
            )
            st.rerun()


def render_contracts_view(conn: sqlite3.Connection) -> None:
    """Renderiza entrada unificada de documentos, cadastro opcional e tabela de documentos."""
    render_page_header(
        "Contratos e Documentos",
        (
            "Envie um arquivo completo (contrato + aditivo + faturas no mesmo PDF/DOCX) ou "
            "múltiplos arquivos separados para extrair regras, cobranças e auditar "
            "automaticamente."
        ),
        eyebrow="Etapa 1 • Entrada Unificada",
    )

    flash = st.session_state.pop("contracts_flash", None)
    if isinstance(flash, tuple) and len(flash) == 3:
        render_banner(flash[0], flash[1], flash[2])

    contracts = fetch_contracts(conn)
    all_docs = fetch_documents(conn)
    selected_id: str | None = st.session_state.get("selected_contract_id")
    active_contract = fetch_contract(conn, selected_id) if selected_id else None

    if contracts or all_docs:
        selected_docs = fetch_documents(conn, active_contract.id) if active_contract else all_docs
        selected_rules = fetch_rules(conn, active_contract.id) if active_contract else ()
        pending_count = sum(1 for r in selected_rules if not r.review_status.is_trusted)
        render_kpi_row(
            [
                KpiItem("Contratos cadastrados", str(len(contracts))),
                KpiItem(
                    "Contrato selecionado",
                    active_contract.id if active_contract else "Nenhum",
                ),
                KpiItem("Documentos recebidos", str(len(selected_docs))),
                KpiItem("Regras pendentes", str(pending_count)),
            ]
        )

    st.subheader("Enviar documentos para auditoria")
    st.caption(
        "Envie tudo em um único arquivo (contrato + aditivo + faturas no mesmo PDF/DOCX) ou "
        "vários arquivos separados (.pdf, .docx, .xml, .csv, .xlsx) — o sistema identifica regras "
        "e cobranças e já calcula a auditoria automaticamente."
    )
    if active_contract is not None:
        validity_lbl = format_validity_br(
            active_contract.validity.start, active_contract.validity.end
        )
        st.caption(
            f"Contrato selecionado no momento: **{active_contract.id}** — "
            f"{active_contract.supplier} ({validity_lbl}). Se o arquivo indicar outro contrato, um "
            "novo contrato será criado automaticamente."
        )

    uploaded_files = st.file_uploader(
        "Selecionar um ou mais arquivos (.pdf, .docx, .xml, .csv, .xlsx)",
        type=["pdf", "docx", "xml", "csv", "xlsx"],
        accept_multiple_files=True,
        key="upload_pc_files",
    )
    with st.expander("Opções avançadas de classificação", expanded=False):
        upload_mode = st.selectbox(
            "Como classificar os arquivos",
            options=list(_UPLOAD_MODE_LABELS.keys()),
            format_func=lambda item: _UPLOAD_MODE_LABELS[item],
            key="upload_doc_mode",
        )

    if st.button("Importar e processar arquivos", type="primary", key="process_pc_files_btn"):
        if not uploaded_files:
            render_banner(
                "bad",
                "Arquivo obrigatório",
                "Selecione ao menos um arquivo (.pdf, .docx, .xml, .csv ou .xlsx) antes de "
                "importar.",
            )
        else:
            file_payloads = [(item.name, item.getvalue()) for item in uploaded_files]
            needs_ai = any(
                Path(name).suffix.casefold() in {".pdf", ".docx"} for name, _ in file_payloads
            )
            client = None
            llm_warn: str | None = None
            if needs_ai:
                client, llm_warn = build_gemini_client()

            with st.status("Processando documentos…", expanded=True) as status:
                st.write(
                    f"Lendo {len(file_payloads)} arquivo(s), gravando SHA-256 e analisando "
                    "regras/cobranças…"
                )
                batch = ingest_uploaded_files(
                    conn,
                    file_payloads,
                    mode=upload_mode,
                    selected_contract_id=active_contract.id if active_contract else None,
                    client=client,
                )
                status.update(
                    label="Processamento concluído!",
                    state="complete",
                    expanded=False,
                )

            if batch.active_contract_id is not None:
                st.session_state["selected_contract_id"] = batch.active_contract_id

            total_rules = sum(item.rules_count for item in batch.outcomes)
            total_invoices = sum(item.invoices_count for item in batch.outcomes)
            st.session_state["last_upload_batch"] = {
                "auto_contracts": [c.id for c in batch.auto_registered_contracts],
                "llm_warn": llm_warn or "",
                "audits_count": batch.audits_count,
                "divergences_count": batch.divergences_count,
                "outcomes": [
                    {
                        "file_name": item.file_name,
                        "detected_kind": item.detected_kind,
                        "read_quality": (item.document.read_quality.value if item.document else ""),
                        "rules_count": item.rules_count,
                        "invoices_count": item.invoices_count,
                        "warnings": list(item.warnings),
                    }
                    for item in batch.outcomes
                ],
            }
            audit_summary = (
                f" e {batch.audits_count} auditoria(s) executada(s) "
                f"({batch.divergences_count} com divergência/alerta)"
                if batch.audits_count > 0
                else ""
            )
            flash_tone: BadgeTone = (
                "bad" if batch.divergences_count > 0 else ("ai" if batch.warnings else "ok")
            )
            st.session_state["contracts_flash"] = (
                flash_tone,
                "Importação concluída",
                (
                    f"{len(batch.outcomes)} arquivo(s) processado(s): "
                    f"{total_rules} regra(s) extraída(s), {total_invoices} cobrança(s) "
                    f"importada(s){audit_summary}."
                ),
            )
            st.rerun()

    last_batch = st.session_state.get("last_upload_batch")
    if isinstance(last_batch, dict):
        _render_last_batch_alerts(last_batch)
        with st.container(horizontal=True):
            if st.button("Revisar regras extraídas", type="secondary", key="shortcut_rules_btn"):
                st.session_state["active_view"] = VIEW_RULES
                st.rerun()
            if st.button(
                "Ver cobranças e memória de cálculo",
                type="secondary",
                key="shortcut_billing_btn",
            ):
                st.session_state["active_view"] = VIEW_BILLING
                st.rerun()
            if st.button(
                "Abrir painel e gráficos",
                type="secondary",
                key="shortcut_dashboard_btn",
            ):
                st.session_state["active_view"] = VIEW_DASHBOARD
                st.rerun()

    with st.expander("Cadastrar contrato manualmente (opcional)", expanded=False):
        with st.form("contract_register_form"):
            contract_id = st.text_input(
                "Número / identificador do contrato",
                placeholder="Ex.: CT-2026-001",
                key="contract_form_id",
            )
            company = st.text_input(
                "Empresa contratante",
                placeholder="Ex.: Empresa Exemplo S.A.",
                key="contract_form_company",
            )
            supplier = st.text_input(
                "Fornecedor",
                placeholder="Ex.: Fornecedor Exemplo Ltda.",
                key="contract_form_supplier",
            )
            supplier_cnpj = st.text_input(
                "CNPJ do fornecedor",
                placeholder="00.000.000/0001-00",
                key="contract_form_cnpj",
            )
            subject = st.text_input(
                "Objeto resumido",
                placeholder="Ex.: Serviços de suporte técnico contínuo",
                key="contract_form_subject",
            )
            c_start, c_end = st.columns(2)
            with c_start:
                validity_start = st.date_input(
                    "Início da vigência",
                    value=date(2026, 1, 1),
                    key="contract_form_start",
                )
            with c_end:
                validity_end = st.date_input(
                    "Fim da vigência",
                    value=date(2026, 12, 31),
                    key="contract_form_end",
                )
            open_ended = st.checkbox(
                "Contrato sem data de término definida",
                value=False,
                key="contract_form_open_ended",
            )
            c_tol, c_status = st.columns(2)
            with c_tol:
                tolerance_raw = st.text_input(
                    "Tolerância de auditoria (R$)",
                    value="0,00",
                    key="contract_form_tolerance",
                )
            with c_status:
                status_choice = st.selectbox(
                    "Status inicial",
                    options=list(ContractStatus),
                    format_func=lambda item: CONTRACT_STATUS_LABELS[item],
                    key="contract_form_status",
                )

            submitted = st.form_submit_button("Cadastrar contrato", type="primary")
            if submitted:
                try:
                    tolerance = parse_decimal_input(tolerance_raw)
                    new_contract = Contract(
                        id=contract_id.strip(),
                        company=company.strip(),
                        supplier=supplier.strip(),
                        supplier_cnpj=supplier_cnpj.strip(),
                        subject=subject.strip(),
                        validity=ValidityPeriod(
                            start=validity_start,
                            end=None if open_ended else validity_end,
                        ),
                        comparison_tolerance=tolerance,
                        status=status_choice,
                    )
                except InvalidOperation:
                    render_banner(
                        "bad",
                        "Tolerância inválida",
                        "Informe um valor numérico em reais, como 0,00 ou 5,00.",
                    )
                except ValidationError as exc:
                    render_banner("bad", "Dados inválidos", _format_validation_error(exc))
                else:
                    register_contract(conn, new_contract)
                    st.session_state["selected_contract_id"] = new_contract.id
                    st.session_state["contracts_flash"] = (
                        "ok",
                        "Contrato cadastrado",
                        (
                            f"{new_contract.id} ({new_contract.supplier}) salvo com tolerância de "
                            f"{format_brl(new_contract.comparison_tolerance)}."
                        ),
                    )
                    st.rerun()

    st.divider()
    st.subheader("Documentos recebidos")
    scope_options = ["Todos os documentos recebidos", "Apenas do contrato ativo"]
    scope_choice = st.radio(
        "Escopo da listagem",
        options=scope_options,
        horizontal=True,
        key="docs_scope_radio",
    )
    visible_docs = (
        all_docs
        if scope_choice == "Todos os documentos recebidos"
        else (fetch_documents(conn, active_contract.id) if active_contract else ())
    )

    if not visible_docs:
        render_empty_state(
            "Nenhum documento recebido ainda",
            "Envie arquivos PDF, DOCX, XML, CSV ou XLSX pelo painel acima para iniciar.",
            next_action="Selecione os arquivos do computador e clique em Importar e processar.",
        )
        _render_empty_contract_cleanup(conn, active_contract)
        return

    table_rows: list[list[str]] = []
    for doc in visible_docs:
        rev_badge = (
            render_badge("ok", "✓", "Revisado")
            if doc.reviewed
            else render_badge("ai", "✦", "Pendente")
        )
        quality_badge = (
            render_badge("ok", "✓", READ_QUALITY_LABELS[doc.read_quality])
            if doc.read_quality is ReadQuality.OK
            else render_badge("bad", "▲", READ_QUALITY_LABELS[doc.read_quality])
        )
        table_rows.append(
            [
                doc.file_name,
                doc.contract_id or "Sem vínculo",
                DOCUMENT_TYPE_LABELS[doc.document_type],
                quality_badge,
                f"{doc.file_hash[:12]}…",
                format_datetime_br(doc.received_at),
                rev_badge,
            ]
        )
    render_html_table(
        ["Arquivo", "Contrato", "Tipo", "Leitura", "SHA-256", "Recebido em", "Revisão"],
        table_rows,
        html_cols={3, 6},
    )

    st.markdown("#### Apagar documentos enviados")
    doc_by_id = {doc.id: doc for doc in visible_docs}
    col_del_one, col_del_all = st.columns([1.2, 1.0], gap="large")
    with col_del_one:
        target_doc_id = st.selectbox(
            "Selecionar documento para apagar",
            options=list(doc_by_id.keys()),
            format_func=lambda did: (
                f"{doc_by_id[did].file_name} ({DOCUMENT_TYPE_LABELS[doc_by_id[did].document_type]})"
            ),
            key="delete_single_doc_select",
        )
        if st.button(
            "Apagar documento selecionado",
            type="secondary",
            key="delete_single_doc_btn",
        ):
            deleted = delete_uploaded_document(conn, target_doc_id)
            st.session_state.pop("last_upload_batch", None)
            st.session_state["contracts_flash"] = (
                "ok",
                "Documento apagado",
                (
                    f"'{deleted.file_name}' removido com "
                    f"{deleted.deleted_rules} regra(s), "
                    f"{deleted.deleted_amendments} aditivo(s) e "
                    f"{deleted.deleted_invoices} cobrança(s) vinculada(s)."
                ),
            )
            st.rerun()

    with col_del_all:
        if st.session_state.pop("_reset_confirm_delete_docs", False):
            st.session_state["confirm_delete_listed_docs"] = False
        confirm_batch = st.checkbox(
            "Confirmar exclusão de todos os documentos listados",
            key="confirm_delete_listed_docs",
        )
        if st.button(
            "Apagar todos os listados",
            type="secondary",
            key="delete_all_docs_btn",
        ):
            if not confirm_batch:
                render_banner(
                    "bad",
                    "Confirmação necessária",
                    "Marque a caixa de confirmação antes de apagar todos os documentos listados.",
                )
            else:
                scope_contract_id = (
                    active_contract.id
                    if scope_choice == "Apenas do contrato ativo" and active_contract is not None
                    else None
                )
                deleted_list = delete_all_uploaded_documents(conn, contract_id=scope_contract_id)
                st.session_state.pop("last_upload_batch", None)
                st.session_state["_reset_confirm_delete_docs"] = True
                st.session_state["contracts_flash"] = (
                    "ok",
                    "Documentos apagados",
                    f"{len(deleted_list)} documento(s) enviado(s) removido(s) com segurança.",
                )
                st.rerun()

    _render_empty_contract_cleanup(conn, active_contract)
