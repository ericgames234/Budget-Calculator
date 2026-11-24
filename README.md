# Budget Manager

A personal budget manager for the command line. You record income and expenses,
fix or remove mistakes, list and filter transactions, and see totals per currency.
Every transaction is checked against a fixed schema, both when you type it in and
when it is loaded from a file. Mistakes are reported with the row, the value, and
how to fix it.

Version 1.0.0 · Python 3.11+ · no third-party dependencies.
For the reasoning behind the design, refer to [DESIGN.md](DESIGN.md).


## 1. Setup

 Python 3.11 or newer (`python --version`).

**Option A: install it (gives you a `budget` command to fill)**

```powershell
cd Budget-manager
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -e .
```

**Option B: run straight from the source folder (no installation at all)**

```powershell
cd Budget-manager
$env:PYTHONPATH = "src"         # macOS/Linux: export PYTHONPATH=src
```

Then use `python -m budget_manager` wherever this README says `budget`.

## 2. Run

```text
budget [--file LEDGER.csv] COMMAND [options]
budget                                  # no command: interactive menu
```

| Command | What it's doing |
|---|---|
| `add KIND CATEGORY AMOUNT [--date D] [--currency C]` | Record a transaction. Date defaults to today, currency to EUR. |
| `edit ID [--date] [--kind] [--category] [--amount] [--currency]` | Change one or more fields of a transaction. Fields you leave out are kept. |
| `delete ID [--yes]` | Remove a transaction. Asks `[y/N]` first unless you pass `--yes`. |
| `list [filters]` | Show transactions, oldest first. |
| `summary [filters]` | Income, expenses, net and expense breakdown, **per currency**. |
| `categories` | Show the allowed kinds, categories and currencies. |
| `validate FILE` | Check a CSV file and report every problem. Changes nothing. |
| `backup DEST` | Copy the ledger to a new file. Never overwrites. |

Filters (for `list` and `summary`): `--kind`, `--category`, `--currency`,
`--from YYYY-MM-DD`, `--to YYYY-MM-DD`. Date ranges are inclusive.

The ledger is `budget.csv` in the current folder unless you pass `--file` **before**
the command. If the file doesn't exist, it is created on the first `add`.

Exit codes: `0` success · `1` validation/file problem, or a delete you cancelled · `2` bad command syntax.

## 3. Example

```text
> budget --file data/sample_transactions.csv summary --currency USD
USD  (3 transactions)
  Income            100.00
  Expenses          354.15
  Net              -254.15
  Expenses by category:
    transport           312.40   88.2%
    food                 41.75   11.8%

> budget add expense food 18.50 --date 2026-09-04
Added T001  2026-09-04  expense food  18.50 EUR

> budget add expense Food 18,50
Error: input has 2 problems:
  - category 'Food' is not recognised; did you mean 'food'? Values are case-sensitive.
  - amount '18,50' contains a comma. Use '.' for decimals and no thousands separator (e.g. 1250.50).
```

How to fix a mistake: FYI this works on a copy, so the sample file is kept the same:

```text
> budget --file data/sample_transactions.csv backup my_budget.csv
> budget --file my_budget.csv edit T003 --amount 19.50 --category shopping
Updated T003:
  category: food -> shopping
  amount: 18.50 -> 19.50

> budget --file my_budget.csv edit T003 --date 2026-13-01
Error: date '2026-13-01' is not a real calendar date.

> budget --file my_budget.csv delete T014
Delete T014  2026-09-30  expense other  0.01 EUR? [y/N] y
Deleted T014  2026-09-30  expense other  0.01 EUR
```

Interactive mode asks for each field and asks again until the answer is valid.
When editing, the current values are the defaults, so pressing Enter keeps them:

```text
> budget
Budget manager 1.0.0 - ledger: budget.csv

[a]dd  [e]dit  [d]elete  [l]ist  [s]ummary  [c]ategories  [q]uit
> e
Transaction id (Enter to cancel): T001
Editing T001  2026-09-04  expense food  185.00 EUR. Press Enter to keep a value.
Kind (expense/income) [expense]:
Category ('?' lists them) [food]:
Amount (e.g. 18.50) [185.00]: 18,50
  ! amount '18,50' contains a comma. Use '.' for decimals and no thousands separator (e.g. 1250.50).
Amount (e.g. 18.50) [185.00]: 18.50
Date (YYYY-MM-DD) [2026-09-04]:
Currency [EUR]:
Updated T001:
  amount: 185.00 -> 18.50
```

### Sample data: valid and invalid cases

| File | Purpose |
|---|---|
| `data/sample_transactions.csv` | **Valid cases.** 19 realistic transactions over two months: both kinds, 11 categories, two currencies, the smallest amount (0.01), amounts in the thousands, and month-end dates. `budget validate` accepts it. |
| `data/invalid_transactions.csv` | **Invalid cases.** One valid row, then one row per validation rule, each breaking exactly that rule. `budget validate` rejects it with 17 numbered errors. |

What each row of the invalid file breaks:

| Row | Value | Rule |
|---|---|---|
| 3 | `04/09/2026` | date format must be YYYY-MM-DD |
| 4 | `2026-02-30` | date must be a real calendar date |
| 5 | `Expense` | kind is case-sensitive |
| 6 | `refund` | kind must be expense or income |
| 7 | `groceries` | category must be in the vocabulary |
| 8 | `-12.00` | amount must be > 0 |
| 9 | `0.00` | amount must be > 0 |
| 10 | `18,50` | no comma in amounts (ambiguous) |
| 11 | `18.555` | at most 2 decimal places |
| 12 | `eur` | currency is case-sensitive |
| 13 | `XYZ` | currency must be supported |
| 14 | `T003` again | ids must be unique |
| 15 | *(empty)* | id is required |
| 16 | `X15` | id format is T + 3 or more digits |
| 17 | 7 values | row has more values than header columns |
| 18 | 5 values | missing value (currency) |
| 19 | `␣food` | no leading/trailing spaces |

## 4. Test

From the `Budget-manager` folder:

```powershell
python -m unittest -v
```

No install or third-party packages are needed for this. Also, There are 65 tests:

| File | Covers |
|---|---|
| `tests/test_validation.py` | All rules, accept and reject, and that messages say what to fix |
| `tests/test_services.py` | add / find / edit / delete / filter / order / summarise on in-memory lists (no files) |
| `tests/test_storage.py` | CSV round-trip, row numbers in errors, atomic save, backup, both sample files |
| `tests/test_cli.py` | End to end: real commands on real files, scripted interactive sessions, and a real `python -m budget_manager` subprocess |
| `tests/test_architecture.py` | The layer rules: imports point inward, and each module sticks to its responsibility |

## 5. Current known limitations

- **No currency conversion.** Each currency is totalled separately. There are no exchange rates.
- **Fixed vocabularies.** Categories and currencies are set in `src/budget_manager/models.py`.
  Supported currencies are EUR, USD, GBP, CHF (all of them use 2 decimal places).
- **No undo.** Edit and delete save immediately. Use `backup` first if you're making several changes.
- **Ids can be reused at the end.** Deleting the *newest* transaction frees its id for the next
  `add`. Ids deleted from the middle are never reused.
- **An id can't be edited.** Delete the transaction and add a new one instead.
- **No notes or description field.** The schema is exactly `id, date, kind, category, amount, currency`.
- **One user, one file.** If two programs write the ledger at the same moment, the last save wins.
  A save never leaves the file half-written, though.
- **Strict input.** `Food`, `eur`, `18,50` and ` food` are rejected with a suggested fix,
  not corrected automatically. This is deliberate (see DESIGN.md §5).
- **No budgets or limits yet** (e.g. "warn me when food goes over 300 a month").
