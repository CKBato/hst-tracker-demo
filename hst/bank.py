"""Bank statement lines: match to receipts, or classify and estimate when there is no receipt."""
import csv
import re
from datetime import date
from decimal import Decimal

from .rules import back_calculate, category_for, itc_pct

ZERO = Decimal("0.00")
WINDOW_DAYS = (-1, 5)  # bank posting date minus receipt date


def read_bank_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return [{"date": r["date"], "description": r["description"], "amount": Decimal(r["amount"])}
                for r in csv.DictReader(fh)]


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
