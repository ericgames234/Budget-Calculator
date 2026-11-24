# Design Rationale

This document explains the reasoning behind the structure of the code. Read it alongside
the source: each section names the file where the decision lives.

---

## 1. Goal of this release

v1.0 is one release of a working, integrated budget manager with the core
functions (**add, edit, delete, filter, summarise**), validated from end to end.
In practice, "validated end to end" means three things:

1. Every path into the program checks its data: typing a command, answering a
   prompt, editing a record, loading a file.
2. Every check uses the same rules.
3. Tests run the real program against real files, not just isolated functions.

---

## 2. Conventions: one responsibility per module

Each module has exactly one job as specified in the requirements. Just as important is what each module does **not** do:

| Module | Its one responsibility | It never... |
|---|---|---|
| `models.py` | Defines the schema: `Transaction`, `Kind`, the vocabularies, the field order, and the canonical text form (`to_record`) | imports another module, checks rules, touches files |
| `validation.py` | Holds every rule: turns untrusted text into a `Transaction` or a list of clear errors | reads files, prints, calculates totals |
| `services.py` | Makes every decision and calculation: add, find, edit, delete, filter, order, summarise, percentage shares, what changed | reads files, prints, prompts |
| `storage.py` | Moves data between disk and memory: load, atomic save, backup | decides what is valid (it asks `validation`), talks to the user |
| `cli.py` | Handles input and output: arguments, prompts, formatting, exit codes | contains business logic |

### Keeping the CLI free of business logic

A useful test: *if you replaced the terminal with a web page, would this code have
to be rewritten?* If yes, it belongs in the CLI. If no, it's business logic and
belongs further in. Applying that test moved these out of `cli.py`:

| Was in the CLI | Now | Why it's business logic |
|---|---|---|
| category % of expenses (`amount / expenses * 100`) | `CategoryTotal.share`, computed in `services.summarize` | A web page would need the same number |
| sorting by date | `services.chronological` | "Ledger order" is a domain rule. It also had a bug as plain text sorting: `T1000` sorted before `T999` |
| finding a transaction by id | `services.find_transaction` | Lookup and "not found" are domain outcomes |
| working out what an edit changed | `services.changed_fields` | Decides whether anything needs saving |

What *stays* in the CLI is the wiring and display: parsing arguments, prompting,
the `[y/N]` confirmation, defaults shown in brackets, column widths, rounding
`99.9625` to `100.0%` **for display**, and choosing stdout vs stderr.

Command mode and interactive mode share one function per action (`do_add`,
`do_edit`, `do_delete`). Each workflow (load → service → save → report) is written
once, and the two front-ends only differ in how they gather input.

### Conventions are tested

`tests/test_architecture.py` reads each module's imports (without running them) and fails if:

- a module imports from a layer further out (e.g. `services` importing `storage`);
- a module imports something outside its job (e.g. `cli` importing `decimal` or `csv`,
  or `services` importing `os` or `pathlib`);
- `models`, `validation`, `services` or `storage` call `print` or `input`;
- a new module is added without declaring its allowed dependencies.

---

## 3. Layers and dependency direction

```
            +-----------------------------------------------+
            |  cli.py       input/output, prompts, exit codes |
            |  storage.py   CSV files on disk                 |   outer: changes often,
            |   +---------------------------------------+   |   touches the world
            |   | services.py   decisions & calculations |   |
            |   |   +-------------------------------+   |   |
            |   |   | validation.py  all the rules  |   |   |   inner: stable,
            |   |   |   +-----------------------+   |   |   |   pure logic
            |   |   |   | models.py  the schema |   |   |   |
            |   |   |   +-----------------------+   |   |   |
            |   |   +-------------------------------+   |   |
            |   +---------------------------------------+   |
            +-----------------------------------------------+
```

**The rule is that imports only point inward.**

| Module | Imports from the package | Must never import |
|---|---|---|
| `models.py` | nothing | everything else |
| `validation.py` | models | services, storage, cli |
| `services.py` | models, validation | **storage, cli** |
| `storage.py` | models, validation | services, cli |
| `cli.py` | all of the above | (it is the outermost layer) |

**Why is it important?** Well, the original `cli.py` did everything in one flat script:
it parsed arguments, read `entries.json`, changed the list and wrote the file.
There was no way to test "does summary count correctly?" without creating files
and faking a command line. Now:

- `services.py` does no I/O at all. Its tests are plain lists in and values out.
  They need no temporary files and no mocking.
- `storage.py` doesn't know what a "summary" is. You could swap CSV for SQLite
  by rewriting one file; services and the CLI wouldn't change.
- Adding edit/delete to **both** front-ends needed only one new service function
  each. Two front-ends on one core is the clearest proof that the layering works.

Storage and the CLI are both *adapters*: thin edges that connect the core to the
outside world (the disk and the terminal). Neither imports the other. `cli.py` is
the one place that wires them together.

---

## 4. The schema contract (`models.py`)

```python
@dataclass(frozen=True, slots=True)
class Transaction:
    id: str          # "T001"
    date: dt.date    # 2026-09-04
    kind: Kind       # Kind.EXPENSE | Kind.INCOME
    category: str    # one of CATEGORIES
    amount: Decimal  # > 0, at most 2 decimals
    currency: str    # one of CURRENCIES
```

| Decision | Reasoning |
|---|---|
| **Typed fields** (a real `date`, `Decimal`, `Kind`), not strings | Once you hold a `Transaction`, its data is already proven valid. Code further in never has to re-check `"is this date real?"`. This is the *parse, don't validate* idea: text is turned into types once, at the edge. |
| **`frozen=True`** | Nothing can change `amount` after validation approved it. Even *edit* doesn't modify the object: it builds a new, re-validated one and swaps it in. |
| **`Decimal`, not `float`** for amount | The slide says "positive float", but floats can't represent most cents exactly: `0.10 + 0.20 == 0.30000000000000004`. Over a year of transactions those errors add up and totals stop matching. `Decimal("0.10") + Decimal("0.20") == Decimal("0.30")` exactly. The *file* format is unchanged (`18.50`). Only the in-memory type is stricter. |
| **Amount always positive; direction comes from `kind`** | With signed amounts, an expense of `-18.50` and an income of `-18.50` (a refund?) are ambiguous. With one source of truth for direction, the sign can never contradict the kind. |
| **`Kind` is an enum** | The only possible values are `expense` and `income`, so a typo like `"expnese"` can't sit quietly in the data. |
| **Controlled vocabularies** (`CATEGORIES`, `CURRENCIES`) in one place | Free-text categories split totals (`food`, `Food`, `groceries`). The categories are broad, high-level groups on purpose. Validation, help text and the `categories` command all read the same tuple. |
| **Currencies limited to EUR, USD, GBP, CHF** | They all use 2 decimal places, which the amount rule depends on. JPY (0 decimals) would need a per-currency rule. |
| **`to_record()`**, the canonical text form | The exact inverse of `parse_transaction`, so `parse_transaction(t.to_record()) == t`. Storage writes it, and edit merges changes into it. It's one definition of "how a transaction looks as text". |

---

## 5. Validate at the boundaries (`validation.py`)

### 5.1 One module holds every rule

Validation could have been scattered: a check in the CLI, another in storage,
another in a service. That gives rules that disagree. The CLI might accept `Food`
while the loader rejects it, and you'd end up with a file the program wrote but
can't read back.

Here, **every rule is a function in `validation.py`**, and there are exactly two
boundaries that call them:

| Boundary | Entry point | Called by |
|---|---|---|
| **Construction** (data from a person) | `parse_transaction`, via `services.add_transaction` and `services.edit_transaction` | `add`, `edit`, interactive mode |
| **Import** (data from a file) | `check_header` + `parse_rows`, via `storage.load_transactions` | every command that reads the ledger, and `validate` |

**Edit goes through the same door as add.** Edit doesn't check only the changed
field. It merges the change into the record's text form and re-validates the
*whole record* with `parse_transaction`. So an edit can never produce something `add`
would reject, and any future rule that compares two fields will apply to edits
automatically.

Interactive mode also calls the single-field parsers (`parse_kind`, `parse_amount`, …)
to re-prompt straight away. They are the *same functions*, so a prompt can never be
looser or stricter than the loader.

**Filters are validated too.** `list --category fod` is an error, not "No matching
transactions". An empty result from a typo looks like a real answer, and that kind
of silent failure is what this design avoids.

### 5.2 Error messages say what, where, and how to fix it

```
Row 8: amount must be > 0, got '-12.00'. Record money going out as kind 'expense' with a positive amount.
Row 12: currency 'eur' is not recognised; did you mean 'EUR'? Values are case-sensitive.
Error: No transaction has id 'T099'. Run 'list' to see ids.
```

- **Row numbers are file line numbers** (the header is row 1), so they match what you
  see in a text editor or spreadsheet.
- **All problems are collected**, not just the first. A file with 17 mistakes produces
  17 messages in one run, not 17 rounds of fix-and-rerun.
- Each kind of amount mistake (negative, zero, comma, 3 decimals, not a number) has its
  own message, because "invalid amount" alone doesn't tell the user what to change.

### 5.3 Never silently correct

| Input | Could have... | Instead | Why |
|---|---|---|---|
| `18,50` | read as 18.50 | reject | It could equally mean 1,850 (comma as thousands separator). Guessing wrong changes the amount by 100×. |
| `18.555` | rounded to 18.56 | reject | Which rounding rule? And why does the data have fractions of a cent? Probably a typo. |
| `Food`, `eur` | lowercased / uppercased | reject, **suggest** `'food'`, `'EUR'` | The user sees and confirms the fix. |
| ` food` | stripped | reject | Stray spaces usually come from hand-editing; flagging them keeps the file clean. |
| `-12.00` | treated as an expense | reject | A sign and a kind that might disagree is the ambiguity §4 removed. |
| One bad row in a file | load the other rows | reject the whole file | Dropping rows quietly changes your totals without telling you. |

Some things *are* handled automatically because they aren't ambiguous: the invisible
byte-order mark that Excel adds to "CSV UTF-8" files (not data), and defaults that are
always **shown** (`Date [2026-11-24]:`, `--currency` default in `--help`).

---

## 6. Services (`services.py`)

- **Pure functions that return new lists.** `edit_transaction(...) -> (new_list, old, new)`
  leaves the input unchanged, so a test can check "before" and "after" side by side, and
  the CLI decides whether to save. A test checks that no service ever modifies its input.
- **`next_id` = highest number + 1**, not count + 1. Count + 1 could hand out an id
  that already exists after a deletion; highest + 1 never collides. One trade-off:
  deleting the *newest* transaction frees its id for the next add. Preventing that would
  need a stored counter outside the six-field schema, which isn't worth it for a
  personal ledger.
- **Ids can't be edited.** An id is the transaction's identity. Changing it is really
  "delete and add", so that's what you do.
- **`summarize` groups by currency** and never adds them together. "350 EUR + 100 USD = 450"
  is meaningless, and converting needs exchange rates the tool doesn't have.
- **`TransactionNotFound`** is a service exception, not a validation error. `T099` is a
  perfectly *valid* id; it just doesn't exist in this ledger. That's a lookup result.

---

## 7. Why edit and delete are in this release (added features)

Strict validation catches mistakes in *form*: `18,50`, `Food`, `2026-02-30`. It can't
catch a mistake in *fact*: typing `185.00` instead of `18.50` produces a perfectly valid
transaction. Without edit/delete, the only fix is hand-editing the CSV, which skips the
construction boundary. The bad value is then only caught on the next load, and the whole
ledger is blocked until you fix it. With edit/delete, fixes go through the same validation
as everything else.

Delete is the only command that destroys data, so it has the most safeguards:

- it asks `[y/N]` first; only an explicit `y` or `yes` proceeds, and Enter, `n` or
  end of input all cancel;
- `--yes` skips the question for scripts. This is an explicit opt-out, never the default;
- a cancelled delete exits with code 1, so a script can tell nothing happened;
- the confirmation shows the full transaction, not just its id, so you can see
  you've picked the right one.

---

## 8. Storage (`storage.py`)

| Decision | Why |
|---|---|
| **CSV**, not JSON | A budget is a table. CSV opens in Excel or LibreOffice for hand-editing, and the sample data is CSV. |
| **Missing file = empty ledger** | That is how a new budget starts (same as the original `entries.json`). For `validate`, a missing file *is* an error. |
| **Atomic save**: write a temporary file, then `os.replace` | If the program crashes mid-save, the old file is untouched. A half-written ledger would be the worst failure for a finance tool. |
| **Reload before every write** | Edits made to the file in another program aren't lost when the CLI saves. |
| **`backup` opens with `"x"` (exclusive create)** | Kept from the original `cli.py`: a backup must never overwrite another backup. It copies bytes, so even an invalid ledger can be backed up. |
| **`StorageError`** wraps OS errors | Users see `Could not save budget.csv: Permission denied.`, never a traceback. In interactive mode, the error is shown and you return to the menu. |

---

## 9. Testing strategy

There is one test file per layer, plus one for the conventions:

1. **Validation**: every rule accepts its good cases and rejects its bad ones, with
   message checks (the message is part of the contract). Also checks the `to_record` round-trip.
2. **Services**: business logic on in-memory lists, including Decimal exactness, edit
   re-validation, and a check that no service ever modifies its input.
3. **Storage**: round-trips, BOM, non-UTF-8, atomic save, and both shipped data files
   (`invalid_transactions.csv` must produce exactly one error for each of rows 3–19).
4. **CLI end to end**: real commands on real temp files; a rejected `add`/`edit` and a
   cancelled `delete` leave the file **byte-for-byte unchanged**; an invalid ledger blocks
   every command; scripted interactive sessions; and a real `python -m budget_manager` subprocess.
5. **Architecture**: the layer rules from §2 and §3.

Tests use `unittest` from the standard library, so there is nothing to install. `pytest` also runs them unchanged.

---

## 10. From the original `cli.py` to this release

| Original | Now | Why |
|---|---|---|
| One script, all concerns mixed | 5 modules, one responsibility each | testable in isolation (§2, §3) |
| `entries` = list of free-text strings | `Transaction` with 6 typed fields | the schema contract (§4) |
| `entries.json` | `budget.csv` | a table, editable in a spreadsheet (§8) |
| bad JSON → Python traceback | every problem listed, with its row | actionable errors (§5.2) |
| `file.write_text(...)` | temp file + `os.replace` | no half-written files (§8) |
| `add "Buy milk"` only | `add` / `edit` / `delete` + interactive prompts | structured input, fixable mistakes (§7) |
| `summary` = count of entries | income / expenses / net / breakdown per currency | the actual budget question |
| `backup` with `"x"` mode | kept, plus a friendly error if the file exists | the idea was right |
| `argparse` subcommands | kept, plus a shared filter parser for `list`/`summary` | same filters everywhere |

---

## 11. Kept this out for scope reasons. (v1.0)

Undo, budgets and alerts, currency conversion, recurring transactions, a notes field,
and multi-user locking. Each would be a schema or layer addition, not a rewrite: that
is what the layering nets you. See README §5 for more details.
