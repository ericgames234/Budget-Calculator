"""Command-line interface: the only module that talks to the user.

Responsibility: input and output. It gathers input (arguments or prompts),
passes it to services and storage, and presents the results. It contains no
business logic: it doesn't calculate, decide what is valid, look things up,
or work out what changed. If you find yourself writing an ``if`` about money
or about transaction data here, it belongs in services.py or validation.py.

Two front-ends share the same actions (``do_add``, ``do_edit``, ``do_delete``):

* command mode:     budget add expense food 18.50 --date 2026-09-04
* interactive mode: budget              (run with no command: a menu with prompts)

Exit codes: 0 = success, 1 = validation/file problem or cancelled,
2 = bad command syntax (argparse's own convention), 130 = Ctrl+C.

Depends on: models, validation, services, storage (it is the outermost layer).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from . import __version__, services, storage
from .models import CATEGORIES, CURRENCIES, Kind, Transaction
from .services import TransactionNotFound
from .storage import StorageError
from .validation import (
    FieldError,
    ValidationError,
    parse_amount,
    parse_category,
    parse_currency,
    parse_date,
    parse_filter,
    parse_kind,
)

DEFAULT_FILE = Path("budget.csv")
DEFAULT_CURRENCY = "EUR"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INTERRUPTED = 130

InputFn = Callable[[str], str]


@dataclass
class Console:
    """Where the CLI reads answers from and writes to. Tests pass in fakes."""

    input_fn: InputFn = input
    out: TextIO = field(default_factory=lambda: sys.stdout)
    err: TextIO = field(default_factory=lambda: sys.stderr)

    def say(self, text: str = "") -> None:
        print(text, file=self.out)

    def warn(self, text: str) -> None:
        print(text, file=self.err)


def _today() -> str:
    return dt.date.today().isoformat()


# --- argument parsing ---------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="budget",
        description="Personal budget manager. Run with no command for interactive mode.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_FILE,
        help="ledger CSV file; put it before the command (default: %(default)s)",
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    kind_help = " or ".join(k.value for k in Kind)
    category_help = "one of the names shown by the 'categories' command"
    amount_help = "positive, '.' for decimals, max 2 decimals, e.g. 18.50"
    currency_help = f"one of {', '.join(CURRENCIES)}"

    add = commands.add_parser("add", help="record a transaction")
    add.add_argument("kind", help=kind_help)
    add.add_argument("category", help=category_help)
    add.add_argument("amount", help=amount_help)
    add.add_argument("--date", help="YYYY-MM-DD (default: today)")
    add.add_argument("--currency", default=DEFAULT_CURRENCY, help=f"{currency_help} (default: %(default)s)")

    edit = commands.add_parser("edit", help="change fields of a transaction (others are kept)")
    edit.add_argument("id", help="e.g. T003 (shown by 'list')")
    edit.add_argument("--date", help="YYYY-MM-DD")
    edit.add_argument("--kind", help=kind_help)
    edit.add_argument("--category", help=category_help)
    edit.add_argument("--amount", help=amount_help)
    edit.add_argument("--currency", help=currency_help)

    delete = commands.add_parser("delete", help="remove a transaction (asks to confirm)")
    delete.add_argument("id", help="e.g. T003 (shown by 'list')")
    delete.add_argument("--yes", action="store_true", help="skip the confirmation question")

    # Shared by list and summary, so both always accept the same filters.
    filters = argparse.ArgumentParser(add_help=False)
    filters.add_argument("--kind", help=kind_help)
    filters.add_argument("--category")
    filters.add_argument("--currency")
    filters.add_argument("--from", dest="start", metavar="DATE", help="on or after YYYY-MM-DD")
    filters.add_argument("--to", dest="end", metavar="DATE", help="on or before YYYY-MM-DD")

    commands.add_parser("list", parents=[filters], help="show transactions, oldest first")
    commands.add_parser("summary", parents=[filters], help="totals per currency")
    commands.add_parser("categories", help="show allowed kinds, categories and currencies")

    validate = commands.add_parser("validate", help="check a CSV file without changing it")
    validate.add_argument("path", type=Path)

    backup = commands.add_parser("backup", help="copy the ledger to a new file (never overwrites)")
    backup.add_argument("destination", type=Path)
    return parser


# --- presentation -------------------------------------------------------------


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def describe(t: Transaction) -> str:
    return f"{t.id}  {t.date}  {t.kind} {t.category}  {t.amount:,.2f} {t.currency}"


def format_table(transactions: Sequence[Transaction]) -> str:
    """Render transactions in the order given (ordering is the caller's choice)."""
    header = f"{'ID':<6} {'DATE':<10} {'KIND':<7} {'CATEGORY':<13} {'AMOUNT':>12}  CUR"
    lines = [header, "-" * len(header)]
    for t in transactions:
        lines.append(
            f"{t.id:<6} {t.date.isoformat():<10} {t.kind.value:<7} "
            f"{t.category:<13} {t.amount:>12,.2f}  {t.currency}"
        )
    lines.append(_plural(len(transactions), "transaction"))
    return "\n".join(lines)


def format_summary(summaries: Sequence[services.CurrencySummary]) -> str:
    blocks = []
    if len(summaries) > 1:
        blocks.append("Each currency is totalled separately; currencies are never added together.")
    for s in summaries:
        lines = [
            f"{s.currency}  ({_plural(s.transaction_count, 'transaction')})",
            f"  {'Income':<10}{s.income:>14,.2f}",
            f"  {'Expenses':<10}{s.expenses:>14,.2f}",
            f"  {'Net':<10}{s.net:>14,.2f}",
        ]
        if s.expenses_by_category:
            lines.append("  Expenses by category:")
            for c in s.expenses_by_category:
                lines.append(f"    {c.category:<14}{c.amount:>12,.2f}  {c.share:5.1f}%")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def print_errors(exc: ValidationError, console: Console) -> None:
    if exc.context:
        console.warn(f"Error: {exc.context}")
    elif len(exc.errors) == 1:
        console.warn(f"Error: {exc.errors[0]}")
        return
    else:
        console.warn(f"Error: input has {len(exc.errors)} problems:")
    for message in exc.errors:
        console.warn(f"  - {message}")


def _confirm(console: Console, question: str) -> bool:
    """Only an explicit 'y' or 'yes' counts as yes. End of input counts as no."""
    try:
        answer = console.input_fn(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


# --- actions shared by both front-ends ----------------------------------------
# Each action has the same shape: load, call one service, save if something
# changed, report. The decisions are made inside the service call.


def do_add(path: Path, console: Console, **values: str) -> int:
    transactions = storage.load_transactions(path)
    transactions, new = services.add_transaction(transactions, **values)
    storage.save_transactions(path, transactions)
    console.say(f"Added {describe(new)}")
    return EXIT_OK


def do_edit(path: Path, console: Console, txn_id: str, **changes: str | None) -> int:
    transactions = storage.load_transactions(path)
    transactions, old, new = services.edit_transaction(transactions, txn_id, **changes)
    changed = services.changed_fields(old, new)
    if not changed:
        console.say(f"No changes: {txn_id} already has those values.")
        return EXIT_OK
    storage.save_transactions(path, transactions)
    before, after = old.to_record(), new.to_record()
    console.say(f"Updated {txn_id}:")
    for name in changed:
        console.say(f"  {name}: {before[name]} -> {after[name]}")
    return EXIT_OK


def do_delete(path: Path, console: Console, txn_id: str, *, confirm: bool) -> int:
    transactions = storage.load_transactions(path)
    target = services.find_transaction(transactions, txn_id)
    if confirm and not _confirm(console, f"Delete {describe(target)}?"):
        console.say("Cancelled; nothing was deleted.")
        return EXIT_ERROR
    transactions, removed = services.delete_transaction(transactions, txn_id)
    storage.save_transactions(path, transactions)
    console.say(f"Deleted {describe(removed)}")
    return EXIT_OK


# --- command mode -------------------------------------------------------------


def _cmd_add(args: argparse.Namespace, console: Console) -> int:
    return do_add(
        args.file,
        console,
        date=args.date if args.date is not None else _today(),
        kind=args.kind,
        category=args.category,
        amount=args.amount,
        currency=args.currency,
    )


def _cmd_edit(args: argparse.Namespace, console: Console) -> int:
    return do_edit(
        args.file,
        console,
        args.id,
        date=args.date,
        kind=args.kind,
        category=args.category,
        amount=args.amount,
        currency=args.currency,
    )


def _cmd_delete(args: argparse.Namespace, console: Console) -> int:
    return do_delete(args.file, console, args.id, confirm=not args.yes)


def _selected(args: argparse.Namespace) -> list[Transaction]:
    # Check the filters before loading, so a typo is reported even if the file is large.
    criteria = parse_filter(
        kind=args.kind, category=args.category, currency=args.currency, start=args.start, end=args.end
    )
    return services.filter_transactions(storage.load_transactions(args.file), criteria)


def _cmd_list(args: argparse.Namespace, console: Console) -> int:
    transactions = _selected(args)
    if transactions:
        console.say(format_table(services.chronological(transactions)))
    else:
        console.say("No matching transactions.")
    return EXIT_OK


def _cmd_summary(args: argparse.Namespace, console: Console) -> int:
    transactions = _selected(args)
    if transactions:
        console.say(format_summary(services.summarize(transactions)))
    else:
        console.say("No matching transactions.")
    return EXIT_OK


def _cmd_categories(args: argparse.Namespace, console: Console) -> int:
    console.say(f"Kinds:      {', '.join(k.value for k in Kind)}")
    console.say(f"Categories: {', '.join(CATEGORIES)}")
    console.say(f"Currencies: {', '.join(CURRENCIES)}")
    return EXIT_OK


def _cmd_validate(args: argparse.Namespace, console: Console) -> int:
    transactions = storage.load_transactions(args.path, missing_ok=False)
    console.say(f"OK: {args.path} is valid ({_plural(len(transactions), 'transaction')}).")
    return EXIT_OK


def _cmd_backup(args: argparse.Namespace, console: Console) -> int:
    storage.backup_ledger(args.file, args.destination)
    console.say(f"Backed up {args.file} to {args.destination}.")
    return EXIT_OK


_HANDLERS: dict[str, Callable[[argparse.Namespace, Console], int]] = {
    "add": _cmd_add,
    "edit": _cmd_edit,
    "delete": _cmd_delete,
    "list": _cmd_list,
    "summary": _cmd_summary,
    "categories": _cmd_categories,
    "validate": _cmd_validate,
    "backup": _cmd_backup,
}


# --- interactive mode ---------------------------------------------------------

MENU = "[a]dd  [e]dit  [d]elete  [l]ist  [s]ummary  [c]ategories  [q]uit"


def _ask(console: Console, label: str, parser: Callable[[str], object], default: str | None = None) -> str:
    """Ask until the answer passes the same validation rule the loader uses.

    Pressing Enter accepts the default, which is always shown in [brackets]
    so it is never applied without the user seeing it.
    """
    prompt = f"{label} [{default}]: " if default is not None else f"{label}: "
    while True:
        raw = console.input_fn(prompt)
        if raw == "" and default is not None:
            raw = default
        try:
            parser(raw)
            return raw
        except FieldError as exc:
            console.say(f"  ! {exc}")


def _category_or_help(raw: str) -> str:
    """Like parse_category, but '?' shows the list (through the re-prompt message)."""
    if raw == "?":
        raise FieldError(f"categories: {', '.join(CATEGORIES)}")
    return parse_category(raw)


# The prompts for one transaction. Used by both add (defaults: today, EUR) and
# edit (defaults: the current values, so Enter keeps them).
_FIELD_PROMPTS: tuple[tuple[str, str, Callable[[str], object]], ...] = (
    ("kind", "Kind (expense/income)", parse_kind),
    ("category", "Category ('?' lists them)", _category_or_help),
    ("amount", "Amount (e.g. 18.50)", parse_amount),
    ("date", "Date (YYYY-MM-DD)", parse_date),
    ("currency", "Currency", parse_currency),
)


def _ask_fields(console: Console, defaults: dict[str, str]) -> dict[str, str]:
    return {name: _ask(console, label, parser, defaults.get(name)) for name, label, parser in _FIELD_PROMPTS}


def _pick(path: Path, console: Console) -> Transaction | None:
    """Ask for an id until it exists. Enter on its own cancels."""
    transactions = storage.load_transactions(path)
    if not transactions:
        console.say("No transactions yet.")
        return None
    while True:
        raw = console.input_fn("Transaction id (Enter to cancel): ")
        if raw == "":
            return None
        try:
            return services.find_transaction(transactions, raw)
        except TransactionNotFound as exc:
            console.say(f"  ! {exc} Use [l]ist to see ids.")


def _interactive_add(path: Path, console: Console) -> None:
    values = _ask_fields(console, {"date": _today(), "currency": DEFAULT_CURRENCY})
    do_add(path, console, **values)


def _interactive_edit(path: Path, console: Console) -> None:
    target = _pick(path, console)
    if target is None:
        return
    console.say(f"Editing {describe(target)}. Press Enter to keep a value.")
    do_edit(path, console, target.id, **_ask_fields(console, target.to_record()))


def _interactive_delete(path: Path, console: Console) -> None:
    target = _pick(path, console)
    if target is not None:
        do_delete(path, console, target.id, confirm=True)


def _interactive_list(path: Path, console: Console) -> None:
    transactions = storage.load_transactions(path)
    if transactions:
        console.say(format_table(services.chronological(transactions)))
    else:
        console.say("No transactions yet.")


def _interactive_summary(path: Path, console: Console) -> None:
    transactions = storage.load_transactions(path)
    if transactions:
        console.say(format_summary(services.summarize(transactions)))
    else:
        console.say("No transactions yet.")


def _interactive_categories(path: Path, console: Console) -> None:
    _cmd_categories(argparse.Namespace(), console)


_MENU_ACTIONS: dict[str, Callable[[Path, Console], None]] = {
    "a": _interactive_add,
    "add": _interactive_add,
    "e": _interactive_edit,
    "edit": _interactive_edit,
    "d": _interactive_delete,
    "delete": _interactive_delete,
    "l": _interactive_list,
    "list": _interactive_list,
    "s": _interactive_summary,
    "summary": _interactive_summary,
    "c": _interactive_categories,
    "categories": _interactive_categories,
}


def run_interactive(path: Path, console: Console | None = None) -> int:
    """Menu loop. A failed action prints its error and returns to the menu."""
    console = Console() if console is None else console
    storage.load_transactions(path)  # stop early if the ledger is invalid
    console.say(f"Budget manager {__version__} - ledger: {path}")
    try:
        while True:
            choice = console.input_fn(f"\n{MENU}\n> ").strip().lower()
            if choice in ("q", "quit", "exit"):
                break
            action = _MENU_ACTIONS.get(choice)
            if action is None:
                console.say(f"Unknown choice {choice!r}. Type a, e, d, l, s, c or q.")
                continue
            try:
                action(path, console)
            except ValidationError as exc:
                print_errors(exc, console)
            except (TransactionNotFound, StorageError) as exc:
                console.warn(f"Error: {exc}")
    except EOFError:  # Ctrl+D / Ctrl+Z, or the end of piped input
        console.say()
    console.say("Goodbye.")
    return EXIT_OK


# --- entry point --------------------------------------------------------------


def main(
    argv: Sequence[str] | None = None,
    *,
    input_fn: InputFn = input,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Run the CLI and return an exit code (instead of calling ``sys.exit``), so tests can call it."""
    console = Console(
        input_fn=input_fn,
        out=sys.stdout if out is None else out,
        err=sys.stderr if err is None else err,
    )
    args = build_parser().parse_args(argv)
    try:
        if args.command is None:
            return run_interactive(args.file, console)
        return _HANDLERS[args.command](args, console)
    except ValidationError as exc:
        print_errors(exc, console)
    except TransactionNotFound as exc:
        console.warn(f"Error: {exc} Run 'list' to see ids.")
    except StorageError as exc:
        console.warn(f"Error: {exc}")
    except KeyboardInterrupt:
        console.warn("\nInterrupted.")
        return EXIT_INTERRUPTED
    return EXIT_ERROR
