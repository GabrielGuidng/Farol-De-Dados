"""Etapas 7.2 (Revisão compacta de regras e aditivos) e 7.3 (Vigência por competência)."""

import calendar
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4

import altair as alt
import pandas as pd
import streamlit as st
from pydantic import ValidationError

from backend.api import (
    AUTO_APPROVE_USER,
    audit_stored_invoice,
    fetch_amendments,
    fetch_audits,
    fetch_contract,
    fetch_documents,
    fetch_invoices,
    fetch_reviews,
    fetch_rules,
    get_rules_for_competence,
    mark_amendment_reviewed,
    register_amendment,
    review_rule,
)
from backend.motor import (
    ReasonCode,
    SelectionStatus,
    select_applicable_item_rules,
    select_applicable_rule,
)
from backend.schemas import (
    Amendment,
    Contract,
    ContractRule,
    Document,
    DocumentType,
    Evidence,
    ReferencePeriod,
    ReviewDecision,
    ReviewStatus,
    ReviewTargetType,
    RuleRelation,
    RuleType,
    ValidityPeriod,
)
from frontend.state import open_connection
from frontend.ui.components import (
    KpiItem,
    describe_rule_value,
    escape_text,
    highlight_excerpt_in_text,
    render_badge,
    render_banner,
    render_empty_state,
    render_html_table,
    render_kpi_row,
    render_page_header,
)
from frontend.ui.formatting import (
    REVIEW_STATUS_BADGES,
    RULE_RELATION_LABELS,
    RULE_RELATION_SHORT_LABELS,
    RULE_TYPE_LABELS,
    SELECTION_STATUS_BADGES,
    format_brl,
    format_compact_validity,
    format_competence_pt,
    format_confidence_pct,
    format_date_br,
    format_validity_br,
    parse_decimal_input,
)
from frontend.ui.tokens import CHART_COLORS, THEMES, ThemeName

_DEFAULT_RULE_CONFIRM_JUST = "Regra confirmada na conferência documental."
_DIALOG_DECISION_LABELS: dict[ReviewDecision, str] = {
    ReviewDecision.CORRECT: "Corrigida (válida)",
    ReviewDecision.CONFIRM: "Confirmada (sem alteração)",
    ReviewDecision.REJECT: "Rejeitada (descartada)",
}
_MONTH_BR_RE = re.compile(r"^([0-9]{2})/([0-9]{4})$")
_ISO_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_DATE_BR_RE = re.compile(r"^[0-9]{2}/[0-9]{2}/[0-9]{4}$")


def _refresh_unreviewed_contract_audits(conn: sqlite3.Connection, contract_id: str) -> None:
    """Recalcula auditorias sem decisão humana quando regras ou aditivos são revisados."""
    for inv in fetch_invoices(conn, contract_id):
        existing = fetch_audits(conn, inv.id)
        already_decided = any(
            bool(
                fetch_reviews(
                    conn,
                    target_type=ReviewTargetType.AUDIT_RESULT,
                    target_id=aud.result.id,
                )
            )
            for aud in existing
        )
        if not already_decided:
            audit_stored_invoice(conn, inv.id)


def _inline_doc_details_html(
    *,
    summary_label: str,
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
        f"<summary>{escape_text(summary_label)}</summary>"
        f'<div class="original-doc-hint">{escape_text(hint)}</div>'
        f'<div class="original-doc-viewer">{highlighted_html}</div>'
        "</details>"
    )


def _format_date_for_dialog(value: date | None, *, is_end: bool) -> str:
    if value is None:
        return ""
    if not is_end and value.day == 1:
        return f"{value.year:04d}-{value.month:02d}"
    if is_end and value.day == calendar.monthrange(value.year, value.month)[1]:
        return f"{value.year:04d}-{value.month:02d}"
    return value.isoformat()


def _parse_flexible_validity_date(raw: str, *, is_end: bool) -> date | None:
    cleaned = raw.strip()
    if not cleaned:
        return None
    if _ISO_DATE_RE.fullmatch(cleaned):
        return date.fromisoformat(cleaned)
    if _DATE_BR_RE.fullmatch(cleaned):
        return datetime.strptime(cleaned, "%d/%m/%Y").date()
    m_br = _MONTH_BR_RE.fullmatch(cleaned)
    if m_br is not None:
        period = ReferencePeriod.model_validate(f"{m_br.group(2)}-{m_br.group(1)}")
        return period.last_day if is_end else period.first_day
    try:
        period = ReferencePeriod.model_validate(cleaned)
    except ValidationError as exc:
        raise ValueError("Data inválida; informe AAAA-MM, AAAA-MM-DD ou DD/MM/AAAA.") from exc
    return period.last_day if is_end else period.first_day


def _build_corrected_rule(
    rule: ContractRule,
    *,
    value_raw: str,
    start_raw: str,
    end_raw: str,
    clause_raw: str,
    quantity_raw: str | None = None,
) -> ContractRule:
    """Constrói cópia corrigida de `ContractRule` preservando tipagem Decimal e validação."""
    start_date = _parse_flexible_validity_date(start_raw, is_end=False)
    end_date = _parse_flexible_validity_date(end_raw, is_end=True)

    stripped = value_raw.strip()
    updates: dict[str, object] = {}
    if stripped:
        if rule.rule_type in (
            RuleType.MONTHLY_PRICE,
            RuleType.ITEM_PRICE,
            RuleType.OVERAGE_PRICE,
            RuleType.INSS_RETENTION,
            RuleType.ISS_RETENTION,
            RuleType.APPROVAL_LIMIT,
        ):
            updates["value"] = parse_decimal_input(stripped)
        elif rule.rule_type in (RuleType.FRANCHISE, RuleType.PAYMENT_TERMS):
            updates["quantity"] = parse_decimal_input(stripped)
        elif rule.rule_type is RuleType.ADJUSTMENT:
            updates["adjustment_index"] = stripped
        elif rule.rule_type is RuleType.EXTENSION:
            initial_param = _format_date_for_dialog(rule.validity.end, is_end=True)
            if not end_raw.strip() or stripped != initial_param:
                end_date = _parse_flexible_validity_date(stripped, is_end=True)

    if rule.rule_type is RuleType.ITEM_PRICE and quantity_raw is not None:
        clean_qty = quantity_raw.strip()
        updates["quantity"] = parse_decimal_input(clean_qty) if clean_qty else None

    updates["validity"] = ValidityPeriod(start=start_date, end=end_date)

    clean_clause = clause_raw.strip() or None
    if clean_clause != rule.evidence.clause:
        if clean_clause is None and rule.evidence.without_page:
            raise ValueError("Documento sem página exige cláusula preenchida.")
        updates["evidence"] = Evidence.model_validate(
            {
                **rule.evidence.model_dump(exclude={"without_page"}),
                "clause": clean_clause,
            }
        )
    return rule.model_copy(update=updates)


def _initial_param_input(rule: ContractRule) -> str:
    if (
        rule.rule_type
        in (
            RuleType.MONTHLY_PRICE,
            RuleType.ITEM_PRICE,
            RuleType.OVERAGE_PRICE,
            RuleType.INSS_RETENTION,
            RuleType.ISS_RETENTION,
            RuleType.APPROVAL_LIMIT,
        )
        and rule.value is not None
    ):
        return f"{rule.value:.2f}".replace(".", ",")
    if rule.rule_type in (RuleType.FRANCHISE, RuleType.PAYMENT_TERMS) and rule.quantity is not None:
        return str(rule.quantity).replace(".", ",")
    if rule.rule_type is RuleType.ADJUSTMENT and rule.adjustment_index is not None:
        return rule.adjustment_index
    if rule.rule_type is RuleType.EXTENSION:
        return _format_date_for_dialog(rule.validity.end, is_end=True)
    return ""


def _clear_editing_rule() -> None:
    st.session_state.pop("editing_rule_id", None)


@st.dialog("Corrigir regra contratual", width="medium", on_dismiss=_clear_editing_rule)
def _open_rule_correction_dialog(
    rule: ContractRule,
    *,
    contract_id: str,
    reviewer: str,
) -> None:
    st.caption(f"Tipo: **{RULE_TYPE_LABELS[rule.rule_type]}** (v{rule.version})")
    with st.form(key=f"rule_correct_form_{rule.id}", border=False):
        new_param = st.text_input(
            "Valor / quantidade / índice / novo fim (AAAA-MM)",
            value=_initial_param_input(rule),
            key=f"corr_val_{rule.id}",
        )
        quantity_raw: str | None = None
        if rule.rule_type is RuleType.ITEM_PRICE:
            qty_default = str(rule.quantity).replace(".", ",") if rule.quantity is not None else ""
            quantity_raw = st.text_input(
                "Quantidade contratada / máxima",
                value=qty_default,
                placeholder="Ex.: 14",
                key=f"corr_qty_{rule.id}",
            )
        c_ini, c_fim = st.columns(2, gap="medium")
        with c_ini:
            start_raw = st.text_input(
                "Início da vigência",
                value=_format_date_for_dialog(rule.validity.start, is_end=False),
                placeholder="AAAA-MM ou DD/MM/AAAA",
                key=f"corr_start_{rule.id}",
            )
        with c_fim:
            end_raw = st.text_input(
                "Fim da vigência (opcional)",
                value=_format_date_for_dialog(rule.validity.end, is_end=True),
                placeholder="AAAA-MM ou vazio",
                key=f"corr_end_{rule.id}",
            )
        c_cl, c_dec = st.columns(2, gap="medium")
        with c_cl:
            clause_raw = st.text_input(
                "Cláusula",
                value=rule.evidence.clause or "",
                placeholder="Ex.: Cláusula 4ª (a) / Anexo A",
                key=f"corr_clause_{rule.id}",
            )
        with c_dec:
            chosen_decision = st.selectbox(
                "Estado após revisão",
                options=[ReviewDecision.CORRECT, ReviewDecision.CONFIRM, ReviewDecision.REJECT],
                format_func=lambda item: _DIALOG_DECISION_LABELS[item],
                key=f"corr_decision_{rule.id}",
            )
        just_corr = st.text_area(
            "Justificativa da correção (obrigatória)",
            placeholder="Descreva o motivo da correção conforme o documento original",
            key=f"just_{rule.id}",
            height=96,
        )
        _sp, c_cancel, c_save = st.columns([2.2, 1.0, 1.35], gap="small")
        cancel_clicked = c_cancel.form_submit_button("Cancelar", type="secondary", width="stretch")
        save_clicked = c_save.form_submit_button("Salvar correção", type="primary", width="stretch")

        if cancel_clicked:
            st.session_state.pop("editing_rule_id", None)
            st.rerun()

        if save_clicked:
            clean_just = just_corr.strip()
            if not clean_just:
                render_banner(
                    "bad",
                    "Justificativa obrigatória",
                    "Descreva o motivo da correção conforme o documento original.",
                )
            else:
                try:
                    corrected = (
                        _build_corrected_rule(
                            rule,
                            value_raw=new_param,
                            start_raw=start_raw,
                            end_raw=end_raw,
                            clause_raw=clause_raw,
                            quantity_raw=quantity_raw,
                        )
                        if chosen_decision is ReviewDecision.CORRECT
                        else None
                    )
                    with closing(open_connection()) as dlg_conn:
                        review_rule(
                            dlg_conn,
                            rule.id,
                            user=reviewer,
                            decision=chosen_decision,
                            justification=clean_just,
                            corrected_rule=corrected,
                        )
                        _refresh_unreviewed_contract_audits(dlg_conn, contract_id)
                except (InvalidOperation, ValueError, ValidationError) as exc:
                    render_banner("bad", "Erro ao salvar correção", str(exc))
                else:
                    st.session_state.pop("editing_rule_id", None)
                    st.session_state["rules_flash"] = (
                        "ok",
                        "Revisão registrada",
                        f"{RULE_TYPE_LABELS[rule.rule_type]} atualizada na trilha.",
                    )
                    st.rerun()


def _rule_compact_row_html(
    rule: ContractRule,
    *,
    source_doc: Document | None,
    previous_value_hint: str | None,
    is_auto_approved: bool = False,
) -> str:
    doc_name = source_doc.file_name if source_doc else "Documento de origem"
    page_txt = f"pág. {rule.evidence.page}" if rule.evidence.page is not None else "sem pág."
    loc_parts: list[str] = []
    if rule.evidence.clause:
        loc_parts.append(rule.evidence.clause)
    if rule.evidence.paragraph:
        loc_parts.append(f"parág. {rule.evidence.paragraph}")
    loc_parts.append(page_txt)
    loc_line = " • ".join(loc_parts)

    doc_link_html = _inline_doc_details_html(
        summary_label=f"Ver no documento ({page_txt})",
        full_text=source_doc.extracted_text if source_doc else None,
        excerpt=rule.evidence.literal_excerpt,
    )

    rel_short = RULE_RELATION_SHORT_LABELS[rule.relation]
    rel_tone = "info" if rule.relation is RuleRelation.CREATED else "ai"
    rel_badge = render_badge(
        rel_tone, "•" if rule.relation is RuleRelation.CREATED else "↺", rel_short
    )
    prev_html = (
        f'<div class="compact-rule-sub">← {escape_text(previous_value_hint)}</div>'
        if previous_value_hint
        else ""
    )

    if is_auto_approved and rule.review_status is ReviewStatus.CONFIRMED:
        rev_badge = render_badge("ok", "✓", "Aprovada (auto)")
        auto_hint = '<div class="compact-rule-sub">Confiança &gt; 85% • editável</div>'
    else:
        rev_tone, rev_icon, rev_label = REVIEW_STATUS_BADGES[rule.review_status]
        rev_badge = render_badge(rev_tone, rev_icon, rev_label)
        auto_hint = ""
    conf_txt = format_confidence_pct(rule.verification_score)

    missing_start_hint = (
        '<div class="compact-rule-sub">⚠ Falta data inicial</div>'
        if rule.validity.start is None
        else ""
    )
    version_sub = (
        f'<div class="compact-rule-sub">Versão {rule.version}</div>' if rule.version > 1 else ""
    )
    row_cls = "compact-rule-row" if rule.review_status.is_trusted else "compact-rule-row ai-pending"

    return (
        f'<div class="{row_cls}">'
        f"<div><strong>{escape_text(RULE_TYPE_LABELS[rule.rule_type])}</strong>{version_sub}</div>"
        f'<div class="mono-value">{escape_text(describe_rule_value(rule))}</div>'
        f"<div>{escape_text(format_compact_validity(rule.validity.start, rule.validity.end))}"
        f"{missing_start_hint}</div>"
        f"<div>{rel_badge}{prev_html}</div>"
        "<div>"
        f"<strong>{escape_text(doc_name)}</strong>"
        f"{doc_link_html}"
        f'<div class="compact-rule-sub">{escape_text(loc_line)}</div>'
        f'<div class="compact-rule-excerpt">“{escape_text(rule.evidence.literal_excerpt)}”</div>'
        "</div>"
        f'<div class="mono-value">{escape_text(conf_txt)}</div>'
        f"<div>{rev_badge}{auto_hint}</div>"
        "</div>"
    )


def _render_rule_review_tab(
    conn: sqlite3.Connection,
    *,
    contract_id: str,
    rules: tuple[ContractRule, ...],
    doc_by_id: dict[str, Document],
    amendments: tuple[Amendment, ...],
    reviewer: str,
    auto_approved_ids: set[str] | None = None,
) -> None:
    pending_rules = [r for r in rules if not r.review_status.is_trusted]
    trusted_rules = [r for r in rules if r.review_status.is_trusted]
    amendment_docs = [
        doc for doc in doc_by_id.values() if doc.document_type is DocumentType.AMENDMENT
    ]
    pending_amendments = [doc for doc in amendment_docs if not doc.reviewed]

    render_kpi_row(
        [
            KpiItem("Regras extraídas", str(len(rules))),
            KpiItem("Pendentes / Inconclusivas", str(len(pending_rules))),
            KpiItem("Confirmadas / Corrigidas", str(len(trusted_rules))),
            KpiItem("Aditivos por revisar", str(len(pending_amendments))),
        ]
    )

    auto_ids = auto_approved_ids or set()
    if auto_ids:
        render_banner(
            "info",
            "Regras aprovadas automaticamente (confiança > 85%)",
            (
                f"{len(auto_ids)} regra(s) foram confirmadas automaticamente por atingirem "
                "confiança acima de 85% na verificação documental. Caso queira alterar alguma, "
                "clique em Corrigir ou Rejeitar na linha correspondente."
            ),
        )

    h_left, h_mid, h_right = st.columns([2.2, 1.4, 1.3], gap="small", vertical_alignment="center")
    with h_left:
        st.markdown("**Regras contratuais extraídas (Evidência Documental)**")
        st.caption(
            "Nenhuma regra entra em vigor sem vínculo a documento, cláusula, página e trecho."
        )
    with h_mid:
        filter_raw = st.segmented_control(
            "Fila exibida",
            options=["Todas as regras", "Pendentes"],
            default="Todas as regras",
            key="rules_filter_mode",
            label_visibility="collapsed",
        )
        filter_mode = (
            filter_raw
            if isinstance(filter_raw, str) and filter_raw in ("Todas as regras", "Pendentes")
            else "Todas as regras"
        )
    with h_right:
        if st.button(
            "Confirmar pendentes do contrato",
            type="secondary",
            key="confirm_all_pending_rules_btn",
            disabled=not pending_rules,
            width="stretch",
        ):
            for p_rule in pending_rules:
                review_rule(
                    conn,
                    p_rule.id,
                    user=reviewer,
                    decision=ReviewDecision.CONFIRM,
                    justification="Regra confirmada em lote na conferência do contrato.",
                )
            _refresh_unreviewed_contract_audits(conn, contract_id)
            st.session_state["rules_flash"] = (
                "ok",
                "Regras confirmadas em lote",
                f"{len(pending_rules)} regra(s) pendente(s) confirmada(s) para {contract_id}.",
            )
            st.rerun()

    visible_rules = pending_rules if filter_mode == "Pendentes" else list(rules)
    rule_by_id = {r.id: r for r in rules}
    prev_hint_by_new_id: dict[str, str] = {}
    for am in amendments:
        if am.new_rule is not None and am.affected_rule_id in rule_by_id:
            prev_hint_by_new_id[am.new_rule.id] = describe_rule_value(
                rule_by_id[am.affected_rule_id]
            )

    if not visible_rules:
        if not rules:
            render_empty_state(
                "Nenhuma regra extraída ainda",
                "Envie o contrato-base na tela Contratos e Documentos para extrair regras.",
            )
        else:
            render_empty_state(
                "Nenhuma regra pendente na fila",
                "Todas as regras deste contrato já passaram por revisão humana.",
                next_action="Confira a aba Vigência por Competência ao lado.",
            )
    else:
        head_info, head_act = st.columns([4.55, 1.45], gap="small")
        with head_info:
            st.html(
                '<div class="compact-rule-head">'
                "<div>TIPO</div>"
                "<div>VALOR / CONDIÇÃO</div>"
                "<div>VIGÊNCIA</div>"
                "<div>RELAÇÃO</div>"
                "<div>EVIDÊNCIA DE ORIGEM</div>"
                "<div>CONF.</div>"
                "<div>REVISÃO</div>"
                "</div>"
            )
        with head_act:
            st.html('<div class="compact-rule-actions-head">AÇÕES HUMANAS</div>')

        ordered_rules = sorted(
            visible_rules,
            key=lambda r: (
                0 if not r.review_status.is_trusted else 1,
                r.validity.start or date.min,
                r.version,
                r.rule_type.value,
            ),
        )
        for rule in ordered_rules:
            source_doc = doc_by_id.get(rule.evidence.source_document_id)
            col_info, col_act = st.columns([4.55, 1.45], gap="small", vertical_alignment="top")
            with col_info:
                st.html(
                    _rule_compact_row_html(
                        rule,
                        source_doc=source_doc,
                        previous_value_hint=prev_hint_by_new_id.get(rule.id),
                        is_auto_approved=rule.id in auto_ids,
                    )
                )
            with col_act:
                b_conf, b_corr = st.columns(2, gap="small")
                with b_conf:
                    if st.button(
                        "Confirmar",
                        key=f"confirm_rule_btn_{rule.id}",
                        type="primary" if not rule.review_status.is_trusted else "secondary",
                        disabled=rule.review_status is ReviewStatus.CONFIRMED,
                        width="stretch",
                    ):
                        review_rule(
                            conn,
                            rule.id,
                            user=reviewer,
                            decision=ReviewDecision.CONFIRM,
                            justification=_DEFAULT_RULE_CONFIRM_JUST,
                        )
                        _refresh_unreviewed_contract_audits(conn, contract_id)
                        st.session_state["rules_flash"] = (
                            "ok",
                            "Regra confirmada",
                            f"{RULE_TYPE_LABELS[rule.rule_type]} confirmada na trilha.",
                        )
                        st.rerun()

                with b_corr:
                    if st.button(
                        "Corrigir",
                        key=f"open_corr_btn_{rule.id}",
                        type="secondary",
                        width="stretch",
                    ):
                        st.session_state["editing_rule_id"] = rule.id

                with st.popover("Rejeitar", width="stretch"):
                    with st.form(key=f"rule_reject_form_{rule.id}"):
                        just_rej = st.text_input(
                            "Motivo da rejeição",
                            value="Regra descartada por não se aplicar ao contrato.",
                            key=f"just_rej_{rule.id}",
                        )
                        if st.form_submit_button(
                            "Confirmar rejeição", type="secondary", width="stretch"
                        ):
                            clean_rej = just_rej.strip()
                            if not clean_rej:
                                render_banner(
                                    "bad",
                                    "Justificativa obrigatória",
                                    "Informe o motivo da rejeição.",
                                )
                            else:
                                review_rule(
                                    conn,
                                    rule.id,
                                    user=reviewer,
                                    decision=ReviewDecision.REJECT,
                                    justification=clean_rej,
                                )
                                _refresh_unreviewed_contract_audits(conn, contract_id)
                                st.session_state["rules_flash"] = (
                                    "ok",
                                    "Regra rejeitada",
                                    f"{RULE_TYPE_LABELS[rule.rule_type]} marcada como rejeitada.",
                                )
                                st.rerun()

        editing_id = st.session_state.get("editing_rule_id")
        if isinstance(editing_id, str) and editing_id in rule_by_id:
            _open_rule_correction_dialog(
                rule_by_id[editing_id],
                contract_id=contract_id,
                reviewer=reviewer,
            )

    st.divider()
    st.subheader("Termos aditivos do contrato")
    if not amendment_docs:
        render_empty_state(
            "Nenhum termo aditivo recebido",
            "Quando houver aditivos em PDF ou DOCX, eles aparecerão aqui em lista compacta.",
        )
        return

    linked_doc_ids = {item.source_document_id for item in amendments}
    for doc in amendment_docs:
        status_badge = (
            render_badge("ok", "✓", "Aditivo revisado")
            if doc.reviewed
            else render_badge("ai", "✦", "Aguardando revisão")
        )
        link_txt = (
            "Vínculo na linha do tempo registrado"
            if doc.id in linked_doc_ids
            else "Sem vínculo explícito de alteração"
        )
        doc_rules = [r for r in rules if r.evidence.source_document_id == doc.id]
        other_rules = [r for r in rules if r.evidence.source_document_id != doc.id]

        c_am_info, c_am_act = st.columns([3.8, 1.4], gap="small", vertical_alignment="center")
        with c_am_info:
            st.html(
                '<div class="compact-amend-row">'
                f"<div><strong>{escape_text(doc.file_name)}</strong>"
                f'<div class="compact-rule-sub">SHA-256 {escape_text(doc.file_hash[:12])}… • '
                f"{escape_text(link_txt)}</div></div>"
                f"<span>{status_badge}</span>"
                "</div>"
            )
        with c_am_act:
            if not doc.reviewed:
                if st.button(
                    "Marcar aditivo como revisado",
                    key=f"mark_amend_btn_{doc.id}",
                    type="primary",
                    width="stretch",
                ):
                    mark_amendment_reviewed(
                        conn,
                        doc.id,
                        user=reviewer,
                        justification=f"Aditivo {doc.file_name} conferido e validado.",
                    )
                    _refresh_unreviewed_contract_audits(conn, contract_id)
                    st.session_state["rules_flash"] = (
                        "ok",
                        "Aditivo revisado",
                        f"{doc.file_name} marcado como revisado na trilha.",
                    )
                    st.rerun()
            if doc.id not in linked_doc_ids and doc_rules and other_rules:
                with st.popover("Vincular regra", width="stretch"):
                    with st.form(key=f"link_amendment_{doc.id}"):
                        affected_rule = st.selectbox(
                            "Regra anterior afetada",
                            options=other_rules,
                            format_func=lambda r: (
                                f"{RULE_TYPE_LABELS[r.rule_type]} ({describe_rule_value(r)})"
                            ),
                            key=f"aff_{doc.id}",
                        )
                        relation = st.selectbox(
                            "Relação do aditivo",
                            options=[RuleRelation.ALTERED, RuleRelation.REVOKED],
                            format_func=lambda rel: RULE_RELATION_LABELS[rel],
                            key=f"rel_{doc.id}",
                        )
                        candidate_new = st.selectbox(
                            "Nova regra (quando alterada)",
                            options=doc_rules,
                            format_func=lambda r: (
                                f"{RULE_TYPE_LABELS[r.rule_type]} ({describe_rule_value(r)})"
                            ),
                            key=f"new_r_{doc.id}",
                        )
                        eff_default = (
                            candidate_new.validity.start
                            if candidate_new.validity.start is not None
                            else date(2026, 5, 1)
                        )
                        eff_date = st.date_input(
                            "Data de efeito do aditivo",
                            value=eff_default,
                            key=f"eff_{doc.id}",
                        )
                        if st.form_submit_button(
                            "Salvar vínculo", type="secondary", width="stretch"
                        ):
                            try:
                                register_amendment(
                                    conn,
                                    Amendment(
                                        id=f"amend-{uuid4().hex[:10]}",
                                        affected_rule_id=affected_rule.id,
                                        new_rule=candidate_new
                                        if relation is RuleRelation.ALTERED
                                        else None,
                                        source_document_id=doc.id,
                                        effective_date=eff_date,
                                        relation=relation,
                                    ),
                                )
                            except (ValueError, ValidationError) as exc:
                                render_banner("bad", "Erro ao vincular aditivo", str(exc))
                            else:
                                _refresh_unreviewed_contract_audits(conn, contract_id)
                                st.session_state["rules_flash"] = (
                                    "ok",
                                    "Aditivo vinculado",
                                    f"Vínculo de {doc.file_name} registrado.",
                                )
                                st.rerun()


def _iter_reference_periods(
    start_period: ReferencePeriod,
    end_period: ReferencePeriod,
    *,
    max_months: int = 36,
) -> list[ReferencePeriod]:
    periods: list[ReferencePeriod] = []
    year, month = start_period.year, start_period.month
    while (year, month) <= (end_period.year, end_period.month) and len(periods) < max_months:
        periods.append(ReferencePeriod(year=year, month=month))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return periods


def _active_price_rules_for_month(
    item_rules: list[ContractRule],
    monthly_rules: list[ContractRule],
    period: ReferencePeriod,
) -> tuple[Decimal | None, tuple[ContractRule, ...]]:
    if item_rules:
        sels = select_applicable_item_rules(item_rules, period)
        selected_rules: list[ContractRule] = []
        for item_key, sel in sels.items():
            if sel.status is SelectionStatus.SELECTED and sel.rule is not None:
                selected_rules.append(sel.rule)
            elif sel.status is SelectionStatus.NEEDS_REVIEW and all(
                r.code is ReasonCode.MID_PERIOD_CHANGE for r in sel.reasons
            ):
                matching = [
                    r
                    for r in item_rules
                    if r.validity.start is not None
                    and r.validity.start <= period.last_day
                    and (r.validity.end is None or r.validity.end >= period.first_day)
                    and " ".join((r.item_name or "").casefold().split())
                    == " ".join(item_key.split())
                ]
                if not matching:
                    mid_ids = {rid for reason in sel.reasons for rid in reason.rule_ids}
                    matching = [r for r in item_rules if r.id in mid_ids]
                if matching:
                    selected_rules.append(
                        max(matching, key=lambda r: (r.validity.start or date.min, r.version))
                    )
        if selected_rules:
            total = sum(
                (r.value * r.quantity for r in selected_rules if r.value and r.quantity),
                Decimal("0.00"),
            )
            return total, tuple(selected_rules)

    if monthly_rules:
        sel_m = select_applicable_rule(monthly_rules, period)
        if sel_m.status is SelectionStatus.SELECTED and sel_m.rule is not None:
            return sel_m.rule.value, (sel_m.rule,)
        if sel_m.status is SelectionStatus.NEEDS_REVIEW and all(
            r.code is ReasonCode.MID_PERIOD_CHANGE for r in sel_m.reasons
        ):
            mid_ids = {rid for reason in sel_m.reasons for rid in reason.rule_ids}
            cands = [r for r in monthly_rules if r.id in mid_ids]
            if cands:
                picked = max(cands, key=lambda r: (r.validity.start or date.min, r.version))
                return picked.value, (picked,)

    return None, ()


def _build_vigencia_timeline_chart(
    contract: Contract | None,
    timeline_rules: tuple[ContractRule, ...],
    selected_period: ReferencePeriod,
    doc_by_id: dict[str, Document],
    theme: ThemeName,
) -> alt.Chart | alt.LayerChart | None:
    """Constrói gráfico em linha em degrau com evolução mensal da vigência e marcos de aditivos."""
    item_rules = [
        r
        for r in timeline_rules
        if r.rule_type is RuleType.ITEM_PRICE
        and r.review_status is not ReviewStatus.REJECTED
        and r.quantity_scope in (None, "MONTHLY")
        and r.value is not None
        and r.quantity is not None
    ]
    monthly_rules = [
        r
        for r in timeline_rules
        if r.rule_type is RuleType.MONTHLY_PRICE
        and r.review_status is not ReviewStatus.REJECTED
        and r.value is not None
    ]
    price_rules = [*item_rules, *monthly_rules]
    if not price_rules:
        return None

    starts = [
        d
        for d in (
            contract.validity.start if contract is not None else None,
            *(r.validity.start for r in price_rules),
        )
        if d is not None
    ]
    if not starts:
        return None
    min_start = min(starts)

    ext_ends = [
        r.validity.end
        for r in timeline_rules
        if r.rule_type is RuleType.EXTENSION
        and r.review_status.is_trusted
        and r.validity.end is not None
    ]
    end_candidates = [
        d
        for d in (
            contract.validity.end if contract is not None else None,
            *ext_ends,
            *(r.validity.end for r in price_rules),
            selected_period.last_day,
        )
        if d is not None
    ]
    rule_starts = [r.validity.start for r in price_rules if r.validity.start is not None]
    max_end = (
        max(end_candidates)
        if end_candidates
        else (max(rule_starts) + timedelta(days=150) if rule_starts else selected_period.last_day)
    )
    start_period = ReferencePeriod(year=min_start.year, month=min_start.month)
    end_period = ReferencePeriod(year=max_end.year, month=max_end.month)
    periods = _iter_reference_periods(start_period, end_period)

    rows: list[dict[str, object]] = []
    milestones: list[dict[str, object]] = []
    prev_sig: tuple[tuple[str, int], ...] | None = None

    for period in periods:
        total_val, active = _active_price_rules_for_month(item_rules, monthly_rules, period)
        if total_val is None or not active:
            continue
        sig = tuple(sorted((r.id, r.version) for r in active))
        comp_pt = format_competence_pt(period)
        origin_docs = ", ".join(
            dict.fromkeys(
                doc_by_id[r.evidence.source_document_id].file_name
                if r.evidence.source_document_id in doc_by_id
                else r.evidence.source_document_id
                for r in active
            )
        )
        max_ver = max(r.version for r in active)
        marco = "Contrato-Base" if max_ver == 1 else f"Vigência v{max_ver}"
        rows.append(
            {
                "competencia": comp_pt,
                "valor": float(total_val),
                "valor_brl": format_brl(total_val),
                "marco": marco,
                "origem": origin_docs,
                "selecionada": period == selected_period,
            }
        )
        if prev_sig is not None and sig != prev_sig:
            milestones.append(
                {
                    "competencia": comp_pt,
                    "evento": f"Início {marco}: {format_brl(total_val)}",
                }
            )
        prev_sig = sig

    if not rows:
        return None

    palette = THEMES[theme]
    series_colors = CHART_COLORS[theme]
    ordered_periods = [str(r["competencia"]) for r in rows]
    df = pd.DataFrame(rows)
    distinct_vals = {r["valor"] for r in rows}
    zero_scale = len(distinct_vals) <= 1

    layers: list[alt.Chart] = [
        alt.Chart(df)
        .mark_line(
            interpolate="step-after",
            strokeWidth=2.5,
            color=series_colors[0],
            point=alt.OverlayMarkDef(filled=True, size=65, color=series_colors[0]),
        )
        .encode(
            x=alt.X(
                "competencia:N",
                sort=ordered_periods,
                title="Competência (Linha do Tempo)",
            ),
            y=alt.Y(
                "valor:Q",
                title="Valor contratual vigente (R$)",
                scale=alt.Scale(zero=zero_scale),
            ),
            tooltip=[
                alt.Tooltip("competencia:N", title="Competência"),
                alt.Tooltip("valor_brl:N", title="Valor vigente"),
                alt.Tooltip("marco:N", title="Marco contratual"),
                alt.Tooltip("origem:N", title="Documento de origem"),
            ],
        )
    ]

    if milestones:
        df_ms = pd.DataFrame(milestones)
        layers.append(
            alt.Chart(df_ms)
            .mark_rule(
                strokeDash=[4, 4],
                strokeWidth=1.5,
                color=series_colors[2],
            )
            .encode(
                x=alt.X("competencia:N", sort=ordered_periods),
                tooltip=[
                    alt.Tooltip("competencia:N", title="Transição em"),
                    alt.Tooltip("evento:N", title="Evento"),
                ],
            )
        )

    selected_rows = [r for r in rows if bool(r["selecionada"])]
    if selected_rows:
        df_sel = pd.DataFrame(selected_rows)
        layers.append(
            alt.Chart(df_sel)
            .mark_circle(
                size=160,
                opacity=0.95,
                color=series_colors[3],
                stroke=palette["primary"],
                strokeWidth=2,
            )
            .encode(
                x=alt.X("competencia:N", sort=ordered_periods),
                y=alt.Y("valor:Q"),
                tooltip=[
                    alt.Tooltip("competencia:N", title="Competência consultada"),
                    alt.Tooltip("valor_brl:N", title="Valor vigente"),
                    alt.Tooltip("marco:N", title="Marco contratual"),
                ],
            )
        )

    combined: alt.Chart | alt.LayerChart = (
        layers[0] if len(layers) == 1 else alt.layer(*layers)
    ).properties(
        title="Linha do tempo: Evolução do valor contratual vigente",
        height=230,
    )
    return (
        combined.properties(usermeta={"embedOptions": {"actions": False}})
        .configure(background=palette["bg-surface"])
        .configure_view(stroke=palette["border"], strokeWidth=1)
        .configure_axis(
            labelColor=palette["text-secondary"],
            titleColor=palette["text-secondary"],
            gridColor=palette["border"],
            domainColor=palette["border"],
            tickColor=palette["border"],
        )
        .configure_title(color=palette["text-primary"])
    )


def _render_timeline_html(
    timeline_rules: tuple[ContractRule, ...],
    selected_ids: set[str],
    doc_by_id: dict[str, Document],
    period_str: str,
) -> str:
    ordered = sorted(
        timeline_rules,
        key=lambda r: (
            r.rule_type.value,
            r.validity.start or date.min,
            r.version,
            r.id,
        ),
    )
    nodes: list[str] = []
    for rule in ordered:
        is_active = rule.id in selected_ids
        node_cls = "timeline-node active" if is_active else "timeline-node"
        source_doc = doc_by_id.get(rule.evidence.source_document_id)
        origin_name = source_doc.file_name if source_doc else "Documento de origem"
        badge = (
            render_badge("ok", "✓", f"Vigente {period_str}")
            if is_active
            else render_badge("info", "●", f"Versão {rule.version}")
        )
        validity_txt = escape_text(format_compact_validity(rule.validity.start, rule.validity.end))
        nodes.append(
            f'<div class="{node_cls}">'
            f'<div class="node-version">{escape_text(RULE_TYPE_LABELS[rule.rule_type])} • '
            f"v{rule.version}</div>"
            f'<div class="node-title">{escape_text(describe_rule_value(rule))}</div>'
            f'<div class="node-dates">{validity_txt}</div>'
            f'<div class="compact-rule-sub" style="margin:6px 0;">{escape_text(origin_name)}</div>'
            f"{badge}"
            "</div>"
        )
    return f'<div class="timeline-track">{"".join(nodes)}</div>'


def _render_competence_tab(
    conn: sqlite3.Connection,
    *,
    contract_id: str,
    doc_by_id: dict[str, Document],
    contract: Contract | None = None,
) -> None:
    st.subheader("Consulta de regras vigentes por competência")
    c_period, c_hint = st.columns([1.0, 2.2], vertical_alignment="bottom")
    with c_period:
        period_raw = st.text_input(
            "Competência (AAAA-MM)",
            value=str(st.session_state.get("selected_competence", "2026-04")),
            key="competence_input",
        )
    with c_hint:
        st.caption(
            "Mostra em tabela única quais regras não rejeitadas cobrem o mês inteiro, "
            "sem presumir datas nem pró-rata."
        )

    try:
        period = ReferencePeriod.model_validate(period_raw.strip())
    except ValidationError:
        render_banner(
            "bad",
            "Competência inválida",
            "Informe a competência no formato AAAA-MM (por exemplo, 2026-04 ou 2026-10).",
        )
        return

    st.session_state["selected_competence"] = str(period)
    result = get_rules_for_competence(conn, contract_id, period)

    for issue in result.timeline.issues:
        render_banner("bad", "Problema na linha do tempo", issue.message)

    st.markdown("#### Linha do tempo da vigência contratual")
    if not result.timeline.rules:
        render_empty_state(
            "Nenhuma regra na linha do tempo",
            "Envie e mantenha ao menos uma regra não rejeitada para visualizar a vigência.",
        )
        return

    selected_ids = {
        sel.rule.id
        for sel in result.selections.values()
        if sel.status is SelectionStatus.SELECTED and sel.rule is not None
    }
    raw_theme = st.session_state.get("theme", "dark")
    active_theme: ThemeName = "light" if raw_theme == "light" else "dark"
    active_contract = contract if contract is not None else fetch_contract(conn, contract_id)
    vig_chart = _build_vigencia_timeline_chart(
        active_contract,
        result.timeline.rules,
        period,
        doc_by_id,
        active_theme,
    )
    if vig_chart is not None:
        st.altair_chart(
            vig_chart,
            theme=None,
            width="stretch",
            alt=(
                "Gráfico em linha da evolução do valor contratual vigente ao longo da linha do "
                "tempo"
            ),
            key="chart_vigencia_timeline",
        )
    st.html(_render_timeline_html(result.timeline.rules, selected_ids, doc_by_id, str(period)))

    st.markdown(f"#### Regras aplicáveis em `{period}`")
    table_rows: list[list[str]] = []
    for rule_type in RuleType:
        selection = result.selections[rule_type]
        tone, icon, label = SELECTION_STATUS_BADGES[selection.status]
        badge = render_badge(tone, icon, label)

        if selection.rule is not None:
            rule = selection.rule
            doc = doc_by_id.get(rule.evidence.source_document_id)
            doc_name = doc.file_name if doc else "Documento de origem"
            extra_ai = (
                " " + render_badge("ai", "✦", "Pendente revisão")
                if not rule.review_status.is_trusted
                else ""
            )
            page_txt = (
                f"pág. {rule.evidence.page}" if rule.evidence.page is not None else "sem pág."
            )
            details_html = _inline_doc_details_html(
                summary_label=f"Ver no documento ({page_txt})",
                full_text=doc.extracted_text if doc else None,
                excerpt=rule.evidence.literal_excerpt,
            )
            origin_cell = (
                f"<strong>{escape_text(doc_name)}</strong> (v{rule.version})"
                f'<div class="compact-rule-sub">{escape_text(rule.evidence.clause or "Cláusula")} '
                f"• {escape_text(page_txt)}</div>"
                f"{details_html}"
            )
            val_txt = describe_rule_value(rule)
            vig_txt = format_validity_br(rule.validity.start, rule.validity.end)
            status_cell = f"{badge}{extra_ai}"
        else:
            reasons_txt = (
                " ".join(r.message for r in selection.reasons) or "Nenhuma regra aplicável."
            )
            val_txt = "—"
            vig_txt = f"{format_date_br(period.first_day)} a {format_date_br(period.last_day)}"
            origin_cell = f'<span class="compact-rule-sub">{escape_text(reasons_txt)}</span>'
            status_cell = badge

        table_rows.append(
            [
                RULE_TYPE_LABELS[rule_type],
                val_txt,
                vig_txt,
                status_cell,
                origin_cell,
            ]
        )

    render_html_table(
        ["Tipo de regra", "Valor vigente", "Vigência", "Situação", "Origem e evidência"],
        table_rows,
        html_cols={3, 4},
    )


def render_rules_vigencia_view(conn: sqlite3.Connection) -> None:
    """Renderiza revisão humana compacta (7.2) e vigência por competência (7.3)."""
    render_page_header(
        "Regras e Vigência",
        (
            "Confronte cada regra extraída com o documento original em tabela compacta "
            "e consulte a vigência por competência."
        ),
        eyebrow="Etapas 2 e 3 • Validação & Linha do Tempo",
    )

    flash = st.session_state.pop("rules_flash", None)
    if isinstance(flash, tuple) and len(flash) == 3:
        render_banner(flash[0], flash[1], flash[2])

    contract_id: str | None = st.session_state.get("selected_contract_id")
    contract = fetch_contract(conn, contract_id) if contract_id else None
    if contract is None:
        render_empty_state(
            "Nenhum contrato selecionado",
            "Cadastre ou selecione um contrato para revisar regras e consultar vigências.",
            next_action="Acesse Contratos e Documentos para cadastrar o primeiro contrato.",
        )
        return

    st.caption(
        f"Contrato ativo: **{contract.id}** — {contract.supplier} (CNPJ {contract.supplier_cnpj})"
    )

    rules = fetch_rules(conn, contract.id)
    documents = fetch_documents(conn, contract.id)
    doc_by_id = {doc.id: doc for doc in documents}
    amendments = fetch_amendments(conn, contract.id)
    rule_reviews = fetch_reviews(conn, target_type=ReviewTargetType.RULE)
    latest_review_user: dict[str, str] = {}
    for rev in rule_reviews:
        latest_review_user[rev.target_id] = rev.user
    auto_approved_ids = {
        r.id
        for r in rules
        if r.review_status is ReviewStatus.CONFIRMED
        and latest_review_user.get(r.id) == AUTO_APPROVE_USER
    }
    reviewer = (
        str(st.session_state.get("reviewer_name", "auditor.local")).strip() or "auditor.local"
    )

    tab_review, tab_competence = st.tabs(["Revisão Humana e Aditivos", "Vigência por Competência"])
    with tab_review:
        _render_rule_review_tab(
            conn,
            contract_id=contract.id,
            rules=rules,
            doc_by_id=doc_by_id,
            amendments=amendments,
            reviewer=reviewer,
            auto_approved_ids=auto_approved_ids,
        )
    with tab_competence:
        _render_competence_tab(
            conn,
            contract_id=contract.id,
            doc_by_id=doc_by_id,
            contract=contract,
        )
