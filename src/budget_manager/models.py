"""Domain model: the transaction record that every other module agrees on.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Kind(StrEnum):
    """Which way the money moves. An enum, not a free string, so typos can't exist."""

    EXPENSE = "expense"
    INCOME = "income"


#: Allowed categories (a controlled vocabulary). Lowercase, one word each.
#: Edit this tuple to change the vocabulary: validation, help text and the
#: ``categories`` command all read it from here.
CATEGORIES: tuple[str, ...] = (
    "food",
    "housing",
    "transport",
    "utilities",
    "health",
    "entertainment",
    "shopping",
    "education",
    "salary",
    "freelance",
    "gift",
    "other",
)

#: Supported ISO 4217 currency codes. All of them use 2 decimal places, which
#: the amount rule depends on (see ``validation.parse_amount``).
CURRENCIES: tuple[str, ...] = ("EUR", "USD", "GBP", "CHF")

#: Column order for the CSV file. This is the schema contract.
FIELDS: tuple[str, ...] = ("id", "date", "kind", "category", "amount", "currency")


@dataclass(frozen=True, slots=True)
class Transaction:
    """One validated money movement.

    ``frozen=True`` makes instances immutable: no code can change an amount
    after validation has approved it. ``amount`` is always positive. The
    direction comes from ``kind``, never from a minus sign.
    """

    id: str  # "T001"
    date: dt.date  # 2026-09-04
    kind: Kind  # Kind.EXPENSE
    category: str  # "food" (one of CATEGORIES)
    amount: Decimal  # Decimal("18.50"), > 0, at most 2 decimal places
    currency: str  # "EUR" (one of CURRENCIES)

    def to_record(self) -> dict[str, str]:
        """The canonical text form of this transaction, keyed by FIELDS.

        This is the exact inverse of ``validation.parse_transaction``:
        ``parse_transaction(t.to_record()) == t``. Storage writes it to files,
        and editing merges changes into it before re-validating.
        """
        return {
            "id": self.id,
            "date": self.date.isoformat(),
            "kind": self.kind.value,
            "category": self.category,
            "amount": f"{self.amount:.2f}",
            "currency": self.currency,
        }


@dataclass(frozen=True, slots=True)
class TransactionFilter:
    """What to select when listing or summarising. ``None`` means "any"."""

    kind: Kind | None = None
    category: str | None = None
    currency: str | None = None
    start: dt.date | None = None  # inclusive
    end: dt.date | None = None  # inclusive
