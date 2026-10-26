"""Receipts + bank statement -> assessed receipts, matched bank lines, period summary."""
import csv
import json
from decimal import Decimal
from pathlib import Path

from .assess import assess_receipt
from .bank import match_bank, read_bank_csv
from .rules import load_rules

D = Decimal


def process_folder(folder, rules=None):
    rules = rules or load_rules()
    folder = Path(folder)
    seen, receipts = set(), []
    for path in sorted(folder.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        res = assess_receipt(text, rules, seen)
        res.update(file=path.name, text=text)
        receipts.append(res)
    bank_path = folder / "bank.csv"
    bank = match_bank(read_bank_csv(bank_path), receipts, rules) if bank_path.exists() else []
    return receipts, bank


def summarize(receipts, bank, decisions=None):
    """decisions: {file: 'approved'|'rejected'} reviewer choices on 'review' receipts."""
    decisions = decisions or {}
    s = {k: D("0") for k in ("hst_paid", "claimable", "not_claimable", "under_review_hst", "pending",
                             "missing_est_itc", "missing_spend")}
    counts = {"receipts": 0, "claim": 0, "partial": 0, "none": 0, "review": 0, "duplicates": 0,
              "missing_receipts": 0, "weak_matches": 0}
    for r in receipts:
        if "DUPLICATE" in r["flags"]:
            counts["duplicates"] += 1
            continue
        counts["receipts"] += 1
        counts[r["status"]] += 1
        hst, claim, pend = D(r["hst_amount"]), D(r["itc_claimable"]), D(r["itc_pending"])
        s["hst_paid"] += hst
        d = decisions.get(r["file"])
        if r["status"] == "review":
            if d == "approved":
                s["claimable"] += pend
                s["not_claimable"] += hst - pend
            elif d == "rejected":
                s["not_claimable"] += hst
            else:
                s["under_review_hst"] += hst
                s["pending"] += pend
        else:
            s["claimable"] += claim
            s["not_claimable"] += hst - claim
    for b in bank:
        if b["kind"] == "missing_receipt":
            counts["missing_receipts"] += 1
            s["missing_est_itc"] += D(b["est_itc"])
            s["missing_spend"] += -D(b["amount"])
        elif b["kind"] == "weak_match":
            counts["weak_matches"] += 1
    return {"amounts": {k: str(v.quantize(D("0.01"))) for k, v in s.items()}, "counts": counts}


def write_outputs(receipts, bank, summary, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with open(out / "ledger.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "vendor", "date", "category", "total", "hst", "hst_source",
                    "status", "itc_claimable", "itc_pending", "reasons"])
        for r in receipts:
            f = r["fields"]
            w.writerow([r["file"], f["vendor"], f["date"], r["category"], f["total"], r["hst_amount"],
                        r["hst_source"], r["status"], r["itc_claimable"], r["itc_pending"],
                        " | ".join(r["reasons"])])
    with open(out / "missing_receipts.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "description", "amount", "estimated_hst", "estimated_itc"])
        for b in bank:
            if b["kind"] == "missing_receipt":
                w.writerow([b["date"], b["description"], b["amount"], b["est_hst"], b["est_itc"]])
    (out / "results.json").write_text(json.dumps({"receipts": [
        {k: v for k, v in r.items() if k != "text"} for r in receipts], "bank": bank}, indent=2),
        encoding="utf-8")
