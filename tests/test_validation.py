"""Validation rules: each rule accepts what it should and rejects with a useful message."""

import datetime as dt
import unittest
from decimal import Decimal

from budget_manager.models import Kind, Transaction
from budget_manager.validation import (
    FieldError,
    ValidationError,
    check_changes,
    check_header,
    parse_amount,
    parse_category,
    parse_currency,
    parse_date,
    parse_filter,
    parse_id,
    parse_kind,
    parse_rows,
    parse_transaction,
)

VALID = {
    "id": "T001",
    "date": "2026-09-04",
    "kind": "expense",
    "category": "food",
    "amount": "18.50",
    "currency": "EUR",
}


class FieldRules(unittest.TestCase):
    def assertRejected(self, parser, raw, *fragments):
        with self.assertRaises(FieldError) as ctx:
            parser(raw)
        for fragment in fragments:
            self.assertIn(fragment, str(ctx.exception))

    def test_id(self):
        self.assertEqual(parse_id("T001"), "T001")
        self.assertEqual(parse_id("T1234"), "T1234")
        self.assertRejected(parse_id, "T01", "at least 3 digits", "'T01'")
        self.assertRejected(parse_id, "X001", "'X001'")

    def test_date(self):
        self.assertEqual(parse_date("2026-09-04"), dt.date(2026, 9, 4))
        self.assertEqual(parse_date("2028-02-29"), dt.date(2028, 2, 29))  # leap year
        self.assertRejected(parse_date, "04/09/2026", "YYYY-MM-DD")
        self.assertRejected(parse_date, "20260904", "YYYY-MM-DD")  # Python accepts it; the contract doesn't
        self.assertRejected(parse_date, "2026-02-30", "not a real calendar date")

    def test_kind(self):
        self.assertIs(parse_kind("income"), Kind.INCOME)
        self.assertRejected(parse_kind, "Expense", "did you mean 'expense'")
        self.assertRejected(parse_kind, "refund", "expense, income")

    def test_category(self):
        self.assertEqual(parse_category("food"), "food")
        self.assertRejected(parse_category, "FOOD", "did you mean 'food'")
        self.assertRejected(parse_category, "groceries", "use one of")

    def test_currency(self):
        self.assertEqual(parse_currency("EUR"), "EUR")
        self.assertRejected(parse_currency, "eur", "did you mean 'EUR'")
        self.assertRejected(parse_currency, "XYZ", "EUR, USD")

    def test_amount_accepts_exact_decimals(self):
        self.assertEqual(parse_amount("18.50"), Decimal("18.50"))
        self.assertEqual(parse_amount("7"), Decimal("7"))
        self.assertEqual(parse_amount("0.01"), Decimal("0.01"))

    def test_amount_rejections_say_what_to_do(self):
        self.assertRejected(parse_amount, "-12.00", "> 0", "'expense'")
        self.assertRejected(parse_amount, "0", "> 0")
        self.assertRejected(parse_amount, "0.00", "> 0")
        self.assertRejected(parse_amount, "18,50", "comma", "'.'")
        self.assertRejected(parse_amount, "1,250.00", "comma")
        self.assertRejected(parse_amount, "18.555", "more than 2 decimal places")
        self.assertRejected(parse_amount, "abc", "not a number")
        for sneaky in ("NaN", "Infinity", "1e3", "+5", "1_000"):
            self.assertRejected(parse_amount, sneaky)

    def test_nothing_is_silently_trimmed(self):
        self.assertRejected(parse_category, " food", "leading or trailing spaces")
        self.assertRejected(parse_amount, "", "required")
        self.assertRejected(parse_amount, None, "required")
        self.assertRejected(parse_amount, "   ", "required")


class RecordRules(unittest.TestCase):
    def test_valid_record_becomes_typed_transaction(self):
        t = parse_transaction(VALID)
        self.assertEqual(
            t, Transaction("T001", dt.date(2026, 9, 4), Kind.EXPENSE, "food", Decimal("18.50"), "EUR")
        )

    def test_text_form_round_trips(self):
        t = parse_transaction(VALID)
        self.assertEqual(t.to_record(), VALID)
        self.assertEqual(parse_transaction(t.to_record()), t)

    def test_edit_must_change_something(self):
        self.assertEqual(check_changes({"amount": "5", "kind": None}), {"amount": "5"})
        with self.assertRaises(ValidationError) as ctx:
            check_changes({"amount": None})
        self.assertIn("Nothing to change", str(ctx.exception))

    def test_all_field_errors_are_collected_and_located(self):
        bad = {**VALID, "amount": "-1", "currency": "eur"}
        with self.assertRaises(ValidationError) as ctx:
            parse_transaction(bad, location="Row 4")
        self.assertEqual(len(ctx.exception.errors), 2)
        self.assertTrue(all(e.startswith("Row 4: ") for e in ctx.exception.errors))

    def test_header(self):
        check_header(["id", "date", "kind", "category", "amount", "currency"])
        check_header(["currency", "amount", "category", "kind", "date", "id"])  # any order is fine
        for bad, fragment in (
            (None, "empty"),
            (["id", "date", "kind", "category", "amount"], "missing column(s): currency"),
            (["id", "date", "kind", "category", "amount", "currency", "note"], "unknown column(s): note"),
            (["id", "date", "kind", "category", "amount", "amount", "currency"], "repeats column(s): amount"),
        ):
            with self.assertRaises(ValidationError) as ctx:
                check_header(bad)
            self.assertIn(fragment, str(ctx.exception))

    def test_rows_duplicate_ids(self):
        rows = [(2, VALID), (3, {**VALID, "date": "2026-09-05"})]
        with self.assertRaises(ValidationError) as ctx:
            parse_rows(rows)
        self.assertEqual(
            ctx.exception.errors, ["Row 3: id 'T001' is a duplicate (first used on row 2)."]
        )

    def test_rows_all_or_nothing(self):
        rows = [(2, VALID), (3, {**VALID, "id": "T002", "amount": "0"})]
        with self.assertRaises(ValidationError):
            parse_rows(rows)  # one bad row: nothing is returned, not a partial list


class FilterRules(unittest.TestCase):
    def test_valid_filter(self):
        f = parse_filter(kind="expense", start="2026-09-01", end="2026-09-30")
        self.assertIs(f.kind, Kind.EXPENSE)
        self.assertEqual(f.end, dt.date(2026, 9, 30))
        self.assertIsNone(f.category)

    def test_typos_in_filters_are_errors_not_empty_results(self):
        with self.assertRaises(ValidationError) as ctx:
            parse_filter(category="fod", start="2026-10-05", end="2026-10-01")
        self.assertEqual(len(ctx.exception.errors), 2)
        self.assertIn("is after", ctx.exception.errors[1])


if __name__ == "__main__":
    unittest.main()
