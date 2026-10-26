"""Score receipts and bank matching against ground truth."""
from collections import Counter, defaultdict
from decimal import Decimal

D = Decimal
TOL = D("0.01")


def evaluate(receipts, bank, truth):
    tr, tb = truth["receipts"], truth["bank"]
    n = len(receipts)

    status_ok, by_defect = 0, defaultdict(lambda: [0, 0])
    hst_clean = [0, 0]
    backcalc = [0, 0]
    claimed = expected_total = overclaim = D(0)
    unsafe = []
    for r in receipts:
        t = tr[r["file"]]
        ok = r["status"] == t["expected_status"]
        status_ok += ok
        by_defect[t["defect"]][0] += ok
        by_defect[t["defect"]][1] += 1
        if t["defect"] == "none":
            hst_clean[1] += 1
            hst_clean[0] += abs(D(r["hst_amount"]) - D(t["hst"])) <= TOL
        if t["defect"] == "no_tax_line":
            backcalc[1] += 1
            backcalc[0] += abs(D(r["hst_amount"]) - D(t["hst"])) <= TOL
        got, want = D(r["itc_claimable"]), D(t["expected_itc"])
        claimed, expected_total = claimed + got, expected_total + want
        if got > want + TOL:
            overclaim += got - want
            unsafe.append(r["file"])

    truth_match = {i: b["receipt"] for i, b in enumerate(tb) if b["label"] == "matched"}
    pred_match = {b["index"]: b["receipt"] for b in bank if b["kind"] in ("matched", "weak_match")}
    correct = sum(1 for i, f in pred_match.items() if truth_match.get(i) == f)
    kinds_ok = Counter()
    kinds_total = Counter()
    for i, b in enumerate(tb):
        label = b["label"]
        got = bank[i]["kind"]
        got = "matched" if got == "weak_match" else got
        kinds_total[label] += 1
        kinds_ok[label] += got == label
    est_expected = sum(D(b["expected_est_itc"]) for b in tb if b["label"] == "missing_receipt")
    est_got = sum(D(b["est_itc"]) for b in bank if b["kind"] == "missing_receipt")

    return {
        "receipts": n,
        "status_accuracy": round(status_ok / n, 3),
        "status_accuracy_by_defect": {d: f"{a}/{t}" for d, (a, t) in sorted(by_defect.items())},
        "hst_amount_accuracy_clean_receipts": f"{hst_clean[0]}/{hst_clean[1]}",
        "back_calculation_accuracy": f"{backcalc[0]}/{backcalc[1]}",
        "itc_claimed": str(claimed), "itc_expected": str(expected_total),
        "overclaimed_dollars": str(overclaim), "unsafe_claims": unsafe,
        "bank_match_precision": round(correct / len(pred_match), 3) if pred_match else None,
        "bank_match_recall": round(correct / len(truth_match), 3) if truth_match else None,
        "bank_classification": {k: f"{kinds_ok[k]}/{kinds_total[k]}" for k in sorted(kinds_total)},
        "missing_receipt_est_itc": {"expected": str(est_expected), "got": str(est_got)},
    }
