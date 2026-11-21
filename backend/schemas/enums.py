"""Vocabulario canonico de estados e tipos (ver .regras/style.md, secao 10)."""

from collections.abc import Iterable
from enum import StrEnum


class AuditClassification(StrEnum):
    """Classificacao final de uma cobranca."""

    NO_DIVERGENCE = "NO_DIVERGENCE"
    DIVERGENCE = "DIVERGENCE"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    MISSING_DOCUMENTATION = "MISSING_DOCUMENTATION"

    @property
    def severity_rank(self) -> int:
        """Posicao na ordem de gravidade; 0 e o mais grave."""
        return CLASSIFICATION_SEVERITY_ORDER.index(self)


# Unico lugar onde a ordem de gravidade e definida: do mais grave ao menos grave.
CLASSIFICATION_SEVERITY_ORDER: tuple[AuditClassification, ...] = (
    AuditClassification.MISSING_DOCUMENTATION,
    AuditClassification.DIVERGENCE,
    AuditClassification.NEEDS_REVIEW,
    AuditClassification.NO_DIVERGENCE,
)


def most_severe(classifications: Iterable[AuditClassification]) -> AuditClassification:
    """Devolve a classificacao mais grave; levanta ValueError se a lista estiver vazia."""
    return min(classifications, key=lambda item: item.severity_rank)


class ReviewStatus(StrEnum):
    """Estado de revisao humana. So CONFIRMED e CORRECTED sao confiaveis."""

    PENDING_REVIEW = "PENDING_REVIEW"
    CONFIRMED = "CONFIRMED"
    CORRECTED = "CORRECTED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"

    @property
    def is_trusted(self) -> bool:
        """Indica se a regra passou por revisao humana que a aceitou."""
        return self in (ReviewStatus.CONFIRMED, ReviewStatus.CORRECTED)


class RuleRelation(StrEnum):
    """Relacao da regra com o aditivo ("substituida" e registrada como ALTERED)."""

    CREATED = "CREATED"
    ALTERED = "ALTERED"
    REVOKED = "REVOKED"


class RuleType(StrEnum):
    """Tipo da regra contratual."""

    MONTHLY_PRICE = "MONTHLY_PRICE"
    FRANCHISE = "FRANCHISE"
    OVERAGE_PRICE = "OVERAGE_PRICE"
    ADJUSTMENT = "ADJUSTMENT"
    EXTENSION = "EXTENSION"


class DocumentType(StrEnum):
    """Tipo do documento recebido."""

    CONTRACT = "CONTRACT"
    AMENDMENT = "AMENDMENT"
    INVOICE = "INVOICE"
    NFE_XML = "NFE_XML"
    SPREADSHEET = "SPREADSHEET"


class ReadQuality(StrEnum):
    """Qualidade da leitura do documento (PDF sem camada de texto nao e processado)."""

    OK = "OK"
    NO_TEXT = "NO_TEXT"


class ContractStatus(StrEnum):
    """Situacao do contrato."""

    ACTIVE = "ACTIVE"
    ENDED = "ENDED"
    CANCELED = "CANCELED"


class InvoiceSource(StrEnum):
    """Origem dos dados da cobranca."""

    PDF = "PDF"
    NFE_XML = "NFE_XML"
    SPREADSHEET = "SPREADSHEET"


class CheckResult(StrEnum):
    """Resultado de uma verificacao individual da auditoria."""

    OK = "OK"
    REVIEW = "REVIEW"
    DIVERGENCE = "DIVERGENCE"
    PENDING = "PENDING"

    @property
    def classification(self) -> AuditClassification:
        """Classificacao correspondente (unico lugar do mapeamento resultado -> classificacao)."""
        return _CHECK_RESULT_CLASSIFICATION[self]


_CHECK_RESULT_CLASSIFICATION: dict[CheckResult, AuditClassification] = {
    CheckResult.OK: AuditClassification.NO_DIVERGENCE,
    CheckResult.REVIEW: AuditClassification.NEEDS_REVIEW,
    CheckResult.DIVERGENCE: AuditClassification.DIVERGENCE,
    CheckResult.PENDING: AuditClassification.MISSING_DOCUMENTATION,
}


class DivergenceDirection(StrEnum):
    """Direcao ou situacao da comparacao quando ha divergencia ou revisao."""

    OVERCHARGE = "OVERCHARGE"
    UNDERCHARGE = "UNDERCHARGE"
    POSSIBLE_DIVERGENCE = "POSSIBLE_DIVERGENCE"
    UNVERIFIABLE = "UNVERIFIABLE"


class ReviewTargetType(StrEnum):
    """Tipo do objeto alvo de uma revisao humana."""

    RULE = "RULE"
    AMENDMENT = "AMENDMENT"
    DOCUMENT = "DOCUMENT"
    AUDIT_RESULT = "AUDIT_RESULT"


class ReviewDecision(StrEnum):
    """Decisao tomada pela pessoa revisora."""

    CONFIRM = "CONFIRM"
    CORRECT = "CORRECT"
    REJECT = "REJECT"
