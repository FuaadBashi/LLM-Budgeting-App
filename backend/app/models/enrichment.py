"""Merchant enrichment cache. Phase 11.

The table that makes an LLM affordable here. Keyed on the *normalised* merchant
description -- the same function duplicate detection uses -- so `TESCO STORES
3421` and `TESCO STORES 9982` resolve to one row. A merchant is asked about
once, ever; every later transaction from it is a database hit.

That turns a per-transaction cost into a per-new-merchant cost, which flattens
to almost nothing within a few weeks of use. It is the same resolve-once-cache-
forever shape bank-data providers use for merchant enrichment.

`source` matters more than it looks. A suggestion the user overrode is recorded
as theirs and outranks anything the model says later: the cache gets more
accurate with use, and correcting it is free.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampedUUID
from app.models.enums import SuggestionSource


class MerchantSuggestion(TimestampedUUID, Base):
    __tablename__ = "merchant_suggestions"
    __table_args__ = (
        Index("ix_merchant_suggestion_key", "fingerprint", unique=True),
    )

    #: `normalise_description(description)`. One row per merchant, not per row.
    fingerprint: Mapped[str] = mapped_column(String(160), nullable=False)
    #: A real description that produced this key, so the row is legible to a
    #: human reading the table.
    example: Mapped[str] = mapped_column(Text, nullable=False, default="")

    category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("categories.id", ondelete="CASCADE"), nullable=True
    )
    source: Mapped[SuggestionSource] = mapped_column(
        Enum(SuggestionSource, name="suggestion_source", native_enum=False),
        nullable=False,
        default=SuggestionSource.MODEL,
    )
    #: Which model said so, so a bad batch can be found and cleared.
    model: Mapped[str] = mapped_column(String(60), nullable=False, default="")
