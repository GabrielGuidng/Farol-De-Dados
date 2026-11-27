"""Etapa 7.4 — Cobranças e Auditoria: filtros em linha, cartões em português e decisões rápidas."""

import sqlite3
from contextlib import closing
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import streamlit as st

from backend.api import (
    assign_invoice_contract,
    audit_stored_invoice,
    delete_stored_invoice,
    fetch_audits,
    fetch_contract,
    fetch_contracts,
    fetch_documents,
    fetch_invoices,
    fetch_reviews,
    ingest_uploaded_files,
    record_audit_decision,
)
from backend.persistencia import StoredAudit
from backend.schemas import (
    AuditClassification,
    CheckResult,
    Contract,
    Document,
    Invoice,
    ReviewDecision,
    ReviewStatus,
    ReviewTargetType,
)
from frontend.state import build_gemini_client, open_connection
from frontend.ui.components import (
    KpiItem,
    describe_rule_value,
    escape_text,
    highlight_excerpt_in_text,
    render_badge,
    render_banner,
    render_empty_state,
    render_kpi_row,
    render_page_header,
)
from frontend.ui.executive_report import (
    build_invoices_csv_bytes,
    build_single_invoice_printable_html,
)
from frontend.ui.formatting import (
    AUDIT_CLASSIFICATION_BADGES,
    AUDIT_DECISION_LABELS,
    AUDIT_REVIEW_STATUS_BADGES,
    INVOICE_SOURCE_LABELS,
    RULE_TYPE_LABELS,
    clean_technical_ids_in_message,
    format_brl,
    format_competence_pt,
    format_competence_short,
    format_datetime_br,
    format_quantity,
    format_signed_brl,
    humanize_check_name,
    natural_invoice_sort_key,
)

_ZERO = Decimal("0.00")
_CHECK_SEVERITY_ORDER: dict[CheckResult, int] = {
    CheckResult.PENDING: 0,
    CheckResult.DIVERGENCE: 1,
    CheckResult.REVIEW: 2,
    CheckResult.OK: 3,
}
_SCOPE_OPTIONS: tuple[str, ...] = (
    "Do contrato selecionado (+ sem vínculo)",
    "Todas as cobranças",
    "Somente sem vínculo",
)
_CLS_OPTIONS: tuple[str, ...] = (
    "Todas",
    "Exigem atenção",
    "Com divergência",
    "Em revisão humana",
    "Falta documentação",
    "Sem divergência",
)
_AUDIT_DIALOG_DECISION_LABELS: dict[ReviewDecision, str] = {
    ReviewDecision.CORRECT: "Com ressalva (glosa/ajuste)",
    ReviewDecision.CONFIRM: "Confirmada (válida)",
    ReviewDecision.REJECT: "Rejeitada (bloqueada)",
}


def _clear_editing_audit() -> None:
    st.session_state.pop("editing_audit_id", None)


@st.dialog(
    "Registrar ressalva ou revisão da cobrança",
    width="medium",
    on_dismiss=_clear_editing_audit,
)
def _open_audit_decision_dialog(
    invoice: Invoice,
    stored: StoredAudit,
    *,
    rev_status: ReviewStatus,
    reviewer: str,
) -> None:
    res = stored.result
    st.caption(
        f"Cobrança nº **{invoice.number}** ({format_competence_short(invoice.reference_period)}) "
        f"• {invoice.supplier} • Total {format_brl(res.total)} • "
        f"Diferença {format_signed_brl(res.difference)}"
    )
    with st.form(key=f"audit_decision_form_{res.id}", border=False):
        chosen_decision = st.selectbox(
            "Estado após revisão",
            options=[ReviewDecision.CORRECT, ReviewDecision.CONFIRM, ReviewDecision.REJECT],
            format_func=lambda item: _AUDIT_DIALOG_DECISION_LABELS[item],
            key=f"audit_dec_select_{res.id}",
        )
        just_corr = st.text_area(
            "Justificativa da decisão (obrigatória)",
            placeholder=(
                "Descreva o motivo da ressalva ou correção conforme o contrato e a nota fiscal"
            ),
            key=f"audit_dec_just_{res.id}",
            height=96,
        )
        _sp, c_cancel, c_save = st.columns([2.2, 1.0, 1.35], gap="small")
        cancel_clicked = c_cancel.form_submit_button("Cancelar", type="secondary", width="stretch")
        save_clicked = c_save.form_submit_button("Salvar ressalva", type="primary", width="stretch")
        if cancel_clicked:
            st.session_state.pop("editing_audit_id", None)
            st.rerun()
        if save_clicked:
            clean_just = just_corr.strip()
            if not clean_just:
                render_banner(
                    "bad",
                    "Justificativa obrigatória",
                    "Descreva o motivo da ressalva ou revisão na trilha de auditoria.",
                )
            else:
                with closing(open_connection()) as dlg_conn:
                    record_audit_decision(
                        dlg_conn,
                        res.id,
                        user=reviewer,
                        decision=chosen_decision,
                        justification=clean_just,
                        previous_status=rev_status,
                    )
                st.session_state.pop("editing_audit_id", None)
                st.session_state["billing_flash"] = (
                    "ok",
                    "Decisão registrada",
                    (
                        f"Nota {invoice.number} atualizada como "
                        f"{AUDIT_DECISION_LABELS[chosen_decision]}."
                    ),
                )
                st.rerun()


def _latest_audit(conn: sqlite3.Connection, invoice_id: str) -> StoredAudit | None:
    audits = fetch_audits(conn, invoice_id)
    return audits[-1] if audits else None


def _latest_audit_status(conn: sqlite3.Connection, audit_id: str) -> ReviewStatus:
    reviews = fetch_reviews(
        conn,
        target_type=ReviewTargetType.AUDIT_RESULT,
        target_id=audit_id,
    )
    if not reviews:
        return ReviewStatus.PENDING_REVIEW
    return reviews[-1].new_status


def _run_audits_for_invoices(
    conn: sqlite3.Connection,
    invoices: list[Invoice],
    *,
    use_llm: bool,
    approve_links: bool,
) -> tuple[int, list[str]]:
    tradutor_client = None
    report_client = None
    warnings: list[str] = []

    if use_llm:
        client, llm_warn = build_gemini_client()
        if client is None:
            warnings.append(
                llm_warn or "IA indisponível; auditoria executada somente com motor determinístico."
            )
        else:
            tradutor_client = client
            report_client = client

    reports_meta: dict[str, dict[str, object]] = dict(
        st.session_state.get("audit_reports_meta", {})
    )
    audited_count = 0
    for inv in invoices:
        try:
            outcome = audit_stored_invoice(
                conn,
                inv.id,
                tradutor_client=tradutor_client,
                report_client=report_client,
                links_approved_by_human=approve_links,
            )
        except (ValueError, RuntimeError) as exc:
            warnings.append(f"Nota {inv.number}: {exc}")
            continue

        audited_count += 1
        audit_id = outcome.stored_audit.result.id
        reports_meta[audit_id] = {
            "final_text": outcome.report.final_text,
            "rejected_llm_text": outcome.report.rejected_llm_text,
            "used_llm_text": outcome.report.used_llm_text,
            "awaiting_link_review": outcome.awaiting_link_review,
            "warnings": list(outcome.warnings),
        }
        warnings.extend(outcome.warnings)

    st.session_state["audit_reports_meta"] = reports_meta
    return audited_count, warnings


def _render_unlinked_section(
    conn: sqlite3.Connection,
    unlinked: list[Invoice],
    contracts: tuple[Contract, ...],
) -> None:
    if not unlinked:
        return

    st.subheader("Vincular cobranças pendentes ou com CNPJ ambíguo")
    if not contracts:
        render_empty_state(
            "Sem contratos cadastrados para vincular",
            "Há cobrança(s) sem vínculo, mas ainda não existe contrato cadastrado no banco.",
            next_action="Cadastre o contrato em Contratos e Documentos para habilitar o vínculo.",
        )
        return

    contract_by_id = {c.id: c for c in contracts}
    for inv in unlinked:
        matching_ids = [c.id for c in contracts if c.supplier_cnpj == inv.supplier_cnpj]
        hint = (
            f"CNPJ ambíguo ({len(matching_ids)} contratos encontrados)"
            if len(matching_ids) > 1
            else "Sem contrato cadastrado para este CNPJ/número"
        )
        render_banner(
            "ai",
            f"Nota {inv.number} ({inv.supplier})",
            f"{hint} — competência {format_competence_pt(inv.reference_period)}, "
            f"total {format_brl(inv.total)}.",
        )
        ordered_options = matching_ids + [c.id for c in contracts if c.id not in matching_ids]

        def _format_option(
            cid: str,
            _map: dict[str, Contract] = contract_by_id,
            _inv_cnpj: str = inv.supplier_cnpj,
        ) -> str:
            c = _map[cid]
            cnpj_tag = " • ✓ Mesmo CNPJ" if c.supplier_cnpj == _inv_cnpj else ""
            return f"{c.id} — {c.supplier} ({c.supplier_cnpj}){cnpj_tag}"

        with st.form(key=f"assign_contract_form_{inv.id}"):
            chosen_contract_id = st.selectbox(
                f"Contrato correto para Nota {inv.number}",
                options=ordered_options,
                format_func=_format_option,
                key=f"assign_select_{inv.id}",
            )
            if st.form_submit_button("Vincular contrato", type="secondary"):
                linked_inv = assign_invoice_contract(conn, inv.id, chosen_contract_id)
                _run_audits_for_invoices(
                    conn,
                    [linked_inv],
                    use_llm=False,
                    approve_links=False,
                )
                st.session_state["selected_contract_id"] = chosen_contract_id
                st.session_state["billing_flash"] = (
                    "ok",
                    "Vínculo atualizado",
                    (
                        f"Nota {inv.number} vinculada ao contrato {chosen_contract_id} "
                        "e auditoria recalculada."
                    ),
                )
                st.rerun()


def _inline_excerpt_details(
    *,
    full_text: str | None,
    excerpt: str,
) -> str:
    if not full_text:
        return ""
    highlighted_html, matched = highlight_excerpt_in_text(full_text, excerpt)
    if not highlighted_html:
        return ""
    hint = (
        "Trecho destacado no texto integral do documento:"
        if matched
        else "Texto integral do documento (correspondência exata não destacada):"
    )
    return (
        '<details class="original-doc-details compact-inline-details">'
        "<summary>[Ver trecho no documento]</summary>"
        f'<div class="original-doc-hint">{escape_text(hint)}</div>'
        f'<div class="original-doc-viewer">{highlighted_html}</div>'
        "</details>"
    )


def _format_diff_with_pct(base: Decimal | None, difference: Decimal | None) -> tuple[str, str]:
    if difference is None:
        return "—", ""
    diff_txt = format_signed_brl(difference)
    if base is not None and base > _ZERO:
        pct = (difference / base * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        pct_txt = f"{pct:+.2f}%".replace(".", ",")
        diff_txt = f"{diff_txt} ({pct_txt})"
    tone_cls = "ok" if difference == _ZERO else ("bad" if difference > _ZERO else "ai")
    return diff_txt, tone_cls


def _render_audit_card(
    conn: sqlite3.Connection,
    *,
    invoice: Invoice,
    stored: StoredAudit | None,
    doc_by_id: dict[str, Document],
    doc_names: dict[str, str],
    reviewer: str,
) -> None:
    comp_short = format_competence_short(invoice.reference_period)
    contract_lbl = f"Contrato {invoice.contract_id}" if invoice.contract_id else "Sem vínculo"
    source_lbl = INVOICE_SOURCE_LABELS[invoice.source]

    if stored is None:
        badge = render_badge("info", "●", "Aguardando auditoria")
        st.html(
            '<div class="audit-card">'
            '<div class="audit-card-header">'
            "<div>"
            f'<div class="audit-card-title">Cobrança nº {escape_text(invoice.number)} • '
            f"{escape_text(comp_short)} • {escape_text(format_brl(invoice.total))}</div>"
            f'<div class="audit-card-sub">{escape_text(invoice.supplier)} • '
            f"{escape_text(contract_lbl)} • {escape_text(source_lbl)}</div>"
            "</div>"
            f"<div>{badge}</div>"
            "</div>"
            "</div>"
        )
        c_aud, c_del = st.columns([1.4, 0.8], gap="small")
        with c_aud:
            if st.button(
                "Auditar esta cobrança",
                key=f"audit_single_{invoice.id}",
                type="primary",
                width="stretch",
            ):
                _run_audits_for_invoices(
                    conn,
                    [invoice],
                    use_llm=bool(st.session_state.get("billing_use_llm", False)),
                    approve_links=bool(st.session_state.get("billing_approve_links", False)),
                )
                st.rerun()
        with c_del:
            with st.popover("Remover", width="stretch"):
                st.caption(f"Remover Nota {invoice.number}?")
                if st.button(
                    "Confirmar exclusão",
                    key=f"delete_invoice_btn_{invoice.id}",
                    type="secondary",
                    width="stretch",
                ):
                    delete_stored_invoice(conn, invoice.id)
                    st.session_state["billing_flash"] = (
                        "ok",
                        "Cobrança removida",
                        f"Nota {invoice.number} removida do sistema.",
                    )
                    st.rerun()
        return

    result = stored.result
    cls_tone, cls_icon, cls_label = AUDIT_CLASSIFICATION_BADGES[result.classification]
    cls_badge = render_badge(cls_tone, cls_icon, cls_label)
    rev_status = _latest_audit_status(conn, result.id)
    rev_tone, rev_icon, rev_label = AUDIT_REVIEW_STATUS_BADGES[rev_status]
    rev_badge = (
        ""
        if rev_status is ReviewStatus.PENDING_REVIEW
        else " " + render_badge(rev_tone, rev_icon, rev_label)
    )

    ref_txt = format_brl(result.base)
    charged_base = (
        sum(
            (it.total_value for it in invoice.items if "exced" not in it.description.casefold()),
            _ZERO,
        )
        if invoice.items
        else invoice.total
    )
    base_charged_txt = format_brl(charged_base)
    total_txt = format_brl(result.total)
    diff_txt, diff_tone = _format_diff_with_pct(result.base, result.difference)

    items_summary = (
        " • ".join(
            f"{it.description} ({format_quantity(it.quantity, it.unit)} × "
            f"{format_brl(it.unit_value)} = {format_brl(it.total_value)})"
            for it in invoice.items
        )
        if invoice.items
        else "Sem itens discriminados."
    )

    checks_html_parts: list[str] = []
    ordered_checks = sorted(result.checks, key=lambda c: _CHECK_SEVERITY_ORDER[c.result])
    for chk in ordered_checks:
        mem_html = ""
        if chk.calculation_memory:
            mem_lines = [
                clean_technical_ids_in_message(line, doc_names) for line in chk.calculation_memory
            ]
            mem_html = f'<div class="calc-memory">{escape_text(chr(10).join(mem_lines))}</div>'
        msg_clean = clean_technical_ids_in_message(chk.message, doc_names)
        chk_badge = (
            ""
            if chk.result is CheckResult.OK
            else " "
            + render_badge(
                "bad" if chk.result in (CheckResult.DIVERGENCE, CheckResult.PENDING) else "ai",
                "▲" if chk.result in (CheckResult.DIVERGENCE, CheckResult.PENDING) else "✦",
                "Divergência"
                if chk.result is CheckResult.DIVERGENCE
                else ("Pendência" if chk.result is CheckResult.PENDING else "Revisão"),
            )
        )
        checks_html_parts.append(
            '<div class="audit-check-row">'
            f'<div class="audit-check-name">{escape_text(humanize_check_name(chk.name))}'
            f"{chk_badge}</div>"
            f'<div class="audit-check-body">{escape_text(msg_clean)}{mem_html}</div>'
            "</div>"
        )

    evidence_items_html: list[str] = []
    for rule in stored.rules_snapshot:
        src_doc = doc_by_id.get(rule.evidence.source_document_id)
        src_name = src_doc.file_name if src_doc else "Documento contratual"
        page_txt = f"pág. {rule.evidence.page}" if rule.evidence.page is not None else "sem página"
        loc_txt = f"{rule.evidence.clause or 'Cláusula'} • {page_txt}"
        doc_details = _inline_excerpt_details(
            full_text=src_doc.extracted_text if src_doc else None,
            excerpt=rule.evidence.literal_excerpt,
        )
        evidence_items_html.append(
            '<div style="margin-top:4px;">'
            f"• <strong>{escape_text(RULE_TYPE_LABELS[rule.rule_type])}:</strong> "
            f"{escape_text(describe_rule_value(rule))} — {escape_text(src_name)} — "
            f"{escape_text(loc_txt)} "
            f'("<em>{escape_text(rule.evidence.literal_excerpt)}</em>")'
            f"{doc_details}"
            "</div>"
        )

    evidence_panel_html = (
        '<div class="audit-evidence-panel">'
        '<div class="audit-evidence-title">Evidências e regras aplicadas:</div>'
        + (
            "".join(evidence_items_html)
            if evidence_items_html
            else "<div>Nenhuma regra aplicada.</div>"
        )
        + "</div>"
    )

    st.html(
        '<div class="audit-card">'
        '<div class="audit-card-header">'
        "<div>"
        f'<div class="audit-card-title">Cobrança nº {escape_text(invoice.number)} • '
        f"{escape_text(comp_short)} • {escape_text(total_txt)}</div>"
        f'<div class="audit-card-sub">{escape_text(invoice.supplier)} • '
        f"{escape_text(contract_lbl)} • Origem: {escape_text(source_lbl)}</div>"
        "</div>"
        f"<div>{cls_badge}{rev_badge}</div>"
        "</div>"
        '<div class="audit-metrics-row">'
        '<div class="audit-metric-cell"><div class="m-label">VALOR DE REFERÊNCIA</div>'
        f'<div class="m-val">{escape_text(ref_txt)}</div></div>'
        '<div class="audit-metric-cell"><div class="m-label">VALOR BASE COBRADO</div>'
        f'<div class="m-val">{escape_text(base_charged_txt)}</div></div>'
        '<div class="audit-metric-cell"><div class="m-label">TOTAL DA COBRANÇA</div>'
        f'<div class="m-val">{escape_text(total_txt)}</div></div>'
        '<div class="audit-metric-cell"><div class="m-label">DIFERENÇA (BASE − REF.)</div>'
        f'<div class="m-val {diff_tone}">{escape_text(diff_txt)}</div></div>'
        "</div>"
        f'<div class="audit-items-line"><strong>Itens:</strong> {escape_text(items_summary)}</div>'
        f"{''.join(checks_html_parts)}"
        f"{evidence_panel_html}"
        '<div class="audit-governance-note">'
        "<strong>Nota de governança:</strong> o resultado apresenta evidências para decisão "
        "humana; não declara fraude e não bloqueia pagamentos automaticamente."
        "</div>"
        "</div>"
    )

    reports_meta: dict[str, dict[str, object]] = st.session_state.get("audit_reports_meta", {})
    meta = reports_meta.get(result.id, {})
    if meta.get("awaiting_link_review"):
        render_banner(
            "ai",
            "Vínculo semântico aguardando aprovação",
            "Confira os itens e marque a aprovação de vínculos se concordar.",
        )

    audit_reviews = fetch_reviews(
        conn,
        target_type=ReviewTargetType.AUDIT_RESULT,
        target_id=result.id,
    )
    decision_caption = (
        f"Última decisão ({format_datetime_br(audit_reviews[-1].reviewed_at)} por "
        f"{audit_reviews[-1].user}): {AUDIT_DECISION_LABELS[audit_reviews[-1].decision]}"
        if audit_reviews
        else "Sem decisão humana registrada para esta cobrança."
    )

    c_stat, c_conf, c_ress, c_rej, c_prt, c_del = st.columns(
        [1.85, 1.05, 1.35, 1.05, 0.72, 0.72],
        gap="small",
        vertical_alignment="center",
    )
    with c_stat:
        st.caption(decision_caption)

    with c_conf:
        if st.button(
            "Confirmar análise",
            key=f"confirm_audit_btn_{result.id}",
            type="secondary",
            width="stretch",
        ):
            record_audit_decision(
                conn,
                result.id,
                user=reviewer,
                decision=ReviewDecision.CONFIRM,
                justification="Análise da auditoria confirmada pelo revisor.",
                previous_status=rev_status,
            )
            st.session_state["billing_flash"] = (
                "ok",
                "Análise confirmada",
                f"Auditoria da Nota {invoice.number} confirmada na trilha.",
            )
            st.rerun()

    with c_ress:
        if st.button(
            "Registrar ressalva",
            key=f"open_audit_corr_btn_{result.id}",
            type="secondary",
            width="stretch",
        ):
            st.session_state["editing_audit_id"] = result.id

    with c_rej:
        with st.popover("Rejeitar cobrança", width="stretch"):
            with st.form(key=f"audit_reject_form_{result.id}"):
                just_rej = st.text_input(
                    "Motivo da rejeição",
                    value="Cobrança rejeitada para pagamento após análise.",
                    key=f"audit_rej_just_{result.id}",
                )
                if st.form_submit_button("Confirmar rejeição", type="secondary", width="stretch"):
                    clean_rej = just_rej.strip()
                    if not clean_rej:
                        render_banner(
                            "bad",
                            "Justificativa obrigatória",
                            "Informe o motivo da rejeição.",
                        )
                    else:
                        record_audit_decision(
                            conn,
                            result.id,
                            user=reviewer,
                            decision=ReviewDecision.REJECT,
                            justification=clean_rej,
                            previous_status=rev_status,
                        )
                        st.session_state["billing_flash"] = (
                            "ok",
                            "Cobrança rejeitada",
                            f"Auditoria da Nota {invoice.number} rejeitada.",
                        )
                        st.rerun()

    with c_prt:
        if st.button("Imprimir", key=f"print_invoice_btn_{invoice.id}", width="stretch"):
            st.session_state["billing_print_html"] = build_single_invoice_printable_html(
                invoice,
                stored,
                doc_names=doc_names,
                decision_label=AUDIT_REVIEW_STATUS_BADGES[rev_status][2],
                auto_print=True,
            )

    with c_del:
        with st.popover("Remover", width="stretch"):
            st.caption(f"Excluir Nota {invoice.number} ({comp_short})?")
            if st.button(
                "Excluir nota",
                key=f"delete_invoice_btn_{invoice.id}",
                type="secondary",
                width="stretch",
            ):
                delete_stored_invoice(conn, invoice.id)
                st.session_state["billing_flash"] = (
                    "ok",
                    "Cobrança removida",
                    f"Nota {invoice.number} excluída mantendo o histórico de revisões.",
                )
                st.rerun()


def render_billing_audit_view(conn: sqlite3.Connection) -> None:
    """Renderiza filtros em linha, cartões detalhados de auditoria e decisões humanas (7.4)."""
    render_page_header(
        "Cobranças e Auditoria",
        (
            "Filtre cobranças, inspecione memória de cálculo e evidências em português claro "
            "e registre decisões em 1 clique."
        ),
        eyebrow="Etapa 4 • Confronto Determinístico & Decisão",
    )

    flash = st.session_state.pop("billing_flash", None)
    if isinstance(flash, tuple) and len(flash) == 3:
        render_banner(flash[0], flash[1], flash[2])

    contracts = fetch_contracts(conn)
    all_invoices = list(fetch_invoices(conn))
    selected_id: str | None = st.session_state.get("selected_contract_id")
    active_contract = fetch_contract(conn, selected_id) if selected_id else None

    unlinked_all = [inv for inv in all_invoices if inv.contract_id is None]
    audited_pairs: list[tuple[Invoice, StoredAudit | None]] = [
        (inv, _latest_audit(conn, inv.id)) for inv in all_invoices
    ]
    audited_total = sum(1 for _, aud in audited_pairs if aud is not None)
    attention_total = sum(
        1
        for _, aud in audited_pairs
        if aud is not None and aud.result.classification is not AuditClassification.NO_DIVERGENCE
    )

    render_kpi_row(
        [
            KpiItem("Cobranças registradas", str(len(all_invoices))),
            KpiItem("Sem vínculo contratual", str(len(unlinked_all))),
            KpiItem("Cobranças auditadas", str(audited_total)),
            KpiItem("Exigem atenção", str(attention_total)),
        ]
    )

    col_up, col_ai = st.columns(2, gap="medium")
    with col_up:
        with st.expander(
            "Enviar novos documentos por aqui (.pdf, .docx, .xml, .csv, .xlsx)",
            expanded=False,
        ):
            uploaded_billing = st.file_uploader(
                "Selecionar arquivos (.xml, .csv, .xlsx, .pdf, .docx)",
                type=["xml", "csv", "xlsx", "pdf", "docx"],
                accept_multiple_files=True,
                key="billing_upload_files",
            )
            if st.button("Importar cobranças", type="primary", key="billing_import_btn"):
                if not uploaded_billing:
                    render_banner(
                        "bad",
                        "Arquivo obrigatório",
                        "Selecione ao menos um arquivo antes de importar.",
                    )
                else:
                    file_payloads = [(item.name, item.getvalue()) for item in uploaded_billing]
                    has_llm_doc = any(
                        Path(name).suffix.casefold() in {".pdf", ".docx"}
                        for name, _ in file_payloads
                    )
                    client = None
                    import_warnings: list[str] = []
                    if has_llm_doc:
                        client, llm_warn = build_gemini_client()
                        if client is None and llm_warn is not None:
                            import_warnings.append(llm_warn)

                    with st.spinner("Processando documentos, gravando SHA-256 e auditando…"):
                        batch = ingest_uploaded_files(
                            conn,
                            file_payloads,
                            mode="AUTO",
                            selected_contract_id=active_contract.id if active_contract else None,
                            client=client,
                        )
                    if batch.active_contract_id is not None:
                        st.session_state["selected_contract_id"] = batch.active_contract_id
                    import_warnings.extend(batch.warnings)
                    total_imported = sum(item.invoices_count for item in batch.outcomes)
                    total_unlinked = sum(item.unlinked_invoices_count for item in batch.outcomes)
                    st.session_state["billing_import_warnings"] = import_warnings
                    st.session_state["billing_flash"] = (
                        "bad"
                        if batch.divergences_count > 0
                        else ("ai" if total_unlinked or import_warnings else "ok"),
                        "Importação e auditoria concluídas",
                        (
                            f"{total_imported} cobrança(s) importada(s), "
                            f"{batch.audits_count} auditada(s) e "
                            f"{total_unlinked} aguardando vínculo manual."
                        ),
                    )
                    st.rerun()

    with col_ai:
        with st.expander("Opções avançadas de auditoria (IA e vínculos)", expanded=False):
            use_llm = st.checkbox(
                "Usar IA (.env) para Tradutor semântico e resumo textual",
                value=False,
                key="billing_use_llm",
            )
            approve_links = st.checkbox(
                "Aprovar vínculos semânticos sugeridos pelo Tradutor",
                value=False,
                key="billing_approve_links",
            )

    for msg in st.session_state.get("billing_import_warnings", ()):
        tone = "bad" if "Falha" in str(msg) or "erro" in str(msg).casefold() else "ai"
        render_banner(tone, "Aviso de importação", str(msg))

    _render_unlinked_section(conn, unlinked_all, contracts)

    st.subheader("Cobranças auditadas")
    st.caption(
        "Classificação determinística em Centavos, ordenada por gravidade e competência, "
        "com memória de cálculo e evidência grifada."
    )

    f1, f2, f3, f4, f5 = st.columns(
        [1.65, 1.2, 1.3, 0.9, 0.85],
        gap="small",
        vertical_alignment="bottom",
    )
    with f1:
        scope_choice = st.selectbox(
            "Escopo",
            options=list(_SCOPE_OPTIONS),
            key="billing_scope_select",
        )
    with f2:
        cls_choice = st.selectbox(
            "Classificação",
            options=list(_CLS_OPTIONS),
            key="billing_cls_select",
        )
    with f3:
        search_query = st.text_input(
            "Buscar",
            placeholder="nº, competência, fornecedor",
            key="billing_search_input",
        )

    if scope_choice == _SCOPE_OPTIONS[0]:
        if active_contract is not None:
            scoped = [
                (inv, aud)
                for inv, aud in audited_pairs
                if inv.contract_id in (active_contract.id, None)
            ]
            if not scoped and audited_pairs:
                scoped = list(audited_pairs)
        else:
            scoped = list(audited_pairs)
    elif scope_choice == _SCOPE_OPTIONS[2]:
        scoped = [(inv, aud) for inv, aud in audited_pairs if inv.contract_id is None]
    else:
        scoped = list(audited_pairs)

    cls_filtered: list[tuple[Invoice, StoredAudit | None]] = []
    for inv, aud in scoped:
        if cls_choice == "Todas":
            cls_filtered.append((inv, aud))
        elif cls_choice == "Exigem atenção":
            if aud is None or aud.result.classification is not AuditClassification.NO_DIVERGENCE:
                cls_filtered.append((inv, aud))
        elif aud is not None:
            cls_label = AUDIT_CLASSIFICATION_BADGES[aud.result.classification][2]
            if cls_label == cls_choice:
                cls_filtered.append((inv, aud))

    q_norm = search_query.strip().casefold()
    if q_norm:
        filtered = [
            (inv, aud)
            for inv, aud in cls_filtered
            if q_norm in inv.number.casefold()
            or q_norm in str(inv.reference_period).casefold()
            or q_norm in format_competence_pt(inv.reference_period).casefold()
            or q_norm in format_competence_short(inv.reference_period).casefold()
            or q_norm in inv.supplier.casefold()
            or (inv.contract_id is not None and q_norm in inv.contract_id.casefold())
        ]
    else:
        filtered = cls_filtered

    # Ordena por gravidade mais severa e depois por competência e número natural
    filtered.sort(
        key=lambda pair: (
            pair[1].result.classification.severity_rank if pair[1] is not None else -1,
            natural_invoice_sort_key(pair[0]),
        )
    )

    csv_rows: list[tuple[Invoice, StoredAudit | None, ReviewStatus]] = [
        (
            inv,
            aud,
            _latest_audit_status(conn, aud.result.id)
            if aud is not None
            else ReviewStatus.PENDING_REVIEW,
        )
        for inv, aud in filtered
    ]
    visible_invoices = [inv for inv, _ in filtered]

    with f4:
        st.download_button(
            "Exportar CSV",
            data=build_invoices_csv_bytes(csv_rows),
            file_name="cobrancas_auditadas_farol.csv",
            mime="text/csv",
            key="billing_export_csv_btn",
            disabled=not csv_rows,
            width="stretch",
        )
    with f5:
        if st.button(
            "Reauditar",
            type="primary",
            key="audit_visible_btn",
            disabled=not visible_invoices,
            width="stretch",
        ):
            with st.spinner("Executando verificações determinísticas no motor…"):
                count, warns = _run_audits_for_invoices(
                    conn,
                    visible_invoices,
                    use_llm=use_llm,
                    approve_links=approve_links,
                )
            st.session_state["billing_flash"] = (
                "ai" if warns else "ok",
                "Auditoria concluída",
                f"{count} cobrança(s) auditada(s) e congelada(s) com snapshot das regras.",
            )
            if warns:
                st.session_state["billing_import_warnings"] = warns
            st.rerun()

    print_html = st.session_state.pop("billing_print_html", None)
    if isinstance(print_html, str) and print_html:
        st.iframe(print_html, height=1, alt="Janela de impressão da cobrança auditada")
        st.download_button(
            "Baixar ficha para impressão (.html)",
            data=print_html,
            file_name="ficha_auditoria_cobranca.html",
            mime="text/html",
            key="download_single_print_html_btn",
        )

    if not filtered:
        render_empty_state(
            "Nenhuma cobrança encontrada neste filtro",
            "Ajuste os filtros acima ou envie arquivos XML, CSV, XLSX, PDF ou DOCX.",
            next_action="Selecione outro escopo/classificação ou importe uma cobrança.",
        )
        return

    all_docs = fetch_documents(conn)
    doc_by_id = {doc.id: doc for doc in all_docs}
    doc_names = {doc.id: doc.file_name for doc in all_docs}
    reviewer = (
        str(st.session_state.get("reviewer_name", "auditor.local")).strip() or "auditor.local"
    )

    collapse_clean = len(filtered) > 3 and cls_choice == "Todas"
    clean_shown_header = False

    for inv, stored in filtered:
        is_clean = (
            stored is not None and stored.result.classification is AuditClassification.NO_DIVERGENCE
        )
        if collapse_clean and is_clean:
            if not clean_shown_header:
                st.caption(
                    "Notas sem divergência recolhidas abaixo para priorizar a análise dos achados:"
                )
                clean_shown_header = True
            label = (
                f"✓ Cobrança nº {inv.number} • {format_competence_short(inv.reference_period)} • "
                f"{format_brl(inv.total)} — Sem divergência"
            )
            with st.expander(label, expanded=False):
                _render_audit_card(
                    conn,
                    invoice=inv,
                    stored=stored,
                    doc_by_id=doc_by_id,
                    doc_names=doc_names,
                    reviewer=reviewer,
                )
        else:
            _render_audit_card(
                conn,
                invoice=inv,
                stored=stored,
                doc_by_id=doc_by_id,
                doc_names=doc_names,
                reviewer=reviewer,
            )

    audits_by_id = {
        aud.result.id: (inv, aud, _latest_audit_status(conn, aud.result.id))
        for inv, aud in filtered
        if aud is not None
    }
    editing_audit_id = st.session_state.get("editing_audit_id")
    if isinstance(editing_audit_id, str) and editing_audit_id in audits_by_id:
        dlg_inv, dlg_aud, dlg_status = audits_by_id[editing_audit_id]
        _open_audit_decision_dialog(
            dlg_inv,
            dlg_aud,
            rev_status=dlg_status,
            reviewer=reviewer,
        )
