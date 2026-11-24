"""File I/O: round-trips, the import boundary, atomic saves and backups."""

import datetime as dt
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from budget_manager import storage
from budget_manager.models import Kind, Transaction
from budget_manager.storage import StorageError
from budget_manager.validation import ValidationError

from . import DATA

HEADER = "id,date,kind,category,amount,currency\n"


class StorageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.ledger = self.dir / "budget.csv"

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, text, encoding="utf-8"):
        self.ledger.write_text(text, encoding=encoding)

    def test_missing_file_is_an_empty_ledger(self):
        self.assertEqual(storage.load_transactions(self.ledger), [])
        with self.assertRaises(StorageError):
            storage.load_transactions(self.ledger, missing_ok=False)

    def test_round_trip_preserves_every_value(self):
        original = [
            Transaction("T001", dt.date(2026, 9, 4), Kind.EXPENSE, "food", Decimal("18.5"), "EUR"),
            Transaction("T002", dt.date(2026, 9, 5), Kind.INCOME, "gift", Decimal("100"), "USD"),
        ]
        storage.save_transactions(self.ledger, original)
        self.assertEqual(storage.load_transactions(self.ledger), original)
        # Amounts are written with exactly 2 decimals.
        self.assertIn("T001,2026-09-04,expense,food,18.50,EUR", self.ledger.read_text())

    def test_save_leaves_no_temp_files(self):
        storage.save_transactions(self.ledger, [])
        self.assertEqual([p.name for p in self.dir.iterdir()], ["budget.csv"])

    def test_excel_byte_order_mark_is_accepted(self):
        self.write(HEADER + "T001,2026-09-04,expense,food,18.50,EUR\n", encoding="utf-8-sig")
        self.assertEqual(len(storage.load_transactions(self.ledger)), 1)

    def test_invalid_rows_report_spreadsheet_row_numbers(self):
        self.write(HEADER + "T001,2026-09-04,expense,food,18.50,EUR\nT002,2026-09-04,expense,food,-3,EUR\n")
        with self.assertRaises(ValidationError) as ctx:
            storage.load_transactions(self.ledger)
        self.assertIn("1 problem", ctx.exception.context)
        self.assertEqual(len(ctx.exception.errors), 1)
        self.assertTrue(ctx.exception.errors[0].startswith("Row 3: amount must be > 0"))

    def test_bad_header_and_empty_file(self):
        self.write("id,date,amount\n")
        with self.assertRaises(ValidationError):
            storage.load_transactions(self.ledger)
        self.write("")
        with self.assertRaises(ValidationError):
            storage.load_transactions(self.ledger)

    def test_non_utf8_file(self):
        self.ledger.write_bytes(HEADER.encode() + "T001,2026-09-04,expense,café,1.00,EUR\n".encode("latin-1"))
        with self.assertRaises(StorageError) as ctx:
            storage.load_transactions(self.ledger)
        self.assertIn("UTF-8", str(ctx.exception))

    def test_backup_copies_and_never_overwrites(self):
        self.write(HEADER)
        target = self.dir / "backup.csv"
        storage.backup_ledger(self.ledger, target)
        self.assertEqual(target.read_text(), HEADER)
        with self.assertRaises(StorageError) as ctx:
            storage.backup_ledger(self.ledger, target)
        self.assertIn("never overwrite", str(ctx.exception))

    def test_backup_of_missing_ledger(self):
        with self.assertRaises(StorageError):
            storage.backup_ledger(self.ledger, self.dir / "b.csv")


class SampleData(unittest.TestCase):
    """The shipped data files are part of the release, so they are tested too."""

    def test_sample_file_is_valid(self):
        transactions = storage.load_transactions(DATA / "sample_transactions.csv", missing_ok=False)
        self.assertEqual(len(transactions), 19)

    def test_invalid_file_triggers_every_rule(self):
        with self.assertRaises(ValidationError) as ctx:
            storage.load_transactions(DATA / "invalid_transactions.csv")
        errors = ctx.exception.errors
        # Row 2 is the only valid row; rows 3-19 each break exactly one rule.
        self.assertEqual([e.split(":")[0] for e in errors], [f"Row {n}" for n in range(3, 20)])


if __name__ == "__main__":
    unittest.main()
