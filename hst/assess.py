"""Decide, for one receipt, how much HST it contains and how much is claimable (and why)."""
import re
from decimal import Decimal

from .extract import extract, vendor_candidates
from .rules import back_calculate, category_for, itc_pct, rate

ZERO = Decimal("0.00")
TOLERANCE = Decimal("0.02")

FLAG_TEXT = {
    "MISSING_FIELD": "Vendor, date or total could not be read.",
    "UNKNOWN_CATEGORY": "Vendor not recognised: needs a category before any claim.",
    "MATH_MISMATCH": "Subtotal + HST does not equal the total.",
    "RATE_MISMATCH": "HST amount is not 13% of the subtotal.",
    "TAX_NOT_ITEMIZED": "HST not itemized: amount back-calculated from the total (13/113). Confirm before claiming.",
    "UNEXPECTED_HST": "HST appears on a category that normally has none.",
    "MISSING_BN": "Supplier GST/HST registration number missing: required to support a claim at this amount.",
    "DUPLICATE": "Same vendor, date and total already recorded: not counted twice.",
    "POSSIBLE_DUPLICATE": "Looks like an earlier receipt (same vendor and total, date differs by one character): the date may be misread. Check it is not counted twice.",
    "LOW_OCR_CONFIDENCE": "The photo was hard to read: check the numbers against the receipt.",
}
REVIEW_FLAGS = {"MISSING_FIELD", "UNKNOWN_CATEGORY", "MATH_MISMATCH", "RATE_MISMATCH",
                "TAX_NOT_ITEMIZED", "UNEXPECTED_HST", "MISSING_BN", "LOW_OCR_CONFIDENCE",
                "POSSIBLE_DUPLICATE"}


def _key(vendor, date, total):
    return (re.sub(r"[^a-z0-9]", "", (vendor or "").lower()), date, total)


def _near_duplicate(key, seen):
    """Photo path only: same vendor and total with a date that differs by one character (an OCR slip)."""
    for other in seen:
        if other[0] != key[0] or other[2] != key[2]:
            continue
        if other[1] is None or key[1] is None:  # an unreadable date could be anything
            return True
        if len(other[1]) == len(key[1]) and sum(a != b for a, b in zip(other[1], key[1])) == 1:
            return True
    return False


def assess_receipt(text, rules, seen, ocr_confidence=None):
    f = extract(text)
    flags, reasons = [], []
    vendor, date, total = f["vendor"], f["date"], f["total"]
    cat = category_for(vendor, rules) if vendor else None
    if cat is None:  # junk above the merchant name in a photo: look for a recognisable name nearby
        for cand in vendor_candidates(text):
            if category_for(cand, rules):
                vendor, cat = cand, category_for(cand, rules)
                f["vendor"] = vendor
                break
    cfg = rules["categories"].get(cat) if cat else None
    hst, source = f["hst"], "itemized" if f["hst"] is not None else "none"

    if not (vendor and date and total is not None):
        flags.append("MISSING_FIELD")
        if vendor and total is not None:  # remember it so a later clean copy is recognised
            key = _key(vendor, None, total)
            if ocr_confidence is not None and _near_duplicate(key, seen):
                flags.append("POSSIBLE_DUPLICATE")
            seen.add(key)
    elif _key(vendor, date, total) in seen:
        flags.append("DUPLICATE")
    else:
        key = _key(vendor, date, total)
        if ocr_confidence is not None and _near_duplicate(key, seen):
            flags.append("POSSIBLE_DUPLICATE")
        seen.add(key)

    if cat is None:
        flags.append("UNKNOWN_CATEGORY")

    if "DUPLICATE" not in flags and total is not None:
        if hst is not None:
            if f["subtotal"] is not None:
                if abs(f["subtotal"] + hst - total) > TOLERANCE:
                    flags.append("MATH_MISMATCH")
                elif abs(hst - f["subtotal"] * rate(rules)) > TOLERANCE and (cfg is None or cfg["expects_tax"]):
                    flags.append("RATE_MISMATCH")
            elif hst > 0 and abs(hst - back_calculate(total, rules)) > TOLERANCE:
                flags.append("MATH_MISMATCH")  # no subtotal: HST must equal 13/113 of the total
            if cfg and not cfg["expects_tax"] and hst > 0:
                flags.append("UNEXPECTED_HST")
        elif cfg is None or cfg["expects_tax"]:
            hst, source = back_calculate(total, rules), "back_calculated"
            flags.append("TAX_NOT_ITEMIZED")
        else:
            hst, source = ZERO, "none_expected"

    min_conf = rules.get("ocr", {}).get("min_confidence", 70)
    if ocr_confidence is not None and ocr_confidence < min_conf:
        flags.append("LOW_OCR_CONFIDENCE")

    hst = hst if hst is not None else ZERO
    threshold = Decimal(str(rules["documentation"]["registration_number_required_from"]))
    if "DUPLICATE" not in flags and hst > 0 and total is not None and total >= threshold \
            and not f["registration_number"]:
        flags.append("MISSING_BN")

    pct = itc_pct(cat, rules) if cat else Decimal(0)
    potential = (hst * pct).quantize(Decimal("0.01"))
    status, claimable, pending = "review", ZERO, ZERO
    if "DUPLICATE" in flags:
        status = "none"
    elif any(x in REVIEW_FLAGS for x in flags):
        pending = potential
    elif pct == 0:
        status = "none"
    else:
        status = "claim" if pct == 1 else "partial"
        claimable = potential

    if cfg and "DUPLICATE" not in flags:
        reasons.append(cfg["reason"])
    reasons += [FLAG_TEXT[x] for x in flags]
    return {
        "fields": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in f.items()},
        "category": cat,
        "hst_amount": str(hst), "hst_source": source,
        "status": status, "itc_claimable": str(claimable), "itc_pending": str(pending),
        "flags": flags, "reasons": reasons,
    }
