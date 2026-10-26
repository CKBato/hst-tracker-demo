"""Measure the photo path end to end with the real local OCR engine.

Renders synthetic receipts to images (clean, then degraded to look like phone photos), reads them with
Tesseract, runs the normal assessment, and compares with ground truth. Needs Pillow and Tesseract.

    python eval_photos.py [receipts] [seed]
"""
import json
import random
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from hst.assess import assess_receipt
from hst.generate import generate
from hst.ocr import read_image
from hst.rules import load_rules

FONT = r"C:\Windows\Fonts\consola.ttf"


def render(text, path, level, rng):
    font = ImageFont.truetype(FONT, 26)
    lines = text.splitlines()
    img = Image.new("RGB", (620, 60 + 36 * len(lines)), (250, 250, 246))
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((30, 30 + 36 * i), line, font=font, fill=(25, 25, 25))
    if level == "phone":
        img = img.rotate(rng.uniform(-2.5, 2.5), expand=True, fillcolor=(120, 118, 112))
        img = img.filter(ImageFilter.GaussianBlur(1.1))
        px = img.load()
        for _ in range(img.width * img.height // 25):
            x, y = rng.randrange(img.width), rng.randrange(img.height)
            v = rng.randint(-45, 45)
            r, g, b = px[x, y]
            px[x, y] = (max(0, min(255, r + v)), max(0, min(255, g + v)), max(0, min(255, b + v)))
        img = img.resize((int(img.width * 0.7), int(img.height * 0.7)))
        img.save(path, quality=60)
    else:
        img.save(path)


def run_level(level, truth, texts, workdir, rules, rng):
    paths = {}
    for name, text in texts.items():
        p = workdir / f"{level}_{name}.{'jpg' if level == 'phone' else 'png'}"
        render(text, p, level, rng)
        paths[name] = p
    with ThreadPoolExecutor(4) as pool:
        ocr = dict(zip(paths, pool.map(lambda p: read_image(p, "tesseract"), paths.values())))
    seen, rows = set(), []
    for name in sorted(texts):
        res = assess_receipt(ocr[name]["text"], rules, seen, ocr[name]["confidence"])
        t = truth["receipts"][name]
        want_status, want_itc = t["expected_status"], Decimal(t["expected_itc"])
        got_itc = Decimal(res["itc_claimable"])
        rows.append({"name": name, "ok": res["status"] == want_status, "res": res, "t": t,
                     "over": max(Decimal(0), got_itc - want_itc), "conf": ocr[name]["confidence"] or 0})
    n = len(rows)
    ok = sum(r["ok"] for r in rows)
    safe_review = sum(1 for r in rows if not r["ok"] and r["res"]["status"] == "review")
    over = [r for r in rows if r["over"] > Decimal("0.01")]
    return {
        "level": level, "receipts": n, "status_matches_expected": f"{ok}/{n}",
        "wrong_but_safe (sent to review instead)": safe_review,
        "other_mismatches": n - ok - safe_review,
        "over_claimed_receipts": len(over), "over_claimed_dollars": str(sum((r["over"] for r in over), Decimal(0))),
        "mean_ocr_confidence": round(sum(r["conf"] for r in rows) / n, 1),
        "_rows": rows,
    }


def main(n=40, seed=7):
    rules = load_rules()
    rng = random.Random(seed)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        truth = generate(tmp / "src", n=n, seed=seed)
        texts = {p.name: p.read_text(encoding="utf-8") for p in sorted((tmp / "src").glob("*.txt"))}
        out = []
        for level in ("clean", "phone"):
            r = run_level(level, truth, texts, tmp, rules, rng)
            for row in r["_rows"]:
                if not row["ok"] and row["res"]["status"] != "review" or row["over"] > Decimal("0.01"):
                    print(f"[{level}] UNSAFE/odd: {row['name']} got {row['res']['status']} "
                          f"expected {row['t']['expected_status']} flags={row['res']['flags']}")
            out.append({k: v for k, v in r.items() if k != "_rows"})
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main(*(int(a) for a in sys.argv[1:3]))
