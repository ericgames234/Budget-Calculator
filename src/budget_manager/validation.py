"""Boundary validation: turn untrusted text into trusted ``Transaction`` objects.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Mapping
from decimal import Decimal

from .models import CATEGORIES, CURRENCIES, FIELDS, Kind, Transaction, TransactionFilter


class FieldError(ValueError):
    """One problem with one value. The message is a complete, user-facing sentence."""


class ValidationError(ValueError):
    """One or more problems with a record, a set of filters, or a whole file.

    ``errors`` lists every problem found, not only the first, so the user can
    fix them all in one pass. ``context`` is an optional heading such as
    "data/x.csv has 3 problems:".
    """

    def __init__(self, errors: list[str], context: str | None = None) -> None:
        self.errors = list(errors)
        self.context = context
        super().__init__("\n".join(([context] if context else []) + self.errors))


_ID_RE = re.compile(r"T\d{3,}")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_AMOUNT_RE = re.compile(r"\d+(\.\d{1,2})?")
_TOO_PRECISE_RE = re.compile(r"\d+\.\d{3,}")


# --- single values ------------------------------------------------------------
# Each parse_* function takes raw text and returns a typed value, or raises
# FieldError. The CLI also uses them on their own, to check one prompt or one
# filter option at a time.


def _present(field: str, raw: str | None) -> str:
    """Reject missing values and surrounding spaces, which are both easy to miss by eye."""
    if raw is None or raw.strip() == "":
        raise FieldError(f"{field} is required.")
    if raw != raw.strip():
        raise FieldError(f"{field} {raw!r} has leading or trailing spaces; remove them.")
    return raw


def _not_recognised(field: str, raw: str, allowed: Iterable[str], suggestion: str) -> FieldError:
    allowed = tuple(allowed)
    if suggestion in allowed:
        # Hint at the fix, but don't apply it: the user stays in control.
        return FieldError(
            f"{field} {raw!r} is not recognised; did you mean {suggestion!r}? "
            "Values are case-sensitive."
        )
    return FieldError(f"{field} {raw!r} is not recognised; use one of: {', '.join(allowed)}.")


def parse_id(raw: str | None) -> str:
    raw = _present("id", raw)
    if not _ID_RE.fullmatch(raw):
        raise FieldError(f"id must be 'T' followed by at least 3 digits (e.g. T001), got {raw!r}.")
    return raw


def parse_date(raw: str | None, field: str = "date") -> dt.date:
    raw = _present(field, raw)
    # The regex comes first because date.fromisoformat on Python 3.11 also
    # accepts forms like "20260904". The contract is exactly YYYY-MM-DD.
    if not _DATE_RE.fullmatch(raw):
        raise FieldError(f"{field} must use the format YYYY-MM-DD (e.g. 2026-09-04), got {raw!r}.")
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        raise FieldError(f"{field} {raw!r} is not a real calendar date.") from None


def parse_kind(raw: str | None) -> Kind:
    raw = _present("kind", raw)
    allowed = [k.value for k in Kind]
    if raw not in allowed:
        raise _not_recognised("kind", raw, allowed, raw.lower())
    return Kind(raw)


def parse_category(raw: str | None) -> str:
    raw = _present("category", raw)
    if raw not in CATEGORIES:
        raise _not_recognised("category", raw, CATEGORIES, raw.lower())
    return raw


def parse_currency(raw: str | None) -> str:
    raw = _present("currency", raw)
    if raw not in CURRENCIES:
        raise _not_recognised("currency", raw, CURRENCIES, raw.upper())
    return raw


def parse_amount(raw: str | None) -> Decimal:
    """Parse a positive amount with at most 2 decimal places, as an exact Decimal.

    Each kind of mistake gets its own message, because "invalid amount" alone
    doesn't tell the user what to change.
    """
    raw = _present("amount", raw)
    if raw.startswith("-"):
        raise FieldError(
            f"amount must be > 0, got {raw!r}. Record money going out as kind "
            "'expense' with a positive amount."
        )
    if "," in raw:
        # "18,50" could mean 18.50 (European decimal comma) or 1850 (thousands
        # separator). That is ambiguous, so we refuse to guess.
        raise FieldError(
            f"amount {raw!r} contains a comma. Use '.' for decimals and no thousands "
            "separator (e.g. 1250.50)."
        )
    if _TOO_PRECISE_RE.fullmatch(raw):
        raise FieldError(f"amount {raw!r} has more than 2 decimal places; amounts are in cents.")
    if not _AMOUNT_RE.fullmatch(raw):
        raise FieldError(f"amount {raw!r} is not a number. Use digits with up to 2 decimals (e.g. 18.50).")
    value = Decimal(raw)
    if value == 0:
        raise FieldError(f"amount must be > 0, got {raw!r}.")
    return value


# --- whole records ------------------------------------------------------------

_PARSERS = {
    "id": parse_id,
    "date": parse_date,
    "kind": parse_kind,
    "category": parse_category,
    "amount": parse_amount,
    "currency": parse_currency,
}
assert tuple(_PARSERS) == FIELDS, "every schema field needs exactly one parser"


def parse_transaction(raw: Mapping[str, str | None], *, location: str = "") -> Transaction:
    """Validate every field of one record and build a ``Transaction``.

    Every field is checked, even after one fails, so the user sees all the
    problems at once. ``location`` (e.g. "Row 4") is added to the start of
    each message.
    """
    prefix = f"{location}: " if location else ""
    values: dict[str, object] = {}
    errors: list[str] = []
    for field, parser in _PARSERS.items():
        try:
            values[field] = parser(raw.get(field))
        except FieldError as exc:
            errors.append(f"{prefix}{exc}")
    if errors:
        raise ValidationError(errors)
    return Transaction(**values)  # type: ignore[arg-type]


def check_changes(changes: Mapping[str, str | None]) -> dict[str, str]:
    """An edit must change at least one field. Returns only the fields given.

    The new *values* are not checked here. They are merged into the existing
    record, and the whole record goes through ``parse_transaction`` again, the
    same construction boundary that ``add`` uses.
    """
    given = {name: value for name, value in changes.items() if value is not None}
    if not given:
        editable = ", ".join(f for f in FIELDS if f != "id")
        raise ValidationError([f"Nothing to change: give at least one new value ({editable})."])
    return given


def check_header(fieldnames: Iterable[str] | None) -> None:
    """The header must contain exactly the schema columns (in any order)."""
    expected = ", ".join(FIELDS)
    if not fieldnames:
        raise ValidationError([f"File is empty; the first line must be the header: {expected}."])
    names = list(fieldnames)
    errors: list[str] = []
    duplicates = sorted({n for n in names if names.count(n) > 1})
    missing = [f for f in FIELDS if f not in names]
    unknown = [n for n in names if n not in FIELDS]
    if duplicates:
        errors.append(f"Header repeats column(s): {', '.join(duplicates)}.")
    if missing:
        errors.append(f"Header is missing column(s): {', '.join(missing)}.")
    if unknown:
        errors.append(f"Header has unknown column(s): {', '.join(unknown)}.")
    if errors:
        errors.append(f"Expected exactly these columns: {expected}.")
        raise ValidationError(errors)


def parse_rows(rows: Iterable[tuple[int, Mapping[str | None, object]]]) -> list[Transaction]:
    """Validate a whole file's rows, given as (row number, row dict) pairs.

    Either every row is valid and you get all the transactions, or nothing is
    returned and ``ValidationError`` lists every problem in the file. Loading
    only the good rows would silently drop data, so that never happens.
    """
    transactions: list[Transaction] = []
    errors: list[str] = []
    first_seen: dict[str, int] = {}
    for number, row in rows:
        location = f"Row {number}"

        # Uniqueness is checked on the raw id, so a duplicate is reported even
        # if another field in the same row is also wrong.
        raw_id = row.get("id")
        if isinstance(raw_id, str) and raw_id:
            if raw_id in first_seen:
                errors.append(
                    f"{location}: id {raw_id!r} is a duplicate (first used on row {first_seen[raw_id]})."
                )
                continue
            first_seen[raw_id] = number

        if None in row:  # csv.DictReader stores surplus values under the key None
            errors.append(
                f"{location}: has more values than the {len(FIELDS)} header columns "
                "(is there an unquoted comma in a value?)."
            )
            continue

        try:
            transactions.append(parse_transaction(row, location=location))  # type: ignore[arg-type]
        except ValidationError as exc:
            errors.extend(exc.errors)

    if errors:
        raise ValidationError(errors)
    return transactions


def parse_filter(
    *,
    kind: str | None = None,
    category: str | None = None,
    currency: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> TransactionFilter:
    """Validate filter options with the same rules as the data they filter.

    This matters: a typo like ``--category fod`` must be an error. If it were
    accepted, the filter would match nothing and the user would see "no
    transactions", which looks like a real answer but isn't.
    """
    values: dict[str, object] = {}
    errors: list[str] = []
    checks = (
        ("kind", kind, parse_kind),
        ("category", category, parse_category),
        ("currency", currency, parse_currency),
        ("start", start, lambda r: parse_date(r, "--from date")),
        ("end", end, lambda r: parse_date(r, "--to date")),
    )
    for name, raw, parser in checks:
        if raw is None:
            continue
        try:
            values[name] = parser(raw)
        except FieldError as exc:
            errors.append(str(exc))
    s, e = values.get("start"), values.get("end")
    if s and e and s > e:  # type: ignore[operator]
        errors.append(f"--from date {s} is after --to date {e}.")
    if errors:
        raise ValidationError(errors)
    return TransactionFilter(**values)  # type: ignore[arg-type]
