"""Camada de serviços (casos de uso) que compõe leitura, agentes, motor e persistência."""

import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from backend.agentes import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    AnchoringCheckResult,
    AuditReport,
    ExtractionMetadata,
    FiscalPdfInput,
    InvoiceWorkflowState,
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
    UnsafeXmlError,
    import_invoices_csv,
    import_invoices_xlsx,
    parse_nfe,
    parse_nfse,
    read_docx,
    read_pdf,
)
from backend.motor import apply_amendments, audit_invoice
from backend.persistencia import (
    StoredAudit,
    get_audit,
    get_contract,
    get_document,
    get_invoice,
    get_rule,
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
    AuditResult,
    Contract,
    ContractRule,
    Document,
    DocumentType,
    HumanReview,
    Invoice,
    ReadQuality,
    ReviewDecision,
    ReviewStatus,
    ReviewTargetType,
    StrictModel,
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

    if suffix == ".pdf":
        jurista_input = JuristaInput.from_pdf(
            document.id,
            contract_id,
            pdf_result,
            file_hash=document.file_hash,
            is_amendment=is_amendment,
        )
    else:
        jurista_input = JuristaInput.from_docx(
            document.id,
            contract_id,
            docx_result,
            file_hash=document.file_hash,
            is_amendment=is_amendment,
        )

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
    Recebe arquivo de cobrança (XML, CSV, XLSX ou PDF), grava SHA-256, extrai cobranças,
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
    elif suffix == ".pdf":
        if client is None:
            raise ValueError("importação de cobrança em PDF exige LLMClient configurado")
        pdf_result = read_pdf(content)
        if pdf_result.warning is not None:
            warnings_list.append(pdf_result.warning)
        document = receive_document(
            document_id=document_id,
            document_type=DocumentType.INVOICE,
            file_name=file_name,
            content=content,
            received_at=timestamp,
            read_quality=pdf_result.read_quality,
            extracted_text=pdf_result.full_text,
            reviewed=False,
            conn=conn,
        )
        fiscal_result = run_fiscal_pdf_agent(
            FiscalPdfInput.from_pdf(document.id, pdf_result, file_hash=document.file_hash),
            client,
            extracted_at=timestamp,
        )
        warnings_list.extend(fiscal_result.warnings)
        parsed_invoices = () if fiscal_result.invoice is None else (fiscal_result.invoice,)
    else:
        raise ValueError(f"formato não suportado para cobrança: {file_name}")

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
    if len(distinct_contracts) == 1:
        sole_contract_id = next(iter(distinct_contracts))
        document = document.model_copy(update={"contract_id": sole_contract_id})
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
            documents = list_documents(conn, contract.id)
            other_invoices = tuple(
                item for item in list_invoices(conn, contract.id) if item.id != invoice.id
            )
            contract_amendments = _contract_amendments_for_context(
                list_amendments(conn), all_rules, documents
            )
            amendment_new_rule_ids = {
                item.new_rule.id for item in contract_amendments if item.new_rule is not None
            }
            base_rules = tuple(rule for rule in all_rules if rule.id not in amendment_new_rule_ids)
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
