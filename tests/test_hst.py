import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from hst.assess import assess_receipt
from hst.evaluate import evaluate
from hst.generate import generate
from hst.pipeline import process_folder, summarize
from hst.rules import back_calculate, load_rules

RULES = load_rules()


def receipt(vendor="Staples Business Depot", bn="HST# 123456789RT0001", sub="100.00", hst="13.00",
            total="113.00", date="2025-03-14"):
    lines = [vendor.upper(), bn, f"Date: {date}"]
    if sub:
        lines.append(f"Subtotal {sub}")
    if hst:
        lines.append(f"HST (13%) {hst}")
    lines.append(f"TOTAL {total}")
    return "\n".join(l for l in lines if l) + "\n"


class Assess(unittest.TestCase):
    def test_back_calculation_is_13_over_113(self):
        self.assertEqual(back_calculate(Decimal("113.00"), RULES), Decimal("13.00"))

    def test_normal_office_purchase_is_fully_claimable(self):
        r = assess_receipt(receipt(), RULES, set())
        self.assertEqual((r["status"], r["itc_claimable"]), ("claim", "13.00"))

    def test_meals_only_half(self):
        r = assess_receipt(receipt(vendor="The Keg Steakhouse"), RULES, set())
        self.assertEqual((r["status"], r["itc_claimable"]), ("partial", "6.50"))

    def test_personal_and_club_not_claimable(self):
        for vendor in ("Sephora", "GoodLife Fitness"):
            r = assess_receipt(receipt(vendor=vendor), RULES, set())
            self.assertEqual((r["status"], r["itc_claimable"]), ("none", "0.00"))

    def test_zero_rated_groceries_have_no_hst(self):
        r = assess_receipt(receipt(vendor="Loblaws", sub=None, hst=None, total="80.00"), RULES, set())
        self.assertEqual((r["status"], r["hst_amount"]), ("none", "0.00"))

    def test_missing_registration_number_blocks_claim_over_30(self):
        r = assess_receipt(receipt(bn=""), RULES, set())
        self.assertEqual(r["status"], "review")
        self.assertIn("MISSING_BN", r["flags"])
        self.assertEqual(r["itc_claimable"], "0.00")

    def test_small_receipt_needs_no_registration_number(self):
        r = assess_receipt(receipt(bn="", sub="20.00", hst="2.60", total="22.60"), RULES, set())
        self.assertEqual(r["status"], "claim")

    def test_wrong_hst_goes_to_review_not_claimed(self):
        r = assess_receipt(receipt(hst="20.00"), RULES, set())
        self.assertEqual(r["status"], "review")
        self.assertEqual(r["itc_claimable"], "0.00")

    def test_untaxed_total_is_back_calculated_and_flagged(self):
        r = assess_receipt(receipt(sub=None, hst=None, total="113.00"), RULES, set())
        self.assertEqual((r["hst_amount"], r["status"]), ("13.00", "review"))
        self.assertIn("TAX_NOT_ITEMIZED", r["flags"])

    def test_unknown_vendor_is_never_claimed(self):
        r = assess_receipt(receipt(vendor="Mystery Shop"), RULES, set())
        self.assertEqual(r["status"], "review")
        self.assertEqual(r["itc_claimable"], "0.00")

    def test_duplicate_not_counted_twice(self):
        seen = set()
        assess_receipt(receipt(), RULES, seen)
        r = assess_receipt(receipt(), RULES, seen)
        self.assertIn("DUPLICATE", r["flags"])
        self.assertEqual(r["itc_claimable"], "0.00")

    def test_unreadable_receipt_fails_safe(self):
        r = assess_receipt("blurry nonsense\n", RULES, set())
        self.assertEqual(r["status"], "review")
        self.assertEqual(r["itc_claimable"], "0.00")


class EndToEnd(unittest.TestCase):
    def test_generated_set_never_overclaims_and_summary_adds_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            truth = generate(Path(tmp), n=80, seed=3)
            receipts, bank = process_folder(Path(tmp))
            report = evaluate(receipts, bank, truth)
            self.assertEqual(report["unsafe_claims"], [])
            self.assertEqual(Decimal(report["overclaimed_dollars"]), 0)
            s = summarize(receipts, bank)["amounts"]
            total = (Decimal(s["claimable"]) + Decimal(s["under_review_hst"])
                     + Decimal(s["not_claimable"]))
            self.assertEqual(total, Decimal(s["hst_paid"]))

    def test_review_approval_moves_pending_to_claimable(self):
        with tempfile.TemporaryDirectory() as tmp:
            generate(Path(tmp), n=60, seed=7)
            receipts, bank = process_folder(Path(tmp))
            first = next(r for r in receipts if r["status"] == "review" and Decimal(r["itc_pending"]) > 0)
            before = summarize(receipts, bank)["amounts"]
            after = summarize(receipts, bank, {first["file"]: "approved"})["amounts"]
            self.assertEqual(Decimal(after["claimable"]) - Decimal(before["claimable"]),
                             Decimal(first["itc_pending"]))


if __name__ == "__main__":
    unittest.main()
