"""Camada de serviços (casos de uso) que compõe leitura, agentes, motor e persistência."""

import re
import sqlite3
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
    TradutorResult,
    build_invoice_report_node,
    fetch_invoice_rules_node,
    link_invoice_items_node,
    run_fiscal_pdf_agent,
    run_invoice_motor_node,
    run_jurista_agent,
)
from backend.leitura import (
    DocxReadResult,
    PdfReadResult,
    UnsafeXmlError,
    import_invoices_csv,
    import_invoices_xlsx,
    parse_nfe,
    parse_nfse,
    read_docx,
    read_pdf,
)
from backend.motor import (
    RuleSelection,
    TimelineResult,
    apply_amendments,
    audit_invoice,
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
    list_invoices,
    list_reviews,
    list_rules,
    receive_document,
    record_audit_review,
    record_document_review,
    record_rule_review,
    save_amendment,
    save_audit,
    save_contract,
    save_document,
    save_invoice,
    save_rule,
)
from backend.schemas import (
    Amendment,
    AuditClassification,
    AuditResult,
    Contract,
    ContractRule,
    ContractStatus,
    Document,
    DocumentType,
    HumanReview,
    Invoice,
    InvoiceSource,
    ReadQuality,
    ReferencePeriod,
    ReviewDecision,
    ReviewStatus,
    ReviewTargetType,
    RuleRelation,
    RuleType,
    StrictModel,
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
) -> DocumentProcessingResult:
    """Lê PDF ou DOCX de contrato/aditivo, grava SHA-256, extrai e persiste regras pendentes."""
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
    for rule in jurista_result.rules:
        save_rule(conn, rule)

    warnings = _unique_messages((read_warning,), jurista_result.warnings)
    return DocumentProcessingResult(
        document=document,
        rules=jurista_result.rules,
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


def resolve_contract_link(
    contracts: Sequence[Contract], invoice: Invoice
) -> tuple[str | None, str | None]:
    """
    Resolve o vínculo da cobrança ao contrato por nº informado ou CNPJ único cadastrado.
    Em CNPJ ambíguo (> 1 contrato) ou ausente, mantém sem vínculo com aviso explícito.
    """
    if invoice.contract_id is not None:
        matched = next((item for item in contracts if item.id == invoice.contract_id), None)
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
_CONTRACT_EXPLICIT_NUM_RE = re.compile(
    r"(?i)\b(?:contrato|instrumento)(?:\s+particular)?\s+"
    r"(?:n[º°.]?|n[úu]mero|#)\s*([A-Z0-9][A-Z0-9./_-]{2,})"
)
_CONTRACT_CODE_RE = re.compile(r"(?i)\bcontrato\s+([A-Z]{1,6}[-_/][0-9][A-Z0-9./_-]*)")
_CNPJ_CANDIDATE_RE = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")
_CNPJ_ANY_TOKEN_RE = re.compile(
    r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?[0-9?X*]{2}(?![0-9A-Za-z?X*])",
    re.IGNORECASE,
)
_SUPPLIER_CNPJ_RE = re.compile(
    r"(?i)(?:contratad[ao]|fornecedor|prestador(?:a)?(?:\s+de\s+servi[çc]os)?)"
    r"[^\n]{0,80}?(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?[0-9?X*]{2}(?![0-9A-Za-z?X*]))"
)
_COMPANY_LINE_RE = re.compile(r"(?im)^\s*contratante\s*[:\-]\s*(.+?)\.?\s*$")
_SUPPLIER_LINE_RE = re.compile(r"(?im)^\s*contratad[ao]\s*[:\-]\s*(.+?)\s*$")
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
        parsed: _ParsedContractUpload | None = _read_contract_bytes(file_name, raw_bytes)
    except (OSError, ValueError):
        parsed = _ParsedContractUpload(
            read_quality=ReadQuality.NO_TEXT,
            full_text="",
            read_warning=f"Falha ao ler texto de '{file_name}'.",
        )

    if mode in {"CONTRACT", "AMENDMENT", "BILLING", "DOSSIER"}:
        return mode, parsed, None

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
        return "AMENDMENT", parsed, None
    if "dossie" in lowered or "dossiê" in lowered:
        return "DOSSIER", parsed, None
    if has_billing_filename:
        return ("DOSSIER" if has_clauses else "BILLING"), parsed, None
    if has_clauses and has_invoice_marker:
        return "DOSSIER", parsed, None
    if has_invoice_marker:
        return "BILLING", parsed, None
    header_slice = text.lstrip()[:350]
    if (
        header_slice
        and not header_slice.casefold().startswith("contrato ")
        and _AMENDMENT_HEADING_RE.search(header_slice)
    ):
        return "AMENDMENT", parsed, None
    return "CONTRACT", parsed, None


def _extract_contract_id_hint(text: str, file_name: str) -> str | None:
    for source in (file_name, text):
        match_ct = _CONTRACT_CT_RE.search(source)
        if match_ct is not None:
            return match_ct.group(1).replace("_", "-").upper()
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


def _extract_supplier_cnpj_info(text: str) -> tuple[str | None, str | None]:
    """Identifica o CNPJ da contratada/fornecedor sem confundir com o da contratante."""
    head = text[:2500]
    has_both_parties = bool(
        re.search(r"(?i)\bcontratante\b", head) and re.search(r"(?i)\bcontratad[ao]\b", head)
    )
    head_tokens = _CNPJ_ANY_TOKEN_RE.findall(head)
    if has_both_parties and len(head_tokens) >= 2:
        supplier_token = head_tokens[1]
        if is_valid_cnpj(supplier_token):
            return normalize_cnpj(supplier_token), None
        return None, supplier_token

    for match_sup in _SUPPLIER_CNPJ_RE.finditer(text):
        token = match_sup.group(1)
        if is_valid_cnpj(token):
            return normalize_cnpj(token), None
        return None, token

    for match in _CNPJ_CANDIDATE_RE.finditer(text):
        candidate = match.group(0)
        if is_valid_cnpj(candidate):
            return normalize_cnpj(candidate), None
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
    batch_warnings: list[str],
) -> None:
    if contract.supplier_cnpj != _PROVISIONAL_CNPJ:
        return
    if detected_cnpj is None or detected_cnpj == _PROVISIONAL_CNPJ:
        return
    upgraded = contract.model_copy(
        update={
            "supplier_cnpj": detected_cnpj,
            "company": company_name or contract.company,
            "supplier": supplier_name or contract.supplier,
        }
    )
    register_contract(conn, upgraded)
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
                batch_warnings=batch_warnings,
            )
            return selected_contract_id

    if file_cid is not None:
        existing_cid = get_contract(conn, file_cid)
        if existing_cid is not None:
            _maybe_upgrade_provisional_contract(
                conn,
                existing_cid,
                detected_cnpj=effective_cnpj,
                company_name=effective_company,
                supplier_name=effective_supplier,
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
        validity=ValidityPeriod(),
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
        by_type: dict[RuleType, list[ContractRule]] = {}
        for rule in extracted_rules:
            if rule.validity.start is not None:
                by_type.setdefault(rule.rule_type, []).append(rule)
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
    existing_amendments = tuple(
        item
        for item in _contract_amendments_for_context(list_amendments(conn), all_rules, documents)
        if item.affected_rule_id not in rejected_ids
        and (item.new_rule is None or item.new_rule.id not in rejected_ids)
    )
    already_linked_new_ids = {
        item.new_rule.id for item in existing_amendments if item.new_rule is not None
    }
    base_rules = tuple(
        rule
        for rule in all_rules
        if rule.id not in rejected_ids and rule.id not in already_linked_new_ids
    )
    timeline_rules = apply_amendments(base_rules, existing_amendments).rules

    for new_rule in extracted_rules:
        if new_rule.id in already_linked_new_ids or new_rule.validity.start is None:
            continue
        effective = new_rule.validity.start
        if new_rule.validity.end is not None and new_rule.validity.end < effective:
            continue
        candidates = [
            cand
            for cand in timeline_rules
            if cand.evidence.source_document_id != doc.id
            and cand.rule_type is new_rule.rule_type
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
        register_amendment(
            conn,
            Amendment(
                id=f"amend-auto-{uuid4().hex[:10]}",
                affected_rule_id=target.id,
                new_rule=None if relation is RuleRelation.REVOKED else new_rule,
                source_document_id=doc.id,
                effective_date=effective,
                relation=relation,
            ),
        )
        already_linked_new_ids.add(new_rule.id)


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
) -> BatchUploadResult:
    """Ingere múltiplos arquivos (.pdf, .docx, .xml, .csv, .xlsx) e audita automaticamente."""
    timestamp = received_at if received_at is not None else datetime.now(UTC)
    classified: list[
        tuple[int, str, bytes, _DetectedUploadKind, _ParsedContractUpload | None, str | None]
    ] = []
    for idx, (file_name, raw_bytes) in enumerate(files):
        kind, pre_parsed, warn = _classify_uploaded_file_with_content(file_name, raw_bytes, mode)
        classified.append((idx, file_name, raw_bytes, kind, pre_parsed, warn))

    if selected_contract_id is not None and get_contract(conn, selected_contract_id) is None:
        raise ValueError(f"contrato {selected_contract_id} não encontrado")

    auto_contracts: list[Contract] = []
    batch_warnings: list[str] = []
    active_contract_id = selected_contract_id

    parsed_docs, hints_by_id, batch_unique_cnpj = _collect_batch_hints(
        [(name, payload, kind, pre_parsed) for _, name, payload, kind, pre_parsed, _ in classified]
    )

    order_rank = {"CONTRACT": 0, "DOSSIER": 1, "AMENDMENT": 2, "BILLING": 3, "SKIPPED": 4}
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
                            for raw_inv in fiscal_res.invoices:
                                existing_target = get_contract(conn, resolved_cid)
                                if (
                                    existing_target is not None
                                    and existing_target.supplier_cnpj == _PROVISIONAL_CNPJ
                                    and raw_inv.supplier_cnpj != _PROVISIONAL_CNPJ
                                ):
                                    _maybe_upgrade_provisional_contract(
                                        conn,
                                        existing_target,
                                        detected_cnpj=raw_inv.supplier_cnpj,
                                        company_name=None,
                                        supplier_name=raw_inv.supplier,
                                        batch_warnings=batch_warnings,
                                    )
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

        # Processamento de cobranças (.xml, .csv, .xlsx ou fatura .pdf/.docx)
        doc_id = f"doc-{uuid4().hex[:10]}"
        suffix = Path(file_name).suffix.casefold()
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
            deleted_invoices += conn.execute(
                "DELETE FROM invoices WHERE id = ?",
                (inv_id,),
            ).rowcount
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
    ):
        return False
    with conn:
        deleted = conn.execute("DELETE FROM contracts WHERE id = ?", (contract_id,)).rowcount
    return deleted > 0
