# HST Tracker for Small Business (Ontario demo)

Photograph a receipt on your phone, or drop a whole folder of photos on your PC, and get the HST in it, whether it is claimable, and why. Match the bank statement to those receipts and list the payments that still have no receipt.

> **Not tax advice.** The rules are simplified for illustration (Ontario, 13%). An accountant must review `hst/rules.json` before any real use. Check receipt records against CRA requirements before relying on any figure.

## The problem
Small businesses in Canada keep receipts to claim HST input tax credits, but separating HST from totals, deciding what is claimable, and finding payments with no receipt is slow and error-prone. Over-claiming is the costly mistake, so this tool is built to **never claim what it is unsure about**.

## How it works
```
phone photo / PC folder -> read the photo (OCR) -> read fields -> checks -> claimable? + reason -> ledger
bank CSV -> match to receipts | estimate when no receipt | ignore non-taxable -> missing-receipt list -> summary
```
- **Photo reading** (`hst/ocr.py`), switchable engine:
  - **Local OCR (Tesseract)**: free, private, nothing leaves your computer. Default.
  - **Claude vision**: set `ANTHROPIC_API_KEY`. Usually better on messy photos, but the image is sent to Anthropic and costs money per receipt.
- **Read** (`hst/extract.py`): vendor, date, supplier registration number, subtotal, HST, total. Tolerates common OCR noise.
- **Assess** (`hst/assess.py`): checks the maths (subtotal + HST = total, HST = 13%, or 13/113 of the total when there is no subtotal), back-calculates untaxed totals and flags them, requires a supplier registration number from $30 (simplified), rejects implausible dates, flags unknown vendors, unexpected HST, and duplicates. For photos it also holds back anything with low OCR confidence and anything that looks like an earlier receipt with a misread date.
- **Rules** (`hst/rules.json`): readable config. Full credit for ordinary business expenses, 50% for meals and entertainment, none for personal items and club memberships, none (no HST charged) for basic groceries and insurance. Every decision has a plain-language reason.
- **Bank** (`hst/bank.py`): flexible CSV (amount or debit/credit columns). Matches debits to receipts by amount, date window and payee words. Unmatched taxable payments get a labelled **estimate** and go on the "get the receipt" list.
- **Store** (`hst/store.py`): photos and text are kept in `store/` (private, git-ignored). Everything else is recomputed, so a rules fix applies to old receipts too.

## Run it
Install Tesseract once (Windows):
```bash
winget install UB-Mannheim.TesseractOCR
```
```bash
python ui.py            # this PC:  http://127.0.0.1:8766
python ui.py --lan      # also your phone: prints a private link with a token
```
- **Phone:** run `--lan`, open the printed link on a phone on the same Wi-Fi, tap **Take photo of receipt**. Allow Python through the Windows firewall for private networks when asked. The link carries a secret token and only people with it can use the app. It is plain HTTP on your local network, so use trusted Wi-Fi only.
- **PC bulk:** open **Import & tools**, type a folder such as `C:\Users\you\Documents\hst2026`, and click Import. Subfolders are included, photos already imported are skipped, and progress is shown. You can also drag photos onto the Capture tab. Folder import is only allowed from the PC itself, never from the phone link.
- **Bank:** upload a CSV on the **Bank** tab. Download `hst_ledger.csv` and `missing_receipts.csv` for your accountant.
- **Review:** held receipts show the photo next to what was read, with Approve and Reject buttons.
- **Command line:** `python run.py` (text-based synthetic run) and `python -m unittest discover -s tests -t .`

## Results
**Rules and matching on synthetic receipt text** (60 receipts, 64 bank lines): status correct 60/60, bank match precision and recall 100%, 7/7 missing receipts found, $0 over-claimed.

**Photo path with the real Tesseract engine** (`python eval_photos.py 80 <seed>`, five seeds, 400 receipts per condition). Receipts are rendered to images, then read back with Tesseract. "Phone" adds rotation, blur, noise and compression.

| Condition | Matches expected status | Sent to review instead | Over-claimed receipts |
|---|---|---|---|
| Clean render | 323/400 (81%) | 77 | 0 |
| Phone-like degraded | 264/400 (66%) | 136 | 0 |

Every miss went to review and none was claimed. Typical causes: a garbled supplier number, a misread digit caught by the maths check (for example 185.00 read as 185.08), or an unreadable date. Along the way the test found two real gaps that are now fixed: a year misread as 2825 was accepted, and a duplicate whose original had an unreadable date was claimed twice.

## Honest limits
- **These are not real phone photos.** I rendered receipts from synthetic text in a clean monospace font. Real receipts (thermal paper, crumpled, glare, shadows, odd fonts, long item lists) will read worse. Measure on 50 to 100 of your own receipts before trusting the numbers.
- **The Claude vision engine has not been run against the real API** (no key here). It is unit-tested with a mocked response only.
- **HEIC photos:** the phone page converts to JPEG before upload. For folder import, convert HEIC to JPG first.
- **Registration numbers are not verified** against the CRA registry. Only presence is checked.
- **Simplified rules:** Ontario only, no Quick Method, no vehicle limits, no mixed personal and business use, no partial zero-rated items. Category comes from vendor keywords.
- **Bank estimates are estimates**, not claimable without a receipt.
- **Security:** `--lan` is plain HTTP with a shared token and no user accounts. Fine for a trusted home or office network, not for the public internet. Receipt photos are stored unencrypted in `store/`.

## What a real engagement adds
1. Measurement on the client's real receipts and tuning of the OCR or vision step.
2. Rules signed off by the client's accountant, including every province they trade in.
3. Real accounts, HTTPS, encrypted storage and backups of the receipt images.
4. Bank feeds and write-back to QuickBooks, Xero or their accountant's tools.
5. Sales-side HST and the full return worksheet.

## Files
`ui.py` (web app), `run.py`, `eval_photos.py`, `hst/` (ocr, extract, assess, rules.json, rules, bank, store, pipeline, evaluate, generate), `tests/` (29 tests). `data/`, `output/` and `store/` are generated and git-ignored.
