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
    raw_supplier_cnpj: str | None = None
    supplier_legal_nature: str | None = None
    number: NonEmptyStr
    issue_date: PlainDate
    attestation_date: PlainDate | None = None
    reference_period: ReferencePeriod
    competence_inferred: bool = False
    total: Money
    inss_withheld: Money | None = None
    iss_withheld: Money | None = None
    net_total: Money | None = None
    service_description: str | None = None
    measured_hours: Quantity | None = None
    expected_glosa: Money | None = None
    source: InvoiceSource
    items: tuple[BillingItem, ...] = ()
