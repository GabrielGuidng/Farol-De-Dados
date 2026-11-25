"""Testes de verificação de ancoragem: cenários A-01, A-02, A-03 e A-04 (Etapa 5.3)."""

from datetime import date
from decimal import Decimal

from backend.agentes import AnchoringIssueCode, verify_rule_anchoring
from backend.schemas import ReviewStatus, RuleType


def test_anchoring_passes_with_normalized_spaces_and_quotes() -> None:
    document = "Cláusula 1:\n\tO   valor mensal  será “R$ 10.500,00”   a partir de maio."
    result = verify_rule_anchoring(
        rule_type=RuleType.MONTHLY_PRICE,
        literal_excerpt='O valor mensal será "R$ 10.500,00" a partir de maio.',
        document_text=document,
        new_value=Decimal("10500.00"),
    )
    assert result.review_status is ReviewStatus.PENDING_REVIEW
    assert result.verification_score == Decimal("1.00")
    assert result.low_confidence is False
    assert result.issues == ()


def test_a01_nonexistent_excerpt_or_wrong_page_is_inconclusive() -> None:
    pages = {1: "O valor mensal é de R$ 10.000,00.", 2: "Franquia de 40 horas."}
    missing_text = verify_rule_anchoring(
        rule_type=RuleType.MONTHLY_PRICE,
        literal_excerpt="O valor mensal é de R$ 10.500,00.",
        document_text=pages[1],
        new_value=Decimal("10500.00"),
    )
    assert missing_text.review_status is ReviewStatus.INCONCLUSIVE
    assert AnchoringIssueCode.EXCERPT_NOT_FOUND in missing_text.issues
    assert missing_text.verification_score < Decimal("0.85")

    wrong_page = verify_rule_anchoring(
        rule_type=RuleType.FRANCHISE,
        literal_excerpt="Franquia de 40 horas.",
        document_text="\n".join(pages.values()),
        quantity=Decimal("40"),
        page=1,
        pages_by_number=pages,
    )
    assert wrong_page.review_status is ReviewStatus.INCONCLUSIVE
    assert AnchoringIssueCode.EXCERPT_NOT_FOUND in wrong_page.issues


def test_a02_value_not_in_excerpt_is_inconclusive() -> None:
    doc = "O valor mensal do contrato permanece reajustado conforme cláusula."
    result = verify_rule_anchoring(
        rule_type=RuleType.MONTHLY_PRICE,
        literal_excerpt=doc,
        document_text=doc,
        new_value=Decimal("10800.00"),
    )
    assert result.review_status is ReviewStatus.INCONCLUSIVE
    assert AnchoringIssueCode.VALUE_NOT_IN_EXCERPT in result.issues

    franchise_fail = verify_rule_anchoring(
        rule_type=RuleType.FRANCHISE,
        literal_excerpt="Franquia de 40 horas.",
        document_text="Franquia de 40 horas.",
        quantity=Decimal("50"),
    )
    assert AnchoringIssueCode.VALUE_NOT_IN_EXCERPT in franchise_fail.issues

    adjustment_ok = verify_rule_anchoring(
        rule_type=RuleType.ADJUSTMENT,
        literal_excerpt="Reajuste anual pelo índice IPCA.",
        document_text="Reajuste anual pelo índice IPCA.",
        adjustment_index="IPCA",
    )
    assert adjustment_ok.review_status is ReviewStatus.PENDING_REVIEW

    extension_ok = verify_rule_anchoring(
        rule_type=RuleType.EXTENSION,
        literal_excerpt="Prorroga a vigência até 31/12/2027.",
        document_text="Prorroga a vigência até 31/12/2027.",
        validity_end=date(2027, 12, 31),
    )
    assert extension_ok.review_status is ReviewStatus.PENDING_REVIEW


def test_a03_new_value_equal_to_previous_value_is_inconclusive() -> None:
    excerpt = "O valor mensal passa de R$ 10.000,00 para R$ 10.500,00."
    result = verify_rule_anchoring(
        rule_type=RuleType.MONTHLY_PRICE,
        literal_excerpt=excerpt,
        document_text=excerpt,
        new_value=Decimal("10500.00"),
        previous_value=Decimal("10500.00"),
    )
    assert result.review_status is ReviewStatus.INCONCLUSIVE
    assert result.issues == (AnchoringIssueCode.NEW_EQUALS_PREVIOUS,)
    assert result.verification_score == Decimal("0.75")
    assert result.low_confidence is True


def test_a04_capturing_previous_value_from_transition_is_inconclusive() -> None:
    excerpt = "O valor mensal passa de R$ 10.000,00 para R$ 10.800,00."
    wrong = verify_rule_anchoring(
        rule_type=RuleType.MONTHLY_PRICE,
        literal_excerpt=excerpt,
        document_text=excerpt,
        new_value=Decimal("10000.00"),
    )
    assert wrong.review_status is ReviewStatus.INCONCLUSIVE
    assert AnchoringIssueCode.EXTRACTED_PREVIOUS_VALUE in wrong.issues

    antes_excerpt = "Antes de R$ 10.000,00; o novo valor mensal é R$ 10.800,00."
    wrong_antes = verify_rule_anchoring(
        rule_type=RuleType.MONTHLY_PRICE,
        literal_excerpt=antes_excerpt,
        document_text=antes_excerpt,
        new_value=Decimal("10000.00"),
    )
    assert wrong_antes.review_status is ReviewStatus.INCONCLUSIVE
    assert AnchoringIssueCode.EXTRACTED_PREVIOUS_VALUE in wrong_antes.issues

    correct = verify_rule_anchoring(
        rule_type=RuleType.MONTHLY_PRICE,
        literal_excerpt=excerpt,
        document_text=excerpt,
        new_value=Decimal("10800.00"),
        previous_value=Decimal("10000.00"),
    )
    assert correct.review_status is ReviewStatus.PENDING_REVIEW
    assert correct.verification_score == Decimal("1.00")
