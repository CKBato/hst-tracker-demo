# HST Tracker for Small Business (Ontario demo)

Snap a receipt, and know the HST in it, whether it's claimable, and why. Match the bank statement to those receipts and list the payments that still have no receipt. Runs offline with Python's standard library only. All data is synthetic.

> **Not tax advice.** The rules are simplified for illustration (Ontario, 13%). An accountant must review `hst/rules.json` before any real use.

## The problem
Small businesses in Canada keep receipts to claim HST input tax credits, but separating HST from totals, deciding what is claimable, and finding payments with no receipt is slow and error-prone. Over-claiming is the costly mistake, so this tool is built to **never claim what it is unsure about**.

## How it works
```
receipt text -> read fields -> checks -> claimable? (with reason) -> ledger
bank CSV     -> match to receipts | estimate when no receipt | ignore non-taxable -> missing-receipt list
                                                              -> period summary
```
- **Read** (`hst/extract.py`): vendor, date, supplier registration number, subtotal, HST, total, across three receipt layouts.
- **Assess** (`hst/assess.py`): checks subtotal + HST = total and HST = 13% of subtotal; back-calculates HST from a tax-inclusive total (`total x 13/113`) and flags it; flags a missing registration number on purchases of $30 or more (simplified threshold), unknown vendors, unexpected HST, duplicates.
- **Rules** (`hst/rules.json`): readable config. Full credit for ordinary business expenses, 50% for meals and entertainment, none for personal items and club memberships, none (no HST charged) for basic groceries and insurance. Every decision carries a plain-language reason.
- **Bank** (`hst/bank.py`): matches each debit to a receipt by amount, date window and payee words. Unmatched taxable payments get a clearly-labelled **estimate** and go on a "get the receipt" list. Fees, payroll, transfers and interest are marked no-HST. Deposits are ignored (sales HST is outside this demo).
- **Summary** (`hst/pipeline.py`): HST paid, claimable credits, not claimable, held for review, missing receipts. A reviewer can approve or reject held items; approval moves the amount into claimable. Writes `ledger.csv`, `missing_receipts.csv`, `results.json`, `summary.json`.

## Run it
```bash
python run.py                  # generate synthetic receipts + bank statement, process, print summary and evaluation
python ui.py                   # web UI at http://127.0.0.1:8766 (localhost only)
python -m unittest discover -s tests -t .
```
The UI has **Check a receipt** (paste or open a text receipt, see fields, status and reasons) and **Period dashboard** (generate a batch, browse receipts, bank lines and missing receipts, approve or reject held items).

## Results (60 synthetic receipts, 55 bank lines, seed 7)
| Metric | Result |
|---|---|
| Receipt status matches expected | 100% (7 defect types, 60/60) |
| HST amount correct on clean receipts | 35/35 |
| Back-calculated HST (no tax line) | 7/7 |
| Bank matches: precision / recall | 100% / 100% |
| Missing-receipt payments found | 7/7 |
| Dollars over-claimed | $0 |
| Credits claimed vs expected | $502.91 vs $502.94 (rounding) |

Seeds 1-5 at 100 receipts: no over-claims and 100% match precision and recall every time.

## Honest limits
- **Best case.** I wrote the generator and extractor together, so it knows all three layouts. Real receipts will score lower. The meaningful result is the safety behavior: anything unreadable, unrecognised, inconsistent or undocumented is held for review and never counted as claimable (see the tests).
- **Input is text.** Reading a photo needs an OCR or vision-model step in front of `extract()`. I have not built or tested one here.
- **Simplified rules.** Ontario only, no Quick Method, no capital property or vehicle limits, no mixed personal/business use, no partial zero-rated items. Category comes from vendor keywords.
- **Bank estimates are estimates.** A bank line has no tax detail, so HST for unmatched payments is calculated and labelled, and is not claimable without a receipt.
- Documentation thresholds and credit percentages are simplified. Verify against current CRA guidance.

## What a real engagement adds
1. Photo/PDF capture (mobile upload, email-forward) with OCR or a vision model, measured on the client's real receipts.
2. Client-specific rules reviewed and signed off by their accountant, including the provinces they sell in.
3. Bank feeds and write-back to QuickBooks, Xero or their accountant's tools.
4. Sales-side HST and the full return worksheet.
5. Receipt image retention and an audit trail for CRA record-keeping.

## Files
`run.py`, `ui.py`, `hst/` (rules.json, rules, extract, assess, bank, pipeline, evaluate, generate), `tests/`. `data/` and `output/` are regenerated on each run.
