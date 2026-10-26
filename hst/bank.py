"""Bank statement lines: match to receipts, or classify and estimate when there is no receipt."""
import csv
import io
import re
from datetime import date, datetime
from pathlib import Path
from decimal import Decimal

from .rules import back_calculate, category_for, itc_pct

ZERO = Decimal("0.00")
WINDOW_DAYS = (-1, 5)  # bank posting date minus receipt date


DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y"]  # ISO or Canadian-bank MM/DD/YYYY. Anything else is skipped.
HEADERS = {
    "date": ("date", "transaction date", "posted date", "posting date"),
    "description": ("description", "details", "memo", "payee", "transaction description"),
    "amount": ("amount",),
    "debit": ("debit", "withdrawal", "withdrawals", "money out"),
    "credit": ("credit", "deposit", "deposits", "money in"),
}


def _parse_date(raw):
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _money(raw):
    raw = (raw or "").replace("$", "").replace(",", "").strip()
    neg = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()")
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except ArithmeticError:
        return None
    return -value if neg else value


def parse_bank_csv(text):
    """Flexible bank CSV: amount column (negative = money out) or separate debit/credit columns.
    Returns (rows, skipped_count)."""
    reader = csv.DictReader(io.StringIO(text))
    cols = {(h or "").strip().lower(): h for h in (reader.fieldnames or [])}
    pick = {k: next((cols[n] for n in names if n in cols), None) for k, names in HEADERS.items()}
    rows, skipped = [], 0
    for r in reader:
        when = _parse_date(r.get(pick["date"], "")) if pick["date"] else None
        desc = (r.get(pick["description"]) or "").strip() if pick["description"] else ""
        if pick["amount"]:
            amount = _money(r.get(pick["amount"]))
        else:
            debit = _money(r.get(pick["debit"])) if pick["debit"] else None
            credit = _money(r.get(pick["credit"])) if pick["credit"] else None
            amount = -abs(debit) if debit else (abs(credit) if credit else None)
        if when and desc and amount is not None:
            rows.append({"date": when, "description": desc, "amount": amount})
        else:
            skipped += 1
    return rows, skipped


def read_bank_csv(path):
    return parse_bank_csv(Path(path).read_text(encoding="utf-8-sig"))[0]


def _tokens(s):
    return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if len(t) >= 3}


def match_bank(rows, receipts, rules):
    """receipts: list of assess results with 'file', 'fields', 'status'. Returns one result per bank row."""
    used, out = set(), []
    for idx, row in enumerate(rows):
        res = {"index": idx, **{k: (str(v) if isinstance(v, Decimal) else v) for k, v in row.items()},
               "kind": None, "receipt": None, "est_hst": "0.00", "est_itc": "0.00", "note": ""}
        out.append(res)
        if row["amount"] > 0:
            res.update(kind="deposit", note="Money in: sales HST is outside this demo.")
            continue
        amount = -row["amount"]
        cands = []
        for r in receipts:
            f = r["fields"]
            if r["file"] in used or r["status"] == "none" and "DUPLICATE" in r["flags"]:
                continue
            if f["total"] is None or f["date"] is None or abs(Decimal(f["total"]) - amount) > Decimal("0.01"):
                continue
            lag = (date.fromisoformat(row["date"]) - date.fromisoformat(f["date"])).days
            if WINDOW_DAYS[0] <= lag <= WINDOW_DAYS[1]:
                cands.append((len(_tokens(row["description"]) & _tokens(f["vendor"] or "")), r))
        if cands:
            overlap, best = max(cands, key=lambda c: c[0])
            used.add(best["file"])
            res.update(kind="matched", receipt=best["file"],
                       note="Matched to receipt." if overlap else "Weak match (amount and date only): confirm.")
            if not overlap:
                res["kind"] = "weak_match"
            continue
        desc = row["description"].lower()
        if any(k in desc for k in rules["bank_non_taxable_keywords"]):
            res.update(kind="non_taxable", note="Fee, payroll, transfer or interest: no HST claim.")
            continue
        cat = category_for(row["description"], rules)
        if cat is None:
            res.update(kind="unknown", note="Unrecognised payee: needs a category and a receipt.")
        elif not rules["categories"][cat]["expects_tax"]:
            res.update(kind="non_taxable", note=rules["categories"][cat]["reason"])
        else:
            est = back_calculate(amount, rules)
            res.update(kind="missing_receipt", est_hst=str(est),
                       est_itc=str((est * itc_pct(cat, rules)).quantize(Decimal("0.01"))),
                       note="No receipt found: HST is an estimate and cannot be claimed without one.")
    return out
