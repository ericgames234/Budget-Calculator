"""Business logic, tested with plain lists: no files, no prompts.

These tests can be this simple because services.py does no I/O.
"""

import datetime as dt
import unittest
from decimal import Decimal

from budget_manager import services
from budget_manager.models import Kind, Transaction, TransactionFilter
from budget_manager.services import TransactionNotFound
from budget_manager.validation import ValidationError


def txn(id, date, kind, category, amount, currency="EUR"):
    return Transaction(id, dt.date.fromisoformat(date), Kind(kind), category, Decimal(amount), currency)


LEDGER = [
    txn("T001", "2026-09-01", "income", "salary", "2000.00"),
    txn("T002", "2026-09-02", "expense", "food", "0.10"),
    txn("T003", "2026-09-03", "expense", "food", "0.20"),
    txn("T004", "2026-09-15", "expense", "housing", "800.00"),
    txn("T005", "2026-10-01", "expense", "transport", "50.00", "USD"),
]
SNAPSHOT = list(LEDGER)


class Immutability(unittest.TestCase):
    def tearDown(self):
        self.assertEqual(LEDGER, SNAPSHOT, "services must never modify the list passed in")


class AddTransaction(Immutability):
    def test_assigns_next_id_and_returns_new_list(self):
        updated, new = services.add_transaction(
            LEDGER, date="2026-10-02", kind="expense", category="food", amount="9.99", currency="EUR"
        )
        self.assertEqual(new.id, "T006")
        self.assertEqual(updated, [*LEDGER, new])

    def test_new_ids_never_collide(self):
        gappy = [LEDGER[0], LEDGER[4]]  # T001, T005: T002-T004 were deleted
        self.assertEqual(services.next_id(gappy), "T006")
        self.assertEqual(services.next_id([]), "T001")

    def test_invalid_input_raises_with_every_problem(self):
        with self.assertRaises(ValidationError) as ctx:
            services.add_transaction(
                LEDGER, date="yesterday", kind="spend", category="food", amount="5", currency="EUR"
            )
        self.assertEqual(len(ctx.exception.errors), 2)


class FindEditDelete(Immutability):
    def test_find(self):
        self.assertEqual(services.find_transaction(LEDGER, "T003"), LEDGER[2])
        with self.assertRaises(TransactionNotFound) as ctx:
            services.find_transaction(LEDGER, "T099")
        self.assertEqual(str(ctx.exception), "No transaction has id 'T099'.")

    def test_edit_changes_only_given_fields_and_keeps_id_and_position(self):
        updated, old, new = services.edit_transaction(LEDGER, "T003", amount="20.00", category="health")
        self.assertEqual(old, LEDGER[2])
        self.assertEqual(new, txn("T003", "2026-09-03", "expense", "health", "20.00"))
        self.assertEqual(updated[2], new)
        self.assertEqual(updated[:2] + updated[3:], LEDGER[:2] + LEDGER[3:])
        self.assertEqual(services.changed_fields(old, new), ["category", "amount"])

    def test_edit_uses_the_same_rules_as_add(self):
        with self.assertRaises(ValidationError) as ctx:
            services.edit_transaction(LEDGER, "T003", amount="-20", currency="eur")
        self.assertEqual(len(ctx.exception.errors), 2)
        self.assertIn("amount must be > 0", ctx.exception.errors[0])

    def test_edit_needs_at_least_one_change_and_an_existing_id(self):
        with self.assertRaises(ValidationError) as ctx:
            services.edit_transaction(LEDGER, "T003")
        self.assertIn("Nothing to change", ctx.exception.errors[0])
        with self.assertRaises(TransactionNotFound):
            services.edit_transaction(LEDGER, "T099", amount="1")

    def test_edit_with_identical_values_reports_no_changed_fields(self):
        _, old, new = services.edit_transaction(LEDGER, "T002", amount="0.10")
        self.assertEqual(services.changed_fields(old, new), [])

    def test_delete(self):
        updated, removed = services.delete_transaction(LEDGER, "T002")
        self.assertEqual(removed, LEDGER[1])
        self.assertEqual([t.id for t in updated], ["T001", "T003", "T004", "T005"])
        self.assertEqual(services.next_id(updated), "T006", "a deleted id in the middle is not reused")
        with self.assertRaises(TransactionNotFound):
            services.delete_transaction(LEDGER, "T099")


class FilterAndOrder(Immutability):
    def ids(self, **criteria):
        return [t.id for t in services.filter_transactions(LEDGER, TransactionFilter(**criteria))]

    def test_no_criteria_returns_everything(self):
        self.assertEqual(self.ids(), ["T001", "T002", "T003", "T004", "T005"])

    def test_each_criterion(self):
        self.assertEqual(self.ids(kind=Kind.INCOME), ["T001"])
        self.assertEqual(self.ids(category="food"), ["T002", "T003"])
        self.assertEqual(self.ids(currency="USD"), ["T005"])

    def test_date_range_is_inclusive(self):
        self.assertEqual(
            self.ids(start=dt.date(2026, 9, 2), end=dt.date(2026, 9, 15)), ["T002", "T003", "T004"]
        )

    def test_criteria_combine_with_and(self):
        self.assertEqual(self.ids(kind=Kind.EXPENSE, currency="EUR", start=dt.date(2026, 9, 3)), ["T003", "T004"])

    def test_chronological_compares_ids_as_numbers(self):
        same_day = [
            txn("T1000", "2026-09-01", "expense", "food", "1"),
            txn("T999", "2026-09-01", "expense", "food", "1"),
            txn("T001", "2026-08-31", "expense", "food", "1"),
        ]
        self.assertEqual([t.id for t in services.chronological(same_day)], ["T001", "T999", "T1000"])


class Summarize(Immutability):
    def test_totals_are_exact(self):
        eur = services.summarize(LEDGER)[0]
        # With floats, 0.10 + 0.20 == 0.30000000000000004. Decimal gives exactly 0.30.
        food = next(c for c in eur.expenses_by_category if c.category == "food")
        self.assertEqual(food.amount, Decimal("0.30"))
        self.assertEqual(eur.expenses, Decimal("800.30"))
        self.assertEqual(eur.income, Decimal("2000.00"))
        self.assertEqual(eur.net, Decimal("1199.70"))

    def test_currencies_are_never_added_together(self):
        summaries = services.summarize(LEDGER)
        self.assertEqual([s.currency for s in summaries], ["EUR", "USD"])
        usd = summaries[1]
        self.assertEqual((usd.income, usd.expenses, usd.transaction_count), (Decimal("0"), Decimal("50.00"), 1))

    def test_breakdown_is_largest_first_with_shares(self):
        eur = services.summarize(LEDGER)[0]
        self.assertEqual([c.category for c in eur.expenses_by_category], ["housing", "food"])
        self.assertEqual(round(sum(c.share for c in eur.expenses_by_category), 6), Decimal("100"))
        self.assertEqual(round(eur.expenses_by_category[0].share, 2), Decimal("99.96"))

    def test_empty(self):
        self.assertEqual(services.summarize([]), [])


if __name__ == "__main__":
    unittest.main()
