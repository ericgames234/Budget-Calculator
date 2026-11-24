"""Persistence: read and write the ledger as a CSV file.
"""

from __future__ import annotations

import csv
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from .models import FIELDS, Transaction
from .validation import ValidationError, check_header, parse_rows


class StorageError(Exception):
    """The file could not be read or written (missing, locked, not text, ...)."""


def _problems(n: int) -> str:
    return f"{n} problem{'s' if n != 1 else ''}"


def load_transactions(path: Path | str, *, missing_ok: bool = True) -> list[Transaction]:
    """Read and validate every row of a ledger file.

    A missing file means "no transactions yet" when ``missing_ok`` is true
    (that is how a new ledger starts). A file that exists must be completely
    valid: one bad row rejects the whole file, and the ``ValidationError``
    lists every problem with its row number.
    """
    path = Path(path)
    if not path.exists():
        if missing_ok:
            return []
        raise StorageError(f"File not found: {path}")
    try:
        # utf-8-sig skips the invisible byte-order mark Excel adds when it saves
        # "CSV UTF-8". The BOM is not data, so removing it changes no value.
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            try:
                check_header(reader.fieldnames)
                # reader.line_num is the line just read, so row numbers match
                # what the user sees in a text editor or spreadsheet.
                return parse_rows((reader.line_num, row) for row in reader)
            except ValidationError as exc:
                raise ValidationError(
                    exc.errors, context=f"{path} has {_problems(len(exc.errors))}:"
                ) from None
    except UnicodeDecodeError:
        raise StorageError(f"{path} is not UTF-8 text. Save it as 'CSV UTF-8' and try again.") from None
    except csv.Error as exc:
        raise StorageError(f"{path} is not a readable CSV file: {exc}.") from None
    except OSError as exc:
        raise StorageError(f"Could not read {path}: {exc.strerror or exc}.") from None


def _to_row(t: Transaction) -> list[str]:
    record = t.to_record()
    return [record[field] for field in FIELDS]


def save_transactions(path: Path | str, transactions: Iterable[Transaction]) -> None:
    """Write all transactions *atomically*.

    The data is first written to a temporary file in the same folder, which
    then replaces the real file in one step (``os.replace``). If the program
    crashes or the disk fills up partway, the old ledger is left untouched,
    never half-written.
    """
    path = Path(path)
    tmp: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(FIELDS)
            writer.writerows(_to_row(t) for t in transactions)
        os.replace(tmp, path)
        tmp = None
    except OSError as exc:
        raise StorageError(f"Could not save {path}: {exc.strerror or exc}.") from None
    finally:
        if tmp is not None:
            Path(tmp).unlink(missing_ok=True)


def backup_ledger(source: Path | str, destination: Path | str) -> None:
    """Copy the ledger byte for byte. It never overwrites an existing file.

    The copy is made even if the ledger is invalid, since that is exactly
    when you might want a backup before fixing it.
    """
    source, destination = Path(source), Path(destination)
    if not source.exists():
        raise StorageError(f"Nothing to back up: {source} does not exist yet.")
    try:
        data = source.read_bytes()
        with destination.open("xb") as f:  # "x" = create; fail if it exists
            f.write(data)
    except FileExistsError:
        raise StorageError(
            f"{destination} already exists. Choose a new name; backups never overwrite."
        ) from None
    except OSError as exc:
        raise StorageError(f"Could not back up to {destination}: {exc.strerror or exc}.") from None
