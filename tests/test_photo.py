import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from hst.assess import assess_receipt
from hst.bank import parse_bank_csv
from hst.extract import extract
from hst.ocr import engines_available, parse_tsv, read_claude
from hst.rules import load_rules
from hst.store import Store

RULES = load_rules()
GOOD = ("STAPLES BUSINESS DEPOT\nHST# 123456789RT0001\nDate: 2025-03-14\n"
        "Subtotal 100.00\nHST (13%) 13.00\nTOTAL 113.00\n")


class TsvAndEngines(unittest.TestCase):
    def test_parse_tsv_rebuilds_lines_and_confidence(self):
        head = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
        rows = [head,
                "5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t90.0\tTOTAL",
                "5\t1\t1\t1\t1\t2\t0\t0\t1\t1\t80.0\t113.00",
                "5\t1\t1\t1\t2\t1\t0\t0\t1\t1\t-1\t",
                "5\t1\t1\t1\t2\t2\t0\t0\t1\t1\t70.0\tHST"]
        text, conf = parse_tsv("\n".join(rows))
        self.assertEqual(text, "TOTAL 113.00\nHST\n")
        self.assertAlmostEqual(conf, 80.0)

    def test_claude_engine_request_shape_and_parsing(self):
        calls = []

        def fake_post(url, headers, body):
            calls.append((url, headers, json.loads(body)))
            return {"content": [{"type": "text", "text": "Merchant: Bell Canada\nTotal 51.13"}]}

        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "r.jpg"
            img.write_bytes(b"\xff\xd8fake")
            os.environ["ANTHROPIC_API_KEY"] = "test-key"
            try:
                res = read_claude(img, post=fake_post)
            finally:
                del os.environ["ANTHROPIC_API_KEY"]
        url, headers, body = calls[0]
        self.assertTrue(url.endswith("/v1/messages"))
        self.assertEqual(headers["x-api-key"], "test-key")
        content = body["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "image")
        self.assertEqual(content[0]["source"]["media_type"], "image/jpeg")
        self.assertIn("Merchant: Bell Canada", res["text"])
        self.assertIsNone(res["confidence"])

    def test_not_a_receipt_yields_no_fields_and_review(self):
        def fake_post(url, headers, body):
            return {"content": [{"type": "text", "text": "NOT A RECEIPT"}]}

        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp) / "r.png"
            img.write_bytes(b"x")
            os.environ["ANTHROPIC_API_KEY"] = "k"
            try:
                res = read_claude(img, post=fake_post)
            finally:
                del os.environ["ANTHROPIC_API_KEY"]
        r = assess_receipt(res["text"], RULES, set())
        self.assertEqual((r["status"], r["itc_claimable"]), ("review", "0.00"))


class OcrNoise(unittest.TestCase):
    def test_extract_tolerates_ocr_noise(self):
        text = "| STAPLES DEPOT\nHST# 123456789RTQ001\nDate: 2025-03-14 14:22\nSubtotal 100.00\nH5T (13%) 13.00\nT0TAL 113.00\n"
        f = extract(text)
        self.assertEqual((f["date"], f["hst"], f["total"]), ("2025-03-14", Decimal("13.00"), Decimal("113.00")))
        self.assertTrue(f["registration_number"].startswith("123456789RT"))

    def test_implausible_date_is_not_accepted(self):
        text = GOOD.replace("2025-03-14", "2825-03-14")
        r = assess_receipt(text, RULES, set())
        self.assertIn("MISSING_FIELD", r["flags"])
        self.assertEqual(r["itc_claimable"], "0.00")

    def test_misread_total_is_caught_by_the_math(self):
        r = assess_receipt(GOOD.replace("113.00", "113.08"), RULES, set(), ocr_confidence=90)
        self.assertIn("MATH_MISMATCH", r["flags"])
        self.assertEqual(r["itc_claimable"], "0.00")

    def test_low_ocr_confidence_never_claims(self):
        r = assess_receipt(GOOD, RULES, set(), ocr_confidence=40)
        self.assertEqual((r["status"], r["itc_claimable"]), ("review", "0.00"))

    def test_date_slip_on_a_second_photo_is_not_double_counted(self):
        seen = set()
        first = assess_receipt(GOOD, RULES, seen, ocr_confidence=90)
        second = assess_receipt(GOOD.replace("2025-03-14", "2025-03-11"), RULES, seen, ocr_confidence=90)
        self.assertEqual(first["status"], "claim")
        self.assertEqual((second["status"], second["itc_claimable"]), ("review", "0.00"))
        self.assertIn("POSSIBLE_DUPLICATE", second["flags"])

    def test_unreadable_date_then_clean_copy_is_not_double_counted(self):
        seen = set()
        assess_receipt(GOOD.replace("2025-03-14", "2025-93-14"), RULES, seen, ocr_confidence=90)
        second = assess_receipt(GOOD, RULES, seen, ocr_confidence=90)
        self.assertEqual(second["itc_claimable"], "0.00")

    def test_typed_text_does_not_trigger_near_duplicate(self):
        seen = set()
        assess_receipt(GOOD, RULES, seen)
        again = assess_receipt(GOOD.replace("2025-03-14", "2025-03-11"), RULES, seen)
        self.assertEqual(again["status"], "claim")


class BankCsv(unittest.TestCase):
    def test_amount_column_and_us_dates(self):
        rows, skipped = parse_bank_csv("Date,Description,Amount\n03/14/2025,BELL CANADA,-51.13\n2025-03-15,DEPOSIT,\"1,000.00\"\nbad,x,1\n")
        self.assertEqual([(r["date"], r["amount"]) for r in rows],
                         [("2025-03-14", Decimal("-51.13")), ("2025-03-15", Decimal("1000.00"))])
        self.assertEqual(skipped, 1)

    def test_debit_credit_columns(self):
        rows, _ = parse_bank_csv("Transaction Date,Details,Debit,Credit\n2025-03-14,SHELL,40.00,\n2025-03-15,CLIENT,,500.00\n")
        self.assertEqual([r["amount"] for r in rows], [Decimal("-40.00"), Decimal("500.00")])


class StoreTests(unittest.TestCase):
    def test_persistence_dedupe_and_recompute(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Store(tmp, RULES)
            rid, new = s.add_receipt(GOOD, 90.0, "tesseract", "upload", "a.jpg", b"photo-bytes", ".jpg")
            self.assertTrue(new)
            self.assertEqual(s.add_receipt(GOOD, 90.0, "tesseract", "upload", "b.jpg", b"photo-bytes", ".jpg"), (rid, False))
            self.assertTrue(s.image_path(rid).exists())
            reopened = Store(tmp, RULES)
            snap = reopened.snapshot()
            self.assertEqual(len(snap["receipts"]), 1)
            self.assertEqual(snap["receipts"][0]["status"], "claim")
            self.assertEqual(snap["summary"]["amounts"]["claimable"], "13.00")

    def test_review_decision_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = Store(tmp, RULES)
            rid, _ = s.add_receipt(GOOD.replace("HST# 123456789RT0001\n", ""), 90.0, "t", "upload")
            s.decide(rid, "approved")
            snap = Store(tmp, RULES).snapshot()
            self.assertEqual(snap["receipts"][0]["status"], "review")
            self.assertEqual(snap["summary"]["amounts"]["claimable"], "13.00")


@unittest.skipUnless(engines_available()["tesseract"], "Tesseract not installed")
class RealOcr(unittest.TestCase):
    def test_tesseract_reads_a_rendered_receipt(self):
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            self.skipTest("Pillow not installed")
        from hst.ocr import read_image
        font_path = r"C:\Windows\Fonts\consola.ttf"
        if not Path(font_path).exists():
            self.skipTest("font not available")
        font = ImageFont.truetype(font_path, 26)
        lines = GOOD.splitlines()
        img = Image.new("RGB", (620, 60 + 36 * len(lines)), "white")
        d = ImageDraw.Draw(img)
        for i, line in enumerate(lines):
            d.text((30, 30 + 36 * i), line, font=font, fill="black")
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "r.png"
            img.save(p)
            res = read_image(p, "tesseract")
        r = assess_receipt(res["text"], RULES, set(), res["confidence"])
        self.assertEqual(r["hst_amount"], "13.00")
        self.assertIn(r["status"], ("claim", "review"))  # never anything riskier


if __name__ == "__main__":
    unittest.main()
