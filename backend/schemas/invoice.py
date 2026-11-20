"""Cobranca (nota fiscal ou fatura) e seus itens."""

from backend.schemas.base import StrictModel
from backend.schemas.cnpj import Cnpj
from backend.schemas.enums import InvoiceSource
from backend.schemas.periods import ReferencePeriod
from backend.schemas.types import EntityId, Money, NonEmptyStr, PlainDate, Quantity, UnitPrice


class BillingItem(StrictModel):
    """Item da cobranca; o valor unitario guarda a precisao original (ate 10 casas)."""

    description: NonEmptyStr
    quantity: Quantity
    unit: NonEmptyStr | None = None
    unit_value: UnitPrice
    total_value: Money


class Invoice(StrictModel):
    """Cobranca recebida; contract_id vazio indica vinculo ausente com contrato."""

    id: EntityId
    contract_id: EntityId | None = None
    supplier: NonEmptyStr
    supplier_cnpj: Cnpj
    number: NonEmptyStr
    issue_date: PlainDate
    reference_period: ReferencePeriod
    competence_inferred: bool = False
    total: Money
    measured_hours: Quantity | None = None
    source: InvoiceSource
    items: tuple[BillingItem, ...] = ()
