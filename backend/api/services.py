"""Camada de serviços (casos de uso) que compõe leitura, agentes, motor e persistência."""

import re
import sqlite3
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import uuid4

from backend.agentes import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    AnchoringCheckResult,
    AuditReport,
    ExtractedRuleCandidate,
    ExtractionMetadata,
    FakeLLMClient,
    FiscalPdfInput,
    InvoiceWorkflowState,
    JuristaExtractionPayload,
    JuristaInput,
    LLMClient,
    LLMExecutionError,
    TradutorResult,
    build_invoice_report_node,
    fetch_invoice_rules_node,
    link_invoice_items_node,
    run_fiscal_pdf_agent,
    run_invoice_motor_node,
    run_jurista_agent,
)
from backend.leitura import (
    DEFAULT_MIN_TEXT_CHARS,
    DocxReadResult,
    PdfPage,
    PdfReadResult,
    UnsafeXmlError,
    detect_spreadsheet_kind,
    extract_dossier_artifacts,
    has_structured_financial_sections,
    has_supplemental_contract_rules,
    import_certificates_csv,
    import_certificates_xlsx,
    import_invoices_csv,
    import_invoices_xlsx,
    import_payment_orders_csv,
    import_payment_orders_xlsx,
    parse_nfe,
    parse_nfse,
    read_docx,
    read_pdf,
)
from backend.motor import (
    PaymentCycleAuditResult,
    PaymentFinding,
    RuleSelection,
    TimelineResult,
    apply_amendments,
    audit_invoice,
    audit_payment_cycle,
    normalize_item_key,
    select_applicable_rule,
)
from backend.persistencia import (
    StoredAudit,
    get_audit,
    get_contract,
    get_document,
    get_invoice,
    get_rule,
    init_db,
    list_amendments,
    list_audits,
    list_contracts,
    list_documents,
    list_financial_summaries,
    list_invoices,
    list_payment_orders,
    list_reviews,
    list_rules,
    list_tax_certificates,
    receive_document,
    record_audit_review,
    record_document_review,
    record_rule_review,
    save_amendment,
    save_audit,
    save_contract,
    save_document,
    save_financial_summary,
    save_invoice,
    save_payment_order,
    save_rule,
    save_tax_certificate,
)
from backend.schemas import (
    Amendment,
    AuditClassification,
    AuditResult,
    Check,
    Contract,
    ContractRule,
    ContractStatus,
    Document,
    DocumentType,
    DossierFinancialSummary,
    HumanReview,
    Invoice,
    InvoiceSource,
    PaymentOrder,
    ReadQuality,
    ReferencePeriod,
    ReviewDecision,
    ReviewStatus,
    ReviewTargetType,
    RuleRelation,
    RuleType,
    StrictModel,
    TaxCertificate,
    ValidityPeriod,
    is_valid_cnpj,
    normalize_cnpj,
)


class DocumentProcessingResult(StrictModel):
    """Resultado do processamento completo de um contrato ou aditivo."""

    document: Document
    rules: tuple[ContractRule, ...] = ()
    anchoring_results: tuple[AnchoringCheckResult, ...] = ()
    warnings: tuple[str, ...] = ()
    schema_valid: bool = True
    metadata: ExtractionMetadata


class RuleReviewResult(StrictModel):
    """Regra atualizada após decisão humana acompanhada do registro na trilha."""

    rule: ContractRule
    review: HumanReview


class DocumentReviewResult(StrictModel):
    """Documento atualizado após decisão humana acompanhada do registro na trilha."""

    document: Document
    review: HumanReview


class InvoiceImportResult(StrictModel):
    """Resultado da importação de documento de cobrança com vínculo contratual."""

    document: Document
    invoices: tuple[Invoice, ...] = ()
    unlinked_invoice_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class InvoiceAuditOutcome(StrictModel):
    """Resultado persistido da auditoria de uma cobrança com relatório verificado."""

    stored_audit: StoredAudit
    report: AuditReport
    translation_result: TradutorResult | None = None
    awaiting_link_review: bool = False
    warnings: tuple[str, ...] = ()


class CompetenceRulesResult(StrictModel):
    """Seleção das regras vigentes por tipo para uma competência."""

    contract_id: str
    period: ReferencePeriod
    timeline: TimelineResult
    selections: dict[RuleType, RuleSelection]


UploadClassificationMode = Literal["AUTO", "CONTRACT", "AMENDMENT", "BILLING", "DOSSIER"]


class UploadedFileOutcome(StrictModel):
    """Resultado individual do processamento de um arquivo enviado em lote."""

    file_name: str
    detected_kind: str
    document: Document | None = None
    contract_id: str | None = None
    rules_count: int = 0
    invoices_count: int = 0
    unlinked_invoices_count: int = 0
    warnings: tuple[str, ...] = ()


class BatchUploadResult(StrictModel):
    """Resultado agregado do envio direto de múltiplos arquivos do computador."""

    auto_registered_contracts: tuple[Contract, ...] = ()
    active_contract_id: str | None = None
    outcomes: tuple[UploadedFileOutcome, ...] = ()
    warnings: tuple[str, ...] = ()
    audits_count: int = 0
    divergences_count: int = 0


class DocumentDeletionResult(StrictModel):
    """Resumo da exclusão de um documento enviado e de seus itens derivados."""

    document_id: str
    file_name: str
    deleted_rules: int = 0
    deleted_amendments: int = 0
    deleted_invoices: int = 0
    deleted_audits: int = 0


def _unique_messages(*groups: Sequence[str | None]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for message in group:
            if message is None:
                continue
            clean = message.strip()
            if clean and clean not in seen:
                seen.add(clean)
                ordered.append(clean)
    return tuple(ordered)


def register_contract(conn: sqlite3.Connection, contract: Contract) -> Contract:
    """Cadastra ou atualiza um contrato na persistência e devolve a entidade salva."""
    save_contract(conn, contract)
    return contract


def fetch_contract(conn: sqlite3.Connection, contract_id: str) -> Contract | None:
    """Carrega um contrato pelo identificador, ou devolve None se não existir."""
    return get_contract(conn, contract_id)


def fetch_contracts(conn: sqlite3.Connection) -> tuple[Contract, ...]:
    """Lista os contratos cadastrados ordenados por identificador."""
    return list_contracts(conn)


def fetch_documents(
    conn: sqlite3.Connection, contract_id: str | None = None
) -> tuple[Document, ...]:
    """Lista documentos recebidos, opcionalmente filtrados por contrato."""
    return list_documents(conn, contract_id=contract_id)


def fetch_rules(
    conn: sqlite3.Connection,
    contract_id: str | None = None,
    review_status: ReviewStatus | None = None,
) -> tuple[ContractRule, ...]:
    """Lista regras extraídas ou consolidadas, com filtros opcionais."""
    rules = list_rules(conn, contract_id=contract_id)
    if review_status is None:
        return rules
    return tuple(rule for rule in rules if rule.review_status is review_status)


def fetch_invoices(conn: sqlite3.Connection, contract_id: str | None = None) -> tuple[Invoice, ...]:
    """Lista cobranças armazenadas, opcionalmente filtradas por contrato."""
    return list_invoices(conn, contract_id=contract_id)


def fetch_audits(
    conn: sqlite3.Connection, invoice_id: str | None = None
) -> tuple[StoredAudit, ...]:
    """Lista auditorias salvas com snapshot das regras da época."""
    return list_audits(conn, invoice_id=invoice_id)


def fetch_reviews(
    conn: sqlite3.Connection,
    *,
    target_type: ReviewTargetType | None = None,
    target_id: str | None = None,
) -> tuple[HumanReview, ...]:
    """Lista decisões humanas registradas na trilha de auditoria."""
    return list_reviews(conn, target_type=target_type, target_id=target_id)


def fetch_amendments(
    conn: sqlite3.Connection, contract_id: str | None = None
) -> tuple[Amendment, ...]:
    """Lista aditivos cadastrados, opcionalmente filtrados para um contrato."""
    all_amendments = list_amendments(conn)
    if contract_id is None:
        return all_amendments
    return _contract_amendments_for_context(
        all_amendments,
        list_rules(conn, contract_id),
        list_documents(conn, contract_id),
    )


def fetch_payment_orders(
    conn: sqlite3.Connection, contract_id: str | None = None
) -> tuple[PaymentOrder, ...]:
    """Lista ordens de pagamento cadastradas, opcionalmente filtradas por contrato."""
    return list_payment_orders(conn, contract_id=contract_id)


def fetch_tax_certificates(
    conn: sqlite3.Connection, contract_id: str | None = None
) -> tuple[TaxCertificate, ...]:
    """Lista certidões de regularidade, opcionalmente filtradas por contrato."""
    return list_tax_certificates(conn, contract_id=contract_id)


def fetch_financial_summaries(
    conn: sqlite3.Connection, contract_id: str | None = None
) -> tuple[DossierFinancialSummary, ...]:
    """Lista quadros de resumo financeiro extraídos de dossiês."""
    return list_financial_summaries(conn, contract_id=contract_id)


AUTO_APPROVE_USER = "sistema.auto"
AUTO_APPROVE_CONFIDENCE_THRESHOLD = Decimal("0.85")


def _auto_approve_high_confidence_rules(
    conn: sqlite3.Connection,
    rules: Sequence[ContractRule],
    *,
    threshold: Decimal = AUTO_APPROVE_CONFIDENCE_THRESHOLD,
    reviewed_at: datetime,
) -> tuple[tuple[ContractRule, ...], int]:
    """Confirma automaticamente regras extraídas com confiança acima do limiar configurado."""
    updated: list[ContractRule] = []
    auto_count = 0
    for rule in rules:
        if (
            rule.review_status is ReviewStatus.PENDING_REVIEW
            and rule.verification_score is not None
            and rule.verification_score > threshold
        ):
            pct = int(rule.verification_score * Decimal("100"))
            rev = review_rule(
                conn,
                rule.id,
                user=AUTO_APPROVE_USER,
                decision=ReviewDecision.CONFIRM,
                justification=(
                    f"Aprovada automaticamente (confiança {pct}% > 85%); "
                    "disponível para revisão ou alteração posterior."
                ),
                reviewed_at=reviewed_at,
            )
            updated.append(rev.rule)
            auto_count += 1
        else:
            updated.append(rule)
    return tuple(updated), auto_count


def sync_contract_dossier_data(
    conn: sqlite3.Connection,
    contract_id: str,
    *,
    auto_approve_above_threshold: bool = False,
) -> None:
    """
    Sincroniza de forma idempotente metadados bancários, retenções de NFs, glosas, OPs,
    certidões, tabelas de itens/postos e resumo financeiro dos documentos recebidos.
    """
    contract = get_contract(conn, contract_id)
    if contract is None:
        return

    docs = list_documents(conn, contract_id)
    contract_docs = sorted(
        (
            doc
            for doc in docs
            if doc.document_type in (DocumentType.CONTRACT, DocumentType.AMENDMENT)
            and doc.read_quality is ReadQuality.OK
            and doc.extracted_text.strip()
        ),
        key=lambda d: (0 if d.document_type is DocumentType.CONTRACT else 1, d.received_at, d.id),
    )
    if not contract_docs:
        return

    existing_rules = [
        r for r in list_rules(conn, contract_id) if r.review_status is not ReviewStatus.REJECTED
    ]
    existing_rule_by_sig: dict[tuple[RuleType, str, date | None, Decimal | None], ContractRule] = {}
    for r in existing_rules:
        sig = (
            r.rule_type,
            normalize_item_key(r.item_name) if r.rule_type is RuleType.ITEM_PRICE else "",
            r.validity.start,
            r.value,
        )
        existing_rule_by_sig.setdefault(sig, r)
    existing_rule_sigs: set[tuple[RuleType, str, date | None, Decimal | None]] = set(
        existing_rule_by_sig
    )
    all_contract_amendments = list_amendments(conn)
    existing_singleton_types = {
        r.rule_type
        for r in existing_rules
        if r.rule_type
        in (
            RuleType.INSS_RETENTION,
            RuleType.ISS_RETENTION,
            RuleType.APPROVAL_LIMIT,
            RuleType.PAYMENT_TERMS,
            RuleType.ADJUSTMENT,
        )
    }
    existing_invoices = list(list_invoices(conn, contract_id))
    inv_by_number = {inv.number.strip().casefold(): inv for inv in existing_invoices}

    for doc in contract_docs:
        extracted = extract_dossier_artifacts(
            doc.extracted_text,
            contract_id=contract_id,
            source_document_id=doc.id,
        )
        if doc.document_type is DocumentType.CONTRACT:
            meta = extracted.contract_metadata
            fallback_validity = (
                _extract_contract_validity(doc.extracted_text)
                if (meta.validity_start is None and meta.validity_end is None)
                else ValidityPeriod()
            )
            contract_updates: dict[str, object] = {}
            if contract.legal_nature is None and meta.legal_nature is not None:
                contract_updates["legal_nature"] = meta.legal_nature
            if contract.bank_code is None and meta.bank_code is not None:
                contract_updates["bank_code"] = meta.bank_code
            if contract.bank_agency is None and meta.bank_agency is not None:
                contract_updates["bank_agency"] = meta.bank_agency
            if contract.bank_account is None and meta.bank_account is not None:
                contract_updates["bank_account"] = meta.bank_account
            if contract.validity.start is None and contract.validity.end is None:
                cand_start = meta.validity_start or fallback_validity.start
                cand_end = meta.validity_end or fallback_validity.end
                if cand_start is not None or cand_end is not None:
                    if cand_start is not None and cand_end is not None and cand_start > cand_end:
                        cand_start, cand_end = None, None
                    if cand_start is not None or cand_end is not None:
                        contract_updates["validity"] = ValidityPeriod(
                            start=cand_start, end=cand_end
                        )
            if contract_updates:
                contract = contract.model_copy(update=contract_updates)
                save_contract(conn, contract)

        for key, patch in extracted.invoice_patches.items():
            inv = inv_by_number.get(key)
            if inv is None:
                continue
            inv_updates: dict[str, object] = {}
            if inv.attestation_date is None and patch.attestation_date is not None:
                inv_updates["attestation_date"] = patch.attestation_date
            if inv.inss_withheld is None and patch.inss_withheld is not None:
                inv_updates["inss_withheld"] = patch.inss_withheld
            if inv.iss_withheld is None and patch.iss_withheld is not None:
                inv_updates["iss_withheld"] = patch.iss_withheld
            if inv.net_total is None and patch.net_total is not None:
                inv_updates["net_total"] = patch.net_total
            if inv.service_description is None and patch.service_description is not None:
                inv_updates["service_description"] = patch.service_description
            if inv.supplier_legal_nature is None and patch.supplier_legal_nature is not None:
                inv_updates["supplier_legal_nature"] = patch.supplier_legal_nature
            if inv.raw_supplier_cnpj is None and patch.raw_supplier_cnpj is not None:
                inv_updates["raw_supplier_cnpj"] = patch.raw_supplier_cnpj
            if patch.supplier and patch.supplier != inv.supplier:
                # Preserva razão social divergente real da nota (ex.: Eireli na NF 1044).
                inv_updates["supplier"] = patch.supplier
            if inv_updates:
                updated_inv = inv.model_copy(update=inv_updates)
                save_invoice(conn, updated_inv)
                inv_by_number[key] = updated_inv

        if extracted.expected_glosas_by_period:
            for key, inv in list(inv_by_number.items()):
                expected_glosa = extracted.expected_glosas_by_period.get(str(inv.reference_period))
                if expected_glosa is not None and inv.expected_glosa is None:
                    updated_inv = inv.model_copy(update={"expected_glosa": expected_glosa})
                    save_invoice(conn, updated_inv)
                    inv_by_number[key] = updated_inv

        for op in extracted.payment_orders:
            matched_inv = inv_by_number.get(op.invoice_number.strip().casefold())
            linked_op = (
                op.model_copy(update={"invoice_id": matched_inv.id})
                if matched_inv is not None and op.invoice_id is None
                else op
            )
            save_payment_order(conn, linked_op)

        for cert in extracted.tax_certificates:
            save_tax_certificate(conn, cert)

        if extracted.financial_summary is not None:
            save_financial_summary(conn, extracted.financial_summary)

        if has_supplemental_contract_rules(doc.extracted_text) or has_structured_financial_sections(
            doc.extracted_text
        ):
            newly_saved_supp: list[ContractRule] = []
            for supp_rule in extracted.supplemental_rules:
                supp_key = (
                    supp_rule.rule_type,
                    normalize_item_key(supp_rule.item_name)
                    if supp_rule.rule_type is RuleType.ITEM_PRICE
                    else "",
                    supp_rule.validity.start,
                    supp_rule.value,
                )
                if supp_rule.rule_type in existing_singleton_types:
                    continue
                if supp_key in existing_rule_sigs:
                    existing_r = existing_rule_by_sig.get(supp_key)
                    if (
                        existing_r is not None
                        and supp_rule.rule_type is RuleType.ITEM_PRICE
                        and supp_rule.quantity is not None
                        and existing_r.quantity != supp_rule.quantity
                    ):
                        revs = list_reviews(
                            conn,
                            target_type=ReviewTargetType.RULE,
                            target_id=existing_r.id,
                        )
                        human_locked = existing_r.review_status.is_trusted and any(
                            rv.user != AUTO_APPROVE_USER for rv in revs
                        )
                        if not human_locked:
                            reconciled = existing_r.model_copy(
                                update={
                                    "quantity": supp_rule.quantity,
                                    "unit": existing_r.unit or supp_rule.unit,
                                    "quantity_scope": (
                                        existing_r.quantity_scope or supp_rule.quantity_scope
                                    ),
                                }
                            )
                            save_rule(conn, reconciled)
                            existing_rule_by_sig[supp_key] = reconciled
                            for am in all_contract_amendments:
                                if am.new_rule is not None and am.new_rule.id == reconciled.id:
                                    save_amendment(
                                        conn,
                                        am.model_copy(update={"new_rule": reconciled}),
                                    )
                    continue
                # Se for regra base sem start explícito nas cláusulas, herda start do contrato.
                effective_supp = supp_rule
                if (
                    doc.document_type is DocumentType.CONTRACT
                    and supp_rule.validity.start is None
                    and contract.validity.start is not None
                ):
                    effective_supp = supp_rule.model_copy(
                        update={
                            "validity": ValidityPeriod(
                                start=contract.validity.start,
                                end=supp_rule.validity.end or contract.validity.end,
                            )
                        }
                    )
                save_rule(conn, effective_supp)
                existing_rule_sigs.add(supp_key)
                if effective_supp.rule_type in (
                    RuleType.INSS_RETENTION,
                    RuleType.ISS_RETENTION,
                    RuleType.APPROVAL_LIMIT,
                    RuleType.PAYMENT_TERMS,
                    RuleType.ADJUSTMENT,
                ):
                    existing_singleton_types.add(effective_supp.rule_type)
                newly_saved_supp.append(effective_supp)

            if auto_approve_above_threshold and newly_saved_supp:
                approved_supp, _ = _auto_approve_high_confidence_rules(
                    conn,
                    newly_saved_supp,
                    reviewed_at=datetime.now(UTC),
                )
            else:
                approved_supp = tuple(newly_saved_supp)

            if doc.document_type is DocumentType.AMENDMENT and approved_supp:
                _auto_link_extracted_amendments(conn, contract_id, doc, approved_supp)

        if (
            auto_approve_above_threshold
            and doc.document_type is DocumentType.AMENDMENT
            and not doc.reviewed
        ):
            doc_rules = [
                r
                for r in list_rules(conn, contract_id)
                if r.evidence.source_document_id == doc.id
                and r.review_status is not ReviewStatus.REJECTED
            ]
            if doc_rules and all(r.review_status.is_trusted for r in doc_rules):
                mark_amendment_reviewed(
                    conn,
                    doc.id,
                    user=AUTO_APPROVE_USER,
                    justification=(
                        "Aditivo revisado automaticamente: todas as regras extraídas têm "
                        "confiança superior a 85%."
                    ),
                    reviewed_at=datetime.now(UTC),
                )


def fetch_payment_cycle_audit(
    conn: sqlite3.Connection, contract_id: str | None = None
) -> PaymentCycleAuditResult:
    """Sincroniza e executa a auditoria determinística de OPs, certidões e resumos."""
    if contract_id is not None:
        sync_contract_dossier_data(conn, contract_id)
        contract = get_contract(conn, contract_id)
        if contract is None:
            return PaymentCycleAuditResult()
        all_rules = list_rules(conn, contract_id)
        active_rules = tuple(r for r in all_rules if r.review_status is not ReviewStatus.REJECTED)
        return audit_payment_cycle(
            contract,
            rules=active_rules,
            invoices=list_invoices(conn, contract_id),
            payment_orders=list_payment_orders(conn, contract_id),
            certificates=list_tax_certificates(conn, contract_id),
            financial_summaries=list_financial_summaries(conn, contract_id),
        )

    contracts = list_contracts(conn)
    if not contracts:
        return PaymentCycleAuditResult()

    all_findings: list[PaymentFinding] = []
    merged_checks: dict[str, tuple[Check, ...]] = {}
    merged_dup_pairs: dict[str, tuple[str, ...]] = {}
    total_ops = 0
    total_certs = 0
    total_net = Decimal("0.00")

    for contract in contracts:
        part = fetch_payment_cycle_audit(conn, contract.id)
        total_ops += part.payment_orders_count
        total_certs += part.certificates_count
        total_net += part.total_net_paid
        all_findings.extend(part.findings)
        merged_checks.update(part.checks_by_invoice_number)
        merged_dup_pairs.update(part.duplicate_op_pairs)

    return PaymentCycleAuditResult(
        payment_orders_count=total_ops,
        certificates_count=total_certs,
        total_net_paid=total_net,
        checks_by_invoice_number=merged_checks,
        findings=tuple(all_findings),
        duplicate_op_pairs=merged_dup_pairs,
    )


def process_contract_document(
    conn: sqlite3.Connection,
    *,
    contract_id: str,
    document_id: str,
    file_name: str,
    content: bytes | Path,
    client: LLMClient,
    document_type: DocumentType = DocumentType.CONTRACT,
    received_at: datetime | None = None,
    confidence_threshold: Decimal = DEFAULT_CONFIDENCE_THRESHOLD,
    auto_approve_above_threshold: bool = False,
) -> DocumentProcessingResult:
    """Lê PDF ou DOCX de contrato/aditivo, grava SHA-256, extrai e persiste regras."""
    if get_contract(conn, contract_id) is None:
        raise ValueError(f"contrato {contract_id} não encontrado")
    if document_type not in (DocumentType.CONTRACT, DocumentType.AMENDMENT):
        raise ValueError(
            "process_contract_document aceita apenas DocumentType.CONTRACT ou AMENDMENT"
        )

    suffix = Path(file_name).suffix.casefold()
    is_amendment = document_type is DocumentType.AMENDMENT
    timestamp = received_at if received_at is not None else datetime.now(UTC)
    pdf_result: PdfReadResult | None = None
    docx_result: DocxReadResult | None = None

    if suffix == ".pdf":
        pdf_result = read_pdf(content)
        if pdf_result.read_quality is ReadQuality.NO_TEXT:
            raw_pdf = content.read_bytes() if isinstance(content, Path) else content
            ocr_parsed = _try_ocr_pdf_with_client(file_name, raw_pdf, client)
            if ocr_parsed is not None and ocr_parsed.pdf_result is not None:
                pdf_result = ocr_parsed.pdf_result
        read_warning = pdf_result.warning
        read_quality = pdf_result.read_quality
        extracted_text = pdf_result.full_text
    elif suffix == ".docx":
        docx_result = read_docx(content)
        read_warning = docx_result.warning
        read_quality = docx_result.read_quality
        extracted_text = docx_result.full_text
    else:
        raise ValueError(f"formato não suportado para documento contratual: {file_name}")

    document = receive_document(
        document_id=document_id,
        contract_id=contract_id,
        document_type=document_type,
        file_name=file_name,
        content=content,
        received_at=timestamp,
        read_quality=read_quality,
        extracted_text=extracted_text,
        reviewed=False,
        conn=conn,
    )

    if pdf_result is not None:
        jurista_input = JuristaInput.from_pdf(
            document.id,
            contract_id,
            pdf_result,
            file_hash=document.file_hash,
            is_amendment=is_amendment,
        )
    elif docx_result is not None:
        jurista_input = JuristaInput.from_docx(
            document.id,
            contract_id,
            docx_result,
            file_hash=document.file_hash,
            is_amendment=is_amendment,
        )
    else:
        raise ValueError(f"formato não suportado para documento contratual: {file_name}")

    jurista_result = run_jurista_agent(
        jurista_input,
        client,
        confidence_threshold=confidence_threshold,
        extracted_at=timestamp,
    )
    final_rules = jurista_result.rules
    extra_warnings: list[str] = []
    for rule in final_rules:
        save_rule(conn, rule)

    if auto_approve_above_threshold and final_rules:
        final_rules, auto_count = _auto_approve_high_confidence_rules(
            conn,
            final_rules,
            reviewed_at=timestamp,
        )
        if auto_count > 0:
            extra_warnings.append(
                f"{auto_count} regra(s) aprovada(s) automaticamente (confiança > 85%); "
                "caso queira alterar, use Corrigir ou Rejeitar em Regras e Vigência."
            )
        if is_amendment and all(
            rule.review_status is ReviewStatus.CONFIRMED for rule in final_rules
        ):
            doc_rev = mark_amendment_reviewed(
                conn,
                document.id,
                user=AUTO_APPROVE_USER,
                justification=(
                    "Aditivo revisado automaticamente: todas as regras extraídas têm "
                    "confiança superior a 85%."
                ),
                reviewed_at=timestamp,
            )
            document = doc_rev.document

    warnings = _unique_messages((read_warning,), jurista_result.warnings, extra_warnings)
    return DocumentProcessingResult(
        document=document,
        rules=final_rules,
        anchoring_results=jurista_result.anchoring_results,
        warnings=warnings,
        schema_valid=jurista_result.schema_valid,
        metadata=jurista_result.metadata,
    )


def review_rule(
    conn: sqlite3.Connection,
    rule_id: str,
    *,
    user: str,
    decision: ReviewDecision,
    justification: str,
    reviewed_at: datetime | None = None,
    corrected_rule: ContractRule | None = None,
) -> RuleReviewResult:
    """Registra decisão humana sobre regra (confirmar, corrigir ou rejeitar) e devolve o estado."""
    timestamp = reviewed_at if reviewed_at is not None else datetime.now(UTC)
    review = record_rule_review(
        conn,
        rule_id,
        user=user,
        decision=decision,
        justification=justification,
        reviewed_at=timestamp,
        corrected_rule=corrected_rule,
    )
    updated = get_rule(conn, rule_id)
    if updated is None:
        raise ValueError(f"regra {rule_id} não encontrada após revisão")
    return RuleReviewResult(rule=updated, review=review)


def register_amendment(conn: sqlite3.Connection, amendment: Amendment) -> Amendment:
    """Valida referências e grava um aditivo contratual na base de dados."""
    if get_rule(conn, amendment.affected_rule_id) is None:
        raise ValueError(f"regra afetada {amendment.affected_rule_id} não encontrada")
    if get_document(conn, amendment.source_document_id) is None:
        raise ValueError(f"documento do aditivo {amendment.source_document_id} não encontrado")
    save_amendment(conn, amendment)
    return amendment


def review_document(
    conn: sqlite3.Connection,
    document_id: str,
    *,
    user: str,
    decision: ReviewDecision,
    justification: str,
    reviewed_at: datetime | None = None,
) -> DocumentReviewResult:
    """Registra decisão humana de revisão sobre um documento e devolve o registro atualizado."""
    timestamp = reviewed_at if reviewed_at is not None else datetime.now(UTC)
    review = record_document_review(
        conn,
        document_id,
        user=user,
        decision=decision,
        justification=justification,
        reviewed_at=timestamp,
    )
    updated = get_document(conn, document_id)
    if updated is None:
        raise ValueError(f"documento {document_id} não encontrado após revisão")
    return DocumentReviewResult(document=updated, review=review)


def mark_amendment_reviewed(
    conn: sqlite3.Connection,
    document_id: str,
    *,
    user: str,
    justification: str,
    decision: ReviewDecision = ReviewDecision.CONFIRM,
    reviewed_at: datetime | None = None,
) -> DocumentReviewResult:
    """Marca um documento de aditivo como revisado (ou rejeitado) na trilha de decisões."""
    document = get_document(conn, document_id)
    if document is None:
        raise ValueError(f"documento {document_id} não encontrado")
    if document.document_type is not DocumentType.AMENDMENT:
        raise ValueError(f"documento {document_id} não é um aditivo ({document.document_type})")
    return review_document(
        conn,
        document_id,
        user=user,
        decision=decision,
        justification=justification,
        reviewed_at=reviewed_at,
    )


def _contract_id_keys(raw_id: str | None) -> set[str]:
    """Gera chaves normalizadas para casar Ata 007/2025 <-> ARP-007/2025 <-> 007/2025."""
    if not raw_id:
        return set()
    cleaned = raw_id.strip().upper()
    keys = {cleaned}
    stripped = re.sub(
        r"^(?:ATA\s+DE\s+REGISTRO\s+DE\s+PRE[ÇC]OS|ATA|ARP|CONTRATO(?:\s+DE\s+LOCA[ÇC][ÃA]O)?|"
        r"TERMO\s+DE\s+CONTRATO|LOC)[-\s#:Nº°.]*",
        "",
        cleaned,
    ).strip(" -.:#")
    if stripped:
        keys.add(stripped)
        num_year = re.search(r"([A-Z]*-?\d+/\d{4})$", stripped)
        if num_year:
            keys.add(num_year.group(1))
    return keys


def _match_contract_by_id_or_hint(
    contracts: Sequence[Contract], raw_id: str | None
) -> Contract | None:
    if not raw_id:
        return None
    exact = next((item for item in contracts if item.id == raw_id), None)
    if exact is not None:
        return exact
    target_keys = _contract_id_keys(raw_id)
    matches = [item for item in contracts if _contract_id_keys(item.id) & target_keys]
    return matches[0] if len(matches) == 1 else None


def resolve_contract_link(
    contracts: Sequence[Contract], invoice: Invoice
) -> tuple[str | None, str | None]:
    """
    Resolve o vínculo da cobrança ao contrato por nº informado, referência na descrição
    (ex.: Ata de Registro de Preços nº 007/2025 / Contrato nº ...) ou CNPJ único cadastrado.
    Em CNPJ ambíguo (> 1 contrato) ou ausente, mantém sem vínculo com aviso explícito.
    """
    if invoice.contract_id is not None:
        matched = _match_contract_by_id_or_hint(contracts, invoice.contract_id)
        if matched is None:
            return (
                None,
                f"Contrato {invoice.contract_id} informado na cobrança {invoice.number} "
                "não está cadastrado; cobrança mantida sem vínculo.",
            )
        if matched.supplier_cnpj != invoice.supplier_cnpj:
            return (
                matched.id,
                f"CNPJ da cobrança {invoice.number} diverge do contrato {matched.id}.",
            )
        return matched.id, None

    if invoice.service_description:
        desc_hint = _extract_contract_id_hint(invoice.service_description, "")
        if desc_hint is not None:
            matched_desc = _match_contract_by_id_or_hint(contracts, desc_hint)
            if matched_desc is not None:
                if matched_desc.supplier_cnpj != invoice.supplier_cnpj:
                    return (
                        matched_desc.id,
                        f"CNPJ da cobrança {invoice.number} diverge do contrato {matched_desc.id}.",
                    )
                return matched_desc.id, None

    matching_cnpj = [item for item in contracts if item.supplier_cnpj == invoice.supplier_cnpj]
    if len(matching_cnpj) == 1:
        return matching_cnpj[0].id, None
    if len(matching_cnpj) > 1:
        return (
            None,
            f"Mais de um contrato cadastrado para o CNPJ {invoice.supplier_cnpj}; cobrança "
            f"{invoice.number} mantida sem vínculo automático.",
        )
    return (
        None,
        f"Nenhum contrato cadastrado para o CNPJ {invoice.supplier_cnpj}; cobrança "
        f"{invoice.number} mantida sem vínculo.",
    )


def _parse_xml_invoice(content: bytes | Path, *, invoice_id: str | None) -> Invoice:
    try:
        return parse_nfe(content, invoice_id=invoice_id)
    except UnsafeXmlError:
        raise
    except ValueError as nfe_exc:
        try:
            return parse_nfse(content, invoice_id=invoice_id)
        except UnsafeXmlError:
            raise
        except ValueError as nfse_exc:
            raise ValueError(
                f"XML não reconhecido como NF-e nem NFS-e: {nfe_exc}; {nfse_exc}"
            ) from nfse_exc


def import_billing_document(
    conn: sqlite3.Connection,
    *,
    document_id: str,
    file_name: str,
    content: bytes | Path,
    client: LLMClient | None = None,
    received_at: datetime | None = None,
    invoice_id: str | None = None,
) -> InvoiceImportResult:
    """
    Recebe arquivo de cobrança (XML, CSV, XLSX, PDF ou DOCX), grava SHA-256, extrai cobranças,
    resolve vínculo por nº do contrato ou CNPJ único e persiste no banco.
    """
    suffix = Path(file_name).suffix.casefold()
    timestamp = received_at if received_at is not None else datetime.now(UTC)
    warnings_list: list[str] = []

    if suffix == ".xml":
        parsed_invoices: tuple[Invoice, ...] = (_parse_xml_invoice(content, invoice_id=invoice_id),)
        document = receive_document(
            document_id=document_id,
            document_type=DocumentType.NFE_XML,
            file_name=file_name,
            content=content,
            received_at=timestamp,
            read_quality=ReadQuality.OK,
            extracted_text=f"Cobrança {parsed_invoices[0].number} importada de {file_name}.",
            reviewed=False,
            conn=conn,
        )
    elif suffix in (".csv", ".xlsx"):
        parsed_invoices = (
            import_invoices_csv(content) if suffix == ".csv" else import_invoices_xlsx(content)
        )
        numbers = ", ".join(inv.number for inv in parsed_invoices) or "nenhuma"
        document = receive_document(
            document_id=document_id,
            document_type=DocumentType.SPREADSHEET,
            file_name=file_name,
            content=content,
            received_at=timestamp,
            read_quality=ReadQuality.OK,
            extracted_text=f"Planilha {file_name} com cobrança(s): {numbers}.",
            reviewed=False,
            conn=conn,
        )
    elif suffix in (".pdf", ".docx"):
        if client is None:
            raise ValueError("importação de cobrança em PDF/DOCX exige LLMClient configurado")
        pdf_result: PdfReadResult | None = None
        docx_result: DocxReadResult | None = None
        if suffix == ".pdf":
            pdf_result = read_pdf(content)
            if pdf_result.read_quality is ReadQuality.NO_TEXT:
                raw_pdf = content.read_bytes() if isinstance(content, Path) else content
                ocr_parsed = _try_ocr_pdf_with_client(file_name, raw_pdf, client)
                if ocr_parsed is not None and ocr_parsed.pdf_result is not None:
                    pdf_result = ocr_parsed.pdf_result
            read_warning = pdf_result.warning
            read_quality = pdf_result.read_quality
            extracted_text = pdf_result.full_text
        else:
            docx_result = read_docx(content)
            read_warning = docx_result.warning
            read_quality = docx_result.read_quality
            extracted_text = docx_result.full_text

        if read_warning is not None:
            warnings_list.append(read_warning)
        document = receive_document(
            document_id=document_id,
            document_type=DocumentType.INVOICE,
            file_name=file_name,
            content=content,
            received_at=timestamp,
            read_quality=read_quality,
            extracted_text=extracted_text,
            reviewed=False,
            conn=conn,
        )
        if pdf_result is not None:
            fiscal_input = FiscalPdfInput.from_pdf(
                document.id, pdf_result, file_hash=document.file_hash
            )
        elif docx_result is not None:
            fiscal_input = FiscalPdfInput.from_docx(
                document.id, docx_result, file_hash=document.file_hash
            )
        else:
            raise ValueError(f"formato não suportado para cobrança: {file_name}")
        fiscal_result = run_fiscal_pdf_agent(
            fiscal_input,
            client,
            extracted_at=timestamp,
        )
        warnings_list.extend(fiscal_result.warnings)
        parsed_invoices = fiscal_result.invoices
    else:
        raise ValueError(f"formato não suportado para cobrança: {file_name}")

    for raw_invoice in parsed_invoices:
        if raw_invoice.contract_id is not None:
            target_contract = get_contract(conn, raw_invoice.contract_id)
            if (
                target_contract is not None
                and target_contract.supplier_cnpj == _PROVISIONAL_CNPJ
                and raw_invoice.supplier_cnpj != _PROVISIONAL_CNPJ
            ):
                _maybe_upgrade_provisional_contract(
                    conn,
                    target_contract,
                    detected_cnpj=raw_invoice.supplier_cnpj,
                    company_name=None,
                    supplier_name=raw_invoice.supplier,
                    batch_warnings=warnings_list,
                )

    contracts = list_contracts(conn)
    saved_invoices: list[Invoice] = []
    unlinked_ids: list[str] = []

    for raw_invoice in parsed_invoices:
        resolved_contract_id, link_warning = resolve_contract_link(contracts, raw_invoice)
        if link_warning is not None:
            warnings_list.append(link_warning)
        linked_invoice = raw_invoice.model_copy(update={"contract_id": resolved_contract_id})
        save_invoice(conn, linked_invoice)
        saved_invoices.append(linked_invoice)
        if resolved_contract_id is None:
            unlinked_ids.append(linked_invoice.id)

    distinct_contracts = {inv.contract_id for inv in saved_invoices if inv.contract_id is not None}
    if saved_invoices:
        marker = f"\n[invoice_ids:{','.join(inv.id for inv in saved_invoices)}]"
        updates: dict[str, object] = {"extracted_text": f"{document.extracted_text}{marker}"}
        if len(distinct_contracts) == 1:
            updates["contract_id"] = next(iter(distinct_contracts))
        document = document.model_copy(update=updates)
        save_document(conn, document)

    return InvoiceImportResult(
        document=document,
        invoices=tuple(saved_invoices),
        unlinked_invoice_ids=tuple(unlinked_ids),
        warnings=_unique_messages(warnings_list),
    )


def _contract_amendments_for_context(
    all_amendments: Sequence[Amendment],
    rules: Sequence[ContractRule],
    documents: Sequence[Document],
) -> tuple[Amendment, ...]:
    rule_ids = {rule.id for rule in rules}
    doc_ids = {doc.id for doc in documents}
    return tuple(
        item
        for item in all_amendments
        if item.affected_rule_id in rule_ids
        or item.source_document_id in doc_ids
        or (item.new_rule is not None and item.new_rule.id in rule_ids)
    )


def audit_stored_invoice(
    conn: sqlite3.Connection,
    invoice_id: str,
    *,
    tradutor_client: LLMClient | None = None,
    report_client: LLMClient | None = None,
    similarity_threshold: Decimal = DEFAULT_CONFIDENCE_THRESHOLD,
    links_approved_by_human: bool = False,
) -> InvoiceAuditOutcome:
    """Executa o fluxo completo de auditoria para uma cobrança salva e congela o snapshot."""
    invoice = get_invoice(conn, invoice_id)
    if invoice is None:
        raise ValueError(f"cobrança {invoice_id} não encontrada")

    if invoice.contract_id is not None:
        sync_contract_dossier_data(conn, invoice.contract_id)
        refreshed_invoice = get_invoice(conn, invoice_id)
        if refreshed_invoice is not None:
            invoice = refreshed_invoice

    contract: Contract | None = None
    base_rules: tuple[ContractRule, ...] = ()
    contract_amendments: tuple[Amendment, ...] = ()
    documents: tuple[Document, ...] = ()
    other_invoices: tuple[Invoice, ...] = ()
    preview_rules: tuple[ContractRule, ...] = ()
    all_rules: tuple[ContractRule, ...] = ()

    if invoice.contract_id is not None:
        contract = get_contract(conn, invoice.contract_id)
        if contract is not None:
            all_rules = list_rules(conn, contract.id)
            rejected_ids = {
                rule.id for rule in all_rules if rule.review_status is ReviewStatus.REJECTED
            }
            documents = list_documents(conn, contract.id)
            other_invoices = tuple(
                item for item in list_invoices(conn, contract.id) if item.id != invoice.id
            )
            contract_amendments = tuple(
                item
                for item in _contract_amendments_for_context(
                    list_amendments(conn), all_rules, documents
                )
                if item.affected_rule_id not in rejected_ids
                and (item.new_rule is None or item.new_rule.id not in rejected_ids)
            )
            amendment_new_rule_ids = {
                item.new_rule.id for item in contract_amendments if item.new_rule is not None
            }
            base_rules = tuple(
                rule
                for rule in all_rules
                if rule.id not in rejected_ids and rule.id not in amendment_new_rule_ids
            )
            preview_rules = apply_amendments(base_rules, contract_amendments).rules

    state = InvoiceWorkflowState(
        invoice=invoice,
        contract=contract,
        consolidated_rules=preview_rules,
        amendments=contract_amendments,
        documents=documents,
        other_invoices=other_invoices,
        links_approved_by_human=links_approved_by_human,
    )
    state = fetch_invoice_rules_node(state)
    if tradutor_client is not None:
        state = link_invoice_items_node(
            state,
            tradutor_client,
            similarity_threshold=similarity_threshold,
        )

    def _motor_with_base_rules(
        target_invoice: Invoice,
        target_contract: Contract | None,
        _preview: Sequence[ContractRule],
        target_amendments: Sequence[Amendment],
        target_documents: Sequence[Document],
        target_other_invoices: Sequence[Invoice],
    ) -> AuditResult:
        return audit_invoice(
            target_invoice,
            contract=target_contract,
            rules=base_rules,
            amendments=target_amendments,
            documents=target_documents,
            other_invoices=target_other_invoices,
        )

    state = run_invoice_motor_node(state, _motor_with_base_rules)
    state = build_invoice_report_node(state, report_client=report_client)
    if state.audit_result is None or state.report is None:
        raise RuntimeError(
            f"auditoria da cobrança {invoice_id} não produziu resultado ou relatório"
        )

    snapshot_candidates: dict[str, ContractRule] = {rule.id: rule for rule in preview_rules}
    for rule in all_rules:
        snapshot_candidates.setdefault(rule.id, rule)

    snapshot_list: list[ContractRule] = []
    seen_rule_ids: set[str] = set()
    for applied in state.audit_result.applied_rules:
        if applied.rule_id in seen_rule_ids:
            continue
        matched_rule = snapshot_candidates.get(applied.rule_id)
        if matched_rule is not None:
            seen_rule_ids.add(applied.rule_id)
            snapshot_list.append(matched_rule)

    stored = save_audit(conn, state.audit_result, rules_snapshot=tuple(snapshot_list))
    return InvoiceAuditOutcome(
        stored_audit=stored,
        report=state.report,
        translation_result=state.translation_result,
        awaiting_link_review=state.awaiting_link_review,
        warnings=_unique_messages(state.warnings),
    )


def record_audit_decision(
    conn: sqlite3.Connection,
    audit_id: str,
    *,
    user: str,
    decision: ReviewDecision,
    justification: str,
    reviewed_at: datetime | None = None,
    previous_status: ReviewStatus = ReviewStatus.PENDING_REVIEW,
) -> HumanReview:
    """Registra decisão humana sobre um resultado de auditoria salvo."""
    if get_audit(conn, audit_id) is None:
        raise ValueError(f"auditoria {audit_id} não encontrada")
    timestamp = reviewed_at if reviewed_at is not None else datetime.now(UTC)
    return record_audit_review(
        conn,
        audit_id,
        user=user,
        decision=decision,
        justification=justification,
        reviewed_at=timestamp,
        previous_status=previous_status,
    )


def register_human_decision(
    conn: sqlite3.Connection,
    *,
    target_type: ReviewTargetType,
    target_id: str,
    user: str,
    decision: ReviewDecision,
    justification: str,
    reviewed_at: datetime | None = None,
    corrected_rule: ContractRule | None = None,
    previous_status: ReviewStatus = ReviewStatus.PENDING_REVIEW,
) -> HumanReview:
    """Despacha o registro de decisão humana para regra, documento ou auditoria."""
    if target_type is ReviewTargetType.RULE:
        return review_rule(
            conn,
            target_id,
            user=user,
            decision=decision,
            justification=justification,
            reviewed_at=reviewed_at,
            corrected_rule=corrected_rule,
        ).review
    if target_type is ReviewTargetType.DOCUMENT:
        return review_document(
            conn,
            target_id,
            user=user,
            decision=decision,
            justification=justification,
            reviewed_at=reviewed_at,
        ).review
    if target_type is ReviewTargetType.AUDIT_RESULT:
        return record_audit_decision(
            conn,
            target_id,
            user=user,
            decision=decision,
            justification=justification,
            reviewed_at=reviewed_at,
            previous_status=previous_status,
        )
    raise ValueError(f"tipo de alvo de revisão não suportado: {target_type}")


def get_contract_timeline(conn: sqlite3.Connection, contract_id: str) -> TimelineResult:
    """Consolida regras não rejeitadas e aditivos do contrato para a linha do tempo."""
    if get_contract(conn, contract_id) is None:
        raise ValueError(f"contrato {contract_id} não encontrado")
    all_rules = list_rules(conn, contract_id)
    documents = list_documents(conn, contract_id)
    rejected_ids = {rule.id for rule in all_rules if rule.review_status is ReviewStatus.REJECTED}
    contract_amendments = tuple(
        item
        for item in _contract_amendments_for_context(list_amendments(conn), all_rules, documents)
        if item.affected_rule_id not in rejected_ids
        and (item.new_rule is None or item.new_rule.id not in rejected_ids)
    )
    amendment_new_rule_ids = {
        item.new_rule.id for item in contract_amendments if item.new_rule is not None
    }
    base_rules = tuple(
        rule
        for rule in all_rules
        if rule.id not in rejected_ids and rule.id not in amendment_new_rule_ids
    )
    return apply_amendments(base_rules, contract_amendments)


def get_rules_for_competence(
    conn: sqlite3.Connection, contract_id: str, period: ReferencePeriod
) -> CompetenceRulesResult:
    """Devolve, para cada tipo de regra, qual regra vigora na competência informada."""
    timeline = get_contract_timeline(conn, contract_id)
    selections: dict[RuleType, RuleSelection] = {}
    for rule_type in RuleType:
        typed_rules = tuple(rule for rule in timeline.rules if rule.rule_type is rule_type)
        selections[rule_type] = select_applicable_rule(typed_rules, period)
    return CompetenceRulesResult(
        contract_id=contract_id,
        period=period,
        timeline=timeline,
        selections=selections,
    )


def assign_invoice_contract(conn: sqlite3.Connection, invoice_id: str, contract_id: str) -> Invoice:
    """Associa manualmente uma cobrança sem vínculo ou ambígua ao contrato escolhido."""
    invoice = get_invoice(conn, invoice_id)
    if invoice is None:
        raise ValueError(f"cobrança {invoice_id} não encontrada")
    contract = get_contract(conn, contract_id)
    if contract is None:
        raise ValueError(f"contrato {contract_id} não encontrado")
    updated = invoice.model_copy(update={"contract_id": contract.id})
    save_invoice(conn, updated)
    return updated


def reset_all_data(db_path: Path) -> None:
    """Remove o banco SQLite em disco e recria o esquema limpo com as triggers."""
    raw = str(db_path)
    if raw == ":memory:" or raw.startswith("file:"):
        raise ValueError("reset_all_data exige caminho de arquivo SQLite em disco")
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    init_db(db_path).close()


def seed_synthetic_demo(
    conn: sqlite3.Connection,
    synthetic_dir: Path | None = None,
    *,
    reviewed_at: datetime | None = None,
) -> tuple[str, ...]:
    """Carrega o cenário sintético CT-2026-001 completo para demonstração offline."""
    if get_contract(conn, "CT-2026-001") is not None:
        raise ValueError(
            "O contrato sintético CT-2026-001 já está cadastrado; "
            "apague os dados antes de recarregar."
        )
    target_dir = (
        synthetic_dir
        if synthetic_dir is not None
        else Path(__file__).resolve().parents[2] / "data" / "sintetico"
    )
    if not target_dir.exists():
        raise FileNotFoundError(f"Pasta sintética não encontrada: {target_dir}")

    timestamp = reviewed_at if reviewed_at is not None else datetime.now(UTC)
    reviewer = "demo.sintetico"

    contract = Contract(
        id="CT-2026-001",
        company="Empresa Exemplo S.A.",
        supplier="Fornecedor Exemplo Ltda.",
        supplier_cnpj="11.222.333/0001-81",
        subject="Serviços de suporte técnico contínuo",
        validity=ValidityPeriod(start=date(2026, 1, 1), end=date(2026, 12, 31)),
        comparison_tolerance=Decimal("0.00"),
        status=ContractStatus.ACTIVE,
    )
    register_contract(conn, contract)

    base_client = FakeLLMClient(
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
                        clause="Cláusula 3ª",
                        literal_excerpt=(
                            "Cláusula 3ª: O valor mensal é de R$ 10.000,00 de 01/01/2026 até "
                            "31/12/2026."
                        ),
                    ),
                    ExtractedRuleCandidate(
                        rule_type=RuleType.FRANCHISE,
                        quantity=Decimal("40"),
                        unit="horas técnicas",
                        validity_start=date(2026, 1, 1),
                        relation=RuleRelation.CREATED,
                        page=3,
                        clause="Cláusula 4ª",
                        literal_excerpt=(
                            "Cláusula 4ª: A franquia mensal é de 40 horas técnicas desde "
                            "01/01/2026."
                        ),
                    ),
                    ExtractedRuleCandidate(
                        rule_type=RuleType.OVERAGE_PRICE,
                        new_value=Decimal("150.00"),
                        validity_start=date(2026, 1, 1),
                        relation=RuleRelation.CREATED,
                        page=3,
                        clause="Cláusula 5ª",
                        literal_excerpt=(
                            "Cláusula 5ª: A hora excedente custa R$ 150,00 desde 01/01/2026."
                        ),
                    ),
                    ExtractedRuleCandidate(
                        rule_type=RuleType.ADJUSTMENT,
                        adjustment_index="IPCA",
                        periodicity_months=12,
                        validity_start=date(2026, 1, 1),
                        relation=RuleRelation.CREATED,
                        page=3,
                        clause="Cláusula 6ª",
                        literal_excerpt=(
                            "Cláusula 6ª: Reajuste pelo índice IPCA a cada 12 meses desde "
                            "01/01/2026."
                        ),
                    ),
                )
            )
        ]
    )
    base_proc = process_contract_document(
        conn,
        contract_id=contract.id,
        document_id="doc-ct-2026-001-base",
        file_name="ct_2026_001_contrato_base.pdf",
        content=target_dir / "ct_2026_001_contrato_base.pdf",
        client=base_client,
        document_type=DocumentType.CONTRACT,
        received_at=timestamp,
    )
    for rule in base_proc.rules:
        review_rule(
            conn,
            rule.id,
            user=reviewer,
            decision=ReviewDecision.CONFIRM,
            justification="Regra do contrato base confirmada na carga sintética.",
            reviewed_at=timestamp,
        )
    review_document(
        conn,
        base_proc.document.id,
        user=reviewer,
        decision=ReviewDecision.CONFIRM,
        justification="Contrato base conferido na carga sintética.",
        reviewed_at=timestamp,
    )

    base_monthly_rule = base_proc.rules[0]

    add01_proc = process_contract_document(
        conn,
        contract_id=contract.id,
        document_id="doc-ct-2026-001-add-01",
        file_name="ct_2026_001_aditivo_01.docx",
        content=target_dir / "ct_2026_001_aditivo_01.docx",
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
                                "passa de R$ 10.000,00 para R$ 10.800,00 a partir de 01/05/2026."
                            ),
                        ),
                    )
                )
            ]
        ),
        document_type=DocumentType.AMENDMENT,
        received_at=timestamp,
    )
    add01_confirmed = review_rule(
        conn,
        add01_proc.rules[0].id,
        user=reviewer,
        decision=ReviewDecision.CONFIRM,
        justification="1º Aditivo confirmado na carga sintética.",
        reviewed_at=timestamp,
    ).rule
    register_amendment(
        conn,
        Amendment(
            id="amend-ct-2026-001-01",
            affected_rule_id=base_monthly_rule.id,
            new_rule=add01_confirmed,
            source_document_id=add01_proc.document.id,
            effective_date=date(2026, 5, 1),
            relation=RuleRelation.ALTERED,
        ),
    )
    mark_amendment_reviewed(
        conn,
        add01_proc.document.id,
        user=reviewer,
        justification="1º Aditivo vinculado e revisado na carga sintética.",
        reviewed_at=timestamp,
    )

    add02_proc = process_contract_document(
        conn,
        contract_id=contract.id,
        document_id="doc-ct-2026-001-add-02",
        file_name="ct_2026_001_aditivo_02.pdf",
        content=target_dir / "ct_2026_001_aditivo_02.pdf",
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
                            clause="Cláusula 2ª",
                            literal_excerpt=(
                                "Cláusula 2ª: Passa de R$ 10.800,00 para R$ 10.500,00 a partir "
                                "de 01/09/2026."
                            ),
                        ),
                    )
                )
            ]
        ),
        document_type=DocumentType.AMENDMENT,
        received_at=timestamp,
    )
    add02_confirmed = review_rule(
        conn,
        add02_proc.rules[0].id,
        user=reviewer,
        decision=ReviewDecision.CONFIRM,
        justification="2º Aditivo confirmado na carga sintética.",
        reviewed_at=timestamp,
    ).rule
    register_amendment(
        conn,
        Amendment(
            id="amend-ct-2026-001-02",
            affected_rule_id=add01_confirmed.id,
            new_rule=add02_confirmed,
            source_document_id=add02_proc.document.id,
            effective_date=date(2026, 9, 1),
            relation=RuleRelation.ALTERED,
        ),
    )
    mark_amendment_reviewed(
        conn,
        add02_proc.document.id,
        user=reviewer,
        justification="2º Aditivo vinculado e revisado na carga sintética.",
        reviewed_at=timestamp,
    )

    imported_ids: list[str] = []
    billing_files = (
        ("doc-inv-demo-101", "nfe_101_caso_b_regular.xml", "inv-demo-101"),
        ("doc-inv-demo-104", "nfe_104_caso_a_divergencia.xml", "inv-demo-104"),
        ("doc-inv-demo-109", "nfse_109_competencia_inferida.xml", "inv-demo-109"),
        ("doc-inv-demo-csv", "cobrancas_lote_ct_2026_001.csv", None),
    )
    for doc_id, file_name, invoice_id in billing_files:
        path = target_dir / file_name
        if not path.exists():
            continue
        imp = import_billing_document(
            conn,
            document_id=doc_id,
            file_name=file_name,
            content=path,
            received_at=timestamp,
            invoice_id=invoice_id,
        )
        imported_ids.extend(inv.id for inv in imp.invoices)

    return (contract.id, *imported_ids)


_CONTRACT_CT_RE = re.compile(r"\b(CT[-_]\d{4}[-_]\d{2,})\b", re.IGNORECASE)
_ATA_EXPLICIT_NUM_RE = re.compile(
    r"(?i)\b(?:ata\s+de\s+registro\s+de\s+pre[çc]os|arp|ata)\s*"
    r"(?:n[º°.]?|n[úu]mero|#)\s*([A-Z0-9][A-Z0-9./_-]{2,})"
)
_CONTRACT_EXPLICIT_NUM_RE = re.compile(
    r"(?i)\b(?:contrato(?:\s+de\s+loca[çc][ãa]o(?:\s+n[ãa]o\s+residencial)?)?|"
    r"termo\s+de\s+contrato|instrumento(?:\s+particular)?)\s+"
    r"(?:n[º°.]?|n[úu]mero|#)\s*([A-Z0-9][A-Z0-9./_-]{2,})"
)
_CONTRACT_CODE_RE = re.compile(r"(?i)\bcontrato\s+([A-Z]{1,6}[-_/][0-9][A-Z0-9./_-]*)")
_CNPJ_CANDIDATE_RE = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_CNPJ_ANY_TOKEN_RE = re.compile(
    r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?[0-9?X*]{2}(?![0-9A-Za-z?X*])",
    re.IGNORECASE,
)
_PREAMBLE_ROLE_END_RE = re.compile(
    r"(?is)doravante\s+(?:denominad[ao]\s+)?(?P<role>"
    r"CONTRATANTE|[ÓO]RG[ÃA]O\s+GERENCIADOR|LOCAT[ÁA]RI[AO]|TOMADOR(?:A)?|"
    r"CONCEDENTE|COMPRADOR(?:A)?|"
    r"CONTRATAD[AO]|DETENTOR(?:A)?|LOCADOR(?:A)?|"
    r"PRESTADOR(?:A)?(?:\s+DE\s+SERVI[ÇC]OS)?|FORNECEDOR(?:A)?|"
    r"CREDENCIAD[AO]|BENEFICI[ÁA]RI[AO])\b"
)
_SUPPLIER_ROLES = frozenset(
    {
        "CONTRATADA",
        "CONTRATADO",
        "DETENTORA",
        "DETENTOR",
        "LOCADORA",
        "LOCADOR",
        "PRESTADORA",
        "PRESTADOR",
        "PRESTADORA DE SERVICOS",
        "PRESTADOR DE SERVICOS",
        "FORNECEDORA",
        "FORNECEDOR",
        "CREDENCIADA",
        "CREDENCIADO",
        "BENEFICIARIA",
        "BENEFICIARIO",
    }
)
_SUPPLIER_CNPJ_RE = re.compile(
    r"(?is)(?:contratad[ao]|detentor[ao]?|locador[ao]?|fornecedor[ao]?|"
    r"prestador(?:a)?(?:\s+de\s+servi[çc]os)?)"
    r"(?:(?!\b(?:contratante|org[ãa]o\s+gerenciador|locat[áa]ri[ao]|tomador|cl[aá]usula)\b)[\s\S]){0,300}?"
    r"(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?[0-9?X*]{2}(?![0-9A-Za-z?X*]))"
)
_CONTRATANTE_WORD_RE = re.compile(
    r"(?i)\b(?:contratante|[óo]rg[ãa]o\s+gerenciador|locat[áa]ri[ao]|tomador(?:a)?)\b"
)
_CONTRATADA_WORD_RE = re.compile(
    r"(?i)\b(?:contratad[ao]|detentor[ao]?|locador[ao]?|fornecedor[ao]?|"
    r"prestador(?:a)?(?:\s+de\s+servi[çc]os)?)\b"
)
_INSURANCE_CONTEXT_RE = re.compile(r"(?i)\b(?:ap[óo]lice|seguro|garantia|certid[ãa]o)\b")
_VALIDITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?is)CL[ÁA]USULA\s+(?:\d+[ªa.º°]?|PRIMEIRA|SEGUNDA|TERCEIRA|QUARTA|QUINTA|"
        r"SEXTA|S[ÉE]TIMA|OITAVA|NONA|D[ÉE]CIMA)[^\n]*(?:PRAZO|VIG[ÊE]NCIA)[\s\S]{0,260}?"
        r"(\d{2}/\d{2}/\d{4})\s*(?:a|at[ée]|[-–])\s*(\d{2}/\d{2}/\d{4})"
    ),
    re.compile(
        r"(?i)(?:o\s+contrato|a\s+loca[çc][ãa]o|a\s+ata|o\s+instrumento)\s+"
        r"(?:ter[áa]\s+vig[êe]ncia|vigorar[áa])[^.\n]{0,140}?(?:de\s+)?"
        r"(\d{2}/\d{2}/\d{4})\s*(?:a|at[ée]|[-–])\s*(\d{2}/\d{2}/\d{4})"
    ),
    re.compile(
        r"(?i)vigorar[áa][^.\n]{0,120}?(?:de\s+)?"
        r"(\d{2}/\d{2}/\d{4})\s*(?:a|at[ée]|[-–])\s*(\d{2}/\d{2}/\d{4})"
    ),
    re.compile(
        r"(?i)vig[êe]ncia[^.\n]{0,120}?(?:de\s+|in[íi]cio\s+em\s+)?"
        r"(\d{2}/\d{2}/\d{4})\s*(?:a|at[ée]|[-–]|e\s+t[ée]rmino\s+em)\s*(\d{2}/\d{2}/\d{4})"
    ),
)
_COMPANY_LINE_RE = re.compile(
    r"(?im)^\s*(?:contratante|[óo]rg[ãa]o\s+gerenciador|locat[áa]ri[ao])\s*[:\-]\s*(.+?)\.?\s*$"
)
_SUPPLIER_LINE_RE = re.compile(
    r"(?im)^\s*(?:contratad[ao]|detentor[ao]?|locador[ao]?|fornecedor)\s*[:\-]\s*(.+?)\s*$"
)
_CONTRACT_CLAUSE_RE = re.compile(r"(?i)\bcl[aá]usula\b")
_STRUCTURED_INVOICE_RE = re.compile(
    r"(?:nota\s+fiscal|fatura(?:\s+de\s+servi[çc]os)?|nf-?e|nfs-?e|cobran[çc]a)"
    r"\s*(?:n[º°.]?|n[úu]mero|#)\s*[A-Z0-9-]+"
    r"|(?:valor\s+total\s+da\s+(?:fatura|nota)|total\s+(?:da\s+nota|faturado|cobrado)\s*:?\s*R\$)"
    r"|(?:demonstrativo\s+de\s+faturamento|documentos\s+fiscais|notas\s+fiscais\s+emitidas|"
    r"faturas\s+emitidas|hist[óo]rico\s+de\s+faturamento|dossi[êe]\s+contratual)"
    r"|^\s*(?:nf|nota\s+fiscal|fatura)\s+emiss[ãa]o\s+compet[êe]ncia\b",
    re.IGNORECASE | re.MULTILINE,
)
_AMENDMENT_HEADING_RE = re.compile(r"(?im)^\s*(?:pelo\s+)?(?:\d+[º°]?\s*)?termo\s+aditivo\b")
_PROVISIONAL_CNPJ = "00000000000191"
_DetectedUploadKind = Literal["CONTRACT", "DOSSIER", "AMENDMENT", "BILLING", "SKIPPED"]


class _BatchHint(StrictModel):
    cnpj: str | None = None
    company: str | None = None
    supplier: str | None = None


class _ParsedContractUpload(StrictModel):
    read_quality: ReadQuality
    full_text: str
    read_warning: str | None = None
    pdf_result: PdfReadResult | None = None
    docx_result: DocxReadResult | None = None


def _read_contract_bytes(file_name: str, raw_bytes: bytes) -> _ParsedContractUpload:
    suffix = Path(file_name).suffix.casefold()
    if suffix == ".pdf":
        pdf_res = read_pdf(raw_bytes)
        return _ParsedContractUpload(
            read_quality=pdf_res.read_quality,
            full_text=pdf_res.full_text,
            read_warning=pdf_res.warning,
            pdf_result=pdf_res,
        )
    docx_res = read_docx(raw_bytes)
    return _ParsedContractUpload(
        read_quality=docx_res.read_quality,
        full_text=docx_res.full_text,
        read_warning=docx_res.warning,
        docx_result=docx_res,
    )


def _try_ocr_pdf_with_client(
    file_name: str,
    raw_bytes: bytes,
    client: LLMClient | None,
) -> _ParsedContractUpload | None:
    if client is None or Path(file_name).suffix.casefold() != ".pdf":
        return None
    transcribe_fn = getattr(client, "transcribe_pdf", None)
    if not callable(transcribe_fn):
        return None
    try:
        raw_pages = transcribe_fn(raw_bytes)
    except (LLMExecutionError, OSError, ValueError, RuntimeError):
        return None
    if not raw_pages:
        return None
    pages = tuple(
        PdfPage(page_number=int(page_num), text=str(page_text).strip())
        for page_num, page_text in raw_pages
        if str(page_text).strip()
    )
    non_ws = sum(len("".join(p.text.split())) for p in pages)
    if not pages or non_ws < DEFAULT_MIN_TEXT_CHARS:
        return None
    full_text = "\n\n".join(p.text for p in pages)
    pdf_res = PdfReadResult(
        pages=pages,
        read_quality=ReadQuality.OK,
        full_text=full_text,
        warning=f"Texto de '{file_name}' extraído por leitura multimodal (PDF digitalizado).",
    )
    return _ParsedContractUpload(
        read_quality=ReadQuality.OK,
        full_text=full_text,
        read_warning=pdf_res.warning,
        pdf_result=pdf_res,
    )


def _classify_from_parsed(
    file_name: str,
    parsed: _ParsedContractUpload,
    mode: UploadClassificationMode,
) -> _DetectedUploadKind:
    if mode != "AUTO":
        return mode

    lowered = file_name.casefold()
    text = parsed.full_text
    has_clauses = bool(_CONTRACT_CLAUSE_RE.search(text))
    has_invoice_marker = bool(_STRUCTURED_INVOICE_RE.search(text))
    has_billing_filename = any(
        token in lowered
        for token in (
            "fatura",
            "nota_fiscal",
            "nf-e",
            "nfe",
            "nfse",
            "boleto",
            "cobranca",
            "cobrança",
        )
    )

    if "aditivo" in lowered:
        return "AMENDMENT"
    if "dossie" in lowered or "dossiê" in lowered:
        return "DOSSIER"
    if has_billing_filename:
        return "DOSSIER" if has_clauses else "BILLING"
    if has_clauses and has_invoice_marker:
        return "DOSSIER"
    if has_invoice_marker:
        return "BILLING"
    header_slice = text.lstrip()[:350]
    if (
        header_slice
        and not header_slice.casefold().startswith("contrato ")
        and _AMENDMENT_HEADING_RE.search(header_slice)
    ):
        return "AMENDMENT"
    return "CONTRACT"


def _classify_uploaded_file_with_content(
    file_name: str,
    raw_bytes: bytes,
    mode: UploadClassificationMode,
) -> tuple[_DetectedUploadKind, _ParsedContractUpload | None, str | None]:
    suffix = Path(file_name).suffix.casefold()
    if suffix not in {".pdf", ".docx", ".xml", ".csv", ".xlsx"}:
        return (
            "SKIPPED",
            None,
            f"Formato não suportado para '{file_name}'; envie PDF, DOCX, XML, CSV ou XLSX.",
        )
    if suffix in {".xml", ".csv", ".xlsx"}:
        return "BILLING", None, None

    try:
        parsed: _ParsedContractUpload = _read_contract_bytes(file_name, raw_bytes)
    except (OSError, ValueError):
        parsed = _ParsedContractUpload(
            read_quality=ReadQuality.NO_TEXT,
            full_text="",
            read_warning=f"Falha ao ler texto de '{file_name}'.",
        )

    return _classify_from_parsed(file_name, parsed, mode), parsed, None


def _extract_contract_id_hint(text: str, file_name: str) -> str | None:
    for source in (file_name, text):
        match_ct = _CONTRACT_CT_RE.search(source)
        if match_ct is not None:
            return match_ct.group(1).replace("_", "-").upper()
    for match_ata in _ATA_EXPLICIT_NUM_RE.finditer(text):
        candidate = match_ata.group(1).strip(" .,:;")
        if any(char.isdigit() for char in candidate) and ("/" in candidate or "-" in candidate):
            upper = candidate.upper()
            return upper if upper.startswith(("ARP-", "ATA-")) else f"ARP-{upper}"
    for match_num in _CONTRACT_EXPLICIT_NUM_RE.finditer(text):
        candidate = match_num.group(1).strip(" .,:;")
        if any(char.isdigit() for char in candidate) and (
            "/" in candidate or "-" in candidate or len(candidate) >= 3
        ):
            return candidate.upper()
    for match_code in _CONTRACT_CODE_RE.finditer(text):
        candidate = match_code.group(1).strip(" .,:;")
        if any(char.isdigit() for char in candidate):
            return candidate.upper()
    return None


def _extract_contract_validity(text: str) -> ValidityPeriod:
    """Extrai início e fim explícitos de vigência contratual em texto PT-BR, sem inventar datas."""
    for idx, pattern in enumerate(_VALIDITY_PATTERNS):
        for match in pattern.finditer(text):
            if idx == len(_VALIDITY_PATTERNS) - 1:
                prefix = text[max(0, match.start() - 80) : match.start()]
                if _INSURANCE_CONTEXT_RE.search(prefix) is not None:
                    continue
            try:
                start_dt = datetime.strptime(match.group(1), "%d/%m/%Y").date()
                end_dt = datetime.strptime(match.group(2), "%d/%m/%Y").date()
            except ValueError:
                continue
            if start_dt <= end_dt:
                return ValidityPeriod(start=start_dt, end=end_dt)
    return ValidityPeriod()


class _ContractPartiesInfo(StrictModel):
    company: str | None = None
    client_cnpj: str | None = None
    supplier: str | None = None
    supplier_cnpj: str | None = None
    invalid_supplier_cnpj: str | None = None


def _normalize_role_token(role: str) -> str:
    return (
        " ".join(role.upper().split())
        .replace("Ó", "O")
        .replace("Ã", "A")
        .replace("Á", "A")
        .replace("Í", "I")
        .replace("Ç", "C")
    )


def _clean_preamble_entity_name(raw_chunk: str) -> str | None:
    # Mantém apenas o núcleo anterior à qualificação jurídica/CNPJ.
    cut_m = re.search(
        r"(?is),\s*(?:pessoa\s+jur[íi]dica|inscrit[ao]\s+no\s+CNPJ|com\s+sede|CPF\b)"
        r"|\(\s*CNPJ\b",
        raw_chunk,
    )
    prefix = raw_chunk[: cut_m.start()] if cut_m is not None else raw_chunk
    # Em cabeçalhos de título seguidos de corpo do preâmbulo, preserva após a linha de título.
    header_split = re.split(
        r"(?is)^.*?(?:TERMO\s+DE\s+CONTRATO|CONTRATO\s+DE\s+LOCA[ÇC][ÃA]O|ATA\s+DE\s+REGISTRO"
        r"|Processo(?:\s+Administrativo)?\s+n[º°.]?)[^\n]*\n+",
        prefix,
        maxsplit=1,
    )
    if len(header_split) == 2 and header_split[1].strip():
        prefix = header_split[1]
    # Remove locuções introdutórias antes da razão social.
    interm_m = re.search(r"(?is)\bpor\s+interm[ée]dio\s+d[ao]\s+(.+)$", prefix)
    if interm_m is not None and interm_m.group(1).strip():
        prefix = interm_m.group(1)
    prefix = re.sub(
        r"(?is)^.*?(?:Pelo\s+presente\s+instrumento[^,]*,\s*)?de\s+(?:um|outro)\s+lado\b\s*,?\s*",
        "",
        prefix,
    )
    prefix = re.sub(
        r"(?is)^.*?\bAos\s+[^,]+dias\s+do\s+m[êe]s\s+de\s+[^,]+,\s*",
        "",
        prefix,
    )
    prefix = re.sub(
        r"(?is)^[\s;,.-]*(?:e\s+)?(?:,\s*de\s+outro\s+lado\s*,?\s*)?(?:a\s+empresa\s+|o\s+|a\s+)",
        "",
        prefix,
    )
    cleaned = " ".join(prefix.split()).strip(" ,-:;").lstrip(".")
    return cleaned or None


def _extract_contract_parties(text: str) -> _ContractPartiesInfo:
    """Identifica contratante/órgão e fornecedor/contratada/detentora/locadora no preâmbulo."""
    clause_cut = re.search(r"(?im)^\s*CL[ÁA]USULA\s+(?:1[ªa.]|PRIMEIRA)\b", text)
    preamble = text[: clause_cut.start()] if clause_cut is not None else text[:4000]

    company: str | None = None
    client_cnpj: str | None = None
    supplier: str | None = None
    supplier_cnpj: str | None = None
    invalid_supplier_cnpj: str | None = None

    prev_end = 0
    for role_m in _PREAMBLE_ROLE_END_RE.finditer(preamble):
        segment = preamble[prev_end : role_m.end()]
        prev_end = role_m.end()
        role_key = _normalize_role_token(role_m.group("role"))
        cnpjs_in_seg = _CNPJ_ANY_TOKEN_RE.findall(segment)
        if not cnpjs_in_seg:
            lookahead = preamble[role_m.start() : min(len(preamble), role_m.end() + 120)]
            cnpjs_in_seg = _CNPJ_ANY_TOKEN_RE.findall(lookahead)
        token = cnpjs_in_seg[-1] if cnpjs_in_seg else None
        name = _clean_preamble_entity_name(segment)
        if role_key in _SUPPLIER_ROLES:
            if supplier is None and name:
                supplier = name
            if token is not None:
                if is_valid_cnpj(token):
                    supplier_cnpj = normalize_cnpj(token)
                elif supplier_cnpj is None:
                    invalid_supplier_cnpj = token
        else:
            if company is None and name:
                company = name
            if token is not None and is_valid_cnpj(token) and client_cnpj is None:
                client_cnpj = normalize_cnpj(token)

    # Suporte complementar a linhas "CONTRATANTE:" / "LOCADORA:" / "DETENTORA:"
    if company is None:
        company_match = _COMPANY_LINE_RE.search(text)
        if company_match is not None and company_match.group(1).strip():
            company = _clean_party_name(company_match.group(1)) or company_match.group(1).strip()
    if supplier is None:
        supplier_match = _SUPPLIER_LINE_RE.search(text)
        if supplier_match is not None:
            cleaned = _clean_party_name(supplier_match.group(1))
            if cleaned:
                supplier = cleaned

    return _ContractPartiesInfo(
        company=company,
        client_cnpj=client_cnpj,
        supplier=supplier,
        supplier_cnpj=supplier_cnpj,
        invalid_supplier_cnpj=invalid_supplier_cnpj,
    )


def _extract_supplier_cnpj_info(text: str) -> tuple[str | None, str | None]:
    """Identifica o CNPJ da contratada/fornecedor sem nunca confundir com o da contratante."""
    parties = _extract_contract_parties(text)
    if parties.supplier_cnpj is not None:
        return parties.supplier_cnpj, None
    if parties.invalid_supplier_cnpj is not None:
        return None, parties.invalid_supplier_cnpj

    head = text[:3000]
    m_client = _CONTRATANTE_WORD_RE.search(head)
    m_supplier = _CONTRATADA_WORD_RE.search(head)
    has_side_by_side = (
        m_client is not None
        and m_supplier is not None
        and abs(m_supplier.start() - m_client.start()) <= 30
    )
    head_tokens = _CNPJ_ANY_TOKEN_RE.findall(head)
    if (
        has_side_by_side
        and len(head_tokens) >= 2
        and m_client is not None
        and m_supplier is not None
    ):
        supplier_token = head_tokens[1] if m_client.start() < m_supplier.start() else head_tokens[0]
        if is_valid_cnpj(supplier_token):
            return normalize_cnpj(supplier_token), None
        return None, supplier_token

    for match_sup in _SUPPLIER_CNPJ_RE.finditer(text):
        token = match_sup.group(1)
        if (
            parties.client_cnpj
            and is_valid_cnpj(token)
            and normalize_cnpj(token) == parties.client_cnpj
        ):
            continue
        if is_valid_cnpj(token):
            return normalize_cnpj(token), None
        return None, token

    if m_client is not None and m_supplier is not None and len(head_tokens) >= 2:
        supplier_token = head_tokens[1] if m_client.start() < m_supplier.start() else head_tokens[0]
        if not (
            parties.client_cnpj
            and is_valid_cnpj(supplier_token)
            and normalize_cnpj(supplier_token) == parties.client_cnpj
        ):
            if is_valid_cnpj(supplier_token):
                return normalize_cnpj(supplier_token), None
            return None, supplier_token

    for match in _CNPJ_CANDIDATE_RE.finditer(text):
        candidate = match.group(0)
        if is_valid_cnpj(candidate):
            norm = normalize_cnpj(candidate)
            if parties.client_cnpj and norm == parties.client_cnpj:
                continue
            return norm, None
    return None, None


def _extract_valid_cnpj_from_text(text: str) -> str | None:
    valid, _ = _extract_supplier_cnpj_info(text)
    return valid


def _clean_party_name(raw: str) -> str:
    cleaned = _CNPJ_CANDIDATE_RE.sub("", raw)
    cleaned = re.sub(r"(?i)\bcnpj\b", "", cleaned)
    return cleaned.strip(" .,-:;")


def _unique_auto_contract_id(
    conn: sqlite3.Connection,
    preferred: str | None,
    cnpj: str,
    *,
    fallback_stem: str | None = None,
) -> str:
    if preferred:
        base_id = preferred
    elif cnpj != _PROVISIONAL_CNPJ:
        base_id = f"CT-AUTO-{cnpj[8:12]}"
    else:
        slug = re.sub(r"[^A-Z0-9]+", "-", (fallback_stem or "").upper()).strip("-")[:18]
        base_id = f"CT-DOC-{slug}" if slug else "CT-DOC"
    candidate = base_id
    suffix = 2
    while get_contract(conn, candidate) is not None:
        candidate = f"{base_id}-{suffix}"
        suffix += 1
    return candidate


def _extract_party_names(text: str) -> tuple[str | None, str | None]:
    parties = _extract_contract_parties(text)
    if parties.company is not None or parties.supplier is not None:
        return parties.company, parties.supplier
    company_match = _COMPANY_LINE_RE.search(text)
    supplier_match = _SUPPLIER_LINE_RE.search(text)
    company_name = (
        company_match.group(1).strip()
        if company_match is not None and company_match.group(1).strip()
        else None
    )
    supplier_name: str | None = None
    if supplier_match is not None:
        cleaned = _clean_party_name(supplier_match.group(1))
        if cleaned:
            supplier_name = cleaned
    return company_name, supplier_name


def _collect_batch_hints(
    items: Sequence[tuple[str, bytes, _DetectedUploadKind, _ParsedContractUpload | None]],
) -> tuple[dict[str, _ParsedContractUpload], dict[str, _BatchHint], str | None]:
    """Lê arquivos do lote uma única vez e indexa CNPJs e nomes por contrato."""
    parsed_docs: dict[str, _ParsedContractUpload] = {}
    hints_by_id: dict[str, _BatchHint] = {}
    all_cnpjs: set[str] = set()

    for idx, (file_name, raw_bytes, kind, pre_parsed) in enumerate(items):
        key = f"{idx}:{file_name}"
        suffix = Path(file_name).suffix.casefold()
        if kind in {"CONTRACT", "AMENDMENT", "DOSSIER"}:
            if pre_parsed is not None:
                parsed = pre_parsed
            else:
                try:
                    parsed = _read_contract_bytes(file_name, raw_bytes)
                except (OSError, ValueError):
                    parsed = _ParsedContractUpload(
                        read_quality=ReadQuality.NO_TEXT,
                        full_text="",
                        read_warning=f"Falha ao ler texto de '{file_name}'.",
                    )
            parsed_docs[key] = parsed
            cid = _extract_contract_id_hint(parsed.full_text, file_name)
            cnpj = _extract_valid_cnpj_from_text(parsed.full_text)
            company, supplier = _extract_party_names(parsed.full_text)
            if cnpj is not None:
                all_cnpjs.add(cnpj)
            if cid is not None and any(val is not None for val in (cnpj, company, supplier)):
                prev = hints_by_id.get(cid, _BatchHint())
                hints_by_id[cid] = _BatchHint(
                    cnpj=prev.cnpj or cnpj,
                    company=prev.company or company,
                    supplier=prev.supplier or supplier,
                )
        elif kind == "BILLING":
            if pre_parsed is not None:
                parsed_docs[key] = pre_parsed
                bill_cnpj = _extract_valid_cnpj_from_text(pre_parsed.full_text)
                if bill_cnpj is not None:
                    all_cnpjs.add(bill_cnpj)
            try:
                if suffix == ".xml":
                    inv = _parse_xml_invoice(raw_bytes, invoice_id=None)
                    parsed_invoices: tuple[Invoice, ...] = (inv,)
                elif suffix == ".csv":
                    parsed_invoices = import_invoices_csv(raw_bytes)
                elif suffix == ".xlsx":
                    parsed_invoices = import_invoices_xlsx(raw_bytes)
                else:
                    parsed_invoices = ()
            except (UnsafeXmlError, OSError, ValueError):
                parsed_invoices = ()
            for inv in parsed_invoices:
                all_cnpjs.add(inv.supplier_cnpj)
                if inv.contract_id:
                    prev = hints_by_id.get(inv.contract_id, _BatchHint())
                    hints_by_id[inv.contract_id] = _BatchHint(
                        cnpj=prev.cnpj or inv.supplier_cnpj,
                        company=prev.company,
                        supplier=prev.supplier or inv.supplier,
                    )

    unique_cnpj = next(iter(all_cnpjs)) if len(all_cnpjs) == 1 else None
    return parsed_docs, hints_by_id, unique_cnpj


def _maybe_upgrade_provisional_contract(
    conn: sqlite3.Connection,
    contract: Contract,
    *,
    detected_cnpj: str | None,
    company_name: str | None,
    supplier_name: str | None,
    detected_validity: ValidityPeriod | None = None,
    batch_warnings: list[str],
) -> None:
    updates: dict[str, object] = {}
    if (
        contract.supplier_cnpj == _PROVISIONAL_CNPJ
        and detected_cnpj is not None
        and detected_cnpj != _PROVISIONAL_CNPJ
    ):
        updates["supplier_cnpj"] = detected_cnpj
        updates["company"] = company_name or contract.company
        updates["supplier"] = supplier_name or contract.supplier
    if (
        detected_validity is not None
        and contract.validity.start is None
        and contract.validity.end is None
        and (detected_validity.start is not None or detected_validity.end is not None)
    ):
        updates["validity"] = detected_validity
    if not updates:
        return
    upgraded = contract.model_copy(update=updates)
    register_contract(conn, upgraded)
    if "supplier_cnpj" in updates:
        batch_warnings.append(
            f"Contrato {upgraded.id} atualizado com o CNPJ real {upgraded.supplier_cnpj}."
        )


def _resolve_contract_for_file(
    conn: sqlite3.Connection,
    *,
    file_name: str,
    text: str,
    mode: UploadClassificationMode,
    selected_contract_id: str | None,
    hints_by_id: dict[str, _BatchHint],
    batch_unique_cnpj: str | None,
    auto_contracts: list[Contract],
    batch_warnings: list[str],
) -> str:
    """Resolve ou auto-cadastra um contrato para documento com texto legível."""
    file_cid = _extract_contract_id_hint(text, file_name)
    file_cnpj, invalid_supplier_cnpj = _extract_supplier_cnpj_info(text)
    file_company, file_supplier = _extract_party_names(text)
    file_validity = _extract_contract_validity(text)
    hint = hints_by_id.get(file_cid) if file_cid is not None else None
    effective_cnpj = file_cnpj or (hint.cnpj if hint else None) or batch_unique_cnpj
    effective_company = file_company or (hint.company if hint else None)
    effective_supplier = file_supplier or (hint.supplier if hint else None)

    if invalid_supplier_cnpj is not None:
        batch_warnings.append(
            f"CNPJ da contratada em '{file_name}' ('{invalid_supplier_cnpj}') está incompleto "
            "ou inválido; utilizado CNPJ provisório (00000000000191)."
        )

    if selected_contract_id is not None and (
        mode != "AUTO" or file_cid in (None, selected_contract_id)
    ):
        existing_sel = get_contract(conn, selected_contract_id)
        sel_compatible = existing_sel is not None and (
            mode != "AUTO"
            or file_cid == selected_contract_id
            or (
                invalid_supplier_cnpj is None
                and (
                    effective_cnpj is None
                    or existing_sel.supplier_cnpj in {_PROVISIONAL_CNPJ, effective_cnpj}
                )
            )
        )
        if sel_compatible and existing_sel is not None:
            _maybe_upgrade_provisional_contract(
                conn,
                existing_sel,
                detected_cnpj=effective_cnpj,
                company_name=effective_company,
                supplier_name=effective_supplier,
                detected_validity=file_validity,
                batch_warnings=batch_warnings,
            )
            return selected_contract_id

    if file_cid is not None:
        existing_cid = _match_contract_by_id_or_hint(list_contracts(conn), file_cid)
        if existing_cid is not None:
            _maybe_upgrade_provisional_contract(
                conn,
                existing_cid,
                detected_cnpj=effective_cnpj,
                company_name=effective_company,
                supplier_name=effective_supplier,
                detected_validity=file_validity,
                batch_warnings=batch_warnings,
            )
            return existing_cid.id

    if file_cid is None and effective_cnpj is not None:
        by_cnpj = [c for c in list_contracts(conn) if c.supplier_cnpj == effective_cnpj]
        if len(by_cnpj) == 1:
            return by_cnpj[0].id

    is_provisional = effective_cnpj is None
    chosen_cnpj = effective_cnpj if effective_cnpj is not None else _PROVISIONAL_CNPJ
    auto_id = _unique_auto_contract_id(
        conn,
        file_cid,
        chosen_cnpj,
        fallback_stem=Path(file_name).stem,
    )
    auto_contract = Contract(
        id=auto_id,
        company=effective_company or "Empresa contratante (importação direta)",
        supplier=effective_supplier
        or (
            "Fornecedor (CNPJ não informado no arquivo)"
            if is_provisional
            else "Fornecedor Identificado no Documento"
        ),
        supplier_cnpj=chosen_cnpj,
        subject="Contrato cadastrado automaticamente a partir dos documentos enviados",
        validity=file_validity,
        comparison_tolerance=Decimal("0.00"),
        status=ContractStatus.ACTIVE,
    )
    register_contract(conn, auto_contract)
    auto_contracts.append(auto_contract)
    if is_provisional:
        batch_warnings.append(
            f"Contrato {auto_contract.id} cadastrado provisoriamente (sem CNPJ válido da "
            f"contratada em '{file_name}'); atualize o CNPJ no cadastro se necessário."
        )
    else:
        batch_warnings.append(
            f"Contrato {auto_contract.id} cadastrado automaticamente com CNPJ "
            f"{auto_contract.supplier_cnpj}; confira vigência e tolerância se necessário."
        )
    return auto_contract.id


def _auto_link_extracted_amendments(
    conn: sqlite3.Connection,
    contract_id: str,
    doc: Document,
    extracted_rules: Sequence[ContractRule],
) -> None:
    """Vincula automaticamente regras de aditivos (ou alterações sequenciais em dossiês)."""
    if not extracted_rules:
        return

    if doc.document_type is DocumentType.CONTRACT:
        by_type: dict[tuple[RuleType, str], list[ContractRule]] = {}
        for rule in extracted_rules:
            if rule.validity.start is not None:
                key = (
                    rule.rule_type,
                    (
                        normalize_item_key(rule.item_name)
                        if rule.rule_type is RuleType.ITEM_PRICE
                        else ""
                    ),
                )
                by_type.setdefault(key, []).append(rule)
        for group in by_type.values():
            if len(group) < 2:
                continue
            ordered = sorted(group, key=lambda r: (r.validity.start or date.min, r.id))
            for idx in range(len(ordered) - 1):
                prev_rule = ordered[idx]
                next_rule = ordered[idx + 1]
                prev_start = prev_rule.validity.start
                next_start = next_rule.validity.start
                if prev_start is None or next_start is None:
                    continue
                if next_start <= prev_start:
                    continue
                if prev_rule.validity.end is None or prev_rule.validity.end >= next_start:
                    closed_end = next_start - timedelta(days=1)
                    if closed_end >= prev_start:
                        save_rule(
                            conn,
                            prev_rule.model_copy(
                                update={
                                    "validity": ValidityPeriod(
                                        start=prev_start,
                                        end=closed_end,
                                    )
                                }
                            ),
                        )
                        ordered[idx + 1] = next_rule.model_copy(
                            update={"version": prev_rule.version + 1}
                        )
                        save_rule(conn, ordered[idx + 1])
        return

    if doc.document_type is not DocumentType.AMENDMENT:
        return

    all_rules = list_rules(conn, contract_id)
    documents = list_documents(conn, contract_id)
    rejected_ids = {r.id for r in all_rules if r.review_status is ReviewStatus.REJECTED}
    working_amendments = [
        item
        for item in _contract_amendments_for_context(list_amendments(conn), all_rules, documents)
        if item.affected_rule_id not in rejected_ids
        and (item.new_rule is None or item.new_rule.id not in rejected_ids)
    ]
    already_linked_new_ids = {
        item.new_rule.id for item in working_amendments if item.new_rule is not None
    }
    base_rules = tuple(
        rule
        for rule in all_rules
        if rule.id not in rejected_ids and rule.id not in already_linked_new_ids
    )
    timeline_rules = apply_amendments(base_rules, working_amendments).rules

    ordered_extracted = sorted(
        extracted_rules,
        key=lambda r: (r.validity.start or date.min, r.id),
    )
    for new_rule in ordered_extracted:
        if new_rule.id in already_linked_new_ids or new_rule.validity.start is None:
            continue
        effective = new_rule.validity.start
        if new_rule.validity.end is not None and new_rule.validity.end < effective:
            continue
        new_item_key = (
            normalize_item_key(new_rule.item_name)
            if new_rule.rule_type is RuleType.ITEM_PRICE
            else ""
        )
        candidates = [
            cand
            for cand in timeline_rules
            if cand.id != new_rule.id
            and cand.rule_type is new_rule.rule_type
            and (
                new_rule.rule_type is not RuleType.ITEM_PRICE
                or normalize_item_key(cand.item_name) == new_item_key
            )
            and cand.review_status is not ReviewStatus.REJECTED
            and cand.validity.start is not None
            and cand.validity.start < effective
            and (cand.validity.end is None or cand.validity.end >= effective)
        ]
        if not candidates:
            continue
        target = max(
            candidates,
            key=lambda cand: (cand.validity.start or date.min, cand.version, cand.id),
        )
        relation = (
            RuleRelation.REVOKED
            if new_rule.relation is RuleRelation.REVOKED
            else RuleRelation.ALTERED
        )
        created_amend = Amendment(
            id=f"amend-auto-{uuid4().hex[:10]}",
            affected_rule_id=target.id,
            new_rule=None if relation is RuleRelation.REVOKED else new_rule,
            source_document_id=doc.id,
            effective_date=effective,
            relation=relation,
        )
        register_amendment(conn, created_amend)
        working_amendments.append(created_amend)
        already_linked_new_ids.add(new_rule.id)
        base_rules = tuple(r for r in base_rules if r.id not in already_linked_new_ids)
        timeline_rules = apply_amendments(base_rules, working_amendments).rules


def _store_contract_doc_without_ai(
    conn: sqlite3.Connection,
    *,
    contract_id: str | None,
    document_id: str,
    file_name: str,
    raw_bytes: bytes,
    document_type: DocumentType,
    timestamp: datetime,
    parsed: _ParsedContractUpload | None = None,
) -> tuple[Document, tuple[str, ...]]:
    resolved_parsed = parsed if parsed is not None else _read_contract_bytes(file_name, raw_bytes)
    document = receive_document(
        document_id=document_id,
        contract_id=contract_id,
        document_type=document_type,
        file_name=file_name,
        content=raw_bytes,
        received_at=timestamp,
        read_quality=resolved_parsed.read_quality,
        extracted_text=resolved_parsed.full_text,
        reviewed=False,
        conn=conn,
    )
    warns: list[str] = []
    if resolved_parsed.read_warning is not None:
        warns.append(resolved_parsed.read_warning)
    if resolved_parsed.read_quality is ReadQuality.NO_TEXT:
        warns.append(
            f"PDF sem camada de texto (NO_TEXT): '{file_name}' registrado com SHA-256, "
            "mas sem texto para extração."
        )
    else:
        warns.append(
            f"Arquivo '{file_name}' salvo com SHA-256; configure LLM_API_KEY em .env para "
            "extrair regras por IA automaticamente."
        )
    return document, _unique_messages(warns)


def ingest_uploaded_files(
    conn: sqlite3.Connection,
    files: Sequence[tuple[str, bytes]],
    *,
    mode: UploadClassificationMode = "AUTO",
    selected_contract_id: str | None = None,
    client: LLMClient | None = None,
    received_at: datetime | None = None,
    auto_approve_above_threshold: bool = True,
) -> BatchUploadResult:
    """Ingere múltiplos arquivos (.pdf, .docx, .xml, .csv, .xlsx) e audita automaticamente."""
    timestamp = received_at if received_at is not None else datetime.now(UTC)
    classified: list[
        tuple[int, str, bytes, _DetectedUploadKind, _ParsedContractUpload | None, str | None]
    ] = []
    for idx, (file_name, raw_bytes) in enumerate(files):
        kind, pre_parsed, warn = _classify_uploaded_file_with_content(file_name, raw_bytes, mode)
        if (
            pre_parsed is not None
            and pre_parsed.read_quality is ReadQuality.NO_TEXT
            and client is not None
        ):
            ocr_parsed = _try_ocr_pdf_with_client(file_name, raw_bytes, client)
            if ocr_parsed is not None:
                pre_parsed = ocr_parsed
                kind = _classify_from_parsed(file_name, ocr_parsed, mode)
        classified.append((idx, file_name, raw_bytes, kind, pre_parsed, warn))

    if selected_contract_id is not None and get_contract(conn, selected_contract_id) is None:
        raise ValueError(f"contrato {selected_contract_id} não encontrado")

    auto_contracts: list[Contract] = []
    batch_warnings: list[str] = []
    active_contract_id = selected_contract_id

    parsed_docs, hints_by_id, batch_unique_cnpj = _collect_batch_hints(
        [(name, payload, kind, pre_parsed) for _, name, payload, kind, pre_parsed, _ in classified]
    )

    order_rank: dict[_DetectedUploadKind, int] = {
        "CONTRACT": 0,
        "DOSSIER": 1,
        "AMENDMENT": 2,
        "BILLING": 3,
        "SKIPPED": 4,
    }
    ordered = sorted(classified, key=lambda item: order_rank[item[3]])
    outcomes: list[UploadedFileOutcome] = []
    affected_contract_ids: set[str] = set()
    imported_invoice_ids: list[str] = []

    for idx, file_name, raw_bytes, kind, _, initial_warn in ordered:
        if kind == "SKIPPED":
            outcomes.append(
                UploadedFileOutcome(
                    file_name=file_name,
                    detected_kind="SKIPPED",
                    warnings=(initial_warn,) if initial_warn else (),
                )
            )
            continue

        if kind in {"CONTRACT", "AMENDMENT", "DOSSIER"}:
            doc_type = DocumentType.AMENDMENT if kind == "AMENDMENT" else DocumentType.CONTRACT
            doc_id = f"doc-{uuid4().hex[:10]}"
            parsed = parsed_docs.get(f"{idx}:{file_name}")
            if parsed is None:
                parsed = _read_contract_bytes(file_name, raw_bytes)

            if parsed.read_quality is ReadQuality.NO_TEXT:
                no_text_cid: str | None = selected_contract_id
                if no_text_cid is None:
                    hint_cid = _extract_contract_id_hint("", file_name)
                    if hint_cid is not None and get_contract(conn, hint_cid) is not None:
                        no_text_cid = hint_cid
                doc = receive_document(
                    document_id=doc_id,
                    contract_id=no_text_cid,
                    document_type=doc_type,
                    file_name=file_name,
                    content=raw_bytes,
                    received_at=timestamp,
                    read_quality=ReadQuality.NO_TEXT,
                    extracted_text="",
                    reviewed=False,
                    conn=conn,
                )
                outcomes.append(
                    UploadedFileOutcome(
                        file_name=file_name,
                        detected_kind=kind,
                        document=doc,
                        contract_id=doc.contract_id,
                        rules_count=0,
                        warnings=(
                            f"PDF sem camada de texto (NO_TEXT): '{file_name}' registrado com "
                            "SHA-256, mas sem texto para extração.",
                        ),
                    )
                )
                continue

            resolved_cid = _resolve_contract_for_file(
                conn,
                file_name=file_name,
                text=parsed.full_text,
                mode=mode,
                selected_contract_id=selected_contract_id,
                hints_by_id=hints_by_id,
                batch_unique_cnpj=batch_unique_cnpj,
                auto_contracts=auto_contracts,
                batch_warnings=batch_warnings,
            )
            affected_contract_ids.add(resolved_cid)
            if kind in {"CONTRACT", "DOSSIER"} or active_contract_id is None:
                active_contract_id = resolved_cid

            if client is not None:
                try:
                    proc = process_contract_document(
                        conn,
                        contract_id=resolved_cid,
                        document_id=doc_id,
                        file_name=file_name,
                        content=raw_bytes,
                        client=client,
                        document_type=doc_type,
                        received_at=timestamp,
                        auto_approve_above_threshold=auto_approve_above_threshold,
                    )
                except (OSError, ValueError, RuntimeError) as exc:
                    saved_doc = get_document(conn, doc_id)
                    if saved_doc is None:
                        saved_doc, _ = _store_contract_doc_without_ai(
                            conn,
                            contract_id=resolved_cid,
                            document_id=doc_id,
                            file_name=file_name,
                            raw_bytes=raw_bytes,
                            document_type=doc_type,
                            timestamp=timestamp,
                            parsed=parsed,
                        )
                    outcomes.append(
                        UploadedFileOutcome(
                            file_name=file_name,
                            detected_kind=kind,
                            document=saved_doc,
                            contract_id=resolved_cid,
                            warnings=(
                                f"Falha na IA ao processar '{file_name}': {exc}. "
                                "O documento e seu SHA-256 foram preservados.",
                            ),
                        )
                    )
                    continue

                _auto_link_extracted_amendments(conn, resolved_cid, proc.document, proc.rules)
                warns = list(proc.warnings)
                saved_doc_final = proc.document
                dossier_invoices_count = 0
                dossier_unlinked_count = 0

                if kind == "DOSSIER":
                    suffix = Path(file_name).suffix.casefold()
                    fiscal_input: FiscalPdfInput | None = None
                    if suffix == ".pdf":
                        pdf_obj = parsed.pdf_result or read_pdf(raw_bytes)
                        fiscal_input = FiscalPdfInput.from_pdf(
                            proc.document.id,
                            pdf_obj,
                            file_hash=proc.document.file_hash,
                        )
                    elif suffix == ".docx":
                        docx_obj = parsed.docx_result or read_docx(raw_bytes)
                        fiscal_input = FiscalPdfInput.from_docx(
                            proc.document.id,
                            docx_obj,
                            file_hash=proc.document.file_hash,
                        )

                    if fiscal_input is not None:
                        try:
                            fiscal_res = run_fiscal_pdf_agent(
                                fiscal_input,
                                client,
                                extracted_at=timestamp,
                            )
                        except (OSError, ValueError, RuntimeError) as exc:
                            warns.append(
                                f"Falha ao extrair cobrança do dossiê '{file_name}': {exc}"
                            )
                        else:
                            warns.extend(fiscal_res.warnings)
                            dossier_saved_ids: list[str] = []
                            existing_target = get_contract(conn, resolved_cid)
                            if (
                                existing_target is not None
                                and existing_target.supplier_cnpj == _PROVISIONAL_CNPJ
                            ):
                                valid_counts = Counter(
                                    raw_inv.supplier_cnpj
                                    for raw_inv in fiscal_res.invoices
                                    if raw_inv.supplier_cnpj != _PROVISIONAL_CNPJ
                                    and is_valid_cnpj(raw_inv.supplier_cnpj)
                                )
                                if valid_counts:
                                    top_cnpj, top_hits = valid_counts.most_common(1)[0]
                                    min_majority = max(1, (len(fiscal_res.invoices) + 1) // 2)
                                    is_unique_top = (
                                        len(valid_counts) == 1
                                        or top_hits > valid_counts.most_common(2)[1][1]
                                    )
                                    if top_hits >= min_majority and is_unique_top:
                                        rep_supplier = next(
                                            (
                                                raw_inv.supplier
                                                for raw_inv in fiscal_res.invoices
                                                if raw_inv.supplier_cnpj == top_cnpj
                                            ),
                                            None,
                                        )
                                        _maybe_upgrade_provisional_contract(
                                            conn,
                                            existing_target,
                                            detected_cnpj=top_cnpj,
                                            company_name=None,
                                            supplier_name=rep_supplier,
                                            batch_warnings=batch_warnings,
                                        )
                            for raw_inv in fiscal_res.invoices:
                                linked_cid, link_warn = resolve_contract_link(
                                    list_contracts(conn), raw_inv
                                )
                                if linked_cid is None and raw_inv.contract_id is None:
                                    refreshed = get_contract(conn, resolved_cid)
                                    if refreshed is not None and (
                                        refreshed.supplier_cnpj == raw_inv.supplier_cnpj
                                        or any(c.id == resolved_cid for c in auto_contracts)
                                        or raw_inv.supplier_cnpj == _PROVISIONAL_CNPJ
                                    ):
                                        linked_cid = resolved_cid
                                        link_warn = None
                                if link_warn is not None:
                                    warns.append(link_warn)
                                saved_inv = raw_inv.model_copy(update={"contract_id": linked_cid})
                                save_invoice(conn, saved_inv)
                                imported_invoice_ids.append(saved_inv.id)
                                dossier_saved_ids.append(saved_inv.id)
                                if linked_cid is None:
                                    dossier_unlinked_count += 1
                                else:
                                    affected_contract_ids.add(linked_cid)

                            dossier_invoices_count = len(dossier_saved_ids)
                            if dossier_saved_ids:
                                saved_doc_final = proc.document.model_copy(
                                    update={
                                        "extracted_text": (
                                            f"{proc.document.extracted_text}"
                                            f"\n[invoice_ids:{','.join(dossier_saved_ids)}]"
                                        )
                                    }
                                )
                                save_document(conn, saved_doc_final)

                    sync_contract_dossier_data(
                        conn,
                        resolved_cid,
                        auto_approve_above_threshold=auto_approve_above_threshold,
                    )

                if (
                    proc.document.read_quality is ReadQuality.OK
                    and not proc.rules
                    and dossier_invoices_count == 0
                ):
                    warns.append(
                        f"Nenhuma regra extraída de '{file_name}'; "
                        "o sistema não presume que nada mudou."
                    )
                outcomes.append(
                    UploadedFileOutcome(
                        file_name=file_name,
                        detected_kind=kind,
                        document=saved_doc_final,
                        contract_id=resolved_cid,
                        rules_count=len(proc.rules),
                        invoices_count=dossier_invoices_count,
                        unlinked_invoices_count=dossier_unlinked_count,
                        warnings=_unique_messages(warns),
                    )
                )
            else:
                doc, warns = _store_contract_doc_without_ai(
                    conn,
                    contract_id=resolved_cid,
                    document_id=doc_id,
                    file_name=file_name,
                    raw_bytes=raw_bytes,
                    document_type=doc_type,
                    timestamp=timestamp,
                    parsed=parsed,
                )
                outcomes.append(
                    UploadedFileOutcome(
                        file_name=file_name,
                        detected_kind=kind,
                        document=doc,
                        contract_id=resolved_cid,
                        warnings=warns,
                    )
                )
            continue

        # Processamento de cobranças, OPs ou certidões (.xml, .csv, .xlsx ou PDF/DOCX)
        doc_id = f"doc-{uuid4().hex[:10]}"
        suffix = Path(file_name).suffix.casefold()
        if suffix in {".csv", ".xlsx"}:
            try:
                sheet_kind = detect_spreadsheet_kind(raw_bytes, is_xlsx=(suffix == ".xlsx"))
            except (OSError, ValueError):
                sheet_kind = "INVOICES"

            if sheet_kind == "PAYMENT_ORDERS":
                fallback_cid = selected_contract_id or active_contract_id
                try:
                    orders = (
                        import_payment_orders_csv(
                            raw_bytes,
                            default_contract_id=fallback_cid,
                            source_document_id=doc_id,
                        )
                        if suffix == ".csv"
                        else import_payment_orders_xlsx(
                            raw_bytes,
                            default_contract_id=fallback_cid,
                            source_document_id=doc_id,
                        )
                    )
                except (OSError, ValueError) as exc:
                    outcomes.append(
                        UploadedFileOutcome(
                            file_name=file_name,
                            detected_kind="BILLING",
                            warnings=(f"Falha ao importar OPs de '{file_name}': {exc}",),
                        )
                    )
                    continue
                target_cid = (
                    orders[0].contract_id
                    if orders and orders[0].contract_id is not None
                    else fallback_cid
                )
                doc = receive_document(
                    document_id=doc_id,
                    contract_id=target_cid,
                    document_type=DocumentType.SPREADSHEET,
                    file_name=file_name,
                    content=raw_bytes,
                    received_at=timestamp,
                    read_quality=ReadQuality.OK,
                    extracted_text=(
                        f"Planilha de ordens de pagamento '{file_name}' "
                        f"({len(orders)} registro(s))."
                    ),
                    reviewed=False,
                    conn=conn,
                )
                inv_lookup = (
                    {
                        inv.number.strip().casefold(): inv.id
                        for inv in list_invoices(conn, target_cid)
                    }
                    if target_cid is not None
                    else {}
                )
                for order in orders:
                    cid = order.contract_id or target_cid
                    matched_inv_id = inv_lookup.get(order.invoice_number.strip().casefold())
                    save_payment_order(
                        conn,
                        order.model_copy(update={"contract_id": cid, "invoice_id": matched_inv_id}),
                    )
                    if cid is not None:
                        affected_contract_ids.add(cid)
                outcomes.append(
                    UploadedFileOutcome(
                        file_name=file_name,
                        detected_kind="BILLING",
                        document=doc,
                        contract_id=target_cid,
                        warnings=(),
                    )
                )
                continue

            if sheet_kind == "CERTIFICATES":
                fallback_cid = selected_contract_id or active_contract_id
                if fallback_cid is None:
                    contracts_avail = list_contracts(conn)
                    if len(contracts_avail) == 1:
                        fallback_cid = contracts_avail[0].id
                if fallback_cid is None:
                    outcomes.append(
                        UploadedFileOutcome(
                            file_name=file_name,
                            detected_kind="BILLING",
                            warnings=(
                                f"Selecione um contrato antes de importar certidões de "
                                f"'{file_name}'.",
                            ),
                        )
                    )
                    continue
                try:
                    certs = (
                        import_certificates_csv(
                            raw_bytes,
                            default_contract_id=fallback_cid,
                            source_document_id=doc_id,
                        )
                        if suffix == ".csv"
                        else import_certificates_xlsx(
                            raw_bytes,
                            default_contract_id=fallback_cid,
                            source_document_id=doc_id,
                        )
                    )
                except (OSError, ValueError) as exc:
                    outcomes.append(
                        UploadedFileOutcome(
                            file_name=file_name,
                            detected_kind="BILLING",
                            warnings=(f"Falha ao importar certidões de '{file_name}': {exc}",),
                        )
                    )
                    continue
                doc = receive_document(
                    document_id=doc_id,
                    contract_id=fallback_cid,
                    document_type=DocumentType.SPREADSHEET,
                    file_name=file_name,
                    content=raw_bytes,
                    received_at=timestamp,
                    read_quality=ReadQuality.OK,
                    extracted_text=(
                        f"Planilha de certidões '{file_name}' ({len(certs)} registro(s))."
                    ),
                    reviewed=False,
                    conn=conn,
                )
                for cert in certs:
                    save_tax_certificate(conn, cert)
                    affected_contract_ids.add(cert.contract_id)
                outcomes.append(
                    UploadedFileOutcome(
                        file_name=file_name,
                        detected_kind="BILLING",
                        document=doc,
                        contract_id=fallback_cid,
                        warnings=(),
                    )
                )
                continue

        if suffix in {".pdf", ".docx"} and client is None:
            parsed_bill = parsed_docs.get(f"{idx}:{file_name}") or _read_contract_bytes(
                file_name, raw_bytes
            )
            doc = receive_document(
                document_id=doc_id,
                contract_id=active_contract_id,
                document_type=DocumentType.INVOICE,
                file_name=file_name,
                content=raw_bytes,
                received_at=timestamp,
                read_quality=parsed_bill.read_quality,
                extracted_text=parsed_bill.full_text,
                reviewed=False,
                conn=conn,
            )
            bill_warns: list[str] = []
            if parsed_bill.read_warning is not None:
                bill_warns.append(parsed_bill.read_warning)
            bill_warns.append(
                f"Fatura '{file_name}' gravada com SHA-256; configure LLM_API_KEY em .env "
                "para extrair os itens da fatura."
            )
            outcomes.append(
                UploadedFileOutcome(
                    file_name=file_name,
                    detected_kind="BILLING",
                    document=doc,
                    contract_id=doc.contract_id,
                    warnings=_unique_messages(bill_warns),
                )
            )
            continue

        try:
            imp = import_billing_document(
                conn,
                document_id=doc_id,
                file_name=file_name,
                content=raw_bytes,
                client=client,
                received_at=timestamp,
            )
        except (UnsafeXmlError, OSError, ValueError, RuntimeError) as exc:
            saved_billing_doc = get_document(conn, doc_id)
            outcomes.append(
                UploadedFileOutcome(
                    file_name=file_name,
                    detected_kind="BILLING",
                    document=saved_billing_doc,
                    contract_id=saved_billing_doc.contract_id if saved_billing_doc else None,
                    warnings=(f"Falha ao importar '{file_name}': {exc}",),
                )
            )
            continue
        for inv in imp.invoices:
            imported_invoice_ids.append(inv.id)
            if inv.contract_id is not None:
                affected_contract_ids.add(inv.contract_id)
        outcomes.append(
            UploadedFileOutcome(
                file_name=file_name,
                detected_kind="BILLING",
                document=imp.document,
                contract_id=imp.document.contract_id,
                invoices_count=len(imp.invoices),
                unlinked_invoices_count=len(imp.unlinked_invoice_ids),
                warnings=imp.warnings,
            )
        )

    audits_count = 0
    divergences_count = 0
    for cid in sorted(affected_contract_ids):
        sync_contract_dossier_data(
            conn,
            cid,
            auto_approve_above_threshold=auto_approve_above_threshold,
        )
    if affected_contract_ids or imported_invoice_ids:
        imported_id_set = set(imported_invoice_ids)
        seen_inv_ids: set[str] = set()
        for inv in list_invoices(conn):
            if inv.id in seen_inv_ids:
                continue
            if inv.id not in imported_id_set and inv.contract_id not in affected_contract_ids:
                continue
            seen_inv_ids.add(inv.id)
            existing_audits = list_audits(conn, inv.id)
            already_reviewed = any(
                bool(
                    list_reviews(
                        conn,
                        target_type=ReviewTargetType.AUDIT_RESULT,
                        target_id=aud.result.id,
                    )
                )
                for aud in existing_audits
            )
            if already_reviewed:
                continue
            audit_out = audit_stored_invoice(conn, inv.id)
            audits_count += 1
            if (
                audit_out.stored_audit.result.classification
                is not AuditClassification.NO_DIVERGENCE
            ):
                divergences_count += 1

    for outcome in outcomes:
        batch_warnings.extend(outcome.warnings)

    return BatchUploadResult(
        auto_registered_contracts=tuple(auto_contracts),
        active_contract_id=active_contract_id,
        outcomes=tuple(outcomes),
        warnings=_unique_messages(batch_warnings),
        audits_count=audits_count,
        divergences_count=divergences_count,
    )


_INVOICE_IDS_MARKER_RE = re.compile(r"\[invoice_ids:([^\]]+)\]")
_NFE_DOC_TEXT_RE = re.compile(r"^Cobrança\s+(.+?)\s+importada de\s+")
_SPREADSHEET_DOC_TEXT_RE = re.compile(r"com cobrança\(s\):\s*(.+?)\.?\s*$")


def _invoice_ids_for_document(conn: sqlite3.Connection, doc: Document) -> list[str]:
    existing = list_invoices(conn)
    existing_ids = {inv.id for inv in existing}
    marker = _INVOICE_IDS_MARKER_RE.search(doc.extracted_text)
    if marker is not None:
        return [
            item.strip()
            for item in marker.group(1).split(",")
            if item.strip() and item.strip() in existing_ids
        ]

    if doc.document_type not in {
        DocumentType.NFE_XML,
        DocumentType.SPREADSHEET,
        DocumentType.INVOICE,
    }:
        return []

    # Fallback para documentos gravados antes do marcador explícito de cobranças.
    scoped = list_invoices(conn, doc.contract_id) if doc.contract_id is not None else existing
    clean_text = doc.extracted_text.strip()
    if doc.document_type is DocumentType.NFE_XML:
        match = _NFE_DOC_TEXT_RE.search(clean_text)
        if match is not None:
            number = match.group(1).strip()
            return [
                inv.id
                for inv in scoped
                if inv.source is InvoiceSource.NFE_XML and inv.number == number
            ]
    elif doc.document_type is DocumentType.SPREADSHEET:
        match = _SPREADSHEET_DOC_TEXT_RE.search(clean_text)
        if match is not None:
            numbers = {part.strip() for part in match.group(1).split(",") if part.strip()}
            return [
                inv.id
                for inv in scoped
                if inv.source is InvoiceSource.SPREADSHEET and inv.number in numbers
            ]
    elif doc.document_type is DocumentType.INVOICE:
        candidate = f"inv-{doc.id}"
        return [candidate] if candidate in existing_ids else []
    return []


def delete_uploaded_document(
    conn: sqlite3.Connection,
    document_id: str,
) -> DocumentDeletionResult:
    """Apaga um documento enviado e seus dados derivados (preservando `reviews` append-only)."""
    doc = get_document(conn, document_id)
    if doc is None:
        raise ValueError(f"documento {document_id} não encontrado")

    rule_ids_set: set[str] = {
        str(row["rule_id"])
        for row in conn.execute(
            "SELECT rule_id FROM evidences WHERE source_document_id = ?",
            (document_id,),
        ).fetchall()
    }
    for row in conn.execute(
        (
            "SELECT new_rule_id FROM amendments "
            "WHERE source_document_id = ? AND new_rule_id IS NOT NULL"
        ),
        (document_id,),
    ).fetchall():
        rule_ids_set.add(str(row["new_rule_id"]))

    amendment_ids_set: set[str] = {
        str(row["id"])
        for row in conn.execute(
            "SELECT id FROM amendments WHERE source_document_id = ?",
            (document_id,),
        ).fetchall()
    }
    for row in conn.execute("SELECT id, affected_rule_id, new_rule_id FROM amendments").fetchall():
        if str(row["affected_rule_id"]) in rule_ids_set or (
            row["new_rule_id"] is not None and str(row["new_rule_id"]) in rule_ids_set
        ):
            amendment_ids_set.add(str(row["id"]))

    invoice_ids = _invoice_ids_for_document(conn, doc)

    deleted_amendments = 0
    deleted_rules = 0
    deleted_audits = 0
    deleted_invoices = 0

    with conn:
        for amend_id in sorted(amendment_ids_set):
            deleted_amendments += conn.execute(
                "DELETE FROM amendments WHERE id = ?",
                (amend_id,),
            ).rowcount
        for rule_id in sorted(rule_ids_set):
            deleted_rules += conn.execute(
                "DELETE FROM rules WHERE id = ?",
                (rule_id,),
            ).rowcount
        for inv_id in invoice_ids:
            deleted_audits += conn.execute(
                "DELETE FROM audits WHERE invoice_id = ?",
                (inv_id,),
            ).rowcount
            conn.execute("DELETE FROM payment_orders WHERE invoice_id = ?", (inv_id,))
            deleted_invoices += conn.execute(
                "DELETE FROM invoices WHERE id = ?",
                (inv_id,),
            ).rowcount
        conn.execute("DELETE FROM payment_orders WHERE source_document_id = ?", (document_id,))
        conn.execute("DELETE FROM tax_certificates WHERE source_document_id = ?", (document_id,))
        conn.execute("DELETE FROM financial_summaries WHERE source_document_id = ?", (document_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))

    return DocumentDeletionResult(
        document_id=doc.id,
        file_name=doc.file_name,
        deleted_rules=deleted_rules,
        deleted_amendments=deleted_amendments,
        deleted_invoices=deleted_invoices,
        deleted_audits=deleted_audits,
    )


def delete_all_uploaded_documents(
    conn: sqlite3.Connection,
    contract_id: str | None = None,
) -> tuple[DocumentDeletionResult, ...]:
    """Apaga em sequência todos os documentos enviados (ou os de um contrato)."""
    docs = list_documents(conn, contract_id=contract_id)
    return tuple(delete_uploaded_document(conn, doc.id) for doc in docs)


def delete_stored_invoice(conn: sqlite3.Connection, invoice_id: str) -> bool:
    """Remove uma cobrança e auditorias (preserva `reviews`) e limpa vínculos documentais."""
    inv = get_invoice(conn, invoice_id)
    if inv is None:
        return False

    docs_to_update: list[tuple[str, str]] = []
    docs_to_delete: list[str] = []

    for doc in list_documents(conn):
        marker = _INVOICE_IDS_MARKER_RE.search(doc.extracted_text)
        has_refs = bool(
            conn.execute(
                "SELECT 1 FROM evidences WHERE source_document_id = ? LIMIT 1",
                (doc.id,),
            ).fetchone()
            or conn.execute(
                "SELECT 1 FROM amendments WHERE source_document_id = ? LIMIT 1",
                (doc.id,),
            ).fetchone()
        )
        if marker is not None:
            raw_ids = [item.strip() for item in marker.group(1).split(",") if item.strip()]
            if invoice_id not in raw_ids:
                continue
            remaining = [item for item in raw_ids if item != invoice_id]
            if (
                not remaining
                and doc.document_type in {DocumentType.NFE_XML, DocumentType.INVOICE}
                and not has_refs
            ):
                docs_to_delete.append(doc.id)
                continue
            if remaining:
                new_text = (
                    doc.extracted_text[: marker.start()]
                    + f"[invoice_ids:{','.join(remaining)}]"
                    + doc.extracted_text[marker.end() :]
                )
            else:
                new_text = _INVOICE_IDS_MARKER_RE.sub("", doc.extracted_text).rstrip()
                if not new_text and doc.read_quality is ReadQuality.OK:
                    new_text = f"Documento {doc.file_name}"
            docs_to_update.append((new_text, doc.id))
            continue

        if (
            doc.document_type in {DocumentType.NFE_XML, DocumentType.INVOICE}
            and _invoice_ids_for_document(conn, doc) == [invoice_id]
            and not has_refs
        ):
            docs_to_delete.append(doc.id)

    with conn:
        conn.execute("DELETE FROM audits WHERE invoice_id = ?", (invoice_id,))
        deleted = conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,)).rowcount
        for new_text, doc_id in docs_to_update:
            conn.execute(
                "UPDATE documents SET extracted_text = ? WHERE id = ?",
                (new_text, doc_id),
            )
        for doc_id in docs_to_delete:
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    return deleted > 0


def delete_empty_contract(conn: sqlite3.Connection, contract_id: str) -> bool:
    """Remove o contrato se ele não tiver documentos, regras ou cobranças vinculados."""
    if get_contract(conn, contract_id) is None:
        return False
    if (
        list_documents(conn, contract_id)
        or list_rules(conn, contract_id)
        or list_invoices(conn, contract_id)
        or list_payment_orders(conn, contract_id)
        or list_tax_certificates(conn, contract_id)
    ):
        return False
    with conn:
        conn.execute("DELETE FROM financial_summaries WHERE contract_id = ?", (contract_id,))
        deleted = conn.execute("DELETE FROM contracts WHERE id = ?", (contract_id,)).rowcount
    return deleted > 0
