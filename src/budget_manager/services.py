"""Budget logic: add, find, edit, delete, filter, order, summarise.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from .models import FIELDS, Kind, Transaction, TransactionFilter
from .validation import check_changes, parse_transaction

ZERO = Decimal("0.00")


class TransactionNotFound(LookupError):
    """No transaction has the requested id."""

    def __init__(self, txn_id: str) -> None:
        self.txn_id = txn_id
        super().__init__(f"No transaction has id {txn_id!r}.")


# --- ids and lookup -----------------------------------------------------------


def next_id(transactions: Sequence[Transaction]) -> str:
    """Return one more than the highest existing id number.

    Using highest + 1 instead of count + 1 means a new id can never collide
    with an existing one, even after rows are deleted.
    """
    highest = max((int(t.id[1:]) for t in transactions), default=0)
    return f"T{highest + 1:03d}"


def _index_of(transactions: Sequence[Transaction], txn_id: str) -> int:
    for index, t in enumerate(transactions):
        if t.id == txn_id:
            return index
    raise TransactionNotFound(txn_id)


def find_transaction(transactions: Sequence[Transaction], txn_id: str) -> Transaction:
    """Return the transaction with this id, or raise ``TransactionNotFound``."""
    return transactions[_index_of(transactions, txn_id)]


# --- changes ------------------------------------------------------------------


def add_transaction(
    transactions: Sequence[Transaction],
    *,
    date: str,
    kind: str,
    category: str,
    amount: str,
    currency: str,
) -> tuple[list[Transaction], Transaction]:
    """Validate raw input and append it as a new transaction.

    This is the construction boundary: raw text goes in, and only a fully
    validated ``Transaction`` comes out. Raises ``ValidationError`` listing
    every problem. Returns ``(new_list, added)``.
    """
    raw = {
        "id": next_id(transactions),
        "date": date,
        "kind": kind,
        "category": category,
        "amount": amount,
        "currency": currency,
    }
    new = parse_transaction(raw)
    return [*transactions, new], new


def edit_transaction(
    transactions: Sequence[Transaction],
    txn_id: str,
    *,
    date: str | None = None,
    kind: str | None = None,
    category: str | None = None,
    amount: str | None = None,
    currency: str | None = None,
) -> tuple[list[Transaction], Transaction, Transaction]:
    """Replace some fields of an existing transaction. ``None`` means "keep".

    The new values are merged into the current record's text form and the
    whole record is validated again with ``parse_transaction``, the same
    boundary as ``add``. So an edit can't produce anything ``add`` would
    reject. The id and the position in the list never change.

    Returns ``(new_list, old, new)``. ``old == new`` if the values given
    were the same as before.
    """
    changes = check_changes(
        {"date": date, "kind": kind, "category": category, "amount": amount, "currency": currency}
    )
    index = _index_of(transactions, txn_id)
    old = transactions[index]
    new = parse_transaction({**old.to_record(), **changes})
    updated = list(transactions)
    updated[index] = new
    return updated, old, new


def delete_transaction(
    transactions: Sequence[Transaction], txn_id: str
) -> tuple[list[Transaction], Transaction]:
    """Remove one transaction. Returns ``(new_list, removed)``."""
    index = _index_of(transactions, txn_id)
    return [*transactions[:index], *transactions[index + 1 :]], transactions[index]


def changed_fields(old: Transaction, new: Transaction) -> list[str]:
    """Names of the fields whose values differ, in schema order."""
    before, after = old.to_record(), new.to_record()
    return [field for field in FIELDS if before[field] != after[field]]


# --- queries ------------------------------------------------------------------


def _matches(t: Transaction, f: TransactionFilter) -> bool:
    return (
        (f.kind is None or t.kind is f.kind)
        and (f.category is None or t.category == f.category)
        and (f.currency is None or t.currency == f.currency)
        and (f.start is None or t.date >= f.start)
        and (f.end is None or t.date <= f.end)
    )


def filter_transactions(
    transactions: Sequence[Transaction], criteria: TransactionFilter
) -> list[Transaction]:
    """Return the transactions that match every given criterion, in their original order."""
    return [t for t in transactions if _matches(t, criteria)]


def chronological(transactions: Sequence[Transaction]) -> list[Transaction]:
    """Oldest first; same-day transactions in the order they were recorded.

    Ids are compared as numbers, so T1000 comes after T999 (as text it would sort first).
    """
    return sorted(transactions, key=lambda t: (t.date, int(t.id[1:])))


@dataclass(frozen=True, slots=True)
class CategoryTotal:
    category: str
    amount: Decimal
    share: Decimal  # percentage of the currency's total expenses, 0-100


@dataclass(frozen=True, slots=True)
class CurrencySummary:
    """Totals for one currency. Different currencies are never added together."""

    currency: str
    transaction_count: int
    income: Decimal
    expenses: Decimal
    expenses_by_category: tuple[CategoryTotal, ...]  # largest first

    @property
    def net(self) -> Decimal:
        return self.income - self.expenses


def summarize(transactions: Sequence[Transaction]) -> list[CurrencySummary]:
    """Total income, expenses, net and expense breakdown, one summary per currency.

    Adding EUR and USD together would give a number that means nothing, and
    converting them needs exchange rates this tool doesn't have. So each
    currency is reported on its own instead of guessing.
    """
    by_currency: dict[str, list[Transaction]] = defaultdict(list)
    for t in transactions:
        by_currency[t.currency].append(t)

    summaries = []
    for currency in sorted(by_currency):
        group = by_currency[currency]
        income = sum((t.amount for t in group if t.kind is Kind.INCOME), ZERO)
        expenses = sum((t.amount for t in group if t.kind is Kind.EXPENSE), ZERO)

        per_category: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for t in group:
            if t.kind is Kind.EXPENSE:
                per_category[t.category] += t.amount
        breakdown = tuple(
            CategoryTotal(category, amount, amount / expenses * 100)
            for category, amount in sorted(per_category.items(), key=lambda item: (-item[1], item[0]))
        )

        summaries.append(CurrencySummary(currency, len(group), income, expenses, breakdown))
    return summaries
