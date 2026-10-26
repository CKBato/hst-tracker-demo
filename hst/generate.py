"""Synthetic Ontario receipts + bank statement with ground truth. All fake, seeded.

The expected-answer encoding below is deliberately independent of rules.json so the
evaluation checks the rules engine instead of echoing it.
"""
import csv
import json
import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

RATE = Decimal("0.13")
VENDORS = [
    ("Staples Business Depot", "office_supplies"), ("Home Depot", "office_supplies"),
    ("Petro-Canada", "fuel"), ("Bell Canada", "phone_internet"), ("Adobe Systems", "software"),
    ("The Keg Steakhouse", "meals"), ("Boston Pizza", "meals"), ("Ticketmaster", "entertainment"),
    ("Ace Accounting LLP", "professional_fees"), ("GoodLife Fitness", "club_membership"),
    ("Sephora", "personal"), ("Loblaws", "groceries"), ("Intact Insurance", "insurance"),
]
UNKNOWN_VENDORS = ["Joe's Place", "Maple Trading Co"]
RANGES = {"office_supplies": (8, 300), "fuel": (25, 120), "phone_internet": (40, 180),
          "software": (10, 150), "meals": (12, 220), "entertainment": (60, 400),
          "professional_fees": (200, 2500), "club_membership": (40, 120), "personal": (15, 200),
          "groceries": (20, 250), "insurance": (90, 600), "unknown": (15, 400)}
GEN_PCT = {"meals": Decimal("0.5"), "entertainment": Decimal("0.5"), "club_membership": Decimal(0),
           "personal": Decimal(0), "groceries": Decimal(0), "insurance": Decimal(0)}
ZERO_TAX = {"groceries", "insurance"}
DEFECTS = [("none", 0.62), ("missing_bn", 0.08), ("math_error", 0.07), ("no_tax_line", 0.07),
           ("duplicate", 0.06), ("unknown_vendor", 0.06), ("cash_only", 0.04)]
NON_TAXABLE_BANK = ["TD SERVICE FEE", "PAYROLL RUN", "E-TRANSFER SENT", "LOAN INTEREST", "CRA PAYMENT"]


def money(x):
    return Decimal(x).quantize(Decimal("0.01"), ROUND_HALF_UP)


def pct(category):
    return GEN_PCT.get(category, Decimal(1))


def render(r, layout):
    v, bn = r["vendor"], r["registration_number"]
    d = date.fromisoformat(r["date"])
    sub, hst, tot = r["_sub"], r["_hst_printed"], r["total"]
    itemize = r["defect"] != "no_tax_line" and r["category_expects_tax"]
    if layout == "A":
        out = [v.upper(), "Store #0123 Toronto ON"]
        if bn:
            out.append(f"HST# {bn}")
        out += [f"Date: {d.isoformat()}", "1 x item", ""]
        if itemize:
            out += [f"Subtotal {sub}", f"HST (13%) {hst}"]
        out.append(f"TOTAL {tot}" + (" (HST included)" if r["defect"] == "no_tax_line" else ""))
    elif layout == "B":
        out = ["INVOICE", f"From: {v}"]
        if bn:
            out.append(f"GST/HST Reg No: {bn}")
        out += [f"Invoice date: {d.strftime('%d/%m/%Y')}", "Description: services", ""]
        if itemize:
            out += [f"Amount before tax: {sub}", f"HST 13%: {hst}"]
        out.append(f"Amount due: {tot} CAD")
    else:
        out = [f"Merchant: {v}"]
        if bn:
            out.append(f"Tax Reg #: {bn}")
        out += [f"Txn date: {d.strftime('%b %d, %Y')}", ""]
        if itemize:
            out += [f"Net {sub}", f"H.S.T. 13% {hst}"]
        out.append(f"Amount {tot}")
    return "\n".join(out) + "\n"


def make_receipt(rng, vendor, category, day_offset=None):
    lo, hi = RANGES[category]
    sub = money(rng.uniform(lo, hi))
    hst = Decimal("0.00") if category in ZERO_TAX else money(sub * RATE)
    d = date(2025, 1, 1) + timedelta(days=day_offset if day_offset is not None else rng.randint(0, 88))
    return {
        "vendor": vendor, "category": category, "category_expects_tax": category not in ZERO_TAX,
        "date": d.isoformat(), "registration_number": f"{rng.randint(10**8, 10**9 - 1)}RT0001",
        "subtotal": str(sub), "hst": str(hst), "total": str(sub + hst),
        "_sub": sub, "_hst_printed": hst,
    }


def expected(r):
    """Independent statement of the right answer for a receipt."""
    d = r["defect"]
    hst, total, cat = Decimal(r["hst"]), Decimal(r["total"]), r["category"]
    if d == "duplicate":
        return "none", Decimal(0)
    if d in ("unknown_vendor", "math_error", "no_tax_line"):
        return "review", Decimal(0)
    if d == "missing_bn" and hst > 0 and total >= 30:
        return "review", Decimal(0)
    p = pct(cat)
    status = "none" if p == 0 else ("claim" if p == 1 else "partial")
    return status, money(hst * p)


def bank_description(vendor, rng):
    base = vendor.upper().replace("'", "")[:22]
    return f"{base} #{rng.randint(100, 9999)} TORONTO ON"


def generate(out_dir, n=60, seed=7):
    rng = random.Random(seed)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in list(out_dir.glob("*.txt")) + list(out_dir.glob("*.csv")):
        old.unlink()
    receipts, bank, truth_r, clean_pool = {}, [], {}, []

    for i in range(n):
        defect = rng.choices([d for d, _ in DEFECTS], [w for _, w in DEFECTS])[0]
        layout = rng.choice("ABC")
        if defect == "duplicate" and clean_pool:
            src = rng.choice(clean_pool)
            name = f"rcpt_{i:03d}.txt"
            receipts[name] = receipts[src]
            truth_r[name] = {**truth_r[src], "defect": "duplicate", "duplicate_of": src,
                             "has_bank_line": False}
            st, itc = expected(truth_r[name])
            truth_r[name].update(expected_status=st, expected_itc=str(itc))
            continue
        if defect == "duplicate":
            defect = "none"
        if defect == "unknown_vendor":
            vendor, category = rng.choice(UNKNOWN_VENDORS), "unknown"
        else:
            vendor, category = rng.choice(VENDORS)
        rec = make_receipt(rng, vendor, category)
        if defect in ("missing_bn", "no_tax_line", "math_error") and Decimal(rec["hst"]) == 0:
            defect = "none"
        rec["defect"] = defect
        if defect == "math_error":
            rec["_hst_printed"] = money(rec["_hst_printed"] + Decimal(rng.randint(2, 9)))
            if rec["_hst_printed"] == Decimal(rec["hst"]):
                rec["defect"] = "none"
        if defect == "missing_bn":
            rec["registration_number"] = None
        text = render(rec, layout)
        name = f"rcpt_{i:03d}.txt"
        receipts[name] = text
        st, itc = expected(rec)
        truth_r[name] = {k: v for k, v in rec.items() if not k.startswith("_")}
        truth_r[name].update(layout=layout, expected_status=st, expected_itc=str(itc), has_bank_line=False)
        if defect == "none" and st != "review":
            clean_pool.append(name)
        if defect != "cash_only" and rng.random() < 0.85:
            lag = rng.randint(0, 3)
            when = date.fromisoformat(rec["date"]) + timedelta(days=lag)
            bank.append({"date": when.isoformat(), "description": bank_description(vendor, rng),
                         "amount": f"-{rec['total']}", "label": "matched", "receipt": name})
            truth_r[name]["has_bank_line"] = True

    # bank-only lines: taxable purchases with no receipt, non-taxable items, deposits
    taxable = [(v, c) for v, c in VENDORS if c not in ZERO_TAX]
    for _ in range(max(2, n // 8)):
        vendor, category = rng.choice(taxable)
        rec = make_receipt(rng, vendor, category)
        est = money(Decimal(rec["total"]) * RATE / (1 + RATE))
        bank.append({"date": rec["date"], "description": bank_description(vendor, rng),
                     "amount": f"-{rec['total']}", "label": "missing_receipt", "receipt": None,
                     "expected_est_itc": str(money(est * pct(category))), "category": category})
    for _ in range(max(2, n // 8)):
        d = date(2025, 1, 1) + timedelta(days=rng.randint(0, 88))
        bank.append({"date": d.isoformat(), "description": rng.choice(NON_TAXABLE_BANK),
                     "amount": f"-{money(rng.uniform(5, 3000))}", "label": "non_taxable", "receipt": None})
    for _ in range(max(1, n // 15)):
        d = date(2025, 1, 1) + timedelta(days=rng.randint(0, 88))
        bank.append({"date": d.isoformat(), "description": "CLIENT DEPOSIT",
                     "amount": str(money(rng.uniform(500, 8000))), "label": "deposit", "receipt": None})

    rng.shuffle(bank)
    bank.sort(key=lambda b: b["date"])
    for name, text in receipts.items():
        (out_dir / name).write_text(text, encoding="utf-8")
    with open(out_dir / "bank.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "description", "amount"])
        for b in bank:
            w.writerow([b["date"], b["description"], b["amount"]])
    truth = {"receipts": truth_r, "bank": bank}
    (out_dir / "ground_truth.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
    return truth


if __name__ == "__main__":
    import sys
    generate(sys.argv[1] if len(sys.argv) > 1 else "data")
