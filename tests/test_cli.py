"""End-to-end: drive the real CLI against real files.

``main()`` is called in-process with fake stdout/stderr and a scripted
``input_fn``. The last class also starts a real ``python -m budget_manager``
subprocess, exactly as a user would run it.
"""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from budget_manager.cli import main
from budget_manager.storage import StorageError

from . import DATA, SRC


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.ledger = self.dir / "budget.csv"

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *argv, inputs=()):
        out, err = io.StringIO(), io.StringIO()
        answers = iter(inputs)

        def fake_input(prompt):
            out.write(prompt)
            try:
                return next(answers)
            except StopIteration:
                raise EOFError from None

        code = main(["--file", str(self.ledger), *argv], input_fn=fake_input, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def test_add_list_summary_workflow(self):
        self.assertEqual(self.run_cli("add", "income", "salary", "2000", "--date", "2026-09-01")[0], 0)
        self.assertEqual(self.run_cli("add", "expense", "food", "18.50", "--date", "2026-09-04")[0], 0)
        code, out, _ = self.run_cli("add", "expense", "food", "1.50", "--date", "2026-09-02")
        self.assertIn("Added T003", out)

        code, out, _ = self.run_cli("list", "--category", "food")
        self.assertEqual(code, 0)
        self.assertLess(out.index("T003"), out.index("T002"), "list is sorted by date")
        self.assertIn("2 transactions", out)

        code, out, _ = self.run_cli("summary")
        self.assertIn("Expenses           20.00", out)
        self.assertIn("Net             1,980.00", out)

    def test_rejected_add_changes_nothing(self):
        self.run_cli("add", "expense", "food", "5", "--date", "2026-09-01")
        before = self.ledger.read_bytes()
        code, out, err = self.run_cli("add", "expense", "Food", "-5")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("did you mean 'food'", err)
        self.assertIn("amount must be > 0", err)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_invalid_ledger_blocks_every_command(self):
        self.ledger.write_text("id,date,kind,category,amount,currency\nT001,2026-09-01,expense,food,abc,EUR\n")
        before = self.ledger.read_bytes()
        for argv in (("list",), ("summary",), ("add", "expense", "food", "1")):
            code, _, err = self.run_cli(*argv)
            self.assertEqual(code, 1)
            self.assertIn("Row 2: amount 'abc' is not a number", err)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_bad_filter_is_an_error(self):
        code, _, err = self.run_cli("list", "--kind", "spend")
        self.assertEqual(code, 1)
        self.assertIn("kind 'spend'", err)

    def test_validate_command(self):
        code, out, _ = self.run_cli("validate", str(DATA / "sample_transactions.csv"))
        self.assertEqual((code, out.strip()), (0, f"OK: {DATA / 'sample_transactions.csv'} is valid (19 transactions)."))
        code, _, err = self.run_cli("validate", str(DATA / "invalid_transactions.csv"))
        self.assertEqual(code, 1)
        self.assertIn("has 17 problems", err)
        code, _, err = self.run_cli("validate", str(self.dir / "nope.csv"))
        self.assertEqual(code, 1)
        self.assertIn("File not found", err)

    def test_backup_command(self):
        self.run_cli("add", "expense", "food", "5")
        target = self.dir / "copy.csv"
        self.assertEqual(self.run_cli("backup", str(target))[0], 0)
        code, _, err = self.run_cli("backup", str(target))
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

    def seed(self):
        self.run_cli("add", "expense", "food", "185.00", "--date", "2026-09-04")  # T001: typo, meant 18.50
        self.run_cli("add", "income", "salary", "2000", "--date", "2026-09-01")  # T002

    def test_edit_fixes_a_mis_input(self):
        self.seed()
        code, out, _ = self.run_cli("edit", "T001", "--amount", "18.50")
        self.assertEqual(code, 0)
        self.assertIn("amount: 185.00 -> 18.50", out)
        self.assertIn("T001,2026-09-04,expense,food,18.50,EUR", self.ledger.read_text())

    def test_rejected_edit_changes_nothing(self):
        self.seed()
        before = self.ledger.read_bytes()
        for argv, fragment in (
            (("edit", "T001", "--amount", "18,50"), "contains a comma"),
            (("edit", "T001"), "Nothing to change"),
            (("edit", "T099", "--amount", "1"), "No transaction has id 'T099'. Run 'list'"),
        ):
            code, _, err = self.run_cli(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn(fragment, err)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_edit_with_same_value_does_not_rewrite(self):
        self.seed()
        code, out, _ = self.run_cli("edit", "T001", "--amount", "185")
        self.assertEqual(code, 0)
        self.assertIn("No changes", out)

    def test_delete_asks_first(self):
        self.seed()
        before = self.ledger.read_bytes()
        for answers in (["n"], [""], []):  # no, Enter, end of input: all mean "don't delete"
            code, out, _ = self.run_cli("delete", "T001", inputs=answers)
            self.assertEqual(code, 1)
            self.assertIn("Cancelled", out)
        self.assertEqual(self.ledger.read_bytes(), before)

        code, out, _ = self.run_cli("delete", "T001", inputs=["y"])
        self.assertEqual((code, "Deleted T001" in out), (0, True))
        code, out, _ = self.run_cli("delete", "T002", "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(self.ledger.read_text(), "id,date,kind,category,amount,currency\n")

    def test_interactive_edit_and_delete(self):
        self.seed()
        code, out, err = self.run_cli(
            inputs=[
                "e", "T099",                      # unknown id: asked again
                "T001", "", "", "18.50", "", "",  # keep everything except the amount
                "d", "T002", "y",
                "q",
            ]
        )
        self.assertEqual(code, 0, err)
        self.assertIn("No transaction has id 'T099'", out)
        self.assertIn("Kind (expense/income) [expense]:", out)
        self.assertIn("amount: 185.00 -> 18.50", out)
        self.assertIn("Deleted T002", out)
        self.assertEqual(
            self.ledger.read_text(),
            "id,date,kind,category,amount,currency\nT001,2026-09-04,expense,food,18.50,EUR\n",
        )

    def test_interactive_error_returns_to_menu(self):
        self.seed()
        disk_full = StorageError(f"Could not save {self.ledger}: No space left on device.")
        with mock.patch("budget_manager.storage.save_transactions", side_effect=disk_full):
            code, out, err = self.run_cli(inputs=["d", "T002", "y", "l", "q"])
        self.assertEqual(code, 0)
        self.assertIn("Could not save", err)
        self.assertNotIn("Deleted T002", out)
        self.assertIn("2 transactions", out, "the menu kept working, and the failed delete removed nothing")
        self.assertIn("Goodbye.", out)

    def test_interactive_session_reprompts_until_valid(self):
        code, out, _ = self.run_cli(
            inputs=[
                "a",          # menu: add
                "Expense",    # rejected: case
                "expense",
                "?",          # lists categories
                "food",
                "18,50",      # rejected: comma
                "18.50",
                "2026-02-30", # rejected: not a date
                "2026-09-04",
                "",           # currency: accept default [EUR]
                "l",
                "s",
                "q",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("did you mean 'expense'", out)
        self.assertIn("categories: food, housing", out)
        self.assertIn("contains a comma", out)
        self.assertIn("not a real calendar date", out)
        self.assertIn("Added T001  2026-09-04  expense food  18.50 EUR", out)
        self.assertIn("Goodbye.", out)
        self.assertIn("T001,2026-09-04,expense,food,18.50,EUR", self.ledger.read_text())

    def test_interactive_end_of_input_exits_cleanly(self):
        code, out, _ = self.run_cli(inputs=["x"])  # unknown choice, then end of input
        self.assertEqual(code, 0)
        self.assertIn("Unknown choice 'x'", out)
        self.assertIn("Goodbye.", out)


class RealProcess(unittest.TestCase):
    """Runs the program as a separate process, the way a user would."""

    def run_module(self, cwd, *argv, stdin=""):
        env = {**os.environ, "PYTHONPATH": str(SRC)}
        return subprocess.run(
            [sys.executable, "-m", "budget_manager", *argv],
            cwd=cwd, env=env, input=stdin, capture_output=True, text=True, timeout=60,
        )

    def test_full_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_module(tmp, "add", "expense", "food", "18.50", "--date", "2026-09-04")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((Path(tmp) / "budget.csv").exists(), "default ledger is ./budget.csv")

            result = self.run_module(tmp, "summary")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Expenses           18.50", result.stdout)

            result = self.run_module(tmp, "add", "expense", "food", "0")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertIn("amount must be > 0", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

            result = self.run_module(tmp, "frobnicate")
            self.assertEqual(result.returncode, 2)  # argparse usage error

            result = self.run_module(tmp, stdin="l\nq\n")  # interactive, piped input
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("T001", result.stdout)


if __name__ == "__main__":
    unittest.main()
