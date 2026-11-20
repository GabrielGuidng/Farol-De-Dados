"""Evidencia documental: a prova de onde uma regra veio."""

from typing import Self

from pydantic import Field, computed_field, model_validator

from backend.schemas.base import StrictModel
from backend.schemas.types import EntityId, NonEmptyStr


class Evidence(StrictModel):
    """Documento de origem, localizacao (pagina, ou clausula e paragrafo) e trecho literal."""

    source_document_id: EntityId
    page: int | None = Field(default=None, ge=1)
    clause: NonEmptyStr | None = None
    paragraph: NonEmptyStr | None = None
    literal_excerpt: NonEmptyStr

    @model_validator(mode="after")
    def _require_locator(self) -> Self:
        if self.page is None and (self.clause is None or self.paragraph is None):
            raise ValueError("informe a pagina ou, sem pagina, a clausula e o paragrafo")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def without_page(self) -> bool:
        """Indicador de "sem pagina" (DOCX sem conversao para PDF)."""
        return self.page is None
